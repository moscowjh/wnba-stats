"""WWC 2026 site configuration.

The second LeagueConfig, and therefore the first proof that `sag.config`,
`sag.render.chrome` and `sag.seo` are genuinely league-agnostic rather than
WNBA-shaped with the names filed off. Everything league-specific is declared
here; paths derive from it by convention.

Option C (2026-08-23): this site keeps the shared near-black ground and
diverges on exactly one token, `--accent` — amber → cyan. Typography differs
too (sans for prose, mono scoped to data), but `font-family` has always lived
per-emitter, so that costs `core/` nothing.
"""

from pathlib import Path

from sag.config import LeagueConfig

WWC = LeagueConfig(
    slug="wwc",
    season="2026",
    site_dir=Path(__file__).resolve().parent,
    display_name="WWC",
    base_url="https://wwc.statsataglance.com",
    # None on purpose. A league is allowed to launch unmeasured — the usage
    # beacon (which is token-free and carries site="wwc") still reports, so
    # cross-site questions like "does the Cup site hand readers back to WNBA?"
    # are answerable from day one without a second Web Analytics property.
    cf_analytics_token=None,
    # The trailing space is deliberate. Both emitters concatenate this
    # directly ("#" + "22" → "#22"), so a prefix that is a word rather than
    # a symbol has to carry its own separator or it renders "No.22".
    jersey_prefix="No. ",
    accent="#35D0FF",
)

# ── WWC-only presentation facts ───────────────────────────────────────────
# These are one-site nav/label decisions, not league-config concepts, so they
# stay here rather than growing `sag.config` a field per site.

#: The WNBA site's fifth tab is "Key". Renamed here to "Guide" (Jason,
#: 2026-08-25): on a prose-heavy tournament site "Key" undersells a page that
#: is now the primer for someone who did not know this event existed.
#:
#: ⚠️ Provisional pairing with GUIDE_IS_LANDING below. Jason floated
#: "FIBA WWC Guide" for the case where the Guide is NOT the landing page and
#: has to identify itself in a nav full of peers. While it IS the landing
#: page the masthead already says which tournament this is, so the short
#: label wins.
GUIDE_TAB_LABEL = "Guide"

#: Which surface is the front door. Flipped to True 2026-08-26 for a review
#: round, on user feedback that read "I didn't know there was a women's
#: basketball world cup" — if that is the first reaction, a schedule is the
#: wrong thing to open with.
#:
#: This is a real switch, not a comment: it moves Guide to `/` and Games to
#: `/games/`, and the nav, canonicals, sitemap and analytics keys all follow.
#: Flipping it back is one edit. **Decide before the site is indexed** —
#: after that, changing the front door means redirects.
GUIDE_IS_LANDING = True

#: The tournament's own identity, as it appears in the masthead and titles.
TOURNAMENT_NAME = "FIBA Women's Basketball World Cup"
TOURNAMENT_STRAP = "Berlin · 4–13 September 2026"
