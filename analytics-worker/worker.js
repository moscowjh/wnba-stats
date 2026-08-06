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
//   site = which statsataglance property sent it ("wnba", "wwc", "ncaaw").
//       Added 2026-08-05, BEFORE a second site existed, so cross-site funnel
//       questions (does the Cup site hand users back to WNBA?) are answerable
//       in ONE query against ONE dataset. Separate datasets per site would
//       have made that join impossible. Rows written before this date have an
//       empty blob10 and are all WNBA by definition — every reader must treat
//       '' as 'wnba' (see usage_report.py _site_clause).
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

// Any statsataglance property may report. Listed explicitly rather than
// wildcarding *.statsataglance.com so a stray subdomain can't write rows.
const ALLOWED_ORIGINS = [
  "https://wnba.statsataglance.com",
  "https://wwc.statsataglance.com",
  "https://ncaaw.statsataglance.com",
];

// Sites permitted in blob10. An unrecognised value is coerced to "unknown"
// rather than stored, so a typo can't silently fork the dataset into two
// series that look like real traffic.
const KNOWN_SITES = ["wnba", "wwc", "ncaaw"];

function corsHeaders(request) {
  const origin = request.headers.get("Origin") || "";
  const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(request) });
    }

    if (url.pathname === "/t") {
      const p = url.searchParams;
      const event = (p.get("e") || "unknown").slice(0, 32);
      const tab = (p.get("t") || "").slice(0, 32);
      const src = (p.get("s") || "none").slice(0, 32);
      const returning = p.get("r") === "1" ? "1" : "0";
      const rawSite = (p.get("site") || "wnba").slice(0, 16);
      const site = KNOWN_SITES.includes(rawSite) ? rawSite : "unknown";

      if (env.USAGE) {
        // blobs 5-9 are RESERVED — blob5 for P3 (recency bucket), blob6-9 for
        // P4 (country, device, referrer, session). Site takes blob10 to stay
        // clear of both; see USAGE-TRACKER-HANDOFF.md. Positions are arbitrary
        // to Analytics Engine but not to the queries already written against
        // them, so nothing before blob10 may be reused.
        env.USAGE.writeDataPoint({
          blobs: [event, tab, src, returning, "", "", "", "", "", site],
          doubles: [1],
          indexes: [event], // lets queries group/filter by event type cheaply
        });
      }

      // 204 + no body: this is a fire-and-forget beacon, never blocks the page.
      return new Response(null, { status: 204, headers: corsHeaders(request) });
    }

    return new Response(
      "wnba-usage-tracker is alive.\n" +
      "POST or GET /t?e=<event>&t=<tab>&s=<utm_source>&r=<0|1>&site=<wnba|wwc|ncaaw>\n",
      { headers: { "content-type": "text/plain; charset=utf-8" } }
    );
  },
};
