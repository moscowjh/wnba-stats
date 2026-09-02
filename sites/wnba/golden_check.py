#!/usr/bin/env python3
"""golden_check.py — golden-file harness for the live build outputs.

Guards the two surfaces the daily build publishes — `index.html` and
`social_payload.json` — through refactors that are supposed to change
nothing (the sag.render chrome extraction being the motivating one).

The inputs are a PINNED snapshot, frozen once under sites/wnba/golden/
(gitignored — the snapshot is a local dev tool, and this public repo
deliberately commits no fetched CSVs). Never diff a fresh local build
against the live site: the data CSVs drift day to day, and a harness
that cries wolf in week one is a harness nobody trusts in week two.

Determinism: the build's only wall-clock reads go through
build_stats_page.today_et(), which honors SAG_TODAY. Freeze records
max(game_date)+1 as the snapshot's "today" — the morning-build shape,
so the Games tab's yesterday-finals path and the social factoid's
last-night gate are both exercised. The payload's `generated_utc` is
genuinely wall-clock metadata and is masked out of the diff.

Usage:
    .venv/bin/python sites/wnba/golden_check.py freeze   # pin snapshot + record goldens
    .venv/bin/python sites/wnba/golden_check.py check    # re-render snapshot, diff against goldens

`check` exits non-zero on any difference and writes the offending
outputs next to the goldens as *.actual for inspection.
"""

import argparse
import difflib
import json
import os
import shutil
import sys
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent
GOLDEN = SITE_DIR / "golden"
SNAP_DATA = GOLDEN / "data"
EXPECTED = GOLDEN / "expected"
META = GOLDEN / "meta.json"

# Everything build_stats_page.main() reads. pbp is fetched but not read by
# this builder, so it is not snapshotted. player_bios feeds the player-page
# builder and rides along so the harness can grow to cover those pages.
SNAPSHOT_FILES = [
    "player_box_2026.csv",
    "team_box_2026.csv",
    "linescores_2026.json",
    "schedule_today.json",
    "player_bios_2026.json",
]


def _build(out_site_dir, sag_today):
    """Run build_stats_page.main() against the snapshot, writing into
    out_site_dir instead of the real site directory. All of the builder's
    paths resolve through its module-level WNBA config at call time, so
    rebinding that one name redirects every read and write."""
    import dataclasses

    import pandas  # noqa: F401 — fail here, clearly, if the venv is wrong

    os.environ["SAG_TODAY"] = sag_today
    sys.path.insert(0, str(SITE_DIR))
    import build_stats_page as bsp
    from config import WNBA

    # The real league config with only the paths redirected — never a
    # hand-built copy, which would silently drop any field added later
    # (cf_analytics_token nearly slipped through exactly that way).
    cfg = dataclasses.replace(
        WNBA, site_dir=Path(out_site_dir), data_dir_override=SNAP_DATA)
    bsp.WNBA = cfg
    bsp.OUTPUT = cfg.page_output

    # og.png is an INPUT to the page now, not just a served asset: since
    # 2026-09-02 `seo.social_tags` gates the og:image/twitter:image block on
    # `cfg.og_image.exists()`. Redirecting site_dir moves public_dir with it,
    # so without this copy the harness renders the NO-IMAGE variant
    # (twitter:card=summary) while production renders the card — and the
    # golden would faithfully freeze a page that production never emits.
    # Exactly why SNAP_DATA exists: the harness supplies inputs, it does not
    # fake them.
    if WNBA.og_image.exists():
        cfg.public_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WNBA.og_image, cfg.og_image)

    bsp.main()
    return cfg.page_output, cfg.social_payload


def _masked_payload(path):
    """social_payload.json minus its wall-clock metadata."""
    data = json.loads(Path(path).read_text())
    data.pop("generated_utc", None)
    return json.dumps(data, ensure_ascii=False, indent=2)


def freeze():
    import pandas as pd

    if EXPECTED.exists():
        print(f"Refusing to overwrite existing goldens at {EXPECTED}.")
        print("Delete sites/wnba/golden/ first if you mean to re-pin.")
        return 1

    SNAP_DATA.mkdir(parents=True, exist_ok=True)
    EXPECTED.mkdir(parents=True, exist_ok=True)
    data_dir = SITE_DIR / "data"
    for name in SNAPSHOT_FILES:
        src = data_dir / name
        if not src.exists():
            print(f"Missing input {src} — run fetch_data.py first.")
            return 1
        shutil.copy2(src, SNAP_DATA / name)

    latest = pd.to_datetime(
        pd.read_csv(SNAP_DATA / "player_box_2026.csv")["game_date"]).max()
    sag_today = (latest + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    out = GOLDEN / "out"
    page, payload = _build(out, sag_today)
    shutil.copy2(page, EXPECTED / "index.html")
    shutil.copy2(payload, EXPECTED / "social_payload.json")
    META.write_text(json.dumps(
        {"sag_today": sag_today, "data_through": str(latest.date())},
        indent=2))
    print(f"Goldens pinned: data through {latest.date()}, "
          f"SAG_TODAY={sag_today}")
    print(f"  {EXPECTED / 'index.html'}")
    print(f"  {EXPECTED / 'social_payload.json'}")
    return 0


def check():
    if not META.exists():
        print("No goldens pinned — run `golden_check.py freeze` first.")
        return 1
    sag_today = json.loads(META.read_text())["sag_today"]

    out = GOLDEN / "out"
    page, payload = _build(out, sag_today)

    failures = []

    exp_html = (EXPECTED / "index.html").read_text()
    got_html = Path(page).read_text()
    if got_html != exp_html:
        failures.append("index.html")
        (EXPECTED / "index.html.actual").write_text(got_html)
        diff = list(difflib.unified_diff(
            exp_html.splitlines(), got_html.splitlines(),
            "expected/index.html", "actual", lineterm=""))
        print("\n".join(diff[:60]))
        if len(diff) > 60:
            print(f"... ({len(diff) - 60} more diff lines)")

    exp_pl = _masked_payload(EXPECTED / "social_payload.json")
    got_pl = _masked_payload(payload)
    if got_pl != exp_pl:
        failures.append("social_payload.json")
        (EXPECTED / "social_payload.json.actual").write_text(got_pl)
        print("\n".join(difflib.unified_diff(
            exp_pl.splitlines(), got_pl.splitlines(),
            "expected/social_payload.json (generated_utc masked)", "actual",
            lineterm="")))

    if failures:
        print(f"\nGOLDEN CHECK FAILED: {', '.join(failures)} changed.")
        print("If the change is intended, delete sites/wnba/golden/ and re-freeze.")
        return 1
    print("Golden check passed: index.html and social_payload.json "
          "byte-identical to the pinned render.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["freeze", "check"])
    args = ap.parse_args()
    sys.exit(freeze() if args.mode == "freeze" else check())


if __name__ == "__main__":
    main()
