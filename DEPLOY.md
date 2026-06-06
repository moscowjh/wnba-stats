# Deploying the WNBA stats page (Cloudflare Pages + GitHub Actions)

This turns the manual pipeline into: **every morning, fetch fresh box scores →
rebuild the static page → publish to a public URL.** No server, no R, no cost.

## How the pieces fit

```
GitHub Actions (cron, 7am ET)
    └─ python fetch_data.py        # pulls box scores (no R)
    └─ python build_stats_page.py  # bakes data into one static HTML
    └─ copies it to public/index.html and commits
            │
            ▼  (push triggers a deploy)
Cloudflare Pages  →  https://your-site.pages.dev
```

GitHub does the work; Cloudflare just serves the result. Because Cloudflare
watches the repo, the Actions workflow needs **no Cloudflare credentials**.

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
        .github/workflows/daily.yml DEPLOY.md
git commit -m "WNBA stats site: automated daily build"
# create an empty public repo on github.com first, then:
git remote add origin https://github.com/moscowjh/wnba-stats.git
git branch -M main
git push -u origin main
```

### 2. Connect the repo to Cloudflare Pages
1. Sign up / log in at <https://dash.cloudflare.com> (free).
2. **Workers & Pages → Create → Pages → Connect to Git.**
3. Authorize GitHub and pick your `wnba-stats` repo.
4. Build settings:
   - **Framework preset:** None
   - **Build command:** *(leave empty)*
   - **Build output directory:** `public`
5. **Save and Deploy.** You'll get a `https://<name>.pages.dev` URL in ~1 minute.

That's it. From now on, every commit the daily Action makes redeploys the site
automatically.

### 3. Test the automation
- In GitHub, open the **Actions** tab → **Daily WNBA stats build** → **Run
  workflow** (the `workflow_dispatch` button). Watch it fetch, build, and commit.
- Within a minute, Cloudflare shows a new deployment and your URL updates.

## Cost
Cloudflare Pages free tier: unlimited bandwidth and requests, 500 builds/month.
A once-daily commit is ~30 builds/month — comfortably free, and it stays free at
fan-sharing scale. A custom domain (e.g. `wnbastats.com`) is optional and runs
~$10/yr through any registrar; you'd add it under the Pages project's **Custom
domains** tab.

## Adjusting the schedule
Edit the `cron` line in `.github/workflows/daily.yml`. It's in **UTC**.
`0 11 * * *` = 11:00 UTC = 7am ET in summer (EDT). Later in the year, when ET
shifts to EST (UTC-5), 11:00 UTC becomes 6am ET — nudge to `0 12 * * *` if you
want to hold 7am.

## If the data ever lags
`fetch_data.py` prints how old the newest game is and warns past 2 days. If you
see lag in practice, the upgrade path is to swap the cached loaders for the live
ESPN endpoints (`espn_wnba_schedule()` + `espn_wnba_summary()`), which are
always current — a good task to hand to Claude Code with a sample game to test
against.
