#!/usr/bin/env python3
"""Fetch 2026 WNBA player + team box scores and write the two CSVs that
build_stats_page.py consumes. This replaces the manual R/wehoop step so the
whole pipeline is one Python toolchain and can run unattended in CI.

Data source: the `sportsdataverse` package (the Python sibling of the R
`wehoop` package). It reads the same cached box-score data wehoop reads, so
the column schema is identical to the CSVs you've been generating by hand —
this is a drop-in replacement, no changes to build_stats_page.py required.

Freshness note: load_wnba_*_boxscore() reads a community-maintained cache
that is refreshed on a regular cadence during the season. In practice a
morning run will include the prior night's finals, but if you ever observe a
lag you can upgrade the fetch to hit ESPN's live endpoints directly
(sportsdataverse exposes espn_wnba_schedule() + espn_wnba_summary() for that).
The freshness_check() below prints how old the newest game is and warns if it
looks stale, so a lag won't fail silently.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import sportsdataverse.wnba as wnba

SEASON = 2026
HERE = Path(__file__).resolve().parent
PLAYER_CSV = HERE / "wnba_player_box_2026.csv"
TEAM_CSV = HERE / "wnba_team_box_2026.csv"

# Warn (don't fail) if the newest game in the data is older than this.
MAX_STALENESS_DAYS = 2


def fetch() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull player and team box scores as pandas DataFrames."""
    player = wnba.load_wnba_player_boxscore(seasons=[SEASON], return_as_pandas=True)
    team = wnba.load_wnba_team_boxscore(seasons=[SEASON], return_as_pandas=True)

    # Guard: never overwrite good CSVs with an empty pull (e.g. a transient
    # upstream outage). Exiting non-zero makes the CI step fail loudly.
    if player is None or team is None or player.empty or team.empty:
        sys.exit("ERROR: fetch returned no rows — aborting without touching the CSVs.")
    return player, team


def freshness_check(player: pd.DataFrame) -> None:
    latest = pd.to_datetime(player["game_date"]).max()
    age_days = (datetime.now(timezone.utc).date() - latest.date()).days
    print(f"Latest game_date in data: {latest.date()} ({age_days} day(s) old)")
    print(f"Games: {player['game_id'].nunique()} | player rows: {len(player):,}")
    if age_days > MAX_STALENESS_DAYS:
        print(
            f"WARNING: newest game is {age_days} days old — the upstream cache "
            "may be lagging. Consider switching to the live ESPN endpoints."
        )


def main() -> None:
    player, team = fetch()
    freshness_check(player)
    player.to_csv(PLAYER_CSV, index=False)
    team.to_csv(TEAM_CSV, index=False)
    print(f"Wrote {PLAYER_CSV.name} and {TEAM_CSV.name}")


if __name__ == "__main__":
    main()
