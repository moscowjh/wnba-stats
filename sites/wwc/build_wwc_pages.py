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
import unicodedata
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
#: Leaders is never a front-door candidate, so it is a plain constant rather
#: than a derived one — but it is still a constant. The Games page shipped a
#: canonical pointing at the home page for three days because a path literal
#: was typed once and then the front door moved (2026-08-29); nothing on this
#: site writes a surface path by hand any more.
LEADERS_PATH = "/leaders/"

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
#: A SECOND fixture, and a different job: three fabricated finals across five
#: teams, so the Leaders aggregation is exercised over MULTIPLE games. One
#: game cannot test what Leaders is actually made of — a games-played column,
#: a total beside an average, or the rule that a null excludes a player from
#: one board and not the rest. Same rules as the single fixture: `--preview`
#: only, `_fixture: true` on every game, banner on every page it reaches, and
#: a hard build failure if one ever reaches an aggregate on a real run.
LEADERS_FIXTURE = REF / "leaders_fixture.json"


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
  /* The WNBA marker. Strengthened 2026-09-03 when the Club column made it
     the thing the eye is meant to find down a twelve-row table — heavier box,
     600 weight, a tint of the accent behind it. The HUE is untouched on
     purpose: Option C (2026-08-23) makes --accent the only token allowed to
     diverge between the two sites, so a second blue is not available. */
  .wn{{color:var(--accent);font-size:10px;border:1.5px solid var(--accent);
    padding:1px 5px;border-radius:2px;white-space:nowrap;font-weight:600;
    background:color-mix(in srgb, var(--accent) 10%, transparent)}}
  /* The generated count line above each roster table. Reads as a caption, not
     as a heading — the table is the programme, this only says how to read it. */
  .wnl{{margin:0 0 8px}}
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
  /* ── Leaders ────────────────────────────────────────────────────────── */
  /* Five cards in one auto-fill grid, the shape the WNBA site's Leaders tab
     already uses, so a reader who knows one site is not relearning the other.
     No new colour: the card is --surface on --border like every other card
     here, and the heading takes --accent. minmax(272px) means three across at
     the 900px body and exactly one across on a 375px phone, at full width.

     No table-scroll wrapper, and the reason is the name cell WRAPS rather
     than fits: only `th` carries nowrap site-wide, and the four headers are
     Player / GP / Tot / PPG. So the longest name in the field ("Pallas
     Kunaiyi-Akpanah", 22 chars) reflows onto a second line inside the card
     instead of pushing the numbers off a phone. Do not add nowrap to these
     cells — that is what would turn a wrap into an overflow. */
  .lgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(272px,1fr));
    gap:7px;margin:4px 0 2px}}
  .lcard{{background:var(--surface);border:1px solid var(--border);
    padding:9px 11px 3px}}
  .lcard h3{{color:var(--accent);font-size:11px;letter-spacing:.8px;
    text-transform:uppercase;font-weight:700;margin-bottom:1px}}
  .lcard table{{font-size:11.5px}}
  .lcard td,.lcard th{{padding:4px 3px}}
  .lcard tr:last-child td{{border-bottom:none}}
  .lcard .tla{{font-size:10px}}
  .lrk{{color:var(--muted);display:inline-block;min-width:15px}}
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

#: Whether the nav carries a Leaders entry. Set ONCE by `main()`, before any
#: page is rendered, from whether this run actually has final box scores to
#: rank — never from the calendar, and never from `results.json`.
#:
#: Results and box scores arrive from different places and can lag each other
#: (`game_cell()` already assumes this and refuses to link a score to a box
#: page that is not being emitted). Gating the tab on results would therefore
#: put up a Leaders tab over an empty board — the exact thing that kept this
#: tab off the 31 August launch. Gating it on the data the page RANKS means
#: the tab cannot appear before there is something on it.
#:
#: Module state rather than a parameter because `shell()` builds the nav for
#: every page and threading a flag through six page functions to say one thing
#: about the whole run is more code and more places to get it wrong. It is
#: assigned exactly once, in `main()`, above every `write()` call.
_LEADERS_IN_NAV = False


# Nav is a list of (path, label), front door first. Leaders is the only
# CONDITIONAL entry: it was deliberately absent until 4 September, and it now
# appears by itself the moment the first game goes final rather than on a date
# somebody has to remember. The same data-driven lifecycle `game_cell()` and
# the standings table already use, applied to navigation — which is also what
# removes the deploy-timing question, since merging this before the tournament
# changes nothing visible.
def nav_items():
    guide = (GUIDE_PATH, GUIDE_TAB_LABEL)
    games = (GAMES_PATH, "Games")
    middle = [("/teams/", "Teams"), ("/groups/", "Groups")]
    if _LEADERS_IN_NAV:
        middle.append((LEADERS_PATH, "Leaders"))
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


SOCIAL_BRAND = "stats at a glance"
#: Short form for card headlines only — the full FIBA name is what
#: `<title>` carries, and a card has no room for both.
WC = "Women’s World Cup"


def social_title(what):
    """The card headline. NOT the page title — they are different surfaces.

    `<title>` is the Google result, and "Spain — FIBA Women’s Basketball World
    Cup 2026" is exactly right there: long, keyword-bearing, unambiguous. A link
    preview is not that. iMessage renders the title bold above the bare domain
    and shows NO description at all, so the same string wraps to three lines,
    leads with "FIBA", and on the landing page trailed off into "— a guide",
    which reads as a disclaimer rather than an offer.

    Brand first, subject second (Jason, 2026-09-02). The trade-off, recorded so
    it is a choice and not an accident: every card now opens with the same 17
    characters, so if a platform truncates hard the distinguishing half is what
    it drops. Accepted because the domain under the title already says
    statsataglance, and repetition across a set of forwarded links is the point.
    """
    return f"{SOCIAL_BRAND} | {what}"


def shell(path, title, body, description, extra_head="", social=None):
    """One page. `path` is both the canonical URL and the nav highlight.

    `social` overrides the card headline only; it never touches `<title>`.
    """
    nav = "".join(
        f'<a href="{p}"{" class=\"on\"" if p == path else ""}>{esc(l)}</a>'
        for p, l in nav_items())
    canonical = seo.canonical_url(WWC, path)
    # The beacon's page key must fit the analytics worker's 32-char slice —
    # over-long keys COLLIDE rather than truncate (DEPLOY.md, blob2).
    key = analytics_key(path)
    assert len(key) <= 32, f"analytics page key too long: {key!r}"
    # Shared with both WNBA emitters. Until 2026-09-02 this page shipped four
    # og:* tags and NO twitter:card, which is why forwarded links previewed as
    # bare URLs rather than as a card — twitter:card is the tag every one of
    # X / iMessage / Slack / WhatsApp reads to decide to draw a card at all.
    #
    # There is no `sites/wwc/public/og.png` yet, so `social_tags` deliberately
    # emits `twitter:card=summary` and ZERO og:image tags: a card pointing at a
    # missing PNG previews worse than no image, and these platforms cache hard.
    # Drop the file in and the next build starts emitting the full card with no
    # code change here.
    social = "\n".join(
        seo.social_tags(WWC, path, social or title, description))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
{social}
{extra_head}<style>
{PAGE_CSS}</style>
</head>
<body>
<div class="mast">
  <h1><a href="/">Women\u2019s Basketball World Cup</a></h1>
  <div class="brand">stats at a glance</div>
  <div class="strap">{TOURNAMENT_STRAP}</div>
</div>
{WNBA_PROMO_HTML}
<nav>{nav}</nav>
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
    # The group winner is named FIRST in every quarter-final. FIBA's schedule
    # alternates the sides (games 29-30 list the qualifier first, 31-32 the
    # group winner), which reads as a difference between the fixtures when it
    # is only a difference in how they were typed. Two of the four would
    # otherwise be mirror images of the other two for no reason a reader can
    # see. Only ever fires where one side is a group winner and the other is
    # not, so the qualifiers, semi-finals and final are untouched.
    parts = rule.split(" - ")
    if (len(parts) == 2 and parts[1].startswith("1st Grp")
            and not parts[0].startswith("1st Grp")):
        rule = f"{parts[1]} - {parts[0]}"
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
                 "in Berlin, with tip times in US Eastern and Berlin local.",
                 social=social_title(f"{WC} Schedule"))


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
            n = wnba_on_squad(t)
            # Singular matters: Puerto Rico and others sit at exactly 1.
            wl = (f'{n} WNBA player{"" if n == 1 else "s"}' if n
                  else 'no current WNBA players')
            apps = t["wwc_record"]["appearances_count"]
            out.append(
                f'<a class="tc" href="/teams/{team_slug(t)}/">'
                f'<div class="n">{t["flag"]} {esc(t["name"])}</div>'
                f'<div class="m">{esc(wl)} · {apps} World Cup'
                f'{"" if apps == 1 else "s"}</div></a>')
        out.append("</div>")
    return shell("/teams/", f"Teams — {TOURNAMENT_NAME} 2026", "".join(out),
                 "All 16 teams at the 2026 FIBA Women's Basketball World Cup, "
                 "by group, with their WNBA connections.",
                 social=social_title(f"{WC} Teams"))


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


def wnba_on_squad(t):
    """How many WNBA players this team is bringing.

    Reads the SQUAD, always. The country-level `wnba.current` fallback is gone
    (2026-09-03): `ingest_squads.py` now derives every roster row's `wnba` flag
    from the curated `wnba.players` block for all sixteen teams, so there is no
    longer a team whose squad cannot answer this.

    The distinction was never pedantic: `wnba.current` counts a country's WNBA
    players, the squad counts the ones actually travelling, and the two diverge
    as soon as anybody is cut. On the day real rosters landed (2026-08-31)
    EIGHT of fourteen teams disagreed — France's card said 11 while its own
    table showed 8 badges, and Hungary's said 0 above a table naming Dorka
    Juhász, who had not been cut at all. A reader who clicks the card can count
    the rows, so the card has to be the number they will arrive at.

    Since 2026-09-03 the roster table is the ONLY place these players are
    listed, and its count line comes from the same rows that render the badges
    — see `roster_block`, which refuses to emit a table where the two disagree.
    """
    return sum(1 for p in t["roster"]["players"] if p.get("wnba"))


def wnba_count_line(team_name, n, total):
    """The generated sentence above the roster table, or '' at zero.

    Generated, never typed, and derived from the same list that renders the
    badges. This sentence and the badges are the two halves of the bug this
    table replaces: a hand-maintained headline above a table reading a
    different source is how Hungary published "0 current players" over a row
    naming Dorka Juhász. One table can only count itself.
    """
    if not n:
        return ""
    verb = "player is" if n == 1 else "players are"
    word = CARDINAL.get(n, str(n))
    if n == total:
        return f"All {word} {team_name} {verb} in the WNBA."
    return f"{word.capitalize()} {team_name} {verb} in the WNBA."


#: One roster table, eight columns (2026-09-03). The separate "WNBA
#: connection" grid it replaces printed the same twelve names twice on the USA
#: page, eight of twelve again on France, seven on Australia — the split only
#: ever earned its keep on teams with few WNBA players, and it cost a whole
#: class of bug in exchange.
#:
#: Order is the SPINE first — Pos / No. / Name / Club — so that the columns
#: which survive a 380px phone without scrolling are the ones that identify a
#: player and say where she plays. Age / Height / Ctr. / Note are the
#: scrollable tail. NOTE: handoff §2 lists the source table's own order
#: (Age and Height before Club); §6 asks for Club in the spine and names the
#: tail explicitly. §6 wins here because Club is the column the redesign is
#: built around, and burying it past the fold contradicts the point.
#:
#: Each entry is (header, key, css class, extra <td> style). Every column
#: except Name renders only when at least one player on THIS team has a
#: value — see `roster_block`.
ROSTER_COLUMNS = [
    ("Pos.", "pos", "mu", "width:38px"),
    ("No.", "no", "mu num", "width:34px"),
    ("Name", "name", "", ""),
    ("Club", "club", "", "min-width:120px"),
    ("Age", "age", "mu num", "width:34px"),
    ("Height", "height", "mu", "width:64px;white-space:nowrap"),
    ("Ctr.", "ctr", "mu tla", "width:38px"),
    ("Note", "note", "mu", "white-space:normal;min-width:140px"),
]


def roster_block(t, published):
    """ONE table. It is the programme.

    Jason, 2026-09-03: "This is the program that most people who are watching
    it don't have." So this is not a data dump beside the prose — it is the
    thing a viewer cannot get anywhere else, and every column is here because
    a viewer watching a game wants it.

    Provisional rosters remain a PERMANENT design state, not a transient one:
    FIBA rosters need not be final until just before the tournament, and two
    of sixteen teams have no published squad at all.
    """
    r = t["roster"]
    label = {"final": "final 12",
             "pool": f'provisional — {r["player_count"]}-player pool',
             "not_announced": "not announced"}[r["status"]]
    out = [f'<h2 class="sec">Roster <span class="mu">— {esc(label)}</span></h2>']

    if r["players"]:
        status_by_name = {p["name"]: p for p in t["wnba"]["players"]}
        cells = [roster_cells(p, status_by_name, published)
                 for p in r["players"]]

        # Optional columns render PER TEAM, only when at least one player on
        # THIS team has a value. A column of twelve identical dashes reads as
        # broken rather than as honest. Nigeria (no published squad) and
        # Puerto Rico (a squad our own data holds better than the source) are
        # not in the Wikipedia capture, so they simply have no Age, Height or
        # Ctr. column — the tables that DO have the data are not held back to
        # match, and neither team is stubbed, placeholdered or dropped. Same
        # lifecycle logic the standings table uses, where W/L/PF/PA do not
        # exist until a game has been played.
        cols = [c for c in ROSTER_COLUMNS
                if c[1] == "name" or any(x[c[1]] for x in cells)]

        head = "".join(f"<th>{h}</th>" for h, *_ in cols)
        body = []
        for x in cells:
            tds = []
            for _, key, klass, style in cols:
                attrs = (f' class="{klass}"' if klass else "") + \
                        (f' style="{style}"' if style else "")
                tds.append(f'<td{attrs}>{x[key] or DASH}</td>')
            body.append(f"<tr>{''.join(tds)}</tr>")
        table = f"<table><tr>{head}</tr>{''.join(body)}</table>"

        # The regression test for the bug this table replaces, asserted at the
        # point of render rather than trusted. The count line and the badges
        # must be one number; if they are not, the page is the Hungary page
        # again and it is better to fail the build than to publish it.
        n = sum(1 for x in cells if x["is_wnba"])
        badges = table.count('class="wn"')
        if badges != n:
            raise SystemExit(
                f'{t["code"]}: roster table renders {badges} WNBA badges but '
                f'the count line claims {n} — these are the same players and '
                f'must be one number')

        line = wnba_count_line(t["name"], n, len(cells))
        if line:
            out.append(f'<div class="cnote wnl"><b>{esc(line)}</b></div>')
        out.append(table_scroll(table))
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
    if t["wnba"]["roster_basis"].startswith("proxy"):
        # Nigeria. No squad has been announced at all, so the names are
        # inference from nationality and camp invitations — say so plainly
        # rather than letting the table read as a call-up. This caveat used to
        # hang off the WNBA connection block; it moved here when that block was
        # removed, because deleting a section must not delete its warning.
        out.append('<div class="pend">No squad has been announced. These are '
                   'the players connected to this team, not a confirmed '
                   'call-up list.</div>')
    return "".join(out)


def roster_cells(p, status_by_name, published):
    """One row's worth of rendered cells. Reads fields; does not derive them.

    Everything that needed normalising — the name join, the club, the WNBA
    flag, the country code — was settled by `ingest_squads.py` under a human,
    once, with a hard failure available. Doing it here would mean doing it on
    every build, silently, where a missed join renders as a blank cell that
    looks like honest absence.
    """
    pf = p["plays_for"]
    is_wnba = bool(p.get("wnba"))
    return {
        "pos": esc(p["position"] or ""),
        "no": esc(str(p["number"])) if p.get("number") is not None else "",
        "name": roster_name(p, published),
        "club": club_cell(p),
        "age": esc(str(p["age"])) if p.get("age") is not None else "",
        "height": esc(p.get("height") or ""),
        "ctr": esc(pf.get("club_country") or ""),
        "note": note_cell(p, status_by_name),
        "is_wnba": is_wnba,
    }


def club_cell(p):
    """Where she plays. One column, and the emphasis of the whole table.

    A current WNBA player shows the three-letter team code boxed in the site
    accent — the `.wn` badge that already marks WNBA everywhere on this site.
    No new colour token: Option C (2026-08-23) makes `--accent` the only token
    allowed to diverge between the two sites, so this badge got a heavier box
    and weight rather than a second blue.

    Everyone else shows her club, plainly. That subordinates a European
    player's club to her WNBA team for the handful who have both, and it is an
    accepted trade-off — Jason, 2026-09-03: "My site is mainly for a US
    audience, so I can live with it." The club that loses is not dropped; it
    moves to the Note column, which is why that column exists.
    """
    pf = p["plays_for"]
    if p.get("wnba"):
        team = pf.get("wnba_team")
        return (f'<span class="wn">{esc(team)} (WNBA)</span>' if team
                else '<span class="wn">WNBA</span>')
    return esc(pf.get("club_name") or "")


def roster_name(p, published):
    """The name, linked to OUR player page wherever we publish one.

    The jersey number used to be printed here as a prefix; it has its own
    column since 2026-09-03.
    """
    href = player_href(p["name"], published)
    if href:
        return (f'<a href="{href}" {CROSS_SITE} style="font-weight:500;'
                f'color:var(--text)">{esc(p["name"])}</a>')
    return f'<span style="font-weight:500">{esc(p["name"])}</span>'


DASH = '<span class="mu">—</span>'
STATUS_LABEL = {"former": "former", "drafted_only": "drafted"}


def note_cell(p, status_by_name):
    """Short reader-facing context, or ''.

    Jason, 2026-09-03: "Those former WNBA team or NCAAWB affiliations are
    important." Two of the three sources exist; the third does not.

    1. FORMER / DRAFTED WNBA — synthesised from `wnba.players[].status`,
       because it is a fact about the player that the Club column can no
       longer carry once that column means "plays there now".
    2. The OTHER club, for a player who has both a WNBA team and a club
       abroad. The capture's single Club column cannot express both; this is
       where the loser goes (Kennedy Burke, ÇBK Mersin). Mostly empty today,
       because the source lists only the WNBA side for those players — empty,
       not invented.
    3. NCAA history — we do not have it, and the pages ship without it.
       Jason, 2026-09-03: "In an ideal world, we'd have time to research those
       former NCAA connections... At t-minus-36, we do not." It has no field in
       the schema and is not getting one for this tournament; the aspiration
       was retired rather than parked as a backlog item that would just sit
       there. Note that several players' CURRENT club IS an NCAA program
       (Auburn, TCU, Louisville, Kansas State, UCF, Wisconsin, Sam Houston,
       Fresno State) and renders in the Club column like any other club. That
       is not a gap being papered over: for a college player on a national
       team it is her main affiliation, and the Club column is where it
       belongs.

    The raw `note` field is MIXED-PURPOSE and cannot ship wholesale: of the 41
    notes in the file, roughly a third are internal provenance and hedging
    ("Not 'Megan DiLeo'", "Basketball Australia's release still says Chicago")
    rather than prose for a reader. Publishing those would leak our working
    notes onto a team page. Until an editorial pass splits them, only the
    synthesised status ships — which is why `NOTE_FIELD_IS_READER_SAFE` is
    False and is a switch rather than a deletion.
    """
    rec = status_by_name.get(p["name"])
    bits = []
    if rec and rec["status"] != "current":
        label = STATUS_LABEL.get(rec["status"], rec["status"])
        team = rec.get("wnba_team_full") or rec.get("wnba_team")
        bits.append(f'{label.capitalize()} {esc(team)}' if team
                    else f'{label.capitalize()} WNBA')
    other = p["plays_for"].get("other_club")
    if other:
        bits.append(f"Also {esc(other)}")
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
    # ONE roster table since 2026-09-03. The separate "WNBA connection" grid
    # that used to sit here is gone: on the USA page it printed the same twelve
    # names the roster printed, and its hand-maintained headline count read a
    # different source from the table below it.
    out.append(roster_block(t, published))
    out.append(fixtures_block(t, rows, teams, results, box_ids))

    n = wnba_on_squad(t)
    desc = (f'{t["name"]} at the 2026 FIBA Women\'s Basketball World Cup: '
            f'squad, coach, group fixtures and '
            f'{n if n else "no"} current WNBA player{"s" if n != 1 else ""}.')
    return shell(f"/teams/{team_slug(t)}/",
                 f'{t["name"]} — {TOURNAMENT_NAME} 2026',
                 "".join(out), desc, extra_head=team_jsonld(t),
                 social=social_title(f'{t["name"]} at the {WC}'))


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
for whoever is still tied.</p>''')
    return shell("/groups/", f"Groups — {TOURNAMENT_NAME} 2026", "".join(out),
                 "Group tables, fixtures and the route to the quarter-finals "
                 "at the 2026 FIBA Women's Basketball World Cup.",
                 social=social_title(f"{WC} Groups"))


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
    wnba_total = sum(wnba_on_squad(x) for x in doc["teams"])
    with_wnba = sum(1 for x in doc["teams"] if wnba_on_squad(x))

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
most star-studded, with {plink("Napheesa Collier")}, {plink("Breanna Stewart")},
{plink("Caitlin Clark")} and {plink("Paige Bueckers")} among its names. France,
which nearly beat the US in Paris in 2024, returns a strong team headlined by
{plink("Gabby Williams")}. Even the host nation, <b>{tlink("GERMANY")}</b>,
which has qualified only {NUM_WORD.get(len(ger["wwc_record"]["editions"]), len(ger["wwc_record"]["editions"]))}
before, carries {wnba_on_squad(ger)} WNBA players on its roster.</p>

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
                 "WNBA.",
                 social=social_title("Women\u2019s Basketball World Cup"))


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
                 f"{DAYNAME.get(row['date'], row['date'])} in Berlin.",
                 social=social_title(f"{title} \u2014 {WC}"))


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
    if preview and LEADERS_FIXTURE.exists():
        boxes.extend(json.loads(
            LEADERS_FIXTURE.read_text(encoding="utf-8"))["games"])
    return boxes


# ══ Leaders ═══════════════════════════════════════════════════════════════

#: The five boards, in render order: (stat key, card title, per-game label).
#:
#: COUNTING STATS ONLY — no FG%, 3P%, FT%, eFG%, TS%, or any other rate. That
#: is a product decision, not an omission (Product Brief line 71): over a
#: three-to-eight game group stage a 5-for-8 shooter leads every percentage
#: board there is. It also moots qualification thresholds entirely, which is
#: exactly where the open questions were hardest. The page states it in one
#: muted line so it does not read as something we forgot.
LEADER_BOARDS = [("pts", "Scoring", "PPG"), ("reb", "Rebounds", "RPG"),
                 ("ast", "Assists", "APG"), ("stl", "Steals", "SPG"),
                 ("blk", "Blocks", "BPG")]

#: Top 10 per board, matching the WNBA site's Leaders tab so the two sites
#: read alike. Ties at the cut line are truncated there too (`nlargest(10)`).
LEADER_TOP_N = 10


def _name_key(name):
    """The identity a box-score name aggregates under.

    There is NO player id in the box-score format — the join is
    (schedule_key, name) and nothing else — so this is the whole of a
    player's identity across games, and every WNBA `athlete_id` lesson
    (incident 2026-08-04) applies here with no id to fall back on.

    It normalises ENCODING and nothing else: NFC, the three space characters
    that are not a space, collapsed runs, case. Those are the same string
    written differently, and merging them is safe — U+00A0 alone broke every
    name join in the 2026-09-02 roster capture, silently.

    It deliberately does NOT try to be clever about spelling. Korea was
    re-romanised between two Wikipedia pulls a day apart (Kang Yi-seul → Kang
    Lee-seul); no normaliser joins those, and one that tried would sooner or
    later merge two different people. Real spelling drift splits a player in
    two here, which is why `report_name_joins()` exists — the guard is a loud
    report, not a hopeful match.
    """
    s = unicodedata.normalize("NFC", str(name))
    # Spelled as escapes, never as the literal characters. This is the
    # guard against invisible characters; writing it IN invisible
    # characters is one editor away from becoming a silent no-op.
    for ch in ("\u00a0", "\u2007", "\u202f"):
        s = s.replace(ch, " ")
    return " ".join(s.split()).casefold()


def _competition_ranks(vals):
    """Places for a ranked list, ties sharing the higher place: 1, 1, 3.

    ⚠️ SECOND COPY. `build_stats_page._competition_ranks` is the first, and
    the two must stay in step — a duplicated tie rule on two sites is exactly
    how the 2026-08-25 leader-tie bug happened (Clark and Thomas, 290 assists
    apiece, rendered 1 and 2 on the card while both player pages said "1st",
    which then failed the morning validation and gated the daily post).

    Copied rather than shared on purpose, and only for now: promoting it to
    `core/` moves the golden-check surface, which is not a thing to do the day
    before a tournament. Unifying the two is a backlog row for after the Cup.

    Equality is exact on the unrounded value, as it is there. Two players who
    merely display the same tenth are not tied and keep separate places.
    """
    places, prev_val, prev_place = [], None, 0
    for pos, v in enumerate(vals, start=1):
        place = prev_place if (prev_val is not None and v == prev_val) else pos
        places.append(place)
        prev_val, prev_place = v, place
    return places


def aggregate_box_players(finals):
    """Sum every final box score into one record per (team, player).

    Two rules do the real work here, and both are silent-wrong-data guards
    rather than conveniences:

    **A null is not a zero.** The box-score format's own contract says a stat
    the feed does not carry must be null, never 0, so that the template renders
    an em dash and a null makes the TEAM total null rather than a quiet
    undercount. Summing nulls as zero on a *displayed leaderboard* would
    reproduce precisely the undercount that rule exists to prevent, one level
    further from anyone able to notice. So a null does not add zero: it marks
    that category tainted for that player, and `compute_leaders()` drops her
    from that board rather than ranking her on a partial sum. Per category —
    a missing steal costs her the steals board and nothing else.

    **Games played is appearances, not games in the window.** A player who
    appears in two of three finals has GP 2. A DNP row of all-nulls removes
    itself from all five boards through the rule above, with no special case.
    """
    players = {}
    nameless = []
    for box in finals:
        for side in box["teams"]:
            key = side["schedule_key"]
            for p in side.get("players", []):
                raw = (p.get("name") or "").strip()
                if not raw:
                    # Rankable identity is the NAME here; there is nothing
                    # else. A nameless row cannot be aggregated at all, so say
                    # so rather than ranking a player called "".
                    nameless.append((box["game_id"], key))
                    continue
                rec = players.setdefault((key, _name_key(raw)), {
                    "name": raw, "team": key, "spellings": set(),
                    "gp": 0, "tot": {c: 0 for c, _, _ in LEADER_BOARDS},
                    "null": set(),
                })
                rec["spellings"].add(raw)
                rec["gp"] += 1
                for cat, _, _ in LEADER_BOARDS:
                    v = p.get(cat)
                    if v is None:
                        rec["null"].add(cat)
                    else:
                        rec["tot"][cat] += v
    return players, nameless


def report_name_joins(players, teams, nameless, log):
    """The (schedule_key, name) join, checked out loud. REPORTS, never fails.

    A legitimately unrostered player — a late replacement, an injury call-up —
    must not take the site down on a match day, so nothing here raises. It
    prints, and the step log is the instrument. Read it.

    Two different symptoms of the one failure:

    - **One player, several spellings.** Only reachable when two spellings
      normalise together, i.e. an encoding difference. Reported because a
      source that is drifting in a way we CAN absorb is a source that will
      shortly drift in a way we cannot.
    - **A box-score name that is not on that team's roster.** This is where
      real spelling drift surfaces: a re-romanised name splits into two
      records, the roster matches one of them, and the other is printed here.

    Teams whose roster is a pool or unannounced get one line saying there is
    nothing to check against, rather than twelve lines of noise that would
    train everyone to skip the section.
    """
    rosters = {k: {_name_key(p["name"]) for p in t["roster"]["players"]}
               for k, t in teams.items()}
    # Whether an unmatched name is ALARMING depends on what it is being
    # checked against. Mali's roster is a 22-name pool and Nigeria's is
    # inferred from nationality and camp invitations, so a name missing from
    # either is close to expected; a name missing from the USA's final twelve
    # is not. Carrying that on the line means the reader does not have to
    # remember which teams are which at 6am on a match day.
    provisional = {
        k: (t["roster"]["status"] != "final"
            or t["wnba"]["roster_basis"].startswith("proxy"))
        for k, t in teams.items()}
    unmatched, no_roster, drift = {}, set(), []
    for (key, nk), rec in sorted(players.items()):
        if len(rec["spellings"]) > 1:
            drift.append((key, sorted(rec["spellings"])))
        if not rosters.get(key):
            no_roster.add(key)
        elif nk not in rosters[key]:
            unmatched.setdefault(key, []).append(rec["name"])

    log(f"leaders: {len(players)} players across "
        f"{len({r['team'] for r in players.values()})} teams")
    for gid, key in nameless:
        log(f"  !! {key} in {gid}: a box-score row carries no name — "
            f"excluded from every board")
    for key, spellings in drift:
        log(f"  !! {key}: one player, {len(spellings)} spellings merged on "
            f"encoding — {' / '.join(spellings)}")
    for key in sorted(no_roster):
        log(f"  -- {key}: no published roster to check names against")
    for key, names in sorted(unmatched.items()):
        qual = (" (roster is provisional, so this is expected)"
                if provisional.get(key) else "")
        log(f"  !! {key}: {len(names)} box-score name(s) not on the roster{qual}"
            f" — {', '.join(names)}")
    if not (nameless or drift or unmatched):
        log("  every box-score name matches a rostered player")


def compute_leaders(boxes, teams, preview=False, log=print):
    """The five boards, from every FINAL box score this run holds.

    Returns the whole page's input, including the empty case: `games` is 0
    before a ball is bounced and `boards` is then empty, which is a state the
    page renders rather than a state it avoids.

    Ranked on the unrounded per-game average, displayed to a tenth, with GP on
    every row and the total beside the average. That combination is the answer
    to the small-sample problem: on 5 September the PPG leader is whoever had
    the single best game, and showing the total and the games played beside it
    makes that VISIBLE rather than hiding it behind a minimum-games rule we
    would then have to defend. Same instinct as the standings table's
    "Provisional" label. No qualification threshold is needed, which keeps the
    hardest open question closed.
    """
    finals = [b for b in boxes if b.get("status") == "final"]

    # A fixture must never reach an aggregate. The box-score PAGE banners
    # itself because a page is one game and says on its face what it is; a
    # leaderboard is a blend, and one synthetic game mixed into real ones
    # produces a board that is wrong in a way no banner can undo and no reader
    # can decompose. So: banner in --preview, hard failure in a normal build.
    # This is the same fail-loud posture as the WNBA fetch and `load_schedule`'s
    # duplicate-id assertion — the cost of stopping is a rebuild, the cost of
    # continuing is a published leaderboard nobody can trust.
    fixtures = [b["game_id"] for b in finals if b.get("_fixture")]
    if fixtures and not preview:
        raise SystemExit(
            f"REFUSING TO BUILD: synthetic fixture box score(s) {fixtures} "
            f"reached compute_leaders on a non-preview run. A fixture may "
            f"render its own page under --preview, where it banners itself; "
            f"it may never be summed into a leaderboard. Check "
            f"sites/wwc/data/boxscores/ for a stray fixture file.")

    players, nameless = aggregate_box_players(finals)
    if finals:
        report_name_joins(players, teams, nameless, log)

    boards = []
    for cat, title, unit in LEADER_BOARDS:
        # The average is computed ONCE, here, and the same number is both
        # ranked on and rendered. Computing it twice — once for the sort key,
        # once for the cell — is two renderings of one fact, which is the
        # trap the team pages collapsed two tables to escape on 2026-09-03.
        # Ranking on the unrounded value and displaying it to a tenth is the
        # WNBA board's behaviour, so two players who merely PRINT the same
        # tenth are not tied and keep separate places.
        rows = [{"name": r["name"], "team": r["team"], "gp": r["gp"],
                 "total": r["tot"][cat], "avg": r["tot"][cat] / r["gp"]}
                # The null rule, applied. Excluded from THIS board only.
                for r in players.values() if cat not in r["null"]]
        rows.sort(key=lambda x: (-x["avg"], -x["total"], x["name"]))
        top = rows[:LEADER_TOP_N]
        for place, x in zip(_competition_ranks([x["avg"] for x in top]), top):
            x["place"] = place
        boards.append((cat, title, unit, top))
        dropped = len(players) - len(rows)
        if dropped:
            log(f"  {title}: {dropped} player(s) held off this board — a null "
                f"{cat} means a partial sum, not a zero")

    # `games` counts box scores; `populated` counts RANKABLE ROWS, and it is
    # the latter that decides whether the tab exists. The two come apart in a
    # case that will really happen: FIBA marks a game final before its box
    # score carries player statistics, so a build lands one final box with
    # empty (or all-null) player lists. Gating on `games` there would raise
    # the tab over five empty tables — precisely the "a tab leading to an
    # empty board is worse than no tab" failure the whole delay existed to
    # avoid, arriving through the back door. Gate on the rows the page
    # actually renders.
    populated = any(rows for _, _, _, rows in boards)
    return {"games": len(finals), "populated": populated,
            "fixture": bool(fixtures), "boards": boards}


def page_leaders(lb, teams, published):
    """The Leaders page, in all three lifecycle states.

    It is EMITTED on every run, including before a ball is bounced, so the URL
    never 404s and the empty state is a real rendered artifact rather than a
    branch nobody has looked at. What is conditional is where it is ADVERTISED:
    `main()` adds it to the nav and the sitemap only once it has something on
    it, and while it is empty the page carries `noindex`. The whole Phase 1 bet
    is a search bet, and a thin empty page is the one kind Google should not be
    finding — it is the same correct-or-blank rule applied to a crawler instead
    of a reader.
    """
    n = lb["games"]
    out = []
    if lb["fixture"]:
        # Reachable only under --preview: `compute_leaders` refuses to blend a
        # fixture on any other run. A leaderboard is more screenshottable than
        # a box score, so it says what it is on its face too.
        out.append(
            '<div class="path" style="border-color:var(--neg);color:var(--neg)">'
            '<b>SYNTHETIC TEST DATA.</b> These boards are computed from '
            'fabricated box scores — not real games, not predictions. This '
            'page exists to exercise the leaders template before FIBA '
            'publishes anything. It is never published.</div>')

    if not lb["populated"]:
        out.append('<h2 class="sec">Leaders</h2>')
        if n:
            # Games are final but their box scores carry no player statistics
            # yet. Say which of the two states this is — "no games have been
            # played" would be a false statement about a tournament in
            # progress, and correct-or-blank applies to prose too.
            played = "1 game" if n == 1 else f"{n} games"
            out.append(f'<p class="prose">{esc(played.capitalize())} '
                       f'{"has" if n == 1 else "have"} been played, but the '
                       f'box scores do not carry player statistics yet. '
                       f'Tournament leaders appear here as soon as they do.'
                       f'</p>')
        else:
            out.append('<p class="prose">No games have been played yet. '
                       'Tournament leaders appear here as soon as the first '
                       'box score is final — the first game tips on '
                       '<b>Friday 4 September</b>.</p>')
        out.append(f'<div class="next"><b>In the meantime:</b> the '
                   f'<a href="/teams/">sixteen squads</a> are published in '
                   f'full, and the <a href="{GAMES_PATH}">schedule</a> carries '
                   f'every tip time in US Eastern and Berlin local.</div>')
    else:
        games = "1 game" if n == 1 else f"{n} games"
        out.append(f'<h2 class="sec">Tournament leaders '
                   f'<span class="mu">— through {esc(games)}</span></h2>')
        out.append('<div class="lgrid">')
        for _, title, unit, rows in lb["boards"]:
            out.append(leader_card(title, unit, rows, teams, published))
        out.append('</div>')
        out.append(gp_caption(lb))

    return shell(LEADERS_PATH, f"Leaders — {TOURNAMENT_NAME} 2026",
                 "".join(out),
                 (f"Points, rebounds, assists, steals and blocks leaders "
                  f"through {n} games at the 2026 FIBA Women's Basketball "
                  f"World Cup in Berlin." if n else
                  "Tournament leaders for the 2026 FIBA Women's Basketball "
                  "World Cup in Berlin, published as games are played."),
                 extra_head=("" if lb["populated"] else
                             '<meta name="robots" content="noindex,follow">\n'),
                 social=social_title(f"{WC} Leaders"))


def gp_caption(lb):
    """What the ranking means, and — once it matters — why GP varies.

    The first sentence is unconditional: the board is ordered on a rate, and
    the divisor is printed on every row. A reader should never have to work
    out which number the order came from.

    The second appears only once the field has actually spread, and it is the
    honest disclosure behind the §3 decision to rank on the average. Group
    play is three games for everyone; from the knockouts on, a team eliminated
    in the group stage stops at three while a finalist plays six or seven, so
    a player can top a per-game board having played barely half as many games
    as the rival below her. That is what ranking on a rate MEANS rather than a
    defect in it — ranking on totals would simply invert the bias, rewarding
    survivors for having played more — but it reads as an error to anyone who
    has not noticed the GP column, which is precisely who this line is for.

    The trigger is the measured spread among ranked players, not a date and
    not a round number of group games: it says something only when there is
    something to say, in any state the tournament passes through.
    """
    gps = [r["gp"] for _, _, _, rows in lb["boards"] for r in rows]
    if not gps:
        return ""
    spread = ("" if max(gps) - min(gps) < 2 else
              " A team\u2019s run ends when it is eliminated, so a player can "
              "lead on fewer games than someone still in the tournament.")
    return (f'<div class="cnote">Ranked on the per-game average. Games played '
            f'(GP) is shown for every player.{spread}</div>')


def leader_card(title, unit, rows, teams, published):
    """One board. Four columns: player, GP, total, average.

    The name is the link, to OUR WNBA player page where we publish one — the
    same bridge `roster_name()` and `box_name()` carry, and the reason this
    site exists. The team code stays plain text: two competing links in one
    narrow cell is worse than one obvious one.
    """
    head = (f'<tr><th>Player</th><th class="r">GP</th>'
            f'<th class="r">Tot</th><th class="r">{esc(unit)}</th></tr>')
    body = []
    for r in rows:
        t = teams[r["team"]]
        href = player_href(r["name"], published)
        name = (f'<a href="{href}" {CROSS_SITE} style="font-weight:500;'
                f'color:var(--text)">{esc(r["name"])}</a>' if href else
                f'<span style="font-weight:500">{esc(r["name"])}</span>')
        body.append(
            f'<tr><td><span class="lrk num">{r["place"]}.</span> {name} '
            f'<span class="tla mu">{t["flag"]} {esc(t["code"])}</span></td>'
            f'<td class="r num mu">{r["gp"]}</td>'
            f'<td class="r num">{r["total"]}</td>'
            f'<td class="r num"><b>{r["avg"]:.1f}</b></td></tr>')
    return (f'<div class="lcard"><h3>{esc(title)}</h3>'
            f'<table>{head}{"".join(body)}</table></div>')


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
    for sub in ("teams", "groups", "key", "guide", "games", "leaders"):
        shutil.rmtree(pub / sub, ignore_errors=True)
    pub.mkdir(parents=True, exist_ok=True)

    # Box scores are loaded BEFORE the pages that link to them, so every
    # "Final" that becomes a link is a link to a page this same run emits.
    rows_by_id = {r["game_id"]: r for r in rows}
    boxes = load_boxscores(args.preview)
    box_ids = {b["game_id"] for b in boxes}

    # Leaders is computed BEFORE any page is written, because whether it
    # exists changes the nav on every one of them. `_LEADERS_IN_NAV` is
    # assigned here and nowhere else — see the comment on the constant for
    # why the tab is gated on final BOX SCORES rather than on results.
    global _LEADERS_IN_NAV
    lb = compute_leaders(boxes, teams, args.preview)
    _LEADERS_IN_NAV = lb["populated"]

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

    # Always emitted, including empty: the URL never 404s and the empty state
    # is a rendered artifact rather than a branch nobody has seen. It enters
    # the SITEMAP only once it has something on it — and never on fixture
    # data, the same line the fixture box scores are held behind. While it is
    # empty it also ships `noindex` (see `page_leaders`), so the gap between
    # "not advertised" and "not indexable" is closed rather than assumed.
    write(pub / "leaders" / "index.html", page_leaders(lb, teams, published))
    if lb["populated"] and not lb["fixture"]:
        paths.append(LEADERS_PATH)

    for box in boxes:
        gid = box["game_id"]
        write(pub / "games" / gid / "index.html",
              page_boxscore(box, rows_by_id, teams, published))
        # A fixture page is rendered for inspection but MUST NOT enter the
        # sitemap — that is the line between a local preview and telling
        # Google a fabricated game exists.
        if not box.get("_fixture"):
            paths.append(f"/games/{gid}/")

    # lastmod is the tournament's own DATA date, never today's date: the
    # pages genuinely have not changed since the data behind them did, and a
    # lastmod that moves every build on a static programme site is a
    # freshness claim we cannot back.
    #
    # TWO sources, because the reference file alone is not the whole data
    # date. `_schema.generated` covers editorial and roster edits; the date
    # of the newest game we hold a result for covers the tournament itself.
    # Without the second term the stamp freezes on Sep 4 and the sitemap
    # spends the entire Cup claiming nothing changed while every Games,
    # Groups and team page changes daily — the inverse of the over-claiming
    # this comment was originally written to prevent, and just as wrong.
    #
    # Taking the max means the stamp advances when, and only when, something
    # on the pages really moved. ISO dates compare correctly as strings, so
    # no parsing is needed. Results whose game_id is not in the schedule are
    # skipped rather than crashing the build on a match day.
    #
    # NOTE: `_schema.generated` is hand-maintained. It went stale once
    # already — it still read 2026-08-19 on 2026-09-01, nine days after the
    # real rosters for all 16 teams landed. Bump it in the same commit as
    # any edit to this file's contents.
    played = [rows_by_id[g]["date"] for g in results if g in rows_by_id]
    lastmod = max([doc["_schema"]["generated"][:10]] + played)
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
    print(f"  leaders: {'nav + ' if _LEADERS_IN_NAV else 'empty state, '}"
          f"{lb['games']} final box score(s)"
          f"{', FIXTURE data' if lb['fixture'] else ''}")
    if args.preview:
        print("  --preview: wrote to preview/ (gitignored, never deployed); "
              "fixture EXCLUDED from sitemap")


if __name__ == "__main__":
    main()
