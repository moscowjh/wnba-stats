# Deploying the WNBA stats page (Cloudflare + GitHub Actions)

This turns the manual pipeline into: **every morning, fetch fresh box scores →
rebuild the static page → publish to a public URL.** No server, no R, no cost.

## How the pieces fit

```
GitHub Actions (cron, 7am ET)
    └─ python fetch_data.py        # pulls box scores + pbp + line scores + today's schedule (no R)
    └─ python build_stats_page.py  # bakes data into one static HTML
    └─ copies it to public/index.html and commits + pushes
    └─ npx wrangler deploy         # deploys directly to Cloudflare
            │
            ▼
Cloudflare Workers  →  serves public/index.html at wnba.statsataglance.com
```

GitHub does all the work — fetching, building, committing, AND deploying. The
Action calls `npx wrangler deploy` directly using a Cloudflare API token stored
as a GitHub secret. Cloudflare just serves the static result.

## Deploy architecture (revised June 2026)

**Cloudflare's Connect-to-Git integration has been disconnected.** The GitHub
Actions workflow is the ONLY deploy path. This was changed after an incident on
June 12 where the Git-trigger promoted the wrong Cloudflare version: a human
push (code change, no HTML rebuild) was promoted to active, while the subsequent
bot push (with the correct rebuilt HTML) was deployed but not promoted. The site
reverted to the old version and required manual intervention in the Cloudflare
dashboard to fix.

Root cause: when two pushes arrive close together, Cloudflare's Git-trigger may
promote the first and leave the second as an inactive version. With a single
deploy path (the Action calling `wrangler deploy`), every deploy uses the commit
that just rebuilt `public/index.html`, so the correct version is always active.

**Manual deploys:** push your code changes to `main`, then trigger the Action
(GitHub Actions tab → "Run workflow", or `gh workflow run build.yml` from CLI).
The Action checks out the latest code, fetches data, rebuilds, commits, and
deploys — all in one run.

## Static site on Cloudflare Workers

Cloudflare has merged "Pages" into "Workers." This repo includes a small
`wrangler.toml` that tells Wrangler to serve the `public/` folder as a static
site, so `npx wrangler deploy` just works. You do **not** need to write any
Worker code — it's still a plain static site under the hood.

## One-time setup

### 1. Put this folder in its own GitHub repo
Keep it separate from your big multi-sport workspace — you do **not** want the
1.75 GB DuckDB or large CSVs in a public repo. This folder already has a
`.gitignore` that excludes the CSVs (they're refetched in CI) and other scratch
files.

```bash
cd basketball-data/WNBA
git init
git add fetch_data.py build_stats_page.py requirements.txt .gitignore \
        wrangler.toml .github/workflows/daily.yml DEPLOY.md
git commit -m "WNBA stats site: automated daily build"
# create an empty public repo on github.com first, then:
git remote add origin https://github.com/moscowjh/wnba-stats.git
git branch -M main
git push -u origin main
```

### 2. Build the page once and commit it
Cloudflare needs something in `public/` to serve on the very first deploy. The
HTML is generated, so build it locally (this uses your local CSVs and only needs
pandas — no fetch step required) and commit the result:

```bash
python3 build_stats_page.py            # regenerates WNBA-2026-stats-explorer.html
mkdir -p public
cp WNBA-2026-stats-explorer.html public/index.html
git add public/index.html
git commit -m "Add static site content"
git push
```

`git status` should show `public/index.html` as tracked — `.gitignore` blocks
`*.csv` but not `public/`.

> Alternative: instead of building by hand, trigger the GitHub Action once
> (step 4) and let it create and commit `public/index.html` for you. Either way,
> the folder must contain `index.html` before Cloudflare can serve anything.

### 3. Create Cloudflare API credentials
The Action deploys directly to Cloudflare via `npx wrangler deploy`. It needs
two secrets:

1. **Get your Account ID:** Cloudflare dashboard → Workers & Pages → Overview.
   The Account ID is in the right sidebar (a 32-character hex string).
2. **Create an API token:** Cloudflare dashboard → My Profile (top-right icon) →
   API Tokens → Create Token → **Custom token** with:
   - **Permissions:** Account / Workers Scripts / Edit
   - **Account Resources:** Include → your account
   - Leave everything else as default → Continue → Create Token
   - Copy the token (shown once).
3. **Store both as GitHub secrets:** GitHub repo → Settings → Secrets and
   variables → Actions → New repository secret:
   - `CLOUDFLARE_ACCOUNT_ID` → paste Account ID
   - `CLOUDFLARE_API_TOKEN` → paste the API token

### 4. Disconnect Cloudflare Connect-to-Git
This prevents the old Git-trigger from competing with the Action's deploy:

1. Cloudflare dashboard → Workers & Pages → `wnba-stats`
2. Settings → Build → **Disconnect from Git** (or Build Configuration →
   disconnect)
3. The Worker and its custom domain (`wnba.statsataglance.com`) remain intact —
   only the automatic Git-trigger is removed.

> **Note:** The `wnba-stats` Worker must already exist in Cloudflare before the
> Action can deploy to it. If starting from scratch, create it via Connect-to-Git
> first, verify it works, then disconnect and switch to Action-based deploys.

### 5. Test the automation
- In GitHub, open the **Actions** tab → **Daily WNBA stats build** → **Run
  workflow** (the `workflow_dispatch` button). Watch it fetch, build, commit,
  and deploy.
- The run log should show a successful `wrangler deploy` step at the end.
- The live site should update within seconds of the run completing.
- Each deploy includes a message ("Daily stats update: YYYY-MM-DD") visible in
  the Cloudflare deployments list (Workers & Pages → wnba-stats → Deployments).

## Cost
Cloudflare's free tier covers this comfortably: a static site on Workers serves
with generous free bandwidth and request limits, and the daily redeploy is one
build per day. Free at fan-sharing scale. The only paid piece is the domain
(below), ~$10–11/yr.

## Custom domain — `statsataglance.com` (chosen 2026-06-08)
The public URL is moving off `wnba-stats.horowitz-jason.workers.dev` (which has a
name in it) to a clean brandable domain, structured for multiple sports via
subdomains:

- `wnba.statsataglance.com` — this site ("WNBA stats at a glance")
- `mlb.statsataglance.com`, `tennis.statsataglance.com`, … — future sports, each
  its own Worker/site under the same brand
- `statsataglance.com` (apex) — redirect to the WNBA subdomain for now; later a
  simple index of covered sports

Setup steps:
1. **Register** `statsataglance.com` via Cloudflare dashboard → Domain
   Registration → Register Domains (~$10–11/yr, at-cost, free WHOIS privacy so
   the owner name is redacted). This is a purchase — done manually.
2. The domain's DNS zone is created on Cloudflare automatically.
3. **Point the subdomain at the Worker:** Workers & Pages → `wnba-stats` →
   Settings → Domains & Routes → Add → Custom Domain → `wnba.statsataglance.com`.
   Cloudflare provisions the DNS record + SSL automatically.
4. **Apex redirect:** Rules → Redirect Rules → `statsataglance.com` →
   `wnba.statsataglance.com`.
5. **(Optional) retire the workers.dev URL:** set `workers_dev = false` in
   `wrangler.toml` and push, so the only public URL is the custom domain.

No `wrangler.toml` change is needed for steps 1–4 — the custom domain is wired in
the dashboard.

## Adjusting the schedule
The schedule is controlled by the Cloudflare cron Worker (`cron-worker/`), which
calls GitHub's `workflow_dispatch` API at 11:17 UTC (7:17am ET in summer). Later
in the year, when ET shifts to EST (UTC-5), 11:17 UTC becomes 6:17am ET — adjust
the cron trigger in `cron-worker/wrangler.toml` if you want to hold ~7am.

## RESOLVED ISSUE — scheduled run not firing (opened 2026-06-08, resolved 2026-06-09)
**Resolution:** GitHub's native `schedule` missed three mornings straight (June 7,
8, 9). Replaced it with a **Cloudflare Cron Trigger** Worker (`cron-worker/`) that
calls GitHub's `workflow_dispatch` API at 11:17 UTC daily. Setup: create a
fine-grained GitHub PAT (repo `wnba-stats`, Actions: Read and write), then
`cd cron-worker && npx wrangler login && npx wrangler deploy && npx wrangler secret
put GH_TOKEN`. The Worker's `?key=` route (CRON_KEY secret) fires a manual test.
Keep `workflow_dispatch` in daily.yml — the Worker depends on it; the old
`schedule:` line can stay (harmless; concurrency guard prevents double runs). The
diagnosis and ruled-out checks below are kept for the record.

**Symptom:** the daily Action did not trigger on its own on June 7, 8, or 9.
Manual "Run workflow" works perfectly every time; only the `schedule` trigger is
affected.

**Ruled out (all verified):** workflow file is correct and on `main` with a valid
`17 11 * * *` cron; Actions are enabled ("Allow all actions"); the Settings →
Actions → General page is clean; no GitHub email/disable notice; the Actions tab
filtered by Event = `schedule` shows "no matching events" — i.e. GitHub has never
even *attempted* a scheduled run. The read-only "Workflow permissions" default is
NOT the cause: the workflow declares its own `permissions: contents: write`, and
the manual run's commit pushed fine.

**Diagnosis:** GitHub's native `schedule` trigger is unreliable for new/low-traffic
repos — it delays and silently drops runs. Each edit to the workflow file also
re-registers the schedule and tends to skip the first occurrence after a change.

**Plan:**
1. As of 2026-06-08, leave `daily.yml` untouched for 24h and see if it fires on
   its own June 9 now that the schedule has stopped being re-registered.
2. If it does NOT fire June 9: stop relying on GitHub's scheduler. Build a
   **Cloudflare Cron Trigger** Worker that calls GitHub's `workflow_dispatch` API
   each morning (`POST /repos/moscowjh/wnba-stats/actions/workflows/daily.yml/dispatches`,
   body `{"ref":"main"}`), authenticated with a fine-grained GitHub token (scoped
   to this repo, Actions: read/write) stored as a Worker secret. Cloudflare crons
   are reliable; the manual dispatch path already works, so this sidesteps the
   flaky native scheduler entirely. The workflow's `concurrency` guard makes a
   double-fire harmless.

**Workaround until resolved:** trigger manually (Actions → Daily WNBA stats build
→ Run workflow) whenever you want a fresh build.

## RESOLVED ISSUE — workflow_dispatch silently broken (opened 2026-06-16, resolved same day)

**Symptom:** The 11:17 UTC cron dispatch failed on June 16. The health check
correctly alerted at 11:45. Manual `gh workflow run daily.yml` returned HTTP 422:
"Workflow does not have 'workflow_dispatch' trigger" — even though the file
clearly contained `workflow_dispatch: {}`.

**Root cause:** A YAML parse error introduced on June 15 when adding a
`--message` flag to the `wrangler deploy` step:
```yaml
run: npx wrangler deploy --message "Daily stats update: $(date -u +'%Y-%m-%d')"
```
The colon-space in `update: $(date...)` is valid shell but invalid YAML — the
parser reads it as a nested mapping key, silently fails to parse the file, and
falls back to using the file path as the workflow name (visible in the API:
`"name": ".github/workflows/daily.yml"` instead of `"Daily WNBA stats build"`).
Because the file couldn't be parsed, GitHub didn't register `workflow_dispatch`
as a trigger, so all dispatches — both the cron Worker's and manual — were
rejected.

**Why it was hard to diagnose:** The file looked correct in both the repo and
the API's base64 content endpoint. GitHub returned no parse-error feedback on
push — the only clue was the wrong workflow name in the API response. Nudge
commits (trivial changes to force re-parsing) didn't help because the parse
error was still present.

**Fix (two parts):**
1. Removed the colon from the message: `"Daily stats update $(date ...)"`.
2. Renamed the workflow file from `daily.yml` to `build.yml` because GitHub's
   internal workflow registry was stuck on the broken state even after the parse
   error was fixed. The rename forced re-registration as a new workflow. The cron
   Worker (`cron-worker/worker.js`) was updated to dispatch `build.yml` and
   redeployed.

**Lesson — YAML gotcha for workflow files:** Never use an unquoted colon-space
inside a `run:` value in GitHub Actions YAML. The shell string looks fine, but
the YAML parser sees it as a mapping separator. Either drop the colon or wrap
the entire value in a YAML block scalar (`run: |`). This is especially
dangerous because GitHub gives no feedback when a workflow file fails to parse —
it silently degrades.

## Data source — ESPN direct (migrated 2026-06-23)

`fetch_data.py` fetches all data directly from ESPN's public API endpoints:

- **Scoreboard** (`site.web.api.espn.com/.../scoreboard?dates=YYYYMMDD`): discovers
  completed games by scanning date ranges.
- **Game Summary** (`site.web.api.espn.com/.../summary?event={game_id}`): provides
  player box scores, team box scores, play-by-play, and each game's **official
  per-quarter line scores** (`header.competitions[].competitors[].linescores`).

The fetch is **incremental**: on each run it loads the existing CSVs, scans the
scoreboard from `max(game_date) - 1 day` to today, and only fetches summaries
for games not already in the data. A full bootstrap (~102 games as of late June)
takes ~60 seconds; a typical daily incremental run fetches 2–4 games in seconds.

Safety mechanisms:
- **Regression guard**: refuses to write CSVs with fewer games than the existing
  files (prevents accidental data loss).
- **Per-game isolation**: if one game's summary fails to parse, it's skipped and
  retried on the next run.
- **Atomic writes**: CSVs are written to a temp file then renamed, so a crash
  mid-write can't corrupt the data.
- **Rate limiting**: 0.5s delay between game fetches; one retry on 5xx errors.

### RESOLVED INCIDENT — sportsdataverse cache regression (2026-06-23)

**What happened:** The previous `fetch_data.py` pulled pre-built CSVs from the
sportsdataverse community cache (`wehoop-wnba-data` repo). On June 23, the
cache silently regressed — games disappeared during a cache rebuild, causing
incorrect standings on the live site (e.g., LV showed 11-4 instead of 12-4).
The regression was caught by comparing the live site against WNBA.com.

**Immediate fix:** Added a regression guard to prevent overwriting good data
with fewer games. Filed sportsdataverse/wehoop-wnba-data#9.

**Permanent fix:** Replaced the sportsdataverse dependency entirely with direct
ESPN API calls (this migration). The schedule fetch and Games tab already used
ESPN directly; this extends that pattern to all data. The sportsdataverse cache
is no longer used anywhere in the pipeline.

**Lesson:** Community-maintained data caches are convenient but unreliable for a
production site. Direct API access is more work upfront but eliminates a class
of silent failures.

### RESOLVED INCIDENT — wrong box-score line scores (2026-07-03)

**What happened:** Box-score quarter columns were derived from play-by-play by
differencing cumulative scores, reading each quarter's end as the score of the
highest-numbered play in the period. That play is often a non-scoring
end-of-period marker carrying a stale, lower score, which scrambled the
intermediate quarters while the diffs still telescoped to the correct final
total. A cross-check found **67 of 144 games (47%)** had ≥1 wrong quarter;
noticed when Dallas–Connecticut (7/2) showed a 3–4-point Q3.

**Why a sum check wouldn't catch it:** the quarter cells telescope, so any
scrambled split still sums to the right total — validation needs an independent
source, not an internal consistency check.

**Fix:** Switched to ESPN's **official** per-team line scores (in the same
summary payload) with a "correct-or-blank" policy — if official line scores are
missing or don't reconcile to the final, the quarter columns are omitted rather
than guessed. `validate_linescores.py` confirmed all 147 season games reconcile
(140/147 also matched an independent PBP running-max derivation; the 7 diffs
were the PBP method being wrong, not the official data).

**Lesson:** Don't reconstruct a value from a lower-level feed when the source
provides it directly. Re-run `validate_linescores.py` periodically (e.g., mid-
and late-season) as ESPN backfills/corrects data.

## Files in this repo
| File | Role |
|------|------|
| `fetch_data.py` | Fetches all data directly from ESPN's public API → **three CSVs** (`wnba_player_box_2026.csv`, `wnba_team_box_2026.csv`, `wnba_pbp_2026.csv`) **plus** today's schedule JSON and `wnba_linescores_2026.json` (official per-quarter line scores). Incremental: only fetches new games on each run. Box-score quarter columns come from the official line scores JSON — **not** derived from PBP (that reconstruction was wrong in ~47% of games; see note below). If a game has no official line scores, its quarter columns are left blank ("correct-or-blank"). |
| `validate_linescores.py` | One-off cross-check: for every completed game, compares ESPN's official line scores against an independent PBP running-max derivation and confirms each reconciles to the team-box final. Run manually in an env with live ESPN access (`python validate_linescores.py`); exits non-zero on any mismatch. |
| `build_stats_page.py` | Builds the self-contained HTML from the CSVs + schedule JSON + line scores JSON |
| `wrangler.toml` | Tells Cloudflare to serve `public/` as a static site |
| `.github/workflows/build.yml` | Daily cron: fetch → build → commit → deploy |
| `requirements.txt` | Python deps for the Action (`pandas`, `requests`) |
| `public/index.html` | The built site Cloudflare serves (regenerated daily) |
| `validate_stats.py` | Layer-2 external verification: diffs our leader boards against stats.wnba.com's API. Runs in CI before the Bluesky post and gates it; also runnable by hand. See "Layer-2 stats validation" below. |
| `validation_report.json` | Latest validator run (per-check PASS/FAIL/SKIPPED). Committed by each build; read by the cron Worker's health check. |
| `player_id_crosswalk.json` | Persisted ESPN `athlete_id` → WNBA `PLAYER_ID` matches, grown by each validator run so later joins are exact even if a name spelling drifts. |
| `cron-worker/` | Cloudflare Worker: triggers daily builds + health check. **Tracked in git as of 2026-07** (reversing the 2026-07-12 stance — it now gates data-quality alerting, so losing its history would hurt). Being tracked does NOT put it in the Actions deploy path: deploys remain manual (`cd cron-worker && npx wrangler deploy`). Secrets (`GH_TOKEN`, `CRON_KEY`) live only in `wrangler secret put`; `.wrangler/` and `.dev.vars` are gitignored. |
| `analytics-worker/` | Cloudflare Worker: receives usage beacons → Workers Analytics Engine. Deployed separately, **intentionally untracked**; see "Usage analytics worker" below. |

## Health check + email alerts (added 2026-06-11)

The cron Worker (`cron-worker/`) now has a second job: at **11:45 UTC** daily it
verifies the morning build end-to-end and emails horowitz.jason@gmail.com
**only on failure**. Silence = all good. This replaces the temporary Claude
scheduled check.

What it verifies, in order:
1. A "Daily WNBA stats build" run exists today and succeeded (GitHub API; if
   still in progress it waits 3 min and re-checks once).
2. If the run committed today's update, the **live site** at
   wnba.statsataglance.com actually shows data through yesterday — read from the
   `<meta name="data-through">` tag that `build_stats_page.py` now embeds
   (falls back to the visible "Stats as of …" text). Retries once after 2 min
   to allow for Cloudflare's redeploy lag.
3. If the run committed nothing, it checks ESPN's public scoreboard for
   yesterday: no games → legitimate off-day, silence; games played → alert
   (the fetch silently returned nothing).
4. **Layer-2 validation drift (added 2026-07-27):** reads the
   `validation_report.json` that today's build committed (raw.githubusercontent
   with a cache-buster). Any category FAIL, or a validator crash, is folded
   into the same alert email. `SKIPPED (source not caught up)` and a stale or
   missing report are notes only — they never trigger an email on their own,
   preserving the silence-means-fine contract.

### The 2026-08-05 outage: a ~4-hour failure of `site.api.espn.com`

**What happened.** `site.api.espn.com` — the host used since the 2026-06-23
ESPN migration — returned an Akamai `Access Denied` 403 to every request from
roughly **11:17 to ~14:45 UTC**, then **recovered on its own** (verified 200s
at 15:02 UTC on every path that had failed). Measured inside the window:

| Test | Result |
|---|---|
| `site.api` + browser User-Agent | 403 |
| `site.api` + script User-Agent, or none | 403 |
| `site.api` from home broadband (not the runner) | 403 |
| `site.api` from a third, unrelated network | 403 |
| `site.api` **NBA** path, not WNBA | 403 |
| **`site.web.api` + any/no User-Agent** | **200** |

The only variable that changed the outcome was the **hostname**. So it was not
rate limiting, not keyed on User-Agent, and not an IP-range block on the
Actions runner. **The mitigation is a one-line host swap** to
`site.web.api.espn.com`, which serves the identical path scheme and response
shape and stayed up throughout.

**What it actually was is unknown** from outside ESPN. A rolled-out-then-
rolled-back Akamai bot rule fits as well as an infrastructure fault — and if
that rule keyed on **TLS fingerprint** rather than headers, no User-Agent change
would ever have defeated it. What is ruled out is UA-based and IP-based
targeting of *us*.

> **Correction.** This section originally said the host had been *retired*.
> That was wrong — it recovered the same day. A total, sustained failure is
> evidence of a host-wide problem, but says nothing about permanence, and
> permanence was assumed rather than tested. Both hosts work today.

**Why we stay on `site.web.api` anyway:** one host stayed up and the other
didn't. But the durable mitigation is `ESPN_ORIGIN` — *either* host can fail,
and switching is now an env var rather than a deploy.

**Debugging lesson for next time:** when ESPN 403s, don't start with header
tricks. Curl the same path on a *different ESPN host*, and a *different league*
on the same host. Two commands separate "they're blocking us" from "this host
is having a bad day." Then **recheck the original host later the same day**
before writing a workaround down as permanent.

### ESPN proxy fallback (prototype, 2026-08-05 — NOT needed, NOT deployed)

`espn-proxy/` is a narrow, key-authenticated Cloudflare Worker that passes
through to ESPN's WNBA API. It was drafted for the IP-range-block theory that
the table above disproved, so it solves a problem this project does not have.

It's kept only for the case where a future replacement host is genuinely geo-
or IP-fenced, which is the one situation where Cloudflare's egress helps. Try
a plain host swap first. `fetch_data.py` sends `ESPN_PROXY_KEY` as
`X-Proxy-Key` when set, so switching over is two repo secrets and three lines
of workflow YAML — and deleting the secrets is the rollback. Setup and limits
in `espn-proxy/README.md`.

### Fail-loud on an incomplete fetch (added 2026-08-05)

`fetch_data.py` **exits non-zero** when it couldn't read everything it needed:
an unreadable scoreboard date, a discovered game that wouldn't download, or a
missing schedule. That deliberately fails the job before the build step, so
nothing is rebuilt, committed, or deployed. Whatever it *did* fetch is written
first and lands in the Actions cache, so the next run resumes from there.

The rule this encodes: **a silently-wrong green build costs more than a loudly
red one.** A blocked deploy leaves yesterday's correct page up, turns the run
red, notifies you, and makes the cron-worker health check auto-rebuild.

Escape hatch: tick **allow_partial** in the Run workflow UI (or set
`ALLOW_PARTIAL=1`) to publish a partial update anyway.

Related: `wnba_schedule_today.json` now carries a `status` field. `"ok"` with an
empty `games` list means ESPN confirmed there are no games today; `"unavailable"`
means the fetch failed. The Games tab renders "No games today." only for the
first, and "Today's schedule is unavailable." otherwise — on 2026-08-05 a failed
fetch wrote an empty list that the page published as a confident, false "No
games today" while four were scheduled. A missing or unreadable file is treated
as unavailable; files predating the field are treated as `"ok"`.

### Self-healing retries (added 2026-08-05)

The health check now **repairs as well as reports**. It runs three passes —
**11:45**, **13:15**, and **14:45 UTC** — and every problem it finds is tagged:

| Tagged | Meaning | Behaviour |
|---|---|---|
| **retryable** | another build would plausibly fix it — failed/absent run, live site stale despite games played, build committed nothing, `data_completeness` FAIL (our data behind stats.wnba.com), validator crash | re-dispatch `build.yml`, stay silent; the next pass re-checks |
| not retryable | needs a human — GitHub API unreachable, site unreachable on an off-day, a *value* mismatch on a stat we do have (a logic bug: rebuilding reproduces it), run still in progress (dispatching would just queue a duplicate) | email immediately |

The **final** pass (14:45) stops retrying and emails whatever is still broken,
noting that two auto-rebuilds already failed to fix it. So the silence-means-
fine contract now reads: *silence means fine, or fixed itself.*

**The Bluesky post is suppressed on auto-retries** unless the Worker can prove
the earlier run didn't post — i.e. no successful run at all, or
`leaders_ok === false` (the post step was gated off). Unknown counts as
"probably posted". `post_to_bluesky.py` has no dedupe guard, and a duplicate
post can't be un-posted while a missed post is merely missed.

`gamesPlayedOn()` also retries ESPN (4 attempts over ~17s). An "unknown" there
silently disables the entire freshness assertion, so one bad second of ESPN
uptime must not be allowed to blind the check.

Manual runs **report, they don't repair**: `?action=check` behaves as a final
pass so you see every problem. Add `&repair=1` to let it dispatch a rebuild.

Origin: on **2026-08-05** every ESPN call 403'd for ~4 hours (see above),
`discover_games()` swallowed the failures per-date, and three builds in a row
went **green** while republishing day-old data and telling visitors "No games
today" with four scheduled. The only signal was a Layer-2 `data_completeness`
FAIL, which read like a stats bug rather than an outage, and it took a
hand-dispatched build to fix.

These passes are **well matched to that failure**: they span 11:45 → 14:45 UTC,
and the outage cleared between 14:44 and 15:02, so the final auto-rebuild lands
at the recovery boundary and would plausibly have repaired the day unattended.

> **Retraction.** This section previously claimed retries "would not have fixed
> 2026-08-05, because no number of rebuilds reaches a dead host." The host was
> not dead — that claim rested on the retirement error corrected above.

Fail-loud and retries remain complements, and fail-loud is still the change that
makes the failure *visible* on the first build rather than the third. But an
outage lasting hours is exactly what multi-hour retry passes exist for.

`gamesPlayedOn()` in the cron Worker was hit by the same outage and had to be
pointed at the new host too — it had been returning `null`, which silently
disables the freshness assertion. A health check that reads its own data source
through the broken path cannot see the breakage.

## Layer-2 stats validation (added 2026-07-27)

`validate_stats.py` diffs our computed leader boards against stats.wnba.com's
own `leagueleaders` API (never a scraped page — WNBA.com's rendered page has
been observed days stale while the API was current). Design, endpoints,
qualification-rule sources, and check thresholds are documented in the script's
docstring; the planning record is `LAYER2-VALIDATION-HANDOFF.md` and
`../WNBA-leader-qualification-rules.md` at the projects root.

The contract:

- **In CI** (`build.yml`, step "Validate against stats.wnba.com", after the
  page build): runs `validate_stats.py --gate` — checks today's broadcast
  category only, writes `validation_report.json` + `player_id_crosswalk.json`
  (committed with the site), and emits `leaders_ok=true|false` to
  `$GITHUB_OUTPUT`. The Bluesky post step only runs when `leaders_ok == 'true'`.
  The step itself is `continue-on-error` — a validator failure can never block
  the site deploy, only the broadcast.
- **Fail-closed for the post, fail-open for lag:** a check FAIL or a validator
  crash blocks the post; the source being unreachable or behind us
  (`SKIPPED — source not caught up`) does NOT, since our own data already
  passed the Layer-1 guards.
- **By hand:** `python validate_stats.py` checks all 11 boards and prints a
  readable report; `--date YYYY-MM-DD` cuts our side off at a date (only
  meaningful while the source is frozen there too, e.g. over a break).
- The stats API needs browser-ish headers **plus gzip Accept-Encoding**
  (it hangs, rather than erroring, without them) — both baked into the script.
  `leaguedashteamstats` (team ratings, the future P2 check) hung from every
  network we tried on 2026-07-27; revisit from the Actions runner before
  building P2 on it.

### One-time setup
1. **Email Routing** (free): Cloudflare dashboard → statsataglance.com zone →
   Email → Email Routing → enable. Add horowitz.jason@gmail.com as a
   destination address and click the verification email it sends. (You don't
   need any routing rules — the Worker only *sends*.)
2. **Push the build-script change** so the next gameday build embeds the
   `data-through` meta tag:
   ```bash
   git add build_stats_page.py && git commit -m "Embed data-through meta for health check" && git push
   ```
3. **Redeploy the Worker** (picks up the new code, second cron, and email
   binding):
   ```bash
   cd cron-worker && npx wrangler deploy
   ```
   `GH_TOKEN` and `CRON_KEY` secrets carry over; no changes needed.
4. **Test it**:
   - `https://<worker-url>/?key=CRON_KEY&action=testemail` — confirms an alert
     can reach your inbox.
   - `https://<worker-url>/?key=CRON_KEY&action=check` — runs the health check
     now and returns its findings as JSON.

### Notes
- The alert sender is `alerts@statsataglance.com`; Gmail may put the first one
  in spam — mark it "not spam" once.
- Worker emails can only go to addresses verified in Email Routing. To change
  the recipient, update both `wrangler.toml` (`destination_address`) and
  `ALERT_TO` in `worker.js`, verify the new address, and redeploy.
- All dates are UTC, matching the build's "Daily stats update: YYYY-MM-DD"
  commit messages.

## Usage analytics worker (added 2026-07-11)

The single-file site's tabs and box scores are client-side JS, so Cloudflare
Web Analytics only ever registers the initial `/` page load. A small,
cookie-free beacon fills the gap: `build_stats_page.py`'s `PAGE_JS` pings a
Worker on page load, on every tab switch, and on every box-score open — so we
can see which tabs get used and whether box scores get opened, not just that
someone showed up.

**The contract** (client in `build_stats_page.py` ↔ worker in `analytics-worker/`):
- Client → `GET`/`POST https://usage.statsataglance.com/t?e=<event>&t=<tab>&s=<utm_source>&r=<0|1>`
  via `navigator.sendBeacon` (fetch fallback). Wrapped in try/catch — fails
  silently, never blocks the page.
- Events: `pageview` (one per load), `tab` (`t` = tab id, e.g. `leaders`),
  `box` (`t` = `game:<id>`). `s` = utm_source captured on the session's first
  load (cached in `sessionStorage`); `r` = `1` for a returning visitor
  (`localStorage` flag), else `0`.
- Worker writes one aggregate, PII-free data point per event to Workers
  Analytics Engine (dataset `wnba_usage`), CORS-restricted to
  `https://wnba.statsataglance.com`.

**Not tracked in git.** The worker source lives in `analytics-worker/` locally
and in Cloudflare; it is deployed separately and is not part of the daily HTML
build. (`cron-worker/` used to share this stance but has been tracked since
2026-07 — it gates data-quality alerting now. This worker stays untracked until
it crosses a similar line.) Trade-off: no git history/backup for the worker
itself — **this section is the durable record of how it's wired.**

### One-time setup
1. **Enable Analytics Engine on the account (one-time).** Until this is done,
   the first deploy fails with `[code: 10089] You need to enable Analytics
   Engine`. Dashboard → Workers & Pages → **Analytics Engine** → enable (free;
   no plan change). You do **not** need to manually create the `wnba_usage`
   dataset — the worker creates it on first write; the binding in
   `wrangler.toml` is what matters.
2. **Deploy the worker:**
   ```bash
   cd analytics-worker && npx wrangler deploy
   ```
3. **Route the hostname to it.** The client posts to `usage.statsataglance.com`,
   so map that subdomain to the worker: Workers & Pages → `wnba-usage-tracker` →
   Domains → Custom Domains and Routes → Add Domain → `usage.statsataglance.com`.
   Cloudflare provisions the DNS record + SSL automatically. **This step is
   required** — without it the beacons resolve to nothing and silently collect
   no data (the page itself still works fine). No `wrangler.toml` change needed;
   the domain is wired in the dashboard, same as the main site.
4. **Verify.** Hitting `https://usage.statsataglance.com/t` in a browser should
   return a 204; the root `/` returns the "wnba-usage-tracker is alive" text.
   Then load the live site and confirm points land (query below).

### Querying the data
**Use `usage_report.py`** — `python usage_report.py [--days N | --since DATE]
[--json]`. It prints daily pageviews, source breakdown, tab engagement, box
opens, depth-by-source, and new-vs-returning. Credentials come from
`CF_ACCOUNT_ID` / `CF_ANALYTICS_TOKEN` in the environment or a **gitignored
`.env`**; the token must be scoped to **Account Analytics: Read** only — never
reuse `CLOUDFLARE_API_TOKEN`, which can deploy.

Raw SQL API, if you need something the script doesn't cover:
```bash
curl -s "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/analytics_engine/sql" \
  -H "Authorization: Bearer <API_TOKEN>" \
  -d "SELECT blob1 AS event, blob2 AS tab, blob3 AS source, blob4 AS returning,
             SUM(_sample_interval) AS n
      FROM wnba_usage WHERE timestamp > NOW() - INTERVAL '1' DAY
      GROUP BY event, tab, source, returning ORDER BY n DESC"
```

> ⚠️ **Always `SUM(_sample_interval)`, never `count()`.** Analytics Engine
> samples at volume and stores the inverse sample rate per row, so `count()`
> returns the number of *stored* rows, not events. This example said `count()`
> until 2026-08-04, and by then it was already wrong: all-time totals read 841
> with `SUM(_sample_interval)` vs 816 with `count()` — a 3% undercount, growing
> with traffic. Sampling is not a future problem; it is already on.

### utm_source taxonomy
Three stable values — they are the historical series, so don't rename them:

| Value | Surface |
|---|---|
| `bluesky-post` | the daily automated leaders post's OG card (`post_to_bluesky.py`'s `SITE_URL`) |
| `bluesky-bio` | the profile bio link — `wnba.statsataglance.com/bsky`, a **Cloudflare Redirect Rule** (dashboard → Rules → Redirect Rules) that 301s to `/?utm_source=bluesky-bio`. Configured only in the dashboard; this line is its only record. |
| `none` | direct, bookmark, organic, or any untagged referrer |

The post card was untagged until 2026-08-04, so every click on it before that
date is indistinguishable from direct traffic. Not recoverable retroactively.
