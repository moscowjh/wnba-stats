"""ESPN public-API client, parameterised by league slug.

Exists so that no site script builds an ESPN URL by hand. The rule matters
because ESPN mirrors the same paths on more than one host and *either* can
fail host-wide: `site.api.espn.com` 403'd for ~4 hours on 2026-08-05 while
`site.web.api.espn.com` served fine, and a spike script that had hardcoded
the dead host produced a false negative that day.

`ESPN_ORIGIN` is the mitigation, and it is deliberately read from the
environment here under the SAME name that `sites/wnba/fetch_data.py` uses,
so one env var switches every caller at once — to another ESPN host or to
the espn-proxy Worker (`workers/espn-proxy/README.md`), both of which mirror
these paths. Switching is an env var, not a deploy.

Known league slugs: "wnba", "womens-college-basketball", "fiba".

⚠️ `fiba` is thinner than it looks — see `docs/data-sources.md`. As of
2026-08-31 it carries the current women's World Cup and NOTHING else (no
completed games at all across 2023–2025), and at least one team record is
wrong upstream: Mali is served with `abbreviation: "KOR"`, South Korea's
code. **Join ESPN teams on name, never on abbreviation.**
"""

import json
import os
import urllib.request

ESPN_ORIGIN = os.environ.get(
    "ESPN_ORIGIN", "https://site.web.api.espn.com").rstrip("/")

_BASE = "/apis/site/v2/sports/basketball"

# ESPN 403s some default clients. A browser UA is load-bearing on the FIBA
# host in particular, the same lesson the FIBA adapter records.
_UA = "Mozilla/5.0 (compatible; statsataglance/1.0)"


def scoreboard_url(league, dates=None):
    """`dates` is ESPN's own format: "YYYYMMDD" or "YYYYMMDD-YYYYMMDD"."""
    url = f"{ESPN_ORIGIN}{_BASE}/{league}/scoreboard"
    return f"{url}?dates={dates}" if dates else url


def summary_url(league, event_id):
    return f"{ESPN_ORIGIN}{_BASE}/{league}/summary?event={event_id}"


def fetch_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def parse_scoreboard(payload):
    """Scoreboard JSON -> one flat dict per game.

    `completed` is the only trustworthy signal that the scores are final;
    a scheduled game carries score 0 for both sides, which is a real number
    and would otherwise read as a 0-0 result.
    """
    out = []
    for ev in payload.get("events") or []:
        comps = ev.get("competitions") or [{}]
        status = (ev.get("status") or {}).get("type") or {}
        teams = []
        for c in comps[0].get("competitors") or []:
            t = c.get("team") or {}
            teams.append({
                "name": t.get("displayName"),
                # Kept for display/debugging only. NOT a join key — see the
                # Mali/KOR note in the module docstring.
                "abbreviation": t.get("abbreviation"),
                "score": _int(c.get("score")),
                "home_away": c.get("homeAway"),
            })
        out.append({
            "event_id": ev.get("id"),
            "date_utc": ev.get("date"),
            "name": ev.get("name"),
            "completed": bool(status.get("completed")),
            "state": status.get("state"),
            "teams": teams,
        })
    return out


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
