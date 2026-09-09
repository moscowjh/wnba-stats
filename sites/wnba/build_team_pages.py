#!/usr/bin/env python3
"""Emit one static page per WNBA team at /teams/<slug>/, plus a /teams/ index.

Built to the design settled 2026-08-14 and recorded in the sequencing plan's
§6b ("Team page layout — worked 2026-08-14, largely settled"). Scope was
re-confirmed unchanged on 2026-09-08; this file deliberately implements that
design rather than reinterpreting it.

What §6b decided, and why each is not an accident:

* **Roster is the spine of the page, above the disclosure, not behind it.**
  Two reasons: 15 team pages linking to 232 player pages is the crawl path
  that gets those pages discovered (a stronger signal than the sitemap
  alone), and a human who arrived looking for one player should not have to
  open anything to find her.
* **Roster = ESPN's current roster + a short "also appeared this season"
  list.** Settled by the 2026-08-16 probe. Injured/developmental
  designations are NOT carried: the endpoint has no status taxonomy (every
  athlete reads `Active`), so we do not synthesize one.
* **Three cards: Record + place · Pythagorean · Luck.** Deliberately NOT
  ORtg/DRtg — the open possessions work has our ratings ~0.7 low and
  misranking 6 of 15 teams against official, and an ordinal badge on a
  misranked rating is exactly the accuracy failure this site is pitched
  against. Every number in this card set is exact.
* **Ordinals are safe everywhere on a team page — a genuinely different rule
  from the player pages, not a port of one.** Ordinals were barred on player
  rate stats because of volume confounds and a low qualification floor.
  Neither exists at team level: every team plays the same number of games.
* **Coach is a first-class fact, not a footnote.** "Who is the head coach of
  the Chicago Sky" is a real long-tail query with thin competition. The CSV
  freezes the PRE-2026 record and this build adds the current season from our
  own box scores, so the number is always correct and never needs
  hand-updating in-season — the same "no claim a game could falsify" rule the
  player sentences follow.
* **No prose slot.** Player pages carry two sentence slots and team pages
  carry none; the roster and the results are the content. An editorial slot
  is parked to next season's kickoff (Jason, 2026-09-08) — do not add one
  "while you're in there."

Ordering note: this script writes the sitemap covering BOTH players and
teams, so it must run AFTER build_player_pages.py. It derives the player
slugs itself rather than trusting the previous step's output, so the sitemap
is correct even if that ordering is ever disturbed.
"""
from __future__ import annotations

import csv
import json
from html import escape as esc

import pandas as pd

from sag import seo
from sag.render import chrome

import build_box_pages as bbp
import build_stats_page as bsp
from config import WNBA

OUT_DIR = WNBA.public_dir / "teams"
COACHES_CSV = WNBA.site_dir / "reference" / f"wnba_coaches_{WNBA.season}.csv"

SITE_TITLE = f"{WNBA.display_name} {WNBA.season} — At a Glance"

#: See build_player_pages.ANALYTICS_KEY_MAX — the same 32-char worker slice
#: applies, and the same silent-collision risk. "team:" is 5 chars, so the
#: longest team slug (golden-state-valkyries, 22) leaves real margin where the
#: player keys have none.
ANALYTICS_KEY_MAX = 32
INDEX_ANALYTICS_KEY = "teams"

# Inline glossary definitions. Both use <details name="glossary"> — the SAME
# disclosure element settled for the player pages, so the site still has
# exactly one gesture. §6b calls Luck the strongest "teaching through
# exposure" moment on the site so far: it explains why a 12-21 team can be
# better than its record, which is a more interesting thing to say about
# Chicago than "12-21".
PYTHAG_DEFINITION = (
    "<b>Pythagorean record.</b> The record a team's points scored and "
    "allowed say it should have. Built from margin rather than results, so "
    "it ignores how close the wins were."
)
LUCK_DEFINITION = (
    "<b>Luck.</b> Actual wins minus Pythagorean wins. A positive number "
    "means a team has won more than its scoring margin accounts for — often "
    "close games going its way. It tends to fade."
)


def analytics_key(slug):
    """One analytics identity per team page, used for both its pageview and
    its expand event — the same convention the player pages use."""
    return f"team:{slug}"


# ── Small helpers ─────────────────────────────────────────────────────────

def ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def signed(v, places=1):
    """Signed number for the Luck card. `+0.0` and `-0.0` both read as 0.0."""
    r = round(float(v), places)
    if r == 0:
        return f"{0:.{places}f}"
    return f"{r:+.{places}f}"


# ── Inputs ────────────────────────────────────────────────────────────────

def load_coaches():
    """team_abbr -> coach dict from the hand-maintained reference CSV.

    The CSV holds the coach's record with this team BEFORE the current
    season; the current season is added at build time in coach_line(). That
    split is the load-bearing part of the schema — it means the file never
    needs touching mid-season and can never state a number a game could
    falsify.
    """
    if not COACHES_CSV.exists():
        print(f"WARNING: {COACHES_CSV.name} missing — coach lines omitted.")
        return {}
    with COACHES_CSV.open() as fh:
        return {r["team_abbr"]: r for r in csv.DictReader(fh)}


def load_rosters():
    """TLA -> {status, players[]} from the fetched roster file.

    Correct-or-blank: a team whose fetch failed carries status
    "unavailable" and is rendered as unknown, never as an empty roster. A
    missing file means every team is unknown — the page still builds.
    """
    if not WNBA.rosters.exists():
        print(f"WARNING: {WNBA.rosters.name} missing — rosters render as unknown.")
        return {}
    try:
        return json.loads(WNBA.rosters.read_text()).get("teams", {})
    except Exception as e:
        print(f"WARNING: {WNBA.rosters.name} unreadable ({e}).")
        return {}


def load_upcoming():
    """(status, [games]) for the forward schedule window.

    The status distinction is load-bearing and mirrors schedule_today.json:
    "ok" with no game for this team means nothing is scheduled, anything
    else means we do not know. On 2026-08-05 an unavailable fetch was
    published as the confident false claim "No games today" — a team page
    must not repeat that with "no games scheduled".
    """
    if not WNBA.schedule_upcoming.exists():
        return "unavailable", []
    try:
        d = json.loads(WNBA.schedule_upcoming.read_text())
        return d.get("status", "unavailable"), d.get("games", [])
    except Exception:
        return "unavailable", []


# ── Derived facts ─────────────────────────────────────────────────────────

def coach_line(coach, wins_2026, losses_2026):
    """"Head coach: Name · since 2025 · 24-20" with the current season folded
    into the record. Returns "" when the team has no CSV row."""
    if not coach:
        return ""
    w = int(coach.get("pre2026_w") or 0) + wins_2026
    ln = int(coach.get("pre2026_l") or 0) + losses_2026
    since = coach.get("first_season_with_team", "")
    bits = [f'<span class="ac">{esc(coach["coach_name"])}</span>']
    if since:
        bits.append(f"since {esc(since)}")
    bits.append(f'<span class="num">{w}-{ln}</span> with the team')
    return ('<div class="coach">Head coach: ' + " · ".join(bits) + "</div>")


def team_results(team_raw, abbr):
    """Every completed game for one team, newest first, as dicts.

    Reads the ALL-GAMES frame so a playoff game appears here the day it is
    played — the same reason build_games_section() takes load_all_games().
    """
    rows = team_raw[team_raw["team_abbreviation"] == abbr]
    by_game = {g: d for g, d in team_raw.groupby("game_id")}
    out = []
    for _, r in rows.sort_values("game_date", ascending=False).iterrows():
        others = by_game[r["game_id"]]
        opp = others[others["team_abbreviation"] != abbr]
        if opp.empty:
            continue
        opp = opp.iloc[0]
        won = bool(r["team_score"] > opp["team_score"])
        out.append({
            "date": r["game_date"],
            "opp": opp["team_abbreviation"],
            "wl": "W" if won else "L",
            # Score orientation, per the WWC rule this site had to fix live on
            # 2026-09-05: name both teams -> fixture order; name ONE team ->
            # that team's own order. A results row names only the opponent, so
            # this team's score leads regardless of home/away.
            "score": f"{int(r['team_score'])}-{int(opp['team_score'])}",
            "season_type": r.get("season_type", 2),
        })
    return out


def next_game(abbr, status, games):
    """The next scheduled game for one team, or None. Callers must handle the
    `status` separately — None means "none scheduled" only when status is ok."""
    for g in games:
        if abbr in (g.get("home"), g.get("away")):
            return g
    return None


# ── CSS ───────────────────────────────────────────────────────────────────
#
# RATIONALE IN PYTHON COMMENTS, NOT CSS ONES — this block is inlined into
# every team page, so a comment in the string is paid once per page over the
# wire. Same rule the player pages follow.
#
# Typography is variant B, imported from build_stats_page (bsp.SANS/bsp.MONO)
# rather than redeclared, so the three WNBA surfaces cannot drift. Mono is
# scoped by the same structural rule used elsewhere: in a stat table the first
# column names the entity and every other column is a quantity.
_TEAM_CSS = f"""\
body{{font-family:{bsp.SANS};background:var(--bg);color:var(--text);
  font-size:13.5px;padding:14px 10px;max-width:560px;margin:0 auto;
  line-height:1.55;-webkit-font-smoothing:antialiased}}
a{{color:var(--muted)}}
.tp{{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:13px}}
.hd{{display:flex;gap:12px;align-items:flex-start}}
.mono{{width:58px;height:58px;background:var(--surface);border:1px solid var(--border);
  flex:0 0 auto;display:flex;align-items:center;justify-content:center;
  font-family:{bsp.MONO};font-size:19px;color:var(--accent);letter-spacing:.5px}}
.tp h1{{color:var(--accent);font-size:19px;font-weight:700;letter-spacing:-.2px;
  line-height:1.15}}
.mu{{color:var(--muted);font-size:11px}}
.ac{{color:var(--accent)}}
.num{{font-family:{bsp.MONO};font-variant-numeric:tabular-nums}}
.coach{{color:var(--muted);font-size:11.5px;margin-top:5px;line-height:1.6}}
.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:12px}}
.card{{background:var(--surface);padding:8px 9px;border:1px solid var(--border)}}
.card .lab{{font-size:9.5px;color:var(--muted);letter-spacing:.6px;
  font-weight:600;text-transform:uppercase}}
.card .big{{font-size:21px;line-height:1.3;font-weight:600;font-family:{bsp.MONO};
  font-variant-numeric:tabular-nums}}
.card .sub{{font-size:10px;color:var(--muted);min-height:14px;line-height:14px}}
.card .sub a{{color:var(--accent);text-decoration:none}}
.card .sub a:hover{{text-decoration:underline}}
.sec{{color:var(--accent);font-size:10px;letter-spacing:1px;text-transform:uppercase;
  border-bottom:1px solid var(--border);padding-bottom:4px;margin:16px 0 6px;
  font-weight:700}}
table.s{{border-collapse:collapse;width:100%;font-size:11.5px;
  font-variant-numeric:tabular-nums}}
table.s th{{color:var(--muted);text-align:left;padding:4px 5px;font-weight:normal;
  border-bottom:1px solid var(--border);text-transform:uppercase;
  letter-spacing:.4px;font-size:10px}}
table.s td{{padding:5px 5px;border-bottom:1px solid var(--border)}}
table.s td:not(:first-child){{font-family:{bsp.MONO}}}
table.s td a{{color:var(--text);text-decoration:underline;
  text-decoration-color:rgba(136,136,136,0.45);text-underline-offset:2px}}
table.s td a:hover{{color:var(--accent)}}
.w{{color:#7ec27e}}.l{{color:var(--muted)}}
.also{{color:var(--muted);font-size:11px;line-height:1.7;margin-top:7px}}
.also a{{color:var(--muted);text-decoration:underline;
  text-decoration-color:rgba(136,136,136,0.4);text-underline-offset:2px}}
.also a:hover{{color:var(--accent)}}
.nextg{{font-size:12px;margin-top:8px}}
.nextg .ac{{font-family:{bsp.MONO}}}
.links{{margin-top:14px;font-size:12px;line-height:1.9}}
.links a{{color:var(--accent);text-decoration:none}}
.links a:hover{{text-decoration:underline}}
details.exp{{margin-top:12px}}
details.exp>summary{{background:var(--surface);border:1px solid var(--border);
  color:var(--muted);font-size:11px;padding:12px;cursor:pointer;list-style:none;
  display:flex;align-items:center;justify-content:space-between;gap:8px;
  font-weight:500}}
details.exp>summary::-webkit-details-marker{{display:none}}
details.exp>summary::after{{content:"";flex:0 0 auto;width:6px;height:6px;
  border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;
  transform:rotate(45deg);margin-bottom:3px;transition:transform .15s ease}}
details.exp[open]>summary::after{{transform:rotate(225deg);margin-bottom:-2px}}
details.exp>summary:hover{{border-color:var(--accent);color:var(--accent)}}
details.inl{{display:inline-block}}
details.inl>summary{{list-style:none;cursor:pointer;display:inline-block;
  padding:6px 10px 6px 0;margin:-6px 0 -6px 0}}
details.inl>summary::-webkit-details-marker{{display:none}}
details.inl>summary .t{{border-bottom:1px dotted var(--muted)}}
details.inl[open]>summary{{color:var(--accent)}}
details.inl[open]>summary .t{{border-bottom-color:var(--accent)}}
details.inl .body{{position:absolute;width:230px;background:#000;
  border:1px solid var(--accent);padding:8px 9px;font-size:10px;line-height:1.65;
  color:var(--text);z-index:20;margin-top:5px}}
.data-note{{color:var(--muted);font-size:10px;margin-top:14px}}
"""

PAGE_CSS = (
    chrome.tokens_css(WNBA.accent)
    + "*{box-sizing:border-box;margin:0;padding:0}\n"
    + _TEAM_CSS
    + chrome.SUBPAGE_HEADER_CSS
    + chrome.SITE_FOOTER_CSS
)


def page_js(page_key):
    return chrome.usage_js(WNBA.slug, page_key)


def head_html(title, path, description, jsonld=None):
    parts = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{esc(title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
        f'<link rel="canonical" href="{esc(seo.canonical_url(WNBA, path))}">',
        *seo.social_tags(WNBA, path, title, description,
                         og_type="profile", card="summary"),
    ]
    if jsonld:
        parts.append(f'<script type="application/ld+json">{jsonld}</script>')
    parts.append(f"<style>{PAGE_CSS}</style>")
    return "\n".join(parts)


# ── Page pieces ───────────────────────────────────────────────────────────

def card(lab_html, value, sub_html=""):
    return (f'<div class="card"><div class="lab">{lab_html}</div>'
            f'<div class="big">{value}</div>'
            f'<div class="sub">{sub_html}</div></div>')


def glossary_label(text, definition):
    return (f'<details class="inl" name="glossary">'
            f'<summary><span class="t">{text}</span></summary>'
            f'<span class="body">{definition}</span></details>')


def cards_html(row, place):
    """Record + place · Pythagorean · Luck. Ordinals are safe on all three."""
    w, ln = int(row["W"]), int(row["L"])
    xw, xl = float(row["XW"]), float(row["XL"])
    luck = w - xw
    return "".join([
        card("Record", f"{w}-{ln}",
             f'<a href="/#standings">{ordinal(place)} of 15 &rarr;</a>'),
        card(glossary_label("Pythagorean", PYTHAG_DEFINITION),
             f"{xw:.1f}-{xl:.1f}", "expected from margin"),
        card(glossary_label("Luck", LUCK_DEFINITION), signed(luck),
             "wins above expected"),
    ])


def resolve_roster_id(player, team_ids_by_name, appeared_by_id):
    """Our athlete_id for a rostered player: by id, else by name on this team.

    ESPN's roster endpoint and our box scores can disagree about a player's
    athlete_id, because ESPN renumbers players and does so RETROACTIVELY — a
    fresh fetch of an old game returns the new id while our cached rows keep
    the old one. Our incremental CSV therefore has no duplicate to detect
    (canonicalize_athlete_ids correctly does nothing), and the mismatch
    surfaces here instead.

    Shipped 2026-09-09 after Alicia Florez appeared TWICE on the Mystics page:
    once as an unlinked roster row with no stats, because the roster's id
    (5208985) matched nothing in our data, and once under "also appeared this
    season", because her box-score id (5349415) was not in the rostered set.
    Both halves are the same missing join.

    The name fallback is scoped to ONE TEAM'S roster, which is what makes it
    safe: two players sharing a name across the league is plausible and is why
    ids exist, but two on the same 13-woman roster is not a case that occurs.
    """
    aid = str(player.get("athlete_id", ""))
    if aid in appeared_by_id:
        return aid
    return team_ids_by_name.get(player.get("name", "").strip().lower(), aid)


def roster_table(players, appeared_by_id, slug_by_id, team_ids_by_name):
    """The roster, as the spine of the page. Every player who has a page is a
    link — this is the crawl path to the player pages, and the reason the
    roster is not behind the disclosure."""
    rows = []
    for p in sorted(players, key=lambda x: (x["name"].split()[-1].lower(),
                                            x["name"].lower())):
        aid = resolve_roster_id(p, team_ids_by_name, appeared_by_id)
        stats = appeared_by_id.get(aid)
        name = esc(p["name"])
        slug = slug_by_id.get(aid)
        # Correct-or-blank: link only where the page really exists. A rostered
        # player who has not appeared in a box score has no page, and gets
        # plain text rather than a link to a 404.
        cell = f'<a href="/players/{slug}/">{name}</a>' if slug else name
        gp = int(stats["GP"]) if stats is not None else "—"
        ppg = bsp.f1(stats["PPG"]) if stats is not None else "—"
        rows.append(
            f'<tr><td>{cell}</td><td>{esc(p.get("jersey") or "—")}</td>'
            f'<td>{esc(p.get("position") or "—")}</td>'
            f"<td>{gp}</td><td>{ppg}</td></tr>")
    return ('<table class="s"><tr><th>Player</th><th>No.</th><th>Pos</th>'
            "<th>GP</th><th>PPG</th></tr>" + "".join(rows) + "</table>")


def results_table(results, limit=None):
    rows = []
    for g in (results[:limit] if limit else results):
        cls = "w" if g["wl"] == "W" else "l"
        rows.append(
            f'<tr><td>{esc(str(g["date"]))}</td>'
            f'<td>{esc(g["opp"])}</td>'
            f'<td class="{cls}">{g["wl"]}</td>'
            f'<td>{esc(g["score"])}</td></tr>')
    return ('<table class="s"><tr><th>Date</th><th>Opp</th><th>W/L</th>'
            "<th>Score</th></tr>" + "".join(rows) + "</table>")


def next_game_html(abbr, status, games):
    """Above the fold. Distinguishes "nothing scheduled" from "we don't know"."""
    if status != "ok":
        return '<div class="nextg mu">Next game: schedule unavailable.</div>'
    g = next_game(abbr, status, games)
    if not g:
        return '<div class="nextg mu">No upcoming games scheduled.</div>'
    opp = g["home"] if g["away"] == abbr else g["away"]
    where = "at" if g["away"] == abbr else "vs"
    when = " · ".join(x for x in (g.get("date"), g.get("tip_et")) if x)
    return (f'<div class="nextg">Next: <span class="ac">{esc(where)} '
            f'{esc(opp)}</span> <span class="mu">{esc(when)}</span></div>')


def render_page(row, place, abbr, slug, coach, roster, appeared_by_id,
                slug_by_id, also_appeared, results, sched_status, sched_games,
                data_through, team_ids_by_name):
    name = row["Team"]
    w, ln = int(row["W"]), int(row["L"])

    if roster.get("status") == "ok" and roster.get("players"):
        roster_block = roster_table(roster["players"], appeared_by_id,
                                    slug_by_id, team_ids_by_name)
        if also_appeared:
            links = ", ".join(
                (f'<a href="/players/{slug_by_id[a]}/">{esc(n)}</a>'
                 if a in slug_by_id else esc(n))
                for a, n in also_appeared)
            roster_block += (f'<div class="also"><b>Also appeared this '
                             f"season:</b> {links}</div>")
    else:
        # Correct-or-blank. Never an empty table implying an empty roster.
        roster_block = ('<div class="also">Roster unavailable — it could not '
                        "be retrieved for this build.</div>")

    parts = [
        f'<div class="hd"><div class="mono">{esc(abbr)}</div>'
        f'<div style="min-width:0"><h1>{esc(name)}</h1>'
        f'<div class="mu">{WNBA.display_name} {WNBA.season} · '
        f'<span class="num">{w}-{ln}</span></div>'
        + coach + "</div></div>",
        f'<div class="grid3">{cards_html(row, place)}</div>',
        next_game_html(abbr, sched_status, sched_games),
        '<div class="sec">Last 3</div>',
        results_table(results, limit=3),
        '<div class="sec">Roster</div>',
        roster_block,
        # Fragments must match the tab site's real section ids
        # (build_stats_page: standings/leaders/teameff/teamtotals/players/
        # abbreviations) AND the openTabFromHash() handler added alongside
        # these links — every tab is display:none until something activates
        # it, so a fragment alone used to land on a hidden div.
        '<div class="links">'
        f'<a href="/#teamtotals">Team totals &rarr;</a><br>'
        f'<a href="/#teameff">Four factors &amp; efficiency &rarr;</a><br>'
        f'<a href="/#players">All players &rarr;</a></div>',
        '<details class="exp"><summary><span>See full schedule &amp; results'
        "</span></summary>" + results_table(results) + "</details>",
    ]

    path = f"/teams/{slug}/"
    title = f"{name} — {WNBA.display_name} {WNBA.season} stats at a glance"
    description = (
        f"{name} {WNBA.season} {WNBA.display_name} season: {w}-{ln}, roster, "
        f"results and Pythagorean record. Fast, ad-free, updated every "
        f"morning.")
    jsonld = seo.person_jsonld(name, seo.canonical_url(WNBA, path), name)

    masthead = chrome.subpage_header_html(
        esc(SITE_TITLE), "/", crumb_html='<a href="/teams/">← all teams</a>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{head_html(title, path, description, jsonld)}
</head>
<body>
{masthead}<div class="tp">{"".join(parts)}</div>
<div class="data-note">Stats through games of {data_through}</div>
{chrome.SITE_FOOTER_HTML}<script>{page_js(analytics_key(slug))}</script>
{chrome.cf_beacon_html(WNBA.cf_analytics_token)}</body>
</html>
"""


def render_index(entries, data_through):
    rows = "".join(
        f'<tr><td><a href="/teams/{e["slug"]}/">{esc(e["name"])}</a></td>'
        f'<td>{esc(e["abbr"])}</td><td>{e["record"]}</td>'
        f'<td>{e["pythag"]}</td></tr>'
        for e in entries)
    title = f"{WNBA.display_name} {WNBA.season} team pages — At a Glance"
    description = (
        f"One page per {WNBA.display_name} team: {WNBA.season} record, "
        "roster, results and Pythagorean record. Updated every morning.")
    masthead = chrome.subpage_header_html(
        esc(SITE_TITLE), "/",
        crumb_html=f"{len(entries)} teams · stats through {data_through}")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{head_html(title, "/teams/", description)}
</head>
<body>
{masthead}<div class="tp"><table class="s">
<tr><th>Team</th><th>TLA</th><th>Record</th><th>Pythagorean</th></tr>
{rows}</table></div>
{chrome.SITE_FOOTER_HTML}<script>{page_js(INDEX_ANALYTICS_KEY)}</script>
{chrome.cf_beacon_html(WNBA.cf_analytics_token)}</body>
</html>
"""


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    # Regular-season frame for the cards (a season record is a regular-season
    # record); all-games frame for the results list, so a playoff game shows
    # up the day it is played. Same split as build_stats_page.main().
    player_rs, team_rs = bsp.load_data()
    player_all, team_all = bsp.load_all_games()

    standings = bsp.compute_standings(team_rs).reset_index(drop=True)
    through_dt = pd.Timestamp(player_rs["game_date"].max())
    data_through = through_dt.strftime("%B %-d, %Y")
    data_through_iso = through_dt.strftime("%Y-%m-%d")

    abbr_by_name = (team_rs.drop_duplicates("team_display_name")
                    .set_index("team_display_name")["team_abbreviation"]
                    .to_dict())

    # Player identity, shared with build_player_pages via the same functions,
    # so a roster link and the page it points at cannot disagree about a slug.
    season = bsp.compute_player_season(player_rs).reset_index(drop=True)
    season["athlete_id"] = season["athlete_id"].astype(str)
    player_slugs = season["athlete_display_name"].map(seo.slugify)
    slug_by_id = dict(zip(season["athlete_id"], player_slugs))
    appeared_by_id = {r["athlete_id"]: r for _, r in season.iterrows()}
    name_by_id = dict(zip(season["athlete_id"], season["athlete_display_name"]))
    team_by_id = dict(zip(season["athlete_id"], season["team_abbreviation"]))

    coaches = load_coaches()
    rosters = load_rosters()
    sched_status, sched_games = load_upcoming()

    # Coach records need this season's W-L for the team, which is the
    # standings row — computed once, keyed by TLA.
    rec_by_abbr = {abbr_by_name.get(r["Team"], ""): (int(r["W"]), int(r["L"]))
                   for _, r in standings.iterrows()}

    slugs = {}
    for _, r in standings.iterrows():
        slugs[r["Team"]] = seo.slugify(r["Team"])
    dupes = [s for s in slugs.values() if list(slugs.values()).count(s) > 1]
    assert not dupes, f"team slug collision: {sorted(set(dupes))}"
    over = [(s, len(analytics_key(s)) - ANALYTICS_KEY_MAX)
            for s in slugs.values() if len(analytics_key(s)) > ANALYTICS_KEY_MAX]
    assert not over, f"analytics key overflow: {over}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries, n_linked, n_roster_unknown = [], 0, 0

    for place, (_, row) in enumerate(standings.iterrows(), start=1):
        name = row["Team"]
        abbr = abbr_by_name.get(name, "")
        slug = slugs[name]
        roster = rosters.get(abbr, {})
        if roster.get("status") != "ok" or not roster.get("players"):
            n_roster_unknown += 1

        # Our ids for this team's players, by lower-cased name — the fallback
        # join when ESPN's roster id and our box-score id disagree.
        team_ids_by_name = {name_by_id[a].strip().lower(): a
                            for a, t in team_by_id.items() if t == abbr}
        # RESOLVED ids, not raw roster ids: a player whose roster id differs
        # from her box-score id would otherwise be excluded from nothing and
        # appear a second time under "also appeared this season".
        rostered_ids = {resolve_roster_id(p, team_ids_by_name, appeared_by_id)
                        for p in roster.get("players", [])}
        # "Also appeared this season": played for this team, not on the
        # current roster. Resolves the double-roster case (a player traded
        # mid-season appears in both teams' box scores but one team's roster).
        also = sorted(
            ((aid, name_by_id[aid]) for aid, t in team_by_id.items()
             if t == abbr and aid not in rostered_ids),
            key=lambda x: x[1].split()[-1].lower())

        w, ln = rec_by_abbr.get(abbr, (0, 0))
        coach = coach_line(coaches.get(abbr), w, ln)
        results = team_results(team_all, abbr)

        html = render_page(row, place, abbr, slug, coach, roster,
                           appeared_by_id, slug_by_id, also, results,
                           sched_status, sched_games, data_through,
                           team_ids_by_name)
        page_dir = OUT_DIR / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(html)

        n_linked += sum(1 for p in roster.get("players", [])
                        if str(p.get("athlete_id", "")) in slug_by_id)
        entries.append({
            "slug": slug, "name": name, "abbr": abbr,
            "record": f"{int(row['W'])}-{int(row['L'])}",
            "pythag": f"{float(row['XW']):.1f}-{float(row['XL']):.1f}",
        })

    (OUT_DIR / "index.html").write_text(render_index(entries, data_through))

    # The sitemap covers BOTH page families. This step owns it because it runs
    # last; the player slugs are derived here rather than read back from the
    # previous step's output, so the file is correct regardless of ordering.
    # /games/ is included even while empty: an event surface has an indexing
    # lead time that content quality cannot compress (2026-09-06 decision), so
    # the index wants to be crawlable BEFORE the playoffs, not on the day they
    # start. Individual box-score URLs are derived through the same
    # bsp.game_slug() the emitter uses.
    paths = (["/", "/players/", "/teams/", "/games/"]
             + [f"/players/{s}/" for s in player_slugs]
             + [f"/teams/{e['slug']}/" for e in entries]
             + bbp.page_paths(player_all, team_all))
    seo.write_sitemap(WNBA, paths, data_through_iso)

    print(f"Wrote {len(entries)} team pages + index to {OUT_DIR}")
    print(f"  roster links to player pages: {n_linked}")
    if n_roster_unknown:
        print(f"  rosters unavailable: {n_roster_unknown} team(s)")
    print(f"  sitemap: {len(paths)} URLs")


if __name__ == "__main__":
    main()
