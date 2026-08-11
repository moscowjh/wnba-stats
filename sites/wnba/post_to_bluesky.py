#!/usr/bin/env python3
"""Daily WNBA leaders auto-post to Bluesky.

Runs as the LAST step of the daily GitHub Actions build, after the site has
already been built and deployed. Reads `social_payload.json` (emitted by
build_stats_page.py), features ONE leader category per day on a fixed rotation,
appends the deterministic "last night" factoid, and posts it with a clean link
card back to the site.

Design notes:
  - This is a WRITE of our own data. The only data it reads is our own payload
    file, never Bluesky's (flaky, cache-laggy) read/feed endpoints — so the
    staleness problems that plague feed reads don't apply here.
  - Fail-safe: any failure prints to stderr and exits non-zero so the Actions
    step is visibly marked failed, but the step is set `continue-on-error` so a
    failed post never blocks the site build/deploy. It never raises into CI.
  - UTM-tagged (utm_source=bluesky-post) so the usage beacon can attribute
    clicks, but rendered as a clean native link card (built from our own og
    image + copy) rather than an ugly tracked string in the visible post text.
  - Stdlib only (urllib) — no new dependency, in keeping with the site ethos.

Env:
  BLUESKY_HANDLE          e.g. wnba.statsataglance.com  (or the account DID)
  BLUESKY_APP_PASSWORD    an app password (Settings -> App Passwords), NOT the
                          main account password.
"""

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

from config import WNBA

PDS         = "https://bsky.social"
# Tagged so the usage beacon can separate "clicked the daily card" from direct
# traffic. Matches the /bsky bio redirect's `bluesky-bio`; keep these two values
# stable — they are the historical series in the wnba_usage dataset.
SITE_URL    = "https://wnba.statsataglance.com/?utm_source=bluesky-post"
CARD_TITLE  = "WNBA 2026 — At a Glance"
CARD_DESC   = ("Fast, ad-free WNBA stats — leaders, standings, four factors, "
               "updated every morning.")
CTA         = "Full season stats, ad-free ↓"
MAX_CHARS   = 300  # Bluesky post limit

PAYLOAD  = str(WNBA.social_payload)
OG_IMAGE = str(WNBA.og_image)

# Rotation order — must match BROADCAST_CATS in build_stats_page.py. Sequence
# is intentional: related stats sit next to each other (scoring → shooting
# rates → assists → FT/TS efficiency → rebounding pair → blocks/steals).
BROADCAST_ORDER = ['Scoring', '3PT%', 'eFG%', 'Assists', 'FT%',
                   'TS%', 'Rebounds', 'Off Reb', 'Blocks', 'Steals']

HEADERS = {
    'Scoring':  'scoring',
    'Rebounds': 'rebounding',
    'Assists':  'assists',
    'Steals':   'steals',
    'Blocks':   'blocks',
    'Off Reb':  'offensive rebounding',
    'eFG%':     'effective FG%',
    'TS%':      'true shooting',
    '3PT%':     '3-point shooting',
    'FT%':      'free-throw shooting',
}


def _api(path, data=None, token=None, raw_image=False):
    """POST to an XRPC endpoint. Returns parsed JSON. Raises on HTTP error."""
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if raw_image:
        body = data
        headers['Content-Type'] = 'image/png'
    elif data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    else:
        body = None
    req = urllib.request.Request(f"{PDS}{path}", data=body, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def category_for_today():
    ordinal = dt.datetime.now(dt.timezone.utc).date().toordinal()
    return BROADCAST_ORDER[ordinal % len(BROADCAST_ORDER)]


def build_text(payload, cat):
    """Assemble post text, trimming to stay within the Bluesky char limit."""
    through = payload['through']
    rows = payload['categories'][cat]
    factoid = payload.get('factoid')
    head = f"WNBA {HEADERS[cat]} leaders — through {through}"

    def assemble(rws, with_factoid):
        lines = [head, ""]
        lines += [f"{r['rank']}. {r['player']} ({r['team']}) — {r['value']}" for r in rws]
        if with_factoid and factoid:
            lines += ["", factoid]
        lines += ["", CTA]
        return "\n".join(lines)

    # The factoid is the freshness driver, so keep it and shed a leader row
    # before dropping it. Fall back to no-factoid only if even 3 rows won't fit.
    if factoid:
        candidates = [(5, True), (4, True), (3, True), (5, False)]
    else:
        candidates = [(5, False), (4, False), (3, False)]
    for n, wf in candidates:
        text = assemble(rows[:n], wf)
        if len(text) <= MAX_CHARS:
            return text
    return assemble(rows[:3], bool(factoid))[:MAX_CHARS]


def post(text):
    handle = os.environ.get('BLUESKY_HANDLE')
    pw = os.environ.get('BLUESKY_APP_PASSWORD')
    if not handle or not pw:
        print("Missing BLUESKY_HANDLE / BLUESKY_APP_PASSWORD — skipping post.")
        return 0

    sess = _api("/xrpc/com.atproto.server.createSession",
                {"identifier": handle, "password": pw})
    token, did = sess['accessJwt'], sess['did']

    external = {"uri": SITE_URL, "title": CARD_TITLE, "description": CARD_DESC}
    if os.path.exists(OG_IMAGE):
        with open(OG_IMAGE, 'rb') as fh:
            blob = _api("/xrpc/com.atproto.repo.uploadBlob", fh.read(),
                        token=token, raw_image=True)
        external['thumb'] = blob['blob']

    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "langs": ["en"],
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
        "embed": {"$type": "app.bsky.embed.external", "external": external},
    }
    res = _api("/xrpc/com.atproto.repo.createRecord",
               {"repo": did, "collection": "app.bsky.feed.post", "record": record},
               token=token)
    print("Posted:", res.get('uri'))
    return 0


def main():
    if not os.path.exists(PAYLOAD):
        print(f"No {PAYLOAD} — nothing to post (build may not have run).")
        return 0
    with open(PAYLOAD, encoding='utf-8') as fh:
        payload = json.load(fh)
    cat = category_for_today()
    text = build_text(payload, cat)
    print(f"--- category: {cat} ({len(text)} chars) ---\n{text}\n---")
    try:
        return post(text)
    except urllib.error.HTTPError as e:
        print(f"Bluesky post FAILED: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:500]}",
              file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - never raise into CI
        print(f"Bluesky post FAILED: {e!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
