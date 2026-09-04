#!/usr/bin/env python3
"""fetch_rosters.py — capture the OFFICIAL FIBA rosters, verbatim.

NOT part of the build. Run by hand; `ingest_squads.py` folds the capture into
`reference/wwc2026_teams.json`, and the emitter reads only that JSON.

── Why this replaced the Wikipedia capture (2026-09-03) ───────────────────
FIBA published the confirmed 16 rosters the day before tip. The team pages
carry them in the page's own flight payload as structured data, which is
better than the Wikipedia squads table on every axis that matters:

  * It is the SOURCE. Wikipedia was a transcription of it.
  * It covers Nigeria and Puerto Rico, which the Wikipedia table never did —
    those two teams had been carrying a 17-name inferred pool and a squad
    with no numbers, ages or heights at all.
  * It carries `personId`, and that is the whole difference. Every other FIBA
    surface we parse is keyed on it, so a box-score line can now be resolved
    to a rostered player by ID instead of by matching her name.
  * It carries `isOnFinalRoster` and `finalRosterMemberStatusCode`, so
    "final twelve" is a field we read rather than a count we assume.

── The capture is VERBATIM and is never edited ────────────────────────────
Same discipline as `wikipedia-squads-2026-09-03.tsv`: corrections live in an
overrides file and are applied on top, because a source you have quietly
corrected can no longer be re-captured and diffed against the next capture.
`parse_roster` deliberately returns FIBA's dict unprojected for the same
reason — a capture that has already dropped a field cannot be re-read.

── What this file does NOT decide ─────────────────────────────────────────
Anything editorial. FIBA's `firstName`/`lastName` are ASCII-stripped (every
diacritic in the field is gone: Johannès, Juhász, the whole Czech squad) and
Korean and Chinese players are given-name-first. Those are display questions
and they are settled in `ingest_squads.py`, against the names already
published. This script writes down what FIBA said.

Usage:
    .venv/bin/python sites/wwc/fetch_rosters.py
    .venv/bin/python sites/wwc/fetch_rosters.py --out PATH
"""

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

from sag.adapters import fiba

HERE = Path(__file__).resolve().parent
REF = HERE / "reference"

#: From `tournament.source` in wwc2026_teams.json — the same slug
#: `fetch_data.py` uses.
EVENT_SLUG = "fiba-womens-basketball-world-cup-2026"

#: Our team code -> FIBA's URL slug. Hand-written and checked, not derived
#: from the team name: `turkiye`, `puerto-rico`, `korea` and `czechia` all
#: differ from a slugified `name`, and a 404 here is a team silently missing
#: from the capture rather than a loud failure.
TEAM_SLUGS = {
    "JPN": "japan", "ESP": "spain", "GER": "germany", "MLI": "mali",
    "HUN": "hungary", "KOR": "korea", "NGR": "nigeria", "FRA": "france",
    "BEL": "belgium", "AUS": "australia", "PUR": "puerto-rico",
    "TUR": "turkiye", "USA": "usa", "CZE": "czechia", "ITA": "italy",
    "CHN": "china",
}


def capture(code, slug, log):
    url = fiba.team_url(EVENT_SLUG, slug)
    roster = fiba.parse_roster(fiba.flight_payload(fiba.fetch(url)))
    if not roster or not roster.get("players"):
        raise SystemExit(
            f"{code}: no roster block at {url}. FIBA's page structure may "
            f"have changed — check the anchor in `fiba.parse_roster` before "
            f"assuming the roster is unpublished.")
    players = roster["players"]
    final = [p for p in players if p.get("isOnFinalRoster")]
    log(f"  {code}: {len(players)} players, {len(final)} on the final roster "
        f"({', '.join(sorted({p.get('finalRosterMemberStatusCode') or '?' for p in players}))})")
    return {"source_url": url, "roster": roster}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path,
                    default=REF / f"fiba-rosters-{date.today()}.json",
                    help="capture path (default: reference/fiba-rosters-TODAY.json)")
    args = ap.parse_args()

    if args.out.exists():
        raise SystemExit(
            f"{args.out} already exists. A capture is a dated snapshot and is "
            f"never overwritten — delete it deliberately, or pass --out.")

    out, log = {}, print
    log(f"capturing {len(TEAM_SLUGS)} rosters from {fiba.FIBA_ORIGIN}")
    for code, slug in TEAM_SLUGS.items():
        out[code] = capture(code, slug, log)
        # Sixteen sequential page fetches against a public site on the busiest
        # day of its year. There is no hurry here and no reason to be rude.
        time.sleep(0.7)

    doc = {
        "_capture": {
            "source": "fiba.basketball team pages, flight payload "
                      "(`roster` block), via sag.adapters.fiba.parse_roster",
            "event": EVENT_SLUG,
            "captured": str(date.today()),
            "verbatim": "FIBA's own player dicts, unprojected. NEVER edit this "
                        "file — corrections belong in an overrides TSV so that "
                        "the next capture can still be diffed against this one.",
            "caveats": [
                "firstName/lastName are ASCII-stripped: every diacritic is "
                "gone. uniformName keeps them, uppercased.",
                "Korean and Chinese players are given-name-first here.",
                "heightInFeetInches is FLOOR-truncated from heightInCm and "
                "understates by up to an inch. Convert from cm.",
            ],
        },
        "teams": out,
    }
    args.out.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(len(t["roster"]["players"]) for t in out.values())
    log(f"wrote {n} players across {len(out)} teams -> {args.out}")


if __name__ == "__main__":
    main()
