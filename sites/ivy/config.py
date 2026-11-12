"""Ivy League women's basketball site configuration (LOCAL PROTOTYPE).

The third LeagueConfig and the first NCAAW consumer of `core/`. Like WWC it
diverges from the shared tokens on exactly one: `--accent`.

Accent — ivy green, #5FC98E. Not WNBA amber (#f5a623), not WWC cyan
(#35D0FF). The league is named for the plant, so the colour is the one piece of
identity no school owns: every member has its own colours (Columbia blue,
Princeton orange, Harvard crimson, Yale blue, Dartmouth green, Brown brown,
Cornell red, Penn red-and-blue), and picking any of them would read as taking a
side on a site meant for all eight. This green is light enough to hold
contrast on the shared near-black ground (~9:1 against #0f0f0f), and it sits
far enough from Dartmouth's forest green (#005730) that nobody mistakes it for
a school colour. Because the accent is green, this site does NOT colour wins
green / losses red — wins are carried by weight, losses by --muted — so the
accent never has to mean two things.
"""

from pathlib import Path

from sag.config import LeagueConfig

IVY = LeagueConfig(
    slug="ivy",
    # The ESPN season key for 2026-27 is "2027"; the display label is separate.
    season="2027",
    site_dir=Path(__file__).resolve().parent,
    display_name="Ivy WBB",
    base_url="https://ivy-hoops.statsataglance.com",
    cf_analytics_token=None,
    jersey_prefix="#",
    accent="#5FC98E",
)

#: ESPN league slug and the Ivy League's ESPN conference group id.
ESPN_LEAGUE = "womens-college-basketball"
IVY_GROUP_ID = "12"
#: ESPN's Division I parent group. Non-D-I opponents carry parent "52".
D1_PARENT_GROUP = "50"

SEASON_LABEL = "2026–27"
#: Previous ESPN season key, fetched for Ivy teams only, for head-to-head.
PREV_SEASON = "2026"

#: The eight members, keyed by ESPN team id (verified 2026-11-10 against
#: /teams/{id}: every one carries groups.id == "12").
IVY_TEAMS = {
    "225": "Brown",
    "171": "Columbia",
    "172": "Cornell",
    "159": "Dartmouth",
    "108": "Harvard",
    "219": "Penn",
    "163": "Princeton",
    "43": "Yale",
}

#: Display timezone. Every Ivy campus is on Eastern time.
TZ = "America/New_York"

#: Display-name overrides for opponents whose ESPN short name is ambiguous.
#: 2598 is ESPN's "Saint Francis Red Wolves" — Saint Francis University in
#: Loretto, Pa., which moved to Division III for 2026-27 (Jason, from the
#: school's athletics site, 2026-11-11). Not St. Francis Brooklyn.
SHORT_NAME_OVERRIDES = {
    "2598": "Saint Francis (Pa.)",
}
