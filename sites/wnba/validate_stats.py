#!/usr/bin/env python3
"""Layer 2 external stats verification: diff our computed leader boards against
stats.wnba.com's own API.

Design (see LAYER2-VALIDATION-HANDOFF.md):

- Compares against the **stats API**, never a scraped page — WNBA.com's rendered
  League Leaders page has been observed three days stale while the API was
  current (2026-07-26).
- **Data-through alignment before diffing.** Per-team games played are compared
  first; if the source is behind us, every drift check reports
  ``SKIPPED (source not caught up)`` instead of crying wolf. If WE are behind
  the source, that's a real data-completeness FAIL on our side.
- Six counting boards (Scoring/Rebounds/Assists/Steals/Blocks/Off Reb) have
  live ranked feeds (``PerMode=PerGame``): the API's RANK order is the
  authority for the top-5 set+order check. The percentage boards all return
  HTTP 500 from WNBA's deployment (verified 2026-07-27), so FT%/3PT% — like
  eFG%/TS%/Turnovers, which have no official category at all — are instead
  **recomputed from official Totals** with our qualification rules and
  compared board-to-board (this validates arithmetic and input data at once).
- Values are compared **unrounded** (``PerMode=Totals``; the PerGame response
  rounds to one decimal, far too lossy to reconstruct a percentage from).
- Player identity: WNBA PLAYER_ID is not ESPN athlete_id. Matched by
  normalized name + team (team codes are identical since the 2026-07 official-
  TLA adoption); every successful match is persisted to
  ``player_id_crosswalk.json`` so later runs join exactly by id even if a
  name spelling drifts. An unmatched top-10 player is a WARN, never silent.
- Traded players: both sides now rank the combined season line, labeled with
  the player's current team (``compute_player_season``). Until 2026-08-04 our
  boards split a multi-team player per team and published a partial season — the
  bug this validator caught on Kelsey Plum (ours 60.904 / 12 GP with LAS vs
  the league's 61.616 / 13 GP across LAS+PHX).

Checks per category (thresholds from the handoff):
  top-5 set and order exact ................. FAIL on mismatch
  per-player value within 0.05 (unrounded) .. FAIL
  games-played exact per top-5 player ....... FAIL  (the data-completeness tell)
  top-10 set overlap ........................ WARN only

Usage:
  python sites/wnba/validate_stats.py                    # all categories, human report
  python sites/wnba/validate_stats.py --date 2026-07-22  # our side cut off at a date
  python sites/wnba/validate_stats.py --gate             # broadcast category only; writes
                                              # leaders_ok=true/false to
                                              # $GITHUB_OUTPUT for the Bluesky
                                              # post gate (fail-closed on
                                              # validator bugs, open on source
                                              # unavailability/lag)

Always writes validation_report.json (consumed by the cron-worker health
check, which folds any FAIL into its alert email). Exit code is always 0 in
--gate mode unless the validator itself crashes — the site deploy must never
be blocked by this script (build.yml also sets continue-on-error).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd
import requests

from config import WNBA
import build_stats_page as bsp

# Both stay at the REPO ROOT, not beside this script: workers/cron/worker.js
# fetches validation_report.json from raw.githubusercontent at
# `main/validation_report.json`, and that Worker is not deployed by CI — moving
# it would break the health check silently and need a manual wrangler deploy.
REPORT_PATH = WNBA.validation_report
CROSSWALK_PATH = WNBA.player_crosswalk

LEADERS_URL = "https://stats.wnba.com/stats/leagueleaders"
# stats.wnba.com runs the stats.nba.com stack: it hangs (rather than failing)
# without browser-ish headers, and — the load-bearing detail — without an
# Accept-Encoding that allows gzip. `requests` sends that by default; keep the
# rest explicit.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.wnba.com/",
    "Origin": "https://www.wnba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
SEASON = "2026"
TIMEOUT = (5, 20)          # (connect, read) — this endpoint family hangs
RETRY_SLEEP = 2
FETCH_DELAY = 0.6          # be polite between calls in full-report mode

VALUE_TOL = 0.05           # per-game units / percentage points, unrounded

# Boards with a live ranked PerGame feed: {our category: API StatCategory}.
RANKED_CATS = {
    'Scoring': 'PTS', 'Rebounds': 'REB', 'Assists': 'AST',
    'Steals': 'STL', 'Blocks': 'BLK', 'Off Reb': 'OREB',
}
# Boards recomputed from official Totals (percentage categories 500 on the
# WNBA deployment; eFG%/TS%/Turnovers have no official category at all).
RECOMPUTE_CATS = ['FT%', '3PT%', 'eFG%', 'TS%', 'Turnovers']


class SourceUnavailable(Exception):
    """The stats API could not be reached — not a drift signal."""


# ── Fetch ────────────────────────────────────────────────────────────────

def _get(params: dict) -> dict:
    """GET leagueleaders with explicit timeout and one bounded retry."""
    last = None
    for attempt in range(2):
        try:
            r = requests.get(LEADERS_URL, params=params, headers=HEADERS,
                             timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if attempt == 0:
                time.sleep(RETRY_SLEEP)
    raise SourceUnavailable(f"leagueleaders {params.get('StatCategory')}: {last}")


def _rows_to_df(payload: dict) -> pd.DataFrame:
    rs = payload["resultSet"]
    return pd.DataFrame(rs["rowSet"], columns=rs["headers"])


def fetch_totals() -> pd.DataFrame:
    """One Totals call carries every stat for every player, unfiltered by
    qualification — the single source for unrounded values, GP alignment, and
    the recomputed boards."""
    return _rows_to_df(_get(dict(
        LeagueID="10", PerMode="Totals", Scope="S", Season=SEASON,
        SeasonType="Regular Season", StatCategory="PTS")))


def fetch_ranked(stat_category: str) -> pd.DataFrame:
    """The qualification-filtered, officially ranked PerGame board."""
    return _rows_to_df(_get(dict(
        LeagueID="10", PerMode="PerGame", Scope="S", Season=SEASON,
        SeasonType="Regular Season", StatCategory=stat_category)))


# ── Identity ─────────────────────────────────────────────────────────────

def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s.lower())
    return " ".join(s.split())


def load_crosswalk() -> dict:
    if CROSSWALK_PATH.exists():
        try:
            return json.loads(CROSSWALK_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_crosswalk(xw: dict) -> None:
    CROSSWALK_PATH.write_text(json.dumps(xw, indent=2, sort_keys=True))


def match_players(p_base: pd.DataFrame, totals: pd.DataFrame) -> dict:
    """Return {athlete_id: wnba PLAYER_ID}, persisting successes for later
    exact joins. Match by normalized name + team, then unique name alone.
    Both sides label a multi-team player with her current team, so name + team is
    an exact match for her too; the name-only fallback remains as insurance
    against a team code drifting on one side."""
    xw = load_crosswalk()
    by_name_team = {}
    by_name = {}
    for _, r in totals.iterrows():
        key = (norm_name(r["PLAYER"]), r["TEAM"])
        by_name_team[key] = r["PLAYER_ID"]
        by_name.setdefault(norm_name(r["PLAYER"]), []).append(r["PLAYER_ID"])

    for _, r in p_base.iterrows():
        aid = str(r["athlete_id"])
        if aid in xw:
            continue
        nm = norm_name(r["athlete_display_name"])
        pid = by_name_team.get((nm, r["team_abbreviation"]))
        if pid is None:
            cands = by_name.get(nm, [])
            if len(set(cands)) == 1:
                pid = cands[0]
        if pid is not None:
            xw[aid] = int(pid)
    save_crosswalk(xw)
    return xw


# ── Their boards ─────────────────────────────────────────────────────────

def their_rates(totals: pd.DataFrame) -> pd.DataFrame:
    """Unrounded per-game / percentage values derived from official Totals."""
    t = totals.copy()
    gp = t["GP"].astype(float)
    t["PPG"] = t["PTS"] / gp
    t["RPG"] = t["REB"] / gp
    t["APG"] = t["AST"] / gp
    t["SPG"] = t["STL"] / gp
    t["BPG"] = t["BLK"] / gp
    t["ORPG"] = t["OREB"] / gp
    t["TPG"] = t["TOV"] / gp
    t["FT%"] = (t["FTM"] / t["FTA"] * 100).where(t["FTA"] > 0)
    t["3PT%"] = (t["FG3M"] / t["FG3A"] * 100).where(t["FG3A"] > 0)
    t["eFG%"] = ((t["FGM"] + 0.5 * t["FG3M"]) / t["FGA"] * 100).where(t["FGA"] > 0)
    t["TS%"] = (t["PTS"] / (2 * (t["FGA"] + 0.44 * t["FTA"])) * 100).where(t["FGA"] > 0)
    return t


# Our qualification rules, applied to THEIR totals for the recomputed boards.
# Keep in lockstep with compute_leaders() in build_stats_page.py — including
# the per-team proration of `sc` and `mg` (see their_board_recomputed).
_RECOMPUTE_SPEC = {
    'FT%':       ('FT%',  lambda t, sc, mg: t["FTM"] >= 50 * sc),
    '3PT%':      ('3PT%', lambda t, sc, mg: t["FG3M"] >= 25 * sc),
    'eFG%':      ('eFG%', lambda t, sc, mg: t["FGM"] >= 100 * sc),
    'TS%':       ('TS%',  lambda t, sc, mg: t["FGM"] >= 100 * sc),
    'Turnovers': ('TPG',  lambda t, sc, mg: t["GP"] >= mg),
}

_RANKED_VALUE_COL = {
    'Scoring': 'PPG', 'Rebounds': 'RPG', 'Assists': 'APG',
    'Steals': 'SPG', 'Blocks': 'BPG', 'Off Reb': 'ORPG',
}


def their_board_ranked(ranked: pd.DataFrame, rates: pd.DataFrame,
                       value_col: str) -> pd.DataFrame:
    """Top 10 in the API's own RANK order, with unrounded values joined from
    Totals by PLAYER_ID (exact — no name matching inside their own data)."""
    top = ranked.nsmallest(10, "RANK")[["PLAYER_ID", "PLAYER", "TEAM", "GP"]]
    merged = top.merge(rates[["PLAYER_ID", value_col]], on="PLAYER_ID",
                       how="left")
    return merged.rename(columns={value_col: "value"})


def their_board_recomputed(rates: pd.DataFrame, cat: str,
                           team_games: pd.Series) -> pd.DataFrame:
    """Board built from official Totals under OUR qualification rules.

    Minimums are prorated per team, exactly as compute_leaders does it — a
    league-wide scale would hold Connecticut's players to Seattle's game count.
    `team_games` is our own team-box count, keyed on the official TLAs both
    sides use; a team code we don't recognize falls back to the league max,
    which is the stricter (never over-inclusive) direction."""
    value_col, qualifier = _RECOMPUTE_SPEC[cat]
    tg = rates["TEAM"].map(team_games).fillna(team_games.max())
    scale = tg / 44.0
    min_gp = (0.70 * tg).round().clip(lower=1)
    pool = rates[qualifier(rates, scale, min_gp)].dropna(subset=[value_col])
    top = pool.nlargest(10, value_col)[["PLAYER_ID", "PLAYER", "TEAM", "GP",
                                        value_col]]
    return top.rename(columns={value_col: "value"})


# ── Checks ───────────────────────────────────────────────────────────────

def check_alignment(team_raw: pd.DataFrame, totals: pd.DataFrame) -> dict:
    """Compare per-team games played, ours vs the API's (max player GP per
    team — someone on every roster has played every game)."""
    ours = (team_raw[team_raw["season_type"] == 2]
            .groupby("team_abbreviation")["game_id"].nunique())
    theirs = totals.groupby("TEAM")["GP"].max()
    behind_them, behind_us = [], []
    for team in sorted(set(ours.index) | set(theirs.index)):
        o, t = int(ours.get(team, 0)), int(theirs.get(team, 0))
        if t < o:
            behind_them.append(f"{team} {t}<{o}")
        elif o < t:
            behind_us.append(f"{team} {o}<{t}")
    return {
        "ours_total": int(ours.sum()), "theirs_total": int(theirs.sum()),
        "source_behind": behind_them, "we_are_behind": behind_us,
    }


def compare_category(cat: str, ours: pd.DataFrame, theirs: pd.DataFrame,
                     xw: dict) -> dict:
    """Run the four checks for one category. `ours` is a full-mode board from
    compute_leaders; `theirs` has PLAYER_ID/PLAYER/TEAM/GP/value."""
    checks = []

    def add(name, status, detail=""):
        checks.append({"check": name, "status": status, "detail": detail})

    our5 = ours.head(5)
    their5 = theirs.head(5)

    # Top-5 set AND order (by matched identity; normalized name as fallback).
    our_ids = [xw.get(str(a)) for a in our5["athlete_id"]]
    their_ids = list(their5["PLAYER_ID"])
    if None in our_ids:
        unmatched = [n for n, i in zip(our5["Player"], our_ids) if i is None]
        add("top5_order", "WARN", f"unmatched player(s), name-compare only: {unmatched}")
        ok = [norm_name(n) for n in our5["Player"]] == \
             [norm_name(n) for n in their5["PLAYER"]]
    else:
        ok = our_ids == their_ids
    if ok:
        add("top5_order", "PASS", " / ".join(our5["Player"]))
    else:
        add("top5_order", "FAIL",
            f"ours={list(our5['Player'])} theirs={list(their5['PLAYER'])}")

    # Values within tolerance + GP exact, per our top 5.
    their_by_id = theirs.set_index("PLAYER_ID")
    bad_vals, bad_gp, missing = [], [], []
    for _, r in our5.iterrows():
        pid = xw.get(str(r["athlete_id"]))
        if pid is None or pid not in their_by_id.index:
            missing.append(r["Player"])
            continue
        t = their_by_id.loc[pid]
        if pd.notna(t["value"]) and abs(float(r["_raw"]) - float(t["value"])) > VALUE_TOL:
            bad_vals.append(f"{r['Player']} ours={r['_raw']:.3f} theirs={t['value']:.3f}")
        if int(r["GP"]) != int(t["GP"]):
            bad_gp.append(f"{r['Player']} ours={int(r['GP'])} theirs={int(t['GP'])}")
    add("top5_values", "FAIL" if bad_vals else "PASS", "; ".join(bad_vals))
    add("top5_gp", "FAIL" if bad_gp else "PASS", "; ".join(bad_gp))
    if missing:
        add("top5_match", "WARN", f"not matched in source: {missing}")

    # Top-10 set overlap — WARN only.
    our10 = {norm_name(n) for n in ours["Player"]}
    their10 = {norm_name(n) for n in theirs["PLAYER"]}
    if our10 == their10:
        add("top10_set", "PASS")
    else:
        add("top10_set", "WARN",
            f"only-ours={sorted(our10 - their10)} only-theirs={sorted(their10 - our10)}")

    worst = ("FAIL" if any(c["status"] == "FAIL" for c in checks)
             else "WARN" if any(c["status"] == "WARN" for c in checks)
             else "PASS")
    return {"category": cat, "status": worst, "checks": checks}


# ── Driver ───────────────────────────────────────────────────────────────

def broadcast_category_today() -> str:
    ordinal = dt.datetime.now(dt.timezone.utc).date().toordinal()
    return bsp.BROADCAST_CATS[ordinal % len(bsp.BROADCAST_CATS)]


def run(gate: bool, cutoff: str | None) -> dict:
    player_raw, team_raw, *_ = bsp.load_data()
    if cutoff:
        player_raw = player_raw[player_raw["game_date"] <= cutoff]
        team_raw = team_raw[team_raw["game_date"] <= cutoff]
    # Season-combined lines, same basis the WNBA's own leader feed uses.
    p_base = bsp.compute_player_season(player_raw)
    our_boards = bsp.compute_leaders(p_base, full=True)
    # Games each team has played — the per-team basis for qualification
    # minimums on both sides of the diff.
    team_games = team_raw.groupby("team_abbreviation")["game_id"].nunique()

    cats = ([broadcast_category_today()] if gate
            else list(RANKED_CATS) + RECOMPUTE_CATS)

    report = {
        "run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "data_through": str(pd.to_datetime(player_raw["game_date"]).max().date()),
        "cutoff": cutoff,
        "broadcast_category": broadcast_category_today(),
        "gate_mode": gate,
        "categories": [],
    }

    try:
        totals = fetch_totals()
    except SourceUnavailable as e:
        report["alignment"] = {"error": str(e)}
        for cat in cats:
            report["categories"].append(
                {"category": cat, "status": "SKIPPED",
                 "checks": [{"check": "source", "status": "SKIPPED",
                             "detail": f"source unavailable: {e}"}]})
        report["leaders_ok"] = True   # can't verify ≠ wrong; Layer 1 already ran
        return report

    rates = their_rates(totals)
    align = check_alignment(team_raw, totals)
    report["alignment"] = align

    if align["we_are_behind"] and not cutoff:
        # OUR data is missing games the league has — a real problem on our side.
        report["categories"].append(
            {"category": "(all)", "status": "FAIL",
             "checks": [{"check": "data_completeness", "status": "FAIL",
                         "detail": "our data is behind the source: "
                                   + ", ".join(align["we_are_behind"])}]})
        report["leaders_ok"] = False
        return report

    if align["source_behind"]:
        for cat in cats:
            report["categories"].append(
                {"category": cat, "status": "SKIPPED",
                 "checks": [{"check": "alignment", "status": "SKIPPED",
                             "detail": "source not caught up: "
                                       + ", ".join(align["source_behind"])}]})
        report["leaders_ok"] = True
        return report

    xw = match_players(p_base, totals)

    for cat in cats:
        try:
            if cat in RANKED_CATS:
                ranked = fetch_ranked(RANKED_CATS[cat])
                theirs = their_board_ranked(ranked, rates, _RANKED_VALUE_COL[cat])
                if not gate:
                    time.sleep(FETCH_DELAY)
            else:
                theirs = their_board_recomputed(rates, cat, team_games)
            report["categories"].append(
                compare_category(cat, our_boards[cat], theirs, xw))
        except SourceUnavailable as e:
            report["categories"].append(
                {"category": cat, "status": "SKIPPED",
                 "checks": [{"check": "source", "status": "SKIPPED",
                             "detail": str(e)}]})

    bc = report["broadcast_category"]
    bc_results = [c for c in report["categories"]
                  if c["category"] in (bc, "(all)")]
    report["leaders_ok"] = all(c["status"] != "FAIL" for c in bc_results)
    return report


def print_report(report: dict) -> None:
    print(f"Layer-2 validation — data through {report['data_through']}"
          + (f" (cutoff {report['cutoff']})" if report.get("cutoff") else ""))
    a = report.get("alignment", {})
    if "error" not in a and a:
        print(f"alignment: ours={a['ours_total']} team-games,"
              f" theirs={a['theirs_total']};"
              f" source_behind={a['source_behind'] or 'no'};"
              f" we_behind={a['we_are_behind'] or 'no'}")
    for c in report["categories"]:
        print(f"\n[{c['status']:>7}] {c['category']}")
        for chk in c["checks"]:
            line = f"    {chk['status']:<7} {chk['check']}"
            if chk.get("detail"):
                line += f" — {chk['detail']}"
            print(line)
    n = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIPPED": 0}
    for c in report["categories"]:
        n[c["status"]] += 1
    print(f"\nsummary: {n['PASS']} pass, {n['WARN']} warn, {n['FAIL']} fail,"
          f" {n['SKIPPED']} skipped"
          f"  |  broadcast category '{report['broadcast_category']}'"
          f" leaders_ok={report['leaders_ok']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gate", "--broadcast-category-gate", action="store_true",
                    dest="gate",
                    help="check only today's broadcast category and emit "
                         "leaders_ok to $GITHUB_OUTPUT")
    ap.add_argument("--date", dest="cutoff", default=None,
                    help="cut OUR data off at this date (YYYY-MM-DD) for "
                         "spot-checks; only meaningful while the source is "
                         "also frozen at that date")
    args = ap.parse_args()

    def emit_gate(ok: bool) -> None:
        gh = os.environ.get("GITHUB_OUTPUT")
        if gh:
            with open(gh, "a") as fh:
                fh.write(f"leaders_ok={'true' if ok else 'false'}\n")

    try:
        report = run(args.gate, args.cutoff)
    except Exception:
        # A validator bug fails CLOSED for the broadcast (don't publish what we
        # couldn't verify) but must never block the site deploy — build.yml
        # runs this step with continue-on-error.
        emit_gate(False)
        REPORT_PATH.write_text(json.dumps(
            {"run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "error": "validator exception — see build log",
             "leaders_ok": False}, indent=2))
        raise

    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print_report(report)
    print(f"\nwrote {REPORT_PATH.name}")
    emit_gate(bool(report["leaders_ok"]))


if __name__ == "__main__":
    main()
