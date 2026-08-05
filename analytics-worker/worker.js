// wnba-usage-tracker — lightweight, cookie-free usage analytics for
// wnba.statsataglance.com.
//
// Why this exists: Cloudflare Web Analytics only sees the initial page
// load ("/"), because the site is a single static HTML file with
// vanilla-JS tabs — there's no real navigation for it to catch. This
// Worker receives small beacon pings from the page's own JS so we can see
// which tab people actually use and whether a box score got opened, not
// just that someone showed up.
//
// Writes one data point per event to Workers Analytics Engine (free tier,
// no cookies, no PII, aggregate-only — a good fit for a no-ads/no-tracking
// site). Query later via the Analytics Engine SQL API (see README note
// below, or DEPLOY.md).
//
// Events logged (sent by build_stats_page.py's PAGE_JS):
//   pageview — one per page load
//   tab      — one per tab switch (tab = tab id, e.g. "leaders")
//   box      — one per box-score open (tab = "game:<id>")
//
// Every event also carries:
//   s = utm_source from the page's first load (?utm_source=..., cached
//       client-side in sessionStorage for the rest of that session)
//   r = "1" if this is a returning visitor (localStorage flag set on
//       first visit), else "0"
//
// Querying: use ../usage_report.py. Raw SQL API if you need something it
// doesn't cover (token scoped to Account Analytics: Read, nothing else):
//   curl -s "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/analytics_engine/sql" \
//     -H "Authorization: Bearer <API_TOKEN>" \
//     -d "SELECT blob1 AS event, blob2 AS tab, blob3 AS source, blob4 AS returning,
//                SUM(_sample_interval) AS n
//         FROM wnba_usage WHERE timestamp > NOW() - INTERVAL '1' DAY
//         GROUP BY event, tab, source, returning ORDER BY n DESC"
//
// ⚠️ SUM(_sample_interval), never count(). Analytics Engine samples at volume
// and stores the inverse sample rate per row, so count() counts stored rows,
// not events. This comment said count() until 2026-08-04, by which point it
// already under-counted by 3% (841 true vs 816 raw, all-time).

const ALLOWED_ORIGIN = "https://wnba.statsataglance.com";

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    if (url.pathname === "/t") {
      const p = url.searchParams;
      const event = (p.get("e") || "unknown").slice(0, 32);
      const tab = (p.get("t") || "").slice(0, 32);
      const src = (p.get("s") || "none").slice(0, 32);
      const returning = p.get("r") === "1" ? "1" : "0";

      if (env.USAGE) {
        env.USAGE.writeDataPoint({
          blobs: [event, tab, src, returning],
          doubles: [1],
          indexes: [event], // lets queries group/filter by event type cheaply
        });
      }

      // 204 + no body: this is a fire-and-forget beacon, never blocks the page.
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    return new Response(
      "wnba-usage-tracker is alive.\n" +
      "POST or GET /t?e=<event>&t=<tab>&s=<utm_source>&r=<0|1>\n",
      { headers: { "content-type": "text/plain; charset=utf-8" } }
    );
  },
};
