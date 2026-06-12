# WNBA Stats Explorer — Build Documentation

## Purpose

A self-contained HTML stats page for the 2026 WNBA season, live at [wnba.statsataglance.com](https://wnba.statsataglance.com). Built in the spirit of [plaintextsports.com](https://plaintextsports.com): fast, minimal, no ads, monospace aesthetic. Intended as a fan-facing WNBA stats product with opinionated defaults for at-a-glance use, especially on mobile at games.

---

## Files

```
basketball-data/WNBA/
├── build_stats_page.py            # Build script — run this to regenerate HTML
├── fetch_data.py                  # ESPN box score fetcher (used by GitHub Actions)
├── prototype_site.py              # UX prototype (reference only, not deployed)
├── WNBA-2026-stats-explorer.html  # Output — static, self-contained, no server needed
├── wnba_player_box_2026.csv       # Per-game player box scores (source: wehoop / ESPN)
├── wnba_team_box_2026.csv         # Per-game team box scores (source: wehoop / ESPN)
├── DEPLOY.md                      # Infrastructure, domain, cron/health-check details
├── .github/workflows/daily.yml    # GitHub Actions: fetch → build → commit → deploy
└── cron-worker/                   # Cloudflare Worker: triggers daily builds + health check
```

---

## Data Pipeline

**Source:** ESPN's undocumented WNBA box score API, accessed via [wehoop](https://wehoop.sportsdataverse.org/) R package (local/manual) or `fetch_data.py` (GitHub Actions, daily automated).

**Local build:** After updating the CSVs, run:
```bash
python3 /Users/jasonhhorowitz/projects/basketball-data/WNBA/build_stats_page.py
```

**Production pipeline:** Cloudflare cron Worker dispatches GitHub Actions at 11:17 UTC daily → `fetch_data.py` pulls fresh box scores → `build_stats_page.py` bakes HTML → committed to `public/index.html` → Cloudflare Pages redeploys automatically. Health check at 11:45 UTC emails on failure only. See `DEPLOY.md` for full details.

---

## What the Build Script Computes

### Standings
From `wnba_team_box_2026.csv` grouped by team, sorted by Win% descending:

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

### Team Efficiency (Four Factors)
Computed via a **self-join** on `wnba_team_box_2026.csv`: each team's game row is joined to its opponent's game row using `game_id` + `opponent_team_id = team_id`. This provides opponent box-score stats needed for defensive factors.

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

### Abbreviations
Static reference tab explaining all stat abbreviations, grouped to match tab order: Standings, Leaders, Team Efficiency, Team Totals & Players.

---

## HTML / UI Decisions

- **No JavaScript frameworks** — vanilla JS only, no build step
- **Monospace font** (`Courier New`), dark background, amber accent — plaintextsports aesthetic
- **Tab navigation** — six tabs: Standings, Leaders, Team Efficiency, Team Totals, Players, Abbreviations
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

## Stats Not Yet Included (future work)

- **Advanced player stats** — PER, BPM, VORP, usage rate; require play-by-play or external source
- **Opponent stats tab** — defensive stats on a per-player basis
- **Season-over-season** — historical comparison; wehoop has data back to 2002
- **Game log view** — per-game results for a selected player or team
- **"At-a-glance" default view** — curated per-tab column sets for casual fans, with full depth one gesture away (see top-level `CLAUDE.md` for design context)

---

## Code Structure (build_stats_page.py)

After the June 12, 2026 comprehensive rewrite:

1. **Formatting helpers** — `ma()`, `fmt_winpct()`, `pct()`, `f1()`, `short_name()`
2. **HTML table helpers** — `df_to_html()`, `ff_to_html()`
3. **Data computation** — one function per concern: `load_data()`, `compute_standings()`, `compute_player_base()`, `build_player_stats_df()`, `compute_team_stats()`, `compute_four_factors()`, `compute_leaders()`
4. **HTML section builders** — one per tab: `build_standings_section()`, `build_leaders_section()`, `build_team_efficiency_section()`, `build_team_totals_section()`, `build_players_section()`, `build_abbreviations_section()`
5. **CSS / JS** — extracted into `PAGE_CSS` and `PAGE_JS` module-level constants
6. **Assembly** — `build_option_lists()`, `assemble_page()`, `main()` under `if __name__ == '__main__':`

---

## Data Licensing Note

Sports statistics are factual and not copyrightable under US law (*NBA v. Motorola*, 2nd Circuit 1997). The wehoop package and `fetch_data.py` pull from ESPN's undocumented public API — no auth required, widely used by the sports analytics community. This is non-commercial use. See `WNBA-stats-site.md` in the top-level projects directory for full licensing research.
