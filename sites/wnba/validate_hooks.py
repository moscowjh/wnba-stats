#!/usr/bin/env python3
"""validate_hooks.py — asserts the Tier 1 editorial contract in hooks.json.

Same pattern as validate_coaches.py: a hand-maintained reference file gets a
machine check, because "a human proofread it once" does not survive edits.

Asserted, per live entry (underscore-prefixed keys are metadata):
  1. The slug maps to a rendered player page — an entry for a player who no
     longer renders is stale editorial nobody will ever see or re-verify.
  2. `sentence` is a non-empty string.
  3. `sources` is a LIST with >=1 item, each carrying a `url` and a
     non-empty verbatim `quote` (the quote is what survives link rot).
  4. `falsifiable_by_game` is exactly false — the author's explicit
     assertion that NO game, in any league at any level, can make the
     sentence untrue. (A college record falls to a college game.)
  5. `date_written` is present.
  6. The slug does not also sit in `_rejected` — being live and rejected
     at once is a contradiction someone needs to resolve.

Run:  .venv/bin/python sites/wnba/validate_hooks.py
Exits non-zero on any failure. Needs the season data (for rendered slugs),
so in CI it runs after the fetch step.
"""

import json
import sys

from sag import seo

import build_stats_page as bsp
from config import WNBA

HOOKS_PATH = WNBA.site_dir / "reference" / "hooks.json"


def main():
    data = json.loads(HOOKS_PATH.read_text())
    entries = {k: v for k, v in data.items() if not k.startswith("_")}
    rejected = set(data.get("_rejected", {})) - {"note"}

    player_raw, _ = bsp.load_data()
    season = bsp.compute_player_season(player_raw)
    rendered = set(season["athlete_display_name"].map(seo.slugify))

    failures = []

    def check(cond, slug, msg):
        if not cond:
            failures.append(f"{slug}: {msg}")

    for slug, e in entries.items():
        check(slug in rendered, slug,
              "no rendered player page for this slug — stale entry?")
        check(isinstance(e.get("sentence"), str) and e["sentence"].strip(),
              slug, "missing or empty `sentence`")
        sources = e.get("sources")
        check(isinstance(sources, list) and len(sources) >= 1, slug,
              "`sources` must be a list with at least one source")
        for i, s in enumerate(sources or []):
            check(bool(s.get("url")), slug, f"source[{i}] has no url")
            check(isinstance(s.get("quote"), str) and s["quote"].strip(),
                  slug, f"source[{i}] has no verbatim quote")
        check(e.get("falsifiable_by_game") is False, slug,
              "`falsifiable_by_game` must be exactly false — absent or true "
              "means the sentence may not ship")
        check(bool(e.get("date_written")), slug, "missing `date_written`")
        check(slug not in rejected, slug,
              "slug appears in both the live entries and _rejected")

    if failures:
        print(f"HOOKS VALIDATION FAILED ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        sys.exit(1)
    print(f"hooks.json OK: {len(entries)} live "
          f"entr{'y' if len(entries) == 1 else 'ies'}, "
          f"{len(rejected)} rejected on record, all contracts hold.")


if __name__ == "__main__":
    main()
