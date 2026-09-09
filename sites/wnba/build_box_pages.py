#!/usr/bin/env python3
"""Emit a standalone box-score page per PLAYOFF game at /games/<slug>/.

Decided 2026-09-08: playoff box scores become real pages with their own URLs,
canonicals and sitemap entries, the way the WWC site already does it, instead
of the inline JS toggle the Games tab uses. Side effect worth knowing: this
dissolves the shelved "deep-link box scores" problem from August — showGame()
was single-origin because there was no real URL to link to, and now there is.

**Scope boundary, confirmed with Jason: playoff games only, this season.**
Regular-season box scores stay on the inline pattern. A full retroactive
migration is banked as a separate post-season project — it would touch the
live daily build in the same week as everything else, right before the year's
highest-traffic day.

DELIBERATE DEVIATION from the handoff, which said to port
sites/wwc/build_wwc_pages.py's page_boxscore(). That instruction was written
on the observation that WWC "renders exactly the content the WNBA Games tab
already builds inline" — which is true, and is precisely the argument for NOT
porting it. build_stats_page already has working, WNBA-shaped renderers for
the line score, the team-stats block and each team's box (_line_score,
_team_stats_block, _team_table). This module reuses those, so:

  * the standalone page cannot drift from the inline one — they are the same
    code, not two implementations of one design;
  * the WNBA's own column set, TLA handling and correct-or-blank line-score
    behaviour come along for free;
  * a port would have added a second box-score renderer to this repo, which
    is the thing the shared `core/` exists to avoid.

The CSS is shared for the same reason: build_stats_page.GAMES_CSS was
extracted so both surfaces style from one string.
"""
from __future__ import annotations

import json
from html import escape as esc

import pandas as pd

from sag import seo
from sag.render import chrome

import build_stats_page as bsp
from config import WNBA

OUT_DIR = WNBA.public_dir / "games"
SITE_TITLE = f"{WNBA.display_name} {WNBA.season} — At a Glance"

#: See build_player_pages.ANALYTICS_KEY_MAX. "game:" plus a date-and-teams
#: slug overflows the worker's 32-char blob slice, so these pages key on the
#: game id, which is short, stable and unique. main() asserts the fit.
ANALYTICS_KEY_MAX = 32

PAGE_CSS = (
    chrome.tokens_css(WNBA.accent)
    + f"""\
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:{bsp.SANS};background:var(--bg);color:var(--text);
        font-size:13.5px;padding:14px 10px;max-width:640px;margin:0 auto;
        line-height:1.55;-webkit-font-smoothing:antialiased}}
  a{{color:var(--muted)}}
  h1{{color:var(--accent);font-size:16px;font-weight:700;letter-spacing:-.2px;
      margin-bottom:2px}}
  .rnd{{color:var(--muted);font-size:11.5px;margin-bottom:10px}}
  .ser{{color:var(--accent);font-size:12px;margin-top:2px}}
  .data-note{{color:var(--muted);font-size:10px;margin-top:14px}}
  .backl{{display:inline-block;color:var(--accent);font-size:12px;
      padding:6px 0 12px;text-decoration:none}}
  .backl:hover{{text-decoration:underline}}
"""
    # The SAME rules that style the inline box score on the tab site. One
    # source of truth — see build_stats_page.GAMES_CSS.
    + bsp.GAMES_CSS
    + chrome.SUBPAGE_HEADER_CSS
    + chrome.SITE_FOOTER_CSS
)


#: The URL is formed in exactly one place — build_stats_page.game_slug —
#: so the Series tab that LINKS to these pages and this module that WRITES
#: them cannot disagree. Re-exported here for readability at call sites.
game_slug = bsp.game_slug


def analytics_key(game_id):
    return f"game:{game_id}"


def series_by_game(path=None):
    """game_id -> series row, from the file fetch_data.parse_series() writes.

    Absent for the whole regular season, which is correct rather than an
    error: there are no series until there are playoffs.
    """
    p = path or WNBA.series
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
    except Exception as e:
        print(f"WARNING: {p.name} unreadable ({e}) — round labels omitted.")
        return {}
    return {int(r["game_id"]): r for r in d.get("games", [])}


def render_page(player_all, team_all, linescores, gid, date_iso, slug,
                series_row, data_through):
    """One box-score page, rendered from build_stats_page's own components."""
    g = player_all[player_all["game_id"] == gid]
    away, home = bsp._game_sides(player_all, team_all, gid, date_iso)

    # Score orientation: this heading names BOTH teams, so it takes fixture
    # order (away first) rather than either team's own order.
    matchup = f"{away['name']} at {home['name']}"
    headline = (series_row or {}).get("headline") or ""
    summary = (series_row or {}).get("summary") or ""

    body = (
        f'<a class="backl" href="/#games">&larr; Back to Games</a>'
        f"<h1>{esc(matchup)}</h1>"
        f'<div class="rnd">{esc(bsp._dow(pd.Timestamp(date_iso).date()))}'
        + (f" &middot; {esc(headline)}" if headline else "")
        + "</div>"
        + (f'<div class="ser">{esc(summary)}</div>' if summary else "")
        + '<div class="gm-hd">'
        + _side(away, away, home) + '<div class="gm-fin">Final</div>'
        + _side(home, away, home) + "</div>"
        + bsp._line_score(linescores, gid, away, home)
        + '<h2 class="gm-h2">Team Stats</h2>'
        + bsp._team_stats_block(g, away, home)
        + '<h2 class="gm-h2">Box Score</h2>'
        + bsp._team_table(g, away) + bsp._team_table(g, home)
    )

    path = f"/games/{slug}/"
    title = (f"{away['abbr']} {away['score']}, {home['abbr']} {home['score']} "
             f"— {date_iso} box score | {WNBA.display_name} at a Glance")
    description = (
        f"Full box score: {away['name']} {away['score']}, {home['name']} "
        f"{home['score']}, {date_iso}"
        + (f" ({headline})" if headline else "")
        + ". Line score, team stats and every player. Fast and ad-free.")

    head = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{esc(title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
        f'<link rel="canonical" href="{esc(seo.canonical_url(WNBA, path))}">',
        *seo.social_tags(WNBA, path, title, description, card="summary"),
        f"<style>{PAGE_CSS}</style>",
    ]
    masthead = chrome.subpage_header_html(
        esc(SITE_TITLE), "/", crumb_html='<a href="/games/">← all box scores</a>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{chr(10).join(head)}
</head>
<body>
{masthead}{body}
<div class="data-note">Stats through games of {data_through}</div>
{chrome.SITE_FOOTER_HTML}
<script>{chrome.usage_js(WNBA.slug, analytics_key(gid))}
{chrome.SCROLL_FADE_JS}</script>
{chrome.cf_beacon_html(WNBA.cf_analytics_token)}</body>
</html>
"""


def _side(m, away, home):
    """One team's line in the page header. Mirrors _box_section's `hd`."""
    winner = home if home["score"] > away["score"] else away
    cls = " gm-win" if m is winner else ""
    return (f'<div><div><span class="gm-tm{cls}">{esc(m["abbr"])}</span> '
            f'<span class="gm-rec">{m["w"]}-{m["l"]}</span></div>'
            f'<div class="gm-sc{cls}">{m["score"]}</div></div>')


def render_index(entries, data_through):
    rows = "".join(
        f'<tr><td><a href="/games/{e["slug"]}/">{esc(e["matchup"])}</a></td>'
        f'<td>{esc(e["date"])}</td><td>{esc(e["score"])}</td>'
        f'<td>{esc(e["round"])}</td></tr>' for e in entries)
    title = f"{WNBA.display_name} {WNBA.season} playoff box scores — At a Glance"
    description = (f"Every {WNBA.display_name} {WNBA.season} playoff box score, "
                   "one page each. Fast, ad-free, updated every morning.")
    head = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{esc(title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
        f'<link rel="canonical" href="{esc(seo.canonical_url(WNBA, "/games/"))}">',
        *seo.social_tags(WNBA, "/games/", title, description),
        f"<style>{PAGE_CSS}</style>",
        "<style>table.s{border-collapse:collapse;width:100%;font-size:12px}"
        "table.s th{color:var(--muted);text-align:left;padding:5px;"
        "border-bottom:1px solid var(--border);font-weight:normal;"
        "text-transform:uppercase;font-size:10px;letter-spacing:.4px}"
        "table.s td{padding:6px 5px;border-bottom:1px solid var(--border)}"
        "table.s td a{color:var(--text);text-decoration:underline;"
        "text-decoration-color:rgba(136,136,136,.45);text-underline-offset:2px}"
        "table.s td a:hover{color:var(--accent)}</style>",
    ]
    masthead = chrome.subpage_header_html(esc(SITE_TITLE), "/")
    body = (f"<h1>Playoff box scores</h1>"
            f'<div class="rnd">{len(entries)} game'
            f'{"" if len(entries)==1 else "s"}</div>'
            f'<table class="s"><tr><th>Game</th><th>Date</th><th>Score</th>'
            f"<th>Round</th></tr>{rows}</table>"
            if entries else
            "<h1>Playoff box scores</h1>"
            '<div class="rnd">The playoffs have not started yet. Box scores '
            "for every playoff game will appear here.</div>")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{chr(10).join(head)}
</head>
<body>
{masthead}{body}
<div class="data-note">Stats through games of {data_through}</div>
{chrome.SITE_FOOTER_HTML}
<script>{chrome.usage_js(WNBA.slug, "games")}</script>
{chrome.cf_beacon_html(WNBA.cf_analytics_token)}</body>
</html>
"""


def playoff_games(player_all, team_all):
    """[(game_id, date_iso)] for postseason games, oldest first.

    Gated on season_type == 3 in the box-score data — the same gate
    parse_series() uses, and for the same reason: the presence of a series
    object is not a reliable postseason signal.
    """
    if "season_type" not in team_all.columns:
        return []
    post = team_all[team_all["season_type"] == 3]
    return sorted({(int(r["game_id"]), str(r["game_date"]))
                   for _, r in post.iterrows()}, key=lambda t: (t[1], t[0]))


def team_name_map(team_all):
    return (team_all.drop_duplicates("team_abbreviation")
            .set_index("team_abbreviation")["team_display_name"].to_dict())


def page_paths(player_all, team_all):
    """Every /games/ URL this module would write, WITHOUT writing anything.

    Exists so build_team_pages can put these in the sitemap without importing
    side effects, and so the sitemap, the Series tab and the emitter all form
    the URL through the single bsp.game_slug().
    """
    names = team_name_map(team_all)
    out = []
    for gid, date_iso in playoff_games(player_all, team_all):
        try:
            away, home = bsp._game_sides(player_all, team_all, gid, date_iso)
        except (IndexError, KeyError):
            continue
        out.append(f"/games/{game_slug(date_iso, away['abbr'], home['abbr'], names)}/")
    return out


def main():
    player_all, team_all = bsp.load_all_games()

    # Dated from the ALL-GAMES frame, not the regular-season one. These pages
    # show playoff games, so the regular-season max would understate them by
    # weeks — and on a frame containing only postseason rows (as in the 2025
    # simulation) it is NaT, which is how this was caught.
    dates = pd.to_datetime(team_all["game_date"], errors="coerce").dropna()
    data_through = (dates.max().strftime("%B %-d, %Y") if len(dates)
                    else "—")

    team_names = (team_all.drop_duplicates("team_abbreviation")
                  .set_index("team_abbreviation")["team_display_name"].to_dict())
    linescores = {}
    if WNBA.linescores.exists():
        try:
            linescores = json.loads(WNBA.linescores.read_text())
        except Exception:
            linescores = {}
    series = series_by_game()

    games = playoff_games(player_all, team_all)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for gid, date_iso in games:
        away, home = bsp._game_sides(player_all, team_all, gid, date_iso)
        slug = game_slug(date_iso, away["abbr"], home["abbr"], team_names)
        key = analytics_key(gid)
        assert len(key) <= ANALYTICS_KEY_MAX, f"analytics key too long: {key}"
        html = render_page(player_all, team_all, linescores, gid, date_iso,
                           slug, series.get(gid), data_through)
        d = OUT_DIR / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(html)
        entries.append({
            "slug": slug, "date": date_iso,
            "matchup": f"{away['abbr']} at {home['abbr']}",
            "score": f"{away['score']}-{home['score']}",
            "round": (series.get(gid) or {}).get("headline", ""),
        })

    # The index ships even with zero games. An event surface has an indexing
    # lead time that content quality cannot compress (2026-09-06 decision), so
    # /games/ wants to exist and be crawlable BEFORE the playoffs, not on the
    # day they start.
    (OUT_DIR / "index.html").write_text(render_index(entries, data_through))
    print(f"Wrote {len(entries)} playoff box-score page(s) + index to {OUT_DIR}")
    return [f"/games/{e['slug']}/" for e in entries]


if __name__ == "__main__":
    main()
