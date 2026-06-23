#!/usr/bin/env python3
"""Fetch 2026 WNBA player + team box scores, play-by-play, and today's schedule,
writing three CSVs and a JSON file that build_stats_page.py consumes.

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

Schedule: the ESPN scoreboard endpoint provides today's game slate (tip times,
home/away teams, game state). This is needed because the box score CSVs only
contain completed games. The scoreboard returns UTC times; we convert to ET.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import sportsdataverse.wnba as wnba

SEASON = 2026
HERE = Path(__file__).resolve().parent
PLAYER_CSV = HERE / "wnba_player_box_2026.csv"
TEAM_CSV = HERE / "wnba_team_box_2026.csv"
PBP_CSV = HERE / "wnba_pbp_2026.csv"
SCHEDULE_JSON = HERE / "wnba_schedule_today.json"
ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
)
ET = ZoneInfo("America/New_York")

# Warn (don't fail) if the newest game in the data is older than this.
MAX_STALENESS_DAYS = 2


def fetch() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """Pull player and team box scores + play-by-play as pandas DataFrames."""
    player = wnba.load_wnba_player_boxscore(seasons=[SEASON], return_as_pandas=True)
    team = wnba.load_wnba_team_boxscore(seasons=[SEASON], return_as_pandas=True)

    # Guard: never overwrite good CSVs with an empty pull (e.g. a transient
    # upstream outage). Exiting non-zero makes the CI step fail loudly.
    if player is None or team is None or player.empty or team.empty:
        sys.exit("ERROR: fetch returned no rows — aborting without touching the CSVs.")

    # PBP is needed for line scores in box scores. Fail soft — box scores
    # still render without it, just missing the quarter-by-quarter breakdown.
    pbp = None
    try:
        pbp = wnba.load_wnba_pbp(seasons=[SEASON], return_as_pandas=True)
        if pbp is not None and pbp.empty:
            pbp = None
    except Exception as e:
        print(f"WARNING: PBP fetch failed ({e}) — box scores will omit line scores.")

    return player, team, pbp


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


def fetch_schedule() -> None:
    """Fetch today's WNBA schedule from ESPN's scoreboard endpoint.

    Writes wnba_schedule_today.json with the ET date used and a list of games:
    {away, home, tip_et, state} where state is pre/in/post.
    Fails soft — writes an empty-games file on error so the build still runs.
    """
    today_et = datetime.now(ET).date()
    date_str = today_et.strftime("%Y%m%d")
    empty = {"date": str(today_et), "games": []}

    try:
        resp = requests.get(
            ESPN_SCOREBOARD,
            params={"dates": date_str},
            headers={"User-Agent": "wnba-stats-fetch"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"WARNING: schedule fetch failed ({e}) — writing empty schedule.")
        SCHEDULE_JSON.write_text(json.dumps(empty, indent=2))
        return

    games = []
    for event in data.get("events", []):
        competition = event.get("competitions", [{}])[0]
        competitors = competition.get("competitors", [])
        if len(competitors) != 2:
            continue

        # competitors[0] is home, competitors[1] is away in ESPN's format
        teams = {}
        for comp in competitors:
            ha = comp.get("homeAway", "")
            abbr = comp.get("team", {}).get("abbreviation", "")
            teams[ha] = abbr

        # Convert UTC tip time to ET
        utc_str = event.get("date", "")
        state = event.get("status", {}).get("type", {}).get("state", "pre")
        tip_et = ""
        if utc_str:
            try:
                utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
                et_dt = utc_dt.astimezone(ET)
                tip_et = et_dt.strftime("%-I:%M %p ET")
            except Exception:
                tip_et = ""

        games.append({
            "away": teams.get("away", ""),
            "home": teams.get("home", ""),
            "tip_et": tip_et,
            "state": state,
        })

    result = {"date": str(today_et), "games": games}
    SCHEDULE_JSON.write_text(json.dumps(result, indent=2))
    print(f"Schedule for {today_et}: {len(games)} game(s) → {SCHEDULE_JSON.name}")


def regression_check(player: pd.DataFrame, team: pd.DataFrame) -> None:
    """Abort if the new pull has fewer games than the existing CSVs.

    The sportsdataverse cache is periodically rebuilt; a mid-rebuild fetch can
    return fewer games than the previous pull. This guard prevents overwriting
    good data with a regressed cache.
    """
    for label, df, path in [("player", player, PLAYER_CSV), ("team", team, TEAM_CSV)]:
        if not path.exists():
            continue
        try:
            old = pd.read_csv(path)
        except Exception:
            continue
        old_games = old["game_id"].nunique()
        new_games = df["game_id"].nunique()
        if new_games < old_games:
            sys.exit(
                f"ERROR: {label} regression — new pull has {new_games} games, "
                f"existing CSV has {old_games}. Cache may be mid-rebuild; "
                f"aborting without overwriting."
            )
        print(f"{label}: {old_games} → {new_games} games ({new_games - old_games:+d})")


def main() -> None:
    player, team, pbp = fetch()
    freshness_check(player)
    regression_check(player, team)
    player.to_csv(PLAYER_CSV, index=False)
    team.to_csv(TEAM_CSV, index=False)
    print(f"Wrote {PLAYER_CSV.name} and {TEAM_CSV.name}")
    if pbp is not None:
        pbp.to_csv(PBP_CSV, index=False)
        print(f"Wrote {PBP_CSV.name} ({len(pbp):,} rows)")
    else:
        print("WARNING: no PBP data written — line scores will be unavailable.")
    fetch_schedule()


if __name__ == "__main__":
    main()
