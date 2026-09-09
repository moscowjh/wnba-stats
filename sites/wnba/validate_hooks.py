#!/usr/bin/env python3
"""validate_hooks.py — asserts the Tier 1 editorial contract in hooks.json.

Same pattern as validate_coaches.py: a hand-maintained reference file gets a
machine check, because "a human proofread it once" does not survive edits.

Asserted, per live entry (underscore-prefixed keys are metadata):
  1. The slug maps to a rendered player page — an entry for a player who no
     longer renders is stale editorial nobody will ever see or re-verify.
  2. At least one of `sentence` (the one-line Tier 1 slot) or `paragraph`
     (the longer block added 2026-09-08) is a non-empty string; any field
     that IS present is non-empty.
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
        # A live entry must carry SOMETHING to render: the one-line Tier 1
        # `sentence`, the longer `paragraph` added 2026-09-08 for the indexing
        # test, or both. An entry with neither is a sourced claim nobody sees.
        has_sentence = isinstance(e.get("sentence"), str) and e["sentence"].strip()
        has_paragraph = isinstance(e.get("paragraph"), str) and e["paragraph"].strip()
        check(has_sentence or has_paragraph, slug,
              "entry has neither a `sentence` nor a `paragraph`")
        for field in ("sentence", "paragraph"):
            if field in e:
                check(isinstance(e[field], str) and e[field].strip(), slug,
                      f"`{field}` present but empty")
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
