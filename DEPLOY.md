# Deploying the WNBA stats page (Cloudflare + GitHub Actions)

This turns the manual pipeline into: **every morning, fetch fresh box scores →
rebuild the static page → publish to a public URL.** No server, no R, no cost.

## How the pieces fit

```
GitHub Actions (cron, 7am ET)
    └─ python sites/wnba/fetch_data.py        # pulls box scores + pbp + line scores + today's schedule (no R)
    └─ python sites/wnba/build_stats_page.py  # bakes data into one static HTML
    └─ copies it to sites/wnba/public/index.html and commits + pushes
    └─ npx wrangler deploy -c sites/wnba/wrangler.toml   # deploys to Cloudflare
            │
            ▼
Cloudflare Workers  →  serves sites/wnba/public/index.html at wnba.statsataglance.com
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
that just rebuilt `sites/wnba/public/index.html`, so the correct version is always active.

**Manual deploys:** push your code changes to `main`, then trigger the Action
(GitHub Actions tab → "Run workflow", or `gh workflow run build.yml` from CLI).
The Action checks out the latest code, fetches data, rebuilds, commits, and
deploys — all in one run.

## Static site on Cloudflare Workers

Cloudflare has merged "Pages" into "Workers." `sites/wnba/wrangler.toml` tells
Wrangler to serve that site's `public/` folder as a static site. The `[assets]
directory` is resolved **relative to the config file**, not the working
directory, which is why the Action passes `-c sites/wnba/wrangler.toml` and
still uploads `sites/wnba/public/`. You do **not** need to write any
Worker code — it's still a plain static site under the hood.

## One-time setup

### 1. Put this folder in its own GitHub repo
Keep it separate from your big multi-sport workspace — you do **not** want the
1.75 GB DuckDB or large CSVs in a public repo. This folder already has a
`.gitignore` that excludes the CSVs (they're refetched in CI) and other scratch
files.

```bash
cd ~/projects/statsataglance
git init
git add core/ sites/wnba/ .gitignore .github/workflows/build.yml DEPLOY.md
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
pip install -e core/                   # once — puts `sag` on the path
python sites/wnba/build_stats_page.py  # regenerates sites/wnba/wnba-2026-stats-explorer.html
cp sites/wnba/wnba-2026-stats-explorer.html sites/wnba/public/index.html
git add sites/wnba/public/index.html
git commit -m "Add static site content"
git push
```

`git status` should show `sites/wnba/public/index.html` as tracked — `.gitignore`
blocks `sites/*/data/` and `*.csv` but not `public/`.

> Alternative: instead of building by hand, trigger the GitHub Action once
> (step 4) and let it create and commit `sites/wnba/public/index.html` for you. Either way,
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
3. **Point the subdomain at the Worker:** Workers & Pages (**Compute** in newer
   dashboards) → `wnba-stats` → the **Domains** tab → **+ Add Domain** → enter
   the subdomain **`wnba`** only; the dialog appends `.statsataglance.com`.
   Cloudflare provisions the DNS record + SSL automatically.
   *(Path corrected 2026-08-30 — this used to read "Settings → Domains & Routes",
   which no longer exists. See the WWC section below for the full walk-through
   and the two traps in that dialog.)*
4. **Apex redirect:** Rules → Redirect Rules → `statsataglance.com` →
   `wnba.statsataglance.com`.
5. **(Optional) retire the workers.dev URL:** set `workers_dev = false` in
   `wrangler.toml` and push, so the only public URL is the custom domain.

No `wrangler.toml` change is needed for steps 1–4 — the custom domain is wired in
the dashboard.

## The WWC site — second hostname, second workflow (added 2026-08-25)

`wwc.statsataglance.com` serves `sites/wwc/public/`, built by
`.github/workflows/wwc.yml`. Engineering detail is in
`docs/wwc-site-internals.md`; this section is the infrastructure only.

**It is a separate workflow on purpose.** `build.yml` is the WNBA daily
pipeline — it fetches box scores, gates on a validator, posts to Bluesky, and
its filename is hardcoded in `workers/cron/worker.js`. The WWC site rebuilds
when its hand-maintained reference data changes, has no data fetch at all
until 4 September, and must not be able to block (or be blocked by) a WNBA
fetch failure.

Triggers: `workflow_dispatch`, plus `push` to `main` filtered to
`sites/wwc/**`, `core/**` and the workflow file. The filter is what keeps it
off the WNBA bot's daily commits, which touch `sites/wnba/public`,
`validation_report.json`, `player_id_crosswalk.json` and
`usage_history.jsonl` — none of which match. A `core/` change re-renders both
sites, which is correct.

### One-time setup — and the ORDER is the point

Walked end to end on 2026-08-29 when `wwc.statsataglance.com` went live. The
dashboard wording below is what was actually on screen that day; Cloudflare has
renamed these controls at least once, so trust the shape of the flow over the
exact labels.

**Deploy first, attach the hostname second — they are separate days' work if
you want them to be.** `npx wrangler deploy` creates the Worker; attaching the
hostname is what makes it public. Keeping them apart is deliberate and it
earned its keep the first time it was used: with `workers_dev = false` a
deployed Worker has **no address at all**, so the WWC site sat built and
unreachable for a full day of editing, and a `/games/` canonical bug that
would have told Google to de-index the page was found and fixed inside that
window. Attached at deploy time it would have been a redirect cleanup on a
live site. **Use this order for `ncaaw.` and anything after it.**

1. **Create the Worker.** Push anything matching the site's path filter; the
   Action's `wrangler deploy` step creates it. Confirm in the deploy log:

   ```
   Uploaded wwc-stats (4.75 sec)
   No targets deployed for wwc-stats     ← no hostname yet, as intended
   Current Version ID: e6ed91f1-…
   ```

2. **Attach the hostname, in the dashboard.** Workers & Pages (newer
   dashboards call this **Compute**) → `wwc-stats` → the **Domains** tab.

   > ⚠️ **The tab is `Domains`, not "Domains & Routes"** — that older wording
   > sent Jason hunting for a control that no longer exists (2026-08-29). It
   > sits in the Worker's own tab row, between Observability and Access. Make
   > sure you are on the **Worker's** Domains tab and not the account- or
   > zone-level Domains page, which cannot bind a Worker at all.

   Under **Custom Domains and Routes**, click **+ Add Domain** (*not* **Add
   Route** — a Route matches URL patterns on a zone and is not what this
   needs). Pick the zone, then:

   > ⚠️ **The dialog asks for the SUBDOMAIN ONLY.** The field appends
   > `.statsataglance.com` on the right, so type `wwc` — not the full
   > hostname. Its hint reads *"Leave empty for root domain"*, and leaving it
   > empty binds the Worker to the bare apex `statsataglance.com`, which
   > currently redirects to `wnba.` — i.e. it points the apex at the wrong
   > site and breaks the WNBA entry path. Reversible, but it is the one
   > mistake this dialog makes easy.

   Cloudflare provisions the DNS record and the SSL certificate itself — do
   **not** add a CNAME by hand. On 2026-08-29 the cert was live immediately;
   budget a few minutes and expect 5xx or an SSL warning until it issues.

   > **Why this is a hand step and not config.** Wrangler *can* declare it
   > (`routes = [{ pattern = "…", custom_domain = true }]`), and
   > `sites/wwc/wrangler.toml` records why it doesn't: that needs a token with
   > Zone/DNS + Workers Routes edit, while the shared `CLOUDFLARE_API_TOKEN` is
   > scoped to **Account / Workers Scripts / Edit** only — see "Create an API
   > token" in the one-time setup at the top of this file. Declaring the route
   > with the current token fails the **whole deploy**, not just the domain
   > step, so the site would stop publishing rather than merely lack a name.

3. **Verify from outside**, not from the dashboard:

   ```bash
   for p in / /games/ /teams/ /groups/ /sitemap.xml /robots.txt; do
     printf "%-14s %s\n" "$p" \
       "$(curl -s -o /dev/null -w '%{http_code}' https://wwc.statsataglance.com$p)"
   done
   ```

   Then check every canonical is self-referential. A page claiming a canonical
   it should not is the failure this step exists to catch, and it is invisible
   from the dashboard.

4. **No new secrets.** It reuses `CLOUDFLARE_API_TOKEN` and
   `CLOUDFLARE_ACCOUNT_ID`.

5. **No Web Analytics token, deliberately.** `WWC.cf_analytics_token` is
   `None` — a league is allowed to launch unmeasured. The cookie-free usage
   beacon still reports with `site=wwc` in `blob10`, so cross-site questions
   ("does the Cup site hand readers back to WNBA?") are answerable from day
   one without a second Web Analytics property. **The analytics Worker needs
   no change** — it slices `e`/`t` without a whitelist, so the new page keys
   (`games`, `teams`, `team:<slug>`, `groups`, `key`, `game:<id>`) land
   without a manual redeploy. All of them fit the 32-char `blob2` slice and
   the emitter asserts it.

### ⚠️ robots.txt on a second hostname — MEASURE IT, DO NOT ASSUME

The 2026-08-17 player-pages cutover found that on this zone an origin
`robots.txt` **replaces** Cloudflare's managed block rather than merging with
it — the opposite of Cloudflare's documented behaviour, and the opposite of
what planning had assumed. That finding was measured on
`wnba.statsataglance.com`. **It has not been measured on a second hostname in
the same zone, and it does not carry by assumption.**

`sites/wwc/public/robots.txt` is emitted by `sag.seo.write_robots` (allow-all
plus the absolute `Sitemap:` line). After the first deploy, capture what is
actually served:

```bash
curl -s https://wwc.statsataglance.com/robots.txt \
  > sites/wnba/reference/robots-txt-observed/wwc-$(date -u +%F).txt
```

Keep the capture beside the WNBA ones in
`sites/wnba/reference/robots-txt-observed/` — they are the same investigation
and splitting them across directories would hide the comparison. Then compare
against `2026-08-17.txt` (77 B, ours alone) and `2026-08-16.txt` (1248 B,
Cloudflare's block):

| Served | Means |
|---|---|
| ~77 B, our three lines only | same behaviour as the WNBA host — replace, not merge |
| ~1248 B + our lines | it really does merge here, and the WNBA finding is host-specific |
| ~1248 B alone | the origin file is not being served at all — investigate before submitting the sitemap |

**Do not submit `sitemap.xml` to Search Console until this is captured**, and
record the result in the backlog either way. The whole point of standing the
site up empty was to convert this unknown into a known one while there was
still slack.

## Adjusting the schedule
The schedule is controlled by the Cloudflare cron Worker (`workers/cron/`), which
calls GitHub's `workflow_dispatch` API at 11:17 UTC (7:17am ET in summer). Later
in the year, when ET shifts to EST (UTC-5), 11:17 UTC becomes 6:17am ET — adjust
the cron trigger in `workers/cron/wrangler.toml` if you want to hold ~7am.

### ⚠️ The trigger list is FULL — 5 of 5 (added 2026-08-30)

**Cloudflare's Free plan allows 5 Cron Triggers per ACCOUNT, not per Worker**
(Paid allows 250). `wnba-stats-cron` is the only Worker on this account with
any, and it now owns all five. **A sixth trigger anywhere on the account will
fail to deploy** — including on a different Worker.

| UTC | Workflow | Job |
|---|---|---|
| 11:17 | `build.yml` **+ `wwc.yml`** | WNBA daily dispatch; also fires the WWC morning catch-up |
| 11:45 | — | WNBA health check, pass 1 (auto-repairs) |
| 13:15 | — | WNBA health check, pass 2 |
| 14:45 | — | WNBA health check, FINAL (emails) **+ WWC report-only check** |
| 22:00 | `wwc.yml` | WWC evening dispatch, after the last Berlin game |

This ceiling is why the WWC catch-up **rides on the 11:17 trigger** instead of
owning a ~06:30 one. If the account ever moves to Workers Paid, splitting it
back out is strictly better — it halves the worst-case lag on a box score FIBA
publishes late — but it is not worth a plan change on its own.

### Why the WWC dispatch is at 22:00 and not 18:45

The figure carried in the backlog was **18:45 UTC, "after the last Berlin
game."** That is a **tip** time, not an end time, and building on it would have
rebuilt the site mid-game and left it frozen overnight showing the day's last
match in progress.

```
latest tip of the tournament   19:00 GMT   (Sep 4)
four other days tip at         18:45 GMT
a game runs                    ~1h45-2h wall clock, plus OT
=> latest plausible final      ~21:15 GMT
```

22:00 UTC gives ~45 minutes past the worst case. Times come from
`sites/wwc/reference/wwc_schedule_2026.csv` (`tip_gmt`).

For reference during the tournament: **22:00 UTC is 6:00 PM EDT**, and
midnight in Berlin (the calendar date there has already rolled over when the
build fires — harmless, because WWC lifecycle state is derived from
`results.json` and never from the clock, but worth knowing before writing
anything that *does* consult a date).

**Same DST caveat as the 11:17 dispatch above.** Cron triggers are UTC and do
not follow US clock changes, so when ET shifts to EST on 2026-11-01, 22:00 UTC
becomes **5:00 PM EST**. Irrelevant to a tournament that ends 13 September, and
noted only because this Worker may outlive it — if these triggers are ever
reused for a winter event, re-derive the time rather than assuming the slot
still means what it meant in September.

**Six games have no announced time yet** — Sep 8, 9 and 12 are all
`time_tba=TRUE`. Nothing ingests times from FIBA (`fetch_data.py` writes
results and box scores only; times come from that hand-maintained CSV), so
those need a human edit when FIBA announces them.

### Rebuilding the WNBA site on demand

Rarely needed — the 11:17 dispatch plus three self-healing health checks cover
the normal failure modes. When you do need it, three ways:

| How | Use it when |
|---|---|
| `https://<worker-url>/?key=CRON_KEY&action=build&post=false` | **A rebuild later the same day.** `post=false` is the important half — see below. |
| `gh workflow run build.yml -f post=false` | You are already in a terminal. |
| Actions UI → `Daily WNBA stats build` → **Run workflow** | You want the `allow_partial` checkbox — the ESPN fetch came back short and you have decided to publish anyway. |

**Always pass `post=false` on a same-day rebuild.** `build.yml` posts the daily
leaders to Bluesky, and `post_to_bluesky.py` has NO dedupe guard: a second run
on a day that already posted publishes a second post, and a post cannot be
unpublished. A missed post is merely missed. The morning's scheduled run has
already posted by the time you are reading this, so on any manual rebuild after
~11:20 UTC, `post=false` is the default you want.

**`&action=build` is REQUIRED, as of 2026-08-31.** The route used to treat "key
with no action" as *dispatch a build and post* — so the single easiest URL to
arrive at by accident, or by fumbling an `action` parameter, was the one
irreversible thing this Worker can do. It now returns HTTP 400 and takes no
action; the build has to name itself. Same inversion applied to `scheduled()`
when the second site landed, for the same reason: a missed build is
recoverable, a double post is not.

Verified before deploying (`wrangler dev`, `CRON_KEY` set in `.dev.vars` and
`GH_TOKEN` deliberately NOT set, so a dispatch 401s rather than fires): a bare
key and a typo'd action each returned 400 having made **no GitHub call at
all**, while `&action=build` reached the dispatch. One dispatch attempt in the
log, from the one URL that asked for it.

### Rebuilding the WWC site on demand

Needed on knockout days, when a rebuild after each game beats waiting for
22:00. Three ways, best-first:

| How | Use it when |
|---|---|
| `https://<worker-url>/?key=CRON_KEY&action=wwc` | **On a phone, watching a game.** Any browser, no laptop, no login. |
| Actions UI → `WWC site build` → **Run workflow** | You want the `fetch` / `force_fetch` checkboxes — i.e. something looks wrong and a box score already on disk needs re-pulling. |
| `gh workflow run wwc.yml` | You are already in a terminal. |

Also available: `?key=CRON_KEY&action=wwccheck` runs the WWC check
**report-only and sends no email** — unlike the scheduled 14:45 pass, which
does. Use it to see what the check sees.

**A run takes ~40s, and mashing the button is safe.** `wwc.yml` sets
`concurrency: cancel-in-progress: false`, so rapid dispatches queue rather
than cancel each other.

**Rebuilding "too early" costs nothing.** If FIBA has not published the box
score yet, the final score still lands and simply is not a link — a score only
becomes a link when the box score is genuinely emitted in the same run (see
`docs/wwc-site-internals.md`, correct-or-blank). Run it again later and the
link appears. The fetch is incremental, so a repeat run is two or three page
fetches, not thirty-six.

**Mid-round rebuilds are safe on knockout days.** `match_games()` refuses to
match a knockout round whose game count disagrees with ours, and the instinct
is that 1-of-4 quarter-finals played would trip it. It does not: FIBA's event
page lists every *fixture* in a round regardless of how many are played, so
the count is 4-vs-4 from the moment the bracket resolves. **But see the
ordering caveat below before trusting a knockout rebuild.**

### ⚠️ UNVERIFIED: knockout games join on FILE ORDER, and six have no times

**Open as of 2026-08-30. Belongs to the Sep 2 live-stats checkpoint.**

Knockout fixtures cannot join on team codes — ours are `TBD` until the bracket
resolves — so `match_games()` joins them on **round, then chronological
order**: it sorts FIBA's games by `datetime_utc` and `zip`s them against our
schedule rows *in CSV file order*. That is correct only if our rows are
themselves chronological.

| Phase | Our file order | Safe? |
|---|---|---|
| `quarter_final` (Sep 10) | 09:30, 12:30, 15:45, 18:45 | ✅ genuinely chronological |
| `third_place` / `final` (Sep 13) | single row each | ✅ nothing to order |
| `qualification_to_qf` (Sep 8–9) | **TBA ×4** | ⚠️ unverified |
| `semi_final` (Sep 12) | **TBA ×2** | ⚠️ unverified |

For those **six** games `tip_gmt` is empty (`time_tba=TRUE`), so nothing
establishes that our file order matches FIBA's chronological order. If they
disagree, a real score lands on the wrong fixture — and `fetch_data.py`'s own
comment names why that is the worst outcome available: it "reads as correct to
everyone." A wrong score is worse than no score.

This is **not** a manual-rebuild problem; a scheduled run carries the identical
risk. Two things close it:

1. **Fill `tip_gmt` for those six rows when FIBA announces the times.** Nothing
   ingests times from FIBA — `fetch_data.py` writes results and box scores
   only — so this is a human edit to
   `sites/wwc/reference/wwc_schedule_2026.csv`. It also fixes the times shown
   on the Games and Groups pages, which today render as TBA.
2. **Guard the zip**: refuse to match a knockout round whose rows lack times,
   rather than silently trusting file order. Same posture as the existing
   count-mismatch skip, one step stricter.

Until both land, treat knockout results from Sep 8, 9 and 12 as
**needing a human eye** after the first rebuild of each round.

### Routing is explicit, and that was a deliberate inversion

`scheduled()` used to end in a catch-all `else` that dispatched `build.yml`
for any cron which was not a health check. Safe with one dispatch cron; unsafe
the moment a second site arrived, because a WWC cron that failed to match
would have fired the **WNBA build** at 22:00 — and `build.yml` posts to
Bluesky, where `post_to_bluesky.py` has no dedupe guard. **A double post is
unrecoverable; a missed build is not.** An unrecognised cron now does nothing
and logs that the two files have drifted. The 11:45 health check repairs a
missed WNBA build; the 11:17 catch-up covers a missed WWC one.

## RESOLVED ISSUE — scheduled run not firing (opened 2026-06-08, resolved 2026-06-09)
**Resolution:** GitHub's native `schedule` missed three mornings straight (June 7,
8, 9). Replaced it with a **Cloudflare Cron Trigger** Worker (`workers/cron/`) that
calls GitHub's `workflow_dispatch` API at 11:17 UTC daily. Setup: create a
fine-grained GitHub PAT (repo `wnba-stats`, Actions: Read and write), then
`cd workers/cron && npx wrangler login && npx wrangler deploy && npx wrangler secret
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
   Worker (`workers/cron/worker.js`) was updated to dispatch `build.yml` and
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
| `fetch_data.py` | Fetches all data directly from ESPN's public API → **three CSVs** (`sites/wnba/data/player_box_2026.csv`, `sites/wnba/data/team_box_2026.csv`, `sites/wnba/data/pbp_2026.csv`) **plus** today's schedule JSON and `sites/wnba/data/linescores_2026.json` (official per-quarter line scores). Incremental: only fetches new games on each run. Box-score quarter columns come from the official line scores JSON — **not** derived from PBP (that reconstruction was wrong in ~47% of games; see note below). If a game has no official line scores, its quarter columns are left blank ("correct-or-blank"). |
| `validate_linescores.py` | One-off cross-check: for every completed game, compares ESPN's official line scores against an independent PBP running-max derivation and confirms each reconciles to the team-box final. Run manually in an env with live ESPN access (`python sites/wnba/validate_linescores.py`); exits non-zero on any mismatch. |
| `build_stats_page.py` | Builds the self-contained HTML from the CSVs + schedule JSON + line scores JSON |
| `sites/wnba/wrangler.toml` | Tells Cloudflare to serve this site's `public/` as a static site. Per-site, not shared infra (D10) |
| `.github/workflows/build.yml` | Daily cron: fetch → build → commit → deploy |
| `core/pyproject.toml` | Python deps for the Action (`pandas`, `requests`), and the packaging for `sag`. Replaced `requirements.txt` at the Phase 1 restructure (D9) |
| `sites/wnba/public/index.html` | The built site Cloudflare serves (regenerated daily) |
| `validate_stats.py` | Layer-2 external verification: diffs our leader boards against stats.wnba.com's API. Runs in CI before the Bluesky post and gates it; also runnable by hand. See "Layer-2 stats validation" below. |
| `validation_report.json` | Latest validator run (per-check PASS/FAIL/SKIPPED). Committed by each build; read by the cron Worker's health check. |
| `player_id_crosswalk.json` | Persisted ESPN `athlete_id` → WNBA `PLAYER_ID` matches, grown by each validator run so later joins are exact even if a name spelling drifts. |
| `workers/cron/` | Cloudflare Worker: triggers daily builds + health check. **Tracked in git as of 2026-07** (reversing the 2026-07-12 stance — it now gates data-quality alerting, so losing its history would hurt). Being tracked does NOT put it in the Actions deploy path: deploys remain manual (`cd workers/cron && npx wrangler deploy`). Secrets (`GH_TOKEN`, `CRON_KEY`) live only in `wrangler secret put`; `.wrangler/` and `.dev.vars` are gitignored. |
| `workers/analytics/` | Cloudflare Worker: receives usage beacons → Workers Analytics Engine. **Tracked in git since 2026-08-05**, under the `.gitignore` rule that every Worker's source is tracked and only its `.wrangler/` build cache is not. Like `workers/cron/`, being tracked does NOT put it in the Actions deploy path: deploys remain manual (`cd workers/analytics && npx wrangler deploy`). See "Usage analytics worker" below. |

## Health check + email alerts (added 2026-06-11)

The cron Worker (`workers/cron/`) now has a second job: at **11:45 UTC** daily it
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

`workers/espn-proxy/` is a narrow, key-authenticated Cloudflare Worker that passes
through to ESPN's WNBA API. It was drafted for the IP-range-block theory that
the table above disproved, so it solves a problem this project does not have.

It's kept only for the case where a future replacement host is genuinely geo-
or IP-fenced, which is the one situation where Cloudflare's egress helps. Try
a plain host swap first. `fetch_data.py` sends `ESPN_PROXY_KEY` as
`X-Proxy-Key` when set, so switching over is two repo secrets and three lines
of workflow YAML — and deleting the secrets is the rollback. Setup and limits
in `workers/espn-proxy/README.md`.

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

Related: `sites/wnba/data/schedule_today.json` now carries a `status` field. `"ok"` with an
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
docstring; the qualification rules are `docs/wnba-leader-qualification-rules.md`
in this repo. The planning record is `LAYER2-VALIDATION-HANDOFF.md`, in the
private docs repo at `statsataglance-docs/product-archive/` (a separate
repository, so that one is a reference rather than a link).

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

`leaders_ok` is not the only thing standing between a build and a post.
`post_to_bluesky.py` skips a day on which **nobody played**: it posts only when
`social_payload.json`'s `through_iso` is yesterday (ET) or later. The build runs
every morning regardless of the schedule, so without that rule a gap in the
calendar becomes a run of identical posts — 17 of them over the 2026 FIBA break
(Aug 31 – Sep 17), each carrying an unchanged board and, because
`emit_social_payload` attaches the "last night" line only for games played
yesterday, no fresh line at all. Nothing else was pausing it: the cron Worker's
off-day awareness only relaxes its own freshness alarm, and `build.yml`'s post
step gates on `leaders_ok` alone. The rule is stateless and date-free, so it
also covers the postseason gap and next May's opener with no edit. `--force`
overrides it for a deliberate manual post, and `SAG_TODAY=YYYY-MM-DD` moves
"today" for testing, exactly as it does in `build_stats_page.py`.

Note that a gameless day is **not** a no-op for the site itself: the Games tab
renders `Today · <date>` and today's slate, so `index.html` changes every
morning and the deploy is doing real work even when no basketball was played.
- **By hand:** `python sites/wnba/validate_stats.py` checks all 11 boards and prints a
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
   cd workers/cron && npx wrangler deploy
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

**The contract** (client in `build_stats_page.py` ↔ worker in `workers/analytics/`):
- Client → `GET`/`POST https://usage.statsataglance.com/t?e=<event>&t=<tab>&s=<utm_source>&r=<0|1>&site=<site>&ref=<host>`
  via `navigator.sendBeacon` (fetch fallback). Wrapped in try/catch — fails
  silently, never blocks the page.
- Events: `pageview` (one per load), `tab` (`t` = tab id, e.g. `leaders`),
  `box` (`t` = `game:<id>`). `s` = utm_source captured on the session's first
  load (cached in `sessionStorage`); `r` = `1` for a returning visitor
  (`localStorage` flag), else `0`.
- Worker writes one aggregate, PII-free data point per event to Workers
  Analytics Engine (dataset `wnba_usage`), CORS-restricted to the
  statsataglance origins in `ALLOWED_ORIGINS`.

| Param | Blob | Meaning |
|---|---|---|
| `e` | `blob1` | `pageview` \| `tab` \| `box` \| `expand` (added 2026-08-17) |
| `t` | `blob2` | tab id; `game:<id>` for `box`; **page key** for `pageview`/`expand` — `''` = the single-file tab site (and every row before 2026-08-17), `players` = the players index, `player:<slug>` = one player page. Keys must fit this column's 32-char slice; `build_player_pages.py` asserts it at build time (`ANALYTICS_KEY_MAX`), because an over-long key silently COLLIDES with any other sharing its first 32 chars rather than merely truncating. |
| `s` | `blob3` | utm_source, session-cached; `none` if untagged |
| `r` | `blob4` | returning visitor — `1` \| `0` |
| `ref` | `blob8` | referring **hostname** — added 2026-08-11 |
| `site` | `blob10` | `wnba` \| `wwc` \| `ncaaw`; `''` on rows predating 2026-08-05, all WNBA |

`blob5`–`blob7` and `blob9` stay reserved for P3/P4 (recency bucket, country,
device class, session id) — see `statsataglance-docs/USAGE-TRACKER-HANDOFF.md`.
Positions are arbitrary to Analytics Engine but not to the queries already
written against them, so nothing in use may be reassigned.

**Referrer (`ref`), added 2026-08-11 — why, and what it does not mean.**
`utm_source` can only ever see links *we* tagged, so organic search, Reddit and
anywhere else that links us unannounced all collapsed into `none` — 97% of
traffic. That was tolerable until Phase 1: ~185 SEO pages whose entire premise
is that search becomes a discovery channel, landing in a bucket that can't
distinguish search from direct. Attribution cannot be applied retroactively,
which is why this shipped ahead of the pages.

- **Hostname only, never the full URL** — referrer paths and query strings carry
  private context (the search terms someone typed, for one).
- Captured once per session and cached in `sessionStorage`, so an in-site
  reload can't overwrite where the visit actually came from.
- `direct` = no referrer was sent. **This is not "typed the URL".** Privacy
  settings, `referrer-policy`, and most in-app browsers strip it, so a low
  `bsky.app` count is *not* low Bluesky traffic — `utm_source` remains the
  reliable signal there. `self` = navigation within the site.
- `''` = the row predates the field, or came from a cached page still running
  the old JS. Kept distinct from `direct` in `usage_report.py`; merging them
  would invent direct traffic that was never measured.

**Tracked in git since 2026-08-05**, along with `workers/cron/`, under the
`.gitignore` rule that a Worker's source is tracked and only its `.wrangler/`
build cache is not. It is still deployed manually and is not part of the daily
HTML build — **so a change here does not ship until someone runs `wrangler
deploy` in this directory.** That is the one trap in this section: the page JS
deploys automatically with the next daily build, the worker does not, and a
page sending `ref` to a worker that ignores it fails silently and looks fine.

### One-time setup
1. **Enable Analytics Engine on the account (one-time).** Until this is done,
   the first deploy fails with `[code: 10089] You need to enable Analytics
   Engine`. Dashboard → Workers & Pages → **Analytics Engine** → enable (free;
   no plan change). You do **not** need to manually create the `wnba_usage`
   dataset — the worker creates it on first write; the binding in
   `wrangler.toml` is what matters.
2. **Deploy the worker:**
   ```bash
   cd workers/analytics && npx wrangler deploy
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
Four stable published values — they are the historical series, so don't
rename them — plus one we subtract rather than analyse:

| Value | Surface |
|---|---|
| `bluesky-post` | the daily automated leaders post's OG card (`post_to_bluesky.py`'s `SITE_URL`) |
| `bluesky-bio` | the profile bio link — `wnba.statsataglance.com/bsky`, a **Cloudflare Redirect Rule** (dashboard → Rules → Redirect Rules) that 301s to `/?utm_source=bluesky-bio`. Configured only in the dashboard; this line is its only record. |
| `x-bio` | the X profile's Website-field link — `wnba.statsataglance.com/x`, a **Cloudflare Redirect Rule** (dashboard → Rules → Overview → Create rule → Redirect Rule) that 301s to `/?utm_source=x-bio`. Configured only in the dashboard; this line is its only record. Added 2026-08-14, confirmed working. |
| `none` | direct, bookmark, organic, or any untagged referrer |
| `owner` | **our own testing traffic.** Not a surface we publish to — a surface we arrive from, tagged so the report can leave it out. Set by loading the site through the bookmarked `https://wnba.statsataglance.com/?utm_source=owner`; the page JS session-caches it, so entering through the bookmark tags the whole visit, player pages included. Excluded by default in `usage_report.py`; `--include-owner` keeps it. Added 2026-08-22. |

The post card was untagged until 2026-08-04, so every click on it before that
date is indistinguishable from direct traffic. Not recoverable retroactively.

**Tagging a manual post.** Nothing is automatic. The tag lives in the URL you
paste — the page reads `?utm_source=` on arrival and caches it for the session.
`bluesky-post` is tagged only because `post_to_bluesky.py` hardcodes it in
`SITE_URL`; a post written by hand carries whatever you put in the link, and
nothing if you paste the bare URL. Do NOT reuse `bluesky-post` for hand-written
posts — it would mix them into the bot's series, which is the one clean
comparison this table exists to protect. New values need no code change:
`usage_report.py`'s `KNOWN_SOURCES` controls presentation order only, and
anything unrecognised is still reported.

### The posting log — `sites/<slug>/reference/posts.csv`

The only record of WHY traffic moved. Analytics Engine can say 22 people
arrived at 15:00 UTC; it can never say a post went out at 14:47. One
hand-typed row per public post: `when_et,platform,what,utm_tag`.

It lives under `reference/` because .gitignore's rule is provenance, not
extension — `*.csv` is ignored as a build artifact and `reference/` is the
carve-out for human-typed files with no upstream. At the repo root it would be
silently untracked, invisible even to `git status`.

Times are **local wall-clock** (`America/New_York`), because that is when a
human posts, converted to UTC on read. Consequence worth internalising: an
evening ET post lands on the NEXT UTC day — 9pm ET is 01:00 UTC tomorrow — so
look for it under the following `--date`.

### Reading one day by the hour

```
python usage_report.py --hourly                    # today (UTC)
python usage_report.py --hourly --date 2026-08-18
python usage_report.py --days 30 --include-owner   # keep our own testing
```

Daily totals cannot answer "did my post cause that?" — a post at 18:47 and a
game tipping at 19:00 land on the same date. `--hourly` separates them and
draws `posts.csv` markers on the hours the posts landed in.

The "views in HH:00–HH:59" figure under each post is **proximity, not proof**,
and it leans generous: hour buckets mean the post's own hour is counted whole
even though the post landed partway through it. A window running past midnight
UTC is cut there and says so — this report queries one day only.

### Reading rows instead of rates

```
python usage_report.py --rows --date 2026-08-18   # every row that day
python usage_report.py --sessions --days 30       # visits, not pageviews
python usage_report.py --sessions --days 30 --session-gap 15
python usage_report.py --sessions --days 30 --limit 0   # no truncation
```

**At 10–30 pageviews a day the aggregates are the wrong instrument.** A day
fits on one screen, and reading it answers questions no rate can. This was
learned the hard way on 2026-08-22: the 29-view "spike" of 2026-08-18 was
really 28 rows, 11 of which were a single four-minute session walking the
players index — our own testing, confirmed afterwards. Every engagement
signal read off the aggregates that week (player pages "bouncing", seven
"spike days", one player page "pulling") decomposed into our own activity
once the rows were visible.

`--rows` applies NO filter, owner traffic included, because the point is to
see everything that happened.

`--sessions` truncates its two lists — 15 visits, 8 expands — and both are
sorted so the interesting end survives: visits by pageviews descending,
expands by delay ascending. The totals printed above each list are always
complete, so the truncation hides rows, never magnitude. `--limit N` sets
both caps; `--limit 0` prints everything.

`--sessions` groups activity into visits. Two caveats that matter:

- **It groups ACTIVITY, not people.** There is no visitor id in this schema
  (blob9 is reserved for one, P4), so two visitors browsing at the same time
  merge into one session and nothing can separate them.
- **The 30-minute rule is a convention.** On 2026-08-18 a real gap of 29m55s
  sits five seconds inside it. Over `--session-gap` 5→60 that day reports
  anywhere from 11 to 6 sessions, so quote a session COUNT with a caveat.
  The walk flag, by contrast, held at 1 across every setting.

The **walk flag** guesses that a session is us rather than a visitor: ≥5
pageviews, ≥3 distinct player pages, median gap under 60s. That is the shape
of the labelled 2026-08-18 burst — a visitor arrives and looks at a thing or
two, we walk a roster. It is a guess from behaviour, it is never subtracted
from any total, and until `owner`-tagged sessions exist it is UNVALIDATED.
The Shape-check block scores it against the tag once both are present.

**Continuations are merged.** A run of activity whose first row has
`ref=self` began with an in-site click, so the visitor was already here — it
is the tail of an earlier visit, not a new one. Counting it as new inflates
the visit count, which is why `--sessions` reports visits and says how many
runs it glued back together. Bounded at `SELF_MERGE_MAX_HOURS` (4): `self`
says the tab came from our own domain, not *when*, and a tab left open
overnight would otherwise weld two unrelated sittings into one. On
2026-08-18 this takes 7 runs down to 5 real visits.

**Expand timing.** `--sessions` splits the expand count by how long after the
pageview it fired. The four expands on 2026-08-18 came at 4s, 8s, 8s and 21s
— that is someone who knows where the button is, not someone who read the
page and wanted more. The main report's "an expand is the signal an arrival
actually READ the page" was overclaiming, and now points here instead.
`FAST_EXPAND_SECONDS` (10) is a judgement call and nothing is subtracted.

### A count from Analytics Engine is not exact

`SUM(_sample_interval)` fixes the large, systematic undercount that `count()`
produces, and the warning above is right that it is mandatory. It does not
make the number exact. On 2026-08-22 the same predicate over 2026-08-18
returned 29 from one aggregate shape and 28 from another *in the same run*,
while direct row enumeration showed 28 rows, all with `_sample_interval` of 1
— i.e. sampling had not even engaged. Row enumeration is the ground truth
available at this volume; treat aggregate totals as ±1 or so, and do not
build an argument on a difference that small.

### Backlog

The P-numbers are referenced from comments in `workers/analytics/worker.js` and
`usage_report.py`; this table is where they actually live. (Those comments also
point at a `USAGE-TRACKER-HANDOFF.md` that was **never committed** — it is not
in git history at all. Treat this section as its replacement rather than going
looking for it.)

| id | item | state |
|---|---|---|
| P2 | daily rollup into `usage_history.jsonl`, so data outlives Analytics Engine's 90-day retention | **done** |
| P3 | recency bucket in `blob5` — `returning` is set on first visit and never expires, so its share can only ever climb. It is not a retention rate and the report says so every run. | open |
| P4 | country / device / **session id** in `blob6` / `blob7` / `blob9` | open |
| P5 | usage dashboard — see below | open, **low** |

P4's session id is the interesting one: without it `--sessions` groups
ACTIVITY, not people, and two visitors browsing at the same time merge into a
single visit with nothing able to separate them. Everything `--sessions` says
about visit counts carries that caveat until blob9 is filled.

#### P5 — usage dashboard (low priority)

A static HTML page rendering `usage_history.jsonl` as trend lines, opened
locally instead of running the CLI. Deliberately parked, for two reasons.

**Wait for clean data.** As of 2026-08-22 the history is mostly pre-instrument:
no `owner`-tagged days, no posting log entries, and an expand count we know is
substantially our own testing. A dashboard built now would draw confident lines
through numbers we have already established are wrong. Mid-September, after a
few weeks of the bookmark and `posts.csv` being used, is the earliest the
charts would mean anything.

**Backfilled zeros are not measurements.** The July rows were backfilled on
2026-08-22 and therefore contain `expands: 0` and `by_surface: {main: ...}` for
dates when those fields did not exist. A naive chart draws a flat zero line
across July and makes it look like engagement grew from nothing. The dashboard
MUST gray out each series before its field went live:

| field | live since |
|---|---|
| `site` | 2026-08-05 |
| `referrers` | 2026-08-11 |
| `by_surface`, `expands`, `top_player_pages` | 2026-08-17 |

**The file is mixed-shape, and that is deliberate.** Backfilled rows run
today's code, so they carry keys their live-written contemporaries lack —
counterintuitively the OLDEST rows are the richest:

| days | `referrers` | `by_surface` / `expands` |
|---|---|---|
| Jul 12 – Aug 1 (backfilled 2026-08-22) | yes | yes |
| Aug 2 – Aug 10 (written live) | no | no |
| Aug 11 – Aug 16 (written live) | yes | no |
| Aug 17 – (written live) | yes | yes |

So charting player-pages over time shows July at zero, a HOLE through Aug
2–16, then real numbers. The hole is older-format rows, not missing traffic.

Aug 2–16 is still inside retention and could be re-snapshotted to make the
file uniform. **Do not.** Two reasons. It rewrites records written at the
time, and a contemporaneous log whose rows get retroactively regenerated is
no longer evidence of anything. And re-running would re-query Analytics
Engine, whose aggregates are NOT stable to the row (see the section above —
the same predicate returned 28 and 29 for 2026-08-18 in the same minute), so
flattening could silently shift historical counts by a view or two in
exchange for cosmetic tidiness.

The dashboard must therefore distinguish three states, not two: a present
zero (measured, genuinely nothing), a missing key (the field did not exist
yet), and `"(not collected)"` inside `referrers` (the field existed but this
row predates it). Treating any of those as a plain zero draws a lie.

Note also that the rollup stores aggregates only. The row-level view that
`--rows` and `--sessions` read — visit shapes, expand timing, per-arrival
referrers — ages out at 90 days and is preserved nowhere. If that detail turns
out to matter for the dashboard, capturing it is a prerequisite, not a
follow-up.

## Dependencies + repository security posture (documented 2026-08-05)

### Python dependencies

`core/pyproject.toml` is the only manifest, consumed at one place —
`pip install -e core/` in `build.yml`. It replaced `requirements.txt` at the
Phase 1 restructure: one declaration, no per-site files, because there is no
divergence to accommodate (D9). The three Cloudflare Workers
are plain JS with **no** `package.json`; `wrangler` is invoked via `npx` at
deploy time and is not a tracked dependency.

```
pandas>=3,<4
requests>=2,<3
```

**Upper-bounded, not exact-pinned, on purpose.** Patch and minor releases still
flow automatically, so these do not go stale and security fixes arrive with no
intervention. What's gated is the **major** bump — where APIs get removed and
builds break — which deserves a human choosing the moment. Exact pins were
rejected because they stop receiving fixes and eventually hit a compatibility
wall when the runner's Python moves, deferring maintenance into a lump rather
than removing it.

Until 2026-08-05 both were fully unpinned: CI installed whatever had shipped
most recently, unreviewed, on a repo whose workflow holds a write token and
commits to `main`. It also meant no reproducibility — the dev laptop resolved
pandas 3.0.3 while CI resolved 3.0.5.

**To take a new major version:** raise the ceiling here deliberately, run the
build, and confirm the page before merging.

The real install surface is 9 packages — the 2 direct plus `numpy`, `urllib3`,
`certifi`, `idna`, `charset_normalizer`, `python-dateutil`, `six`.

### GitHub security settings

Not derivable from the repo; recorded here so it isn't re-litigated. **This
repo is public**, which is what makes the free tier apply.

| Setting | State | Why |
|---|---|---|
| Secret scanning | **on** | Free for public repos. Scans full history. 0 alerts ever. |
| Secret scanning push protection | **on** | Free. *Blocks the push* before a detected secret lands — the control that matters most. |
| Dependabot alerts | **on** (2026-08-05) | Free, passive, no PRs. 0 alerts. |
| Dependabot malware alerts | on (2026-08-05) | Free, passive. Different threat class from CVEs: actively malicious packages — typosquats, hijacked maintainers. Matters because CI runs `pip install` with a write token, and supply-chain attacks favour small transitive packages. |
| Dependabot security updates | **off, deliberately** | Its job is bumping a *pinned* version. With upper bounds, `pip` already installs the newest in-range build every run, so a patched release arrives automatically without a PR. Near-inert here. |
| Grouped security updates | off | Moot while security updates is off. |
| Dependabot version updates | **off, deliberately** | Needs a `.github/dependabot.yml` (absent), and with upper bounds there is no pinned version to bump. No-op plus PR noise. |
| Secret scanning non-provider patterns | **unavailable** | Requires paid GitHub Advanced Security / Secret Protection — not a choice we made. The API accepts a PATCH enabling it, returns `200 OK`, and silently ignores it; the toggle is absent from the settings UI. Don't retry it. |
| Secret scanning validity checks | **unavailable** | Same paid gate, same silent-no-op behaviour. |

**Dependabot rules** (Settings → Code security → Dependabot rules) — GitHub
presets, not readable via the API, so recorded here:

| Rule | State | Why |
|---|---|---|
| Dismiss low-impact alerts for development-scoped dependencies | enabled (GitHub default) | Effectively inert: `core/pyproject.toml` declares no optional/dev extras, so nothing carries dev/prod scope metadata, so nothing should be classified development-scoped. Left on. Worth remembering it exists if an alert ever seems to vanish — and note that in this repo the "dev" environment *is* the production build, so a dev-scoped dismissal would be misleading if it ever fired. |
| Dismiss package malware alerts | **disabled — keep it that way** | This rule auto-dismisses malware alerts. Enabling it would silently undercut the malware alerts above, which are one of the few controls covering the `pip install`-with-a-write-token path. |

**Known residual gap.** `CRON_KEY` and `PROXY_KEY` are self-generated random
strings matching no provider pattern, so free secret scanning would not catch
them. Non-provider patterns is the feature that would, and it's paywalled. What
protects them instead is design, not scanning: secrets live only in
`wrangler secret put` and GitHub repo secrets, never in tracked files, with
`.dev.vars` and `.env` gitignored. If a belt-and-braces check is ever wanted,
a pre-commit hook grepping staged diffs for high-entropy strings is the free
local equivalent — considered 2026-08-05, not built.

**A second exposure this note originally missed (added 2026-08-31).** `CRON_KEY`
is used *in a URL*, including from a phone, which puts it in browser history and
bookmark sync — places no repo-side control reaches. And the Worker's hostname is
effectively public: this repo publishes the account subdomain (see the
`workers.dev` note above) and the Worker's name, so anyone reading it can
construct the endpoint. The two site Workers set `workers_dev = false`; the cron
Worker cannot, because the manual URL is its purpose.

What changed on 2026-08-31 is the **blast radius**, not the secrecy: requiring
`&action=build` means a leaked, shoulder-surfed or mistyped URL can no longer
reach the one unrecoverable action. Lengthening the key is the complementary
move and is a `wrangler secret put CRON_KEY` away, no redeploy — but note it
invalidates any saved bookmark carrying the old key, which then fails *silently*
by returning the status page. The `?utm_source=owner` analytics bookmark is a
different mechanism entirely and is unaffected.

## Player pages + SEO surface (added 2026-08-16, deployed 2026-08-17)

The daily build now also emits ~227 static player pages
(`public/players/<slug>/index.html`), a players index (`/players/`),
`sitemap.xml`, and `robots.txt` — all from `build_player_pages.py`, which
runs right after the main page build (gated by `validate_hooks.py`). They
deploy exactly like `index.html`: committed by the bot, uploaded by the
Action's `wrangler deploy`. No Cloudflare config changed — the `[assets]`
directory upload picks up the whole tree, and `/players/<slug>/` resolves
to that directory's `index.html` automatically.

Analytics: player pages are real navigations, so the Cloudflare Web
Analytics beacon sees each pageview (unlike the tab site, where it only
ever sees "/"). They also carry the shared usage-beacon JS — pageviews
plus an `expand` event when the splits/game-log expand opens, both keyed
`player:<slug>` in `blob2` (see the beacon schema above). The analytics
worker needed NO change: it slices `e`/`t` without a whitelist, so no
manual worker redeploy was involved.

**The `expand` event counts a CHOICE, not a state.** The expand is sticky
per visitor (localStorage), and setting `details.open` fires a `toggle`
event *asynchronously* — so the sticky restore arrives at the listener
looking exactly like a click. Counting it was wrong twice over: a
returning visitor would emit an expand on every player page forever,
inflating the very signal the event exists to measure, and the restore
runs before `initTracking()`, so those events carried no `utm_source` and
reported as new visitors. A `restored` flag suppresses that first
synthetic toggle only. Observed in production 2026-08-17 and fixed the
same hour; if you ever change this JS, re-verify by loading a player page
with `sag-expand=1` already in localStorage and confirming the page emits
a pageview and nothing else.

Minor known undercount, accepted: `toggle` events coalesce, so two clicks
inside the double-click threshold can yield a single net open with no
event. It errs toward under-reporting engagement, which is the safe
direction.

### robots.txt — on THIS zone an origin file REPLACES Cloudflare's block

> **Corrected 2026-08-17 by direct observation.** The section previously
> said Cloudflare *merges* (prepends) its managed block onto an origin
> 200, per its published docs. **That is not what this zone does.** The
> planning assumption was wrong and the cutover measured it.

Three byte-exact captures in `sites/wnba/reference/robots-txt-observed/`
tell the whole story, and the rollback rehearsal confirmed it in both
directions:

| Capture | Origin file? | Served |
|---|---|---|
| `2026-08-16.txt` | no | **1248 B** — Cloudflare Content Signals boilerplate |
| `2026-08-17.txt` | **yes** | **77 B** — only our three lines; CF block **gone** |
| `2026-08-17-during-rollback-rehearsal.txt` | no (reverted) | **1248 B**, byte-identical to 08-16 |

So: with no origin file Cloudflare serves its boilerplate; the moment an
origin `robots.txt` exists it is served **alone**. Removing ours brings
Cloudflare's back, unchanged. No merge, in either direction.

Note what the 1248-byte block actually contained: **entirely comments** —
the preamble *defining* `search` / `ai-input` / `ai-train` plus the EU
Directive Art. 4 sentence, and **zero `Content-Signal:` directives**. Per
the policy's own clause (c), declaring no signal "neither grants nor
restricts." The site had therefore taken **no position**, and never
published `ai-train=no` as planning had assumed. What we stopped serving
was an explanation of a policy we were not invoking.

Current live file (`sag.seo.robots_txt`): `User-agent: * / Allow: /` plus
the absolute `Sitemap:` line — which is what Search Console needs and the
whole reason the file exists.

**Open question, deliberately left open:** whether to reinstate a Content
Signals block in `sag.seo.robots_txt` now that Cloudflare's is no longer
served. The original argument against replicating it ("ours would freeze
while Cloudflare's stays current") is much weaker now — Cloudflare's is
not being served at all on this host, so there is nothing to drift from.
The counter-argument is that the block asserted nothing. See the backlog.

**Verify after any deploy that changes robots.txt:**

```bash
curl -s https://wnba.statsataglance.com/robots.txt \
  > sites/wnba/reference/robots-txt-observed/$(date -u +%F).txt
```

Expect **only** our lines. If a Cloudflare block ever reappears alongside
them, the zone's managed-robots.txt behavior has changed — record the new
capture and revisit.

### Rolling back a merge — corrections learned by rehearsing (2026-08-17)

The player-pages rollback was rehearsed for real on cutover day. Three
things the written procedure got wrong; fix these before trusting it in
an actual incident:

1. **`git revert -m 1 HEAD` is usually wrong.** The daily bot commits
   during the very build you just triggered, so by the time you roll back,
   `HEAD` is the bot's commit and the revert errors out ("not a merge").
   **Name the merge commit explicitly:** `git revert -m 1 <merge-sha>`.
2. **Expect modify/delete conflicts on every generated file** the bot
   rewrote after the merge — 229 of them on cutover day (227 player pages
   + `sitemap.xml` + `robots.txt`). In a rollback the resolution is to
   accept the deletion:
   ```bash
   git diff --name-only --diff-filter=U | tr '\n' '\0' | xargs -0 git rm -q -f
   git revert --continue --no-edit
   ```
3. **Let the deploy propagate before verifying.** Checking ~8 s after the
   run completed reported `200` for paths that were genuinely gone;
   ~60 s later the same paths correctly `404`d. Poll until the expected
   code appears, or append a cache-busting query string (`?cb=$RANDOM`) —
   otherwise you will misread a good rollback as a broken one.

End to end (revert → rebuild → verify → roll forward → rebuild → verify)
the rehearsal took about ten minutes, two ~50 s builds included.

### Search Console

`sitemap.xml` regenerates every build with `lastmod` = the data date.
Submitting it to Google Search Console is a **manual, one-time step**
(GSC → Sitemaps → add `https://wnba.statsataglance.com/sitemap.xml`);
GSC was verified 2026-08-13 and its data is not retroactive.
