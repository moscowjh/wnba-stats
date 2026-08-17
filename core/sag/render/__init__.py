"""Rendering — the page/component engine shared across sites.

The SEO work is what forces this: emitting ~185 per-player and per-team
pages turns `build_stats_page.py`'s monolith into components plus a page
emitter, and that factoring IS the template layer FIBA and the playoffs
both consume (sequencing plan §3). Which is why Phase 1 ships first.

Empty at the restructure commit, by the same reasoning as `adapters/`.

First real occupant (2026-08-16): `chrome` — design tokens, footer,
scroll fade, usage/CF analytics, subpage masthead. The full component
refactor of the tab site's tables and sections remains deferred.
"""

from sag.render import chrome  # noqa: F401
