# Build internals — `build_stats_page.py`

_Engineering documentation for the WNBA build. Companion to `DEPLOY.md` (infrastructure) and `docs/data-sources.md` (per-league feed capabilities)._

## Purpose

A self-contained HTML stats page for the 2026 WNBA season, live at [wnba.statsataglance.com](https://wnba.statsataglance.com). Built in the spirit of [plaintextsports.com](https://plaintextsports.com): fast, minimal, no ads, monospace aesthetic. Intended as a fan-facing WNBA stats product with opinionated defaults for at-a-glance use, especially on mobile at games.

---

## Files

```
statsataglance/                    # repo root (was basketball-data/WNBA/ until 2026-08-07)
│                                  # restructured into core/ + sites/ + workers/ 2026-08-11
├── core/                          # Shared library — `pip install -e core/`
│   ├── pyproject.toml             # THE dependency declaration (replaced requirements.txt)
│   └── sag/                       # Imported as `sag`, not `core` (D11)
│       ├── config.py              # LeagueConfig — owns slug/season/data_dir, derives paths
│       ├── adapters/              # Per-upstream data clients (ESPN, FIBA) — lands here next
│       ├── render/                # Page/component engine — the SEO work forces this out
│       └── seo/                   # Slugs, sitemap, canonical/meta, Schema.org
├── sites/
│   └── wnba/                      # One directory per site; a second league is a sibling
│       ├── config.py              # The WNBA LeagueConfig instance
│       ├── build_stats_page.py    # Build script — run this to regenerate HTML
│       ├── fetch_data.py          # ESPN box score fetcher (used by GitHub Actions)
│       ├── validate_stats.py      # Leader/stat validation; gates the Bluesky post
│       ├── post_to_bluesky.py     # Nightly factoid post (never blocks the deploy)
│       ├── wrangler.toml          # THIS SITE's Cloudflare config, not shared infra
│       ├── data/                  # Fetched CSV/JSON — gitignored, Actions-cached
│       ├── public/index.html      # The deployed page
│       └── wnba-2026-stats-explorer.html   # Build output, copied to public/
├── workers/                       # Shared infra — NOT deployed by CI
│   ├── cron/                      # Build dispatch + health checks
│   ├── analytics/                 # Usage beacon → Analytics Engine
│   └── espn-proxy/                # Contingency only, NOT deployed
├── usage_report.py                # Analytics rollup → usage_history.jsonl (shared, root)
├── validation_report.json         # ⚠️ Root-pinned: workers/cron reads it by raw URL
├── DEPLOY.md                      # Infrastructure, domain, cron/health-check details
├── docs/                          # build-internals.md (this file), data-sources.md
└── .github/workflows/build.yml    # ⚠️ Name is load-bearing — workers/cron hardcodes it
```

UX prototypes (`prototype_site.py`, `prototype_players.py`, the `*-prototype.html`
files) moved to `wbb-lab/wnba/prototypes/` in the 2026-08-07 workspace migration.

---

## Data Pipeline

**Source:** ESPN's undocumented WNBA box score API, accessed via [wehoop](https://wehoop.sportsdataverse.org/) R package (local/manual) or `fetch_data.py` (GitHub Actions, daily automated).

**Local build:** After updating the CSVs, run from the repo root with the local
venv's Python (3.13 — the build uses 3.12+ syntax):
```bash
.venv/bin/pip install -e core/          # once — puts `sag` on the path
.venv/bin/python sites/wnba/build_stats_page.py
```

Run it from anywhere: paths resolve through `sag.config.LeagueConfig`, which
anchors to the *site directory* rather than the working directory.

**Production pipeline:** Cloudflare cron Worker dispatches GitHub Actions at 11:17 UTC daily → `fetch_data.py` pulls fresh box scores → `validate_stats.py` gates → `build_stats_page.py` bakes HTML → copied to `sites/wnba/public/index.html` → **`npx wrangler deploy` from inside the Action**. Health checks at 11:45 / 13:15 / 14:45 UTC auto-rebuild on a fixable problem and email only what survives the final pass. See `DEPLOY.md` for full details.

> **Deploy path.** `wrangler deploy` from the workflow is the *only* deploy path.
> Cloudflare's Connect-to-Git integration was disconnected in June 2026 — a commit
> to `main` does **not** publish anything on its own.

---

## What the Build Script Computes

### Standings
From `sites/wnba/data/team_box_2026.csv` grouped by team, sorted by Win% descending:

| Column | Formula |
|--------|---------|
| W / L | Sum of `team_winner`; L = GP - W |
| Win%   | W / GP |
| PF / PA | Mean of `team_score` / `opponent_team_score` |
| +/-    | PF - PA |
| XW / XL | Pythagorean expectation: `PF_total^13.91 / (PF_total^13.91 + PA_total^13.91) * GP` |

A **dashed playoff cutoff line** appears after the 8th-place team (top 8 make the playoffs).

### Leaders
Top 10 per category with **WNBA qualifying minimums** prorated by `max_gp / 44`:

| Category | Full-season minimum | Prorated formula |
|----------|-------------------|------------------|
| PPG | 525 pts | 525 × (max_gp / 44) |
| RPG | 250 reb | 250 × (max_gp / 44) |
| ORPG | 70% GP | GP ≥ 0.7 × max_gp |
| APG | 150 ast | 150 × (max_gp / 44) |
| SPG | 55 stl | 55 × (max_gp / 44) |
| BPG | 40 blk | 40 × (max_gp / 44) |
| TPG | 70% GP | GP ≥ 0.7 × max_gp |
| 3PT% | 25 3PM | 25 × (max_gp / 44) |
| FT% | 50 FTM | 50 × (max_gp / 44) |
| eFG% | 100 FGM | 100 × (max_gp / 44) |
| TS% | 100 FGM | 100 × (max_gp / 44) |

Player names are **clickable links** that navigate to the Players tab with that player's name pre-filled in the search. Leaders support **team filter** and **player search**.

### Efficiency (Four Factors)
Computed via a **self-join** on `sites/wnba/data/team_box_2026.csv`: each team's game row is joined to its opponent's game row using `game_id` + `opponent_team_id = team_id`. This provides opponent box-score stats needed for defensive factors.

**Possession estimate:**
```
Poss = FGA - ORB + total_turnovers + 0.44 * FTA
```

| Stat | Formula |
|------|---------|
| ORtg | 100 × PTS_total / Poss_avg, where Poss_avg = (Poss + OPP_Poss) / 2 |
| DRtg | 100 × OPP_PTS_total / Poss_avg (same denominator as ORtg) |
| NRtg | ORtg − DRtg |
| Pace | 40 × (Poss + OPP_Poss) / (2 × team_minutes / 5) — normalized to 40 min, OT-adjusted |
| O_eFG% | (FGM + 0.5 × 3PM) / FGA × 100 |
| O_TOV% | TOV / (FGA + 0.44×FTA + TOV) × 100 |
| O_ORB% | ORB / (ORB + OPP_DRB) × 100 |
| O_FT/FGA | FTM / FGA |
| D_eFG% | (OPP_FGM + 0.5 × OPP_3PM) / OPP_FGA × 100 |
| D_TOV% | OPP_TOV / (OPP_FGA + 0.44×OPP_FTA + OPP_TOV) × 100 |
| D_DRB% | DRB / (DRB + OPP_ORB) × 100 |
| D_FT/FGA | OPP_FTM / OPP_FGA |

Sorted by NRtg descending. League Average row pinned to bottom. Group headers labeled "Offensive 4 Factors" / "Defensive 4 Factors". Includes **matchup filter** to compare any two teams.

### Team Totals (per game)
Standard box score columns averaged per game, with PPG as the first stat column. FG, 3PT, FT displayed as `made/attempted`. Includes a **League Average** row pinned to the bottom and a **matchup filter bar** to compare any two teams.

### Player Stats (season totals + per-game)
All players who appeared in a game are shown. No minimum games or minutes filter. Display features:
- **Abbreviated first names** (e.g., "C. Clark") with **team chip** (gray team abbreviation)
- **Per-game stats**: MPG, PPG
- **Season totals**: FG, 3PT, FT, OR, DR, TR, A, ST, B, TO, PF (with FG%, 3PT%, FT% columns)
- **Filtering**: two team dropdowns (selecting two teams sorts players by team), minimum GP slider, live search by name or team
- No 3-player comparison dropdowns (removed in June 2026 rewrite)

### Key
Static reference tab explaining all stat abbreviations, grouped to match tab order: Standings, Leaders, Efficiency, Team Totals & Players.

---

## HTML / UI Decisions

- **No JavaScript frameworks** — vanilla JS only, no build step
- **Monospace font** (`Courier New`), dark background, amber accent — plaintextsports aesthetic
- **Tab navigation** — seven tabs: Games, Standings, Leaders, Efficiency, Team Totals, Players, Key
- **Column sorting** — click any header to sort ascending/descending; League Average row stays pinned to bottom during sort
- **Sticky first column** — team/player name column stays visible when scrolling horizontally on all tables
- **Right-edge fade** — signals more columns to swipe to; hides when scrolled to the end
- **Win% format** — displayed as `.XXX` (sports convention, no leading zero); all-white text in standings (no green/red coloring)
- **Header** — shows "Stats as of {date}" derived from most recent game date in the data; `<meta name="data-through">` tag for health-check verification
- **Playoff cutoff line** — dashed amber line (40% opacity) after 8th-place team in standings, with italic note
- **Player names** — abbreviated first names with gray team chip (e.g., "C. Clark NYL")
- **Leader links** — player names in leader cards are amber-underlined links that jump to the Players tab with search pre-filled
- **Team/player filtering** — leaders and players tabs both have team dropdown + search input; players tab adds two-team comparison and min GP control

---

## Known Issues / Bugs to Fix

- **FG% sorting** — FG column is displayed as a string (`28/62`), so clicking that column header sorts lexicographically, not numerically; either split into separate M/A and % columns or sort on the % column instead
- **Four Factors grouped headers** — the two-row header (Offensive / Defensive group labels) may not sort-highlight correctly on click
- **Pace formula** — matches BBRef methodology (Pace/40, OT-adjusted). Will still differ from stats.wnba.com, which counts actual possessions from play-by-play rather than using the Dean Oliver estimate
- **`total_turnovers` vs `turnovers`** — wehoop provides both; `total_turnovers` (team + individual) is used for possession estimates and team stats; individual `turnovers` is used for player stats. Verify this is correct.
- **DNP filtering** — players with `did_not_play == True` are excluded, but players with `active == False` or blank `reason` may need additional review
- **Sticky header row** — `position: sticky; top: 0` doesn't work inside `overflow-x: auto` containers (browser limitation). Header row scrolls out of view on long tables. Would require a fixed-height scroll container to fix.

---

## Future work

Roadmap items are **not** tracked here. The backlog is the single source of
truth: `statsataglance-docs/statsataglance-backlog.md`, under *Dev → WNBA site*
and *Product*.

This section previously carried five bullets. Three had already been duplicated
into the backlog and had drifted out of date — most notably "at-a-glance default
view," which the backlog and sequencing plan had since developed into the Phase 1
opening decision while this file still described it as unstarted. All five were
consolidated into the backlog on 2026-08-07. Keep it that way: this file
documents how the build *works*, not what it should do next.

---

## Code Structure (build_stats_page.py)

Established in the June 12, 2026 comprehensive rewrite; verified against the
source 2026-08-07.

1. **Formatting helpers** — `ma()`, `fmt_winpct()`, `pct()`, `f1()`, `short_name()`
2. **HTML table helpers** — `df_to_html()`, `ff_to_html()`, `_color_cell()`
3. **Load + guards** — `load_data()`, then `run_data_guards()` and `run_integrity_checks()`. The guards run before anything is computed, so a bad fetch fails the build rather than producing a plausible page.
4. **Data computation** — one function per concern: `compute_standings()` (with `_compute_streak()` / `_compute_last10()`), `compute_player_base()`, `compute_player_season()`, `compute_team_stats()`, `compute_four_factors()`, `compute_leaders()`
5. **HTML section builders** — one per tab: `build_games_section()`, `build_standings_section()`, `build_leaders_section()`, `build_team_efficiency_section()`, `build_team_totals_section()`, `build_players_section()`, `build_abbreviations_section()`
6. **Games-tab internals** — `build_games_section()` is by far the largest (~475 lines) and has its own helper layer: `_dow()`, `_fmt_min()`, `_game_ma()`, `_game_pct()`, `_record_through()`, `_game_sides()`, `_team_totals()`, `_team_stats_block()`, `_player_row()`, `_team_table()`, `_line_score()`, `_box_section()`, `_result_row()`, `_sched_row()`
7. **Side effects** — `emit_social_payload()` writes `social_payload.json` for the Bluesky step
8. **CSS / JS** — extracted into `PAGE_CSS` and `PAGE_JS` module-level constants
9. **Assembly** — `assemble_page()`, `main()` under `if __name__ == '__main__':`

**Phase 1 note:** this is the module the SEO refactor factors into `core/` +
`render/` + `seo/`. The Games-tab helper layer (6) is the natural first
component extraction — it is already a self-contained rendering unit.

---

## Data Licensing Note

Sports statistics are factual and not copyrightable under US law (*NBA v. Motorola*, 2nd Circuit 1997). The wehoop package and `fetch_data.py` pull from ESPN's undocumented public API — no auth required, widely used by the sports analytics community. This is non-commercial use.

Full licensing research lives in the private docs repo, at
`statsataglance-docs/WNBA-licensing-and-feasibility.md`. (Not a link: it is a
separate repository, so a relative path cannot resolve from here.)
