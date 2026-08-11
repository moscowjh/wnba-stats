#!/usr/bin/env python3
"""One-off verification: do ESPN's OFFICIAL per-quarter line scores agree with an
INDEPENDENT derivation (running-max of cumulative play-by-play) for every
completed game this season?

Why two sources: the shipped site renders quarters from ESPN's official
`linescores` (see fetch_data.parse_linescores). This script cross-checks those
against a from-scratch reconstruction off the PBP CSV. Agreement on both, for
all games, is strong evidence the official source is correct AND complete.

Reads the season PBP + team-box CSVs already on disk, and fetches each game's
official line score live from ESPN. Run in an environment with real ESPN access
(the Cowork sandbox mirror returns frozen pre-game data and won't work).

    python sites/wnba/validate_linescores.py

Exit code 0 if everything reconciles, 1 if any mismatch/missing is found.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

import fetch_data as fd  # reuse espn_get + parse_linescores + ESPN_SUMMARY
from config import WNBA

PBP_CSV = WNBA.pbp
TEAM_CSV = WNBA.team_box


def pbp_runmax_quarters(g: pd.DataFrame) -> tuple[list[int], list[int]]:
    """Independent derivation: end-of-period cumulative = running max of the
    cumulative score through that period (robust to stale end-of-period rows,
    unlike taking the last play by game_play_number). Returns (home, away)."""
    prev_h = prev_a = run_h = run_a = 0
    lh: list[int] = []
    la: list[int] = []
    for p in sorted(g["period_number"].dropna().unique()):
        seg = g[g["period_number"] == p]
        run_h = max(run_h, int(pd.to_numeric(seg["home_score"], errors="coerce").max()))
        run_a = max(run_a, int(pd.to_numeric(seg["away_score"], errors="coerce").max()))
        lh.append(run_h - prev_h)
        la.append(run_a - prev_a)
        prev_h, prev_a = run_h, run_a
    return lh, la


def main() -> None:
    pbp = pd.read_csv(PBP_CSV, low_memory=False)
    team = pd.read_csv(TEAM_CSV)
    # game_id -> {abbr: final_score}
    finals: dict[int, dict[str, int]] = {}
    for _, r in team.iterrows():
        finals.setdefault(int(r["game_id"]), {})[r["team_abbreviation"]] = int(r["team_score"])

    game_ids = sorted(pbp["game_id"].unique())
    print(f"Verifying {len(game_ids)} games...\n")

    agree = 0
    mismatches: list[str] = []
    missing_official: list[int] = []
    bad_sum: list[str] = []

    for gid in game_ids:
        g = pbp[pbp["game_id"] == gid]
        try:
            summary = fd.espn_get(fd.ESPN_SUMMARY, {"event": str(gid)})
            official = fd.parse_linescores(summary, int(gid))
        except Exception as e:
            print(f"  {gid}: fetch error ({e})")
            missing_official.append(int(gid))
            continue

        if not official:
            missing_official.append(int(gid))
            continue

        o_h, o_a = official["home"], official["away"]
        p_h, p_a = pbp_runmax_quarters(g)

        # (1) official reconciles to the final team totals
        fin = finals.get(int(gid), {})
        if fin:
            if sum(o_h) != fin.get(official["home_abbr"]) or sum(o_a) != fin.get(official["away_abbr"]):
                bad_sum.append(
                    f"{gid} {official['away_abbr']}@{official['home_abbr']}: "
                    f"official sums {sum(o_a)}/{sum(o_h)} vs finals "
                    f"{fin.get(official['away_abbr'])}/{fin.get(official['home_abbr'])}"
                )

        # (2) official agrees with the independent PBP derivation
        if o_h == p_h and o_a == p_a:
            agree += 1
        else:
            mismatches.append(
                f"{gid} {official['away_abbr']}@{official['home_abbr']}:\n"
                f"      official  away{o_a} home{o_h}\n"
                f"      pbp-runmax away{p_a} home{p_h}"
            )

    print(f"Official == independent PBP derivation: {agree}/{len(game_ids)}")
    print(f"Games missing official linescores:      {len(missing_official)}")
    print(f"Official quarters not summing to total: {len(bad_sum)}")

    if mismatches:
        print("\n--- MISMATCHES (official vs PBP derivation) ---")
        for m in mismatches:
            print("  " + m)
    if bad_sum:
        print("\n--- OFFICIAL DOESN'T SUM TO FINAL ---")
        for b in bad_sum:
            print("  " + b)
    if missing_official:
        print(f"\n--- MISSING OFFICIAL LINESCORES: {missing_official}")

    ok = not mismatches and not bad_sum and not missing_official
    print("\n" + ("ALL GAMES RECONCILE ✔" if ok else "DISCREPANCIES FOUND — see above"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
