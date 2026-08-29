#!/usr/bin/env python3
"""fetch_data.py — pull WWC 2026 results and box scores from fiba.basketball.

Writes exactly what `build_wwc_pages.py` already reads:

    data/results.json              {our_game_id: {status, teams, score, period}}
    data/boxscores/<game_id>.json  one file per completed game

Both are gitignored build artifacts, carried between CI runs by the Actions
cache, exactly as the WNBA CSVs are.

**Incremental**: a game whose box score is already on disk is not re-fetched.
A tournament is 36 games over ten days, so a full rebuild is cheap, but the
daily run should cost two or three page fetches, not thirty-six.

## The join, which is the whole difficulty

FIBA identifies games by an opaque numeric id. We identify them by a stable,
readable `game_id` that is already a public URL (`2026-09-04-japan-mali`,
`qf-29`). Those have to be matched, and matching them wrongly puts a real
score on the wrong fixture — worse than having no score at all, because it
looks right.

Two mechanisms, because the two halves of the tournament differ:

- **Group games (24) join on the two team codes.** Every pair meets exactly
  once in a group, so an unordered `{code_a, code_b}` is a unique key. This is
  fully determined today and testable offline.
- **Knockout games (12) cannot join on teams** — ours are `TBD` until the
  bracket resolves. They join on round, then chronological order within the
  round. **If the counts do not match, nothing is written for that round** and
  the mismatch is reported. Guessing here is precisely the failure this
  function exists to avoid.

## Team order is load-bearing

`results.json` stores `teams` and `score` as parallel pairs, and the Games
page renders `score[0]–score[1]` against `team_1 vs team_2` from the schedule
CSV. FIBA's own A/B ordering is its own business and does not necessarily
match ours, so every result is re-ordered to OUR row before it is written.
Get this wrong and every score on the site displays backwards — a bug that
would look like a data error rather than a plumbing one.

Usage:
    .venv/bin/python sites/wwc/fetch_data.py
    .venv/bin/python sites/wwc/fetch_data.py --force        # re-fetch all
    .venv/bin/python sites/wwc/fetch_data.py --limit 3      # cap page fetches
    .venv/bin/python sites/wwc/fetch_data.py --flight FILE --game-id qf-29
        Parse a saved flight payload instead of fetching. How this gets
        exercised before FIBA has published a single WWC game.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from sag.adapters import fiba

SITE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE_DIR))

from build_wwc_pages import load_schedule, load_teams  # noqa: E402
from config import WWC  # noqa: E402

#: From `tournament.source` in wwc2026_teams.json.
EVENT_SLUG = "fiba-womens-basketball-world-cup-2026"

RESULTS = WWC.data_dir / "results.json"
BOXSCORES = WWC.data_dir / "boxscores"

#: Our schedule `phase` -> the FIBA `roundCode`s that could carry it.
#:
#: Deliberately a list of candidates per phase. FIBA's codes are known for the
#: rounds every tournament has (QF/SF/F) and NOT known for this event's
#: crossover round — the U17 event used `R16` for its round of sixteen, but
#: this is a 4-team play-in, and no WWC 2026 knockout game exists yet to look
#: at. Candidates cost nothing and a wrong guess is caught by the count check
#: below rather than silently mis-assigning a score.
PHASE_ROUND_CODES = {
    "qualification_to_qf": ["QQF", "R16", "PI", "QFQ"],
    "quarter_final": ["QF"],
    "semi_final": ["SF"],
    "third_place": ["3PG", "3RD", "BRZ"],
    "final": ["F", "FNL"],
}


def code_to_schedule_key(doc):
    """FIBA three-letter code -> our `schedule_key`.

    The schema doc makes `code` the FIBA TLA and `schedule_key` the join to
    the schedule CSV, so this is a lookup rather than a guess.
    """
    return {t["code"]: t["schedule_key"] for t in doc["teams"]}


def build_group_index(rows):
    """{frozenset(schedule_key, schedule_key): row} for group games."""
    idx = {}
    for r in rows:
        if r["group"]:
            idx[frozenset((r["team_1"], r["team_2"]))] = r
    return idx


def match_games(rows, sched, doc, report):
    """FIBA game -> our schedule row. Returns [(row, fiba_game)]."""
    key_of = code_to_schedule_key(doc)
    group_idx = build_group_index(rows)
    pairs, used_rows = [], set()

    # ── Group games: unordered team pair is a unique key ──────────────────
    leftovers = []
    for g in sched:
        ka = key_of.get((g["team_a"] or {}).get("code"))
        kb = key_of.get((g["team_b"] or {}).get("code"))
        row = group_idx.get(frozenset((ka, kb))) if ka and kb else None
        if row is not None and row["game_id"] not in used_rows:
            pairs.append((row, g))
            used_rows.add(row["game_id"])
        else:
            leftovers.append(g)

    # ── Knockouts: round, then chronological slot ─────────────────────────
    for phase, codes in PHASE_ROUND_CODES.items():
        ours = [r for r in rows if r["phase"] == phase]
        theirs = [g for g in leftovers if g["round_code"] in codes]
        if not theirs:
            continue
        if len(theirs) != len(ours):
            # Do not guess. A mis-slotted knockout puts a real score on the
            # wrong fixture, which reads as correct to everyone.
            report.append(
                f"SKIPPED {phase}: FIBA has {len(theirs)} game(s) with round "
                f"code(s) {sorted({g['round_code'] for g in theirs})}, our "
                f"schedule has {len(ours)}. Not matching on a count mismatch — "
                f"check PHASE_ROUND_CODES.")
            continue
        theirs.sort(key=lambda g: g["datetime_utc"] or "")
        for row, g in zip(ours, theirs):
            pairs.append((row, g))
    return pairs


def orient(row, g, key_of):
    """(teams, score) in OUR row's order. See the module docstring."""
    ka = key_of.get((g["team_a"] or {}).get("code"))
    kb = key_of.get((g["team_b"] or {}).get("code"))
    sa, sb = g.get("score_a"), g.get("score_b")
    if row["group"]:
        # Group rows name their teams, so orient to them.
        if (ka, kb) == (row["team_2"], row["team_1"]):
            return [kb, ka], [sb, sa]
        return [ka, kb], [sa, sb]
    # Knockout rows are TBD; FIBA's own order is the only order there is.
    return [ka, kb], [sa, sb]


def box_players(side, game):
    """FIBA's roster + stat blocks -> the box-score template's player list.

    A stat FIBA does not carry becomes `None`, never `0` — the template
    renders None as an em dash and makes the team total None too. See
    `docs/wwc-site-internals.md`.
    """
    roster = game["rosters"][side]
    box = game["box"][side]["players"]
    out = []
    for pid, stats in box.items():
        r = roster.get(pid, {})
        out.append({
            "number": r.get("number"),
            "name": r.get("name"),
            "position": r.get("position"),
            "starter": stats.get("starter"),
            # FIBA gives `time_played` as "MM:SS"; the template truncates it.
            "min": stats.get("time_played") or stats.get("min"),
            "pts": stats.get("pts"),
            "fgm": stats.get("fgm"), "fga": stats.get("fga"),
            "tpm": stats.get("fg3m"), "tpa": stats.get("fg3a"),
            "ftm": stats.get("ftm"), "fta": stats.get("fta"),
            "reb": stats.get("reb"), "oreb": stats.get("oreb"),
            "ast": stats.get("ast"), "stl": stats.get("stl"),
            "blk": stats.get("blk"), "tov": stats.get("tov"),
            "pf": stats.get("pf"), "plus_minus": stats.get("plus_minus"),
        })
    # Starters first, then by minutes descending — the WNBA box's order.
    out.sort(key=lambda p: (not p["starter"], -_secs(p["min"])))
    return out


def _secs(v):
    if isinstance(v, str) and ":" in v:
        mm, ss = v.split(":", 1)
        try:
            return int(mm) * 60 + int(ss)
        except ValueError:
            return 0
    try:
        return int(float(v)) * 60
    except (TypeError, ValueError):
        return 0


def to_boxscore(row, game, key_of):
    """The template's box-score document, teams in our row's order."""
    meta = game["meta"]
    sides = ["A", "B"]
    codes = [meta["team_a"]["code"], meta["team_b"]["code"]]
    keys = [key_of.get(c) for c in codes]
    # A code we cannot resolve is a hard error, never a null written to disk.
    # It means one of: FIBA renamed a code, we matched the wrong game, or the
    # payload is from a different competition entirely. All three are things
    # to find out about now rather than to render as a blank team name.
    if None in keys:
        unknown = [c for c, k in zip(codes, keys) if k is None]
        raise ValueError(
            f"FIBA team code(s) {unknown} are not in the WWC 2026 field. "
            f"Refusing to write a box score with an unresolved team.")
    scores = [meta["score_a"], meta["score_b"]]
    lines = [[q["a"] for q in game["linescore"]],
             [q["b"] for q in game["linescore"]]]
    if row["group"] and (keys[0], keys[1]) == (row["team_2"], row["team_1"]):
        sides, keys, scores, lines = sides[::-1], keys[::-1], scores[::-1], lines[::-1]
    return {
        "game_id": row["game_id"],
        "status": "final",
        "fiba_game_id": meta["game_id"],
        "teams": [
            {"schedule_key": keys[i], "score": scores[i], "linescore": lines[i],
             "players": box_players(sides[i], game)}
            for i in (0, 1)
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="re-fetch games already on disk")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the number of game pages fetched this run")
    ap.add_argument("--flight", help="parse a saved flight payload, no network")
    ap.add_argument("--game-id", help="our game_id, required with --flight")
    ap.add_argument("--out", help="where --flight writes; REQUIRED with it")
    ap.add_argument("--map", default="",
                    help="test-only code remap, e.g. CAN=ITALY. Lets a saved "
                         "payload from another competition stand in for a WWC "
                         "fixture so the pipeline can be proved end to end.")
    args = ap.parse_args()

    WWC.ensure_dirs()
    BOXSCORES.mkdir(parents=True, exist_ok=True)
    doc, _ = load_teams()
    rows = load_schedule()
    rows_by_id = {r["game_id"]: r for r in rows}
    key_of = code_to_schedule_key(doc)

    # ── Offline mode: prove the pipeline without a live tournament ────────
    if args.flight:
        if not args.game_id or args.game_id not in rows_by_id:
            print("--flight needs --game-id naming a real schedule row")
            return 1
        # --out is REQUIRED, and deliberately so. Defaulting it to
        # data/boxscores/ would let a validation run quietly publish a game
        # from another competition as a WWC result — the same trap the
        # box-score fixture is kept out of public/ to avoid.
        if not args.out:
            print("--flight requires --out. It must not write into "
                  "data/boxscores/, where the build would treat it as real.")
            return 1
        for pair in filter(None, args.map.split(",")):
            code, key = pair.split("=", 1)
            key_of[code.strip()] = key.strip()
        game = fiba.parse_game(Path(args.flight).read_text(encoding="utf-8"))
        box = to_boxscore(rows_by_id[args.game_id], game, key_of)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(box, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"Parsed offline -> {out}")
        return 0

    report = []
    print(f"Fetching schedule: {fiba.event_games_url(EVENT_SLUG)}")
    payload = fiba.flight_payload(fiba.fetch(fiba.event_games_url(EVENT_SLUG)))
    sched = fiba.parse_schedule(payload)
    print(f"  {len(sched)} games on FIBA's page")
    if not sched:
        print("No games parsed. FIBA's page structure may have changed — "
              "check the signature anchors in sag.adapters.fiba before "
              "assuming an outage.")
        return 1

    pairs = match_games(rows, sched, doc, report)
    print(f"  matched {len(pairs)}/{len(rows)} to our schedule")

    results = {}
    if RESULTS.exists():
        results = json.loads(RESULTS.read_text(encoding="utf-8"))

    fetched = 0
    for row, g in pairs:
        gid = row["game_id"]
        if g["status"] == "scheduled":
            continue
        teams, score = orient(row, g, key_of)
        results[gid] = {"status": g["status"], "teams": teams, "score": score}
        if g["status"] != "final":
            continue
        target = BOXSCORES / f"{gid}.json"
        if target.exists() and not args.force:
            continue
        if args.limit and fetched >= args.limit:
            continue
        url = fiba.game_url(EVENT_SLUG, g["game_id"],
                            g["team_a"]["code"], g["team_b"]["code"])
        try:
            game = fiba.parse_game(fiba.flight_payload(fiba.fetch(url)))
        except Exception as exc:                       # noqa: BLE001
            # Per-game isolation, as the WNBA fetch does: one unparseable
            # game must not cost the other thirty-five.
            report.append(f"FAILED {gid}: {exc}")
            continue
        target.write_text(
            json.dumps(to_boxscore(row, game, key_of), ensure_ascii=False,
                       indent=2), encoding="utf-8")
        fetched += 1
        print(f"  box score {gid}")
        time.sleep(0.5)

    RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"Wrote {RESULTS} ({len(results)} results), "
          f"{fetched} new box score(s)")
    for line in report:
        print(f"  ! {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
