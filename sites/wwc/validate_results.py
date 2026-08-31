#!/usr/bin/env python3
"""validate_results.py — cross-check our WWC results against ESPN.

WHY THIS EXISTS. Knockout fixtures cannot be joined on team codes: our
schedule rows say TBD until the bracket resolves. `fetch_data.py` therefore
joins them on FIBA's official game number, which is exact — but it is exact
against FIBA, and a join can be self-consistently wrong. The failure that
matters is a REAL SCORE ON THE WRONG FIXTURE, which, in that file's own
words, "reads as correct to everyone."

ESPN is the independent second source that can catch it, because it names
the teams that actually played where FIBA says only "game 29". It is a
genuinely separate pipeline, so it cannot share FIBA's mistakes.

POSTURE: REPORT, NEVER BLOCK — the same contract `validate_stats.py` has on
the WNBA side. This exits 0 even when it finds problems, unless --strict is
passed. A wrong page can be rebuilt tomorrow; a build that refuses to
publish because a third party changed its JSON helps nobody. ESPN being
unreachable is explicitly not a failure of ours.

WHAT IT CHECKS, per game both sources have:
  1. Scores agree, per team.
  2. The date agrees, compared in Berlin local time (our schedule's own
     timezone). A disagreement here is the mis-slot signal: the right teams
     and score attached to a fixture played on another day.
  3. Coverage — games ESPN shows as final that we have no box score for.

WHAT IT CANNOT CHECK. ESPN lists only fixtures whose teams are known, so
before a knockout round resolves there is nothing on its side to compare.
That is a real limit, not a bug: this validator is strongest exactly when
the risk is highest, once knockout games have been played.

Usage:
    .venv/bin/python sites/wwc/validate_results.py
    .venv/bin/python sites/wwc/validate_results.py --json report.json
    .venv/bin/python sites/wwc/validate_results.py --strict   # exit 1 on problems
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SITE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE_DIR))

from sag.adapters import espn  # noqa: E402

from build_wwc_pages import load_schedule, load_teams  # noqa: E402
from fetch_data import BOXSCORES  # noqa: E402

ESPN_LEAGUE = "fiba"
BERLIN = ZoneInfo("Europe/Berlin")

# The tournament window, used only to bound the ESPN query.
WINDOW = "20260904-20260913"


def norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def team_lookup(doc):
    """{normalised name -> our team code}, built from every name we hold.

    Deliberately name-based. ESPN's abbreviations are NOT reliable here:
    on 2026-08-31 Mali was served with `abbreviation: "KOR"`, which is South
    Korea's code, so an abbreviation join would have silently merged two
    different teams' games.
    """
    teams = doc["teams"] if isinstance(doc, dict) and "teams" in doc else doc
    teams = list(teams.values()) if isinstance(teams, dict) else teams
    out = {}
    for t in teams:
        code = t.get("code")
        for cand in ([t.get("name"), t.get("schedule_key")]
                     + list(t.get("name_variants") or [])):
            if cand:
                out[norm(cand)] = code
    return out


def key_to_code(doc):
    teams = doc["teams"] if isinstance(doc, dict) and "teams" in doc else doc
    teams = list(teams.values()) if isinstance(teams, dict) else teams
    return {t["schedule_key"]: t["code"] for t in teams}


def our_games(rows, k2c):
    """Our finished games as {frozenset(codes): {...}}, read from the box
    scores actually on disk — the published artifact, not our intentions."""
    by_id = {r["game_id"]: r for r in rows}
    out = {}
    for path in sorted(BOXSCORES.glob("*.json")):
        try:
            box = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if box.get("_fixture"):
            continue          # the synthetic template, never a real result
        sides = box.get("teams") or []
        if len(sides) != 2:
            continue
        codes = frozenset(k2c.get(s.get("schedule_key")) for s in sides)
        if None in codes or len(codes) != 2:
            continue
        row = by_id.get(box.get("game_id")) or {}
        out[codes] = {
            "game_id": box.get("game_id"),
            "date": row.get("date"),
            "phase": row.get("phase"),
            "scores": {k2c.get(s.get("schedule_key")): s.get("score")
                       for s in sides},
        }
    return out


def check(report):
    doc, _ = load_teams()
    rows = load_schedule()
    lookup, k2c = team_lookup(doc), key_to_code(doc)
    ours = our_games(rows, k2c)

    try:
        payload = espn.fetch_json(espn.scoreboard_url(ESPN_LEAGUE, WINDOW))
        theirs = espn.parse_scoreboard(payload)
    except Exception as e:                       # noqa: BLE001
        # Not our failure, and nothing about our site is known to be wrong.
        report["notes"].append(f"ESPN unreachable, nothing cross-checked: {e}")
        return report

    report["espn_games"] = len(theirs)
    report["our_boxscores"] = len(ours)

    for g in theirs:
        if not g["completed"]:
            continue
        codes = frozenset(lookup.get(norm(t["name"])) for t in g["teams"])
        unknown = [t["name"] for t in g["teams"]
                   if lookup.get(norm(t["name"])) is None]
        if unknown:
            report["notes"].append(
                f"ESPN team name we do not recognise: {unknown} "
                f"({g['name']}). Add it to name_variants if this is one of "
                f"our 16.")
            continue

        mine = ours.get(codes)
        if mine is None:
            report["missing"].append({
                "espn": g["name"], "date_utc": g["date_utc"],
                "detail": "ESPN has this final; we have no box score for it.",
            })
            continue
        report["compared"] += 1

        # 1. Scores.
        theirs_by_code = {lookup[norm(t["name"])]: t["score"]
                          for t in g["teams"]}
        for code, their_score in theirs_by_code.items():
            our_score = mine["scores"].get(code)
            if their_score is not None and our_score != their_score:
                report["problems"].append({
                    "game_id": mine["game_id"], "team": code,
                    "ours": our_score, "espn": their_score,
                    "detail": "score disagreement",
                })

        # 2. Date, in the schedule's own timezone. THE MIS-SLOT SIGNAL: the
        # right teams and the right score sitting on a fixture that ESPN
        # says was played on a different day.
        if mine["date"] and g["date_utc"]:
            try:
                espn_local = (
                    datetime.fromisoformat(g["date_utc"].replace("Z", "+00:00"))
                    .astimezone(BERLIN).date().isoformat())
                if espn_local != mine["date"]:
                    report["problems"].append({
                        "game_id": mine["game_id"], "phase": mine["phase"],
                        "ours": mine["date"], "espn": espn_local,
                        "detail": "DATE MISMATCH — the score may be attached "
                                  "to the wrong fixture",
                    })
            except ValueError:
                pass
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="also write the report here")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if problems were found (default: never)")
    args = ap.parse_args()

    report = {"compared": 0, "problems": [], "missing": [], "notes": [],
              "espn_games": 0, "our_boxscores": 0}
    check(report)

    print(f"ESPN games in window: {report['espn_games']}  |  "
          f"our box scores: {report['our_boxscores']}  |  "
          f"cross-checked: {report['compared']}")
    for n in report["notes"]:
        print(f"  note: {n}")
    for m in report["missing"]:
        print(f"  MISSING: {m['espn']} ({m['date_utc']}) — {m['detail']}")
    for p in report["problems"]:
        print(f"  PROBLEM: {p}")

    if not report["problems"] and not report["missing"]:
        if report["compared"]:
            print(f"OK — {report['compared']} game(s) agree with ESPN.")
        else:
            print("OK — nothing to compare yet (no completed games on both "
                  "sides). This is expected before Sep 4.")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2),
                                   encoding="utf-8")

    # Report, never block — unless explicitly asked.
    return 1 if (args.strict and report["problems"]) else 0


if __name__ == "__main__":
    sys.exit(main())
