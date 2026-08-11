# espn-proxy

A narrow, authenticated pass-through to ESPN's public WNBA API, running on
Cloudflare Workers.

**Status: prototype, not deployed, and not needed.** It was drafted for a
diagnosis that turned out to be wrong. Keep it only as a contingency.

## Why (and why it wasn't the answer)

On **2026-08-05** every `site.api.espn.com` request returned an Akamai
`Access Denied` 403 for about four hours. Because `discover_games()` swallowed
each failure, three builds in a row went **green** while republishing day-old
data and telling visitors "No games today" with four scheduled.

The first theory was bot filtering on the old `User-Agent: wnba-stats-fetch`,
and the second was an IP-range block on the Actions runner — this proxy was
drafted for the second. Testing one variable at a time, **inside the outage
window**, killed both:

| Test | Result |
|---|---|
| `site.api` + browser User-Agent | 403 |
| `site.api` + script User-Agent, or none | 403 |
| `site.api` from home broadband, not the runner | 403 |
| `site.api` from a third, unrelated network | 403 |
| `site.api` **NBA** path | 403 |
| **`site.web.api` + any/no User-Agent** | **200** |

Only the hostname mattered. **The mitigation was a one-line swap** of
`ESPN_ORIGIN` to `https://site.web.api.espn.com`.

**Correction (same day):** `site.api` recovered on its own around 15:02 UTC and
serves 200s again. It was a transient host-wide failure, not a retirement — an
earlier version of this file said otherwise. The swap still stands, because one
host stayed up and the other didn't, but the durable lesson is that *either*
host can fail and `ESPN_ORIGIN` makes switching cheap.

This proxy is retained only for a narrower future case: a replacement host that
is genuinely geo- or IP-fenced, where Cloudflare's egress reputation differs
from Azure's. It would **not** have helped here — a third unrelated network was
blocked identically. **Try a host swap first.**

## Design stance

Deliberately **not** a general proxy. It's a keyhole:

| Limit | Value |
|---|---|
| Upstream host | `site.web.api.espn.com` only |
| Paths | `.../wnba/scoreboard`, `.../wnba/summary` — anchored regexes |
| Method | `GET` only |
| Query params | `dates`, `event`, `limit`, `seasontype`, `week` — rebuilt from an allowlist, not forwarded wholesale |
| Auth | `X-Proxy-Key` header vs. the `PROXY_KEY` secret; **fails closed** if the secret is unset |

An open proxy on your own Cloudflare account is a liability — someone else's
abuse becomes your problem. Every limit above is load-bearing; widen them only
on purpose.

**No caching.** A once-daily build gains nothing from a cache, and a cache is
precisely how you write a stale in-progress box score as final — a bug this
project has already been bitten by. Origin every time, responses `no-store`.

**Status codes pass through unchanged.** `fetch_data.py`'s retry policy keys on
them (5xx and 429 retried, 403 fails fast), so flattening upstream errors into
`200`s here would silently break the entire backoff design.

## Deploy

```bash
cd workers/espn-proxy
npx wrangler secret put PROXY_KEY      # paste a long random string
npx wrangler deploy
```

Confirm it's alive (no secret needed):

```bash
curl https://wnba-espn-proxy.<your-subdomain>.workers.dev/health
```

Confirm it actually reaches ESPN:

```bash
curl -H "X-Proxy-Key: <the secret>" \
  "https://wnba-espn-proxy.<your-subdomain>.workers.dev/apis/site/v2/sports/basketball/wnba/scoreboard?dates=20260804"
```

## Wire it into the build

Add the two repo secrets (**Settings → Secrets and variables → Actions**):

- `ESPN_ORIGIN` — `https://wnba-espn-proxy.<your-subdomain>.workers.dev`
- `ESPN_PROXY_KEY` — the same value as `PROXY_KEY`

Then add them to the fetch step in `.github/workflows/build.yml`:

```yaml
      - name: Fetch latest box scores
        run: python sites/wnba/fetch_data.py
        env:
          ALLOW_PARTIAL: ${{ inputs.allow_partial && '1' || '0' }}
          ESPN_ORIGIN: ${{ secrets.ESPN_ORIGIN }}
          ESPN_PROXY_KEY: ${{ secrets.ESPN_PROXY_KEY }}
```

`fetch_data.py` needs no code change — `ESPN_ORIGIN` defaults to
`https://site.web.api.espn.com`, so **leaving the secrets unset reverts to
direct calls**. That's the rollback: delete the secrets, re-run.

## Cost

Cloudflare's free tier is 100,000 requests/day. A full-season rebuild is ~230
requests; an incremental daily build is under 10. This will not cost anything.

## Caveats worth knowing before you rely on it

- **It may not work.** If ESPN is fingerprinting something other than IP and UA
  (TLS fingerprint, header ordering), a Worker will be blocked too. Test with
  the `curl` above before wiring it in.
- **It's a second thing that can break**, sitting directly in the critical path
  of the daily build. The fail-loud change means a proxy outage now goes red
  rather than silently stale, which is the right trade — but it's still one more
  moving part between you and your data.
- **It doesn't fix the underlying exposure.** The pipeline still depends on an
  undocumented API with no contractual relationship behind it. This buys time;
  it isn't a durable answer.
