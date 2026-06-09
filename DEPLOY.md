# Deploying the WNBA stats page (Cloudflare + GitHub Actions)

This turns the manual pipeline into: **every morning, fetch fresh box scores →
rebuild the static page → publish to a public URL.** No server, no R, no cost.

## How the pieces fit

```
GitHub Actions (cron, 7am ET)
    └─ python fetch_data.py        # pulls box scores (no R)
    └─ python build_stats_page.py  # bakes data into one static HTML
    └─ copies it to public/index.html and commits + pushes
            │
            ▼  (push triggers a deploy)
Cloudflare  →  runs `npx wrangler deploy` → https://wnba-stats.<you>.workers.dev
```

GitHub does the work; Cloudflare just serves the result. Because Cloudflare
watches the repo, the Actions workflow needs **no Cloudflare credentials**.

## A note on Cloudflare's two flows

Cloudflare has merged "Pages" into "Workers," and the Connect-to-Git wizard now
puts you in the **Workers** flow, which deploys with a `wrangler deploy` command
instead of a "build output directory" setting. This repo includes a small
`wrangler.toml` that tells Wrangler to serve the `public/` folder as a static
site, so that command just works. You do **not** need to write any Worker code —
it's still a plain static site under the hood.

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

### 3. Connect the repo to Cloudflare
1. Sign up / log in at <https://dash.cloudflare.com> (free).
2. **Workers & Pages → Create → Connect to Git** (authorize GitHub, grant access
   to only the `wnba-stats` repo).
3. Pick `moscowjh/wnba-stats`, click **Next**.
4. On "Set up your application":
   - **Project name:** `wnba-stats`
   - **Build command:** *(leave empty)*
   - **Deploy command:** `npx wrangler deploy` (the default — leave it)
   - Leave "Builds for non-production branches" as is.
5. Click **Deploy.** In ~1 minute you'll get a
   `https://wnba-stats.<subdomain>.workers.dev` URL.

From now on, every push — including the daily Action's commit — redeploys
automatically.

### 4. Test the automation
- In GitHub, open the **Actions** tab → **Daily WNBA stats build** → **Run
  workflow** (the `workflow_dispatch` button). Watch it fetch, build, and commit.
- Within a minute, Cloudflare shows a new deployment and your URL updates.

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
the dashboard, and the Git-connected deploys keep working unchanged.

## Adjusting the schedule
Edit the `cron` line in `.github/workflows/daily.yml`. It's in **UTC**.
`17 11 * * *` = 11:17 UTC = 7:17am ET in summer (EDT). Later in the year, when ET
shifts to EST (UTC-5), 11:17 UTC becomes 6:17am ET — nudge to `17 12 * * *` if you
want to hold ~7am. (The `:17` minute is deliberate — see the known issue below.)

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
| `.github/workflows/daily.yml` | Daily cron: fetch → build → commit |
| `requirements.txt` | Python deps for the Action (`pandas`, `sportsdataverse`) |
| `public/index.html` | The built site Cloudflare serves (regenerated daily) |
