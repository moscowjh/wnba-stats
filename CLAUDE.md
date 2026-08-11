# statsataglance — production repo notes

This is the live, deployed WNBA stats site (`wnba.statsataglance.com`) and its own
git repo (`moscowjh/wnba-stats`, **public**). Mobile-first, plaintextsports
aesthetic, **single-file / zero-dependency** — performance is the brand, so weigh
every change against it.

> **This repo is PUBLIC.** Private material does not belong here. Since the
> 2026-08-07 workspace migration, `.gitignore` covers build artifacts and secrets
> only — it is no longer a deny-list holding back private documents. If something
> private turns up in this directory, move it to `statsataglance-docs/` or
> `wbb-lab/` rather than adding a `.gitignore` line.

## The three repos

Siblings on disk under `~/projects/`, unrelated to each other in git. **Relative
markdown links cannot resolve between them** — reference across by path, as plain
text, never as a link.

| Repo | Visibility | Holds |
|---|---|---|
| `statsataglance/` (here) | **public** | Everything that deploys, plus the engineering docs that govern it |
| `statsataglance-docs/` | private | Brief, backlog, sequencing plan, growth, copy, licensing, incidents, brand art |
| `wbb-lab/` | private | Exploratory analysis: FIBA parser, NCAAW spike, Unrivaled data, retired prototypes |

**The lab imports production. Production never imports the lab.** When the lab
produces something the site needs, that code is *moved* across, deliberately.

## Canonical planning docs — now in the docs repo

This repo is a git root, so a Claude Code session here will **not** auto-load
anything above it. These are the source of truth for anything product-level;
reach them by path from the home directory (`~/projects/...` — expand `~` when a
tool needs a fully absolute path):

- **Backlog — single source of truth** (priorities, Decisions Log, Completed / Shipped Log):
  `~/projects/statsataglance-docs/statsataglance-backlog.md`
  **Record cross-cutting product/backlog items there even when the work happens
  here** — don't let backlog items live only in commit messages.
- **Strategy (canonical):** `statsataglance-docs/PRODUCT-BRIEF.md` (a manually-synced mirror of a Google Doc).
- **Sequencing/ordering:** `statsataglance-docs/statsataglance-sequencing-plan.md`.
- **Workspace layout + git boundaries:** `statsataglance-docs/workspace-architecture.md`.
- **Workspace overview + candidate products:** `~/projects/CLAUDE.md`.

## Engineering docs — these ship WITH the code, here

They are read while writing code, not while deciding what to build, so they live
in the repo they govern (`workspace-architecture.md` D4):

- `docs/build-internals.md` — build-script internals, stat formulas, UI decisions, known issues, code structure.
- `docs/data-sources.md` — per-league feed capability matrix. **Every ESPN caller must go through the adapter's origin handling; nothing else may construct an ESPN URL.**
- `docs/wnba-leader-qualification-rules.md` — the qualification rules as implemented; cited from `compute_leaders()`.
- `DEPLOY.md` — infrastructure, domain, cron/health-check details.

## Build / pipeline

Daily GitHub Actions build: `fetch_data.py` pulls from ESPN's public API (player
box + team box + play-by-play + today's schedule JSON) → `validate_stats.py`
gates → `build_stats_page.py` bakes one static HTML → copied to
`sites/wnba/public/index.html` → **`npx wrangler deploy` from inside the Action**. A
Cloudflare cron Worker (`workers/cron/`) dispatches the build ~11:17 UTC plus
health checks at 11:45 / 13:15 / 14:45 UTC (emails on failure only).

**`wrangler deploy` from the workflow is the only deploy path.** Cloudflare's
Connect-to-Git integration was disconnected in June 2026 — a commit to `main`
does not publish anything on its own. Never run `npx wrangler deploy` locally.

Run the build with the local venv's Python (3.13 — the build uses 3.12+ syntax):
`.venv/bin/python sites/wnba/build_stats_page.py`. It needs `sag` on the path —
`.venv/bin/pip install -e core/`, once.

## Repo layout (restructured 2026-08-11, Phase 1)

```
core/       shared library, installed with `pip install -e core/`, imported as `sag`
sites/      one directory per site; wnba/ has its own config.py, wrangler.toml, data/, public/
workers/    cron/ analytics/ espn-proxy/ — shared infra, NOT deployed by CI
docs/       engineering docs, shipped with the code they govern
```

Reasoning lives in `statsataglance-docs/workspace-architecture.md` §9 (D7–D13) —
don't re-derive it here. Three things are load-bearing and easy to break:

1. **`.github/workflows/build.yml` must keep that filename.** `workers/cron/worker.js:39`
   hardcodes `const WORKFLOW = "build.yml"` in both the dispatch and the health-check
   query. Rename it and the daily build silently stops firing.
2. **`validation_report.json` must stay at the repo root.** The same Worker fetches it
   from `raw.githubusercontent.com/<repo>/main/validation_report.json`.
3. **No Worker is deployed by CI.** The Action's `wrangler deploy` ships the WNBA site
   only. A Worker change needs a manual `wrangler deploy` from its own directory, and
   nothing will remind you — a page sending a field the deployed Worker ignores fails
   silently.

Paths are never spelled out in site scripts: `sag.config.LeagueConfig` owns
`slug`/`season`/`data_dir` and **derives** filenames by convention (D13), anchored to
the site directory rather than the working directory. Add a league by adding a config,
not by adding paths.

## Local working gotchas

Two things that bite repeatedly. They lived only in per-directory Claude memory
until 2026-08-07, which meant they were invisible from inside this repo — so
they're recorded here instead, where they're versioned and travel with the code.

**1. The local CSVs go stale, and nothing tells you.**
`sites/wnba/data/player_box_2026.csv`, `sites/wnba/data/team_box_2026.csv`, and `sites/wnba/data/pbp_2026.csv` are
**build artifacts, not source** — gitignored, and carried between CI runs by the
Actions cache rather than by git. The daily build refreshes them *in CI only*; it
never touches the local copies. Nothing needs them locally for the site to work,
which is exactly why they drift unnoticed.

> **Before answering any season-wide question from the local CSVs, check the max
> `game_date` first.** If it's behind, refresh with `.venv/bin/python sites/wnba/fetch_data.py`
> (incremental — it only pulls games it doesn't have).

Two real instances: on 2026-07-10 the local team box ended July 2, a week behind.
On 2026-08-07 the local set was at 225 games while CI was at 232, and a
byte-comparison prediction was made backwards as a result — the local copy was
assumed to be the fresher one.

Note ESPN dates late games in **UTC**, so a "July 9" night slate can appear under
`2026-07-10`.

**2. Always `git pull --rebase` before pushing.**
The daily build commits and pushes `sites/wnba/public/index.html` (plus
`validation_report.json`, `player_id_crosswalk.json`, `usage_history.jsonl`) as
`wnba-stats-bot` every morning. Push without pulling and it's rejected because
the remote is ahead. Applies to manual pushes and any scripted commit-and-push.

**3. Never construct an ESPN URL by hand.** Go through the adapter's origin
handling — `ESPN_ORIGIN`. `site.api.espn.com` and `site.web.api.espn.com` mirror
the same paths and *either* can fail host-wide; the first 403'd for ~4 hours on
2026-08-05 while the second served fine. A standalone spike script that hardcoded
the dead host produced a false negative that day. See `docs/data-sources.md`.

## Session closeout (end of any session that ships or decides something)

Keep it light — this is the only channel by which work here flows back to the
Cowork backlog/memory:

1. **Update the backlog** in the docs repo: move finished items to the
   **Completed / Shipped Log** (dated), and add a one-line **Decisions Log**
   entry for any non-obvious choice or trade-off.
2. **Write a clear, dated commit message** — the commit history is the backup
   record; a Cowork sync later cross-checks `git log` against the Completed log.

No separate write-up needed — the Completed log *is* the changelog. Log material
changes only (shipped features, decisions, gotchas), not every tweak.

Note: the docs repo has no bot committing to it, so its changes sit uncommitted
until swept by hand. From a **Cowork** session Claude can create files inside
`.git/` but cannot delete them, so it cannot reliably hold a git lock — there,
Claude edits and Jason commits. Claude Code terminal sessions can commit normally.

## Site state

Seven tabs — **Games** (landing: today's schedule + yesterday's finals linked to
inline box scores), Standings, Leaders, Efficiency, Team Totals, Players, Key.
Open follow-ups are in the backlog.
