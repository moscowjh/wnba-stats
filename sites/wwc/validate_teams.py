#!/usr/bin/env python3
"""Validate wwc2026_teams.json. Exit 1 on any failure.

Usage: python3 validate_teams.py [teams.json] [schedule.csv]
Defaults to the paths used in sites/wwc/reference/.
"""
import csv, json, re, sys, collections, pathlib

HERE = pathlib.Path(__file__).parent
TEAMS = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "reference/wwc2026_teams.json"
SCHED = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "reference/wwc_schedule_2026.csv"

ROSTER_STATUS = {"final", "pool", "not_announced"}
ROUTES = {"host", "continental_cup_champion", "qualifying_tournament"}
PROFILE_STATUS = {"empty", "draft", "published"}

fails = []
warns = []
def check(label, cond, detail=""):
    if not cond:
        fails.append(f"{label}{': ' + str(detail) if detail else ''}")

def warn(label, cond, detail=""):
    """A house style rule, not a data error.

    Jason, 2026-09-02: the word cap and the future-facing verb list exist to
    keep a MODEL's drafts honest — "those are useful if the model is doing the
    writing, but I want the ability to overrule." A human editor overrules
    them, so they must never fail a build. They are still worth SAYING, because
    a draft that drifts past them by accident is a different thing from one a
    person lengthened on purpose.

    The 55 itself was never measured — it was derived from the 380px frame, and
    the profile rules doc still lists "is 55 the right cap?" as open."""
    if not cond:
        warns.append(f"{label}{': ' + str(detail) if detail else ''}")

doc = json.loads(TEAMS.read_text())
T = doc["teams"]

#: This tournament's own year, read rather than typed — it is the boundary
#: between "recorded" and "not yet played" for every editions array.
EDITION_YEAR = int(doc["tournament"]["start_date"][:4])

#: `data/results.json` if a build has fetched one. The medal cross-check below
#: is skipped when it is absent, because a fresh clone has no build artifacts
#: and must still validate — this file's own job is the reference data.
RESULTS = HERE / "data/results.json"
results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}

check("expected 16 teams", len(T) == 16, len(T))
check("codes must be unique", len({t["code"] for t in T}) == len(T))
groups = collections.Counter(t["group"] for t in T)
check("expected 4 teams per group", set(groups.values()) == {4}, dict(groups))
check("all team records share a key set",
      len({tuple(sorted(t)) for t in T}) == 1)

for t in T:
    c = t["code"]
    check(f"{c}: roster.status in enum", t["roster"]["status"] in ROSTER_STATUS, t["roster"]["status"])
    check(f"{c}: qualification.route in enum", t["qualification"]["route"] in ROUTES)
    check(f"{c}: profile_status in enum", t["profile_status"] in PROFILE_STATUS)
    check(f"{c}: a 'final' roster cannot exceed 12",
          not (t["roster"]["status"] == "final" and (t["roster"]["player_count"] or 0) > 12))
    check(f"{c}: a 'pool' roster needs a player_count",
          not (t["roster"]["status"] == "pool" and not t["roster"]["player_count"]))

    for field in ("wwc_record", "olympic_record"):
        r = t[field]
        eds = r["editions"]
        if eds is None:
            continue
        check(f"{c}.{field}: editions length equals appearances_count",
              len(eds) == r["appearances_count"], f"{len(eds)} vs {r['appearances_count']}")
        # Until 2026-09-14 this read "2026 must not appear in editions", and
        # that was right for every day it was true: the schema stores one entry
        # per COMPLETED tournament, and a speculative Berlin entry would have
        # flowed straight into best_finish and into the Guide's prose as
        # fabricated data. The tournament finishing is what retired the rule,
        # not a convenience — so what replaces it is the same rule stated
        # durably: nothing may be recorded for a tournament that has not
        # happened. The medal places are separately cross-checked against
        # results.json below, which is the half a year bound cannot do.
        check(f"{c}.{field}: no edition may be in the future",
              all(e["year"] <= EDITION_YEAR for e in eds),
              [e["year"] for e in eds if e["year"] > EDITION_YEAR])
        check(f"{c}.{field}: ranks must be 1..16", all(1 <= e["rank"] <= 16 for e in eds))
        check(f"{c}.{field}: years must be unique", len({e["year"] for e in eds}) == len(eds))
        best = min(e["rank"] for e in eds)
        check(f"{c}.{field}: best_finish.rank derives from editions",
              best == r["best_finish"]["rank"], f"{best} vs {r['best_finish']['rank']}")
        years = sorted(e["year"] for e in eds if e["rank"] == best)
        check(f"{c}.{field}: best_finish.years derives from editions",
              years == sorted(r["best_finish"]["years"]))
        if field == "olympic_record":
            check(f"{c}.{field}: most_recent_year derives from editions",
                  max(e["year"] for e in eds) == r["most_recent_year"])

# --- This tournament's own result, cross-checked against the games we published ---
# The 1-16 classification written in on 2026-09-14 is FIBA's, transcribed by
# hand, and a hand-transcribed rank is exactly the kind of thing that ships
# silently wrong: it flows into best_finish, into "12 of the 20 World Cups",
# and into every team page's history card, with no game to contradict it.
#
# Four of the sixteen ARE contradictable. The medal games are on our own site,
# so USA/France/Spain/Germany can be re-derived from results.json and compared.
# The other twelve rest on FIBA's classification alone and are unprovable here
# — which is worth knowing rather than pretending otherwise.
#
# Gate: only once the data CLAIMS this edition and the final is really played.
# Mid-tournament, and on a clone with no build artifacts, this is skipped
# rather than failed.
final_res = results.get("final") or {}
claims_edition = any(
    any(e["year"] == EDITION_YEAR for e in (t["wwc_record"]["editions"] or []))
    for t in T)
if claims_edition and final_res.get("status") == "final":
    medal = {}
    # `third-place` and `final` are the emitter's game_ids for the two medal
    # games — the phase name itself, per the id scheme in wwc-site-internals.md
    # ("Third place, Final (2) | the phase"). Sides come from the RESULT and
    # never from the bracket, the same rule orient() forces everywhere else.
    for gid, places in (("third-place", (3, 4)), ("final", (1, 2))):
        res = results.get(gid) or {}
        if res.get("status") != "final":
            continue
        (a, b), (sa, sb) = res["teams"], res.get("score") or [None, None]
        if sa is None or sb is None:
            continue
        win, lose = places
        medal[a], medal[b] = (win, lose) if sa > sb else (lose, win)

    check("both medal games resolve from results.json", len(medal) == 4, medal)
    by_key = {t["schedule_key"]: t for t in T}
    for key, rank in sorted(medal.items(), key=lambda kv: kv[1]):
        t = by_key.get(key)
        check(f"{key}: medal team is in the reference data", t is not None)
        if not t:
            continue
        got = next((e["rank"] for e in t["wwc_record"]["editions"]
                    if e["year"] == EDITION_YEAR), None)
        check(f"{t['code']}.wwc_record: {EDITION_YEAR} rank agrees with the "
              f"medal games we published", got == rank, f"{got} vs {rank}")

    placed = sorted(e["rank"] for t in T
                    for e in t["wwc_record"]["editions"] if e["year"] == EDITION_YEAR)
    check(f"{EDITION_YEAR} classification is a complete 1..{len(T)} with no ties",
          placed == list(range(1, len(T) + 1)), placed)

# --- WNBA block ---
WNBA_STATUS = {"current", "former", "drafted_only"}
for t in T:
    c, w = t["code"], t["wnba"]
    counts = {"current": 0, "former": 0, "drafted_only": 0}
    for p in w["players"]:
        check(f"{c}.wnba: player status in enum", p["status"] in WNBA_STATUS, p["status"])
        counts[p["status"]] = counts.get(p["status"], 0) + 1
        if p["wnba_team"]:
            check(f"{c}.wnba: {p['name']} has a resolved team name", bool(p["wnba_team_full"]), p["wnba_team"])
    for k in counts:
        check(f"{c}.wnba: {k} count derives from players", counts[k] == w[k], f"{counts[k]} vs {w[k]}")
    check(f"{c}.wnba: total_connected derives from players",
          w["total_connected"] == len(w["players"]))
    check(f"{c}.wnba: a proxy roster_basis must not claim high confidence",
          not (w["roster_basis"].startswith("proxy") and w["confidence"] == "high"))

# --- roster rows (the one table the team pages render) ---
# Added 2026-09-03 with the single roster table. Every column on that table is
# a field checked here, and the LAST check is the important one: it is the
# regression test for the bug the table replaces. A hand-maintained headline
# count sitting above a table that read a different source is how Hungary
# published "0 current players" directly above a row naming Dorka Juhasz. The
# emitter now derives the count line from the same rows that render the
# badges and refuses to emit a table where they disagree; this asserts the
# same equality one layer down, in the data.
HEIGHT_RE = re.compile(r"^\d+ ft \d+ in$")
PLAYS_FOR_KEYS = {"type", "wnba_team", "club_name", "club_country", "other_club"}
for t in T:
    c = t["code"]
    for p in t["roster"]["players"]:
        who = f"{c}.{p['name']}"
        age = p.get("age")
        check(f"{who}: age is a plausible integer or null",
              age is None or (isinstance(age, int) and 15 <= age <= 50), age)
        h = p.get("height")
        check(f"{who}: height is ft/in or null",
              h is None or bool(HEIGHT_RE.match(h)), h)
        pf = p["plays_for"]
        check(f"{who}: plays_for carries the full key set",
              set(pf) == PLAYS_FOR_KEYS, sorted(set(pf) ^ PLAYS_FOR_KEYS))
        cc = pf["club_country"]
        check(f"{who}: club_country is a three-letter code or null",
              cc is None or (len(cc) == 3 and cc.isupper()), cc)
        # The badge and the flag are one fact. Correct-or-blank: a player
        # marked WNBA without a team would render a bare "WNBA" box.
        check(f"{who}: plays_for.type agrees with the wnba flag",
              (pf["type"] == "wnba") == bool(p["wnba"]), pf["type"])
        check(f"{who}: a WNBA player has a team code",
              not (p["wnba"] and not pf["wnba_team"]))
        check(f"{who}: a non-WNBA player has no team code",
              not (not p["wnba"] and pf["wnba_team"]), pf["wnba_team"])

    roster_names = {p["name"] for p in t["roster"]["players"]}
    # note_cell() looks former/drafted players up by name. A name that does
    # not join renders as a silently missing note, which is indistinguishable
    # from a player who has no history at all.
    missing = [p["name"] for p in t["wnba"]["players"] if p["name"] not in roster_names]
    check(f"{c}: every wnba.players name joins to a roster row", not missing, missing)

    badges = sum(1 for p in t["roster"]["players"] if p["wnba"])
    current = sum(1 for p in t["wnba"]["players"] if p["status"] == "current")
    check(f"{c}: badge rows, current players and the headline count are one number",
          badges == current == t["wnba"]["current"],
          f"{badges} badges / {current} current / {t['wnba']['current']} headline")

# --- profile rules ---
for t in T:
    c = t["code"]
    if t["profile_status"] == "empty":
        check(f"{c}: an empty profile_status means an empty profile", t["profile"] == "")
        continue
    words = len(t["profile"].split())
    warn(f"{c}: profile runs past the 55-word guideline", words <= 55, words)
    lowered = " " + t["profile"].lower()
    for banned in (" will ", " upcoming ", " this week ", " hope", " expect"):
        warn(f"{c}: profile uses future-facing '{banned.strip()}'", banned not in lowered)

# --- joins against the schedule ---
rows = [r for r in csv.DictReader(SCHED.open()) if r["phase"] == "group"]
sched_teams = {r["team_1"] for r in rows} | {r["team_2"] for r in rows}
mine = {t["schedule_key"] for t in T}
check("schedule_key set matches the schedule CSV", sched_teams == mine, sorted(sched_teams ^ mine))

sched_group = {}
for r in rows:
    sched_group[r["team_1"]] = r["group"]
    sched_group[r["team_2"]] = r["group"]
bad = [(t["code"], t["group"], sched_group.get(t["schedule_key"])) for t in T
       if sched_group.get(t["schedule_key"]) != t["group"]]
check("group assignments agree with the schedule CSV", not bad, bad)

# --- qualification arithmetic ---
routes = collections.Counter(t["qualification"]["route"] for t in T)
check("1 host / 4 continental champions / 11 via qualifying tournaments",
      (routes["host"], routes["continental_cup_champion"], routes["qualifying_tournament"]) == (1, 4, 11),
      dict(routes))
alloc = collections.Counter(t["qualification"]["city"] for t in T
                            if t["qualification"]["route"] == "qualifying_tournament")
check("qualifying-tournament allocation matches the declared summary",
      dict(alloc) == doc["tournament"]["qualification_summary"]["qt_allocation"], dict(alloc))

# Warnings print FIRST, so they are visible above the pass/fail line rather
# than scrolled off. They never affect the exit code — see warn().
if warns:
    print(f"style notes ({len(warns)}) - guidance, not errors; a human editor overrules:")
    for w_ in warns:
        print("  ~", w_)

if fails:
    print(f"FAILED ({len(fails)}):")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print(f"OK - {len(T)} teams, all checks pass"
      + (f" ({len(warns)} style notes)" if warns else ""))
