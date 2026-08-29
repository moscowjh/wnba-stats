#!/usr/bin/env python3
"""validate_standings.py — the group-table ranking, checked against every
worked example in FIBA's rulebook.

This repo has no test suite; it has validators that gate CI, and this is one.
It guards `build_wwc_pages.classify()`, which implements the most error-prone
rule on the site: **Official Basketball Rules 2024, Appendix D —
Classification of Teams**, D.1.1–D.1.4.

Source of truth: `wbb-lab/fiba/FIBA-group-tiebreak.pdf`, the OBR **2024** text
— FIBA's own document, kept in the private lab repo rather than this public
one. Nothing here reads it; the rules are encoded below.
The edition matters. Berlin is played under OBR 2024; OBR 2026 takes effect
1 October, after the tournament. The two are substantively identical for
D.1–D.2 (verified 2026-08-26), but the examples below are transcribed from
the edition that actually governs.

Why this earns a file of its own:

- **The rule is a recursion, not a sort.** D.1.3 re-ranks tied teams using
  only the games among them, and D.1.4 restarts the whole procedure each time
  a team is separated out — so each surviving tie forms a *fresh* sub-group
  from its own members' games.
- **A composite sort key looks correct and is not.** The first implementation
  used one. It passed Examples 1-4 and 7 and failed 5 and 6, both six-team
  groups. In Example 5 the four-team sub-group separates {A,B} from {C,D} on
  record; C and D must then be compared on the single C-D game, which D won,
  while their four-team point differences (-5 vs -45) rank C first. The
  rulebook says D. **Had only the three-team examples been on hand, that bug
  would have shipped.**
- **The failure is invisible.** A wrong tie-break renders a table that looks
  entirely normal — right records, right totals, wrong order — and a table
  reads as more authoritative than a sentence, not less.

The assertion is stronger than final order alone: every W / L / classification
points / PF / PA / difference figure is compared against the rulebook's own
printed tables, so a right answer reached by wrong arithmetic still fails.

Usage:
    .venv/bin/python sites/wwc/validate_standings.py
"""

import sys
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE_DIR))

import build_wwc_pages as B  # noqa: E402

# Each entry: (label, games, expected final order, expected top-level rows).
# `games` is (home, home_score, away, away_score) exactly as the rulebook
# prints them. `rows` is {team: (w, l, pts, pf, pa, diff)} from the FIRST
# table of each example — the whole-group standings before any sub-group.
EXAMPLES = [
    (
        "D.2.1 Example 1 — two independent two-way ties, each on head-to-head",
        [("A", 100, "B", 55), ("A", 90, "C", 85), ("A", 75, "D", 80),
         ("B", 100, "C", 95), ("B", 80, "D", 75), ("C", 60, "D", 55)],
        "ABCD",
        {"A": (2, 1, 5, 265, 220, +45), "B": (2, 1, 5, 235, 270, -35),
         "C": (1, 2, 4, 240, 245, -5), "D": (1, 2, 4, 210, 215, -5)},
    ),
    (
        "D.2.2 Example 2 — A clears, three-way sub-group decided on difference",
        [("A", 100, "B", 55), ("A", 90, "C", 85), ("A", 120, "D", 75),
         ("B", 100, "C", 85), ("B", 75, "D", 80), ("C", 65, "D", 55)],
        "ABCD",
        {"A": (3, 0, 6, 310, 215, +95), "B": (1, 2, 4, 230, 265, -35),
         "C": (1, 2, 4, 235, 245, -10), "D": (1, 2, 4, 210, 260, -50)},
    ),
    (
        "D.2.3 Example 3 — B beat C head-to-head and still finishes BELOW C",
        [("A", 85, "B", 90), ("A", 55, "C", 100), ("A", 75, "D", 120),
         ("B", 100, "C", 95), ("B", 75, "D", 85), ("C", 65, "D", 55)],
        "CDBA",
        {"A": (0, 3, 3, 215, 310, -95), "B": (2, 1, 5, 265, 265, 0),
         "C": (2, 1, 5, 260, 210, +50), "D": (2, 1, 5, 260, 215, +45)},
    ),
    (
        "D.2.4 Example 4 — sub-group difference all zero, decided on points scored",
        [("A", 85, "B", 90), ("A", 55, "C", 100), ("A", 75, "D", 120),
         ("B", 100, "C", 90), ("B", 75, "D", 85), ("C", 65, "D", 55)],
        "BCDA",
        {"A": (0, 3, 3, 215, 310, -95), "B": (2, 1, 5, 265, 260, +5),
         "C": (2, 1, 5, 255, 210, +45), "D": (2, 1, 5, 260, 215, +45)},
    ),
    (
        "D.2.5 Example 5 — SIX teams; four-way tie splits, then C vs D on their "
        "own game (the case a sort key gets backwards)",
        [("A", 100, "B", 55), ("A", 85, "C", 90), ("A", 120, "D", 75),
         ("A", 80, "E", 100), ("A", 85, "F", 80),
         ("B", 100, "C", 95), ("B", 80, "D", 75), ("B", 75, "E", 80),
         ("B", 110, "F", 90),
         ("C", 55, "D", 60), ("C", 90, "E", 75), ("C", 105, "F", 75),
         ("D", 70, "E", 45), ("D", 65, "F", 60), ("E", 75, "F", 80)],
        "ABDCEF",
        {"A": (3, 2, 8, 470, 400, +70), "B": (3, 2, 8, 420, 440, -20),
         "C": (3, 2, 8, 435, 395, +40), "D": (3, 2, 8, 345, 360, -15),
         "E": (2, 3, 7, 375, 395, -20), "F": (1, 4, 6, 385, 440, -55)},
    ),
    (
        "D.2.6 Example 6 — SIX teams, three-level cascade: F out, then C and A "
        "clear, then B/D/E re-tied and restarted",
        [("A", 71, "B", 65), ("A", 85, "C", 86), ("A", 77, "D", 75),
         ("A", 80, "E", 86), ("A", 85, "F", 80),
         ("B", 88, "C", 87), ("B", 80, "D", 75), ("B", 75, "E", 76),
         ("B", 95, "F", 90),
         ("C", 95, "D", 100), ("C", 82, "E", 75), ("C", 105, "F", 75),
         ("D", 68, "E", 67), ("D", 65, "F", 60), ("E", 80, "F", 75)],
        "CABEDF",
        {"A": (3, 2, 8, 398, 392, +6), "B": (3, 2, 8, 403, 399, +4),
         "C": (3, 2, 8, 455, 423, +32), "D": (3, 2, 8, 383, 379, +4),
         "E": (3, 2, 8, 384, 380, +4), "F": (0, 5, 5, 380, 430, -50)},
    ),
    (
        "D.2.7 Example 7 — SIX teams; the surviving tie sits in the MIDDLE of "
        "the order, and resolves on points scored then head-to-head",
        [("A", 73, "B", 71), ("A", 85, "C", 86), ("A", 77, "D", 75),
         ("A", 90, "E", 96), ("A", 85, "F", 80),
         ("B", 88, "C", 87), ("B", 80, "D", 79), ("B", 79, "E", 80),
         ("B", 95, "F", 90),
         ("C", 95, "D", 96), ("C", 82, "E", 75), ("C", 105, "F", 75),
         ("D", 68, "E", 67), ("D", 80, "F", 75), ("E", 80, "F", 75)],
        "CBDEAF",
        {"A": (3, 2, 8, 410, 408, +2), "B": (3, 2, 8, 413, 409, +4),
         "C": (3, 2, 8, 455, 419, +36), "D": (3, 2, 8, 398, 394, +4),
         "E": (3, 2, 8, 398, 394, +4), "F": (0, 5, 5, 395, 445, -50)},
    ),
]


def synthetic_doc(n):
    """A doc shaped like `wwc2026_teams.json` with n teams named A..F.

    The ranking code is deliberately given placeholder teams rather than the
    real sixteen: the rulebook's examples run to six-team groups, which no
    real WWC group is, and the logic must not care either way.
    """
    return {"teams": [{"schedule_key": c, "name": c, "code": c, "flag": "",
                       "group": "X"} for c in "ABCDEF"[:n]]}


def main():
    failures = []
    for label, games, expected_order, expected_rows in EXAMPLES:
        n = len(expected_rows)
        doc = synthetic_doc(n)
        results = {
            f"g{i}": {"status": "final", "teams": [a, b], "score": [sa, sb]}
            for i, (a, sa, b, sb) in enumerate(games)
        }
        table = B.compute_standings(doc, "X", results)
        order = "".join(r["team"]["schedule_key"] for r in table)

        bad = []
        if order != expected_order:
            bad.append(f"order {order}, expected {expected_order}")
        for r in table:
            c = r["team"]["schedule_key"]
            got = (r["w"], r["l"], r["pts"], r["pf"], r["pa"], r["diff"])
            if got != expected_rows[c]:
                bad.append(f"team {c}: {got}, expected {expected_rows[c]}")

        if bad:
            failures.append((label, bad))
            print(f"  FAIL  {label}")
        else:
            print(f"  PASS  {label}")

    if failures:
        print("\nFAILED:")
        for label, bad in failures:
            print(f"  {label}")
            for b in bad:
                print(f"      {b}")
        return 1
    print(f"\nOK - {len(EXAMPLES)}/{len(EXAMPLES)} FIBA Appendix D examples "
          f"reproduce exactly (final order and every column).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
