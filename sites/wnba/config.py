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
)
