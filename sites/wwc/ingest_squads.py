#!/usr/bin/env python3
"""ingest_squads.py — fold the Wikipedia squads capture into wwc2026_teams.json.

NOT part of the build. Run by hand when a new capture lands; the emitter reads
only the JSON, so CI never touches this file or the TSVs beside it.

── Why the normalisation lives HERE and not in the emitter ────────────────
Joining two name lists is the kind of work that is cheap to get wrong and
expensive to notice. Doing it at ingest means it happens ONCE, under a human,
with a hard failure available — rather than on every build, silently, where a
missed join renders as a blank cell that looks like honest absence. The
emitter's contract after this runs is: read a field, print it.

── The capture stays verbatim ─────────────────────────────────────────────
`wikipedia-squads-2026-09-03.tsv` is a byte-for-byte capture and is never
edited. Corrections live in `overrides-2026-09-03.tsv` and are applied on top,
because a source you have quietly corrected can no longer be re-captured and
diffed against the next capture. That is the whole reason for two files.

── The TSV wins on names and numbers ──────────────────────────────────────
This is a CORRECTION as much as an enrichment. The JSON held the 2026-09-02
capture; Korea and China were renumbered and Korea re-romanised between the
two. Where they disagree, the newer capture is right.

── Joining ────────────────────────────────────────────────────────────────
Exact normalised match, then token-set (which joins "Park Ji-hyun" to
"Jihyun Park"), then an explicit alias table. Nothing else. A different
surname NEVER auto-joins — the Gustafson/DiLeo case is why ALIASES exists and
why each entry carries its source. Anything unresolved, ambiguous, or left
over on either side raises: a partial ingest that half-updates a roster 36
hours before tip is worse than no ingest at all.

Usage:
    .venv/bin/python sites/wwc/ingest_squads.py            # rewrite the JSON
    .venv/bin/python sites/wwc/ingest_squads.py --dry-run  # report only
"""

import argparse
import csv
import json
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF = HERE / "reference"
TEAMS_JSON = REF / "wwc2026_teams.json"
CAPTURE = REF / "wikipedia-squads-2026-09-03.tsv"
OVERRIDES = REF / "overrides-2026-09-03.tsv"

SCHEMA_VERSION = "2.2.0"
GENERATED = "2026-09-03"

#: Wikipedia's club-country prose -> the three-letter codes the JSON already
#: uses everywhere else. Kept explicit rather than fuzzy-matched: nineteen
#: values is not enough data to justify a guess, and a wrong country code is
#: invisible in a three-character column.
COUNTRY_CODE = {
    "United States": "USA", "Spain": "ESP", "Turkey": "TUR",
    "South Korea": "KOR", "China": "CHN", "Japan": "JPN", "Italy": "ITA",
    "Czech Republic": "CZE", "Hungary": "HUN", "France": "FRA",
    "Australia": "AUS", "Germany": "GER", "Canada": "CAN",
    "Senegal": "SEN", "Rwanda": "RWA", "Romania": "ROU", "Mali": "MLI",
    "Kenya": "KEN", "Ivory Coast": "CIV",
}

#: Explicit name aliases: JSON name (2026-09-02 capture) -> TSV name
#: (2026-09-03 capture). Every entry is a re-romanisation of the same Korean
#: name by the same source between two captures a day apart, recorded here
#: rather than inferred. A surname heuristic would have had to choose between
#: the two Kangs, and choosing wrong swaps two players' ages and heights.
ALIASES = {
    ("KOR", "Ahn Hye-ji"): "An He-ji",
    ("KOR", "Choi Yi-saem"): "Choi I-saem",
    ("KOR", "Kang Yi-seul"): "Kang Lee-seul",
    ("KOR", "Kang Yu-rim"): "Kang Yoo-lim",
}


def norm(s):
    """NFC, no non-breaking spaces, collapsed whitespace.

    U+00A0 is not cosmetic here: it broke every name join in the 2026-09-02
    data, and it is invisible in every tool that would show you the string.
    """
    s = unicodedata.normalize("NFC", str(s))
    s = s.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    return " ".join(s.split())


def tokens(name):
    """Lowercased word set, hyphens split. `Park Ji-hyun` -> {park, ji, hyun}."""
    return frozenset(norm(name).lower().replace("-", " ").replace(".", " ").split())


def read_tsv(path, fieldnames=None):
    """Comment lines dropped. `fieldnames` for files whose columns are
    documented in the header COMMENT rather than in a header row — the
    overrides file is one, and DictReader would otherwise silently eat its
    first correction as a header."""
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if not l.startswith("#") and l.strip()]
    if fieldnames:
        return list(csv.DictReader(lines, fieldnames=fieldnames, delimiter="\t"))
    return list(csv.DictReader(lines, delimiter="\t"))


OVERRIDE_COLUMNS = ["team", "name", "field", "value", "reason"]


def load_overrides():
    """(team, name) -> {field: value}. Reasons are for the human, not the code."""
    out = {}
    for r in read_tsv(OVERRIDES, OVERRIDE_COLUMNS):
        out.setdefault((r["team"], norm(r["name"])), {})[r["field"]] = norm(r["value"])
    return out


def join_squad(code, json_players, tsv_rows, log):
    """Pair JSON roster entries to capture rows. Returns {json index: tsv row}.

    Raises on anything it cannot resolve exactly once.
    """
    remaining = {i: p for i, p in enumerate(json_players)}
    pool = list(tsv_rows)
    paired = {}

    def take(idx, row, how):
        paired[idx] = row
        remaining.pop(idx)
        pool.remove(row)
        if how != "exact":
            log.append(f"  {code}: {how} join "
                       f"{json_players[idx]['name']!r} -> {row['name']!r}")

    for how, key in (("exact", lambda n: norm(n)),
                     ("token-set", tokens)):
        for idx in list(remaining):
            want = key(remaining[idx]["name"])
            hits = [r for r in pool if key(r["name"]) == want]
            if len(hits) == 1:
                take(idx, hits[0], how)
            elif len(hits) > 1:
                raise SystemExit(
                    f"{code}: {remaining[idx]['name']!r} matches {len(hits)} "
                    f"capture rows by {how} — refusing to guess")

    for idx in list(remaining):
        alias = ALIASES.get((code, norm(remaining[idx]["name"])))
        if not alias:
            continue
        hits = [r for r in pool if norm(r["name"]) == alias]
        if len(hits) != 1:
            raise SystemExit(f"{code}: alias {remaining[idx]['name']!r} -> "
                             f"{alias!r} matched {len(hits)} rows")
        take(idx, hits[0], "alias")

    if remaining or pool:
        raise SystemExit(
            f"{code}: unjoined. JSON-only={[p['name'] for p in remaining.values()]} "
            f"capture-only={[r['name'] for r in pool]}. Add an ALIAS with a "
            f"source, or re-capture — do not loosen the matcher.")
    return paired


def sync_wnba(t):
    """Point every roster row's WNBA fields at the curated `wnba.players`.

    Runs for ALL SIXTEEN teams, capture or no capture, and this is the whole
    fix for the class of bug this rebuild replaces. Two denormalised copies of
    "is she in the WNBA" used to live in the file and drift apart — on
    2026-09-02 six roster rows said false while the wnba block called five of
    them current, and Hungary published "0 current players" above a table
    naming Dorka Juhász. There is now ONE derivation, it happens here, and the
    validator asserts the two sides agree. The emitter reads and prints.

    Nigeria is why this is not folded into apply_team(): she is not in the
    capture, but Amy Okonkwo is still a current WNBA player, and her badge
    has to render.
    """
    current = {norm(p["name"]): p for p in t["wnba"]["players"]
               if p["status"] == "current"}
    for p in t["roster"]["players"]:
        rec = current.get(norm(p["name"]))
        pf = p["plays_for"]
        pf["type"] = "wnba" if rec else ("club" if pf.get("club_name") else None)
        # The TLA comes from the curated block, never from the capture: it is
        # what the badge prints and what the WNBA player pages key on.
        pf["wnba_team"] = rec["wnba_team"] if rec else None
        p["wnba"] = rec is not None
    return len(current)


def apply_team(t, tsv_rows, overrides, log):
    code = t["code"]
    players = t["roster"]["players"]
    paired = join_squad(code, players, tsv_rows, log)

    # The roster is re-ordered to the CAPTURE's order, which is the source's
    # own sort: by jersey number, unnumbered players last. Without this the
    # renumbered teams keep an order derived from the numbers they used to
    # have — Korea rendered 2, 1, 12, 39 down a column headed "No.", which
    # reads as a broken table rather than as a squad list. Teams outside the
    # capture (NGR, PUR) keep the order they have; they have no numbers to
    # sort by either.
    order = {id(row): i for i, row in enumerate(tsv_rows)}
    t["roster"]["players"] = [players[i] for i in
                              sorted(paired, key=lambda i: order[id(paired[i])])]

    for idx, row in paired.items():
        p = players[idx]
        old_name = norm(p["name"])
        ov = overrides.get((code, norm(row["name"]))) or overrides.get((code, old_name)) or {}

        def field(key):
            return ov.get(key, norm(row.get(key) or "")) or None

        if old_name != norm(row["name"]):
            log.append(f"  {code}: renamed {p['name']!r} -> {norm(row['name'])!r}")
            p["name"] = norm(row["name"])
        number = norm(row["no"]) or None
        if number != p.get("number"):
            log.append(f"  {code}: {p['name']} number {p.get('number')!r} -> {number!r}")
            p["number"] = number
        if row["pos"]:
            p["position"] = norm(row["pos"])

        age = field("age")
        p["age"] = int(age) if age else None
        p["height"] = field("height")

        club = field("club")
        country = field("club_country")
        if country and country not in COUNTRY_CODE:
            raise SystemExit(f"{code}: unmapped club country {country!r} — "
                             f"add it to COUNTRY_CODE")
        p["plays_for"] = {
            # type/wnba_team are filled by sync_wnba(), which runs afterwards
            # for every team so Nigeria and Puerto Rico get them too.
            "type": None,
            "wnba_team": None,
            "club_name": club,
            "club_country": COUNTRY_CODE[country] if country else None,
            # A player with both a WNBA team and a club abroad. The capture's
            # single Club column cannot hold two, which is exactly why the
            # Note column exists (Kennedy Burke, 2026-09-03).
            "other_club": field("other_club"),
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="report the joins and changes without writing")
    args = ap.parse_args()

    doc = json.loads(TEAMS_JSON.read_text(encoding="utf-8"))
    teams = {t["code"]: t for t in doc["teams"]}
    overrides = load_overrides()

    by_team = {}
    for r in read_tsv(CAPTURE):
        by_team.setdefault(r["team"], []).append(r)

    unknown = set(by_team) - set(teams)
    if unknown:
        raise SystemExit(f"capture names teams not in the JSON: {sorted(unknown)}")

    log = []
    for code, rows in by_team.items():
        apply_team(teams[code], rows, overrides, log)

    # Teams the capture does not cover keep their existing shape and gain the
    # new keys as nulls, so every roster row has one key set and the emitter's
    # per-column gate is the ONLY thing deciding what renders. Nigeria (no
    # published squad) and Puerto Rico (an Instagram screenshot our own data
    # beats) are both deliberately absent from the capture — see the handoff.
    for code, t in teams.items():
        if code in by_team:
            continue
        for p in t["roster"]["players"]:
            p.setdefault("age", None)
            p.setdefault("height", None)
            p["plays_for"].setdefault("other_club", None)
        log.append(f"  {code}: not in the capture — new fields left null "
                   f"({len(t['roster']['players'])} players)")

    for code, t in teams.items():
        n = sync_wnba(t)
        badges = sum(1 for p in t["roster"]["players"] if p["wnba"])
        # The regression test for the Hungary bug, asserted at the point the
        # data is written rather than at the point it is rendered.
        if badges != n or n != t["wnba"]["current"]:
            raise SystemExit(f"{code}: {badges} badge rows, {n} current in "
                             f"wnba.players, {t['wnba']['current']} in the "
                             f"headline count — these must be one number")

    unused = set(overrides) - {
        (t["code"], norm(p["name"]))
        for t in doc["teams"] for p in t["roster"]["players"]}
    if unused:
        raise SystemExit(f"overrides matched nobody: {sorted(unused)}")

    doc["_schema"]["version"] = SCHEMA_VERSION
    doc["_schema"]["generated"] = GENERATED
    doc["_schema"]["keys"] = doc["_schema"]["keys"]
    doc["_schema"]["nulls"] = (
        doc["_schema"]["nulls"].split(" roster.players[].age")[0]
        + " roster.players[].age, .height and .plays_for.club_name/"
        "club_country/other_club are null wherever the squad capture does not "
        "cover the team (NGR, PUR) or the source has no value; the emitter "
        "gates each column per team rather than printing a dash column.")
    doc["_schema"]["sources"] = doc["_schema"]["sources"]
    doc["_corrections"]["KOR.roster.romanisation"] = (
        "The 2026-09-02 capture gave Ahn Hye-ji, Choi Yi-saem, Kang Yi-seul and "
        "Kang Yu-rim. Wikipedia re-romanised all four to An He-ji, Choi I-saem, "
        "Kang Lee-seul and Kang Yoo-lim on 2026-09-03, and renumbered the squad. "
        "The newer capture wins. The four are joined by an EXPLICIT alias table "
        "in ingest_squads.py, not by a matcher: 'Kang Yi-seul' and 'Kang Yu-rim' "
        "are both Kang, and a surname heuristic that guessed wrong would have "
        "swapped two players' ages and heights with nothing to show for it.")
    doc["_corrections"]["CHN.roster.numbers"] = (
        "China was renumbered between the 09-02 and 09-03 captures (eight of "
        "twelve). Names were unaffected. The squads page is edited hourly this "
        "close to tip — re-capture, never assume yesterday's numbers hold.")

    if args.dry_run:
        print("\n".join(log) or "  (no changes)")
        print(f"DRY RUN — {TEAMS_JSON} not written")
        return
    TEAMS_JSON.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n".join(log))
    print(f"ingested {sum(len(v) for v in by_team.values())} capture rows "
          f"across {len(by_team)} teams -> {TEAMS_JSON.name} "
          f"(schema {SCHEMA_VERSION})")


if __name__ == "__main__":
    main()
