// wnba-stats-cron — a Cloudflare Worker with two jobs:
//
//   1. 11:17 UTC  Dispatch the GitHub Actions "build.yml" workflow
//                 (replaces GitHub's unreliable native `schedule` trigger).
//   2. 11:45 UTC  Health check: verify the build ran, succeeded, and the
//                 live site is fresh. Emails an alert ONLY on failure —
//                 silence means everything is fine.
//      13:15 UTC  Same health check, second pass.
//      14:45 UTC  Same health check, FINAL pass.
//
// ── Self-healing retries (added 2026-08-05) ────────────────────────────────
// Most build failures are transient upstream blips, not bugs: ESPN's API goes
// away for a few minutes and the build quietly publishes a stale page. On
// 2026-08-05 that cost a full day of data until it was fixed by hand.
//
// So the health check now REPAIRS as well as reports. Problems are tagged
// retryable (a fresh build would plausibly fix it — failed run, stale site,
// data behind the source) or not (GitHub API unreachable — rebuilding is
// pointless). On a retryable problem the check re-dispatches the build and
// stays quiet; the next pass re-checks. Only the FINAL pass emails about a
// retryable problem, by which point ~3 hours and 2 extra builds have failed
// to fix it and it wants a human. Non-retryable problems email immediately.
//
// The Bluesky post is suppressed on auto-retries UNLESS we can prove the
// earlier run didn't post (post_to_bluesky.py has no dedupe guard, so a
// double post is unrecoverable while a missed post is merely a missed post).
//
// Secrets required (wrangler secret put ...):
//   GH_TOKEN  — fine-grained PAT for moscowjh/wnba-stats, Actions read/write
//   CRON_KEY  — shared key for manual test runs via the Worker URL
//
// Email requires Email Routing enabled on statsataglance.com with
// horowitz.jason@gmail.com as a verified destination address, plus the
// [[send_email]] binding in wrangler.toml.

import { EmailMessage } from "cloudflare:email";

const REPO = "moscowjh/wnba-stats";
const WORKFLOW = "build.yml";
const SITE_URL = "https://wnba.statsataglance.com";
const DISPATCH_CRON = "17 11 * * *";
// Health-check passes, in order. The last one is FINAL: it stops retrying and
// emails whatever is still broken. Keep these in sync with wrangler.toml.
const CHECK_CRON = "45 11 * * *";
const RETRY_CRON = "15 13 * * *";
const FINAL_CRON = "45 14 * * *";
const CHECK_CRONS = [CHECK_CRON, RETRY_CRON, FINAL_CRON];
const ALERT_FROM = "alerts@statsataglance.com";
const ALERT_TO = "horowitz.jason@gmail.com";
// Must match ESPN_ORIGIN in fetch_data.py. `site.api.espn.com` went
// permanently 403 on 2026-08-05 (see the note there), which had been quietly
// breaking THIS check too: gamesPlayedOn() caught the failure, returned null,
// and null disables the entire freshness assertion — so the health check could
// not have caught a stale site on a game day. Fixing the fetcher without
// fixing this line would have left the safety net still torn.
const ESPN_SCOREBOARD =
  "https://site.web.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard";

// ── GitHub helpers ─────────────────────────────────────────────────────────

function ghHeaders(env) {
  return {
    "Authorization": `Bearer ${env.GH_TOKEN}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "wnba-stats-cron",
  };
}

// post=true (default) lets the build post the daily leaders to Bluesky.
// We only send the `inputs` object when suppressing the post, so the daily
// scheduled dispatch stays a bare {ref} call (robust even if the workflow
// input weren't defined). build.yml's `post` input defaults to true.
async function dispatch(env, post = true) {
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const body = post ? { ref: "main" } : { ref: "main", inputs: { post: "false" } };
  const res = await fetch(url, {
    method: "POST",
    headers: ghHeaders(env),
    body: JSON.stringify(body),
  });
  const ok = res.ok;
  const detail = ok
    ? `dispatched build.yml${post ? "" : " (post suppressed)"}`
    : `failed ${res.status}: ${await res.text()}`;
  console.log(detail);
  return { ok, status: res.status, detail };
}

async function todaysRun(env) {
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=5&event=workflow_dispatch`;
  const res = await fetch(url, { headers: ghHeaders(env) });
  if (!res.ok) throw new Error(`GitHub runs API ${res.status}`);
  const { workflow_runs = [] } = await res.json();
  const today = isoDate(Date.now());
  return workflow_runs.find((r) => r.run_started_at?.startsWith(today)) || null;
}

// Did the bot publish today? Scans ALL of today's commits, not just the
// latest — a manual push after the morning build must not mask the bot's
// "Daily stats update" commit (caused a false alarm on 2026-06-11).
async function publishedToday(env, today) {
  const url = `https://api.github.com/repos/${REPO}/commits?since=${today}T00:00:00Z&per_page=30`;
  const res = await fetch(url, { headers: ghHeaders(env) });
  if (!res.ok) throw new Error(`GitHub commits API ${res.status}`);
  const commits = await res.json();
  return commits.some((c) =>
    c.commit?.message?.includes(`Daily stats update: ${today}`)
  );
}

// ── Date helpers (all UTC) ─────────────────────────────────────────────────

function isoDate(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

function yesterdayIso() {
  return isoDate(Date.now() - 24 * 60 * 60 * 1000);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── Site freshness ─────────────────────────────────────────────────────────

// Returns the data-through date of the live site as YYYY-MM-DD, or null.
// Prefers the machine-readable meta tag; falls back to the visible
// "Stats as of <Month DD, YYYY>" text for pages built before the tag existed.
async function siteDataThrough() {
  const res = await fetch(SITE_URL, {
    headers: { "Cache-Control": "no-cache", "User-Agent": "wnba-stats-cron" },
  });
  if (!res.ok) throw new Error(`site fetch ${res.status}`);
  const html = await res.text();

  const meta = html.match(/name="data-through" content="(\d{4}-\d{2}-\d{2})"/);
  if (meta) return meta[1];

  const visible = html.match(/Stats as of ([A-Z][a-z]+ \d{1,2}, \d{4})/);
  if (visible) {
    const d = new Date(visible[1] + " UTC");
    if (!isNaN(d)) return isoDate(d.getTime());
  }
  return null;
}

// ── Layer-2 validation drift ───────────────────────────────────────────────

// Reads validation_report.json, committed to main by each build's
// validate_stats.py step, and folds any FAIL into the health-check email.
// Returns { problem } for real drift / validator crashes, { note } otherwise —
// notes only ever ride along on an email another problem triggered, which
// preserves the "silence means everything is fine" contract.
//
// Also returns:
//   retry     — true when a rebuild could plausibly fix it. A
//               `data_completeness` FAIL ("our data is behind the source")
//               means the fetch missed a game, which is exactly what another
//               build repairs. A *value* mismatch on a stat we do have is a
//               logic bug — rebuilding just reproduces it, so no retry.
//   leadersOk — the run's Bluesky gate, or null if unknown/stale. Used to
//               decide whether an auto-retry may post.
async function validationDrift(today) {
  let res;
  try {
    // Cache-buster: raw.githubusercontent caches ~5 min and the report is
    // committed ~20 min before this check runs.
    res = await fetch(
      `https://raw.githubusercontent.com/${REPO}/main/validation_report.json?cb=${Date.now()}`,
      { headers: { "Cache-Control": "no-cache", "User-Agent": "wnba-stats-cron" } }
    );
  } catch (e) {
    return { note: `validation report unreachable: ${e.message}` };
  }
  if (!res.ok) return { note: `validation report unavailable (${res.status})` };

  let rep;
  try {
    rep = await res.json();
  } catch {
    return { problem: "validation_report.json is not valid JSON — check the validate step" };
  }

  const runDate = (rep.run_at_utc || "").slice(0, 10);
  if (runDate !== today) {
    return { note: `validation report is from ${runDate || "unknown"}, not today` };
  }
  const leadersOk = rep.leaders_ok === true;
  if (rep.error) {
    // A validator crash is usually stats.wnba.com being unreachable — retryable.
    return {
      problem: `Stats validator crashed (Bluesky post was blocked): ${rep.error}`,
      retry: true,
      leadersOk,
    };
  }

  const cats = rep.categories || [];
  const fails = cats.filter((c) => c.status === "FAIL");
  if (fails.length) {
    const failedChecks = fails.flatMap((c) =>
      (c.checks || []).filter((k) => k.status === "FAIL").map((k) => k.check)
    );
    const incomplete = failedChecks.includes("data_completeness");
    const detail = fails
      .map((c) =>
        `${c.category}: ` +
        (c.checks || [])
          .filter((k) => k.status === "FAIL")
          .map((k) => `${k.check}${k.detail ? ` (${k.detail})` : ""}`)
          .join("; ")
      )
      .join("\n  ");
    return {
      problem:
        (incomplete
          ? `Our data is BEHIND stats.wnba.com — the fetch missed at least one ` +
            `game (most likely a transient ESPN outage during the build)`
          : `Layer-2 stats validation FAILED against stats.wnba.com`) +
        (rep.leaders_ok === false ? " — the Bluesky post was blocked" : "") +
        `:\n  ${detail}`,
      retry: incomplete,
      leadersOk,
    };
  }

  const skips = cats.filter((c) => c.status === "SKIPPED");
  if (skips.length) {
    return {
      note: `validation skipped (${skips[0].checks?.[0]?.detail || "source not caught up"})`,
      leadersOk,
    };
  }
  return {
    note: `validation: ${cats.length} categor${cats.length === 1 ? "y" : "ies"} pass`,
    leadersOk,
  };
}

// Were any WNBA games played on the given date? (ESPN public scoreboard.)
// Returns true/false, or null if ESPN is unreachable (treated as "unknown").
//
// Retries with backoff for the same reason fetch_data.py does: an "unknown"
// here silently disables the whole freshness assertion, so one bad second of
// ESPN uptime must not be allowed to blind the health check (2026-08-05).
async function gamesPlayedOn(iso) {
  const delays = [2000, 5000, 10000];
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(`${ESPN_SCOREBOARD}?dates=${iso.replaceAll("-", "")}`, {
        headers: { "User-Agent": "wnba-stats-cron" },
      });
      if (res.ok) {
        const data = await res.json();
        return (data.events?.length ?? 0) > 0;
      }
      console.log(`ESPN scoreboard ${res.status} (attempt ${attempt + 1})`);
    } catch (e) {
      console.log(`ESPN scoreboard error (attempt ${attempt + 1}): ${e.message}`);
    }
    if (attempt >= delays.length) return null;
    await sleep(delays[attempt]);
  }
}

// ── Health check ───────────────────────────────────────────────────────────

// `final` marks the last pass of the day: stop repairing, start escalating.
async function healthCheck(env, { final = false, label = "check" } = {}) {
  const today = isoDate(Date.now());
  const yesterday = yesterdayIso();
  const problems = [];   // { msg, retry }
  const notes = [];

  // retry:true  — another build would plausibly fix this (transient upstream)
  // retry:false — a human is needed; email now, don't burn builds on it
  const fail = (msg, retry = false) => problems.push({ msg, retry });

  // 1. Did today's build run, and did it succeed?
  let run = null;
  try {
    run = await todaysRun(env);
    if (run && run.status !== "completed") {
      notes.push(`run ${run.id} still ${run.status} — waiting 3 min and re-checking`);
      await sleep(3 * 60 * 1000);
      run = await todaysRun(env);
    }
  } catch (e) {
    // We can't see the build at all, so we can't reason about it — and a fresh
    // dispatch would go through the same API. Not retryable.
    fail(`Could not query GitHub Actions API: ${e.message}`);
  }

  if (run === null && problems.length === 0) {
    fail(
      `No "Daily WNBA stats build" run today (${today}). The 11:17 UTC dispatch ` +
      `likely didn't fire. Fix: trigger manually via the Actions "Run workflow" ` +
      `button, or this Worker's ?key= test URL; then check Cloudflare cron logs.`,
      true
    );
  } else if (run && run.status !== "completed") {
    // Still running after the 3-min wait. Don't dispatch — the `daily-build`
    // concurrency group would just queue a duplicate behind it.
    fail(
      `Build run is still "${run.status}" 30+ minutes after dispatch: ${run.html_url}`
    );
  } else if (run && run.conclusion !== "success") {
    fail(`Build run finished with conclusion "${run.conclusion}": ${run.html_url}`, true);
  }

  // 2. Did it publish, and is the live site fresh?
  //
  // Freshness is driven by WHETHER GAMES WERE PLAYED, not by whether a commit
  // exists. A commit is a poor proxy: since 2026-07-27 the build also commits
  // validation_report.json, whose run_at_utc timestamp changes every run, so
  // the staged diff is never empty and a "Daily stats update" commit now lands
  // on every run — including off-days. Keying the freshness expectation on that
  // commit made the off-day branch unreachable and would have emailed a false
  // alarm every off-day. Ground truth is the schedule, so ask ESPN first.
  if (run && run.conclusion === "success") {
    let committedToday = false;
    let commitsQueryOk = true;
    try {
      committedToday = await publishedToday(env, today);
    } catch (e) {
      commitsQueryOk = false;
      fail(`Could not query GitHub commits API: ${e.message}`);
    }

    const played = await gamesPlayedOn(yesterday); // true | false | null

    if (played === false) {
      // Off-day. No freshness expectation — but still confirm the site serves.
      notes.push(`off-day: no games on ${yesterday}; freshness check skipped`);
      try {
        const through = await siteDataThrough();
        notes.push(`site reachable, showing data through ${through ?? "unknown"}`);
      } catch (e) {
        // Site down on an off-day is a Cloudflare/serving problem, not a data
        // problem — rebuilding the same page won't help.
        fail(`Could not fetch the live site on an off-day: ${e.message}`);
      }
    } else if (played === true) {
      // Games were played yesterday: the site must show them.
      try {
        let through = await siteDataThrough();
        if (through !== yesterday) {
          // Cloudflare redeploy can lag a push by a couple of minutes.
          notes.push(`site shows ${through}, expected ${yesterday} — retrying in 2 min`);
          await sleep(2 * 60 * 1000);
          through = await siteDataThrough();
        }
        if (through !== yesterday) {
          fail(
            `WNBA games were played on ${yesterday}, but the live site still shows ` +
            `data through ${through ?? "unknown"}. Either the fetch returned nothing ` +
            `or the Cloudflare redeploy failed — check the run log (${run.html_url}) ` +
            `and the Workers & Pages deploy log.`,
            true
          );
        }
      } catch (e) {
        fail(`Could not fetch the live site: ${e.message}`);
      }

      if (commitsQueryOk && !committedToday) {
        fail(
          `Build succeeded but committed nothing, yet ESPN shows WNBA games were ` +
          `played on ${yesterday}. The data fetch may have silently returned ` +
          `nothing — check the run log: ${run.html_url}`,
          true
        );
      }
    } else {
      // ESPN unreachable — can't tell an off-day from a missed update. Fall back
      // to the commit heuristic, but only NOTE, never alarm, to avoid crying
      // wolf on what is really an ESPN outage.
      notes.push(
        `ESPN scoreboard unreachable, so ${yesterday} could not be confirmed as a ` +
        `game day; freshness not asserted (commit seen today: ${committedToday})`
      );
    }
  }

  // 3. Layer-2 stats validation: did today's build's validate step find drift?
  //
  // leadersOk stays null unless a *today* report says otherwise. Null means
  // "we don't know whether the earlier run posted", and the retry then errs
  // toward not posting.
  let leadersOk = null;
  if (run && run.conclusion === "success") {
    const v = await validationDrift(today);
    if (v.leadersOk !== undefined) leadersOk = v.leadersOk;
    if (v.problem) fail(v.problem, v.retry === true);
    else if (v.note) notes.push(v.note);
  }

  // ── Repair or escalate ───────────────────────────────────────────────────
  const ok = problems.length === 0;
  const canRetry = problems.some((p) => p.retry);
  const needsHuman = problems.some((p) => !p.retry);
  const willRetry = canRetry && !final;

  // Only post from a retry build when we can PROVE the earlier run didn't
  // post. The post step runs `if inputs.post && leaders_ok == 'true'`, so:
  // no successful run at all → nothing posted; leaders_ok false → step was
  // skipped. Anything else (including leaders_ok unknown) → assume it posted.
  const earlierRunPosted = run?.conclusion === "success" && leadersOk === true;
  const postOnRetry = !earlierRunPosted;

  let retryDetail = null;
  if (willRetry) {
    const r = await dispatch(env, postOnRetry);
    retryDetail = r.ok
      ? `auto-rebuild dispatched${postOnRetry ? "" : " (post suppressed — already posted today)"}`
      : `auto-rebuild dispatch FAILED: ${r.detail}`;
    notes.push(retryDetail);
    // If we couldn't even dispatch the repair, nothing is coming to fix this —
    // escalate now rather than waiting out the remaining passes.
    if (!r.ok) fail(`Could not dispatch the auto-rebuild: ${r.detail}`);
  }

  // Email when a human is actually needed: something no rebuild can fix, or
  // the final pass still finding problems. A retryable problem on a non-final
  // pass stays silent — the repair is in flight.
  const shouldEmail = !ok && (final || needsHuman || !canRetry);

  const summary = {
    ok, today, pass: label, final,
    problems: problems.map((p) => (p.retry ? `[retryable] ${p.msg}` : p.msg)),
    notes, retried: retryDetail, emailed: shouldEmail,
    run: run?.html_url ?? null,
  };
  console.log(JSON.stringify(summary));

  if (shouldEmail) {
    const escalation = final && canRetry
      ? `\n\nThis is the FINAL pass. Auto-rebuilds were dispatched at 11:45 and ` +
        `13:15 UTC and did not resolve it — this one needs you.\n`
      : "";
    await sendAlert(
      env,
      `⚠️ WNBA stats site check failed — ${today}`,
      problems.map((p) => `• ${p.msg}`).join("\n\n") +
        escalation +
        (notes.length ? `\n\nNotes:\n${notes.map((n) => `• ${n}`).join("\n")}` : "") +
        `\n\nSite: ${SITE_URL}\nActions: https://github.com/${REPO}/actions\n`
    );
  }
  return summary;
}

// ── Email ──────────────────────────────────────────────────────────────────

async function sendAlert(env, subject, body) {
  if (!env.ALERT_EMAIL) {
    console.log("ALERT_EMAIL binding missing — cannot send alert:", subject);
    return;
  }
  const raw =
    `From: WNBA Stats Alerts <${ALERT_FROM}>\r\n` +
    `To: ${ALERT_TO}\r\n` +
    `Subject: ${subject}\r\n` +
    `Date: ${new Date().toUTCString()}\r\n` +
    `Message-ID: <${crypto.randomUUID()}@statsataglance.com>\r\n` +
    `MIME-Version: 1.0\r\n` +
    `Content-Type: text/plain; charset=utf-8\r\n` +
    `\r\n` +
    body;
  try {
    await env.ALERT_EMAIL.send(new EmailMessage(ALERT_FROM, ALERT_TO, raw));
    console.log("alert email sent:", subject);
  } catch (e) {
    console.log("alert email FAILED:", e.message);
  }
}

// ── Entry points ───────────────────────────────────────────────────────────

export default {
  async scheduled(event, env, ctx) {
    if (CHECK_CRONS.includes(event.cron)) {
      const pass = CHECK_CRONS.indexOf(event.cron) + 1;
      ctx.waitUntil(healthCheck(env, {
        final: event.cron === FINAL_CRON,
        label: `pass ${pass}/${CHECK_CRONS.length}`,
      }));
    } else {
      ctx.waitUntil(dispatch(env));
    }
  },

  // Visiting the Worker URL shows status. With ?key=YOUR_CRON_KEY:
  //   (no action)          — fire a manual build dispatch (posts to Bluesky)
  //   &post=false          — manual build that does NOT post to Bluesky
  //   &action=check        — run the health check now (emails if problems found)
  //   &action=check&repair=1 — ...and auto-dispatch a rebuild if it's fixable
  //   &action=testemail    — send a test alert email
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const key = url.searchParams.get("key");
    if (key && env.CRON_KEY && key === env.CRON_KEY) {
      const action = url.searchParams.get("action");
      // Suppress the Bluesky post when ?post is false/0/no (default: post).
      const post = !["false", "0", "no"].includes(
        (url.searchParams.get("post") || "").toLowerCase()
      );
      let r;
      if (action === "check") {
        // Manual checks REPORT, they don't repair: run as a final pass so you
        // see every problem instead of having it silently re-dispatched.
        // Add &repair=1 to let it dispatch an auto-rebuild too.
        const repair = ["1", "true", "yes"].includes(
          (url.searchParams.get("repair") || "").toLowerCase()
        );
        r = await healthCheck(env, { final: !repair, label: "manual" });
      } else if (action === "testemail") {
        await sendAlert(env, "Test — WNBA stats alerts are working",
          "This is a test alert from wnba-stats-cron. If you can read this, " +
          "failure notifications will reach you.\n");
        r = { ok: true, detail: "test email attempted — check inbox and logs" };
      } else {
        r = await dispatch(env, post);
      }
      return new Response(JSON.stringify(r, null, 2), {
        status: r.ok ? 200 : 502,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response(
      "wnba-stats-cron is alive.\n" +
      "11:17 UTC — dispatches the daily build (7:17am ET).\n" +
      "11:45 UTC — health check; auto-rebuilds on a fixable problem, else emails.\n" +
      "13:15 UTC — health check, pass 2 (same behaviour).\n" +
      "14:45 UTC — health check, FINAL pass; emails anything still broken.\n" +
      "Manual: ?key=YOUR_CRON_KEY [&post=false] [&action=check[&repair=1]|testemail]\n",
      { headers: { "content-type": "text/plain; charset=utf-8" } }
    );
  },
};
