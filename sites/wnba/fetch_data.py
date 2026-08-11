#!/usr/bin/env python3
"""Fetch 2026 WNBA player + team box scores, play-by-play, and today's schedule,
writing three CSVs and a JSON file that build_stats_page.py consumes.

Data source: ESPN's public API endpoints (scoreboard + game summary).
The scoreboard endpoint discovers completed games; the summary endpoint
provides box scores and play-by-play for each game. Fetches are incremental:
only new games are fetched and appended to existing CSVs.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from config import WNBA

SEASON = 2026
SEASON_START = "2026-05-08"

# Games that ESPN's scoreboard reports as completed but that do NOT count
# toward official regular-season stats/standings (stats.wnba.com,
# basketball-reference). The Commissioner's Cup Championship is the classic
# case: it's a standalone title game whose box score is tracked separately.
# Group-play Cup games DO count and must NOT be excluded.
#
# Detection is automatic via _is_noncounting_game() (season type + notes),
# but any game id listed here is force-excluded as a belt-and-suspenders
# override. Seed with the 2026 Commissioner's Cup Championship if/when the
# automatic filter needs backing up.
EXCLUDE_GAME_IDS: set[int] = {
    401857321,  # 2026 Commissioner's Cup Championship (LV @ NY, 2026-06-30).
                # Now also caught structurally via competition.type=CC (id 39) in
                # _is_noncounting_game(); kept here as a redundant safety net in
                # case ESPN ever drops that field.
}
# Paths come from the league config, derived by convention (D13) — never
# spelled out here, so a second league cannot collide with this one by typo.
CFG = WNBA
PLAYER_CSV = CFG.player_box
TEAM_CSV = CFG.team_box

# Columns a pre-existing CSV must already carry to be reused for an incremental
# fetch. When a schema migration adds a column (e.g. athlete_id, season_type),
# an older CSV lacking it is discarded so main() does one clean full re-fetch
# rather than appending new-schema rows onto old-schema ones (which would leave
# the new columns blank for every historical game). This mirrors, and backstops,
# bumping the actions/cache vN token in CI.
REQUIRED_PLAYER_COLS = {"athlete_id", "season_type"}
REQUIRED_TEAM_COLS = {"season_type"}
PBP_CSV = CFG.pbp
LINESCORE_JSON = CFG.linescores
SCHEDULE_JSON = CFG.schedule_today
# Where to reach ESPN's API.
#
# 2026-08-05: `site.api.espn.com` — the host used since the ESPN migration —
# returned an Akamai "Access Denied" 403 to every request for about four hours
# (~11:17 to ~14:45 UTC), then recovered on its own.
#
# During the window the failure was host-wide and indiscriminate: 403 for *any*
# User-Agent (browser string, script string, none at all), from the Actions
# runner, from home broadband, and from a third network — and for other leagues'
# paths (`.../nba/scoreboard`) too. The only variable that changed the outcome
# was the hostname: `site.web.api.espn.com` served 200s throughout.
#
# So it was NOT a block on us, and NOT keyed on User-Agent or source IP. What it
# *was* remains unknown from outside ESPN — a rolled-back Akamai bot rule
# (possibly TLS-fingerprint-based, which no header change would defeat) fits as
# well as an infrastructure fault. Both hosts work today; we stay on the one
# that stayed up.
#
# Don't reinstate the old host on the theory that it's "fixed now" — the point
# is that either host can fail. ESPN_ORIGIN is the real mitigation: switching is
# an env var, not a deploy. Point it at another ESPN host or at the espn-proxy
# Worker (see workers/espn-proxy/README.md), both of which mirror the same paths.
ESPN_ORIGIN = os.environ.get("ESPN_ORIGIN", "https://site.web.api.espn.com").rstrip("/")
ESPN_SCOREBOARD = f"{ESPN_ORIGIN}/apis/site/v2/sports/basketball/wnba/scoreboard"
ESPN_SUMMARY = f"{ESPN_ORIGIN}/apis/site/v2/sports/basketball/wnba/summary"
ET = ZoneInfo("America/New_York")
MAX_STALENESS_DAYS = 2
FETCH_DELAY = 0.5

# ESPN team abbreviations → official WNBA TLAs (as used by stats.wnba.com).
# Applied at fetch time — never at load — so the CSVs, JSON artifacts, and the
# site all speak the official language (decided 2026-07-26; lets the Layer-2
# validator join on team codes without a crosswalk). Teams not listed here are
# spelled identically in both systems.
WNBA_TLA = {
    "POR": "PDX", "GS": "GSV", "LV": "LVA",
    "LA": "LAS", "NY": "NYL", "WSH": "WAS",
}


def _tla(abbr: str) -> str:
    """Official WNBA TLA for an ESPN team abbreviation."""
    return WNBA_TLA.get(abbr, abbr)


# ── HTTP helper ──────────────────────────────────────────────────────────

# Conventional request headers.
#
# These did NOT fix the 2026-08-05 outage — the host swap above did. They were
# added while the working theory was User-Agent-based bot filtering, and that
# specific theory was tested and disproved: during the outage
# `site.api.espn.com` 403'd with a full browser header set, and
# `site.web.api.espn.com` answered 200 with no User-Agent at all. The UA was
# never the variable.
#
# (A bot rule keying on TLS fingerprint rather than headers is still consistent
# with what was observed — every client tested was curl or requests, none a real
# browser. If so, these headers still wouldn't help: spoofing a UA string does
# not change a TLS fingerprint.)
#
# They're kept because asking for JSON and identifying a real client is correct
# HTTP manners, not because they're load-bearing. When ESPN next breaks, check
# whether a *different host* answers before reaching for header tricks.
ESPN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.espn.com/",
    "Origin": "https://www.espn.com",
    "Cache-Control": "no-cache",
}

# Transient-failure policy. ESPN's public API blips for anywhere from a few
# seconds to a few minutes (2026-08-05: the whole 11:17 UTC build missed a game
# because a single scoreboard call failed and the old policy gave up after one
# 2-second retry). These attempts span ~60s of wall clock, which covers the
# short blips without stalling a ~170-call full rebuild for long.
#
#   attempt:  0 ---2s--- 1 ---4s--- 2 ---8s--- 3 ---16s--- 4 ---30s--- 5
#
# Delays are jittered to 50–100% of nominal so parallel or repeated runs don't
# retry in lockstep. Only *transient* failures are retried: connection errors,
# timeouts, 5xx, and 429. A 404 (game id that doesn't exist) still fails fast.
MAX_ATTEMPTS = 6
BASE_BACKOFF = 2.0
MAX_BACKOFF = 30.0
RETRY_STATUS = {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def _backoff(attempt: int) -> float:
    """Jittered exponential delay, in seconds, before retry number `attempt`."""
    nominal = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
    return nominal * (0.5 + random.random() / 2)


def espn_get(url: str, params: dict | None = None) -> dict:
    """GET JSON from ESPN, retrying transient errors with exponential backoff.

    Raises the last exception if every attempt fails, so callers still see a
    real error rather than a silent empty result."""
    headers = dict(ESPN_HEADERS)
    # Shared secret for the espn-proxy Worker, so it isn't an open proxy.
    # Harmless (and ignored) when calling ESPN directly.
    proxy_key = os.environ.get("ESPN_PROXY_KEY")
    if proxy_key:
        headers["X-Proxy-Key"] = proxy_key
    label = url.rsplit("/", 1)[-1]
    last_err: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status not in RETRY_STATUS:
                raise           # 404 and friends: a real error, fail fast
            last_err, reason = e, f"HTTP {status}"
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError, ValueError) as e:
            # ValueError covers a truncated/HTML body that .json() can't parse —
            # a common shape for an upstream hiccup, and worth another attempt.
            last_err, reason = e, type(e).__name__

        if attempt == MAX_ATTEMPTS - 1:
            print(f"ERROR: {label} failed after {MAX_ATTEMPTS} attempts ({reason})")
            raise last_err
        delay = _backoff(attempt)
        print(f"RETRY: {label} {reason} — attempt {attempt + 1}/{MAX_ATTEMPTS}, "
              f"sleeping {delay:.1f}s", flush=True)
        time.sleep(delay)


# ── Game discovery ───────────────────────────────────────────────────────

def _is_noncounting_game(event: dict) -> str | None:
    """Return a reason string if this scoreboard event is a completed game that
    does NOT count toward official stats (so it should be skipped entirely),
    else None.

    "Non-counting" means exhibitions that ESPN reports as completed games but
    that never belong in the data: the All-Star Game and the Commissioner's Cup
    Championship. Postseason games (season type 3) are NOT non-counting — they
    are kept and tagged with season_type so regular-season aggregations can
    filter them out while future playoff views can select them.

    Signals, in order of reliability:
      1. Explicit override list (EXCLUDE_GAME_IDS).
      2. Structural competition type. ESPN mistags the All-Star Game as
         season.type=2 ("regular season"), but the competition carries a
         distinct type (id 4 / abbreviation "ALLSTAR"), which is reliable.
      3. Season type outside {regular(2), postseason(3)} — i.e. preseason(1) or
         anything unexpected. Regular-season Cup group-play games are type 2, so
         this won't over-exclude them, and postseason is deliberately kept.
      4. A notes headline flagging the All-Star Game, or the Commissioner's Cup
         *Championship*/*Final* specifically. Group-play Cup games are
         notes-tagged too, so we require "championship"/"final" — never match
         plain "commissioner's cup".
    """
    try:
        game_id = int(event["id"])
    except (KeyError, ValueError, TypeError):
        return None

    if game_id in EXCLUDE_GAME_IDS:
        return "override list"

    comp = (event.get("competitions") or [{}])[0]

    # Structural competition-type check — the most reliable signal for the
    # exhibitions ESPN mislabels as regular season (season.type=2). Verified
    # against ESPN's full 2026 scoreboard: only these two carry a non-standard
    # competition.type, while Cup GROUP-PLAY games (which DO count) are plain
    # "STD" — so this never over-excludes them.
    #   ALLSTAR (id 4)  — the All-Star Game
    #   CC      (id 39) — the Commissioner's Cup Championship
    comp_type = comp.get("type") or {}
    ct_id = str(comp_type.get("id", "")).strip()
    ct_abbr = str(comp_type.get("abbreviation", "")).strip().upper()
    _NONCOUNTING_COMP = {
        "4": "all-star exhibition", "ALLSTAR": "all-star exhibition",
        "39": "Commissioner's Cup Championship", "CC": "Commissioner's Cup Championship",
    }
    reason = _NONCOUNTING_COMP.get(ct_id) or _NONCOUNTING_COMP.get(ct_abbr)
    if reason:
        return f"competition type {ct_abbr or ct_id!r} ({reason})"

    season_type = event.get("season", {}).get("type")
    if season_type is not None and season_type not in (2, 3):
        return f"season type {season_type} (not regular season or postseason)"

    notes = list(event.get("notes", [])) + list(comp.get("notes", []))
    for note in notes:
        headline = str(note.get("headline", "")).lower()
        if "all-star" in headline or "all star" in headline:
            return f"all-star note: {note.get('headline', '')!r}"
        if "commissioner" in headline and (
            "championship" in headline or "final" in headline
        ):
            return f"cup championship note: {note.get('headline', '')!r}"
    return None


def discover_games(start: date, end: date) -> tuple[list[tuple[int, str, int]], list[str]]:
    """Scan date range via scoreboard, return [(game_id, "YYYY-MM-DD",
    season_type)] for completed games worth keeping (regular season + playoffs;
    exhibitions like the All-Star Game and Cup Championship are skipped).

    season_type is ESPN's integer (2 = regular season, 3 = postseason); it is
    stored on each row so regular-season aggregations can filter to == 2 while
    future playoff views select == 3. Defaults to 2 when ESPN omits it.

    Returns (completed, failed_dates). A date whose scoreboard call failed is
    NOT the same as a date with no games, and the caller must not conflate
    them — see the 2026-08-05 note in main()."""
    completed = []
    failed_dates: list[str] = []
    d = start
    while d <= end:
        date_str = d.strftime("%Y%m%d")
        try:
            data = espn_get(ESPN_SCOREBOARD, {"dates": date_str})
        except Exception as e:
            print(f"WARNING: scoreboard fetch failed for {d} ({e})")
            failed_dates.append(d.isoformat())
            d += timedelta(days=1)
            continue

        iso_date = d.isoformat()
        for event in data.get("events", []):
            status_type = event.get("status", {}).get("type", {})
            state = status_type.get("state", "")
            if state != "post":
                continue
            game_id = int(event["id"])
            # A postponed/rescheduled game still lands in state="post" (it's no
            # longer "pre" or "in") even though it was never played. ESPN flags
            # this with completed=false and a name like STATUS_POSTPONED — check
            # that explicitly rather than inferring it later from a missing box
            # score, so the schedule scan (and its logs) reflect the real reason.
            if status_type.get("completed", True) is False:
                print(
                    f"SKIP: game {game_id} ({iso_date}) excluded — "
                    f"not completed ({status_type.get('description', 'unknown status')})"
                )
                continue
            skip_reason = _is_noncounting_game(event)
            if skip_reason:
                print(
                    f"SKIP: game {game_id} ({iso_date}) excluded — {skip_reason}"
                )
                continue
            st = event.get("season", {}).get("type")
            season_type = int(st) if st is not None else 2
            completed.append((game_id, iso_date, season_type))
        d += timedelta(days=1)
    return completed, failed_dates


# ── Parsing helpers ──────────────────────────────────────────────────────

def _split_ma(val: str) -> tuple[int, int]:
    """Split "M-A" string like "5-12" into (made, attempted)."""
    parts = val.split("-")
    return int(parts[0]), int(parts[1])


def _parse_plus_minus(val: str) -> float | None:
    """Parse "+3" → 3, "-7" → -7, empty/missing → NaN."""
    if not val or val == "--":
        return float("nan")
    return int(val)


def _extract_header_info(summary: dict) -> dict:
    """Extract team metadata from header.competitions[0].competitors[]."""
    competitors = summary["header"]["competitions"][0]["competitors"]
    teams = {}
    for comp in competitors:
        team_id = int(comp["id"])
        ha = comp["homeAway"]
        teams[ha] = {
            "team_id": team_id,
            "team_abbreviation": _tla(comp["team"]["abbreviation"]),
            "team_display_name": comp["team"].get("displayName", ""),
            "score": int(comp["score"]),
            "home_away": ha,
        }
    return teams


# ── Player box parsing ───────────────────────────────────────────────────

def parse_player_box(
    summary: dict, game_id: int, game_date: str, season_type: int
) -> list[dict]:
    """Parse boxscore.players[] into player row dicts."""
    header_teams = _extract_header_info(summary)
    abbr_to_info = {
        info["team_abbreviation"]: info for info in header_teams.values()
    }

    rows = []
    for team_entry in summary.get("boxscore", {}).get("players", []):
        team_abbr = _tla(team_entry["team"]["abbreviation"])
        team_info = abbr_to_info.get(team_abbr, {})
        team_id = team_info.get("team_id", 0)
        team_display_name = team_entry["team"].get(
            "displayName", team_info.get("team_display_name", "")
        )
        team_score = team_info.get("score", 0)
        home_away = team_info.get("home_away", "")

        for stat_group in team_entry.get("statistics", []):
            names = stat_group.get("names", [])
            name_idx = {n: i for i, n in enumerate(names)}

            for athlete_entry in stat_group.get("athletes", []):
                athlete = athlete_entry.get("athlete", {})
                dnp = athlete_entry.get("didNotPlay", False)
                stats = athlete_entry.get("stats", [])

                position = ""
                pos_obj = athlete.get("position", {})
                if isinstance(pos_obj, dict):
                    position = pos_obj.get("abbreviation", "")

                row = {
                    "game_id": game_id,
                    "game_date": game_date,
                    "season_type": season_type,
                    "athlete_id": athlete.get("id", ""),
                    "athlete_display_name": athlete.get("displayName", ""),
                    "team_abbreviation": team_abbr,
                    "team_display_name": team_display_name,
                    "team_id": team_id,
                    "team_score": team_score,
                    "athlete_position_abbreviation": position,
                    "home_away": home_away,
                    "starter": athlete_entry.get("starter", False),
                    "did_not_play": dnp,
                }

                if dnp or not stats:
                    row.update({
                        "minutes": float("nan"),
                        "points": 0,
                        "field_goals_made": 0,
                        "field_goals_attempted": 0,
                        "three_point_field_goals_made": 0,
                        "three_point_field_goals_attempted": 0,
                        "free_throws_made": 0,
                        "free_throws_attempted": 0,
                        "rebounds": 0,
                        "offensive_rebounds": 0,
                        "defensive_rebounds": 0,
                        "assists": 0,
                        "steals": 0,
                        "blocks": 0,
                        "turnovers": 0,
                        "fouls": 0,
                        "plus_minus": float("nan"),
                    })
                else:
                    def _stat(key, default="0"):
                        idx = name_idx.get(key)
                        if idx is None or idx >= len(stats):
                            return default
                        return stats[idx]

                    fg_m, fg_a = _split_ma(_stat("FG", "0-0"))
                    tp_m, tp_a = _split_ma(_stat("3PT", "0-0"))
                    ft_m, ft_a = _split_ma(_stat("FT", "0-0"))

                    min_str = _stat("MIN", "0")
                    try:
                        minutes = float(min_str)
                    except ValueError:
                        minutes = 0.0

                    row.update({
                        "minutes": minutes,
                        "points": int(_stat("PTS")),
                        "field_goals_made": fg_m,
                        "field_goals_attempted": fg_a,
                        "three_point_field_goals_made": tp_m,
                        "three_point_field_goals_attempted": tp_a,
                        "free_throws_made": ft_m,
                        "free_throws_attempted": ft_a,
                        "rebounds": int(_stat("REB")),
                        "offensive_rebounds": int(_stat("OREB")),
                        "defensive_rebounds": int(_stat("DREB")),
                        "assists": int(_stat("AST")),
                        "steals": int(_stat("STL")),
                        "blocks": int(_stat("BLK")),
                        "turnovers": int(_stat("TO")),
                        "fouls": int(_stat("PF")),
                        "plus_minus": _parse_plus_minus(_stat("+/-", "")),
                    })

                rows.append(row)

    return rows


# ── Team box parsing ─────────────────────────────────────────────────────

def parse_team_box(
    summary: dict, game_id: int, game_date: str, season_type: int
) -> list[dict]:
    """Parse boxscore.teams[] + header into 2 team row dicts."""
    header_teams = _extract_header_info(summary)
    team_stats_by_abbr = {}
    for team_entry in summary.get("boxscore", {}).get("teams", []):
        abbr = _tla(team_entry["team"]["abbreviation"])
        stat_dict = {}
        for s in team_entry.get("statistics", []):
            stat_dict[s["name"]] = s.get("displayValue", "")
        team_stats_by_abbr[abbr] = stat_dict

    rows = []
    for side in ("home", "away"):
        info = header_teams[side]
        other_side = "away" if side == "home" else "home"
        other = header_teams[other_side]

        stats = team_stats_by_abbr.get(info["team_abbreviation"], {})

        def _get_split(key: str) -> tuple[int, int]:
            val = stats.get(key, "0-0")
            parts = val.split("-")
            return int(parts[0]), int(parts[1])

        def _get_int(key: str) -> int:
            val = stats.get(key, "0")
            try:
                return int(val)
            except ValueError:
                return int(float(val))

        fg_m, fg_a = _get_split("fieldGoalsMade-fieldGoalsAttempted")
        tp_m, tp_a = _get_split("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
        ft_m, ft_a = _get_split("freeThrowsMade-freeThrowsAttempted")

        row = {
            "game_id": game_id,
            "game_date": game_date,
            "season_type": season_type,
            "team_id": info["team_id"],
            "team_display_name": info["team_display_name"],
            "team_abbreviation": info["team_abbreviation"],
            "team_score": info["score"],
            "opponent_team_id": other["team_id"],
            "opponent_team_score": other["score"],
            "team_winner": info["score"] > other["score"],
            "field_goals_made": fg_m,
            "field_goals_attempted": fg_a,
            "three_point_field_goals_made": tp_m,
            "three_point_field_goals_attempted": tp_a,
            "free_throws_made": ft_m,
            "free_throws_attempted": ft_a,
            "offensive_rebounds": _get_int("offensiveRebounds"),
            "defensive_rebounds": _get_int("defensiveRebounds"),
            "total_rebounds": _get_int("totalRebounds"),
            "assists": _get_int("assists"),
            "steals": _get_int("steals"),
            "blocks": _get_int("blocks"),
            "total_turnovers": _get_int("totalTurnovers"),
            "fouls": _get_int("fouls"),
        }
        rows.append(row)

    return rows


# ── Play-by-play parsing ────────────────────────────────────────────────

def parse_pbp(summary: dict, game_id: int) -> list[dict]:
    """Parse plays[] into PBP row dicts.

    The build does not consume this file — it exists as an event-level record
    for exploratory analysis, so we retain the descriptive fields ESPN provides
    (clock, play type, the human-readable `text` that carries player names and
    substitutions, and participant athlete ids) rather than just the running
    score. `athlete_id_*` and `team_abbrev` join back to the player/team boxes.
    """
    header_teams = _extract_header_info(summary)
    home_abbr = header_teams.get("home", {}).get("team_abbreviation", "")
    away_abbr = header_teams.get("away", {}).get("team_abbreviation", "")
    # Map acting-team ESPN id (a string on each play) -> abbreviation.
    id_to_abbr = {
        str(t["team_id"]): t["team_abbreviation"]
        for t in header_teams.values()
    }

    rows = []
    for play in summary.get("plays", []):
        period = play.get("period", {})
        period_num = period.get("number") if isinstance(period, dict) else None
        if period_num is None:
            continue

        clock = play.get("clock") or {}
        ptype = play.get("type") or {}
        team = play.get("team") or {}
        participants = play.get("participants") or []

        def _athlete_id(idx: int) -> str:
            if idx < len(participants):
                athlete = (participants[idx] or {}).get("athlete") or {}
                return str(athlete.get("id", ""))
            return ""

        row = {
            "game_id": game_id,
            "home_team_abbrev": home_abbr,
            "away_team_abbrev": away_abbr,
            "period_number": int(period_num),
            "game_play_number": int(play.get("sequenceNumber", 0)),
            "clock": clock.get("displayValue", ""),
            "home_score": int(play.get("homeScore", 0)),
            "away_score": int(play.get("awayScore", 0)),
            "team_abbrev": id_to_abbr.get(str(team.get("id", "")), ""),
            "play_type": ptype.get("text", ""),
            "scoring_play": bool(play.get("scoringPlay", False)),
            "score_value": int(play.get("scoreValue", 0) or 0),
            "shooting_play": bool(play.get("shootingPlay", False)),
            "athlete_id_1": _athlete_id(0),
            "athlete_id_2": _athlete_id(1),
            "text": play.get("text", "") or "",
        }
        rows.append(row)

    return rows


# ── Line score parsing ───────────────────────────────────────────────────

def _boxscore_ready(summary: dict) -> bool:
    """True if ESPN has actually populated box score data for this game.

    The scoreboard can mark a game "post" (final) before the summary endpoint's
    boxscore.teams/boxscore.players arrays are backfilled — a transient ESPN
    data-lag, not a real "no stats" game. Treating that as ready would write
    zeroed-out team rows and zero player rows that desync from each other and
    trip the player/team reconciliation check in build_stats_page.py. Callers
    should raise/skip on a not-ready game so it's retried on a later run once
    ESPN catches up, rather than being recorded as fetched with garbage data.
    """
    box = summary.get("boxscore", {})
    teams = box.get("teams", [])
    players = box.get("players", [])
    if not teams or not players:
        return False
    if not any(t.get("statistics") for t in teams):
        return False
    if not any(p.get("statistics") for p in players):
        return False
    return True


def parse_linescores(summary: dict, game_id: int) -> dict | None:
    """Extract ESPN's OFFICIAL per-quarter line scores from the game header.

    Returns {"home_abbr", "away_abbr", "home": [...], "away": [...]} or None
    if ESPN hasn't populated a complete linescores array for both teams.

    We deliberately do NOT derive line scores from play-by-play: reconstructing
    quarter splits from cumulative PBP scores is unreliable (the highest-numbered
    play in a period frequently carries a stale, lower score than the true
    end-of-quarter value, which scrambles the intermediate quarters while the
    totals still reconcile). Policy is "correct or blank": if the official array
    is missing or malformed, return None and the build omits the quarter columns.
    """
    try:
        competitors = summary["header"]["competitions"][0]["competitors"]
    except (KeyError, IndexError, TypeError):
        return None

    out: dict[str, dict] = {}
    for comp in competitors:
        ha = comp.get("homeAway")
        abbr = _tla(comp.get("team", {}).get("abbreviation", ""))
        ls = comp.get("linescores")
        if ha not in ("home", "away") or not ls:
            return None
        vals: list[int] = []
        for entry in ls:
            v = entry.get("value", entry.get("displayValue")) if isinstance(entry, dict) else entry
            try:
                vals.append(int(round(float(v))))
            except (TypeError, ValueError):
                return None
        out[ha] = {"abbr": abbr, "vals": vals}

    if "home" not in out or "away" not in out:
        return None
    if len(out["home"]["vals"]) != len(out["away"]["vals"]) or not out["home"]["vals"]:
        return None

    return {
        "home_abbr": out["home"]["abbr"],
        "away_abbr": out["away"]["abbr"],
        "home": out["home"]["vals"],
        "away": out["away"]["vals"],
    }


# ── CSV I/O ──────────────────────────────────────────────────────────────

def load_existing() -> tuple[set[int], pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]:
    """Read existing CSVs and return (known game_ids, player_df, team_df, pbp_df).
    Returns None for any CSV that doesn't exist or can't be read."""
    game_ids: set[int] = set()
    player_df = team_df = pbp_df = None

    # Read + schema-validate the box-score CSVs. If either is missing a required
    # column it predates a schema migration; discard ALL existing data (and the
    # PBP) so main() does one clean full re-fetch instead of concatenating
    # mismatched schemas. During the current season this only fires the first
    # run after the athlete_id/season_type migration.
    loaded: dict[str, pd.DataFrame] = {}
    stale = False
    for label, path, required in [
        ("player", PLAYER_CSV, REQUIRED_PLAYER_COLS),
        ("team", TEAM_CSV, REQUIRED_TEAM_COLS),
    ]:
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path)
        except Exception as e:
            print(f"WARNING: could not read {path.name} ({e})")
            continue
        missing = required - set(df.columns)
        if missing:
            print(f"SCHEMA: {path.name} is missing {sorted(missing)} — forcing "
                  "a full re-fetch to migrate the schema.")
            stale = True
        loaded[label] = df

    if stale:
        return set(), None, None, None

    for label in ("player", "team"):
        df = loaded.get(label)
        if df is None:
            continue
        game_ids.update(df["game_id"].unique())
        if label == "player":
            player_df = df
        else:
            team_df = df

    if PBP_CSV.exists():
        try:
            pbp_df = pd.read_csv(PBP_CSV)
        except Exception as e:
            print(f"WARNING: could not read {PBP_CSV.name} ({e})")

    return game_ids, player_df, team_df, pbp_df


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    """Write CSV atomically via temp file + rename."""
    tmp = path.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


# ── Checks (preserved from original) ────────────────────────────────────

def freshness_check(player: pd.DataFrame) -> None:
    latest = pd.to_datetime(player["game_date"]).max()
    age_days = (datetime.now(timezone.utc).date() - latest.date()).days
    print(f"Latest game_date in data: {latest.date()} ({age_days} day(s) old)")
    print(f"Games: {player['game_id'].nunique()} | player rows: {len(player):,}")
    if age_days > MAX_STALENESS_DAYS:
        print(
            f"WARNING: newest game is {age_days} days old — the ESPN data "
            "may be lagging or there may be no recent games."
        )


def regression_check(
    old_df: pd.DataFrame | None, new_df: pd.DataFrame, label: str
) -> None:
    """Abort if the new data has fewer games than the existing CSV."""
    if old_df is None:
        return
    old_games = old_df["game_id"].nunique()
    new_games = new_df["game_id"].nunique()
    if new_games < old_games:
        sys.exit(
            f"ERROR: {label} regression — new data has {new_games} games, "
            f"existing CSV has {old_games}. Aborting without overwriting."
        )
    print(f"{label}: {old_games} → {new_games} games ({new_games - old_games:+d})")


# ── Schedule (unchanged from original) ──────────────────────────────────

def fetch_schedule() -> bool:
    """Fetch today's WNBA schedule from ESPN's scoreboard endpoint.

    Writes sites/wnba/data/schedule_today.json with the ET date used, a `status`, and a
    list of games: {away, home, tip_et, state} where state is pre/in/post.
    Returns True if the schedule was actually retrieved.

    `status` is load-bearing and must not be dropped: "ok" with an empty games
    list means ESPN told us there are genuinely no games today, while
    "unavailable" means we never got an answer. The page renders those
    differently — on 2026-08-05 a failed fetch produced an empty list that the
    Games tab published as the confident, false claim "No games today." An
    empty list is a fact only when we know it's a fact.
    """
    today_et = datetime.now(ET).date()
    date_str = today_et.strftime("%Y%m%d")

    try:
        # Routed through espn_get so the schedule shares the browser-like
        # headers and the retry/backoff policy. Still fails soft below.
        data = espn_get(ESPN_SCOREBOARD, {"dates": date_str})
    except Exception as e:
        print(f"WARNING: schedule fetch failed ({e}) — marking it unavailable.")
        SCHEDULE_JSON.write_text(json.dumps(
            {"date": str(today_et), "status": "unavailable",
             "error": str(e)[:200], "games": []}, indent=2))
        return False

    games = []
    for event in data.get("events", []):
        competition = event.get("competitions", [{}])[0]
        competitors = competition.get("competitors", [])
        if len(competitors) != 2:
            continue

        teams = {}
        for comp in competitors:
            ha = comp.get("homeAway", "")
            abbr = _tla(comp.get("team", {}).get("abbreviation", ""))
            teams[ha] = abbr

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

    result = {"date": str(today_et), "status": "ok", "games": games}
    SCHEDULE_JSON.write_text(json.dumps(result, indent=2))
    print(f"Schedule for {today_et}: {len(games)} game(s) → {SCHEDULE_JSON.name}")
    return True


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    CFG.ensure_dirs()
    existing_ids, old_player, old_team, old_pbp = load_existing()
    print(f"Existing data: {len(existing_ids)} game(s) known")

    if old_player is not None:
        max_date = pd.to_datetime(old_player["game_date"]).max().date()
        scan_start = max_date - timedelta(days=1)
    else:
        scan_start = date.fromisoformat(SEASON_START)

    today = datetime.now(ET).date()
    print(f"Scanning scoreboard from {scan_start} to {today}...")
    completed, failed_dates = discover_games(scan_start, today)
    new_games = [(gid, d, st) for gid, d, st in completed if gid not in existing_ids]
    print(f"Found {len(completed)} completed game(s), {len(new_games)} new")

    if not new_games and old_player is None:
        sys.exit("ERROR: no games found and no existing data — nothing to write.")

    new_player_rows: list[dict] = []
    new_team_rows: list[dict] = []
    new_pbp_rows: list[dict] = []
    new_linescores: dict[str, dict] = {}
    failed = 0

    for i, (gid, game_date, season_type) in enumerate(new_games):
        try:
            summary = espn_get(ESPN_SUMMARY, {"event": str(gid)})
            if not _boxscore_ready(summary):
                raise ValueError(
                    "scoreboard reports final but boxscore isn't populated yet"
                )
            new_player_rows.extend(parse_player_box(summary, gid, game_date, season_type))
            new_team_rows.extend(parse_team_box(summary, gid, game_date, season_type))
            new_pbp_rows.extend(parse_pbp(summary, gid))
            ls = parse_linescores(summary, gid)
            if ls:
                new_linescores[str(gid)] = ls
            else:
                print(f"NOTE: game {gid} has no official line scores — "
                      "quarter columns will be blank for it")
        except Exception as e:
            print(f"WARNING: game {gid} ({game_date}) failed: {e}")
            failed += 1
            continue

        if i < len(new_games) - 1:
            time.sleep(FETCH_DELAY)

    if failed:
        print(f"WARNING: {failed} game(s) failed — will retry on next run")

    if new_player_rows:
        new_player_df = pd.DataFrame(new_player_rows)
        player_df = (
            pd.concat([old_player, new_player_df], ignore_index=True)
            if old_player is not None
            else new_player_df
        )
    elif old_player is not None:
        player_df = old_player
    else:
        sys.exit("ERROR: no player data available — aborting.")

    if new_team_rows:
        new_team_df = pd.DataFrame(new_team_rows)
        team_df = (
            pd.concat([old_team, new_team_df], ignore_index=True)
            if old_team is not None
            else new_team_df
        )
    elif old_team is not None:
        team_df = old_team
    else:
        sys.exit("ERROR: no team data available — aborting.")

    if new_pbp_rows:
        new_pbp_df = pd.DataFrame(new_pbp_rows)
        pbp_df = (
            pd.concat([old_pbp, new_pbp_df], ignore_index=True)
            if old_pbp is not None
            else new_pbp_df
        )
    else:
        pbp_df = old_pbp

    freshness_check(player_df)
    regression_check(old_player, player_df, "player")
    regression_check(old_team, team_df, "team")

    atomic_write_csv(player_df, PLAYER_CSV)
    atomic_write_csv(team_df, TEAM_CSV)
    print(f"Wrote {PLAYER_CSV.name} and {TEAM_CSV.name}")

    if pbp_df is not None:
        atomic_write_csv(pbp_df, PBP_CSV)
        print(f"Wrote {PBP_CSV.name} ({len(pbp_df):,} rows)")
    else:
        print("WARNING: no PBP data written.")

    # Official line scores — merge new games into the existing JSON.
    existing_ls: dict[str, dict] = {}
    if LINESCORE_JSON.exists():
        try:
            existing_ls = json.loads(LINESCORE_JSON.read_text())
        except Exception as e:
            print(f"WARNING: could not read {LINESCORE_JSON.name} ({e})")
    if new_linescores:
        existing_ls.update(new_linescores)
        tmp = LINESCORE_JSON.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing_ls, indent=0))
        os.replace(tmp, LINESCORE_JSON)
        print(f"Wrote {LINESCORE_JSON.name} "
              f"({len(new_linescores)} new, {len(existing_ls)} total)")

    schedule_ok = fetch_schedule()

    # ── Fail loud on an incomplete fetch ──────────────────────────────────
    #
    # Everything above has been written, so whatever we DID get is preserved in
    # the Actions cache for the next run. But we exit non-zero so the job stops
    # here: no build, no deploy, no commit.
    #
    # Why blocking is right (2026-08-05): ESPN began 403-ing every call from
    # the runner. Each failure was swallowed per-date, `discover_games` returned
    # nothing, and the build cheerfully republished stale data and went GREEN.
    # Three builds in a row "succeeded" while the site sat a full day behind and
    # told visitors "No games yesterday" — which was false. A silently-wrong
    # green build is far more expensive than a loudly-failed red one: the site
    # keeps yesterday's correct page, the run goes red, GitHub notifies, and the
    # cron-worker health check sees conclusion != success and auto-rebuilds.
    #
    # Set ALLOW_PARTIAL=1 to publish anyway (escape hatch for a day when ESPN is
    # half-broken and a partial update genuinely beats no update).
    problems = []
    if failed_dates:
        problems.append(
            f"{len(failed_dates)} scoreboard date(s) unreadable: "
            f"{', '.join(failed_dates)} — games on those dates cannot be "
            f"distinguished from no games at all"
        )
    if failed:
        problems.append(f"{failed} discovered game(s) failed to fetch")
    if not schedule_ok:
        problems.append("today's schedule is unavailable")

    if problems:
        if os.environ.get("ALLOW_PARTIAL") == "1":
            print("\nINCOMPLETE FETCH (publishing anyway, ALLOW_PARTIAL=1):")
            for p in problems:
                print(f"  - {p}")
            return
        print("\nERROR: incomplete fetch — refusing to rebuild the site.")
        for p in problems:
            print(f"  - {p}")
        print("\nExisting data was written and cached; the site keeps its "
              "current page. Re-run once the source recovers, or set "
              "ALLOW_PARTIAL=1 to publish a partial update.")
        sys.exit(1)


if __name__ == "__main__":
    main()
