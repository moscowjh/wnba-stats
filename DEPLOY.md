# Deploying the WNBA stats page (Cloudflare + GitHub Actions)

This turns the manual pipeline into: **every morning, fetch fresh box scores →
rebuild the static page → publish to a public URL.** No server, no R, no cost.

## How the pieces fit

```
GitHub Actions (cron, 7am ET)
    └─ python fetch_data.py        # pulls box scores (no R)
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

## If the data ever lags
`fetch_data.py` prints how old the newest game is and warns past 2 days. If you
see lag in practice, the upgrade path is to swap the cached loaders for the live
ESPN endpoints (`espn_wnba_schedule()` + `espn_wnba_summary()`), which are
always current — a good task to hand to Claude Code with a sample game to test
against.

## Files in this repo
| File | Role |
|------|------|
| `fetch_data.py` | Pulls 2026 box scores → two CSVs (replaces the R step) |
| `build_stats_page.py` | Builds the self-contained HTML from the CSVs |
| `wrangler.toml` | Tells Cloudflare to serve `public/` as a static site |
| `.github/workflows/build.yml` | Daily cron: fetch → build → commit → deploy |
| `requirements.txt` | Python deps for the Action (`pandas`, `sportsdataverse`) |
| `public/index.html` | The built site Cloudflare serves (regenerated daily) |
| `cron-worker/` | Cloudflare Worker: triggers daily builds + health check |

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
