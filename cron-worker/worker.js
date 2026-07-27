// wnba-stats-cron — a Cloudflare Worker with two jobs:
//
//   1. 11:17 UTC  Dispatch the GitHub Actions "build.yml" workflow
//                 (replaces GitHub's unreliable native `schedule` trigger).
//   2. 11:45 UTC  Health check: verify the build ran, succeeded, and the
//                 live site is fresh. Emails an alert ONLY on failure —
//                 silence means everything is fine.
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
const CHECK_CRON = "45 11 * * *";
const ALERT_FROM = "alerts@statsataglance.com";
const ALERT_TO = "horowitz.jason@gmail.com";
const ESPN_SCOREBOARD =
  "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard";

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
  if (rep.error) {
    return { problem: `Stats validator crashed (Bluesky post was blocked): ${rep.error}` };
  }

  const cats = rep.categories || [];
  const fails = cats.filter((c) => c.status === "FAIL");
  if (fails.length) {
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
        `Layer-2 stats validation FAILED against stats.wnba.com` +
        (rep.leaders_ok === false ? " — the Bluesky post was blocked" : "") +
        `:\n  ${detail}`,
    };
  }

  const skips = cats.filter((c) => c.status === "SKIPPED");
  if (skips.length) {
    return { note: `validation skipped (${skips[0].checks?.[0]?.detail || "source not caught up"})` };
  }
  return { note: `validation: ${cats.length} categor${cats.length === 1 ? "y" : "ies"} pass` };
}

// Were any WNBA games played on the given date? (ESPN public scoreboard.)
// Returns true/false, or null if ESPN is unreachable (treated as "unknown").
async function gamesPlayedOn(iso) {
  try {
    const res = await fetch(`${ESPN_SCOREBOARD}?dates=${iso.replaceAll("-", "")}`, {
      headers: { "User-Agent": "wnba-stats-cron" },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return (data.events?.length ?? 0) > 0;
  } catch {
    return null;
  }
}

// ── Health check ───────────────────────────────────────────────────────────

async function healthCheck(env) {
  const today = isoDate(Date.now());
  const yesterday = yesterdayIso();
  const problems = [];
  const notes = [];

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
    problems.push(`Could not query GitHub Actions API: ${e.message}`);
  }

  if (run === null && problems.length === 0) {
    problems.push(
      `No "Daily WNBA stats build" run today (${today}). The 11:17 UTC dispatch ` +
      `likely didn't fire. Fix: trigger manually via the Actions "Run workflow" ` +
      `button, or this Worker's ?key= test URL; then check Cloudflare cron logs.`
    );
  } else if (run && run.status !== "completed") {
    problems.push(
      `Build run is still "${run.status}" 30+ minutes after dispatch: ${run.html_url}`
    );
  } else if (run && run.conclusion !== "success") {
    problems.push(`Build run finished with conclusion "${run.conclusion}": ${run.html_url}`);
  }

  // 2. Did it publish, and is the live site fresh?
  if (run && run.conclusion === "success") {
    let committedToday = false;
    try {
      committedToday = await publishedToday(env, today);
    } catch (e) {
      problems.push(`Could not query GitHub commits API: ${e.message}`);
    }

    if (committedToday) {
      // New data published — the live site should show games through yesterday.
      try {
        let through = await siteDataThrough();
        if (through !== yesterday) {
          // Cloudflare redeploy can lag a push by a couple of minutes.
          notes.push(`site shows ${through}, expected ${yesterday} — retrying in 2 min`);
          await sleep(2 * 60 * 1000);
          through = await siteDataThrough();
        }
        if (through !== yesterday) {
          problems.push(
            `Build committed today's update but the live site still shows data ` +
            `through ${through ?? "unknown"} (expected ${yesterday}). The Cloudflare ` +
            `redeploy may have failed — check the Workers & Pages deploy log.`
          );
        }
      } catch (e) {
        problems.push(`Could not fetch the live site: ${e.message}`);
      }
    } else {
      // No commit: legitimate on off-days. Verify yesterday really had no games.
      const played = await gamesPlayedOn(yesterday);
      if (played === true) {
        problems.push(
          `Build succeeded but committed nothing, yet ESPN shows WNBA games were ` +
          `played on ${yesterday}. The data fetch may have silently returned ` +
          `nothing — check the run log: ${run.html_url}`
        );
      } else {
        notes.push(
          played === false
            ? `off-day: no games on ${yesterday}, no commit expected`
            : `no commit today; ESPN unreachable so off-day not confirmed`
        );
      }
    }
  }

  // 3. Layer-2 stats validation: did today's build's validate step find drift?
  if (run && run.conclusion === "success") {
    const v = await validationDrift(today);
    if (v.problem) problems.push(v.problem);
    else if (v.note) notes.push(v.note);
  }

  const ok = problems.length === 0;
  const summary = { ok, today, problems, notes, run: run?.html_url ?? null };
  console.log(JSON.stringify(summary));

  if (!ok) {
    await sendAlert(
      env,
      `⚠️ WNBA stats site check failed — ${today}`,
      problems.map((p) => `• ${p}`).join("\n\n") +
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
    if (event.cron === CHECK_CRON) {
      ctx.waitUntil(healthCheck(env));
    } else {
      ctx.waitUntil(dispatch(env));
    }
  },

  // Visiting the Worker URL shows status. With ?key=YOUR_CRON_KEY:
  //   (no action)          — fire a manual build dispatch (posts to Bluesky)
  //   &post=false          — manual build that does NOT post to Bluesky
  //   &action=check        — run the health check now (emails if problems found)
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
        r = await healthCheck(env);
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
      "11:45 UTC — health check; emails only on failure.\n" +
      "Manual: ?key=YOUR_CRON_KEY [&post=false] [&action=check|testemail]\n",
      { headers: { "content-type": "text/plain; charset=utf-8" } }
    );
  },
};
