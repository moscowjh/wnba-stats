#!/usr/bin/env python3
"""validate_leaders.py — the Leaders aggregation, checked against fabricated
box scores.

This repo has no test suite; it has validators that gate CI, and this is one.
It guards `build_wwc_pages.compute_leaders()` and the two ways that function
can be silently, invisibly wrong. Both have precedent in this codebase.

**1. Name is the only player key.** There is no player id in the box-score
format — the join is `(schedule_key, name)` and nothing else. Any spelling
drift between games splits one player into two, and BOTH halves quietly fall
off the board: not an error, not a blank, just a leaderboard missing the
player who should be on it. This is the WNBA `athlete_id` lesson (incident
2026-08-04) arriving on a site with no id to fall back on, and FIBA-adjacent
name churn is not hypothetical — Korea was re-romanised between two Wikipedia
pulls a day apart.

**2. A null is not a zero.** The box-score format's own contract says a stat
the feed does not carry must be null, never 0, so the template renders an em
dash and a null makes the TEAM total null rather than a quiet undercount.
Summing nulls as zero on a *published leaderboard* reproduces exactly that
undercount, one step further from anyone able to spot it.

Neither failure raises anything on its own. Both render a page that looks
completely normal — right columns, plausible numbers, wrong player missing —
which is the same reason `validate_standings.py` exists for the tie-break.

Usage:
    .venv/bin/python sites/wwc/validate_leaders.py
"""

import sys
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE_DIR))

import build_wwc_pages as B  # noqa: E402

# Two real teams, so the roster cross-check runs against the real reference
# file rather than a stub of it — the check is only worth anything if the
# thing it reads is the thing the build reads.
HOME, AWAY = "USA", "AUSTRALIA"


def player(name, **stats):
    """One box-score row. Every stat defaults to 0; pass None for a null."""
    row = {"number": None, "name": name, "position": "G", "starter": True,
           "min": "30:00", "fgm": 0, "fga": 0, "tpm": 0, "tpa": 0,
           "ftm": 0, "fta": 0, "oreb": 0, "tov": 0, "pf": 0,
           "plus_minus": 0, "pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0}
    row.update(stats)
    return row


def game(gid, home_players, away_players=(), status="final", fixture=False):
    box = {"game_id": gid, "status": status, "teams": [
        {"schedule_key": HOME, "score": sum(p["pts"] or 0 for p in home_players),
         "linescore": [0, 0, 0, 0], "players": list(home_players)},
        {"schedule_key": AWAY, "score": sum(p["pts"] or 0 for p in away_players),
         "linescore": [0, 0, 0, 0], "players": list(away_players)}]}
    if fixture:
        box["_fixture"] = True
    return box


def board(lb, title):
    for _, t, _, rows in lb["boards"]:
        if t == title:
            return rows
    raise AssertionError(f"no board titled {title!r}")


def row_for(lb, title, name):
    return next((r for r in board(lb, title) if r["name"] == name), None)


def run(teams):
    """Each case returns a list of failure strings; empty means pass."""
    cases = []

    def case(label):
        def deco(fn):
            cases.append((label, fn))
            return fn
        return deco

    # ── 1. Games played is appearances, and the average divides by it ─────
    @case("a player in 2 of 3 games has GP 2 and averages over 2, not 3")
    def _():
        boxes = [
            game("g1", [player("Ann Absent", pts=10), player("Bea Both", pts=20)]),
            game("g2", [player("Bea Both", pts=10)]),
            game("g3", [player("Cal Solo", pts=30)]),
        ]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        bad = []
        if lb["games"] != 3:
            bad.append(f"games={lb['games']}, expected 3")
        bea = row_for(lb, "Scoring", "Bea Both")
        if bea is None:
            return ["Bea Both is missing from the scoring board entirely"]
        if (bea["gp"], bea["total"], round(bea["avg"], 3)) != (2, 30, 15.0):
            bad.append(f"Bea Both: gp={bea['gp']} tot={bea['total']} "
                       f"avg={bea['avg']:.3f}, expected 2 / 30 / 15.000")
        # The trap this guards: dividing by the number of games in the window
        # rather than by her own appearances would give 30/3 = 10.0 and drop
        # her below Ann Absent, who played once.
        ann = row_for(lb, "Scoring", "Ann Absent")
        if ann and ann["avg"] >= bea["avg"]:
            bad.append("a one-game 10-point player is ranked at or above a "
                       "two-game 15.0 average — GP is being read wrong")
        return bad

    # ── 2. A null excludes from THAT board only ───────────────────────────
    @case("a null steal removes her from Steals and from no other board")
    def _():
        boxes = [
            game("g1", [player("Nel Null", pts=25, stl=None, reb=10)]),
            game("g2", [player("Nel Null", pts=25, stl=4, reb=10)]),
        ]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        bad = []
        if row_for(lb, "Steals", "Nel Null") is not None:
            bad.append("ranked on Steals despite a null in one game — a "
                       "partial sum is being published as a total")
        for title, expect in (("Scoring", 50), ("Rebounds", 20)):
            r = row_for(lb, title, "Nel Null")
            if r is None:
                bad.append(f"dropped from {title} too — a null must cost one "
                           f"board, not all of them")
            elif r["total"] != expect:
                bad.append(f"{title} total {r['total']}, expected {expect}")
        return bad

    @case("summing a null as zero would show up as a wrong total, not a hole")
    def _():
        # The same shape, but with the null in the ONLY game she played: a
        # zero-sum implementation ranks her 0.0 and looks entirely plausible.
        boxes = [game("g1", [player("Vee Void", pts=12, blk=None)])]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        if row_for(lb, "Blocks", "Vee Void") is not None:
            return ["a player whose only block figure is null appears on the "
                    "Blocks board — she is being ranked on a fabricated 0"]
        return []

    # ── 3. The name join, reported ────────────────────────────────────────
    @case("a box-score name absent from the roster is reported, not silent")
    def _():
        boxes = [game("g1", [player("Nota Realplayer", pts=8)])]
        lines = []
        B.compute_leaders(boxes, teams, log=lines.append)
        text = "\n".join(lines)
        if "Nota Realplayer" not in text:
            return ["an unrostered name produced no report line:\n    "
                    + "\n    ".join(lines)]
        return []

    @case("an unrostered name is reported but NOT excluded — a late "
          "replacement must not vanish from the board")
    def _():
        boxes = [game("g1", [player("Nota Realplayer", pts=8)])]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        if row_for(lb, "Scoring", "Nota Realplayer") is None:
            return ["reporting turned into filtering: a real late call-up "
                    "would be silently missing from every board"]
        return []

    @case("a team with no published roster reports once, not once per player")
    def _():
        # All sixteen teams hold roster names TODAY — Nigeria's are a proxy
        # list and Mali's a 22-name pool, but neither is empty — so this
        # branch is unreachable against the current reference file. It is not
        # dead code: `roster.status == "not_announced"` is a real state the
        # emitter renders, Nigeria was in it until 2026-08-25, and a team can
        # re-enter it. So the case is built by emptying one roster rather than
        # by pretending a real team is empty, which is what the first draft of
        # this check did and what made it fail.
        import copy
        stub = copy.deepcopy(teams)
        stub[HOME]["roster"]["players"] = []
        boxes = [game("g1", [player(f"Player {i}", pts=2) for i in range(12)])]
        lines = []
        B.compute_leaders(boxes, stub, log=lines.append)
        noisy = [l for l in lines if "not on the roster" in l and HOME in l]
        if noisy:
            return [f"{len(noisy)} unmatched-name line(s) for a team with no "
                    f"published roster; expected one 'nothing to check' line"]
        if not any("no published roster" in l for l in lines):
            return ["no line at all for a team with no roster — silence reads "
                    "as 'checked and fine'"]
        return []

    # ── 4. The empty state is a page, not an empty table ──────────────────
    @case("zero final box scores renders the empty state and noindex")
    def _():
        lb = B.compute_leaders([], teams, log=lambda *a: None)
        bad = []
        if lb["games"] or any(rows for *_, rows in lb["boards"]):
            bad.append("compute_leaders invented rows from no games")
        html = B.page_leaders(lb, teams, set())
        if "No games have been played yet" not in html:
            bad.append("the empty state text is missing")
        if "<table" in html:
            bad.append("an empty table rendered where prose belongs")
        if 'name="robots" content="noindex' not in html:
            bad.append("the empty page is indexable — a thin page is the one "
                       "kind Google should not be finding")
        return bad

    @case("a populated page drops the noindex and renders tables")
    def _():
        boxes = [game("g1", [player("Bea Both", pts=20)])]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        html = B.page_leaders(lb, teams, set())
        bad = []
        if 'content="noindex' in html:
            bad.append("still noindex with a real board on the page")
        if "<table" not in html:
            bad.append("no table rendered on a populated page")
        if "through 1 game" not in html:
            bad.append("the 'through N games' heading is wrong or missing")
        return bad

    @case("a FINAL box score with no player stats does NOT raise the tab")
    def _():
        # The lag case: FIBA marks a game final before the box score carries
        # player statistics. Gating the nav on games-played rather than on
        # rankable rows would put the tab up over five empty tables — the
        # exact failure the whole Sep-4 delay existed to avoid, arriving by
        # the back door.
        boxes = [{"game_id": "g1", "status": "final", "teams": [
            {"schedule_key": HOME, "score": 0, "linescore": [0, 0, 0, 0],
             "players": []}]}]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        bad = []
        if lb["games"] != 1:
            bad.append("the final box score was not counted at all")
        if lb["populated"]:
            bad.append("reports itself populated with zero rankable rows — "
                       "main() would light the nav tab")
        html = B.page_leaders(lb, teams, set())
        if "<table" in html:
            bad.append("rendered empty tables instead of the empty state")
        if 'content="noindex' not in html:
            bad.append("an empty board is indexable")
        if "No games have been played yet" in html:
            bad.append("claims no games have been played during a tournament "
                       "in progress — correct-or-blank applies to prose too")
        return bad

    @case("an all-null box score is treated the same as an absent one")
    def _():
        # Every stat null: the null rule alone empties all five boards, so
        # the tab must not appear on the strength of the game existing.
        nulls = dict.fromkeys(("pts", "reb", "ast", "stl", "blk"), None)
        boxes = [game("g1", [player("Nel Null", **nulls)])]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        if lb["populated"]:
            return ["a box score of pure nulls counts as a populated board"]
        return []

    # ── 4b. The GP caption says something only when there is something ────
    @case("the GP caption states the ranking basis whenever a board renders")
    def _():
        boxes = [game("g1", [player("Bea Both", pts=20)])]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        html = B.page_leaders(lb, teams, set())
        if "Games played (GP) is shown for every player" not in html:
            return ["a board rendered without saying what it is ranked on"]
        return []

    @case("the elimination clause appears only once GP actually spreads")
    def _():
        # Everyone level: nothing to explain, so nothing is said.
        level = [game("g1", [player("Ann A", pts=20), player("Bea B", pts=10)])]
        lb = B.compute_leaders(level, teams, log=lambda *a: None)
        bad = []
        if "still in the tournament" in B.page_leaders(lb, teams, set()):
            bad.append("explained a spread that does not exist yet")
        # A three-game player against a one-game player: the knockout shape.
        spread = [game("g1", [player("Ann A", pts=20), player("Bea B", pts=30)]),
                  game("g2", [player("Ann A", pts=20)]),
                  game("g3", [player("Ann A", pts=20)])]
        lb = B.compute_leaders(spread, teams, log=lambda *a: None)
        html = B.page_leaders(lb, teams, set())
        if "still in the tournament" not in html:
            bad.append("a 3-vs-1 games-played spread went unexplained — this "
                       "is the finals-day board where a knocked-out player "
                       "tops the average")
        return bad

    # ── 5. What normalises together, and what must not ────────────────────
    @case("two ENCODINGS of one name aggregate to one player")
    def _():
        boxes = [
            game("g1", [player("Bea Both", pts=10)]),   # NBSP
            game("g2", [player("Bea  Both ", pts=10)]),      # doubled + trailing
        ]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        rows = [r for r in board(lb, "Scoring") if "Both" in r["name"]]
        if len(rows) != 1:
            return [f"{len(rows)} records for one player written two ways — "
                    f"a non-breaking space is splitting the board"]
        if rows[0]["gp"] != 2:
            return [f"gp={rows[0]['gp']}, expected 2"]
        return []

    @case("two SPELLINGS of one name do NOT auto-merge")
    def _():
        # The Korea case. No normaliser joins these, and one that tried would
        # eventually merge two different people — which is worse than the
        # split, because a split is reportable and a bad merge is not.
        boxes = [
            game("g1", [player("Kang Yi-seul", pts=10)]),
            game("g2", [player("Kang Lee-seul", pts=10)]),
        ]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        rows = [r for r in board(lb, "Scoring") if "Kang" in r["name"]]
        if len(rows) != 2:
            return [f"{len(rows)} record(s) for two different spellings — "
                    f"names are being merged on similarity, which will "
                    f"eventually merge two real people"]
        return []

    @case("a merged encoding pair is still reported, not absorbed in silence")
    def _():
        boxes = [game("g1", [player("Bea Both", pts=10)]),
                 game("g2", [player("Bea Both", pts=10)])]
        lines = []
        B.compute_leaders(boxes, teams, log=lines.append)
        if not any("spellings merged" in l for l in lines):
            return ["a source drifting in a way we CAN absorb went unreported; "
                    "it will shortly drift in a way we cannot"]
        return []

    # ── 6. Ties, and the fixture refusal ──────────────────────────────────
    @case("tied players share a place: 1, 1, 3 — not 1, 2, 3")
    def _():
        boxes = [game("g1", [player("Ann A", pts=20), player("Bea B", pts=20),
                             player("Cal C", pts=10)])]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        got = [(r["name"], r["place"]) for r in board(lb, "Scoring")]
        if [p for _, p in got] != [1, 1, 3]:
            return [f"places {got}, expected 1, 1, 3 — this is the "
                    f"2026-08-25 leader-tie bug on a second site"]
        return []

    @case("a fixture box score is REFUSED by a normal build, not bannered")
    def _():
        boxes = [game("g1", [player("Bea Both", pts=20)], fixture=True)]
        try:
            B.compute_leaders(boxes, teams, preview=False, log=lambda *a: None)
        except SystemExit:
            return []
        return ["a synthetic box score was summed into a real leaderboard. "
                "A banner is decoration where a build failure belongs: one "
                "fabricated game blends into a board no reader can decompose."]

    @case("the same fixture IS allowed under --preview")
    def _():
        boxes = [game("g1", [player("Bea Both", pts=20)], fixture=True)]
        lb = B.compute_leaders(boxes, teams, preview=True, log=lambda *a: None)
        if not lb["fixture"]:
            return ["the fixture flag did not reach the page, so it would "
                    "render without its synthetic-data banner"]
        return []

    @case("a non-final box score is ignored entirely")
    def _():
        boxes = [game("g1", [player("Bea Both", pts=99)], status="live")]
        lb = B.compute_leaders(boxes, teams, log=lambda *a: None)
        if lb["games"] or row_for(lb, "Scoring", "Bea Both"):
            return ["an in-progress game is being ranked as if it were final"]
        return []

    return cases


def main():
    doc, teams = B.load_teams()
    failures = []
    cases = run(teams)
    for label, fn in cases:
        bad = fn()
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
    print(f"\nOK - {len(cases)}/{len(cases)} leader-aggregation checks pass "
          f"(name join, null handling, empty state, ties, fixture refusal).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
