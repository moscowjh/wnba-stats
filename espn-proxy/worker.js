// espn-proxy — a narrow, authenticated pass-through to ESPN's public WNBA API.
//
// WHY THIS EXISTS
// On 2026-08-05 site.api.espn.com went permanently 403 (Akamai "Access
// Denied") for everyone — any User-Agent, any IP, any league path. It was NOT
// an IP-range block on the Actions runner, which is what this Worker was
// originally drafted to route around. The actual fix was a host swap to
// site.web.api.espn.com; see ESPN_ORIGIN in fetch_data.py.
//
// So this Worker is NOT currently needed and is NOT deployed. It's kept as a
// ready-made escape hatch for the day site.web.api dies too and the
// replacement host turns out to be geo- or IP-fenced — the case where routing
// through Cloudflare's egress genuinely helps. Try a plain host swap FIRST;
// that has been the answer every time so far.
//
// DESIGN STANCE
// This is deliberately NOT a general proxy. It is a keyhole: one upstream host,
// two path patterns, GET only, a fixed query allowlist, and a shared secret.
// An open proxy on your own domain is a liability — someone else's abuse
// becomes your Cloudflare account's problem — so every one of those limits is
// load-bearing. Widen them only on purpose.
//
// NO CACHING. A once-daily build gains nothing from a cache, and a cache is
// exactly how you end up writing a stale in-progress box score as final —
// which is a bug this project has already been bitten by. Requests go to
// origin every time, and responses are marked no-store.
//
// Secrets (wrangler secret put ...):
//   PROXY_KEY — shared secret; callers send it as the X-Proxy-Key header.
//
// Deploy:  cd espn-proxy && npx wrangler deploy
// Wire up: set ESPN_ORIGIN + ESPN_PROXY_KEY in the build workflow (README).

// Keep in sync with ESPN_ORIGIN's default in fetch_data.py.
const UPSTREAM = "https://site.web.api.espn.com";

// Only these paths are proxied. Anchored, so nothing can be appended.
const ALLOWED_PATHS = [
  /^\/apis\/site\/v2\/sports\/basketball\/wnba\/scoreboard$/,
  /^\/apis\/site\/v2\/sports\/basketball\/wnba\/summary$/,
];

// Only these query params are forwarded; anything else is dropped silently.
const ALLOWED_PARAMS = new Set(["dates", "event", "limit", "seasontype", "week"]);

// Sent upstream, mirroring ESPN_HEADERS in fetch_data.py. ESPN has never
// actually keyed on these (see the 2026-08-05 note there) — they're ordinary
// HTTP manners, not a disguise.
const UPSTREAM_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
  "Accept": "application/json, text/plain, */*",
  "Accept-Language": "en-US,en;q=0.9",
  "Referer": "https://www.espn.com/",
  "Origin": "https://www.espn.com",
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

// Constant-time-ish comparison. The window here is small (a length-leaking
// compare on a high-entropy secret isn't a practical attack) but it costs
// nothing to not think about it again.
function secretEquals(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);

    // Liveness probe — no secret required, reveals nothing.
    if (url.pathname === "/health") {
      return json({ ok: true, upstream: UPSTREAM, paths: ALLOWED_PATHS.length });
    }

    if (req.method !== "GET") {
      return json({ error: "method not allowed" }, 405);
    }

    if (!env.PROXY_KEY) {
      // Fail closed. A missing secret must never mean "no auth required".
      return json({ error: "proxy misconfigured — PROXY_KEY unset" }, 503);
    }
    if (!secretEquals(req.headers.get("X-Proxy-Key") || "", env.PROXY_KEY)) {
      return json({ error: "forbidden" }, 403);
    }

    if (!ALLOWED_PATHS.some((re) => re.test(url.pathname))) {
      return json({ error: "path not allowed", path: url.pathname }, 404);
    }

    // Rebuild the query string from the allowlist rather than forwarding it
    // wholesale — keeps a caller from smuggling anything through.
    const params = new URLSearchParams();
    for (const [k, v] of url.searchParams) {
      if (ALLOWED_PARAMS.has(k)) params.append(k, v);
    }

    const target = `${UPSTREAM}${url.pathname}${params.toString() ? `?${params}` : ""}`;

    let upstream;
    try {
      upstream = await fetch(target, {
        method: "GET",
        headers: UPSTREAM_HEADERS,
        cf: { cacheTtl: 0, cacheEverything: false },
      });
    } catch (e) {
      return json({ error: "upstream fetch failed", detail: e.message }, 502);
    }

    // Pass the status through UNCHANGED. fetch_data.py's retry policy keys on
    // it — 5xx and 429 are retried, 403 fails fast — so flattening errors into
    // 200s here would silently break the whole backoff design.
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") || "application/json",
        "cache-control": "no-store",
        "x-proxy-upstream-status": String(upstream.status),
      },
    });
  },
};
