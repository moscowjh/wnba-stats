"""SEO surface — slugs, sitemap, canonical/meta tags, Schema.org, og:image.

Slugs must be stable ASCII and trade-safe (A'ja Wilson -> `aja-wilson`),
because a slug that changes is a URL that 404s after it was indexed.

Everything here keys off a LeagueConfig — robots.txt and sitemap.xml are
PER-HOST facts (wnba., wwc., ncaaw. each need their own), so they are
emitted per-league from the league's own ``base_url``, never hand-placed.
"""

import json
import re
import unicodedata
from html import escape as esc


def slugify(name):
    """'A'ja Wilson' -> 'aja-wilson'. Stable ASCII, trade-safe (no team).

    THE slug function — the page emitter and every cross-link must route
    through this one; two independent slugifiers will disagree eventually,
    and a link that 404s is worse than no link.
    """
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s


def canonical_url(cfg, path):
    """Absolute canonical URL for a site path ('/', '/players/foo/')."""
    return f"{cfg.base_url}{path}"


def sitemap_xml(cfg, paths, lastmod):
    """One <url> per path, all stamped with the build's data date. The daily
    build regenerates this whole file; lastmod moving forward each morning is
    the truthful signal (the stats on every page really did update)."""
    urls = "".join(
        f"  <url><loc>{esc(canonical_url(cfg, p))}</loc>"
        f"<lastmod>{lastmod}</lastmod></url>\n"
        for p in paths
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n"
    )


def write_sitemap(cfg, paths, lastmod):
    out = cfg.public_dir / "sitemap.xml"
    out.write_text(sitemap_xml(cfg, paths, lastmod))
    return out


def robots_txt(cfg):
    """Minimal on purpose: allow-all plus the absolute Sitemap line.

    Cloudflare's managed robots.txt MERGES with (prepends to) an origin file
    rather than replacing it, so the Content Signals block is deliberately
    NOT replicated here — Cloudflare owns that policy and keeps it current;
    we own the sitemap. Duplicating their block would serve it twice and
    freeze our copy the day it was written. Verify the merge on the live
    host after any deploy that changes this file (see DEPLOY.md); the
    observed before/after captures live in reference/robots-txt-observed/.
    """
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {canonical_url(cfg, '/sitemap.xml')}\n"
    )


def write_robots(cfg):
    out = cfg.public_dir / "robots.txt"
    out.write_text(robots_txt(cfg))
    return out


def person_jsonld(name, url, team_name):
    """Schema.org Person for a player page. Deliberately sparse: identity
    and current affiliation only — facts the daily build actually knows.
    (SportsTeam markup arrives with team pages.)"""
    data = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": name,
        "url": url,
        "jobTitle": "Basketball player",
        "affiliation": {"@type": "SportsTeam", "name": team_name},
    }
    return json.dumps(data, ensure_ascii=False)
