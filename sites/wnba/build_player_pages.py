#!/usr/bin/env python3
"""build_player_pages.py — production emitter for per-player pages.

Renders one static page per player into ``public/players/<slug>/index.html``
plus a players index at ``public/players/``, and — because it is the step
that knows every published URL — also writes ``public/sitemap.xml`` and
``public/robots.txt``. Wired into build.yml after the main page build;
everything under public/ deploys via the Action's `wrangler deploy`.

Chrome comes from ``sag.render.chrome`` — the same tokens, footer,
scroll-fade and analytics the tab site uses, imported, not copied. Slugs
come from ``sag.seo.slugify`` — the same function the Players-tab
cross-links use, so a link and its page cannot disagree.

Editorial (Tier 1) sentences come from ``reference/hooks.json`` with full
provenance (sources with verbatim quotes, falsifiable_by_game: false);
``validate_hooks.py`` asserts the contract. A slug absent from hooks.json
gets no editorial sentence — omit rather than pad.

Bio fields (height, college, draft, jersey, experience) come from ESPN's
athlete endpoint via fetch_data.espn_get — the adapter with origin handling;
never construct an ESPN URL any other way. Results are cached in
data/player_bios_<season>.json (gitignored locally, carried in CI by the
Actions data cache) so repeat runs fetch nothing.

Usage:
    .venv/bin/python sites/wnba/build_player_pages.py             # fetch missing bios, emit all
    .venv/bin/python sites/wnba/build_player_pages.py --no-fetch  # cached bios only
"""

import argparse
import json
import re
import time
from html import escape as esc

import pandas as pd

from sag import seo
from sag.render import chrome

import build_stats_page as bsp
import fetch_data as fd
from config import WNBA

BIOS_PATH = WNBA.data_dir / f"player_bios_{WNBA.season}.json"
HOOKS_PATH = WNBA.site_dir / "reference" / "hooks.json"
OUT_DIR = WNBA.public_dir / "players"

# MIRRORS the analytics worker's blob slice width — workers/analytics/worker.js
# slices `e`/`t` to 32 chars, and the two must be changed together. A key
# longer than the slice is not lossy display, it is a SILENT COLLISION: two
# slugs sharing their first 25 characters after "player:" would merge their
# events into one analytics key with no signal that it happened. main()
# asserts every emitted key fits; the current longest (player:darianna-
# littlepage-buggs) is exactly 32, so the margin is zero.
ANALYTICS_KEY_MAX = 32

#: blob2 value for the players index. Distinct from both the empty string
#: (the tab site) and from any `player:<slug>`.
INDEX_ANALYTICS_KEY = "players"


def analytics_key(slug):
    """The one analytics identity for a player page — used for BOTH its
    pageview and its expand event, so engagement is a grouping on one key
    rather than a join between two naming conventions."""
    return f"player:{slug}"

SITE_TITLE = f"{WNBA.display_name} {WNBA.season} — At a Glance"

ESPN_ATHLETE = f"{fd.ESPN_ORIGIN}/apis/common/v3/sports/basketball/wnba/athletes"

TS_DEFINITION = (
    "<b>True shooting.</b> Points scored per shot, counting three-pointers "
    "and free throws at their real value. One number for how efficiently a "
    "player scores."
)

ORDINAL_WORDS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
    11: "11th", 12: "12th",
}

POSITION_WORDS = {
    "G": "guard", "F": "forward", "C": "center",
    "G/F": "guard-forward", "F/G": "forward-guard",
    "F/C": "forward-center", "C/F": "center-forward",
}


# ── Small helpers ─────────────────────────────────────────────────────────

def ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def ordinal_word(n):
    return ORDINAL_WORDS.get(int(n), ordinal(n))


def fmt_ts(v):
    """TS% as the card shows it: 61.2 (percent) -> '.612'."""
    if v is None or pd.isna(v):
        return "—"
    return f"{v / 100:.3f}".lstrip("0")


def parse_height(display_height):
    """ESPN's `6' 1"` -> the card's `6-1`."""
    if not display_height:
        return None
    m = re.match(r"(\d+)'\s*(\d+)", display_height)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def parse_draft(display_draft):
    """'2025: Rd 1, Pk 3 (WAS)' -> ('2025', 1, 3); None if undrafted/absent."""
    if not display_draft:
        return None
    m = re.match(r"(\d{4}): Rd (\d+), Pk (\d+)", display_draft)
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def parse_experience(display_experience):
    """'Rookie' -> 0, '2nd Season' -> 2; None when absent."""
    if not display_experience:
        return None
    if display_experience.strip().lower() == "rookie":
        return 0
    m = re.match(r"(\d+)", display_experience)
    return int(m.group(1)) if m else None


# ── Tier 1 editorial sentences ────────────────────────────────────────────

def load_hooks():
    """Tier 1 sentences with provenance, keyed by slug. Underscore-prefixed
    keys (_schema, _rejected) are metadata, not entries. Only `sentence`
    renders; the sources/claims fields are the provenance record that
    validate_hooks.py asserts over. A slug with no entry gets no editorial
    sentence — that is the 'omit rather than pad' rule, not an error."""
    if not HOOKS_PATH.exists():
        return {}
    data = json.loads(HOOKS_PATH.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ── Bio fetch ─────────────────────────────────────────────────────────────

def _trim_bio(ath):
    return {
        "jersey": ath.get("jersey") or ath.get("displayJersey"),
        "height": ath.get("displayHeight"),
        "position": (ath.get("position") or {}).get("abbreviation"),
        "college": (ath.get("college") or {}).get("name"),
        "draft": ath.get("displayDraft"),
        "experience": ath.get("displayExperience"),
    }


def load_or_fetch_bios(athlete_ids, fetch=True):
    """One athlete-endpoint call per player not already cached. ~230 calls on
    the first run, zero after. A hard failure caches {} so one broken id
    can't re-stall every later run; delete the entry to retry it."""
    bios = {}
    if BIOS_PATH.exists():
        bios = json.loads(BIOS_PATH.read_text())
    missing = [a for a in athlete_ids if a not in bios]
    if not fetch or not missing:
        return bios
    print(f"Fetching bios for {len(missing)} players "
          f"({len(bios)} already cached) ...")
    for i, aid in enumerate(missing, 1):
        try:
            data = fd.espn_get(f"{ESPN_ATHLETE}/{aid}")
            bios[aid] = _trim_bio(data.get("athlete", data))
        except Exception as e:  # noqa: BLE001 — cache the miss, keep going
            print(f"  WARNING: bio fetch failed for {aid}: {e}")
            bios[aid] = {}
        if i % 25 == 0 or i == len(missing):
            BIOS_PATH.write_text(json.dumps(bios, indent=1))
            print(f"  {i}/{len(missing)}")
        time.sleep(0.4)  # politeness between calls; retries live in espn_get
    return bios


# ── Ranks for the context cards ───────────────────────────────────────────

def compute_card_ranks(season):
    """Rank among QUALIFIED players for the three counting cards, using the
    same rule as compute_leaders (docs/wnba-leader-qualification-rules.md):
    team-prorated volume floor OR 70% of team games. Rank on the unrounded
    _raw column, ties share the lower ordinal. NaN = not qualified."""
    scale = season["TEAM_GP"] / 44.0
    min_gp = (0.70 * season["TEAM_GP"]).round().clip(lower=1)
    q_gp = season["GP"] >= min_gp
    quals = {
        "PPG": (season["PTS"] >= 525 * scale) | q_gp,
        "RPG": (season["TRB"] >= 250 * scale) | q_gp,
        "APG": (season["AST"] >= 150 * scale) | q_gp,
    }
    return {
        stat: season[stat + "_raw"].where(q).rank(ascending=False, method="min")
        for stat, q in quals.items()
    }


def assert_ranks_match_leaders(season, ranks):
    """Build-time assertion: the card badges must agree with compute_leaders —
    the implementation of docs/wnba-leader-qualification-rules.md that
    validate_stats.py diffs against stats.wnba.com every morning. If the two
    rank computations ever drift (a qualification tweak lands in one place),
    227 pages of badges go quietly wrong; at this scale a manual check does
    not hold, so the build refuses instead."""
    leaders = bsp.compute_leaders(season, full=True)
    by_id = season.reset_index().set_index("athlete_id")["index"]
    for cat, stat in (("Scoring", "PPG"), ("Rebounds", "RPG"),
                      ("Assists", "APG")):
        prev_raw, prev_rank = None, 0
        for pos, (_, row) in enumerate(leaders[cat].iterrows(), start=1):
            # method="min" semantics: a tie shares the higher (lower-numbered)
            # rank, so expected rank only advances when the raw value drops.
            exp = prev_rank if row["_raw"] == prev_raw else pos
            got = ranks[stat].loc[by_id[row["athlete_id"]]]
            assert pd.notna(got) and int(got) == exp, (
                f"rank drift vs compute_leaders: {row['Player']} is #{pos} "
                f"on the {cat} board but card rank for {stat} is {got!r} "
                f"(expected {exp}). compute_card_ranks and compute_leaders "
                "no longer implement the same qualification rule."
            )
            prev_raw, prev_rank = row["_raw"], exp


def league_ts_avg(season):
    """League TS% from league totals — every point and attempt, not a mean of
    player rates (which would weight a 10-minute player like a 33-minute one)."""
    return season["PTS"].sum() / (2 * (season["FGA"].sum()
                                       + 0.44 * season["FTA"].sum())) * 100


# ── Sentences ─────────────────────────────────────────────────────────────

def generated_sentence(bio):
    """The Tier 2 sentence, from structured fields only — never a claim a
    game could falsify. Returns None (Tier 3: omit) when the sentence would
    only repeat the identity header: height, year and jersey all render up
    there already, so a sentence carrying nothing else — the shape ESPN's
    data produces for internationals with no college/draft record — adds
    zero information. A thin sentence is worse than none."""
    if not bio:
        return None
    parts = []
    has_new_fact = False
    height = parse_height(bio.get("height"))
    posword = POSITION_WORDS.get(bio.get("position") or "", None)
    exp = parse_experience(bio.get("experience"))
    lead_bits = " ".join(p for p in (height, posword) if p)
    if lead_bits and exp is not None:
        season_word = ("rookie season" if exp == 0
                       else f"{ordinal_word(exp)} season")
        parts.append(f"{lead_bits.capitalize()}, {season_word}.")
    elif lead_bits:
        parts.append(f"{lead_bits.capitalize()}.")
    if bio.get("college"):
        parts.append(f"College: {esc(bio['college'])}.")
        has_new_fact = True
    draft = parse_draft(bio.get("draft"))
    if draft:
        has_new_fact = True
        year, rd, pk = draft
        if rd == 1:
            parts.append(f"Drafted: {year}, {ordinal_word(pk)} overall.")
        else:
            # Later rounds: ESPN's pick numbering isn't verified as overall,
            # so say only what's certain.
            parts.append(f"Drafted: {year}, round {rd}.")
    if bio.get("jersey"):
        parts.append(f"Jersey: {WNBA.jersey_prefix}{esc(str(bio['jersey']))}.")
    return " ".join(parts) if has_new_fact else None


# ── Page pieces ───────────────────────────────────────────────────────────

# Page-specific styles; the shared chrome (tokens, masthead, footer,
# scroll fade) is prepended in PAGE_CSS below.
_CARD_CSS = """\
body{font-family:'Courier New',monospace;background:var(--bg);color:var(--text);
  font-size:13px;padding:14px 10px;max-width:480px;margin:0 auto;line-height:1.45}
a{color:var(--muted)}
.pf{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:13px}
.pf h1{color:var(--accent);font-size:15px;margin-bottom:2px;font-weight:normal}
.mu{color:var(--muted);font-size:10px}
.ac{color:var(--accent)}
.hd{display:flex;gap:11px;align-items:flex-start}
.mono{width:60px;height:60px;background:var(--surface);border:1px solid var(--border);
  flex:0 0 auto;display:flex;flex-direction:column;align-items:center;justify-content:center;line-height:1}
.mono .tm{color:var(--muted);font-size:9px}
.mono .num{color:var(--accent);font-size:23px;margin-top:4px}
.slot-ed{font-size:11px;line-height:1.65;margin-top:10px}
.slot-gen{color:var(--muted);font-size:10px;line-height:1.7;margin-top:10px}
.grid4{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-top:10px}
.card{background:var(--surface);padding:7px 8px}
.card .big{font-size:19px}
/* the sub-line reserves its height whether or not it has content — keeps the 2-up grid even */
.card .sub{font-size:10px;color:var(--muted);min-height:14px;line-height:14px}
.card .sub.badge{color:var(--accent)}
details.exp{margin-top:9px}
details.exp>summary{background:var(--surface);border:1px solid var(--border);color:var(--muted);
  font-size:10px;padding:6px 9px;cursor:pointer;list-style:none;text-align:left}
details.exp>summary::-webkit-details-marker{display:none}
details.exp>summary::before{content:"+ "}
details.exp[open]>summary::before{content:"\\2212 "}
details.exp>summary:hover{border-color:var(--accent);color:var(--accent)}
.sec{color:var(--accent);font-size:10px;letter-spacing:1px;text-transform:uppercase;
  border-bottom:1px solid var(--border);padding-bottom:4px;margin:12px 0 6px}
.pf table.s{border-collapse:collapse;width:100%;white-space:nowrap;font-size:10px}
.pf table.s th{color:var(--muted);text-align:left;padding:3px 4px;
  border-bottom:1px solid var(--border);font-weight:normal}
.pf table.s td{padding:3px 4px;border-bottom:1px solid var(--border)}
details.inl{display:inline-block}
/* Tap-target padding lives on the summary; the dotted "clickable" cue lives
   on the inner span so it underlines the text itself rather than drawing at
   the padded box's bottom edge, where it collided with the number below. */
details.inl>summary{list-style:none;cursor:pointer;
  display:inline-block;padding:6px 10px 6px 0;margin:-6px 0 -6px 0}
details.inl>summary::-webkit-details-marker{display:none}
details.inl>summary .t{border-bottom:1px dotted var(--muted)}
details.inl[open]>summary{color:var(--accent)}
details.inl[open]>summary .t{border-bottom-color:var(--accent)}
details.inl .body{position:absolute;width:215px;background:#000;border:1px solid var(--accent);
  padding:8px 9px;font-size:10px;line-height:1.65;color:var(--text);z-index:20;margin-top:5px}
.data-note{color:var(--muted);font-size:10px;margin-top:14px}
"""

PAGE_CSS = (
    chrome.TOKENS_CSS
    + "*{box-sizing:border-box;margin:0;padding:0}\n"
    + _CARD_CSS
    + chrome.SUBPAGE_HEADER_CSS
    + chrome.SCROLL_FADE_CSS
    + chrome.SITE_FOOTER_CSS
)

# The splits expand is sticky per VISITOR, not per page: a returning reader
# who wants the dense tables open shouldn't re-click on every page. Opening
# it also sends the depth beacon — the signal that a landing became a read.
STICKY_JS = """\
var d=document.querySelector('details.exp');
if(d){try{if(localStorage.getItem('sag-expand')==='1')d.open=true}catch(e){}
d.addEventListener('toggle',function(){
  try{localStorage.setItem('sag-expand',d.open?'1':'0')}catch(e){}
  if(d.open)track('expand','__PAGE_KEY__')})}
"""


def page_js(page_key):
    """Page JS for one subpage. The SAME `page_key` identifies the page on
    both its pageview and its expand event, so "did arrivals on this page
    read it?" is one grouping rather than a join across two conventions."""
    return (chrome.usage_js(WNBA.slug, page_key)
            + "\n" + STICKY_JS.replace("__PAGE_KEY__", page_key)
            + "\n" + chrome.SCROLL_FADE_JS)


def head_html(title, path, description, jsonld=None):
    """Shared <head> for every page under /players/: canonical + meta +
    Open Graph/Twitter, plus optional JSON-LD. All URLs absolute."""
    canonical = seo.canonical_url(WNBA, path)
    og_image = f"{WNBA.base_url}/og.png"
    parts = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{esc(title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
        f'<link rel="canonical" href="{esc(canonical)}">',
        '<meta property="og:type" content="profile">',
        '<meta property="og:site_name" content="statsataglance">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:url" content="{esc(canonical)}">',
        f'<meta property="og:image" content="{esc(og_image)}">',
        '<meta name="twitter:card" content="summary">',
        f'<meta name="twitter:title" content="{esc(title)}">',
        f'<meta name="twitter:description" content="{esc(description)}">',
    ]
    if jsonld:
        parts.append(f'<script type="application/ld+json">{jsonld}</script>')
    parts.append(f"<style>{PAGE_CSS}</style>")
    return "\n".join(parts)


def card_html(label_html, value, sub_html="", accent=False):
    big_cls = "big ac" if accent else "big"
    return (f'<div class="card"><div class="mu">{label_html}</div>'
            f'<div class="{big_cls}">{value}</div>{sub_html}</div>')


def counting_card(stat, value, rank, accent=False):
    if pd.notna(rank) and rank <= WNBA.rank_badge_top_n:
        sub = (f'<div class="sub badge">{ordinal(rank)} in '
               f'{esc(WNBA.display_name)}</div>')
    else:
        sub = '<div class="sub"></div>'
    return card_html(stat, bsp.f1(value), sub, accent=accent)


def ts_card(ts_value, lg_avg):
    label = (f'<details class="inl" name="glossary">'
             f'<summary><span class="t">TS%</span></summary>'
             f'<span class="body">{TS_DEFINITION}</span></details>')
    sub = (f'<div class="sub">{esc(WNBA.display_name)} avg '
           f'{fmt_ts(lg_avg)}</div>')
    return card_html(label, fmt_ts(ts_value), sub)


def _scroll_table(inner):
    """Wide-table wrapper: the shared chrome's fade + swipe, same structure
    the tab site uses (.table-scroll > .table-wrap)."""
    return (f'<div class="table-scroll"><div class="table-wrap">{inner}'
            f'</div></div>')


def season_splits_table(row):
    """Column-for-column the Players tab's stat set (build_players_section's
    col_labels minus the Player cell — the page header is the name), in the
    same order, totals where the tab shows totals. Wide on purpose; the
    shared scroll fade provides the swipe cue, same as the live site."""
    gp = row["GP"]
    heads = ["MPG", "PPG", "GP", "FG", "FG%", "3PT", "3PT%", "FT", "FT%",
             "OR", "DR", "TR", "A", "ST", "B", "TO", "PF"]
    cells = [
        bsp.f1(row["MIN"] / gp), bsp.f1(row["PTS"] / gp), int(gp),
        bsp.ma(row["FGM"], row["FGA"]), bsp.f1(bsp.pct(row["FGM"], row["FGA"])),
        bsp.ma(row["TPM"], row["TPA"]), bsp.f1(bsp.pct(row["TPM"], row["TPA"])),
        bsp.ma(row["FTM"], row["FTA"]), bsp.f1(bsp.pct(row["FTM"], row["FTA"])),
        int(row["ORB"]), int(row["DRB"]), int(row["TRB"]), int(row["AST"]),
        int(row["STL"]), int(row["BLK"]), int(row["TOV"]), int(row["PF"]),
    ]
    return _scroll_table(
        '<table class="s"><tr>'
        + "".join(f"<th>{h}</th>" for h in heads) + "</tr><tr>"
        + "".join(f"<td>{c}</td>" for c in cells) + "</tr></table>")


def build_game_meta(team_raw):
    """(game_id, team_abbr) -> (opp_abbr, 'W 89-81' result string)."""
    meta = {}
    by_game = {}
    for _, r in team_raw.iterrows():
        by_game.setdefault(r["game_id"], []).append(r)
    for gid, rows in by_game.items():
        if len(rows) != 2:
            continue
        for me, them in ((rows[0], rows[1]), (rows[1], rows[0])):
            wl = "W" if me["team_winner"] else "L"
            res = f'{wl} {int(me["team_score"])}-{int(them["team_score"])}'
            meta[(gid, me["team_abbreviation"])] = (them["team_abbreviation"], res)
    return meta


def game_log_table(games, game_meta):
    heads = ["Date", "Opp", "Res", "MIN", "PTS", "REB", "AST", "STL",
             "BLK", "TO", "FG", "3P", "FT", "+/-"]
    rows = []
    for _, g in games.iterrows():
        opp, res = game_meta.get(
            (g["game_id"], g["team_abbreviation"]), ("?", ""))
        date = pd.Timestamp(g["game_date"]).strftime("%b %-d")
        at = "@" if g["home_away"] == "away" else "vs"
        pm = g.get("plus_minus")
        pm = "-" if pd.isna(pm) else f"{int(pm):+d}"
        cells = [date, f"{at} {opp}", res, int(g["minutes"] or 0),
                 int(g["points"]), int(g["rebounds"]), int(g["assists"]),
                 int(g["steals"]), int(g["blocks"]), int(g["turnovers"]),
                 bsp.ma(g["field_goals_made"], g["field_goals_attempted"]),
                 bsp.ma(g["three_point_field_goals_made"],
                        g["three_point_field_goals_attempted"]),
                 bsp.ma(g["free_throws_made"], g["free_throws_attempted"]),
                 pm]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return _scroll_table(
        '<table class="s"><tr>'
        + "".join(f"<th>{h}</th>" for h in heads) + "</tr>"
        + "".join(rows) + "</table>")


def render_page(row, bio, ranks, lg_ts, games, game_meta,
                team_names, data_through, hooks):
    name = row["athlete_display_name"]
    slug = seo.slugify(name)
    abbr = row["team_abbreviation"]
    team_full = team_names.get(abbr, abbr)
    pos = row["athlete_position_abbreviation"] or ""
    bio = bio or {}

    jersey = bio.get("jersey")
    mono_num = f"{WNBA.jersey_prefix}{jersey}" if jersey else (pos or "—")

    id_line1 = " · ".join(p for p in (esc(team_full), esc(pos)) if p)
    exp = parse_experience(bio.get("experience"))
    yr = None if exp is None else ("Rookie" if exp == 0 else f"Yr {exp}")
    line2_bits = [parse_height(bio.get("height")),
                  esc(bio["college"]) if bio.get("college") else None, yr]
    id_line2 = " · ".join(b for b in line2_bits if b)

    editorial = (hooks.get(slug) or {}).get("sentence")
    generated = generated_sentence(bio)

    cards = "".join([
        counting_card("PPG", row["PPG"], ranks["PPG"], accent=True),
        counting_card("RPG", row["RPG"], ranks["RPG"]),
        counting_card("APG", row["APG"], ranks["APG"]),
        ts_card(row["TS%_raw"], lg_ts),
    ])

    parts = [f'<div class="hd">'
             f'<div class="mono"><div class="tm">{esc(abbr)}</div>'
             f'<div class="num">{esc(mono_num)}</div></div>'
             f'<div style="min-width:0"><h1>{esc(name)}</h1>'
             f'<div class="mu">{id_line1}</div>'
             + (f'<div class="mu" style="margin-top:3px">{id_line2}</div>'
                if id_line2 else "")
             + "</div></div>"]
    if editorial:
        parts.append(f'<p class="slot-ed">{esc(editorial)}</p>')
    parts.append(f'<div class="grid4">{cards}</div>')
    if generated:
        parts.append(f'<p class="slot-gen">{generated}</p>')
    parts.append(
        '<details class="exp"><summary>full splits &amp; game log</summary>'
        '<div class="sec">Full season</div>' + season_splits_table(row)
        + '<div class="sec">Game log</div>'
        + game_log_table(games, game_meta) + "</details>")

    path = f"/players/{slug}/"
    title = f"{name} — {WNBA.display_name} {WNBA.season} stats at a glance"
    description = (
        f"{name} ({team_full}) {WNBA.season} {WNBA.display_name} season "
        f"stats: {bsp.f1(row['PPG'])} PPG, {bsp.f1(row['RPG'])} RPG, "
        f"{bsp.f1(row['APG'])} APG. Fast, ad-free, updated every morning.")
    jsonld = seo.person_jsonld(name, seo.canonical_url(WNBA, path), team_full)

    masthead = chrome.subpage_header_html(
        esc(SITE_TITLE), "/",
        crumb_html='<a href="/players/">← all players</a>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{head_html(title, path, description, jsonld)}
</head>
<body>
{masthead}<div class="pf">{"".join(parts)}</div>
<div class="data-note">Stats through games of {data_through}</div>
{chrome.SITE_FOOTER_HTML}<script>{page_js(analytics_key(slug))}</script>
{chrome.cf_beacon_html(WNBA.cf_analytics_token)}</body>
</html>
"""


def render_index(entries, data_through):
    """The public players index at /players/ — every player page, one row
    each, alphabetical by last name. Doubles as the crawl hub the sitemap
    and the Players-tab links converge on."""
    def sort_key(e):
        parts = e["name"].split()
        return (parts[-1].lower(), e["name"].lower())

    rows = "".join(
        f'<tr><td><a href="/players/{e["slug"]}/">{esc(e["name"])}</a></td>'
        f'<td>{esc(e["team"])}</td><td>{e["gp"]}</td><td>{e["ppg"]}</td></tr>'
        for e in sorted(entries, key=sort_key))

    title = f"{WNBA.display_name} {WNBA.season} player pages — At a Glance"
    description = (
        f"One page per {WNBA.display_name} player: {WNBA.season} season "
        "stats at a glance, updated every morning. Fast, ad-free.")
    masthead = chrome.subpage_header_html(
        esc(SITE_TITLE), "/",
        crumb_html=f'{len(entries)} players · stats through {data_through}')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{head_html(title, "/players/", description)}
<style>
table{{border-collapse:collapse;width:100%;font-size:11px}}
th{{color:var(--muted);text-align:left;padding:4px 6px;border-bottom:1px solid var(--border);font-weight:normal}}
td{{padding:4px 6px;border-bottom:1px solid var(--border)}}
td a{{color:var(--text);text-decoration:underline;text-decoration-color:rgba(136,136,136,0.5);text-underline-offset:2px}}
td a:hover{{color:var(--accent)}}
</style>
</head>
<body style="max-width:640px">
{masthead}<table><tr><th>Player</th><th>Team</th><th>GP</th><th>PPG</th></tr>{rows}</table>
{chrome.SITE_FOOTER_HTML}<script>{page_js(INDEX_ANALYTICS_KEY)}</script>
{chrome.cf_beacon_html(WNBA.cf_analytics_token)}</body>
</html>
"""


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true",
                    help="use cached bios only; missing bios render Tier 3")
    args = ap.parse_args()

    player_raw, team_raw = bsp.load_data()
    season = bsp.compute_player_season(player_raw)
    season = season.reset_index(drop=True)

    through_dt = pd.Timestamp(player_raw["game_date"].max())
    data_through = through_dt.strftime("%B %-d, %Y")
    data_through_iso = through_dt.strftime("%Y-%m-%d")

    bios = load_or_fetch_bios(list(season["athlete_id"]), fetch=not args.no_fetch)
    hooks = load_hooks()
    ranks = compute_card_ranks(season)
    assert_ranks_match_leaders(season, ranks)
    lg_ts = league_ts_avg(season)
    game_meta = build_game_meta(team_raw)
    team_names = (team_raw.drop_duplicates("team_abbreviation")
                  .set_index("team_abbreviation")["team_display_name"]
                  .to_dict())

    # Slug collisions would silently overwrite a page — fail loudly instead.
    slugs = season["athlete_display_name"].map(seo.slugify)
    dupes = slugs[slugs.duplicated()].tolist()
    assert not dupes, f"slug collision: {dupes}"

    # Same posture for the analytics keys: every "player:<slug>" must fit the
    # worker's slice (ANALYTICS_KEY_MAX) or two players' pageview and expand
    # events merge into one key with no signal — we'd read the number and
    # believe it.
    over = [(s, len(analytics_key(s)) - ANALYTICS_KEY_MAX)
            for s in slugs if len(analytics_key(s)) > ANALYTICS_KEY_MAX]
    assert not over, (
        "analytics key overflow — silent collision risk, widen the worker "
        "slice and ANALYTICS_KEY_MAX together: "
        + ", ".join(f"player:{s} (+{n} over)" for s, n in over))

    # A hooks entry for a player who no longer renders is stale editorial;
    # warn here (the page just doesn't exist), hard-fail in validate_hooks.py.
    orphans = set(hooks) - set(slugs)
    if orphans:
        print(f"WARNING: hooks.json entries with no rendered page: "
              f"{sorted(orphans)}")

    pr = player_raw.copy()
    pr["athlete_id"] = pr["athlete_id"].astype(str)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    n_badged = 0
    for i, row in season.iterrows():
        slug = slugs.loc[i]
        bio = bios.get(row["athlete_id"], {})
        row_ranks = {s: ranks[s].loc[i] for s in ranks}
        games = pr[pr["athlete_id"] == row["athlete_id"]].sort_values(
            "game_date", ascending=False)
        html = render_page(row, bio, row_ranks, lg_ts, games, game_meta,
                           team_names, data_through, hooks)
        page_dir = OUT_DIR / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(html)

        badges = sum(1 for s in row_ranks
                     if pd.notna(row_ranks[s])
                     and row_ranks[s] <= WNBA.rank_badge_top_n)
        n_badged += bool(badges)
        entries.append({"slug": slug, "name": row["athlete_display_name"],
                        "team": row["team_abbreviation"], "gp": int(row["GP"]),
                        "ppg": bsp.f1(row["PPG"]),
                        "tier": ("1" if slug in hooks
                                 else "2" if generated_sentence(bio)
                                 else "3")})

    (OUT_DIR / "index.html").write_text(render_index(entries, data_through))

    # This step knows every published URL, so the per-host SEO files are
    # emitted here: sitemap.xml (all pages, lastmod = the data date) and
    # robots.txt (minimal + absolute Sitemap line; see sag.seo.robots_txt
    # for why the Cloudflare Content Signals block is NOT replicated).
    paths = ["/", "/players/"] + [f"/players/{s}/" for s in slugs]
    seo.write_sitemap(WNBA, paths, data_through_iso)
    seo.write_robots(WNBA)

    n = len(entries)
    t1 = sum(1 for e in entries if e["tier"] == "1")
    t3 = sum(1 for e in entries if e["tier"] == "3")
    print(f"Wrote {n} player pages + index to {OUT_DIR}")
    print(f"  badges: {n_badged}/{n} pages carry at least one "
          f"({n_badged / n:.0%})")
    print(f"  tiers: {t1} hand-written · {n - t1 - t3} generated · "
          f"{t3} omitted")
    print(f"  league TS% avg: {fmt_ts(lg_ts)}")
    print(f"  sitemap: {len(paths)} URLs · robots.txt written")


if __name__ == "__main__":
    main()
