# Observed robots.txt snapshots — `wnba.statsataglance.com`

Point-in-time captures of what the **live host actually serves** at `/robots.txt`.

## Why this exists

`robots.txt` on this host is Cloudflare-side state with **no representation in
this repo** — same category as the Cloudflare Redirect Rules recorded in
`DEPLOY.md`, where the doc line is the only record of a dashboard setting. Once
Cloudflare changes its managed output, or once we add an origin file, the
previous state is **unrecoverable**. These snapshots are the only before-picture.

## Convention

- **One file per capture, named `YYYY-MM-DD.txt`.**
- **Byte-exact.** Captured by redirecting `curl` output to the file — never
  hand-transcribed, never reformatted, never trimmed. The entire value of these
  files is that `diff` between two of them is meaningful.
- Capture command:

  ```
  curl -s https://wnba.statsataglance.com/robots.txt > sites/wnba/reference/robots-txt-observed/$(date -u +%F).txt
  ```

- Re-capture **after** any change to the origin file, and opportunistically every
  few months — Cloudflare has been revising its managed output as the Content
  Signals standard evolves, and we want to notice when it moves under us.
- Interpretation and decisions go in `DEPLOY.md`, not in here. These files stay
  raw.

## What the 2026-08-16 capture showed

HTTP 200, and the body is **entirely comment lines** — the Content Signals Policy
preamble defining `search`, `ai-input` and `ai-train`, plus the EU Directive
2019/790 Article 4 reservation. There is **no `Content-Signal:` directive, no
`User-agent:` line, and no `Sitemap:` line.**

Per the policy's own clause (c), declaring no signal means the operator "neither
grants nor restricts permission via content signal." So as of this date the site
has **taken no position** on search, AI input, or AI training — it is not, as was
assumed in planning, publishing `ai-train=no`.

That matters for two reasons:

1. Any argument to "preserve the site's current published stance" is arguing to
   preserve *the absence of a stance*.
2. Cloudflare's published defaults are **not** what this zone serves. Check the
   host, not the docs.
