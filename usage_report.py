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
  python usage_report.py --hourly             # today, hour by hour, with
                                              # posts.csv markers
  python usage_report.py --hourly --date 2026-08-18
  python usage_report.py --days 30 --include-owner      # keep our own testing

Our own testing traffic is EXCLUDED by default. Load the site through the
bookmarked ``?utm_source=owner`` URL when poking at it and those rows tag
themselves; every run says how many it set aside.

All times are UTC — the beacon timestamps are UTC and the daily build runs
~11:17 UTC, so "yesterday UTC" is always a fully closed day by then.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import statistics
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
HISTORY_PATH = HERE / "usage_history.jsonl"

DATASET = "wnba_usage"
API_BASE = "https://api.cloudflare.com/client/v4/accounts/{acct}/analytics_engine/sql"
TIMEOUT = (5, 30)          # (connect, read)
RETRY_SLEEP = 2

# Beacon schema, from workers/analytics/worker.js:
#   blob1 = event (pageview | tab | box | expand)
#   blob2 = tab id / game:<id> / page key on a pageview or expand
#           ('' = the tab site, 'players', 'player:<slug>'), added 2026-08-17
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

# Our own visits, self-identified. Not part of the utm_source taxonomy in
# DEPLOY.md on purpose: those four values are surfaces we publish TO, this one
# is a surface we arrive FROM, and it exists to be subtracted rather than
# analysed. Set by loading the site through a bookmarked
# ``?utm_source=owner`` URL; the page JS caches it in sessionStorage, so
# entering through the bookmark tags the whole visit including player pages.
OWNER_SOURCE = "owner"

# posts.csv holds local wall-clock times because that is when a human posts.
POST_LOG_TZ = "America/New_York"

# The posting log lives under sites/<slug>/reference/ rather than beside this
# script, because .gitignore's rule is provenance, not extension: *.csv is
# ignored as a build artifact, and reference/ is the carve-out for files a
# human types and that have no upstream to re-fetch them from. At the repo
# root the log would be silently UNTRACKED — invisible even to git status —
# which for the one file recording why traffic moved is the worst possible
# failure mode. This path needs no .gitignore change.
POSTS_DIRNAME = "reference"
POSTS_FILENAME = "posts.csv"

# How long after a post we still count arrivals alongside it. Three hours is
# a judgement call, not a measurement — see the caveat print_hourly emits.
POST_WINDOW_HOURS = 3

# ── Row-level reading ────────────────────────────────────────────────────
# At 10-30 pageviews a day the aggregates are the wrong instrument: a day fits
# on one screen, and reading it answers questions no rate can. This was not
# obvious until 2026-08-22, when the 29-view "spike" of 2026-08-18 turned out
# to be 28 rows, 11 of which were one four-minute session walking the players
# index — our own testing, confirmed afterwards.
ROW_LIMIT = 10000

# The web-analytics convention. Arbitrary, and it CANNOT separate two people
# browsing at the same time: this schema has no visitor id (blob9 is reserved
# for one, P4). A "session" here means "a run of activity", which on a site
# this size is usually but not always one person.
SESSION_GAP_MINUTES = 30

# What our own testing looks like, learned from the labelled 2026-08-18 burst:
# many player pages, several distinct ones, clicked far faster than anyone
# reads. A visitor arrives, looks at a thing or two, and leaves; we walk a
# roster. This is a GUESS FROM BEHAVIOUR, never proof, and the report only
# ever flags it — it is never subtracted from any total.
WALK_MIN_PAGEVIEWS = 5
WALK_MIN_PLAYER_PAGES = 3
WALK_MAX_MEDIAN_GAP_S = 60

# A run of activity whose FIRST row has ref='self' began with an in-site
# navigation, so the visitor was ALREADY here — it is the tail of an earlier
# visit, not a new one, and counting it as new inflates the visit count.
# Bounded, because 'self' says the tab came from our own domain; it does not
# say when. A tab left open overnight would otherwise weld two unrelated
# sittings into one session. Four hours is a judgement call; the report prints
# how many merges it made so the effect is never invisible.
SELF_MERGE_MAX_HOURS = 4

# An expand firing seconds after the pageview is someone who knows where the
# button is — the site's author — not someone who read the page and decided
# to see more. Learned 2026-08-22 from 2026-08-18, whose four expands came
# 4s, 8s, 8s and 21s after their pageviews. This does NOT reclassify anything
# on its own; it splits the expand count so the "they read it" reading has to
# survive contact with the clock.
FAST_EXPAND_SECONDS = 10


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


def _owner_clause(include_owner: bool) -> str:
    """Drop our own testing traffic unless asked for it.

    Excluded BY DEFAULT because a night of testing reads exactly like a night
    of real visitors: on 2026-08-19 a session poking at player pages from the
    Mystics game put ~33 player-page views into the report with no way to tell
    them from arrivals. The dropped count is printed whenever it is non-zero —
    a filter you cannot see is how a report starts lying.

    BEST-EFFORT BY CONSTRUCTION. The tag only lands when the visit STARTS at
    the bookmarked URL, so typing the bare address still records as 'none'.
    The excluded number is a floor on our own traffic, never a total, and no
    row written before the bookmark existed carries the tag — so this changes
    nothing retroactively and the historical series stays continuous.
    """
    return "" if include_owner else f" AND blob3 != '{OWNER_SOURCE}'"


def collect(sql, start: dt.date, end: dt.date | None,
            site: str | None = DEFAULT_SITE,
            include_owner: bool = False) -> dict:
    """Run the queries backing the report. Returns raw aggregates."""
    own = _owner_clause(include_owner)
    w = f"{_window_clause(start, end)} AND {_site_clause(site)}{own}"
    s = f"{_site_clause(site)}{own}"
    # Counted OUTSIDE the owner filter, so the report can state what it set
    # aside even on a run that set aside everything it saw.
    owner_pv = sql(f"SELECT {COUNT} AS n FROM {DATASET} WHERE blob1 = 'pageview' "
                   f"AND {_window_clause(start, end)} AND {_site_clause(site)} "
                   f"AND blob3 = '{OWNER_SOURCE}'")

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
    # Which PAGE the pageview came from (blob2), added 2026-08-17 with the
    # player pages. Empty = the single-file tab site, which is every row
    # written before that date — so the split reads correctly backwards.
    pages = sql(f"SELECT blob2 AS page, {COUNT} AS n FROM {DATASET} "
                f"WHERE blob1 = 'pageview' AND {w} GROUP BY page ORDER BY n DESC")
    expands = sql(f"SELECT blob2 AS page, {COUNT} AS n FROM {DATASET} "
                  f"WHERE blob1 = 'expand' AND {w} GROUP BY page ORDER BY n DESC")

    return {
        "daily": [(r["d"], _n(r)) for r in daily],
        "by_event_source": [(r["event"], r["source"], _n(r)) for r in by_event_source],
        "tabs": [(r["tab"], _n(r)) for r in tabs],
        "returning": {r["r"]: _n(r) for r in returning},
        "alltime_source": {r["source"]: _n(r) for r in alltime_src},
        "referrers": [(r["ref"], _n(r)) for r in referrers],
        "pages": [(r["page"], _n(r)) for r in pages],
        "expands": [(r["page"], _n(r)) for r in expands],
        "owner_pageviews": _n(owner_pv[0]) if owner_pv else 0,
        "include_owner": include_owner,
    }


def collect_hourly(sql, day: dt.date, site: str | None = DEFAULT_SITE,
                   include_owner: bool = False) -> dict:
    """Pageviews per UTC hour for ONE day, plus that day's source split.

    Daily totals cannot answer "did my post cause that?" — a post at 18:47 and
    a game tipping at 19:00 land on the same date and are indistinguishable in
    the daily chart. Hours can separate them; posts.csv supplies the times.
    """
    end = day + dt.timedelta(days=1)
    own = _owner_clause(include_owner)
    w = f"{_window_clause(day, end)} AND {_site_clause(site)}{own}"
    hourly = sql(f"SELECT toHour(timestamp) AS h, {COUNT} AS n FROM {DATASET} "
                 f"WHERE blob1 = 'pageview' AND {w} GROUP BY h ORDER BY h")
    by_source = sql(f"SELECT blob3 AS source, {COUNT} AS n FROM {DATASET} "
                    f"WHERE blob1 = 'pageview' AND {w} GROUP BY source "
                    f"ORDER BY n DESC")
    owner_pv = sql(f"SELECT {COUNT} AS n FROM {DATASET} WHERE blob1 = 'pageview' "
                   f"AND {_window_clause(day, end)} AND {_site_clause(site)} "
                   f"AND blob3 = '{OWNER_SOURCE}'")
    return {
        "day": day,
        "by_hour": {int(r["h"]): _n(r) for r in hourly},
        "by_source": [(r["source"], _n(r)) for r in by_source],
        "owner_pageviews": _n(owner_pv[0]) if owner_pv else 0,
        "include_owner": include_owner,
    }


def _parse_ts(raw) -> dt.datetime:
    """Analytics Engine returns 'YYYY-MM-DD HH:MM:SS' in UTC. Be liberal about
    the exact shape — the API has been seen to add fractional seconds."""
    s = str(raw).strip().replace("T", " ").rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unparseable Analytics Engine timestamp: {raw!r}")


def collect_rows(sql, start: dt.date, end: dt.date | None,
                 site: str | None = DEFAULT_SITE) -> list[dict]:
    """Every beacon row in the window, unaggregated and time-ordered.

    The owner filter is deliberately NOT applied. Here the rows ARE the report,
    and the point of reading them is to see everything that happened —
    including our own visits, which are the only labelled example we have to
    check the walk heuristic against.
    """
    w = f"{_window_clause(start, end)} AND {_site_clause(site)}"
    raw = sql(f"SELECT timestamp, blob1 AS event, blob2 AS page, blob3 AS source, "
              f"blob4 AS ret, blob8 AS ref, _sample_interval AS si "
              f"FROM {DATASET} WHERE {w} ORDER BY timestamp LIMIT {ROW_LIMIT}")
    if len(raw) >= ROW_LIMIT:
        print(f"warning: hit the {ROW_LIMIT}-row cap — this window is TRUNCATED "
              f"and any session near its end is incomplete.", file=sys.stderr)
    out = []
    for r in raw:
        try:
            ts = _parse_ts(r["timestamp"])
        except (ValueError, KeyError) as e:
            print(f"warning: skipped a row — {e}", file=sys.stderr)
            continue
        out.append({"ts": ts, "event": r.get("event") or "?",
                    "page": r.get("page") or "", "source": r.get("source") or "none",
                    "ref": r.get("ref") or "", "si": _n(r, "si")})
    return out


def sessionize(rows: list[dict],
               gap_minutes: int = SESSION_GAP_MINUTES) -> list[dict]:
    """Split time-ordered rows into visits.

    Two steps: cut on inactivity gaps, then glue back the runs that ref='self'
    identifies as continuations (see SELF_MERGE_MAX_HOURS).

    See SESSION_GAP_MINUTES: this groups ACTIVITY, not people. Two visitors
    overlapping in time become one session, and there is no field in this
    schema that could tell them apart.
    """
    runs: list[list[dict]] = []
    for r in rows:
        if runs and (r["ts"] - runs[-1][-1]["ts"]).total_seconds() <= gap_minutes * 60:
            runs[-1].append(r)
        else:
            runs.append([r])
    merged, continuations = _merge_continuations(runs)
    out = []
    for group, n in zip(merged, continuations):
        sess = _summarize_session(group)
        sess["continuations"] = n
        out.append(sess)
    return out


def _merge_continuations(runs: list[list[dict]]) -> tuple[list[list[dict]], list[int]]:
    """Glue a run back onto the previous one when its first row says the
    visitor arrived from our own site. Returns (groups, merges_per_group)."""
    out: list[list[dict]] = []
    counts: list[int] = []
    for run in runs:
        joins = (out and run[0]["ref"] == "self"
                 and (run[0]["ts"] - out[-1][-1]["ts"]).total_seconds()
                 <= SELF_MERGE_MAX_HOURS * 3600)
        if joins:
            out[-1].extend(run)
            counts[-1] += 1
        else:
            out.append(list(run))
            counts.append(0)
    return out, counts


def expand_delays(rows: list[dict]) -> list[dict]:
    """For each expand, how long after that page's pageview did it fire?

    delay=None means no pageview for that page was seen first — the beacon can
    miss a load (blocked on load, cached page), and an expand with no
    denominator must not be silently dropped or silently counted as fast.
    """
    last_pv: dict[str, dt.datetime] = {}
    out = []
    for r in rows:
        if r["event"] == "pageview":
            last_pv[r["page"]] = r["ts"]
        elif r["event"] == "expand":
            seen = last_pv.get(r["page"])
            out.append({"ts": r["ts"], "page": r["page"],
                        "delay_s": int((r["ts"] - seen).total_seconds())
                                   if seen else None})
    return out


def _summarize_session(rows: list[dict]) -> dict:
    events: dict[str, int] = {}
    for r in rows:
        events[r["event"]] = events.get(r["event"], 0) + r["si"]
    gaps = [(b["ts"] - a["ts"]).total_seconds() for a, b in zip(rows, rows[1:])]
    pages = [r["page"] for r in rows if r["event"] == "pageview"]
    sess = {
        "start": rows[0]["ts"],
        "end": rows[-1]["ts"],
        "duration_s": int((rows[-1]["ts"] - rows[0]["ts"]).total_seconds()),
        "events": events,
        "pages": pages,
        "player_pages": {p for p in pages if p.startswith(PLAYER_PREFIX)},
        "sources": sorted({r["source"] for r in rows}),
        "refs": sorted({r["ref"] for r in rows if r["ref"]}),
        "median_gap_s": statistics.median(gaps) if gaps else None,
        "rows": len(rows),
    }
    sess["is_owner_tagged"] = OWNER_SOURCE in sess["sources"]
    sess["looks_like_walk"] = _looks_like_a_walk(sess)
    return sess


def _looks_like_a_walk(sess: dict) -> bool:
    """Does this session have the shape of us testing? See the WALK_* notes."""
    if sess["events"].get("pageview", 0) < WALK_MIN_PAGEVIEWS:
        return False
    if len(sess["player_pages"]) < WALK_MIN_PLAYER_PAGES:
        return False
    return (sess["median_gap_s"] is not None
            and sess["median_gap_s"] < WALK_MAX_MEDIAN_GAP_S)


def read_posts(path: Path) -> list[dict]:
    """Parse posts.csv — one hand-written row per public post.

    This is the only record of WHY a day spiked. Analytics Engine can say 22
    people arrived at 15:00 UTC; it can never say a post went out at 14:47.

    Times are written in local wall-clock (POST_LOG_TZ) because that is when a
    human posts, and converted to UTC here because the beacon is UTC
    throughout. A missing file yields no markers and no error — the hourly
    chart stands on its own. A malformed ROW is skipped with a warning rather
    than aborting the run, same reasoning as existing_snapshot_dates.
    """
    if not path.exists():
        return []
    try:
        tz = ZoneInfo(POST_LOG_TZ)
    except Exception:
        print(f"warning: no timezone data for {POST_LOG_TZ} "
              f"(try: pip install tzdata) — ignoring {path.name}", file=sys.stderr)
        return []
    posts = []
    with path.open(newline="") as fh:
        for lineno, row in enumerate(csv.DictReader(fh), start=2):
            raw = (row.get("when_et") or "").strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                local = dt.datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except ValueError:
                print(f"warning: {path.name} line {lineno}: bad when_et {raw!r} "
                      f"(want 'YYYY-MM-DD HH:MM') — skipped", file=sys.stderr)
                continue
            posts.append({
                "utc": local.astimezone(dt.timezone.utc),
                "local": local,
                "platform": (row.get("platform") or "?").strip(),
                "what": (row.get("what") or "").strip(),
                "tag": (row.get("utm_tag") or "").strip(),
            })
    posts.sort(key=lambda r: r["utc"])
    return posts


# ── Shaping ──────────────────────────────────────────────────────────────

def _source_order(sources) -> list[str]:
    """Known sources first (stable presentation), then anything unexpected."""
    extra = sorted(s for s in sources if s not in KNOWN_SOURCES)
    return [s for s in KNOWN_SOURCES if s in sources] + extra


# blob2 on a pageview, as emitted by sag.render.chrome.usage_js:
#   ''                the single-file tab site (and EVERY row before
#                     2026-08-17, which is why empty must keep meaning this)
#   'players'         the /players/ index
#   'player:<slug>'   one player page
PLAYERS_INDEX_KEY = "players"
PLAYER_PREFIX = "player:"


def _surface(page: str) -> str:
    if page.startswith(PLAYER_PREFIX):
        return "player_pages"
    if page == PLAYERS_INDEX_KEY:
        return "players_index"
    return "main"


def build_report(raw: dict, start: dt.date, end: dt.date | None) -> dict:
    per_source: dict[str, dict[str, int]] = {}
    totals = {"pageview": 0, "tab": 0, "box": 0, "expand": 0}
    for event, source, n in raw["by_event_source"]:
        # GROUP BY event, source already yields one row per pair.
        per_source.setdefault(source, {})[event] = n
        if event in totals:
            totals[event] += n

    ret = raw["returning"]
    returning_n, new_n = ret.get("1", 0), ret.get("0", 0)

    # Per-surface split, and per-page detail for the player pages. The
    # question Phase 1 exists to answer is "do the SEO pages get traffic,
    # and does an arrival read?" — that is pageviews per page against
    # expands on the same key.
    by_surface = {"main": 0, "players_index": 0, "player_pages": 0}
    for page, n in raw["pages"]:
        by_surface[_surface(page)] += n
    expands_by_page = dict(raw["expands"])
    views_by_page = dict(raw["pages"])
    # Union of both keys, not just pages with pageviews: a page can carry an
    # expand while showing zero views — every pageview sent before
    # 2026-08-17 had an empty blob2, and a visitor whose beacon was blocked
    # on load can still open the splits. Dropping those rows would hide real
    # engagement behind a missing denominator.
    player_pages = [
        {"page": page,
         "pageviews": views_by_page.get(page, 0),
         "expands": expands_by_page.get(page, 0)}
        for page in sorted(set(views_by_page) | set(expands_by_page))
        if page.startswith(PLAYER_PREFIX)
    ]
    player_pages.sort(key=lambda r: (-r["pageviews"], -r["expands"], r["page"]))

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
        "by_surface": by_surface,
        "player_pages": player_pages,
        "expands_total": totals["expand"],
        "owner_pageviews": raw.get("owner_pageviews", 0),
        "include_owner": raw.get("include_owner", False),
    }


# ── Output ───────────────────────────────────────────────────────────────

def _rate(num: int, den: int) -> str:
    return f"{num / den:.2f}" if den else "—"


def _pct(num: int, den: int) -> str:
    return f"{100 * num / den:.0f}%" if den else "—"


def _print_owner_note(rep: dict) -> None:
    """Disclose what the owner filter did. Silent when it did nothing, because
    then there is nothing to disclose — but never silent when it dropped rows."""
    n = rep.get("owner_pageviews", 0)
    if rep.get("include_owner"):
        print(f"! INCLUDING our own testing traffic ({n} pageviews) — --include-owner")
    elif n:
        print(f"own testing excluded: {n} pageviews (utm_source={OWNER_SOURCE})")


def print_report(rep: dict) -> None:
    w = rep["window"]
    t = rep["totals"]
    pv = t["pageview"]

    print(f"WNBA usage — {w['start']} → {w['end']} ({w['days']}d, UTC)")
    print(f"pageviews {pv}   tab events {t['tab']}   box opens {t['box']}   "
          f"expands {t['expand']}")
    print("counts use SUM(_sample_interval) — sampling-corrected")
    _print_owner_note(rep)

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

    print("\n── Pages " + "─" * 52)
    print("  which surface did the pageview land on?")
    surf = rep["by_surface"]
    for label, key in (("main page", "main"),
                       ("players index", "players_index"),
                       ("player pages", "player_pages")):
        print(f"  {label:<16} {surf[key]:>7} {_pct(surf[key], pv):>6}")
    pp = rep["player_pages"]
    if pp or surf["player_pages"] or surf["players_index"]:
        exp = sum(r["expands"] for r in pp)
        print(f"\n  {len(pp)} player page(s) with activity · {exp} expand(s) "
              f"· {_rate(exp, surf['player_pages'])} expands/view")
        print("  an expand was meant to signal an arrival actually READ the page —")
        print("  check that with --sessions: an expand firing seconds after the")
        print("  pageview is someone who knows the button, not someone reading")
        print(f"  {'page':<26} {'views':>6} {'expands':>8}")
        for r in pp[:10]:
            print(f"  {r['page']:<26} {r['pageviews']:>6} {r['expands']:>8}")
        if len(pp) > 10:
            print(f"  … and {len(pp) - 10} more")
    else:
        print("  (no player-page traffic in window)")

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
        print("   both the page JS and workers/analytics/, then a day of traffic)")
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


def print_hourly(raw: dict, posts: list[dict]) -> None:
    day = raw["day"]
    by_hour = raw["by_hour"]
    total = sum(by_hour.values())
    now = dt.datetime.now(dt.timezone.utc)

    print(f"WNBA usage — {day} hour by hour (UTC)")
    print(f"pageviews {total}")
    print("counts use SUM(_sample_interval) — sampling-corrected")
    _print_owner_note(raw)
    if day == now.date():
        print(f"! partial day — {now.hour + 1} of 24 UTC hours elapsed")

    day_posts = [p for p in posts if p["utc"].date() == day]
    marks: dict[int, list] = {}
    for p in day_posts:
        marks.setdefault(p["utc"].hour, []).append(p)

    print("\n── Hourly pageviews " + "─" * 41)
    if not total:
        print("  (no pageviews on this day)")
    else:
        peak = max(by_hour.values()) or 1
        for h in range(24):
            n = by_hour.get(h, 0)
            bar = "█" * max(1, round(20 * n / peak)) if n else ""
            line = f"  {h:02d}:00  {n:>4}  {bar}"
            for p in marks.get(h, []):
                what = p["what"] or "(no description)"
                line += f"  ← {p['platform']} {p['utc']:%H:%M} \"{what}\""
            print(line.rstrip())

    print("\n── Posts on this day " + "─" * 40)
    if not posts:
        print(f"  (nothing logged yet in {POSTS_FILENAME} — one row per public")
        print("   post is what turns the spikes above from anonymous into")
        print("   attributable. The file is there; it just has no rows.)")
    elif not day_posts:
        print("  (none logged for this day)")
    else:
        clipped = False
        for p in day_posts:
            # Hour buckets, so the post's OWN hour is counted whole even though
            # the post landed partway through it. That leans generous on
            # purpose — dropping the hour would lose the first minutes, which
            # are the ones most likely to be real. The label names the actual
            # range rather than implying a clean "3 hours after".
            first = p["utc"].hour
            last = min(23, first + POST_WINDOW_HOURS - 1)
            after = sum(by_hour.get(h, 0) for h in range(first, last + 1))
            if first + POST_WINDOW_HOURS - 1 > 23:
                clipped = True
            tag = f"  [{p['tag']}]" if p["tag"] else "  [untagged]"
            print(f"  {p['utc']:%H:%M} UTC ({p['local']:%H:%M} {p['local']:%Z})  "
                  f"{p['platform']}  {p['what']}{tag}")
            print(f"    {after} views in {first:02d}:00–{last:02d}:59 UTC "
                  f"({_pct(after, total)} of the day)")
        if clipped:
            print(f"  ! a window ran past midnight UTC and was cut there — this")
            print(f"    report only queries one day, so the tail is NOT counted.")
            print(f"    Re-run with --date {day + dt.timedelta(days=1)} to see it.")
        print("  ! proximity, NOT proof. A post and a good game share an hour all")
        print("    the time; the post's own hour is counted whole, so this leans")
        print("    generous; and the window is a guess, not a measurement. The")
        print("    source table below is the harder evidence — a tagged link is")
        print("    the only thing that actually proves where an arrival came from.")

    print("\n── Pageviews by source " + "─" * 38)
    if not raw["by_source"]:
        print("  (none)")
    for src, n in raw["by_source"]:
        print(f"  {src:<16} {n:>6} {_pct(n, total):>5}")


def _et(ts: dt.datetime) -> str:
    """UTC instant as local wall-clock, because that is how a human remembers
    what they were doing."""
    try:
        return ts.astimezone(ZoneInfo(POST_LOG_TZ)).strftime("%m-%d %H:%M")
    except Exception:
        return ts.strftime("%m-%d %H:%M") + "Z"


def _dur(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def print_rows(rows: list[dict], day: dt.date) -> None:
    """One day, every beacon row. At this volume the list IS the analysis."""
    print(f"WNBA usage — {day} every row (UTC)")
    print(f"{len(rows)} row(s); {sum(r['si'] for r in rows if r['event'] == 'pageview')} "
          f"pageview(s)")
    print("counts use SUM(_sample_interval) — sampling-corrected")
    print("! includes our own traffic; nothing is filtered here on purpose")

    print("\n── Rows " + "─" * 53)
    if not rows:
        print("  (nothing that day)")
        return
    print(f"  {'utc':<9}{'et':<13}{'event':<9}{'page':<26}{'src':<9}ref")
    prev = None
    for r in rows:
        gap = ""
        if prev is not None:
            g = int((r["ts"] - prev).total_seconds())
            if g >= SESSION_GAP_MINUTES * 60:
                print(f"  {'':-<9}{'':-<13} gap {_dur(g)} " + "-" * 28)
            elif g:
                gap = f"+{g}s"
        prev = r["ts"]
        print(f"  {r['ts']:%H:%M:%S} {_et(r['ts']):<13}{r['event']:<9}"
              f"{(r['page'] or '(main)'):<26}{r['source']:<9}{r['ref'] or '(none)'}"
              f"{'  ' + gap if gap else ''}")


def print_sessions(sessions: list[dict], start: dt.date, end: dt.date | None,
                   gap_minutes: int = SESSION_GAP_MINUTES,
                   expands: list[dict] | None = None) -> None:
    """Visits, not pageviews. The number that actually describes the audience."""
    pv = sum(s["events"].get("pageview", 0) for s in sessions)
    merges = sum(s.get("continuations", 0) for s in sessions)
    print(f"WNBA usage — {start} → {end or 'now'} visits (UTC)")
    print(f"{pv} pageview(s) across {len(sessions)} visit(s)")
    if merges:
        print(f"  ({len(sessions) + merges} runs of activity, {merges} of them "
              f"continuations merged back in)")
    print(f"a visit is activity with no gap longer than {gap_minutes} min, plus")
    print(f"any run that began with an in-site click within "
          f"{SELF_MERGE_MAX_HOURS}h (ref=self)")
    # 2026-08-18 has a real gap of 29m55s — five seconds inside the boundary.
    # The count near the threshold is soft; --session-gap exists so that can be
    # checked rather than assumed.
    print(f"! the {gap_minutes}-minute rule is a convention, not a fact — try "
          f"--session-gap to test how much it moves")

    if not sessions:
        print("\n  (no activity in window)")
        return

    per = sorted(s["events"].get("pageview", 0) for s in sessions)
    print(f"median visit: {statistics.median(per):.0f} pageview(s); "
          f"largest: {per[-1]}")

    ranked = sorted(sessions, key=lambda s: (-s["events"].get("pageview", 0),
                                             s["start"]))
    print("\n── Visits " + "─" * 51)
    print(f"  {'start (et)':<13}{'dur':>7}{'pv':>4}{'tab':>4}{'box':>4}  "
          f"{'source':<9}{'ref':<12}flags")
    for s in ranked[:15]:
        flags = []
        if s["is_owner_tagged"]:
            flags.append("OWNER-TAGGED")
        if s["looks_like_walk"]:
            flags.append("walk?")
        print(f"  {_et(s['start']):<13}{_dur(s['duration_s']):>7}"
              f"{s['events'].get('pageview', 0):>4}{s['events'].get('tab', 0):>4}"
              f"{s['events'].get('box', 0):>4}  "
              f"{','.join(s['sources'])[:8]:<9}{','.join(s['refs'])[:11]:<12}"
              f"{' '.join(flags)}".rstrip())
    if len(ranked) > 15:
        print(f"  … and {len(ranked) - 15} more")

    walks = [s for s in sessions if s["looks_like_walk"]]
    walk_pv = sum(s["events"].get("pageview", 0) for s in walks)
    tagged = [s for s in sessions if s["is_owner_tagged"]]
    print("\n── Shape check " + "─" * 46)
    print(f"  {len(walks)} visit(s) look like a fast roster walk, "
          f"{walk_pv} pageview(s) — {_pct(walk_pv, pv)} of the window")
    print(f"  {len(tagged)} visit(s) carry the {OWNER_SOURCE} tag")
    agree = sum(1 for s in walks if s["is_owner_tagged"])
    if tagged:
        print(f"  of those, {agree} also look like a walk — that overlap is how")
        print(f"  you find out whether the heuristic is worth anything")
    else:
        print(f"  no tagged visits yet, so the walk flag is UNVALIDATED —")
        print(f"  use the bookmark for a while and this line becomes a score")
    print(f"  ! a walk flag is a GUESS from behaviour: >={WALK_MIN_PAGEVIEWS} pageviews,")
    print(f"    >={WALK_MIN_PLAYER_PAGES} distinct player pages, median gap under "
          f"{WALK_MAX_MEDIAN_GAP_S}s.")
    print(f"    It is never subtracted from any total. And a visit groups")
    print(f"    ACTIVITY, not people — two visitors at once merge into one.")

    if expands is not None:
        _print_expand_timing(expands)


def _print_expand_timing(expands: list[dict]) -> None:
    """Split the expand count by how fast it came after the pageview."""
    print("\n── Expand timing " + "─" * 44)
    print("  an expand is only evidence of READING if it took a human moment")
    if not expands:
        print("  (no expands in window)")
        return
    timed = [e for e in expands if e["delay_s"] is not None]
    fast = [e for e in timed if e["delay_s"] < FAST_EXPAND_SECONDS]
    slow = [e for e in timed if e["delay_s"] >= FAST_EXPAND_SECONDS]
    orphan = len(expands) - len(timed)
    print(f"  {len(expands)} expand(s): {len(fast)} within "
          f"{FAST_EXPAND_SECONDS}s of the pageview, {len(slow)} later"
          + (f", {orphan} with no pageview to measure from" if orphan else ""))
    if timed:
        ds = sorted(e["delay_s"] for e in timed)
        print(f"  fastest {ds[0]}s · median {statistics.median(ds):.0f}s · "
              f"slowest {ds[-1]}s")
    for e in sorted(timed, key=lambda x: x["delay_s"])[:8]:
        tag = "knows the button" if e["delay_s"] < FAST_EXPAND_SECONDS else ""
        print(f"  {_et(e['ts']):<13}{e['delay_s']:>5}s  {e['page']:<28}{tag}".rstrip())
    if len(timed) > 8:
        print(f"  … and {len(timed) - 8} more")
    print(f"  ! a fast expand is FAMILIARITY, not engagement — the button is")
    print(f"    found instantly by whoever built the page. {FAST_EXPAND_SECONDS}s is a")
    print(f"    judgement call, and nothing here is subtracted from any total.")


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
        # Added 2026-08-17 with the player pages, and the reason this file
        # exists: Analytics Engine keeps 90 days, so without these the whole
        # Phase 1 traffic curve — the thing the SEO surface was built to
        # produce — would age out unrecorded. A missing key on an older row
        # means "not collected", never zero. Same rule as "referrers".
        "by_surface": rep["by_surface"],
        "expands": rep["expands_total"],
        # Top 10 only: the full per-page detail lives in Analytics Engine for
        # 90 days, and committing 227 counters every day would bloat this
        # file for a long tail that is mostly zeros. The aggregate above is
        # what the trend needs; this names the pages actually pulling.
        "top_player_pages": {r["page"]: r["pageviews"]
                             for r in rep["player_pages"][:10]},
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

def _posts_path(site: str | None) -> Path:
    """Where the posting log for one property lives."""
    return HERE / "sites" / (site or DEFAULT_SITE) / POSTS_DIRNAME / POSTS_FILENAME


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
                    help="the day to act on: with --snapshot the day to record "
                         "(default yesterday UTC), with --hourly the day to break "
                         "out (default today UTC)")
    ap.add_argument("--rows", action="store_true",
                    help="dump every beacon row for one UTC day, unfiltered "
                         "(pairs with --date)")
    ap.add_argument("--sessions", action="store_true",
                    help="group the window's activity into visits "
                         f"({SESSION_GAP_MINUTES}-min gap rule)")
    ap.add_argument("--session-gap", type=int, default=SESSION_GAP_MINUTES,
                    dest="session_gap", metavar="MIN",
                    help="minutes of inactivity that end a session "
                         f"(default {SESSION_GAP_MINUTES})")
    ap.add_argument("--hourly", action="store_true",
                    help="hour-by-hour pageviews for one UTC day, annotated with "
                         f"{POSTS_FILENAME} (pairs with --date)")
    ap.add_argument("--include-owner", action="store_true",
                    help=f"keep our own testing traffic (utm_source={OWNER_SOURCE}); "
                         "it is excluded by default")
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
        if args.rows:
            day = args.date or today
            print_rows(collect_rows(sql, day, day + dt.timedelta(days=1), site), day)
            return 0

        if args.sessions:
            if args.date:
                start, end = args.date, args.date + dt.timedelta(days=1)
            else:
                start, end = args.since or (today - dt.timedelta(days=args.days)), None
            rows = collect_rows(sql, start, end, site)
            print_sessions(sessionize(rows, args.session_gap), start, end,
                           args.session_gap, expand_delays(rows))
            return 0

        if args.hourly:
            day = args.date or today
            raw = collect_hourly(sql, day, site, args.include_owner)
            posts = read_posts(_posts_path(site))
            if args.as_json:
                out = dict(raw, day=day.isoformat())
                out["posts"] = [dict(p, utc=p["utc"].isoformat(),
                                     local=p["local"].isoformat())
                                for p in posts if p["utc"].date() == day]
                print(json.dumps(out, indent=2))
            else:
                print_hourly(raw, posts)
            return 0

        if args.snapshot:
            day = args.date or (today - dt.timedelta(days=1))
            if day >= today:
                print(f"error: {day} is not a closed UTC day yet.", file=sys.stderr)
                return 2
            if day.isoformat() in existing_snapshot_dates(HISTORY_PATH):
                print(f"{day} already recorded in {HISTORY_PATH.name} — nothing to do.")
                return 0
            raw = collect(sql, day, day + dt.timedelta(days=1), site,
                          args.include_owner)
            row = snapshot_row(build_report(raw, day, day + dt.timedelta(days=1)), day)
            row["site"] = args.site
            with HISTORY_PATH.open("a") as fh:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            print(f"recorded {day} → {HISTORY_PATH.name}: "
                  f"{row['pageviews']} pageviews, {row['box_opens']} box opens")
            return 0

        start = args.since or (today - dt.timedelta(days=args.days))
        rep = build_report(collect(sql, start, None, site, args.include_owner),
                           start, None)
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
