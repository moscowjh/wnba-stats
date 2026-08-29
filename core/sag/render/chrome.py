"""Shared site chrome — the single source of truth for what makes a page
look and behave like statsataglance.

Extracted 2026-08-16 from ``build_stats_page.py`` so player pages (and every
later page type) carry the identical tokens, footer, scroll fade, and
analytics instead of a hand-copied fork. The six design tokens were
byte-identical across two builders the day this was written, and nothing
kept them that way — the first palette tweak would have silently forked
the site. Now a tweak here is a tweak everywhere.

The CSS/JS fragments preserve the exact bytes the live index.html has
always carried; ``golden_check.py`` is the proof that this extraction
changed nothing. Keep it that way: edits here re-render every page.
"""

# ── Design tokens ─────────────────────────────────────────────────────────
# --avg is only used by the tab site's league-average rows, but it ships in
# the shared block anyway: one token block, no per-page subsets to drift.
#
# `--accent` is the ONE token a site may diverge on (Option C, 2026-08-23):
# WWC is cyan where WNBA is amber, on the same near-black ground. Everything
# else stays literally shared — a second site is a different accent, not a
# different design system. The value is carried on LeagueConfig.accent, the
# same shape as jersey_prefix, so a site declares it in its config and never
# hand-writes a :root block.
#
# ⚠️ SCROLL_FADE_CSS below hardcodes `rgba(15,15,15,0)` — that is --bg written
# out by hand, and it is duplicated at build_stats_page.py's .gm-tscroll rule.
# Option C does not move --bg, so the literal stays correct and dormant. Any
# future site that DOES diverge on --bg must fix both copies first; fading to
# bare `transparent` is not the fix (transparent is transparent *black*, which
# smears grey). Tracked in the backlog under Dev → WNBA known issues.
_TOKENS_CSS_TMPL = """\
  :root {
    --bg:#0f0f0f; --surface:#1a1a1a; --border:#2e2e2e;
    --text:#e8e8e8; --muted:#888; --accent:%s;
    --avg:#aaa;
  }
"""


def tokens_css(accent):
    """The shared :root token block, with this site's accent substituted.

    `tokens_css("#f5a623")` is byte-identical to the constant this replaced;
    golden_check.py is the proof, and that is the only reason the split was
    safe to make under a deadline.
    """
    return _TOKENS_CSS_TMPL % accent

# ── Site footer ───────────────────────────────────────────────────────────
SITE_FOOTER_CSS = """\
  .site-footer{margin-top:34px;padding-top:12px;border-top:1px solid var(--border);
      color:var(--muted);font-size:11px;line-height:1.8}
  .site-footer a{color:var(--muted);text-decoration:underline}
  .site-footer a:hover{color:var(--accent)}
"""

SITE_FOOTER_HTML = (
    '<div class="site-footer">\n'
    '  feedback → <a href="mailto:hello@statsataglance.com">hello@statsataglance.com</a><br>\n'
    '  an independent, non-commercial fan project\n'
    '</div>\n'
)

# ── Horizontal-scroll fade ────────────────────────────────────────────────
# A wide table sits in a .table-wrap (the scroller) inside a .table-scroll
# (the fade overlay). The JS toggles .more-right while there is more table
# to the right. The .gm-tw/.gm-tscroll selectors are the Games tab's
# narrower variant; on pages without them the selectors simply match nothing.
SCROLL_FADE_CSS = """\
  .table-scroll{position:relative;margin-bottom:16px}
  .table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  .table-scroll::after{content:"";position:absolute;top:0;bottom:0;right:0;
    width:28px;pointer-events:none;opacity:0;transition:opacity .15s ease;z-index:5;
    background:linear-gradient(to right, rgba(15,15,15,0), var(--bg))}
  .table-scroll.more-right::after{opacity:1}
"""

SCROLL_FADE_JS = """\
/* -- Horizontal-scroll fade -- */
function updateScrollFades(wrap) {
  const scroll = wrap.closest('.table-scroll') || wrap.closest('.gm-tscroll');
  if (!scroll) return;
  scroll.classList.toggle('more-right',
    wrap.scrollWidth - wrap.clientWidth - wrap.scrollLeft > 1);
}
function initScrollFades() {
  document.querySelectorAll('.table-wrap, .gm-tw').forEach(wrap => {
    updateScrollFades(wrap);
    wrap.addEventListener('scroll', () => updateScrollFades(wrap), {passive:true});
  });
}
window.addEventListener('load', initScrollFades);
window.addEventListener('resize', () =>
  document.querySelectorAll('.table-wrap, .gm-tw').forEach(updateScrollFades));
"""

# ── Usage tracking (Workers Analytics Engine via wnba-usage-tracker) ─────
_USAGE_JS = """\
/* -- Usage tracking (Workers Analytics Engine via wnba-usage-tracker) --
   Cloudflare Web Analytics only sees the initial page load on this
   single-file site, since tabs and box scores are client-side JS, not
   real navigation. This sends small, cookie-free beacon pings so we can
   see which tabs get used and whether box scores get opened. Fails
   silently if the endpoint is unreachable -- never blocks the page. */
var TRACK_URL = 'https://usage.statsataglance.com/t';
/* Which statsataglance property this page is. Sent on every beacon so one
   dataset can answer cross-site questions (does the Cup site hand users back
   to WNBA?). Added 2026-08-05 before a second site existed — rows without it
   predate the change and are WNBA by definition. */
var SITE_ID = '__SITE_ID__';
/* Referring hostname -- the only way to see search/Reddit/etc, since
   utm_source can only see links we tagged ourselves. HOSTNAME ONLY: full
   referrer URLs carry private context (search terms, for one). Captured
   once per session so an in-site reload can't overwrite the real arrival.
   'direct' = no referrer sent, which is NOT the same as "typed the URL";
   see DEPLOY.md before drawing conclusions from it. */
function refHost() {
  var cached = sessionStorage.getItem('wsag_ref');
  if (cached) return cached;
  var bare = function (h) { return h.toLowerCase().replace(/^www\\./, ''); };
  var h = 'direct';
  if (document.referrer) {
    try {
      var d = bare(new URL(document.referrer).hostname);
      h = (d === bare(window.location.hostname)) ? 'self' : d;
    } catch (e) {}
  }
  sessionStorage.setItem('wsag_ref', h);
  return h;
}
function initTracking() {
  try {
    var qs = new URLSearchParams(window.location.search);
    var src = qs.get('utm_source');
    if (src) sessionStorage.setItem('wsag_src', src);
    window.__wsagSrc = src || sessionStorage.getItem('wsag_src') || 'none';
    window.__wsagReturning = localStorage.getItem('wsag_seen') ? '1' : '0';
    localStorage.setItem('wsag_seen', '1');
    window.__wsagRef = refHost();
    track('pageview', '__PAGE_KEY__');
  } catch (e) {}
}
function track(event, tab) {
  try {
    var params = new URLSearchParams({
      e: event, t: tab || '', s: window.__wsagSrc || 'none',
      r: window.__wsagReturning || '0', site: SITE_ID,
      ref: window.__wsagRef || 'direct'
    });
    var url = TRACK_URL + '?' + params.toString();
    if (navigator.sendBeacon) navigator.sendBeacon(url);
    else fetch(url, { method: 'GET', keepalive: true, mode: 'no-cors' });
  } catch (e) {}
}
window.addEventListener('load', initTracking);
"""


def usage_js(site_id, page_key=""):
    """The usage-beacon JS for one page. `site_id` is the LeagueConfig slug,
    landing in every row's `site` column.

    `page_key` identifies WHICH page sent the pageview (blob2). It is empty
    for the single-file tab site, which is both the historical meaning of
    that column and what keeps this substitution byte-identical there —
    every row written before 2026-08-17 came from the tab site, so "empty
    = main page" reads correctly backwards through the whole series.
    Multi-page surfaces pass a key (`player:<slug>`, `players`), which is
    the only way the beacon can distinguish an SEO landing from a homepage
    visit. Keys must fit the worker's 32-char slice — the page emitter
    asserts that; see ANALYTICS_KEY_MAX in build_player_pages.py.
    """
    return (_USAGE_JS.replace("__SITE_ID__", site_id)
                     .replace("__PAGE_KEY__", page_key))


# ── Cloudflare Web Analytics beacon ──────────────────────────────────────
def cf_beacon_html(token):
    """The Cloudflare Web Analytics script tag, or '' when the site has no
    token configured. Player pages are real navigations, so unlike the tab
    site this beacon sees every one of their pageviews."""
    if not token:
        return ""
    return (
        '<!-- Cloudflare Web Analytics -->'
        '<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
        f'data-cf-beacon=\'{{"token": "{token}"}}\'></script>'
        '<!-- End Cloudflare Web Analytics -->\n'
    )


# ── Subpage masthead ─────────────────────────────────────────────────────
# The tab site's header is its h1 + tab bar, which only makes sense on the
# single-file page. Subpages (player pages, the players index) carry this
# masthead instead: site title linking home, plus an optional local link.
SUBPAGE_HEADER_CSS = """\
  .masthead{margin-bottom:14px;font-size:12px;line-height:1.7}
  .masthead .site-name{color:var(--accent);text-decoration:none;font-size:14px}
  .masthead .site-name:hover{text-decoration:underline}
  .masthead .crumb{color:var(--muted);font-size:11px}
  .masthead .crumb a{color:var(--muted)}
  .masthead .crumb a:hover{color:var(--accent)}
"""


def subpage_header_html(site_title, home_url, crumb_html=""):
    crumb = f'<div class="crumb">{crumb_html}</div>' if crumb_html else ""
    return (
        '<div class="masthead">'
        f'<a class="site-name" href="{home_url}">{site_title}</a>'
        f'{crumb}</div>\n'
    )
