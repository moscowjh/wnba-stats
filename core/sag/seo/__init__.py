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


def social_tags(cfg, path, title, description, *, og_type="website",
                card=None, image_alt=None, twitter_description=None):
    """Open Graph + Twitter tags for one page. Absolute URLs, per spec.

    THE social-tag function. Three emitters used to hand-roll this block and
    had drifted three ways (two image URLs built differently, one site with no
    tags at all) — a link preview is the one surface where the drift is
    invisible locally and permanent once a platform caches it.

    The image is emitted ONLY when the site actually ships one
    (`cfg.og_image.exists()`). A card that references a missing PNG previews
    worse than no card at all, and every one of these platforms caches the
    result — so this follows the same correct-or-blank posture as the
    2026-07-03 line-score fix rather than emitting a hopeful URL. The practical
    effect is that a site gains its card the moment the PNG lands, with no code
    change: drop `public/og.png` in and the next build starts emitting it.

    `card` overrides the card type. It exists for ONE unresolved case — the
    WNBA player pages ship `summary` where the main page ships
    `summary_large_image`, and nobody now knows whether that was deliberate
    (a profile arguably wants the compact card) or drift. Jason chose to keep
    the difference explicit rather than silently unify it, 2026-09-02. Delete
    the argument if it is ever settled.

    `image_alt` and `twitter_description` exist because the WNBA main page
    deliberately ships a SHORTER twitter:description than its og:description
    and a bespoke og:image:alt. Flattening those to one string would have been
    a silent copy change on the live page, so they stay overridable and the
    default is the obvious one.
    """
    tags = [
        f'<meta property="og:type" content="{esc(og_type)}">',
        '<meta property="og:site_name" content="statsataglance">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:url" content="{esc(canonical_url(cfg, path))}">',
    ]
    has_image = cfg.og_image.exists()
    if has_image:
        image_url = f"{cfg.base_url}/{cfg.og_image.name}"
        tags += [
            f'<meta property="og:image" content="{esc(image_url)}">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            f'<meta property="og:image:alt" content="{esc(image_alt or title)}">',
        ]
    # twitter:card is what makes X/iMessage/Slack/WhatsApp draw a card at all —
    # its ABSENCE (not a missing image) is why the WWC links rendered bare.
    tags.append('<meta name="twitter:card" content="'
                f'{card or ("summary_large_image" if has_image else "summary")}">')
    tags += [
        f'<meta name="twitter:title" content="{esc(title)}">',
        '<meta name="twitter:description" content="'
        f'{esc(twitter_description or description)}">',
    ]
    if has_image:
        tags.append(f'<meta name="twitter:image" content="{esc(image_url)}">')
    return tags


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
