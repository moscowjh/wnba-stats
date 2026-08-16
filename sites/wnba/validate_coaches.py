#!/usr/bin/env python3
"""Layer-2 validation for the hand-maintained coaches file.

Same posture as validate_stats.py: check our data against something that was
NOT the source of it. Here the independent check is arithmetic — a coach's
record with a team must account for exactly the games that team played while
she coached it. A hallucinated or transcribed-wrong W-L almost never satisfies
that, because it has to land on an exact integer derived from schedule length.

Exit non-zero if any row fails, so this can gate a build.
"""
import csv, sys, argparse
from pathlib import Path

# WNBA regular-season games per team per year. This table is the ONE thing here
# that needs human verification; everything else is derived from it. Verify once,
# then it only changes when the league changes the schedule.
SEASON_GAMES = {
    2010: 34, 2011: 34, 2012: 34, 2013: 34, 2014: 34, 2015: 34, 2016: 34,
    2017: 34, 2018: 34, 2019: 34,
    2020: 22,   # bubble season
    2021: 32,   # Olympic break
    2022: 36, 2023: 40, 2024: 40, 2025: 44,
}
CURRENT_SEASON = 2026

def expected_games(first_season: int) -> int:
    """Games the team played from the coach's first season through 2025."""
    return sum(g for y, g in SEASON_GAMES.items() if first_season <= y <= 2025)

def validate(path: Path, verbose: bool = True):
    rows = list(csv.DictReader(path.open()))
    problems, notes = [], []

    if len(rows) != 15:
        problems.append(f"expected 15 teams, found {len(rows)}")

    seen_teams, seen_coaches = set(), set()
    for r in rows:
        team = r["team_abbr"]
        coach = r["coach_name"]
        if team in seen_teams:
            problems.append(f"{team}: duplicate team row")
        seen_teams.add(team)
        if coach in seen_coaches:
            problems.append(f"{team}: {coach} appears twice — a coach cannot lead two teams")
        seen_coaches.add(coach)

        for field in ("coach_name", "first_season_with_team", "source_url", "verified_on"):
            if not r.get(field, "").strip():
                problems.append(f"{team}: empty required field '{field}'")

        try:
            first = int(r["first_season_with_team"])
            w, l = int(r["pre2026_w"]), int(r["pre2026_l"])
        except ValueError:
            problems.append(f"{team}: non-numeric season/record fields")
            continue

        if not (2000 <= first <= CURRENT_SEASON):
            problems.append(f"{team}: implausible first season {first}")
            continue

        if first == CURRENT_SEASON:
            if (w, l) != (0, 0):
                problems.append(
                    f"{team}: {coach} first season is {first}, so the pre-{CURRENT_SEASON} "
                    f"record must be 0-0, got {w}-{l}")
            elif verbose:
                notes.append(f"  OK  {team:3s} {coach:18s} first season {first} -> 0-0")
            continue

        exp = expected_games(first)
        got = w + l
        if got != exp:
            problems.append(
                f"{team}: {coach} ({first}-2025) -> record {w}-{l} sums to {got} games, "
                f"but the team played {exp}. Off by {got - exp:+d}.")
        elif verbose:
            notes.append(f"  OK  {team:3s} {coach:18s} {first}-2025  {w}-{l} = {got} games")

    return problems, notes

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?",
                    default="sites/wnba/reference/wnba_coaches_2026.csv")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    problems, notes = validate(Path(a.path), verbose=not a.quiet)
    for n in notes:
        print(n)
    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f"  !!  {p}")
        sys.exit(1)
    print(f"\nAll rows consistent. ({len(notes)} checked)")
