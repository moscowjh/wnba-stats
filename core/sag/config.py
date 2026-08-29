"""League configuration — the one place a league's identity and file layout live.

D13: the config owns ``slug``, ``season`` and ``data_dir``; **filenames are
derived by convention, not listed.** The alternative was twelve literal path
constants (four files x three leagues) repeating an identical shape with one
word changed, where two leagues could collide by a typo. Convention makes that
collision impossible rather than merely unlikely, and a fourth league costs
zero new path lines.

Every field that derives a path is also overridable, for the league that
eventually needs an odd one — the override is the escape hatch that keeps the
convention from having to be right forever.

This is also what makes D12 cheap: with all of a league's data under one
directory, the Actions cache path is one line instead of four hand-maintained
filenames that a later data file could silently miss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LeagueConfig:
    """Identity and paths for one league's site.

    ``site_dir`` anchors everything to the site's own directory rather than to
    the process working directory. That distinction is not cosmetic: before the
    Phase 1 restructure ``build_stats_page.py`` wrote ``social_payload.json``
    relative to CWD while ``post_to_bluesky.py`` read it relative to its own
    file, which agreed only because both scripts sat in the repo root. Moving
    them apart would have silently broken the daily post. Anchoring to
    ``site_dir`` makes the two agree by construction, from any CWD.
    """

    slug: str
    season: str
    site_dir: Path

    #: League label as shown to readers ("WNBA", "WWC") — page titles and
    #: the shared masthead derive from it, e.g. f"{display_name} {season} —
    #: At a Glance".
    display_name: str = ""
    #: Canonical public origin, no trailing slash. Canonical URLs, the
    #: sitemap, and robots.txt's absolute Sitemap line all derive from it —
    #: robots.txt is per-host, so every league needs its own.
    base_url: str = ""
    #: Cloudflare Web Analytics token for this site's beacon; None renders
    #: no beacon (a league can launch unmeasured on purpose, not by accident).
    cf_analytics_token: str | None = None
    #: Jersey-number prefix as this league renders it ("#"; WWC uses "No.").
    jersey_prefix: str = "#"
    #: The one design token a site is allowed to diverge on (Option C,
    #: 2026-08-23). WNBA amber; WWC overrides to cyan "#35D0FF". The other
    #: six tokens in `sag.render.chrome` stay literally shared, so a second
    #: site reads as the same publication with a different accent rather than
    #: as a fork. Passed to `chrome.tokens_css()`; never hand-write a :root.
    accent: str = "#f5a623"
    #: Player pages show an "Nth in <league>" badge only inside this cutoff.
    #: Anchored to the size of the league's honour pool, not a bare integer —
    #: NCAAW's will differ from a 15-team league's.
    rank_badge_top_n: int = 20

    # Overrides — leave as None to take the convention.
    data_dir_override: Path | None = None
    public_dir_override: Path | None = None
    #: Files that must stay at the repository root because something outside
    #: this repo reads them by path. Currently `validation_report.json`, which
    #: `workers/cron/worker.js` fetches from raw.githubusercontent at
    #: `main/validation_report.json`. That Worker is NOT deployed by CI, so
    #: moving the file would break the health check silently and require a
    #: manual `wrangler deploy` to repair — the same trap D8 avoided by
    #: keeping the WNBA workflow named `build.yml`.
    repo_root_override: Path | None = None

    _derived: dict = field(default_factory=dict, repr=False, compare=False)

    # ── Directories ──────────────────────────────────────────────────────
    @property
    def data_dir(self) -> Path:
        """Fetched data — CSV/JSON accumulated across runs, gitignored.

        One directory per league is what lets the Actions cache key name a
        directory instead of a file list (D12).
        """
        return self.data_dir_override or (self.site_dir / "data")

    @property
    def public_dir(self) -> Path:
        """What Cloudflare serves. `wrangler.toml` sits beside it in site_dir
        and points at `./public`, resolved relative to the config file."""
        return self.public_dir_override or (self.site_dir / "public")

    @property
    def repo_root(self) -> Path:
        """Repo root — `sites/<slug>/` is two levels down."""
        return self.repo_root_override or self.site_dir.parent.parent

    # ── Fetched data, derived by convention ──────────────────────────────
    @property
    def player_box(self) -> Path:
        return self.data_dir / f"player_box_{self.season}.csv"

    @property
    def team_box(self) -> Path:
        return self.data_dir / f"team_box_{self.season}.csv"

    @property
    def pbp(self) -> Path:
        return self.data_dir / f"pbp_{self.season}.csv"

    @property
    def linescores(self) -> Path:
        return self.data_dir / f"linescores_{self.season}.json"

    @property
    def schedule_today(self) -> Path:
        return self.data_dir / "schedule_today.json"

    # ── Build outputs ────────────────────────────────────────────────────
    @property
    def page_output(self) -> Path:
        """The built page, before it is copied to `public/index.html`."""
        return self.site_dir / f"{self.slug}-{self.season}-stats-explorer.html"

    @property
    def index_html(self) -> Path:
        return self.public_dir / "index.html"

    @property
    def og_image(self) -> Path:
        return self.public_dir / "og.png"

    @property
    def social_payload(self) -> Path:
        """Written by the build, read by the Bluesky post. Anchored to
        site_dir so writer and reader cannot disagree — see the class
        docstring for the bug this prevents."""
        return self.site_dir / "social_payload.json"

    # ── Files pinned to the repo root by an external reader ──────────────
    @property
    def validation_report(self) -> Path:
        """Read by `workers/cron/worker.js` from the repo root over
        raw.githubusercontent. Do not move without redeploying that Worker
        by hand — nothing in CI will do it for you."""
        return self.repo_root / "validation_report.json"

    @property
    def player_crosswalk(self) -> Path:
        """Persisted ESPN athlete_id -> WNBA PLAYER_ID matches. Committed by
        the build; kept beside the validation report it is produced with."""
        return self.repo_root / "player_id_crosswalk.json"

    def ensure_dirs(self) -> None:
        """Create the directories the build writes into. Safe to call every
        run; a fresh CI checkout has neither."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.public_dir.mkdir(parents=True, exist_ok=True)
