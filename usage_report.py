#!/usr/bin/env python3
"""Query the wnba-usage-tracker beacon data (Workers Analytics Engine).

Replaces the hand-typed curl documented in DEPLOY.md. The question it exists to
answer: **is Bluesky driving site traffic, or only engagement on the posts?**

Design notes (see USAGE-TRACKER-HANDOFF.md):

- **Every count uses ``SUM(_sample_interval)``, never ``count()``.** Analytics
  Engine samples at volume and records the inverse sample rate per row;
  ``count()`` silently under-counts once sampling kicks in. It already has:
  on 2026-08-04 the all-time totals were 841 (true) vs 816 (raw), a 3% gap.
  The ``count()`` example in DEPLOY.md and the worker header was wrong.
- **Sources are never filtered to a known list.** Unrecognized values (old test
  pings like ``deploytest``/``probe``, or a future tagging bug) show up in the
  output rather than being silently dropped.
- **Retention is 90 days** (Analytics Engine limit). Collection started
  2026-07-12, so July data begins falling off the back around mid-October —
  which is what ``--snapshot`` exists to beat.

Auth: ``CF_ACCOUNT_ID`` and ``CF_ANALYTICS_TOKEN`` from the environment,
falling back to a gitignored ``.env`` beside this script. The token needs
**Account Analytics: Read** and nothing else — do NOT reuse
``CLOUDFLARE_API_TOKEN``, which carries deploy permissions. THIS REPO IS
PUBLIC; the token value is never printed, not even on error.

Usage:
  python usage_report.py                      # last 7 days
  python usage_report.py --days 30
  python usage_report.py --since 2026-07-12
  python usage_report.py --json               # machine-readable
  python usage_report.py --snapshot           # append yesterday (UTC) to
                                              # usage_history.jsonl, idempotent
  python usage_report.py --snapshot --date 2026-08-03   # backfill one day

All times are UTC — the beacon timestamps are UTC and the daily build runs
~11:17 UTC, so "yesterday UTC" is always a fully closed day by then.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
HISTORY_PATH = HERE / "usage_history.jsonl"

DATASET = "wnba_usage"
API_BASE = "https://api.cloudflare.com/client/v4/accounts/{acct}/analytics_engine/sql"
TIMEOUT = (5, 30)          # (connect, read)
RETRY_SLEEP = 2

# Beacon schema, from analytics-worker/worker.js:
#   blob1 = event (pageview | tab | box)   blob2 = tab id / game:<id>
#   blob3 = source (utm_source, session-cached, else 'none')
#   blob4 = returning ('1' | '0')          double1 = 1
#   blob8 = referring hostname ('direct' | 'self' | host | ''), added 2026-08-11
#   blob5-7, blob9 reserved (P3 recency, P4 country/device/session)
#   blob10 = site ('wnba' | 'wwc' | 'ncaaw'), added 2026-08-05
COUNT = "SUM(_sample_interval)"

DEFAULT_SITE = "wnba"

# Tagged sources we deliberately emit. Anything else still gets reported —
# this list only controls presentation order.
KNOWN_SOURCES = ["bluesky-post", "bluesky-bio", "none"]


class ConfigError(Exception):
    """Missing/!invalid credentials — never carries a token value."""


class QueryError(Exception):
    """The Analytics Engine SQL API could not be queried."""


# ── Config ───────────────────────────────────────────────────────────────

def _load_env_file(path: Path) -> dict:
    """Parse a minimal KEY=VALUE .env. Real env vars win over the file (CI
    supplies them directly)."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def get_credentials() -> tuple[str, str]:
    """Return (account_id, token) or raise ConfigError with a message that
    never contains the token."""
    fromfile = _load_env_file(ENV_PATH)
    acct = os.environ.get("CF_ACCOUNT_ID") or fromfile.get("CF_ACCOUNT_ID", "")
    token = os.environ.get("CF_ANALYTICS_TOKEN") or fromfile.get("CF_ANALYTICS_TOKEN", "")
    missing = [n for n, v in (("CF_ACCOUNT_ID", acct), ("CF_ANALYTICS_TOKEN", token)) if not v]
    # Catch a half-filled template rather than sending a placeholder as a bearer
    # token and getting an opaque 400 back.
    placeholder = [n for n, v in (("CF_ACCOUNT_ID", acct), ("CF_ANALYTICS_TOKEN", token))
                   if v.startswith("paste-")]
    if missing or placeholder:
        bad = missing + placeholder
        raise ConfigError(
            f"Missing credentials: {', '.join(bad)}.\n"
            f"Set them in the environment, or in {ENV_PATH.name} beside this script "
            f"(gitignored).\n"
            f"The token must be a Cloudflare API token with 'Account Analytics: Read' "
            f"and nothing else — do not reuse CLOUDFLARE_API_TOKEN.")
    return acct, token


# ── Query ────────────────────────────────────────────────────────────────

def make_query(acct: str, token: str):
    """Return a sql(query) -> list[dict] callable with timeout + one retry."""
    url = API_BASE.format(acct=acct)
    headers = {"Authorization": f"Bearer {token}"}

    def sql(query: str) -> list[dict]:
        last = None
        for attempt in range(2):
            try:
                r = requests.post(url, headers=headers, data=query.encode("utf-8"),
                                  timeout=TIMEOUT)
                if r.ok:
                    return r.json().get("data", [])
                # Cloudflare reports a rejected token as HTTP *400* with error
                # code 9106 — not 401/403 — so status alone can't tell an auth
                # failure from a malformed query. Both bodies are token-free.
                body = r.text.strip()[:300]
                if r.status_code in (401, 403) or "Authentication failed" in body:
                    raise QueryError(
                        "the API token was rejected. Check it has 'Account Analytics: "
                        "Read' and that CF_ACCOUNT_ID names the account it was minted "
                        f"in. (Cloudflare said: {body})")
                if r.status_code == 422:
                    raise QueryError(f"the SQL was rejected: {body}")
                raise requests.HTTPError(f"HTTP {r.status_code}: {body}")
            except QueryError:
                raise           # auth / bad-SQL are deterministic — no retry
            except (requests.RequestException, ValueError) as e:
                last = e
                if attempt == 0:
                    time.sleep(RETRY_SLEEP)
        raise QueryError(f"Analytics Engine query failed: {last}")

    return sql


def _n(row: dict, key: str = "n") -> int:
    """AE returns UInt64 as a JSON string."""
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _window_clause(start: dt.date, end: dt.date | None = None) -> str:
    """[start 00:00:00, end 00:00:00) in UTC. end=None means 'up to now'."""
    clause = f"timestamp >= toDateTime('{start.isoformat()} 00:00:00')"
    if end is not None:
        clause += f" AND timestamp < toDateTime('{end.isoformat()} 00:00:00')"
    return clause


def _site_clause(site: str | None) -> str:
    """Restrict to one statsataglance property.

    site=None means every site (the cross-site funnel view). Otherwise filter
    blob10 — and for 'wnba' ALSO accept an empty blob10, because every row
    written before 2026-08-05 predates the site dimension and is WNBA by
    definition. Without that OR, adding this column would silently zero out a
    month of history, which is the exact class of silent breakage this tracker
    keeps getting bitten by."""
    if site is None:
        return "1 = 1"
    if site == DEFAULT_SITE:
        return f"(blob10 = '{site}' OR blob10 = '')"
    return f"blob10 = '{site}'"


def collect(sql, start: dt.date, end: dt.date | None,
            site: str | None = DEFAULT_SITE) -> dict:
    """Run the five queries backing the report. Returns raw aggregates."""
    w = f"{_window_clause(start, end)} AND {_site_clause(site)}"
    s = _site_clause(site)

    daily = sql(f"SELECT toDate(timestamp) AS d, {COUNT} AS n FROM {DATASET} "
                f"WHERE blob1 = 'pageview' AND {w} GROUP BY d ORDER BY d")
    by_event_source = sql(f"SELECT blob1 AS event, blob3 AS source, {COUNT} AS n "
                          f"FROM {DATASET} WHERE {w} GROUP BY event, source")
    tabs = sql(f"SELECT blob2 AS tab, {COUNT} AS n FROM {DATASET} "
               f"WHERE blob1 = 'tab' AND {w} GROUP BY tab ORDER BY n DESC")
    returning = sql(f"SELECT blob4 AS r, {COUNT} AS n FROM {DATASET} "
                    f"WHERE blob1 = 'pageview' AND {w} GROUP BY r")
    alltime_src = sql(f"SELECT blob3 AS source, {COUNT} AS n FROM {DATASET} "
                      f"WHERE blob1 = 'pageview' AND {s} GROUP BY source")
    referrers = sql(f"SELECT blob8 AS ref, {COUNT} AS n FROM {DATASET} "
                    f"WHERE blob1 = 'pageview' AND {w} GROUP BY ref ORDER BY n DESC")

    return {
        "daily": [(r["d"], _n(r)) for r in daily],
        "by_event_source": [(r["event"], r["source"], _n(r)) for r in by_event_source],
        "tabs": [(r["tab"], _n(r)) for r in tabs],
        "returning": {r["r"]: _n(r) for r in returning},
        "alltime_source": {r["source"]: _n(r) for r in alltime_src},
        "referrers": [(r["ref"], _n(r)) for r in referrers],
    }


# ── Shaping ──────────────────────────────────────────────────────────────

def _source_order(sources) -> list[str]:
    """Known sources first (stable presentation), then anything unexpected."""
    extra = sorted(s for s in sources if s not in KNOWN_SOURCES)
    return [s for s in KNOWN_SOURCES if s in sources] + extra


def build_report(raw: dict, start: dt.date, end: dt.date | None) -> dict:
    per_source: dict[str, dict[str, int]] = {}
    totals = {"pageview": 0, "tab": 0, "box": 0}
    for event, source, n in raw["by_event_source"]:
        # GROUP BY event, source already yields one row per pair.
        per_source.setdefault(source, {})[event] = n
        if event in totals:
            totals[event] += n

    ret = raw["returning"]
    returning_n, new_n = ret.get("1", 0), ret.get("0", 0)

    return {
        "window": {"start": start.isoformat(),
                   "end": (end.isoformat() if end else "now"),
                   "days": (((end or dt.datetime.now(dt.timezone.utc).date()) - start).days)},
        "totals": totals,
        "daily_pageviews": [{"date": d, "pageviews": n} for d, n in raw["daily"]],
        "by_source": {s: per_source[s] for s in _source_order(per_source)},
        "alltime_pageviews_by_source": raw["alltime_source"],
        "tabs": [{"tab": t, "events": n} for t, n in raw["tabs"]],
        "returning": {"returning": returning_n, "new": new_n},
        # '' is deliberately kept as its own row rather than merged into
        # 'direct'. It means "not collected" — a pre-2026-08-11 row, or a
        # cached page still running the old JS — and merging the two would
        # invent direct traffic that was never measured.
        "referrers": [{"referrer": r or "(not collected)", "pageviews": n}
                      for r, n in raw["referrers"]],
    }


# ── Output ───────────────────────────────────────────────────────────────

def _rate(num: int, den: int) -> str:
    return f"{num / den:.2f}" if den else "—"


def _pct(num: int, den: int) -> str:
    return f"{100 * num / den:.0f}%" if den else "—"


def print_report(rep: dict) -> None:
    w = rep["window"]
    t = rep["totals"]
    pv = t["pageview"]

    print(f"WNBA usage — {w['start']} → {w['end']} ({w['days']}d, UTC)")
    print(f"pageviews {pv}   tab events {t['tab']}   box opens {t['box']}")
    print("counts use SUM(_sample_interval) — sampling-corrected")

    print("\n── Daily pageviews " + "─" * 42)
    if not rep["daily_pageviews"]:
        print("  (no pageviews in window)")
    else:
        peak = max(r["pageviews"] for r in rep["daily_pageviews"]) or 1
        for r in rep["daily_pageviews"]:
            bar = "█" * max(1, round(24 * r["pageviews"] / peak))
            print(f"  {r['date']}  {r['pageviews']:>4}  {bar}")

    print("\n── Pageviews by source " + "─" * 38)
    print(f"  {'source':<16} {'window':>7} {'share':>6} {'all-time':>7} {'share':>4}")
    at = rep["alltime_pageviews_by_source"]
    at_total = sum(at.values())
    for src in _source_order(set(rep["by_source"]) | set(at)):
        n = rep["by_source"].get(src, {}).get("pageview", 0)
        print(f"  {src:<16} {n:>7} {_pct(n, pv):>6} "
              f"{at.get(src, 0):>6} {_pct(at.get(src, 0), at_total):>4}")

    print("\n── Tab engagement " + "─" * 43)
    if not rep["tabs"]:
        print("  (no tab events in window)")
    for r in rep["tabs"]:
        print(f"  {r['tab']:<16} {r['events']:>7}  {_rate(r['events'], pv)}/pageview")

    print("\n── Box scores " + "─" * 47)
    print(f"  opens {t['box']}   {_rate(t['box'], pv)}/pageview")

    print("\n── Depth by source " + "─" * 42)
    print("  does a visitor from each surface explore, or bounce?")
    print(f"  {'source':<16} {'views':>6} {'tabs/view':>10} {'boxes/view':>11}")
    for src, ev in rep["by_source"].items():
        s_pv = ev.get("pageview", 0)
        print(f"  {src:<16} {s_pv:>6} {_rate(ev.get('tab', 0), s_pv):>10} "
              f"{_rate(ev.get('box', 0), s_pv):>11}")
    thin = [s for s, ev in rep["by_source"].items() if 0 < ev.get("pageview", 0) < 30]
    if thin:
        print(f"  ! small sample ({', '.join(thin)}): a handful of visits is a "
              f"curiosity, not a trend.")

    print("\n── Referrers " + "─" * 48)
    print("  where traffic came from when it carried no utm tag")
    refs = rep["referrers"]
    if not refs or all(x["referrer"] == "(not collected)" for x in refs):
        print("  (no referrer data yet — added 2026-08-11; needs a deploy of")
        print("   both the page JS and analytics-worker/, then a day of traffic)")
    else:
        for x in refs:
            print(f"  {x['referrer']:<24} {x['pageviews']:>6} {_pct(x['pageviews'], pv):>5}")
        print("  ! 'direct' = no referrer sent, NOT 'typed the URL'. Privacy")
        print("    settings and in-app browsers strip it, so a low bsky.app")
        print("    count is not low Bluesky traffic — utm_source is the signal")
        print("    there. 'self' is in-site navigation; '(not collected)' is a")
        print("    row from before the field existed, or a cached old page.")

    r = rep["returning"]
    tot = r["returning"] + r["new"]
    print("\n── New vs returning " + "─" * 41)
    print(f"  returning {r['returning']} ({_pct(r['returning'], tot)})   "
          f"new {r['new']} ({_pct(r['new'], tot)})")
    print("  ! NOT a retention rate. The flag is set on first visit and never")
    print("    expires, so 'returning' means 'has ever opened the site in this")
    print("    browser' — its share only ever climbs. Fix is P3 in the handoff.")


# ── Snapshot (P2) ────────────────────────────────────────────────────────

def snapshot_row(rep: dict, day: dt.date) -> dict:
    """One compact JSON object per closed UTC day, for usage_history.jsonl."""
    return {
        "date": day.isoformat(),
        "pageviews": rep["totals"]["pageview"],
        "by_source": {s: ev.get("pageview", 0) for s, ev in rep["by_source"].items()},
        "tabs": {r["tab"]: r["events"] for r in rep["tabs"]},
        "box_opens": rep["totals"]["box"],
        "returning": rep["returning"]["returning"],
        "new": rep["returning"]["new"],
        # Added 2026-08-11. Rows written before this date simply lack the key;
        # any reader must treat a missing "referrers" as "not collected"
        # rather than as zero referred traffic.
        "referrers": {x["referrer"]: x["pageviews"] for x in rep["referrers"]},
    }


def existing_snapshot_dates(path: Path) -> set[str]:
    if not path.exists():
        return set()
    dates = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            dates.add(json.loads(line)["date"])
        except (ValueError, KeyError):
            continue        # a malformed line must not break idempotency
    return dates


# ── CLI ──────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7,
                    help="window length in days (default 7)")
    ap.add_argument("--since", type=_parse_date, default=None,
                    help="window start YYYY-MM-DD (overrides --days)")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="emit the report as JSON")
    ap.add_argument("--snapshot", action="store_true",
                    help="append one closed UTC day to usage_history.jsonl")
    ap.add_argument("--date", type=_parse_date, default=None,
                    help="with --snapshot: the day to record (default yesterday UTC)")
    ap.add_argument("--site", default=DEFAULT_SITE,
                    help="statsataglance property to report on "
                         f"(default {DEFAULT_SITE}); 'all' for every site combined")
    args = ap.parse_args()
    site = None if args.site == "all" else args.site

    try:
        acct, token = get_credentials()
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    sql = make_query(acct, token)
    today = dt.datetime.now(dt.timezone.utc).date()

    try:
        if args.snapshot:
            day = args.date or (today - dt.timedelta(days=1))
            if day >= today:
                print(f"error: {day} is not a closed UTC day yet.", file=sys.stderr)
                return 2
            if day.isoformat() in existing_snapshot_dates(HISTORY_PATH):
                print(f"{day} already recorded in {HISTORY_PATH.name} — nothing to do.")
                return 0
            raw = collect(sql, day, day + dt.timedelta(days=1), site)
            row = snapshot_row(build_report(raw, day, day + dt.timedelta(days=1)), day)
            row["site"] = args.site
            with HISTORY_PATH.open("a") as fh:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            print(f"recorded {day} → {HISTORY_PATH.name}: "
                  f"{row['pageviews']} pageviews, {row['box_opens']} box opens")
            return 0

        start = args.since or (today - dt.timedelta(days=args.days))
        rep = build_report(collect(sql, start, None, site), start, None)
        if args.as_json:
            print(json.dumps(rep, indent=2))
        else:
            print_report(rep)
        return 0
    except QueryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
