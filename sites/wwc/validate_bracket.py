#!/usr/bin/env python3
"""validate_bracket.py — knockout slot resolution, checked against a whole
fabricated tournament.

This repo has no test suite; it has validators that gate CI, and this is the
fourth. It guards `build_wwc_pages.resolve_slots()`, which turns the schedule's
bracket rules (`2nd A`, `W27`, `L33`) into real teams.

Why this earns a file of its own — the same argument as `validate_standings.py`,
one league further on:

- **A wrong resolution is invisible.** A quarter-final that says *Spain vs
  France* when it should say *Spain vs Belgium* renders exactly like a right
  one. There is no blank, no dash, no error — just a confident wrong answer on
  the most-read page of the site during the week it is most read.
- **It cannot be tested when it matters.** Every input arrives on the day it is
  needed: the first placing token cannot resolve until Group A's last game is
  final on Sep 7, and the first `W` token not until Sep 8. Waiting for real data
  means debugging the bracket against the games it exists to protect. So the
  tournament is fabricated and played out here, in full, offline.
- **Two of the three failure modes are ordering, not logic**, and ordering bugs
  survive review: a bracket resolved off a provisional group table, and a score
  that renders against the wrong side of its own fixture.

Everything is built in-script. No network, no `results.json`, no box scores —
it reads only the committed schedule CSV and team reference data, so it runs
identically in CI and on a laptop in December.

## The fabricated tournament

Group results are chosen so every group finishes **3-0 / 2-1 / 1-2 / 0-3 in
alphabetical order of `schedule_key`**. That is deliberate: distinct records
mean no tie-break is ever exercised here, so a failure in this file is a
failure in the bracket and not in `classify()` — which has its own validator
and FIBA's own worked examples behind it.

Every expected pairing below is therefore derivable by hand from two things:
the alphabetical rule above, and the `matchup_rule` column of
`reference/wwc_schedule_2026.csv`. They were written that way — read off the
CSV, not off this code's output.

Usage:
    .venv/bin/python sites/wwc/validate_bracket.py
"""

import sys
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE_DIR))

import build_wwc_pages as B  # noqa: E402

# ── The fabricated tournament ─────────────────────────────────────────────

#: Finishing order per group, 1st -> 4th. Alphabetical by `schedule_key`, and
#: produced below by a clean 3-0 / 2-1 / 1-2 / 0-3, so no tie-break applies.
PLACINGS = {
    "A": ["GERMANY", "JAPAN", "MALI", "SPAIN"],
    "B": ["FRANCE", "HUNGARY", "KOREA", "NIGERIA"],
    "C": ["AUSTRALIA", "BELGIUM", "PUERTO RICO", "TÜRKIYE"],
    "D": ["CHINA", "CZECHIA", "ITALY", "USA"],
}

#: Knockout outcomes we impose, `game_id -> winning side index`.
#: `qqf-28` is a side-1 win on purpose: a table where side 0 always wins would
#: pass with `won` hardcoded to 0.
KO_WINNER = {
    "qqf-25": 0, "qqf-26": 0, "qqf-27": 0, "qqf-28": 1,
    "qf-29": 0, "qf-30": 0, "qf-31": 0, "qf-32": 0,
    "sf-33": 0, "sf-34": 0, "third-place": 0, "final": 0,
}

#: Games whose result is stored with `teams` REVERSED against the bracket's own
#: side order. Not a quirk of the fixture — it is the live case. `orient()`
#: re-orders group results to our row but leaves knockout results in FIBA's
#: order, because our knockout rows say TBD and there is nothing to orient to.
#: So the resolved sides must follow the RESULT, and `qf-31` is here to prove
#: they do. Resolve it from the rule instead and every knockout final on the
#: site renders backwards.
REVERSED_IN_FIBA_ORDER = {"qf-31"}

#: What the BRACKET RULE alone would give for each reversed game, hand-derived
#: from the CSV: `qf-31` is `1st C - W25`, so AUSTRALIA then JAPAN. The stored
#: fixture is its reverse. Keeping both means the orientation check can prove
#: it is looking at a genuinely reordered result rather than passing on one
#: that happened to agree.
REVERSED_RULE_ORDER = {"qf-31": ("AUSTRALIA", "JAPAN")}

#: Expected pairings, in the order the page shows them. Transcribed BY HAND
#: from `matchup_rule` in the schedule CSV plus PLACINGS above — never pasted
#: from this code's output. `qf-31` is listed in its stored (reversed) order,
#: which is what the page must render.
EXPECTED = {
    "qqf-25": ("JAPAN", "KOREA"),                  # 2nd A - 3rd B
    "qqf-26": ("HUNGARY", "MALI"),                 # 2nd B - 3rd A
    "qqf-27": ("PUERTO RICO", "CZECHIA"),          # 3rd C - 2nd D
    "qqf-28": ("ITALY", "BELGIUM"),                # 3rd D - 2nd C
    "qf-29": ("GERMANY", "PUERTO RICO"),           # 1st A - W27
    "qf-30": ("FRANCE", "BELGIUM"),                # 1st B - W28
    "qf-31": ("JAPAN", "AUSTRALIA"),               # 1st C - W25, stored reversed
    "qf-32": ("CHINA", "HUNGARY"),                 # 1st D - W26
    "sf-33": ("GERMANY", "CHINA"),                 # W29 - W32
    "sf-34": ("FRANCE", "AUSTRALIA"),              # W30 - W31
    "third-place": ("CHINA", "AUSTRALIA"),         # L33 - L34
    "final": ("GERMANY", "FRANCE"),                # W33 - W34
}

#: The rule text a reader sees on an unresolved side. Locks `_RULE_WORDS`,
#: whose off-by-one-round ("W29 is a QUARTER-FINAL winner") was already got
#: wrong once. Sides are in DISPLAY order — group winner first — so this table
#: also pins the swap that puts `1st A` ahead of `W27`.
EXPECTED_SIDE_TEXT = {
    "qqf-25": ("2nd Grp A", "3rd Grp B"),
    "qqf-26": ("2nd Grp B", "3rd Grp A"),
    "qqf-27": ("3rd Grp C", "2nd Grp D"),
    "qqf-28": ("3rd Grp D", "2nd Grp C"),
    "qf-29": ("1st Grp A", "QF Qual winner"),
    "qf-30": ("1st Grp B", "QF Qual winner"),
    "qf-31": ("1st Grp C", "QF Qual winner"),
    "qf-32": ("1st Grp D", "QF Qual winner"),
    "sf-33": ("QF winner", "QF winner"),
    "sf-34": ("QF winner", "QF winner"),
    "third-place": ("SF loser", "SF loser"),
    "final": ("SF winner", "SF winner"),
}

#: One line per team whose tournament has ended, once everything is played.
EXPECTED_STATUS = {
    "SPAIN": "Eliminated — 4th in Group A",
    "NIGERIA": "Eliminated — 4th in Group B",
    "TÜRKIYE": "Eliminated — 4th in Group C",
    "USA": "Eliminated — 4th in Group D",
    "KOREA": "Eliminated — lost the qualification play-off",
    "MALI": "Eliminated — lost the qualification play-off",
    "CZECHIA": "Eliminated — lost the qualification play-off",
    "ITALY": "Eliminated — lost the qualification play-off",
    "PUERTO RICO": "Eliminated — lost the quarter-final",
    "BELGIUM": "Eliminated — lost the quarter-final",
    "JAPAN": "Eliminated — lost the quarter-final",
    "HUNGARY": "Eliminated — lost the quarter-final",
    "CHINA": "3rd place",
    "AUSTRALIA": "4th place",
    "GERMANY": "Champion",
    "FRANCE": "Runner-up",
}


def group_results(rows, groups="ABCD"):
    """Final results for every game in `groups`, built to yield PLACINGS.

    The better-placed team wins by ten. Stored in OUR row order, which is what
    `orient()` guarantees for group rows.
    """
    out = {}
    for r in rows:
        if not r["group"] or r["group"] not in groups:
            continue
        order = PLACINGS[r["group"]]
        a, b = r["team_1"], r["team_2"]
        sa, sb = (80, 70) if order.index(a) < order.index(b) else (70, 80)
        out[r["game_id"]] = {"status": "final", "teams": [a, b],
                             "score": [sa, sb]}
    return out


def play(rows, doc, results, game_ids):
    """Resolve `game_ids`, then record the result KO_WINNER dictates.

    Plays the tournament the way it is really played — one round at a time,
    each round resolved from the rounds already final — so a resolution that
    only works when the whole bracket is present cannot pass.
    """
    slots = B.resolve_slots(rows, doc, results)
    for gid in game_ids:
        sides = list(slots[gid])
        assert None not in sides, f"{gid} did not resolve: {sides}"
        won = KO_WINNER[gid]
        score = [80, 70] if won == 0 else [70, 80]
        if gid in REVERSED_IN_FIBA_ORDER:
            sides, score = sides[::-1], score[::-1]
        results[gid] = {"status": "final", "teams": sides, "score": score}
    return results


def full_tournament(rows, doc):
    """All 36 games final."""
    results = group_results(rows)
    play(rows, doc, results, ["qqf-25", "qqf-26", "qqf-27", "qqf-28"])
    play(rows, doc, results, ["qf-29", "qf-30", "qf-31", "qf-32"])
    play(rows, doc, results, ["sf-33", "sf-34"])
    play(rows, doc, results, ["third-place", "final"])
    return results


# ── Checks ────────────────────────────────────────────────────────────────

def check(label, fn, failures):
    bad = fn()
    if bad:
        failures.append((label, bad))
        print(f"  FAIL  {label}")
    else:
        print(f"  PASS  {label}")


def main():
    rows = B.load_schedule()
    doc, _teams = B.load_teams()
    ko_rows = [r for r in rows if not r["group"]]
    failures = []

    # 1 — the whole bracket, 16 -> 8 -> 4 -> 2 -> 1.
    def whole():
        results = full_tournament(rows, doc)
        slots = B.resolve_slots(rows, doc, results)
        bad = []
        for gid, want in EXPECTED.items():
            if slots.get(gid) != want:
                bad.append(f"{gid}: {slots.get(gid)}, expected {want}")
        if len(slots) != 12:
            bad.append(f"{len(slots)} knockout rows resolved, expected 12")
        return bad
    check("full tournament — all 12 knockout rows resolve to the right teams",
          whole, failures)

    # 2 — a score must stay parallel to the teams beside it.
    def orientation():
        results = full_tournament(rows, doc)
        slots = B.resolve_slots(rows, doc, results)
        bad = []
        for gid in ko_rows and [r["game_id"] for r in ko_rows]:
            res = results[gid]
            if slots[gid] != tuple(res["teams"]):
                bad.append(f"{gid}: sides {slots[gid]} do not follow the "
                           f"result's team order {res['teams']}")
        # And the one that is stored backwards really is backwards, or this
        # check is passing on a fixture that never exercised it.
        for gid, rule_order in REVERSED_RULE_ORDER.items():
            stored = tuple(results[gid]["teams"])
            if stored != rule_order[::-1]:
                bad.append(f"{gid} fixture is not stored reversed against its "
                           f"rule order {rule_order} — this check has gone "
                           f"vacuous")
            # The winner must survive the reordering: same team either way.
            score = results[gid]["score"]
            if stored[score.index(max(score))] != "AUSTRALIA":
                bad.append(f"{gid}: reordering changed who won")
        return bad
    check("a played knockout takes its sides from the RESULT, not the rule",
          orientation, failures)

    # 3 — negative: a half-played group resolves NOTHING. Not provisionally.
    def half_group():
        results = group_results(rows, "A")
        played = [r["game_id"] for r in rows if r["group"] == "A"][:3]
        results = {k: v for k, v in results.items() if k in played}
        slots = B.resolve_slots(rows, doc, results)
        return [f"{gid}: {pair}" for gid, pair in slots.items()
                if pair != (None, None)]
    check("half-complete group resolves nothing (not provisionally)",
          half_group, failures)

    # 4 — negative: a group completing resolves ITS placings and no others.
    def only_group_a():
        results = group_results(rows, "A")
        slots = B.resolve_slots(rows, doc, results)
        want = {"qqf-25": ("JAPAN", None),      # 2nd A - 3rd B
                "qqf-26": (None, "MALI"),       # 2nd B - 3rd A
                "qf-29": ("GERMANY", None)}     # 1st A - W27
        bad = [f"{gid}: {slots.get(gid)}, expected {pair}"
               for gid, pair in want.items() if slots.get(gid) != pair]
        resolved = {k for k, v in slots.items() if v != (None, None)}
        if resolved != set(want):
            bad.append(f"rows touched: {sorted(resolved)}, "
                       f"expected {sorted(want)}")
        # No team from another group may appear anywhere.
        others = {t["schedule_key"] for t in doc["teams"] if t["group"] != "A"}
        for gid, pair in slots.items():
            for k in pair:
                if k in others:
                    bad.append(f"{gid} resolved {k}, which is not in Group A")
        return bad
    check("one complete group resolves exactly its own four placings",
          only_group_a, failures)

    # 5 — negative: the quarter-finals resolve while the semi-finals do not.
    # The Sep 10 morning state, and the one the untimed-semis guard was about.
    def qf_not_sf():
        results = group_results(rows)
        play(rows, doc, results, ["qqf-25", "qqf-26", "qqf-27", "qqf-28"])
        slots = B.resolve_slots(rows, doc, results)
        bad = []
        for gid in ("qf-29", "qf-30", "qf-31", "qf-32"):
            if None in slots[gid]:
                bad.append(f"{gid} should be fully resolved, got {slots[gid]}")
        for gid in ("sf-33", "sf-34", "third-place", "final"):
            if slots[gid] != (None, None):
                bad.append(f"{gid} must not resolve yet, got {slots[gid]}")
        return bad
    check("quarter-finals resolve while the unplayed semi-finals do not",
          qf_not_sf, failures)

    # 6 — the words on an unresolved side.
    def side_text():
        bad = []
        for r in ko_rows:
            sides = B.rule_sides(r)
            got = tuple(B._readable_side(t) for t in sides)
            want = EXPECTED_SIDE_TEXT[r["game_id"]]
            if got != want:
                bad.append(f'{r["game_id"]}: {got}, expected {want}')
        return bad
    check("unresolved sides print the right round, in display order",
          side_text, failures)

    # 7 — the line an ended team's page carries.
    def statuses():
        results = full_tournament(rows, doc)
        slots = B.resolve_slots(rows, doc, results)
        bad = []
        for t in doc["teams"]:
            got = B.team_status(t, doc, rows, results, slots)
            want = EXPECTED_STATUS[t["schedule_key"]]
            if got != want:
                bad.append(f'{t["schedule_key"]}: "{got}", expected "{want}"')
        return bad
    check("every team's end-of-tournament line", statuses, failures)

    # 8 — negative: a team still playing carries NO status line. A semi-final
    # loser is resolved into the third-place game and is not eliminated.
    def still_playing():
        results = group_results(rows)
        play(rows, doc, results, ["qqf-25", "qqf-26", "qqf-27", "qqf-28"])
        play(rows, doc, results, ["qf-29", "qf-30", "qf-31", "qf-32"])
        play(rows, doc, results, ["sf-33", "sf-34"])
        slots = B.resolve_slots(rows, doc, results)
        by_key = {t["schedule_key"]: t for t in doc["teams"]}
        bad = []
        # Beaten semi-finalists, with the third-place game still to play.
        for k in ("CHINA", "AUSTRALIA"):
            got = B.team_status(by_key[k], doc, rows, results, slots)
            if got:
                bad.append(f'{k} lost the SF but still plays for 3rd; '
                           f'got "{got}", expected no line')
        # Finalists.
        for k in ("GERMANY", "FRANCE"):
            got = B.team_status(by_key[k], doc, rows, results, slots)
            if got:
                bad.append(f'{k} is in the final; got "{got}", expected no line')
        # And a team that IS out still says so, or this proves nothing.
        if not B.team_status(by_key["SPAIN"], doc, rows, results, slots):
            bad.append("SPAIN finished 4th in its group and carries no line")
        return bad
    check("a team with a fixture left carries no status line",
          still_playing, failures)

    # 9 — the day the "Jump to next games" link points at, and the ORDER the
    # page renders in. Both are data-driven predicates that silently restyle
    # the most-read page: a stale "next games" link sends a reader to a day
    # that finished, and a page that flips to archive order early buries the
    # game being played tonight.
    def next_day_and_order():
        groups_done = group_results(rows)
        through_qf = group_results(rows)
        V_play = play(rows, doc, through_qf,
                      ["qqf-25", "qqf-26", "qqf-27", "qqf-28"])
        play(rows, doc, V_play, ["qf-29", "qf-30", "qf-31", "qf-32"])
        complete = full_tournament(rows, doc)
        cases = [
            ("nothing played", {}, "2026-09-04"),
            ("groups final", groups_done, "2026-09-08"),
            ("through the QFs", through_qf, "2026-09-12"),
            ("all 36 final", complete, None),
        ]
        bad = []
        for label, results, want in cases:
            got = B.next_games_day(rows, results)
            if got != want:
                bad.append(f"{label}: next games day {got}, expected {want}")
        # And the archive flip, asserted on the rendered page rather than on
        # the predicate: the final's day must lead once everything is final,
        # and must NOT lead a moment before that.
        _doc, teams = B.load_teams()
        for label, results in (("through the QFs", through_qf),
                               ("all 36 final", complete)):
            html = B.page_games(rows, teams, results, set(),
                                B.resolve_slots(rows, doc, results))
            first, last = html.find("Sun 13 Sep"), html.find("Fri 4 Sep")
            archived = results is complete
            if archived and not 0 < first < last:
                bad.append(f"{label}: archive order must lead with Sep 13")
            if not archived and not 0 < last < first:
                bad.append(f"{label}: live order must lead with Sep 4")
            if (B.JUMP_LABEL in html) is archived:
                bad.append(f"{label}: jump link presence is wrong "
                           f"(archived={archived})")
        return bad
    check("next-games day, and the archive order flip",
          next_day_and_order, failures)

    # 10 — every score on every team page, oriented to THAT team. The bug
    # this replaces was not a wrong number: both numbers were right, in an
    # order that flipped per row depending on whether the team sat in the
    # CSV's team_1 or team_2 column. So checking the pair is present proves
    # nothing — the check has to assert WHICH comes first, for all 16 teams
    # and all 36 games, in both the group and knockout sections.
    #
    # It renders `fixtures_block` and reads the HTML rather than calling
    # `game_cell_team` directly, and that is not fussiness. The first version
    # of this check called the helper, so it went on passing when the page was
    # reverted to the broken cell — the helper was right and unused. Whether
    # the PAGE calls the right helper is the whole question.
    def team_page_orientation():
        import re as _re
        results = full_tournament(rows, doc)
        slots = B.resolve_slots(rows, doc, results)
        _doc, teams = B.load_teams()
        bad = []
        for t in doc["teams"]:
            key = t["schedule_key"]
            want = [(x, 0 if x["team_1"] == key else 1) for x in rows
                    if x["group"] == t["group"]
                    and key in (x["team_1"], x["team_2"])]
            want += [(x, side) for x in rows if not x["group"]
                     for side in (0, 1)
                     if slots.get(x["game_id"], (None, None))[side] == key]
            html = B.fixtures_block(t, doc, rows, teams, results, set(), slots)
            rendered = [tr for tr in html.split("<tr>")[1:]]
            if len(rendered) != len(want):
                bad.append(f"{key}: {len(rendered)} rows rendered, "
                           f"{len(want)} fixtures expected")
                continue
            for tr, (x, side) in zip(rendered, want):
                nums = [int(n) for n in _re.findall(r">(\d+)<", tr)]
                letters = _re.findall(r">([WL])<", tr)
                score = results[x["game_id"]]["score"]
                ours, theirs = score[side], score[1 - side]
                if nums[:2] != [ours, theirs]:
                    bad.append(f'{key} / {x["game_id"]}: page shows '
                               f'{nums[:2]}, expected {[ours, theirs]} '
                               f'(own score first)')
                exp = "W" if ours > theirs else "L"
                if letters[:1] != [exp]:
                    bad.append(f'{key} / {x["game_id"]}: letter '
                               f'{letters[:1]}, expected {exp}')
        return bad
    check("every team-page score leads with that team's own score, W/L right",
          team_page_orientation, failures)

    # 11 — and the two-name surfaces keep FIXTURE order. The rule is "name
    # both teams -> fixture order; name one -> that team's order", so a fix
    # that flipped Games as well would be a different bug, not a fix.
    def games_page_keeps_fixture_order():
        results = full_tournament(rows, doc)
        row = next(r for r in rows if r["group"])
        html = B.game_cell(row, results, set())
        import re as _re
        nums = [int(n) for n in _re.findall(r">(\d+)<", html)]
        want = results[row["game_id"]]["score"]
        return ([] if nums[:2] == list(want) else
                [f'{row["game_id"]}: Games shows {nums[:2]}, expected {want}'])
    check("Games/Groups still print fixture order, not team order",
          games_page_keeps_fixture_order, failures)

    if failures:
        print("\nFAILED:")
        for label, bad in failures:
            print(f"  {label}")
            for b in bad:
                print(f"      {b}")
        return 1
    print(f"\nOK - bracket resolution reproduces a whole fabricated "
          f"tournament (36 games, 16 -> 8 -> 4 -> 2 -> 1) and refuses to "
          f"resolve in every state where it must not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
