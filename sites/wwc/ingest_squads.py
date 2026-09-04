#!/usr/bin/env python3
"""ingest_squads.py — fold the OFFICIAL FIBA roster capture into wwc2026_teams.json.

NOT part of the build. Run by hand when a new capture lands; the emitter reads
only the JSON, so CI never touches this file or the captures beside it.

── What changed on 2026-09-03 (the second rewrite of this file) ───────────
FIBA published the confirmed sixteen rosters the day before tip, and this
script switched from ingesting a Wikipedia transcription to ingesting the
source. `fetch_rosters.py` explains why that is better on every axis; the two
consequences that land HERE are:

  1. **The roster is now a fact we read, not a count we infer.** Every team is
     twelve players with `isOnFinalRoster` true. Mali's 22-name pool and
     Nigeria's 17-name inferred squad are resolved, and BOTH lose players who
     will not be at the tournament. Anyone not on the final twelve is DROPPED,
     loudly — the old behaviour, raising on leftovers, was right when leftovers
     meant a broken join and is wrong now that they mean a cut.

  2. **`person_id` lands on every roster row**, and it is the reason to prefer
     this source even where Wikipedia agreed. Every other FIBA surface we parse
     is keyed on `personId`, so the emitter can resolve a box-score line to a
     rostered player by ID. Until now the only bridge between a box score and
     a roster was the player's NAME, in whatever form FIBA wrote it that day —
     and FIBA writes names in a form we deliberately do not publish (below).

── FIBA decides the ROSTER. It does not decide the SPELLING ───────────────
This is the load-bearing rule of this file, and it is a decision, not an
oversight (Jason, 2026-09-03):

  * FIBA's `firstName`/`lastName` are ASCII-stripped. Every diacritic in the
    field is gone — Johannès, Juhász, Şenyürek, and every -ová in the Czech
    squad. `uniformName` proves the accents are real; FIBA's own data model
    just cannot hold them.
  * Korean and Chinese players are given-name-first there ("Jihyun Park",
    "Manman Zhang"). We publish family-name-first, which is the convention
    these players are known by. `uniformName` ("PARK J H", "ZHANG") confirms
    which half is the family name, so this is not a guess.

So a player who JOINS keeps the name we already publish. FIBA's form is used
only for someone genuinely new, and that is reported so a human can check her
against Wikipedia before it ships. Six players whose FIBA registration is a
fuller or later name than ours — Şenyürek Arslan, Takács-Kiss, Steph Talbot —
are taken from FIBA by decision, and they are the ALIASES entries below.

── Height comes from CENTIMETRES ──────────────────────────────────────────
`heightInFeetInches` is floor-truncated from `heightInCm` — verified on all
192 players of this field, no exceptions. 192cm renders as 6'3" where the
honest round is 6'4". Converting from cm with a real round agrees with
Wikipedia on 120 of 151 shared players; taking FIBA's own string agrees on 69.
The truncation is a systematic one-inch understatement and we do not ship it.

── The capture stays verbatim ─────────────────────────────────────────────
`fiba-rosters-2026-09-03.json` is byte-for-byte what FIBA served and is never
edited. Corrections live in `overrides-fiba-2026-09-03.tsv` and are applied on
top, because a source you have quietly corrected can no longer be re-captured
and diffed against the next capture. That is the whole reason for two files.

── Joining ────────────────────────────────────────────────────────────────
Diacritic-folded exact match, then folded token-set, then a surname-anchored
order swap for the given-name-first sources, then an explicit alias table.
Nothing else. A different surname NEVER auto-joins. Every alias carries its
reason. Anything ambiguous raises — the join is the part of this that is cheap
to get wrong and expensive to notice, and after this run it never has to be
done again, because `person_id` is stored.

Usage:
    .venv/bin/python sites/wwc/ingest_squads.py            # rewrite the JSON
    .venv/bin/python sites/wwc/ingest_squads.py --dry-run  # report only
"""

import argparse
import csv
import json
import sys
import unicodedata
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF = HERE / "reference"
TEAMS_JSON = REF / "wwc2026_teams.json"
CAPTURE = REF / "fiba-rosters-2026-09-03.json"
OVERRIDES = REF / "overrides-fiba-2026-09-03.tsv"

SCHEMA_VERSION = "3.0.0"
GENERATED = "2026-09-03"

#: Ages are quoted as of the tournament's first day, which is also the basis
#: the Wikipedia table used, so the two remain comparable. Stored as a number,
#: so it is wrong the moment a player has a birthday mid-tournament — that is
#: true of every "age" column in the sport and is why the DOB is kept too.
AGE_AS_OF = date(2026, 9, 4)

#: FIBA's long position names -> the short codes the JSON and the table
#: already use. FIBA has no combined position, so `G/F` and `F/C` survive only
#: on rows FIBA does not cover — today, none.
POSITIONS = {
    "Point Guard": "PG", "Shooting Guard": "SG", "Small Forward": "SF",
    "Power Forward": "PF", "Center": "C", "Guard": "G", "Forward": "F",
}

#: Characters that carry no combining mark to strip, so NFD does not fold
#: them. Turkish dotless ı is the one that actually bit: without it "Olcay
#: Çakır" and FIBA's "Olcay Cakir" do not join and she reads as a cut player
#: plus a new signing on the same roster.
TRANSLIT = str.maketrans({
    "ı": "i", "İ": "i", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
    "ł": "l", "Ł": "L", "ß": "ss", "æ": "ae", "œ": "oe",
    "ʼ": "", "’": "", "'": "",
})

#: FIBA's spelling -> the name already in the JSON, for pairs no matcher
#: should join. Each is a real person whose two sources differ by more than
#: encoding, and each carries its reason. A matcher loose enough to catch
#: these would sooner or later merge two different people — Korea alone has
#: two Kangs, two Lees and two Parks in this field.
#:
#: `ALIASES` only says "these two records are the same player". Which spelling
#: gets PUBLISHED is a separate question, settled by `PUBLISH_FIBA_NAME`.
ALIASES = {
    ("ESP", "Megan Gustafson"): "Megan DiLeo",
    # ü -> ue is a transliteration, not a diacritic to strip, so no fold
    # joins these two. The German federation uses both.
    ("GER", "Marie Guelich"): "Marie Gülich",
    ("FRA", "Migna Toure"): "Mamignan Touré",
    ("HUN", "Virag Takacs-Kiss"): "Virág Kiss",
    ("AUS", "Steph Talbot"): "Stephanie Talbot",
    ("TUR", "Tilbe Senyurek Arslan"): "Tilbe Şenyürek",
    ("NGR", "Pallas Kunaiyi"): "Pallas Kunaiyi-Akpanah",
    # Wikipedia's "Roukia"/"Kamita" against FIBA's "Rokia"/"Kamite
    # Elisabeth". Same club, same date of birth, same shirt in both captures.
    ("MLI", "Rokia Doumbia"): "Roukia Doumbia",
    ("MLI", "Kamite Elisabeth Dabou"): "Kamita Dabou",
}

#: The six players we publish under FIBA's form rather than our own, decided
#: by Jason on 2026-09-03: FIBA's is the name on the official roster and on
#: the broadcast graphic, and in each of these cases it is also the player's
#: own current name — a married name added (Şenyürek Arslan, Takács-Kiss), a
#: short form she goes by (Steph Talbot), or the form her federation uses.
#: Diacritics are restored here by hand, because FIBA's field cannot carry
#: them and `uniformName` proves they belong.
#:
#: NOT a seventh entry: Megan Gustafson. FIBA registers her under her maiden
#: name; she has played as DiLeo since 2023 and the WNBA, ESPN and our own
#: player page all use it. That override lives in the TSV, where its reason
#: is visible to anyone reading the corrections rather than the code.
PUBLISH_FIBA_NAME = {
    ("GER", "Marie Guelich"): "Marie Gülich",       # unchanged; ours already
    ("FRA", "Migna Toure"): "Migna Touré",
    ("HUN", "Virag Takacs-Kiss"): "Virág Takács-Kiss",
    ("AUS", "Steph Talbot"): "Steph Talbot",
    ("TUR", "Tilbe Senyurek Arslan"): "Tilbe Şenyürek Arslan",
    ("NGR", "Pallas Kunaiyi"): "Pallas Kunaiyi",
}


def norm(s):
    """NFC, no non-breaking spaces, collapsed whitespace.

    U+00A0 is not cosmetic: it broke every name join in the 2026-09-02 data,
    and it is invisible in every tool that would show you the string. FIBA's
    capture adds its own variant of the same hazard — several `lastName`
    values arrive with trailing spaces — which this also absorbs.
    """
    s = unicodedata.normalize("NFC", str(s))
    for ch in (" ", " ", " "):
        s = s.replace(ch, " ")
    return " ".join(s.split())


def fold(s):
    """Accent-, case- and punctuation-insensitive form, for JOINING only.

    Never printed. `Luisa Geiselsöder` and FIBA's `Luisa Geiselsoder` fold
    together; `Marie Gülich` and `Marie Guelich` deliberately do not.
    """
    s = norm(s).translate(TRANSLIT)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().replace("-", " ").replace(".", " ").split())


def tokens(name):
    """Folded word set. Joins `Jin An` to `An Jin` and nothing riskier."""
    return frozenset(fold(name).split())


def fiba_name(p):
    return norm(f'{p.get("firstName", "")} {p.get("lastName", "")}')


def height(cm):
    """`heightInCm` -> `5 ft 9 in`, ROUNDED.

    Not FIBA's `heightInFeetInches`, which floors and so understates by up to
    an inch on 123 of the 192 players in this field. See the module docstring.
    """
    if not cm:
        return None
    total = round(cm / 2.54)
    return f"{total // 12} ft {total % 12} in"


def age_on(dob, as_of=AGE_AS_OF):
    if not dob:
        return None
    b = date.fromisoformat(str(dob)[:10])
    return as_of.year - b.year - ((as_of.month, as_of.day) < (b.month, b.day))


OVERRIDE_COLUMNS = ["team", "name", "field", "value", "reason"]


def read_overrides():
    """(team, FIBA name) -> {field: value}. Reasons are for the human.

    Keyed on FIBA's spelling because that is the key the capture joins on —
    an override keyed on OUR name could not correct our name.
    """
    lines = [l for l in OVERRIDES.read_text(encoding="utf-8").splitlines()
             if not l.startswith("#") and l.strip()]
    out = {}
    # `fieldnames` explicitly: the columns are documented in the header
    # COMMENT rather than in a header row, and DictReader would otherwise
    # silently eat the first correction as a header.
    for r in csv.DictReader(lines, fieldnames=OVERRIDE_COLUMNS, delimiter="\t"):
        out.setdefault((r["team"], norm(r["name"])), {})[r["field"]] = norm(r["value"])
    return out


def join_squad(code, json_players, capture_rows, log):
    """Pair capture rows to existing JSON roster entries.

    Returns `[(capture_row, json_index_or_None)]` in capture order, plus the
    indices left over. Unlike the 2026-09-02 version this does NOT raise on
    leftovers: a leftover now means a player was CUT, which is a fact about
    the tournament rather than a broken join. It still raises on AMBIGUITY,
    which is the case where guessing does damage.
    """
    remaining = {i: p for i, p in enumerate(json_players)}
    paired = []

    def match(key, want):
        hits = [i for i in remaining if key(remaining[i]["name"]) == want]
        if len(hits) > 1:
            raise SystemExit(
                f"{code}: {want!r} matches {len(hits)} existing roster rows "
                f"— refusing to guess")
        return hits[0] if hits else None

    for row in capture_rows:
        name = fiba_name(row)
        first, last = norm(row.get("firstName")), norm(row.get("lastName"))
        idx, how = None, None

        idx, how = match(fold, fold(name)), "exact"
        if idx is None:
            idx, how = match(tokens, tokens(name)), "token-set"
        if idx is None:
            # Surname-anchored order swap, for the given-name-first sources.
            # Strict on BOTH halves: the surname must match exactly and the
            # remaining tokens must concatenate to exactly the given name. It
            # cannot merge Kang Lee-seul with Kang Yoo-lim, which is the
            # failure that would swap two players' ages and heights.
            want = (fold(last), fold(first))
            hits = [i for i in remaining
                    if (t := fold(remaining[i]["name"]).split())
                    and len(t) >= 2 and (t[0], "".join(t[1:])) == want]
            if len(hits) > 1:
                raise SystemExit(f"{code}: {name!r} order-swaps onto "
                                 f"{len(hits)} rows — refusing to guess")
            idx, how = (hits[0] if hits else None), "order-swap"
        if idx is None:
            alias = ALIASES.get((code, name))
            if alias:
                idx = match(fold, fold(alias))
                if idx is None:
                    raise SystemExit(
                        f"{code}: alias {name!r} -> {alias!r} matched nothing. "
                        f"The alias is stale — re-check it, do not delete it.")
                how = "alias"

        if idx is not None:
            if how != "exact":
                log(f"  {code}: {how} join {json_players[idx]['name']!r} "
                    f"<- FIBA {name!r}")
            remaining.pop(idx)
        else:
            log(f"  {code}: NEW  {name!r} #{norm(row.get('uniformNumber'))} "
                f"— not on our previous roster; FIBA's spelling is published "
                f"as-is, CHECK IT against Wikipedia")
        paired.append((row, idx))

    return paired, list(remaining)


def apply_team(t, capture_rows, overrides, log):
    """Rebuild one team's roster from the capture. Order is FIBA's order."""
    code = t["code"]
    old = t["roster"]["players"]
    paired, cut_idx = join_squad(code, old, capture_rows, log)

    cut_names = [old[i]["name"] for i in cut_idx]
    for name in cut_names:
        log(f"  {code}: CUT  {name!r} — on our roster, not on FIBA's final 12")

    players, renames = [], {}
    for row, idx in paired:
        prev = old[idx] if idx is not None else {}
        name = fiba_name(row)
        ov = overrides.get((code, name)) or {}

        def field(key, value):
            return ov.get(key, value)

        # THE name rule. Ours wins on a join, FIBA's only where we decided it
        # should or where the player is new. The override TSV beats both.
        published = PUBLISH_FIBA_NAME.get((code, name)) or prev.get("name") or name
        published = norm(field("name", published))
        if prev and published != norm(prev["name"]):
            log(f"  {code}: renamed {prev['name']!r} -> {published!r}")
            # A rename has to reach the curated `wnba.players` block in the
            # same breath, or `sync_wnba` looks her up under a name that no
            # longer exists and silently drops her badge. The assertion at the
            # end of main() catches it — that is how this line came to be
            # written — but catching a bug is not the same as not having one,
            # and there is exactly one place both spellings are in scope.
            renames[norm(prev["name"])] = published

        number = field("number", norm(row.get("uniformNumber"))) or None
        if prev and number != prev.get("number"):
            log(f"  {code}: {published} number {prev.get('number')!r} -> {number!r}")

        pos = norm(row.get("position"))
        if pos and pos not in POSITIONS:
            raise SystemExit(f"{code}: unmapped position {pos!r} — "
                             f"add it to POSITIONS")

        club = field("club", norm(row.get("clubName"))) or None
        players.append({
            "number": number,
            "name": published,
            "position": field("position", POSITIONS.get(pos)),
            "plays_for": {
                # type/wnba_team are filled by sync_wnba(), which runs for
                # every team afterwards.
                "type": None,
                "wnba_team": None,
                "club_name": club,
                # FIBA's club country is ALREADY a FIBA three-letter code, so
                # the COUNTRY_CODE map the Wikipedia ingest needed is gone.
                # That map was a standing hazard: it raised on any country it
                # had not seen, and this field alone added GRE, ISR, MNE and
                # POL over yesterday's capture.
                "club_country": field("club_country",
                                      norm(row.get("clubCountryFIBACode"))) or None,
                # A player with both a WNBA team and a club abroad. FIBA
                # carries ONE club, which is why this stays an override
                # (Kennedy Burke, 2026-09-03).
                "other_club": field("other_club", None) or None,
            },
            "player_slug": prev.get("player_slug"),
            "wnba": False,
            "age": (int(a) if (a := field("age", age_on(row.get("dateOfBirth"))))
                    is not None else None),
            "height": field("height", height(row.get("heightInCm"))),
            # The two fields this whole rewrite exists for. `person_id` is the
            # only stable identity a FIBA player has; `date_of_birth` is what
            # makes `age` reproducible instead of a number frozen at ingest.
            "person_id": row.get("personId"),
            "date_of_birth": str(row.get("dateOfBirth") or "")[:10] or None,
        })

    for p in t["wnba"]["players"]:
        if norm(p["name"]) in renames:
            p["name"] = renames[norm(p["name"])]

    t["roster"]["players"] = players
    t["roster"].update({
        "status": "final",
        "player_count": len(players),
        "source": f"FIBA official team page, captured {GENERATED} "
                  f"(reference/{CAPTURE.name})",
        "as_of": GENERATED,
        "announced": GENERATED,
    })
    return cut_names


def sync_wnba(t, cut_names, log):
    """Point every roster row's WNBA fields at the curated `wnba.players`,
    and move anyone CUT out of that list.

    Runs for ALL SIXTEEN teams, and this is the fix for the class of bug the
    2026-09-02 rebuild replaced: two denormalised copies of "is she in the
    WNBA" used to drift apart, and Hungary published "0 current players" above
    a table naming Dorka Juhász. There is ONE derivation, it happens here, and
    the assertion below is the regression test.

    New on 2026-09-03: a cut player is MOVED to `not_on_squad`, never deleted
    — the block's own note says `players` covers the travelling squad only and
    anyone connected to the country but not selected is kept. Mali's Aicha
    Coulibaly (Chicago Sky) is the case that made this necessary: she is still
    a current WNBA player and still Malian, and she is not going to Berlin.
    """
    w = t["wnba"]
    cut = {fold(n) for n in cut_names}
    staying = [p for p in w["players"] if fold(p["name"]) not in cut]
    for p in w["players"]:
        if fold(p["name"]) in cut:
            p = dict(p, note=(p.get("note") or "") + (" " if p.get("note") else "")
                     + f"Not selected for the WWC 2026 final twelve "
                       f"({GENERATED}).")
            w.setdefault("not_on_squad", []).append(p)
            log(f"  {t['code']}: moved {p['name']!r} to wnba.not_on_squad "
                f"— cut from the final 12")
    w["players"] = staying

    current = {fold(p["name"]): p for p in staying if p["status"] == "current"}
    for p in t["roster"]["players"]:
        rec = current.get(fold(p["name"]))
        pf = p["plays_for"]
        pf["type"] = "wnba" if rec else ("club" if pf.get("club_name") else None)
        # The TLA comes from the curated block, never from the capture: it is
        # what the badge prints and what the WNBA player pages key on.
        pf["wnba_team"] = rec["wnba_team"] if rec else None
        p["wnba"] = rec is not None

    w["current"] = len(current)
    w["former"] = sum(1 for p in staying if p["status"] == "former")
    w["drafted_only"] = sum(1 for p in staying if p["status"] == "drafted_only")
    w["total_connected"] = len(staying)
    w["roster_basis"] = "final_squad"
    w["as_of"] = GENERATED
    return len(current)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="report the joins and changes without writing")
    args = ap.parse_args()

    doc = json.loads(TEAMS_JSON.read_text(encoding="utf-8"))
    teams = {t["code"]: t for t in doc["teams"]}
    overrides = read_overrides()
    capture = json.loads(CAPTURE.read_text(encoding="utf-8"))

    unknown = set(capture["teams"]) - set(teams)
    if unknown:
        raise SystemExit(f"capture names teams not in the JSON: {sorted(unknown)}")
    missing = set(teams) - set(capture["teams"])
    if missing:
        raise SystemExit(
            f"capture is missing {sorted(missing)}. Every team must be "
            f"present: a partial ingest that half-updates the field on the eve "
            f"of the tournament is worse than no ingest at all.")

    log_lines = []
    log = log_lines.append
    cuts = {}
    for code, entry in capture["teams"].items():
        rows = [p for p in entry["roster"]["players"] if p.get("isOnFinalRoster")]
        if len(rows) != 12:
            raise SystemExit(
                f"{code}: capture has {len(rows)} players on the final roster, "
                f"not 12. Check the capture before loosening this — a team "
                f"that is genuinely short needs a decision, not a default.")
        cuts[code] = apply_team(teams[code], rows, overrides, log)

    for code, t in teams.items():
        n = sync_wnba(t, cuts[code], log)
        badges = sum(1 for p in t["roster"]["players"] if p["wnba"])
        # The regression test for the Hungary bug, asserted where the data is
        # written rather than where it is rendered.
        if badges != n or n != t["wnba"]["current"]:
            raise SystemExit(f"{code}: {badges} badge rows, {n} current in "
                             f"wnba.players, {t['wnba']['current']} in the "
                             f"headline count — these must be one number")
        ids = [p["person_id"] for p in t["roster"]["players"]]
        if len(set(ids)) != len(ids) or not all(ids):
            raise SystemExit(f"{code}: person_id is missing or duplicated. "
                             f"It is the join key to the box scores; a "
                             f"duplicate merges two players' stat lines.")

    unused = set(overrides) - {
        (code, fiba_name(p))
        for code, entry in capture["teams"].items()
        for p in entry["roster"]["players"]}
    if unused:
        raise SystemExit(f"overrides matched nobody in the capture: "
                         f"{sorted(unused)}")

    doc["_schema"]["version"] = SCHEMA_VERSION
    doc["_schema"]["generated"] = GENERATED
    doc["_schema"]["nulls"] = (
        "null = exists, not researched. [] = researched, genuinely none. Never "
        "write a profile claim off a null. Since 2026-09-03 every roster row on "
        "every team carries number, name, position, club, club_country, age, "
        "height, person_id and date_of_birth, because the official FIBA capture "
        "covers all sixteen teams — the per-team column gating in the emitter "
        "is therefore currently a no-op, and is KEPT because the next event's "
        "capture will not be this complete. plays_for.other_club stays null "
        "except where an override supplies it.")
    doc["_schema"]["roster_rule"] = (
        "The roster is FIBA's `isOnFinalRoster` twelve, read from the official "
        "team page — not a count inferred from a source's label. person_id is "
        "the primary key of a roster row and the join to the box scores; name "
        "is a display field and must never be used as an identity. FIBA's own "
        "firstName/lastName are ASCII-stripped and give Korean and Chinese "
        "players given-name-first, so the names published here deliberately "
        "differ from the capture. See ingest_squads.py.")
    doc["_corrections"] = {
        "roster.source": (
            "Replaced the Wikipedia squads transcription with FIBA's own team "
            "pages on 2026-09-03, the day FIBA confirmed the sixteen rosters. "
            "This resolved the two teams the Wikipedia table never covered: "
            "Nigeria was carrying a 17-name pool inferred from nationality and "
            "camp invitations, Puerto Rico a squad with no numbers, ages or "
            "heights. Both are now the official twelve."),
        "roster.cuts": (
            "Mali went from a 22-name preselection to twelve and Nigeria from "
            "seventeen; between them seventeen players came off. Aicha "
            "Coulibaly (Chicago Sky) is the one that changes a published "
            "number — Mali now brings one current WNBA player, not two. She is "
            "in wnba.not_on_squad, not deleted."),
        "roster.height": (
            "Heights are converted from FIBA's heightInCm with a real round. "
            "FIBA's own heightInFeetInches field is FLOOR-truncated — true for "
            "all 192 players of this field — and understates by up to an inch: "
            "192cm renders 6'3\" where the honest round is 6'4\". Rounding from "
            "cm agrees with Wikipedia on 120 of 151 shared players; FIBA's "
            "string agrees on 69."),
        "roster.names": (
            "FIBA's firstName/lastName carry no diacritics and order Korean and "
            "Chinese names given-name-first. A player who joins an existing row "
            "keeps the name already published (Marine Johannès, Park Ji-hyun, "
            "Zhang Ziyu); six are published under FIBA's fuller registration by "
            "decision (Şenyürek Arslan, Takács-Kiss, Steph Talbot, Migna Touré, "
            "Pallas Kunaiyi, and Marie Gülich unchanged). Megan Gustafson is "
            "deliberately NOT among them — FIBA registers her maiden name and "
            "she has played as DiLeo since 2023. See overrides-fiba-2026-09-03.tsv."),
    }

    print("\n".join(log_lines) or "  (no changes)")
    n = sum(len(t["roster"]["players"]) for t in doc["teams"])
    if args.dry_run:
        print(f"DRY RUN — {TEAMS_JSON} not written")
        return
    TEAMS_JSON.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\ningested {n} players across {len(doc['teams'])} teams "
          f"-> {TEAMS_JSON.name} (schema {SCHEMA_VERSION})")


if __name__ == "__main__":
    main()
