#!/usr/bin/env python3
"""build_wwc_pages.py — the WWC 2026 program-site emitter.

Purpose-built, NOT a fork of `build_stats_page.py` (decision 2026-08-19).
Forking would have copied 1,856 lines to get four static page types and
dragged along sortable tables, tab switching and box-score modals that a
tournament programme has no use for — the very machinery that makes that
file big is the machinery these pages don't have. The right seams for a
shared component layer are learned from the SECOND implementation, not
guessed before the first.

What it does import is the shared layer, and only the shared layer:

    sag.render.chrome   design tokens, footer, scroll fade, usage beacon
    sag.seo             slugify, canonical URLs, sitemap, robots

Those are the things that must not drift between sites. Everything else
here is this site's own components. That makes this a second CONSUMER of
`core/`, which is the point — a second consumer is what proves the shared
layer is league-agnostic rather than WNBA-shaped with the names filed off.

Styling is Option C (2026-08-23): the shared near-black ground, one
divergent token (`--accent`, cyan), and a typographic split that costs
`core/` nothing because `font-family` has always lived per-emitter —
system sans for prose, mono SCOPED to data cells. Prose was capped at ~34em
until 2026-08-28; it is now fluid to the 900px body so text and tables reflow
together — see the note above SITE_CSS. No webfonts, ever: single-file,
zero third-party origins, because performance is the brand.

── The three lifecycle states ────────────────────────────────────────────
Every element on every page has to work in all three, and the first is the
one the site lives in for its whole pre-tournament life:

    Program   now → Sep 3     no games played, no statistics anywhere
    Live      Sep 4 → Sep 13  1–8 games played, partial everything
    Archive   Sep 14 on       complete

The state is not a date switch — it is derived from whether results data
exists (`data/results.json`, written by a future FIBA fetch). A page with
no results renders the Program state, which means the Aug 31 publish and a
local run in December behave identically and neither needs a clock.

── Correct-or-blank ──────────────────────────────────────────────────────
Never a placeholder that looks like data. The round-1 prototype printed the
literal word "club" for every non-WNBA player, which reads like a value;
this renders an em dash. Mali's coach is `null` because FIBA names no
coach and nobody has been identified — the slot renders empty rather than
guessing. Same posture as the 2026-07-03 line-score fix.

Usage:
    .venv/bin/python sites/wwc/build_wwc_pages.py
    .venv/bin/python sites/wwc/build_wwc_pages.py --preview
        Additionally renders the tracked box-score FIXTURE, so the page
        template and data format can be inspected before real games exist.
        Never part of a publish — see `--preview` in main() for why.
"""

import argparse
import csv
import dataclasses
import json
import shutil
from collections import OrderedDict
from html import escape as esc

from sag import seo
from sag.render import chrome

from config import (GUIDE_IS_LANDING, GUIDE_TAB_LABEL, TOURNAMENT_NAME,
                    TOURNAMENT_STRAP, WWC)

# ── Routing ───────────────────────────────────────────────────────────────
# One switch decides the front door, and every URL, canonical, sitemap entry,
# nav highlight and analytics key derives from these two names. Nothing else
# in this file writes a path literal for these two surfaces, so flipping
# GUIDE_IS_LANDING cannot leave a stale link behind.
GUIDE_PATH = "/" if GUIDE_IS_LANDING else "/guide/"
GAMES_PATH = "/games/" if GUIDE_IS_LANDING else "/"

REF = WWC.site_dir / "reference"
TEAMS_JSON = REF / "wwc2026_teams.json"
SCHEDULE_CSV = REF / "wwc_schedule_2026.csv"

#: Results, written by a future FIBA fetch. Absent → the Program state.
RESULTS_JSON = WWC.data_dir / "results.json"
BOXSCORE_DIR = WWC.data_dir / "boxscores"
#: The tracked test fixture. `reference/`, not `data/`, by the standing
#: rule — "if I deleted this, could a machine get it back?" No: it is
#: hand-written to exercise the template before FIBA publishes anything.
BOXSCORE_FIXTURE = REF / "boxscore_fixture.json"


# ══ Data ══════════════════════════════════════════════════════════════════

def load_teams():
    doc = json.loads(TEAMS_JSON.read_text(encoding="utf-8"))
    return doc, {t["schedule_key"]: t for t in doc["teams"]}


def load_schedule():
    with SCHEDULE_CSV.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["game_id"] = game_id(r)
    ids = [r["game_id"] for r in rows]
    # A duplicate id would silently overwrite one box-score page with
    # another's — assert rather than discover it on a match day.
    assert len(set(ids)) == len(ids), f"duplicate game_id: {ids}"
    return rows


def game_id(row):
    """A stable, readable id for one game — it becomes a public URL.

    Group games key off date + the two teams, which are known now and never
    change. Knockout games CANNOT key off teams (they are TBD until the
    bracket resolves, and a slug that changes is a URL that 404s after it
    was indexed), so they key off FIBA's own game number where the schedule
    carries one, and off the phase where it does not — there is exactly one
    third-place game and one final.
    """
    if row["group"]:
        return (f'{row["date"]}-{seo.slugify(row["team_1"])}'
                f'-{seo.slugify(row["team_2"])}')
    if row["game_no"]:
        short = {"qualification_to_qf": "qqf", "quarter_final": "qf",
                 "semi_final": "sf"}[row["phase"]]
        return f'{short}-{row["game_no"]}'
    return row["phase"].replace("_", "-")


def load_results():
    """Game results, keyed by game_id. Empty dict = the Program state."""
    if not RESULTS_JSON.exists():
        return {}
    return json.loads(RESULTS_JSON.read_text(encoding="utf-8"))


def wnba_player_pages():
    """Slugs of every player page our own WNBA site actually publishes.

    This is the retention bridge and half the reason the WWC site exists,
    so it is built by LOOKING rather than by trusting: we link a name only
    when `sites/wnba/public/players/<slug>/` is really there. The reference
    data's own `player_slug` field is null for every player, and guessing
    from the name alone would emit 404s for the handful whose ESPN spelling
    differs from FIBA's (see ALIASES).
    """
    players = WWC.repo_root / "sites" / "wnba" / "public" / "players"
    if not players.is_dir():
        return set()
    return {p.name for p in players.iterdir() if p.is_dir()}


#: FIBA's spelling → the slug our own site publishes. Deliberately tiny and
#: hand-maintained; every entry is a real person whose two sources disagree.
#: Note Gustafson: the reference data is right to call her Gustafson (schema
#: doc, "not DiLeo"), and our site's URL for her is nevertheless /megan-dileo/
#: because that is what ESPN calls her. Display name and link target are
#: different questions and this map is where they meet.
ALIASES = {
    "Steph Talbot": "stephanie-talbot",
    "Megan Gustafson": "megan-dileo",
}


#: Every WNBA player link leaves this site, so all three of them carry this.
#:
#: The bridge is ONE-WAY, and that is the problem this solves. A WNBA player
#: page has a masthead link to `/` and an "all players" link, so a reader who
#: arrives there is not stranded on the WNBA site — but nothing on it points
#: back to the World Cup, because the WNBA build knows nothing about this one.
#: Opening in a new tab keeps the Cup tab alive underneath, which is the only
#: route back that exists today (Jason, 2026-08-30).
#:
#: `rel="noopener"` is explicit rather than leaning on the modern browser
#: default for `target="_blank"`: the default is right in current browsers and
#: costs nothing to state, and this is a link we hand to readers on phones
#: whose browser we do not choose.
#:
#: The real fix is reciprocal — a "playing at the World Cup" link on the WNBA
#: player page, which would make this a loop instead of a one-way street. It
#: needs the WNBA build to read `wwc2026_teams.json` and it moves WNBA bytes,
#: so it rides with the player-page work rather than ahead of it. Backlog,
#: 2026-08-30.
CROSS_SITE = 'target="_blank" rel="noopener"'


def player_href(name, published):
    """A link to our WNBA player page, or None. Correct-or-blank."""
    slug = ALIASES.get(name) or seo.slugify(name)
    return f"https://wnba.statsataglance.com/players/{slug}/" if slug in published else None


# ══ Presentation ══════════════════════════════════════════════════════════

MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"
SANS = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        "'Helvetica Neue',Arial,sans-serif")

# Option C, rendered from wbb-lab/wwc/prototypes/mock-round2 variant C.
#
# Mono is SCOPED, not global — .num/.big/.tla/.grp/.wn and nothing else.
# Applying it to `body` is what made round 1 hard to read: the Key page's
# comparison tables are prose in a table, not data, and mono fights them.
# Prose was capped at ~34em through 2026-08-27. That cap is GONE as of
# 2026-08-28, at Jason's direction: text is now fluid to the 900px body, so
# prose and tables reflow together. The old note called the cap the easiest
# thing to lose in a later edit — it was not lost, it was removed on purpose,
# and restoring it needs a decision, not a bug report. The measure that
# remains is `body{max-width:900px}`, which is now the only thing bounding
# line length. Adjacent .prose blocks get their own gap via `.prose+.prose`,
# because the `*{margin:0}` reset above kills the default paragraph margin
# and without it consecutive paragraphs render as one wall of text.
#
# --pos/--neg are WWC-local. They are NOT in the shared token block because no
# other site uses them yet; promoting them to `chrome` the moment a second site
# wants them is the cheap direction, inventing shared tokens for one consumer
# is not.
SITE_CSS = f"""\
  :root {{ --pos:#4caf50; --neg:#e05555; }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:{SANS};background:var(--bg);color:var(--text);
    font-size:13.5px;padding:16px;max-width:900px;margin:0 auto;
    line-height:1.55;-webkit-font-smoothing:antialiased}}
  a{{color:var(--muted)}} a:hover{{color:var(--accent)}}
  .num,.big,.tla,.grp,.wn{{font-family:{MONO};font-variant-numeric:tabular-nums}}
  table{{font-variant-numeric:tabular-nums}}
  .mast{{border-bottom:1px solid var(--border);padding-bottom:10px;margin-bottom:14px}}
  /* The title is now the tournament's full name in caps, not "WWC 2026" —
     28 characters where there were 8 (Jason, 2026-08-29). At a fixed 18px
     that wraps on a 375px iPhone (SE 3rd gen), leaving ~343px of usable
     width after the body's 16px padding. clamp() scales it instead of
     picking one size and hoping: ~17.6px at 375, full 19px on a Max. */
  .mast h1{{font-size:clamp(15px,4.7vw,19px);letter-spacing:.2px;
    font-weight:700;text-transform:uppercase;line-height:1.2}}
  .mast h1 a{{color:var(--accent);text-decoration:none}}
  /* Three steps of hierarchy from the existing tokens, no second accent:
     accent title, near-white brand, muted date. `stats at a glance` is the
     BRAND (Jason, 2026-08-29) and outranks the dateline, so it takes --text
     and the larger size; the tournament dates are reference, not identity. */
  .mast .brand{{color:var(--text);font-size:14.5px;margin-top:5px;
    font-weight:500}}
  .mast .strap{{color:var(--muted);font-size:11.5px;margin-top:2px}}
  /* Cross-site pointer to wnba.statsataglance.com. Mirrors .xsite on the
     WNBA site so the bridge looks the same from both ends. Kept to one
     short sentence: it must not exceed two lines on a phone. */
  .xsite{{font-size:11px;line-height:1.6;margin:0 0 16px;padding:7px 10px;
        border:1px solid var(--border);border-left:2px solid var(--accent);
        background:var(--surface);color:var(--muted)}}
  .xsite a{{color:var(--accent);text-decoration:none}}
  .xsite a:hover{{text-decoration:underline}}
  /* Tap targets, 2026-08-29. Was 13px text with 4px of bottom padding — a
     ~24px target against Apple's 44px guidance, and coloured var(--muted) by
     the generic `a` rule, so the nav read as ordinary body links rather than
     as navigation. Now: full --text colour, 11px vertical padding for a ~44px
     target, and the strip sits on a rule so the active tab's underline joins
     it. Still purely typographic — no pills, no cards. */
  nav{{display:flex;gap:20px;font-size:14px;margin:10px 0 20px;flex-wrap:wrap;
    border-bottom:1px solid var(--border)}}
  nav a{{text-decoration:none;padding:11px 2px;font-weight:600;
    color:var(--text);margin-bottom:-1px;border-bottom:2px solid transparent}}
  nav a:hover{{color:var(--accent)}}
  nav a.on{{color:var(--accent);border-bottom-color:var(--accent)}}
  h2.sec{{color:var(--accent);font-size:11.5px;letter-spacing:.9px;
    text-transform:uppercase;border-bottom:1px solid var(--border);
    padding-bottom:5px;margin:22px 0 9px;font-weight:700}}
  table{{border-collapse:collapse;width:100%;font-size:12px}}
  th{{color:var(--muted);text-align:left;font-weight:normal;padding:6px;
    border-bottom:1px solid var(--border);white-space:nowrap;
    font-size:11px;letter-spacing:.4px;text-transform:uppercase}}
  td{{padding:8px 6px;border-bottom:1px solid var(--border);vertical-align:top}}
  .mu{{color:var(--muted)}} .ac{{color:var(--accent)}} .r{{text-align:right}}
  .day{{color:var(--accent);font-size:12px;letter-spacing:.4px;
    margin:18px 0 5px;font-weight:700}}
  .grp{{display:inline-block;width:18px;height:18px;line-height:18px;
    text-align:center;background:var(--surface);border:1px solid var(--border);
    font-size:10px}}
  .card{{background:var(--surface);padding:9px 11px;border:1px solid var(--border)}}
  .grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:12px 0}}
  .card .lab{{font-size:9.5px;color:var(--muted);letter-spacing:.6px;
    font-weight:600;text-transform:uppercase}}
  .card .big{{font-size:22px;line-height:1.3;font-weight:600}}
  .card .sub{{font-size:10.5px;color:var(--muted);min-height:13px;font-family:{SANS}}}
  .card .sub a{{color:var(--accent);text-decoration:none}}
  .card .sub a:hover{{text-decoration:underline}}
  .hd{{display:flex;gap:12px;align-items:flex-start;margin-bottom:4px}}
  .crest{{width:60px;height:60px;background:var(--surface);
    border:1px solid var(--border);flex:0 0 auto;display:flex;
    flex-direction:column;align-items:center;justify-content:center;line-height:1}}
  .crest .flag{{font-size:24px}}
  .crest .tla{{color:var(--muted);font-size:10px;letter-spacing:1px;margin-top:5px}}
  .hd h2{{font-size:23px;color:var(--accent);line-height:1.1;
    font-weight:700;letter-spacing:-.3px}}
  .ed{{font-size:14.5px;line-height:1.65;margin:14px 0}}
  .prose{{font-size:14.5px;line-height:1.7}}
  .prose+.prose{{margin-top:14px}}
  /* Links inside prose take the accent. The generic `a` rule paints
     links --muted, which is DIMMER than the --text they sit in — an
     inline link would have been less visible than the sentence around
     it, which is backwards for the one affordance meant to be found. */
  .prose a{{color:var(--accent);text-decoration:none}}
  .prose a:hover{{text-decoration:underline}}
  .cnote{{color:var(--muted);font-size:11.5px;line-height:1.6;margin-top:4px}}
  .cnote b{{color:var(--text)}}
  .wn{{color:var(--accent);font-size:10px;border:1px solid var(--accent);
    padding:0 4px;border-radius:2px;white-space:nowrap}}
  .tag{{color:var(--muted);font-size:10px;border:1px solid var(--border);padding:0 4px}}
  .pend{{color:var(--muted);font-size:11.5px;line-height:1.6;
    border-left:2px solid var(--border);padding-left:9px;margin-top:9px}}
  /* End-of-page handoff. The Guide is the only page that dead-ends — Games
     and Groups are already grids of links to team pages — so this exists
     there and nowhere else. Accent left rule rather than a box: it reads as
     "onward" without adding furniture to a site whose look is its absence. */
  .next{{border-left:2px solid var(--accent);padding-left:11px;margin-top:22px;
    font-size:12.5px;line-height:1.75;color:var(--muted)}}
  .next b{{color:var(--text)}}
  .next a{{color:var(--accent);text-decoration:none}}
  .next a:hover{{text-decoration:underline}}
  .path{{background:var(--surface);padding:12px;font-size:12.5px;line-height:2;
    margin:8px 0;border:1px solid var(--border)}}
  .path b{{color:var(--accent)}}
  .tcards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:7px}}
  .tc{{background:var(--surface);border:1px solid var(--border);padding:9px 11px;
    text-decoration:none;color:var(--text);display:block}}
  .tc:hover{{border-color:var(--accent)}}
  .tc .n{{font-size:14.5px;font-weight:600}}
  .tc .m{{color:var(--muted);font-size:10.5px;margin-top:3px}}
  .score{{font-family:{MONO};font-size:15px;font-weight:600}}
  .win{{color:var(--text)}} .lose{{color:var(--muted)}}
  .pos{{color:var(--pos)}} .neg{{color:var(--neg)}}
  /* Inline tap-to-open note — the same `details.inl` pattern the WNBA
     player pages use for TS%, moved here rather than reinvented. A hover
     `title=` tooltip does not exist on touch, and this site is mobile-first,
     so the disclosure has to be a real tap target. No JavaScript: <details>
     does the whole job. Tap padding sits on the summary; the dotted cue sits
     on the inner span so it underlines the text rather than the padded box. */
  details.inl{{display:inline-block}}
  details.inl>summary{{list-style:none;cursor:pointer;display:inline-block;
    padding:6px 10px 6px 0;margin:-6px 0}}
  details.inl>summary::-webkit-details-marker{{display:none}}
  details.inl>summary .t{{border-bottom:1px dotted var(--muted)}}
  details.inl[open]>summary{{color:var(--accent)}}
  details.inl[open]>summary .t{{border-bottom-color:var(--accent)}}
  details.inl .body{{position:absolute;width:250px;background:#000;
    border:1px solid var(--accent);padding:9px 10px;font-size:11px;
    line-height:1.65;color:var(--text);z-index:20;margin-top:5px;
    font-family:{SANS}}}
  details.inl .body b{{color:var(--accent);font-weight:600}}
  .legend{{font-size:11.5px;color:var(--muted);margin:2px 0 10px}}
  /* ── Box score ──────────────────────────────────────────────────────── */
  .bx-hd{{display:flex;align-items:center;gap:12px;margin:6px 0 2px}}
  .bx-hd .side{{flex:1}}
  .bx-hd .side.r{{text-align:right}}
  .bx-hd .tm{{font-size:15px;font-weight:600}}
  .bx-hd .sc{{font-family:{MONO};font-size:30px;font-weight:700;line-height:1.1}}
  .bx-hd .lost .tm,.bx-hd .lost .sc{{color:var(--muted)}}
  .bx-hd .fin{{color:var(--muted);font-size:10.5px;letter-spacing:.6px;
    text-transform:uppercase;flex:0 0 auto;text-align:center}}
  .bx-meta{{color:var(--muted);font-size:11.5px;margin-bottom:12px}}
  .bx-cap{{font-size:13px;font-weight:600;margin:14px 0 5px}}
  table.bx td,table.bx th{{padding:5px 6px;font-size:11.5px;white-space:nowrap}}
  table.bx td:first-child,table.bx th:first-child{{text-align:left;
    white-space:normal;min-width:132px}}
  /* The 132px above is a PLAYER-name measure — "Breanna Stewart" plus a
     position span. The team-comparison table reuses `table.bx` but its first
     cell is only a flag and a three-letter code, so that min-width became
     ~80px of dead air between the country and FG, and pushed the last column
     off a phone screen (Jason, 2026-08-29). Scoped down here rather than
     lowering the shared value, which would start wrapping player names.
     Must stay AFTER the rule above: equal specificity, so source order wins. */
  table.ts td:first-child,table.ts th:first-child{{min-width:0;
    white-space:nowrap}}
  table.bx td:not(:first-child){{text-align:right;font-family:{MONO}}}
  table.bx th:not(:first-child){{text-align:right}}
  table.bx tr.sec td{{color:var(--accent);font-size:9.5px;letter-spacing:.8px;
    text-transform:uppercase;font-weight:700;border-bottom:1px solid var(--border);
    padding-top:9px}}
  .bx-pos{{color:var(--muted);font-size:10px;font-family:{MONO};
    display:inline-block;min-width:16px}}
  table.bx tr.tot td{{font-weight:700;border-top:1px solid var(--border)}}
  table.pct td{{color:var(--muted);font-size:10.5px;padding-top:0;
    border-bottom:1px solid var(--border)}}
  .bx-back{{font-size:11.5px;margin-bottom:10px;display:inline-block}}
"""

PAGE_CSS = (chrome.tokens_css(WWC.accent) + SITE_CSS
            + chrome.SCROLL_FADE_CSS + chrome.SITE_FOOTER_CSS)

# Nav is a list of (path, label), front door first. Leaders is DELIBERATELY
# ABSENT until Sep 4 — it is counting stats over games that have not been
# played, and a tab that leads to an empty board on launch day is worse than
# no tab at all.
def nav_items():
    guide = (GUIDE_PATH, GUIDE_TAB_LABEL)
    games = (GAMES_PATH, "Games")
    middle = [("/teams/", "Teams"), ("/groups/", "Groups")]
    # The front door leads. When Guide is landing it opens the nav; when
    # Games is landing, Guide returns to the trailing explainer slot the
    # WNBA site's "Key" tab occupies.
    if GUIDE_IS_LANDING:
        return [guide, games] + middle
    return [games] + middle + [guide]


# The WWC end of the bridge, and the mirror of wwc_promo_html() on the WNBA
# site. Site-level and data-free: no per-player logic, no read of any WNBA
# artifact — it points at the site, and that is the whole job.
#
# Deliberately ONE short sentence. It sits above the fold on every page and
# must not run past two lines on a phone, so it names the site and three
# things you can get there, and stops. CROSS_SITE opens it in a new tab, the
# same treatment the 86 player links already use, so a reader following it
# never loses their place in the tournament.
#
# Unlike the WNBA side's version this carries no end date. The WNBA site is
# not an event; there is nothing to expire.
WNBA_PROMO_HTML = (
    '<div class="xsite">'
    f'<a href="https://wnba.statsataglance.com/" {CROSS_SITE}>'
    'WNBA 2026 \u2014 At a Glance</a>'
    ' \u00b7 our companion site: standings, leaders, box scores.'
    '</div>'
)


def shell(path, title, body, description, extra_head=""):
    """One page. `path` is both the canonical URL and the nav highlight."""
    nav = "".join(
        f'<a href="{p}"{" class=\"on\"" if p == path else ""}>{esc(l)}</a>'
        for p, l in nav_items())
    canonical = seo.canonical_url(WWC, path)
    # The beacon's page key must fit the analytics worker's 32-char slice —
    # over-long keys COLLIDE rather than truncate (DEPLOY.md, blob2).
    key = analytics_key(path)
    assert len(key) <= 32, f"analytics page key too long: {key!r}"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:type" content="website">
{extra_head}<style>
{PAGE_CSS}</style>
</head>
<body>
<div class="mast">
  <h1><a href="/">Women\u2019s Basketball World Cup</a></h1>
  <div class="brand">stats at a glance</div>
  <div class="strap">{TOURNAMENT_STRAP}</div>
</div>
<nav>{nav}</nav>
{WNBA_PROMO_HTML}
{body}
{chrome.SITE_FOOTER_HTML}<script>
{chrome.usage_js(WWC.slug, key)}
{chrome.SCROLL_FADE_JS}</script>
</body>
</html>
"""


def analytics_key(path):
    """blob2 for the usage beacon. Mirrors the WNBA site's `player:<slug>`
    convention so one dataset answers cross-site questions.

    Keyed on the SURFACE, not on the URL, so that flipping which page is the
    front door does not rename a metric series. `/` is whichever surface is
    landing, and reporting "guide" for it either way is what keeps a
    before/after comparison of that switch readable at all.
    """
    if path == "/":
        return "guide" if GUIDE_IS_LANDING else "games"
    parts = [p for p in path.split("/") if p]
    return parts[0] if len(parts) == 1 else f"{parts[0][:-1]}:{parts[1]}"[:32]


def table_scroll(inner):
    """Wide tables scroll inside their own container with the shared fade —
    the page body must never scroll horizontally on a phone."""
    return f'<div class="table-scroll"><div class="table-wrap">{inner}</div></div>'


# ══ Shared bits ═══════════════════════════════════════════════════════════

DAYNAME = {
    "2026-09-04": "Fri 4 Sep", "2026-09-05": "Sat 5 Sep",
    "2026-09-06": "Sun 6 Sep", "2026-09-07": "Mon 7 Sep",
    "2026-09-08": "Tue 8 Sep", "2026-09-09": "Wed 9 Sep",
    "2026-09-10": "Thu 10 Sep", "2026-09-12": "Sat 12 Sep",
    "2026-09-13": "Sun 13 Sep",
}
# Deliberately abbreviated (Jason, 2026-08-31). These render in a 36px tag
# beside every fixture; "Quarter-final" and "Semi-final" wrapped onto two
# lines on an iPhone, which is what a label is supposed to avoid.
PHASE = {
    "group": "Group", "qualification_to_qf": "QF Qual",
    "quarter_final": "QF", "semi_final": "SF",
    "third_place": "3rd Pl", "final": "Final",
}


def team_slug(t):
    return seo.slugify(t["name"])


def team_link(key, teams, bare=False):
    """A team cell. TBD stays TBD — a knockout slot with no team in it is
    not a gap in our data, it is the state of the tournament."""
    if key == "TBD":
        return '<span class="mu">TBD</span>'
    t = teams[key]
    label = f'{t["flag"]} {esc(t["name"])}'
    if bare:
        return label
    return (f'<a href="/teams/{team_slug(t)}/" style="text-decoration:none;'
            f'color:var(--text);font-weight:500">{label}</a>')


def tip_cell(row):
    """Both time zones, always. The audience is US-Eastern; the tournament
    is in Berlin. Showing one and not the other makes half the readers do
    arithmetic, and the CEST column is the one FIBA itself publishes."""
    if row["tip_et"]:
        et = f'<span class="mu num">{esc(row["tip_et"])} ET</span>'
    else:
        et = '<span class="mu">TBA</span>'
    cest = (f'<span class="mu num" style="opacity:.6">'
            f'{esc(row["tip_cest"])} CEST</span>')
    return f'{et}<br>{cest}'


# "2nd A" reads as a typo to anyone who has not memorised the bracket, so the
# group is named. And a reference to a numbered game is REMOVED rather than
# spelled out: this site does not number its games, so "W27" pointed at
# something a reader had no way to look up. Naming the round it comes from
# says everything a reader needs — which qualifier it was becomes moot the
# moment the team is known, and until then nobody is tracing the bracket.
#
# Deciding this also retired a live data defect rather than fixing it: our
# CSV had the game-27 and game-28 matchup rules swapped against FIBA's
# numbering (found 2026-08-31). With no game numbers on the page, nothing
# published depends on that mapping any more.
# Game numbers -> the ROUND THE WINNER CAME FROM. Read them off the schedule
# rather than by eye: 25-28 are the QF qualifiers, 29-32 the quarter-finals,
# 33-34 the semi-finals. So W29 is a QUARTER-FINAL winner, not a semi-final
# one - an off-by-one-round here is invisible in the code and wrong on the
# page, which is exactly how the first cut of this table got it.
_RULE_WORDS = [
    ("W25", "QF Qual winner"), ("W26", "QF Qual winner"),
    ("W27", "QF Qual winner"), ("W28", "QF Qual winner"),
    ("W29", "QF winner"), ("W30", "QF winner"),
    ("W31", "QF winner"), ("W32", "QF winner"),
    ("W33", "SF winner"), ("W34", "SF winner"),
    ("L33", "SF loser"), ("L34", "SF loser"),
]


def _readable_rule(rule):
    for a, b in _RULE_WORDS:
        rule = rule.replace(a, b)
    # "2nd A" -> "2nd Grp A", without touching an already-named group.
    for pos in ("1st", "2nd", "3rd", "4th"):
        for g in "ABCD":
            rule = rule.replace(f"{pos} {g}", f"{pos} Grp {g}")
    return rule


def matchup_rule(row):
    """The bracket rule, shown until the slot resolves ('2nd Grp A - 3rd Grp B').

    The trailing '  [note]' in the CSV is a provenance annotation for us,
    not copy for a reader — strip it.
    """
    if not row["matchup_rule"]:
        return ""
    rule = _readable_rule(row["matchup_rule"].split("  [")[0])
    return f'<div class="mu" style="font-size:10.5px">{esc(rule)}</div>'


# ══ Games ═════════════════════════════════════════════════════════════════

def page_games(rows, teams, results, box_ids):
    out = ['<h2 class="sec">Full tournament schedule — 36 games</h2>',
           '<p class="mu" style="font-size:11.5px;margin-bottom:6px">'
           'Times are US Eastern, with Berlin local (CEST) below.</p>']
    for d in OrderedDict((r["date"], None) for r in rows):
        out.append(f'<div class="day">{DAYNAME.get(d, d)}</div>')
        body = ["<table>"]
        for r in (x for x in rows if x["date"] == d):
            label = (f'<span class="grp">{r["group"]}</span>' if r["group"]
                     else f'<span class="tag">{PHASE.get(r["phase"], r["phase"])}</span>')
            body.append(
                f'<tr><td style="width:36px">{label}</td>'
                f'<td style="width:37%">{team_link(r["team_1"], teams)}'
                f'{matchup_rule(r)}</td>'
                f'<td class="mu" style="width:24px;font-size:11px">vs</td>'
                f'<td style="width:37%">{team_link(r["team_2"], teams)}</td>'
                f'<td class="r" style="white-space:nowrap">'
                f'{game_cell(r, results, box_ids)}</td></tr>')
        body.append("</table>")
        out.append(table_scroll("".join(body)))
    # GAMES_PATH, never a literal "/". This was hardcoded when Games WAS the
    # landing page; flipping GUIDE_IS_LANDING on 2026-08-26 moved Games to
    # /games/ and left this behind, which meant the page shipped a canonical
    # pointing at the HOME PAGE (telling Google to drop /games/ and fold it
    # into /), reported its pageviews under the "guide" analytics key, and
    # never lit its own nav tab. Found 2026-08-29 by Jason noticing the tab
    # would not underline — the smallest visible symptom of the three.
    return shell(GAMES_PATH, f"Schedule — {TOURNAMENT_NAME} 2026", "".join(out),
                 "Every game of the 2026 FIBA Women's Basketball World Cup "
                 "in Berlin, with tip times in US Eastern and Berlin local.")


def game_cell(row, results, box_ids):
    """The one cell that carries all three lifecycle states.

    Pre → tip times. Live → score + period. Post → final + box-score link.
    Driven by data, never by the clock, so a page built in December shows
    what really happened and a page built today shows tip times.
    """
    res = results.get(row["game_id"])
    if not res:
        return tip_cell(row)
    a, b = res["score"]
    if res.get("status") == "live":
        return (f'<span class="score">{a}–{b}</span><br>'
                f'<span class="mu num">{esc(res.get("period", ""))}</span>')
    hi, lo = ("win", "lose") if a >= b else ("lose", "win")
    score = (f'<span class="{hi}">{a}</span><span class="mu">–</span>'
             f'<span class="{lo}">{b}</span>')
    # Link to the box score only when that page is really being emitted.
    # A result and its box score arrive from different places and can lag
    # each other, so a final score must never be a link to a 404 — the
    # score itself is the fact, the box score is a bonus.
    if row["game_id"] in box_ids:
        score = (f'<a href="/games/{row["game_id"]}/" '
                 f'style="text-decoration:none">{score}</a>')
    return (f'<span class="score">{score}</span><br>'
            f'<span class="mu" style="font-size:10.5px">Final</span>')


# ══ Teams index ═══════════════════════════════════════════════════════════

def page_teams_index(doc):
    """16 pages, no tiering. The card subtitle is the WNBA count because
    that is the reason this audience clicks through at all."""
    out = []
    for g in "ABCD":
        out.append(f'<h2 class="sec">Group {g}</h2><div class="tcards">')
        for t in sorted((x for x in doc["teams"] if x["group"] == g),
                        key=lambda x: x["name"]):
            n = t["wnba"]["current"]
            wl = (f'{n} in the WNBA' if n else 'no current WNBA players')
            apps = t["wwc_record"]["appearances_count"]
            out.append(
                f'<a class="tc" href="/teams/{team_slug(t)}/">'
                f'<div class="n">{t["flag"]} {esc(t["name"])}</div>'
                f'<div class="m">{esc(wl)} · {apps} World Cups</div></a>')
        out.append("</div>")
    return shell("/teams/", f"Teams — {TOURNAMENT_NAME} 2026", "".join(out),
                 "All 16 teams at the 2026 FIBA Women's Basketball World Cup, "
                 "by group, with their WNBA connections.")


# ══ Team page ═════════════════════════════════════════════════════════════

CONTINENTAL = ("AfroBasket", "EuroBasket", "Asia Cup", "AmeriCup")


def continental_name(tournament):
    """The ACTUAL competition name, never a generic expansion of 'CC'
    (Jason, 2026-08-23). 'FIBA Women's Asia Cup 2025' → 'Asia Cup'."""
    for name in CONTINENTAL:
        if name in (tournament or ""):
            return name
    return "Continental champion"


def best_finish(record):
    bf = record["best_finish"]
    if not bf["years"]:
        return "—"
    return f'Best: {bf["result"]} ({max(bf["years"])})'


def history_cards(t):
    """Tournament history, NOT statistics. On publication day zero games
    have been played, and that is the state this row lives in for the whole
    pre-tournament life of the page — so it must be interesting empty.

    Abbreviations are spelled out (Jason, 2026-08-23): '12 app' became
    'WORLD CUP APPEARANCES / 12 / Best: …', 'QT' became 'Qualifier' with a
    link to the city tournament, and 'CC' became the competition's real name.
    """
    q = t["qualification"]
    cards = [("World Cup appearances", t["wwc_record"]["appearances_count"],
              best_finish(t["wwc_record"])),
             ("Olympic appearances", t["olympic_record"]["appearances_count"],
              best_finish(t["olympic_record"]))]
    if q["route"] == "host":
        cards.append(("How they qualified", "Host", "Host nation — Berlin 2026"))
    elif q["route"] == "continental_cup_champion":
        cards.append(("How they qualified", continental_name(q["tournament"]),
                      "2025 continental champion"))
    else:
        sub = (f'<a href="{esc(q["link"])}">{esc(q["city"])} tournament →</a>'
               if q.get("city") and q.get("link") else "Qualifying tournament")
        cards.append(("How they qualified", "Qualifier", sub))

    html = []
    for lab, big, sub in cards:
        # `sub` is pre-escaped HTML only when it is a link we built above.
        sub_html = sub if sub.startswith("<a ") else esc(sub)
        html.append(f'<div class="card"><div class="lab">{esc(lab)}</div>'
                    f'<div class="big">{esc(str(big))}</div>'
                    f'<div class="sub">{sub_html}</div></div>')
    return f'<div class="grid3">{"".join(html)}</div>'


def coach_block(t):
    coach = t["coach"]
    if not coach["name"]:
        # Mali. FIBA's team profile names no coach and nobody has been
        # identified in place of the wrongly-attributed earlier name. The
        # slot renders empty rather than guessing — do not "fix" this.
        return ('<h2 class="sec">Head coach</h2>'
                '<div class="mu">Not announced.</div>'
                '<div class="cnote">FIBA\'s team profile names no head coach '
                'and no other source establishes one.</div>')
    out = [f'<h2 class="sec">Head coach</h2>'
           f'<div style="font-size:15px;font-weight:600">'
           f'{esc(coach["name"])}</div>']
    if coach["us_connection"]["has"]:
        out.append(f'<div class="cnote">'
                   f'{esc(coach["us_connection"]["summary"])}</div>')
    return "".join(out)


def wnba_block(t, published):
    """The WNBA connection — the retention bridge, and half the reason this
    site exists. Names link to our OWN player pages wherever we publish one."""
    w = t["wnba"]
    current = [p for p in w["players"] if p["status"] == "current"]
    n = w["current"]
    head = (f'WNBA connection <span class="mu">— {n} current '
            f'player{"s" if n != 1 else ""}</span>')
    out = [f'<h2 class="sec">{head}</h2>']
    if current:
        rows = ['<table><tr><th>Player</th><th>Pos</th>'
                '<th>WNBA team</th></tr>']
        for p in current:
            href = player_href(p["name"], published)
            name = (f'<a href="{href}" {CROSS_SITE} style="font-weight:500;'
                    f'color:var(--text)">{esc(p["name"])}</a>' if href
                    else f'<span style="font-weight:500">{esc(p["name"])}</span>')
            team = (f'<span class="wn">{esc(p["wnba_team"])}</span> '
                    f'<span class="mu" style="font-size:10.5px">'
                    f'{esc(p["wnba_team_full"])}</span>'
                    if p["wnba_team"] else '<span class="mu">—</span>')
            rows.append(f'<tr><td>{name}</td>'
                        f'<td class="mu" style="width:34px">'
                        f'{esc(p["position"] or "")}</td>'
                        f'<td style="width:170px">{team}</td></tr>')
        rows.append("</table>")
        out.append(table_scroll("".join(rows)))
    else:
        out.append('<div class="mu">No current WNBA players.</div>')

    other = [p for p in w["players"] if p["status"] != "current"]
    if other:
        bits = []
        for p in other:
            label = "former" if p["status"] == "former" else "drafted"
            team = f', {esc(p["wnba_team"])}' if p["wnba_team"] else ""
            bits.append(f'<b>{esc(p["name"])}</b> ({label}{team})')
        out.append(f'<div class="cnote" style="margin-top:6px">Also: '
                   f'{", ".join(bits)}.</div>')

    if w["roster_basis"].startswith("proxy"):
        # China, Germany, Nigeria. No squad has been announced at all, so
        # the list above is inference from nationality — say so plainly
        # rather than letting it read as a call-up.
        out.append('<div class="pend">No squad has been announced. The names '
                   'above are this country\'s current WNBA players, not a '
                   'confirmed call-up list.</div>')
    return "".join(out)


def roster_block(t, published):
    """Provisional rosters are a PERMANENT design state, not a transient one.
    FIBA rosters need not be final until just before the tournament, and as
    of this build it is 2 final / 10 pool / 4 not announced. The treatment is
    designed, not bolted on."""
    r = t["roster"]
    label = {"final": "final 12",
             "pool": f'provisional — {r["player_count"]}-player pool',
             "not_announced": "not announced"}[r["status"]]
    out = [f'<h2 class="sec">Roster <span class="mu">— {esc(label)}</span></h2>']

    if r["players"]:
        # `roster.players[].wnba` is a tri-state flag meaning "has a WNBA
        # connection", which is NOT the same as "plays there now" — Belgium
        # marks Meesseman and Vanloo true while `wnba.players` records both
        # as former. Cross-reference by name so a former player is never
        # badged as current.
        status_by_name = {p["name"]: p for p in t["wnba"]["players"]}
        cells = [(p, wnba_team_cell(p, status_by_name),
                  club_cell(p), note_cell(p, status_by_name))
                 for p in r["players"]]

        # Optional columns render PER TEAM, only when at least one player on
        # THIS team has a value. A column of twelve identical dashes reads as
        # broken rather than as honest, and `plays_for.club_name` is null for
        # every player in the file today — so "Other club" would be exactly
        # that on all three teams that have rosters. This way each column
        # appears as its data lands, team by team, and no page ever shows a
        # wholly empty one. Same lifecycle logic the standings table uses,
        # where W/L/PF/PA do not exist until a game has been played.
        show_club = any(c for _, _, c, _ in cells)
        show_note = any(n for _, _, _, n in cells)

        head = ['<table><tr><th>Player</th><th>WNBA team</th>']
        if show_club:
            head.append("<th>Other club</th>")
        if show_note:
            head.append("<th>Note</th>")
        head.append("</tr>")
        rows = head
        for p, wnba_cell, club, note in cells:
            rows.append(f'<tr><td>{roster_name(p, published)}</td>'
                        f'<td>{wnba_cell or DASH}</td>')
            if show_club:
                rows.append(f'<td>{club or DASH}</td>')
            if show_note:
                rows.append(f'<td class="mu" style="white-space:normal;'
                            f'min-width:140px">{note or DASH}</td>')
            rows.append("</tr>")
        rows.append("</table>")
        out.append(table_scroll("".join(rows)))
    elif r["status"] == "pool":
        out.append(f'<div class="mu">FIBA has published a '
                   f'{r["player_count"]}-name pool; the individual names are '
                   f'not yet available.</div>')
    else:
        out.append('<div class="mu">FIBA has not published a roster for this '
                   'team.</div>')

    if r["status"] == "pool":
        out.append('<div class="pend">A pool, not a squad — it will be cut to '
                   '12 before the tournament. Nothing here should be read as a '
                   'confirmed selection.</div>')
    return "".join(out)


def roster_name(p, published):
    # `is not None`, not truthiness: 0 is a real jersey number and a common
    # one, and a falsy test silently drops it.
    number = (f'<span class="mu num" style="font-size:10.5px">'
              f'{WWC.jersey_prefix}{esc(str(p["number"]))}</span> '
              if p.get("number") is not None else "")
    href = player_href(p["name"], published)
    if href:
        return (f'{number}<a href="{href}" {CROSS_SITE} style="font-weight:500;'
                f'color:var(--text)">{esc(p["name"])}</a>')
    return f'{number}<span style="font-weight:500">{esc(p["name"])}</span>'


DASH = '<span class="mu">—</span>'
STATUS_LABEL = {"former": "former", "drafted_only": "drafted"}


def wnba_team_cell(p, status_by_name):
    """The WNBA team badge, or '' when there is no current WNBA tie.

    The `wnba.players` block wins over `roster.players[].wnba`, and that is
    deliberate: the two disagree inside the same file. Six roster entries
    carry `wnba: false` while the wnba block lists five of them as CURRENT
    (Bibby, Borlase, Fowler, Linskens, Delaere), and Belgium's block records
    Meesseman and Vanloo as former while their roster rows say true. The
    schema doc makes `wnba.players` the authoritative side — the three counts
    derive from it and the validator asserts they agree — so the denormalised
    roster boolean is read only for names the wnba block has never heard of.
    This is not inference; it is preferring the file's verified block to its
    own stale copy. The flags themselves still want fixing.

    A FORMER player gets no badge here — she is not on a WNBA team. Her
    history belongs in the Note column, which is exactly what splitting one
    "Plays for" cell into three columns buys.
    """
    rec = status_by_name.get(p["name"])
    if rec and rec["status"] == "current":
        team = rec.get("wnba_team") or p["plays_for"].get("wnba_team")
        return (f'<span class="wn">{esc(team)}</span>' if team
                else '<span class="wn">WNBA</span>')
    if not rec and p.get("wnba"):
        team = p["plays_for"].get("wnba_team")
        return (f'<span class="wn">{esc(team)}</span>' if team
                else '<span class="wn">WNBA</span>')
    return ""


def club_cell(p):
    """The non-WNBA club, or ''. Correct-or-blank.

    Round 1 printed the literal word 'club' for every non-WNBA player, which
    reads like a value and told the reader nothing. `plays_for.club_name` is
    null for EVERY player in the reference data today, so this column does
    not render at all yet — see the per-team gate in `roster_block`.
    """
    club = p["plays_for"].get("club_name")
    if not club:
        return ""
    country = p["plays_for"].get("club_country")
    suffix = (f' <span class="mu" style="font-size:10.5px">'
              f'{esc(country)}</span>' if country else "")
    return f"{esc(club)}{suffix}"


def note_cell(p, status_by_name):
    """Short reader-facing context, or ''.

    Two sources, in order. A former/drafted status is synthesised here
    because it is a fact about the player that the WNBA-team column can no
    longer carry once that column means "plays there now".

    The `note` field itself is MIXED-PURPOSE and cannot be shipped wholesale:
    of the 41 notes in the file, roughly a third are internal provenance and
    hedging ("Not 'Megan DiLeo'", "Basketball Australia's release still says
    Chicago", "still-rostered status unconfirmed") rather than prose for a
    reader. Publishing those would leak our working notes onto a team page.
    Until an editorial pass splits them, only the synthesised status ships —
    which is why `NOTE_FIELD_IS_READER_SAFE` is False and is a switch rather
    than a deletion: the moment the pass happens, this is one flag.
    """
    rec = status_by_name.get(p["name"])
    bits = []
    if rec and rec["status"] != "current":
        label = STATUS_LABEL.get(rec["status"], rec["status"])
        team = rec.get("wnba_team_full") or rec.get("wnba_team")
        bits.append(f'{label.capitalize()} {esc(team)}' if team
                    else f'{label.capitalize()} WNBA')
    if NOTE_FIELD_IS_READER_SAFE and rec and rec.get("note"):
        bits.append(esc(rec["note"]))
    return " · ".join(bits)


#: See `note_cell`. Flip to True once `wnba.players[].note` has been split
#: into reader-facing prose and internal provenance.
NOTE_FIELD_IS_READER_SAFE = False


def fixtures_block(t, rows, teams, results, box_ids):
    fx = [x for x in rows if x["group"] == t["group"]
          and t["schedule_key"] in (x["team_1"], x["team_2"])]
    out = [f'<h2 class="sec">Group {t["group"]} fixtures</h2>']
    body = ["<table>"]
    for x in fx:
        opp = x["team_2"] if x["team_1"] == t["schedule_key"] else x["team_1"]
        body.append(
            f'<tr><td class="mu num" style="width:90px;font-size:11.5px;'
            f'white-space:nowrap">{DAYNAME.get(x["date"], x["date"])}</td>'
            f'<td class="mu" style="width:24px;font-size:11px">vs</td>'
            f'<td>{team_link(opp, teams)}</td>'
            f'<td class="r" style="white-space:nowrap">'
            f'{game_cell(x, results, box_ids)}</td></tr>')
    body.append("</table>")
    out.append(table_scroll("".join(body)))
    return "".join(out)


def page_team(t, rows, teams, results, box_ids, published):
    out = [
        f'<div class="hd"><div class="crest"><div class="flag">{t["flag"]}</div>'
        f'<div class="tla">{esc(t["code"])}</div></div>'
        f'<div><h2>{esc(t["name"])}</h2>'
        f'<div class="mu">Group {t["group"]}</div></div></div>',
        history_cards(t),
    ]
    # The emitter reads whatever profile is there and does not care what
    # state it is in — Jason's proofread lands as data edits to `profile` /
    # `profile_status` and needs no code change (handoff §8.3).
    if t.get("profile"):
        out.append(f'<div class="ed">{esc(t["profile"])}</div>')
    out.append(coach_block(t))
    out.append(wnba_block(t, published))
    out.append(roster_block(t, published))
    out.append(fixtures_block(t, rows, teams, results, box_ids))

    n = t["wnba"]["current"]
    desc = (f'{t["name"]} at the 2026 FIBA Women\'s Basketball World Cup: '
            f'squad, coach, group fixtures and '
            f'{n if n else "no"} current WNBA player{"s" if n != 1 else ""}.')
    return shell(f"/teams/{team_slug(t)}/",
                 f'{t["name"]} — {TOURNAMENT_NAME} 2026',
                 "".join(out), desc, extra_head=team_jsonld(t))


def team_jsonld(t):
    data = {"@context": "https://schema.org", "@type": "SportsTeam",
            "name": t["name"], "sport": "Basketball",
            "url": seo.canonical_url(WWC, f"/teams/{team_slug(t)}/")}
    if t["coach"]["name"]:
        data["coach"] = {"@type": "Person", "name": t["coach"]["name"]}
    return ('<script type="application/ld+json">'
            f'{json.dumps(data, ensure_ascii=False)}</script>\n')


# ══ Groups ════════════════════════════════════════════════════════════════

def page_groups(doc, rows, teams, results, box_ids):
    """Tables + per-group fixtures + the qualification path, on ONE page.

    Fixtures appear here AND on Games deliberately — "when is my team
    playing" and "who is winning this group" are different questions and a
    reader arriving at either should not be sent to the other page.
    """
    out = ['''<h2 class="sec">How a team reaches the quarter-finals</h2>
<div class="path">
<b>1st in group</b> → straight to the quarter-finals. Skips a knockout game entirely.<br>
<b>2nd and 3rd</b> → qualification play-off, single elimination, cross-group.<br>
<b>4th</b> → eliminated. There is no classification round and no consolation bracket.
</div>
''']

    for i, g in enumerate("ABCD"):
        out.append(f'<h2 class="sec">Group {g}</h2>')
        # Once, above Group A, and only when the columns exist to explain.
        if i == 0 and results:
            out.append(COLUMN_LEGEND)
        done = group_complete(rows, g, results)
        out.append(standings_table(doc, g, results, done))
        if results and not done:
            # Say so. An order that will change once the tie-breaks apply
            # must not be presented as the finished one.
            out.append('<div class="legend">Provisional — sorted on '
                       'classification points while games remain. Tie-breaks '
                       'apply once the group is complete.</div>')
        body = ['<table style="margin-top:6px">']
        for r in (x for x in rows if x["group"] == g):
            body.append(
                f'<tr><td class="mu num" style="width:90px;font-size:11.5px;'
                f'white-space:nowrap">{DAYNAME.get(r["date"], r["date"])}</td>'
                f'<td>{team_link(r["team_1"], teams)}</td>'
                f'<td class="mu" style="width:24px;font-size:11px">vs</td>'
                f'<td>{team_link(r["team_2"], teams)}</td>'
                f'<td class="r" style="white-space:nowrap">'
                f'{game_cell(r, results, box_ids)}</td></tr>')
        body.append("</table>")
        out.append(table_scroll("".join(body)))

    # Tie-breakers — now the FULL ladder. Both items the source doc marked
    # do-not-publish were confirmed 2026-08-26 from the rulebook itself
    # (Official Basketball Rules, Appendix D — Classification of Teams),
    # which replaced a single-source Wikipedia summary. The summary had the
    # right ORDER of criteria types and was wrong in three ways that change
    # results; see `classify()` for the detail. This section is the reader's
    # half of that fix and is the most-asked question of any group stage.
    out.append('''<h2 class="sec">If teams finish tied</h2>
<p class="prose" style="font-size:12.5px">Teams are ranked on
<b>classification points — two for a win, one for a loss</b>. That is not the
system most US fans expect: a 3–0 team has six points, an 0–3 team has three
points, and nobody finishes on zero.</p>
<div class="path">
<b>1.</b> Classification points<br>
<b>2.</b> If teams are tied, <b>only the games between those teams count</b> —
they are re-ranked as a mini-table: record, then points difference, then points
scored, all within that group of teams<br>
<b>3.</b> Still tied → points difference across all group games, then points
scored across all group games<br>
<b>4.</b> Still tied → FIBA world ranking
</div>
<p class="prose" style="font-size:12.5px">Step 2 is the one worth
understanding, because it is not the same as "head-to-head". <b>Three-way ties
are common</b> — they happen whenever three teams beat each other in a circle,
which is a quarter of all possible group outcomes. When that happens each team
is 1–1 against the others, so the mini-table is tied on record and
<b>margin decides</b>. Beating one of them does not put you above them: a team
can win its head-to-head meeting and still finish below the team it beat. Each
time the procedure separates one team out, it <b>starts again from the top</b>
for whoever is still tied. (All four tied is impossible — the six group games
produce six wins, which will not divide evenly among four teams.)</p>''')
    return shell("/groups/", f"Groups — {TOURNAMENT_NAME} 2026", "".join(out),
                 "Group tables, fixtures and the route to the quarter-finals "
                 "at the 2026 FIBA Women's Basketball World Cup.")


def standings_table(doc, group, results, complete=True):
    """Program state: no W/L columns at all.

    An empty standings table on launch day is a grid of dashes that teaches
    nobody anything. Until a game is played this is a team list; the moment
    results exist it grows the columns. Same element, three states.
    """
    teams_in = sorted((t for t in doc["teams"] if t["group"] == group),
                      key=lambda x: x["name"])
    if not results:
        rows = ['<table><tr><th>Team</th><th class="r">Played</th></tr>']
        for t in teams_in:
            rows.append(
                f'<tr><td><a href="/teams/{team_slug(t)}/" '
                f'style="text-decoration:none;color:var(--text);'
                f'font-weight:500">{t["flag"]} {esc(t["name"])}</a></td>'
                f'<td class="r mu num">0</td></tr>')
        rows.append("</table>")
        return table_scroll("".join(rows))

    table = compute_standings(doc, group, results, complete)
    rows = ['<table><tr><th>Team</th><th class="r">Pld</th>'
            '<th class="r">W</th><th class="r">L</th><th class="r">Pts</th>'
            '<th class="r">PF</th><th class="r">PA</th>'
            '<th class="r">Diff</th></tr>']
    for row in table:
        t = row["team"]
        diff = row["diff"]
        # An unresolved run means every FIBA criterion we can compute was
        # exhausted and the next one is world ranking, which we do not hold.
        # Marking it beats implying the order is meaningful.
        mark = (' <span class="mu" title="Cannot be separated from the '
                'criteria we hold">=</span>' if row.get("unresolved") else '')
        rows.append(
            f'<tr><td><a href="/teams/{team_slug(t)}/" '
            f'style="text-decoration:none;color:var(--text);font-weight:500">'
            f'{t["flag"]} {esc(t["name"])}</a>{mark}</td>'
            f'<td class="r num mu">{row["gp"]}</td>'
            f'<td class="r num">{row["w"]}</td><td class="r num">{row["l"]}</td>'
            f'<td class="r num" style="font-weight:600">{row["pts"]}</td>'
            f'<td class="r num mu">{row["pf"]}</td>'
            f'<td class="r num mu">{row["pa"]}</td>'
            f'<td class="r num {"pos" if diff > 0 else "neg" if diff < 0 else "mu"}">'
            f'{diff:+d}</td></tr>')
    rows.append("</table>")
    return table_scroll("".join(rows))


#: The column legend. Rendered ONCE above the first group table and only
#: when the columns it explains actually exist — explaining a Pts column on
#: a page that has no Pts column is worse than saying nothing.
#:
#: `Pts` is the one that genuinely misleads: a US reader reads a points
#: column in a standings table as points scored, or assumes 1-for-a-win.
#: FIBA means classification points, 2 for a win and 1 for a LOSS, which is
#: why nobody in the table has zero. That single fact is the reason this
#: legend exists at all.
COLUMN_LEGEND = (
    '<div class="legend"><details class="inl" name="glossary">'
    '<summary><span class="t">What do these columns mean?</span></summary>'
    '<span class="body">'
    '<b>Pld</b> games played · <b>W</b> won · <b>L</b> lost<br>'
    '<b>Pts</b> — <b>classification points: 2 for a win, 1 for a loss.</b> '
    'Not points scored. A 3–0 team has 6, an 0–3 team has 3, and nobody '
    'has zero.<br>'
    '<b>PF</b> points scored · <b>PA</b> points conceded · '
    '<b>Diff</b> the difference between them.'
    '</span></details></div>'
)


def played_games(results, keys):
    """Final games whose BOTH teams are in `keys`, as (k1, s1, k2, s2)."""
    out = []
    for res in results.values():
        if res.get("status") != "final":
            continue
        k1, k2 = res["teams"]
        if k1 in keys and k2 in keys:
            s1, s2 = res["score"]
            out.append((k1, s1, k2, s2))
    return out


def tally(results, keys):
    """W/L/PF/PA/classification points over the games among `keys`.

    Classification points are FIBA's own column (D.1.1): **2 for a win, 1 for
    a loss**, 0 only for a forfeit — which is why nobody in a FIBA group table
    has zero and a 3-0 team shows 6. A US reader assumes 1-for-a-win or
    3-for-a-win, which is exactly the quiet mismatch this site exists to fix.
    Forfeits are not modelled: none has occurred, and inventing a
    representation for one would be guessing at a data shape we have never
    seen. If one happens, it is a `status` value, not a silent 0.
    """
    rec = {k: {"w": 0, "l": 0, "pf": 0, "pa": 0, "gp": 0} for k in keys}
    for k1, s1, k2, s2 in played_games(results, keys):
        rec[k1]["pf"] += s1; rec[k1]["pa"] += s2; rec[k1]["gp"] += 1
        rec[k2]["pf"] += s2; rec[k2]["pa"] += s1; rec[k2]["gp"] += 1
        rec[k1]["w" if s1 > s2 else "l"] += 1
        rec[k2]["w" if s2 > s1 else "l"] += 1
    for r in rec.values():
        r["pts"] = 2 * r["w"] + r["l"]
        r["diff"] = r["pf"] - r["pa"]
    return rec


def classify(keys, results, overall):
    """Order a set of TIED teams per FIBA Appendix D.1.3 / D.1.4.

    Confirmed 2026-08-26 from the rulebook itself (Appendix D, Classification
    of Teams), which replaced a single-source Wikipedia summary. The summary
    was right about the ORDER of criteria types and wrong in three ways that
    change results:

    1. It collapses four criteria into two. The rulebook runs point difference
       **among the tied teams**, then points scored **among them**, and only
       then difference and points **across all group games**.
    2. "Head-to-head" is misleading once three teams are level. D.1.3 builds a
       SUB-GROUP from only the games among them and re-ranks that mini-table.
       Rulebook Example 3 is the proof this is not pedantry: B, C and D all
       finish 2-1, B beat C head-to-head, and C still places above B on
       sub-group point difference (C +5, D 0, B -5). Pairwise reasoning gets
       that backwards.
    3. D.1.4 is absent from the summary entirely: the moment any team is
       separated out, the procedure RESTARTS from the beginning for whoever
       is left. That is the recursion below, and it is why this cannot be
       expressed as one sort key.

    Beyond those, FIBA falls back to world ranking and then a draw. We hold
    neither, so a genuinely unresolved run is ordered by name and flagged —
    see `unresolved` on the returned rows. Better a visible "we cannot
    separate these" than an invented order in a table, which reads as
    authoritative precisely because it is a table.
    """
    if len(keys) == 1:
        return list(keys)

    # The games among THESE teams and no others. Recomputed at every level of
    # the recursion, which is the whole point — see below.
    sub = tally(results, set(keys))

    # D.1.3's criteria, in order. The first is the win-loss record among the
    # tied teams (classification points express it); the rest are the four
    # bulleted tiebreaks, two scoped to the games between them and two to all
    # games in the group.
    criteria = (
        lambda k: sub[k]["pts"],
        lambda k: sub[k]["diff"],
        lambda k: sub[k]["pf"],
        lambda k: overall[k]["diff"],
        lambda k: overall[k]["pf"],
    )

    for criterion in criteria:
        buckets = {}
        for k in keys:
            buckets.setdefault(criterion(k), []).append(k)
        if len(buckets) == 1:
            continue  # this criterion separates nobody; try the next

        # D.1.4: "If at any level of these criteria one or more team(s) are
        # already classified, the procedure of D.1.3 shall be repeated FROM
        # THE START for all the remaining teams not classified yet."
        #
        # That word "restart" is load-bearing and it is what makes this a
        # recursion rather than a sort. Each surviving bucket is re-entered as
        # a brand-new tie, so `tally()` above recomputes the sub-group from
        # only ITS members' games against each other.
        #
        # A composite sort key gets this wrong, and the six-team examples are
        # where it shows. In Example 5 the four-team sub-group separates
        # {A,B} from {C,D} on record; C and D must then be re-compared on the
        # single C-D game, which D won — but their four-team point
        # differences are -5 and -45, so a sort key ranks C above D and the
        # rulebook ranks D above C. Same failure in Example 6 for {B,D,E}.
        # Examples 1-4 and 7 pass either way, which is exactly why the
        # complete set of worked examples was worth having.
        #
        # Each bucket is strictly smaller than `keys` here (len(buckets) > 1),
        # so the recursion always terminates.
        out = []
        for _, bucket in sorted(buckets.items(), key=lambda kv: -kv[0]):
            out.extend(classify(bucket, results, overall))
        return out

    # Every criterion exhausted and nothing separated them. FIBA's next step
    # is the world ranking, which we do not hold, and after that a draw.
    # Mark them rather than implying the order below means something.
    for k in keys:
        overall[k]["unresolved"] = True
    return sorted(keys, key=lambda k: overall[k]["team"]["name"])


def compute_standings(doc, group, results, complete=True):
    """The group table.

    `complete` decides which of two orderings applies, and the distinction is
    not cosmetic.

    **Appendix D is a rule for the END of the group phase.** D.1.3 opens "If 2
    or more teams have the same win-loss record of *all games in the group*",
    which presumes every game has been played. Applying it to a half-played
    group produces orderings that are indefensible on their face: in a Group A
    where only the Japan-Mali game among three tied teams has finished,
    Germany at 1-0 and +15 sorts BELOW Mali at 0-2, because Germany's
    sub-group record is 0-0 and Mali's is 0-1. That is not a bug in the ladder
    — it is the ladder being asked a question it does not answer.

    So while a group is incomplete this is a **provisional** table sorted the
    way a reader expects (classification points, then difference, then points
    scored), and the page says it is provisional. The moment the last group
    game is final, the real ladder takes over and the order becomes the
    official one. FIBA's own live tables behave the same way.
    """
    in_group = {t["schedule_key"]: t for t in doc["teams"]
                if t["group"] == group}
    overall = tally(results, set(in_group))
    for k, t in in_group.items():
        overall[k]["team"] = t
        overall[k]["unresolved"] = False

    if not complete:
        return sorted(overall.values(),
                      key=lambda r: (-r["pts"], -r["diff"], -r["pf"],
                                     r["team"]["name"]))

    order = []
    for _, run in sorted(
            _runs_by(overall, lambda r: r["pts"]), key=lambda kv: -kv[0]):
        order.extend(classify(run, results, overall))
    return [overall[k] for k in order]


def group_complete(rows, group, results):
    """True once every game in the group has a final result."""
    ids = [r["game_id"] for r in rows if r["group"] == group]
    return bool(ids) and all(
        results.get(i, {}).get("status") == "final" for i in ids)


def _runs_by(overall, keyfn):
    """[(value, [team_key, ...]), ...] grouped by keyfn, for tie detection."""
    buckets = {}
    for k, r in overall.items():
        buckets.setdefault(keyfn(r), []).append(k)
    return list(buckets.items())


# ══ Key ═══════════════════════════════════════════════════════════════════

def page_guide(doc, teams):
    """What a WNBA fan needs to know to read a FIBA game.

    Rules content from `wwc-rules-audit-2026-08-17.md`. The glossary is far
    shorter than the WNBA site's because these pages carry far fewer derived
    statistics — this page is prose in a table, which is precisely why mono
    is scoped away from it.

    The opening brief was added 2026-08-26 on the strongest piece of user
    feedback we have: *"I didn't know there was a women's basketball world
    cup."* If that is the first reaction, then the rules comparison — which
    assumes you already know what you are comparing — was starting one step
    too late. It is written to a hard constraint: brief enough to read on a
    phone without burying the sections under it, complete enough that someone
    who knew nothing can follow the tournament.

    Every number in it is computed from `wwc2026_teams.json` at build time
    rather than typed, so the prose cannot drift from the data underneath it.
    """
    t = doc["tournament"]
    ger = next(x for x in doc["teams"] if x["code"] == "GER")
    usa = next(x for x in doc["teams"] if x["code"] == "USA")
    usa_eds = sorted(usa["wwc_record"]["editions"], key=lambda e: e["year"])
    titles = sum(1 for e in usa_eds if e["rank"] == 1)
    wnba_total = sum(x["wnba"]["current"] for x in doc["teams"])
    with_wnba = sum(1 for x in doc["teams"] if x["wnba"]["current"])

    # The USA's run of consecutive TITLES, derived rather than typed. Jason's
    # 2026-08-28 copy pass changed the claim from "medalled at every one since
    # 1979" to "including the past four", which is a different metric, not a
    # rewording — so the binding changed with it. The schema doc's
    # falsifiability rule applies either way: a superlative one game can break
    # must not be stored as a string. This one breaks the moment they lose a
    # final, and recomputes itself when 2026's result lands.
    title_run = 0
    for e in reversed(usa_eds):
        if e["rank"] != 1:
            break
        title_run += 1

    start_day = int(t["start_date"][-2:])
    end_day = int(t["end_date"][-2:])

    rows = load_schedule()
    results = load_results()
    pub = wnba_player_pages()

    # Inline links, FIRST MENTION ONLY (Jason, 2026-08-29). Measured that day:
    # the Guide carried 4 internal links against Groups' 64 and Games' 48,
    # because those pages are tables of team links and this one is prose. It
    # named six countries and five players, all with pages one directory away,
    # and linked none of them. Linking every occurrence instead would turn the
    # paragraph into a link farm and read as SEO spam.
    def tlink(key, label=None):
        """`label` overrides the team's own name — paragraph 3 opens on "The
        United States", which is the same page as the reference data's "USA"."""
        t = teams[key]
        return f'<a href="/teams/{team_slug(t)}/">{esc(label or t["name"])}</a>'

    def plink(name):
        """Cross-site, to our own WNBA player page — the retention bridge.

        Correct-or-blank: `player_href` returns None unless the page really
        exists on disk, and an unmatched name degrades to plain text rather
        than a 404. The straight apostrophe is what slugifies; the curly one
        is what reads.
        """
        # Swap the apostrophe BEFORE escaping: esc() turns \u0027 into
        # &#x27; and there is nothing left to replace afterwards. The
        # curly form passes through esc() untouched.
        disp = esc(name.replace("\u0027", "\u2019"))
        href = player_href(name, pub)
        # CROSS_SITE for the same reason the three table call sites carry it:
        # these five names are on the LANDING page, so they are the most
        # likely cross-site departure on the whole site, and the Cup tab has
        # to survive one.
        return f'<a href="{href}" {CROSS_SITE}>{disp}</a>' if href else disp

    # The onward block. The Guide is prose and genuinely ends; Games and
    # Groups are already grids of links to team pages and need nothing.
    # Lifecycle-aware so it never needs a dated edit: results on disk flip it
    # from "tips off" to "played", exactly as every other state on this site
    # is derived from data rather than from a clock.
    if results:
        nxt = (f'<b>{len(results)} of {len(rows)} games played.</b> '
               f'<br><a href="{GAMES_PATH}">Results and box scores →</a> · '
               f'<a href="/teams/">The {t["team_count"]} teams →</a>')
    else:
        opener = next((r for r in rows
                       if "USA" in (r["team_1"], r["team_2"])), rows[0])
        a, b = teams[opener["team_1"]], teams[opener["team_2"]]
        other = b if opener["team_1"] == "USA" else a
        year = final_rematch_year(usa, other)
        rematch = f', a rematch of the {year} final' if year else ''
        nxt = (f'<b>Berlin tips off September {start_day}.</b> The USA open '
               f'against {other["flag"]} {esc(other["name"])}{rematch}.'
               f'<br><a href="{GAMES_PATH}">All {len(rows)} games, day by day →</a>'
               f' · <a href="/teams/">The {t["team_count"]} teams →</a>')

    brief = f'''<h2 class="sec">World Cup overview</h2>
<p class="prose"><b>The {esc(TOURNAMENT_NAME)}</b> is the world championship of
women\u2019s basketball and the sport\u2019s biggest event outside the Olympics,
held every four years. Berlin 2026 is the <b>{ordinal(t["edition"])} edition</b>.</p>
<p class="prose"><b>{t["team_count"]} national teams</b> play
{len(rows)} games over ten days, September {start_day}–{end_day}. The
format is short and unforgiving. The teams are divided into four groups, and in
the first round every team plays the other three in its group. Win your group
and you skip straight to the quarter-finals; finish second or third and you play
an extra knockout game to reach them.</p>
<p class="prose"><b>{tlink("USA", "The United States")}</b> have won {titles} of the
{t["edition"] - 1} World Cups, including the past {CARDINAL.get(title_run, title_run)}. The other
contenders are {tlink("FRANCE")} (silver medalists at the 2024 Paris
Olympics), {tlink("AUSTRALIA")} (Asia Cup champions), {tlink("CHINA")} (2022
World Cup runners-up) and {tlink("BELGIUM")} (EuroBasket champions). All of them return experienced lineups that have played together
internationally. Every team\u2019s full record is on its own page — see
<a href="/teams/">Teams</a>.</p>
<p class="prose"><b>{wnba_total} current WNBA players are in this field</b>,
spread across {with_wnba} of the {t["team_count"]} teams. The US roster is the
most star-studded, with {plink("A\u0027ja Wilson")}, {plink("Breanna Stewart")},
{plink("Caitlin Clark")} and {plink("Paige Bueckers")} among its names. France,
which nearly beat the US in Paris in 2024, returns a strong team headlined by
{plink("Gabby Williams")}. Even the host nation, <b>{tlink("GERMANY")}</b>,
which has qualified only {NUM_WORD.get(len(ger["wwc_record"]["editions"]), len(ger["wwc_record"]["editions"]))}
before, carries {ger["wnba"]["current"]} WNBA players on its roster.</p>

'''
    body = brief + f'''<h2 class="sec">Rules: differences to know</h2>
{table_scroll("""<table>
<tr><th style="width:32%">Difference</th><th>What changes</th></tr>
<tr><td><b>Five fouls, not six</b></td><td>Players foul out a full personal earlier, so foul rates and minutes lost to foul trouble are not like-for-like with WNBA numbers.</td></tr>
<tr><td><b>No defensive three seconds</b></td><td>A defender may park in the lane. Legal zone defense, and fewer clean rim attempts than the WNBA game trains you to expect.</td></tr>
<tr><td><b>The ball may be played off the rim</b></td><td>Once it touches the ring it is live. Plays that would be goaltending in the WNBA are legal here.</td></tr>
<tr><td><b>Possession arrow</b></td><td>After the opening tip there is not another jump ball all tournament. Held balls alternate.</td></tr>
<tr><td><b>Timeouts</b></td><td>Coach-only, dead-ball-only, one minute, and no mandatory TV timeouts. A trapped ballhandler has no bailout.</td></tr>
<tr><td><b>Court size and the three-point line</b></td><td>FIBA\u2019s court is 3.9% smaller than the WNBA\u2019s. The three-point arc is identical at the top, 6.75 m, but in the corners FIBA\u2019s line sits closer to the basket — 6.60 m against 6.71 m, about four inches. That gap stems from FIBA\u2019s narrower court, which means the sideline cuts into the arc sooner.</td></tr>
</table>""")}
<div class="next">{nxt}</div>'''
    return shell(GUIDE_PATH, f"{TOURNAMENT_NAME} 2026 — a guide", body,
                 "What the Women\u2019s Basketball World Cup is, who is playing, "
                 "and the six rules differences that matter if you follow the "
                 "WNBA.")


#: "qualified only ONCE before" reads as English; "only 1 before" reads as a
#: database. Germany's appearance count is still the binding — this only
#: spells it. Falls back to the numeral above three, where the word form stops
#: being the natural phrasing.
NUM_WORD = {1: "once", 2: "twice", 3: "three times"}

#: Cardinals in prose. Jason's copy reads "including the past four", and a
#: computed binding that renders "the past 4" undoes the sentence — the number
#: stays derived, only its spelling is fixed here. Numerals above twelve are
#: left as digits, which is where the word form stops helping.
CARDINAL = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
            7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
            12: "twelve"}


def final_rematch_year(a, b):
    """The most recent edition where these two finished 1-2, or None.

    The Guide's onward block says "a rematch of the 2022 final" — a claim a
    schedule change or a data correction could falsify, so it is derived from
    both teams' `editions` rather than written down. No qualifying edition
    means the clause is simply not emitted, which is the same posture as the
    empty coach slot: silence beats a confident wrong sentence.
    """
    ra = {e["year"]: e["rank"] for e in a["wwc_record"]["editions"]}
    rb = {e["year"]: e["rank"] for e in b["wwc_record"]["editions"]}
    shared = [y for y in ra if y in rb and {ra[y], rb[y]} == {1, 2}]
    return max(shared) if shared else None


def ordinal(n):
    """20 -> '20th', 21 -> '21st'. The teens are the special case."""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# ══ Box scores ════════════════════════════════════════════════════════════
# Separate HTML pages, not inline modals — a DELIBERATE divergence from the
# WNBA site. It makes each game an entity page with a URL, a canonical and a
# sitemap entry: ~36 more indexable pages off one template, on a site whose
# whole distribution bet is search. The WNBA site's inline box scores are
# right for a single-file daily page and wrong for a tournament archive.

# Column set and order are the WNBA box score's, deliberately unchanged
# (build_stats_page.py `_STAT_COLS`) — a reader who knows one should not have
# to relearn the other. The one divergence Jason asked for is the player NAME:
# the WNBA site shows "C. Clark" via `short_name()`, this shows "Caitlin
# Clark". A tournament audience is meeting most of these players for the
# first time, and an initial helps only someone who already knows the surname.
BOX_COLS = [("min", "MIN"), ("pts", "PTS"), ("fg", "FG"), ("tp", "3PT"),
            ("ft", "FT"), ("reb", "R"), ("oreb", "OR"), ("ast", "A"),
            ("stl", "S"), ("blk", "B"), ("tov", "TO"), ("pf", "PF"),
            ("plus_minus", "+/\u2212")]

# The team-comparison block above the box score, same shape and order as the
# WNBA site's `_TS_COLS`: a values row per team with a percentage row beneath.
TEAM_COLS = [("fg", "FG"), ("tp", "3PT"), ("ft", "FT"), ("tror", "TR/OR"),
             ("ast", "A"), ("tov", "TO"), ("pf", "PF")]


def fmt_min(v):
    """Truncated whole minutes, as the WNBA box score shows them.

    `_fmt_min` in build_stats_page.py rounds a float to an int. FIBA feeds
    tend to give "34:36", so this accepts either and still renders 35 — the
    reader-facing format is the contract, not the input format.
    """
    if v is None:
        return None
    if isinstance(v, str) and ":" in v:
        mm, ss = v.split(":", 1)
        try:
            return str(int(mm) + (1 if int(ss) >= 30 else 0))
        except ValueError:
            return None
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return None


def made_att(p, col):
    """'4/12', or None when either half is missing.

    Never falls back to 0: a fabricated 0/0 is indistinguishable from a real
    one and would silently corrupt the team totals computed from these rows.
    """
    m, a = p.get(f"{col}m"), p.get(f"{col}a")
    return None if m is None or a is None else f"{m}/{a}"


def pct(m, a):
    return "\u2014" if not a else f"{100.0 * m / a:.1f}%"


def box_name(p, published):
    """The box score's player cell: position, then the FULL name.

    The WNBA row is `<gm-pos>POS</gm-pos> C. Clark` — position first, surname
    initialised. Position is kept because a reader scanning a box score uses
    it; the initial is dropped because a tournament audience is meeting most
    of these players for the first time and "S. Talbot" helps only someone who
    already knows the surname. Jersey numbers live on the roster table, not
    here, for the same reason the WNBA box omits them: at thirteen stat
    columns the row is already at its width budget on a phone.
    """
    pos = (f'<span class="bx-pos">{esc(p["position"])}</span> '
           if p.get("position") else '<span class="bx-pos"></span> ')
    href = player_href(p["name"], published)
    name = (f'<a href="{href}" {CROSS_SITE} style="color:var(--text);'
            f'text-decoration:none;font-weight:500">{esc(p["name"])}</a>'
            if href else f'<span style="font-weight:500">{esc(p["name"])}</span>')
    return pos + name


def box_cell(p, col):
    """One player-row cell. Correct-or-blank at cell level: a stat the feed
    did not carry is an em dash, never a zero."""
    if col == "min":
        v = fmt_min(p.get("min"))
    elif col in ("fg", "tp", "ft"):
        v = made_att(p, col)
    elif col == "plus_minus":
        raw = p.get("plus_minus")
        v = None if raw is None else (f"+{raw}" if raw > 0 else str(raw))
    else:
        v = p.get(col)
    if v is None:
        return DASH
    return f"<b>{esc(str(v))}</b>" if col == "pts" else esc(str(v))


def team_totals(players):
    """Sum a team's player rows. Any missing component makes the whole total
    None rather than a quiet undercount — the same rule as `box_cell`, one
    level up, and the reason it matters more here: a total is the number a
    reader is most likely to quote."""
    def total(key):
        vals = [p.get(key) for p in players]
        return None if any(v is None for v in vals) else sum(vals)
    t = {k: total(k) for k in
         ("fgm", "fga", "tpm", "tpa", "ftm", "fta",
          "reb", "oreb", "ast", "tov", "pf")}
    return t


def team_stats_block(sides, names):
    """FG / 3PT / FT / TR-OR / A / TO / PF, values over percentages."""
    head = ("<tr><th></th>"
            + "".join(f'<th class="r">{esc(l)}</th>' for _, l in TEAM_COLS)
            + "</tr>")
    body = ""
    for side, t in zip(sides, names):
        tt = team_totals(side.get("players", []))

        def ma(k):
            m, a = tt[f"{k}m"], tt[f"{k}a"]
            return DASH if m is None or a is None else f"{m}/{a}"

        vals = [ma("fg"), ma("tp"), ma("ft"),
                (DASH if tt["reb"] is None or tt["oreb"] is None
                 else f'{tt["reb"]}/{tt["oreb"]}'),
                DASH if tt["ast"] is None else str(tt["ast"]),
                DASH if tt["tov"] is None else str(tt["tov"]),
                DASH if tt["pf"] is None else str(tt["pf"])]
        pcts = [DASH if tt[f"{k}m"] is None else pct(tt[f"{k}m"], tt[f"{k}a"])
                for k in ("fg", "tp", "ft")] + ["", "", "", ""]
        body += (f'<tr><td>{t["flag"]} <span class="tla">{esc(t["code"])}'
                 f'</span></td>'
                 + "".join(f'<td class="r num">{v}</td>' for v in vals)
                 + "</tr><tr class=\"pct\"><td></td>"
                 + "".join(f'<td class="r num">{v}</td>' for v in pcts)
                 + "</tr>")
    return table_scroll(f'<table class="bx pct ts">{head}{body}</table>')


def team_box_table(side, t, published):
    """One team's full box score, split STARTERS / BENCH like the WNBA site.

    The split is data-driven: a feed that does not mark starters yields one
    undivided table rather than an invented "STARTERS" heading over an
    arbitrary five.
    """
    players = side.get("players", [])
    head = ('<tr><th>Player</th>'
            + "".join(f'<th class="r">{esc(l)}</th>' for _, l in BOX_COLS)
            + "</tr>")

    def rows_for(group):
        return "".join(
            f'<tr><td>{box_name(p, published)}</td>'
            + "".join(f'<td class="r num">{box_cell(p, c)}</td>'
                      for c, _ in BOX_COLS)
            + "</tr>" for p in group)

    if any("starter" in p for p in players):
        starters = [p for p in players if p.get("starter")]
        bench = [p for p in players if not p.get("starter")]
        ncols = len(BOX_COLS) + 1
        body = ""
        for label, group in (("Starters", starters), ("Bench", bench)):
            if group:
                body += (f'<tr class="sec"><td colspan="{ncols}">{label}</td>'
                         f'</tr>{rows_for(group)}')
    else:
        body = rows_for(players)

    cap = (f'<div class="bx-cap">{t["flag"]} '
           f'<a href="/teams/{team_slug(t)}/" style="text-decoration:none;'
           f'color:var(--text)">{esc(t["name"])}</a></div>')
    return cap + table_scroll(f'<table class="bx">{head}{body}</table>')


def line_score(sides, names):
    """Quarter-by-quarter, with the WNBA site's integrity guard: the periods
    must reconcile to the team's final total or the whole block is omitted.
    Correct-or-blank — we never show quarters we cannot verify."""
    if not all(s.get("linescore") for s in sides):
        return ""
    lens = {len(s["linescore"]) for s in sides}
    if len(lens) != 1:
        return ""
    for s in sides:
        if sum(s["linescore"]) != s["score"]:
            return ""
    n = lens.pop()

    def label(i):
        if i < 4:
            return str(i + 1)
        return "OT" if n == 5 else f"OT{i - 3}"

    head = ("<tr><th></th>"
            + "".join(f'<th class="r">{label(i)}</th>' for i in range(n))
            + '<th class="r">T</th></tr>')
    body = ""
    for s, t in zip(sides, names):
        body += (f'<tr><td>{t["flag"]} <span class="tla">{esc(t["code"])}'
                 f'</span></td>'
                 + "".join(f'<td class="r num">{q}</td>' for q in s["linescore"])
                 + f'<td class="r num" style="font-weight:700">{s["score"]}'
                 f'</td></tr>')
    return table_scroll(f'<table class="bx">{head}{body}</table>')


def page_boxscore(box, rows_by_id, teams, published):
    """One game, as its own page.

    Structure mirrors the WNBA site's inline box score exactly — header,
    line score, Team Stats, then a full table per team — rendered in the WWC
    visual language (national flags, cyan accent, sans/scoped-mono type) and
    with full first names instead of initials.
    """
    row = rows_by_id[box["game_id"]]
    sides = box["teams"]
    names = [teams[s["schedule_key"]] for s in sides]
    scores = [s["score"] for s in sides]
    title = f'{names[0]["name"]} {scores[0]}\u2013{scores[1]} {names[1]["name"]}'

    out = []
    if box.get("_fixture"):
        # Belt and braces. The fixture is already excluded from the sitemap
        # and from any non-preview run, but a rendered HTML file can be
        # opened directly, mailed, or screenshotted — so the page says what
        # it is on its face rather than relying on how it was reached.
        out.append(
            '<div class="path" style="border-color:var(--neg);color:var(--neg)">'
            '<b>SYNTHETIC TEST DATA.</b> Not a real game and not a prediction — '
            'this page exists to exercise the box-score template before FIBA '
            'publishes anything. It is never published.</div>')

    out.append(f'<a class="bx-back" href="{GAMES_PATH}">\u2190 All games</a>')

    won = 0 if scores[0] >= scores[1] else 1
    head = ['<div class="bx-hd">']
    for i, (t, sc) in enumerate(zip(names, scores)):
        cls = "" if i == won else " lost"
        align = "" if i == 0 else " r"
        head.append(
            f'<div class="side{align}{cls}">'
            f'<div class="tm">{t["flag"]} '
            f'<a href="/teams/{team_slug(t)}/" style="text-decoration:none;'
            f'color:inherit">{esc(t["name"])}</a></div>'
            f'<div class="sc">{sc}</div></div>')
        if i == 0:
            head.append('<div class="fin">Final</div>')
    head.append("</div>")
    out.append("".join(head))

    phase = PHASE.get(row["phase"], row["phase"])
    group = f' {row["group"]}' if row["group"] else ""
    out.append(f'<div class="bx-meta">{esc(phase)}{group} \u00b7 '
               f'{DAYNAME.get(row["date"], row["date"])} \u00b7 Berlin</div>')

    out.append(line_score(sides, names))
    out.append('<h2 class="sec">Team stats</h2>')
    out.append(team_stats_block(sides, names))
    out.append('<h2 class="sec">Box score</h2>')
    for side, t in zip(sides, names):
        out.append(team_box_table(side, t, published))

    return shell(f'/games/{box["game_id"]}/',
                 f"{title} \u2014 {TOURNAMENT_NAME} 2026", "".join(out),
                 f"Full box score: {title}, "
                 f"{DAYNAME.get(row['date'], row['date'])} in Berlin.")


def load_boxscores(preview):
    """Real box scores from `data/`, which a future FIBA fetch writes.

    `--preview` additionally loads the tracked FIXTURE, which exists so the
    template and the data FORMAT are testable now rather than first exercised
    on a live match day. It is never loaded by a plain run: publishing an
    invented game as though it happened is the exact failure this codebase
    calls correct-or-blank, and it would be indistinguishable from a real
    result to a reader and to Google.
    """
    boxes = []
    if BOXSCORE_DIR.is_dir():
        for path in sorted(BOXSCORE_DIR.glob("*.json")):
            boxes.append(json.loads(path.read_text(encoding="utf-8")))
    if preview and BOXSCORE_FIXTURE.exists():
        boxes.append(json.loads(BOXSCORE_FIXTURE.read_text(encoding="utf-8")))
    return boxes


# ══ Emit ══════════════════════════════════════════════════════════════════

def write(path, html):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--preview", action="store_true",
                    help="also render the box-score fixture (never published)")
    args = ap.parse_args()

    WWC.ensure_dirs()
    doc, teams = load_teams()
    rows = load_schedule()
    results = load_results()
    published = wnba_player_pages()

    # --preview renders to sites/wwc/preview/, NEVER to public/, and that
    # separation is the actual safety mechanism rather than a convenience.
    # Anything under public/ is `git add`ed by the workflow and uploaded by
    # `wrangler deploy`, so a fixture written there would go live on the real
    # hostname even though the sitemap omits it. `sites/*/preview/` is already
    # gitignored, so a preview build cannot be committed or deployed by
    # accident. Consequence, and it is the point: a plain run has no code path
    # that emits the fixture at all.
    pub = (WWC.site_dir / "preview") if args.preview else WWC.public_dir

    # A stale page for a team that left the field, or a box score for a game
    # that got re-keyed, would sit in the output forever and keep being
    # deployed. The emitter owns this directory: clear the generated trees
    # each run.
    for sub in ("teams", "groups", "key", "guide", "games"):
        shutil.rmtree(pub / sub, ignore_errors=True)
    pub.mkdir(parents=True, exist_ok=True)

    # Box scores are loaded BEFORE the pages that link to them, so every
    # "Final" that becomes a link is a link to a page this same run emits.
    rows_by_id = {r["game_id"]: r for r in rows}
    boxes = load_boxscores(args.preview)
    box_ids = {b["game_id"] for b in boxes}

    # Both front-door candidates route through the same two names, so the
    # landing page is written to index.html and the other to its own path
    # without either page function knowing which it is.
    def out_path(url):
        return pub / "index.html" if url == "/" else \
            pub / url.strip("/") / "index.html"

    paths = [GUIDE_PATH, GAMES_PATH]
    write(out_path(GUIDE_PATH), page_guide(doc, teams))
    write(out_path(GAMES_PATH), page_games(rows, teams, results, box_ids))
    write(pub / "teams" / "index.html", page_teams_index(doc))
    paths.append("/teams/")
    for t in doc["teams"]:
        p = f"/teams/{team_slug(t)}/"
        write(pub / "teams" / team_slug(t) / "index.html",
              page_team(t, rows, teams, results, box_ids, published))
        paths.append(p)
    write(pub / "groups" / "index.html",
          page_groups(doc, rows, teams, results, box_ids))
    paths.append("/groups/")

    for box in boxes:
        gid = box["game_id"]
        write(pub / "games" / gid / "index.html",
              page_boxscore(box, rows_by_id, teams, published))
        # A fixture page is rendered for inspection but MUST NOT enter the
        # sitemap — that is the line between a local preview and telling
        # Google a fabricated game exists.
        if not box.get("_fixture"):
            paths.append(f"/games/{gid}/")

    # lastmod is the tournament's own data date, not today: the pages
    # genuinely have not changed since the reference data did, and a
    # lastmod that moves every build on a static programme site is a
    # freshness claim we cannot back.
    lastmod = doc["_schema"]["generated"][:10]
    # `sag.seo` writes into cfg.public_dir, so a preview run redirects it via
    # the config's own override rather than by writing paths by hand — the
    # same move golden_check.py makes, and the reason that override exists.
    out_cfg = (dataclasses.replace(WWC, public_dir_override=pub)
               if args.preview else WWC)
    seo.write_sitemap(out_cfg, paths, lastmod)
    seo.write_robots(out_cfg)

    print(f"WWC pages -> {pub}: {len(paths)} in sitemap "
          f"({len(doc['teams'])} teams, {len(rows)} games, "
          f"{len(boxes)} box scores)")
    if args.preview:
        print("  --preview: wrote to preview/ (gitignored, never deployed); "
              "fixture EXCLUDED from sitemap")


if __name__ == "__main__":
    main()
