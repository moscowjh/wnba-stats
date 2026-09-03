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
        check(f"{c}.{field}: 2026 must not appear in editions",
              all(e["year"] != 2026 for e in eds))
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
