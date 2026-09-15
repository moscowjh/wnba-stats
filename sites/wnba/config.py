"""WNBA site configuration.

Everything league-specific that the shared library needs is declared here;
paths are derived from it by convention (see `sag.config.LeagueConfig`).
A second league is a second file like this one, not a second set of paths.
"""

from pathlib import Path

from sag.config import LeagueConfig

WNBA = LeagueConfig(
    slug="wnba",
    season="2026",
    site_dir=Path(__file__).resolve().parent,
    display_name="WNBA",
    base_url="https://wnba.statsataglance.com",
    # Public by design — this token is already visible in every served page.
    cf_analytics_token="7397748b2cd6455b8887cfe01269a48b",
    jersey_prefix="#",
    rank_badge_top_n=20,
)


# ── Navigation ────────────────────────────────────────────────────────────
# The subpage strip, added 2026-09-15 with the nav rebuild. It lives HERE, not
# in `core/`, and is passed into `chrome.subpage_header_html(tabs=...)`: the
# label set is a WNBA fact, and a shared layer that knew the word "Standings"
# would be exactly the WNBA-shaped-with-the-names-filed-off that `sites/wwc/`
# exists to disprove. `core/` renders whatever list it is handed.
#
# Order matches the homepage strip. Homepage sections are hash links; Teams and
# Players are real pages. `ACTIVE_*` name which entry lights on which surface —
# box score pages light Games, and become Playoffs in Step 2.
SUBPAGE_TABS = [
    ("Games",     "/#games"),
    ("Standings", "/#standings"),
    ("Leaders",   "/#leaders"),
    ("Teams",     "/teams/"),
    ("Players",   "/players/"),
    ("Stats",     "/#stats"),
    ("Key",       "/#abbreviations"),
]

ACTIVE_TEAMS = "Teams"
ACTIVE_PLAYERS = "Players"
ACTIVE_GAMES = "Games"

# ── Conferences ───────────────────────────────────────────────────────────
# Used only by the /teams/ index, which groups East then West. This mapping
# exists NOWHERE else in the repo — ESPN's box score carries no conference —
# so it is typed here, on the site side rather than in `core/`.
#
# Verified 2026-09-14 against the WNBA's own 2026 schedule release and
# Wikipedia's Eastern/Western Conference pages:
#   https://www.wnba.com/news/2026-schedule-release
#   https://en.wikipedia.org/wiki/Eastern_Conference_(WNBA)
# Portland (PDX) and Toronto (TOR) are the 2026 expansion teams: Toronto East,
# Portland West. 7 East, 8 West.
CONFERENCE = {
    "ATL": "East", "CHI": "East", "CON": "East", "IND": "East",
    "NYL": "East", "TOR": "East", "WAS": "East",
    "DAL": "West", "GSV": "West", "LVA": "West", "LAS": "West",
    "MIN": "West", "PHX": "West", "PDX": "West", "SEA": "West",
}
