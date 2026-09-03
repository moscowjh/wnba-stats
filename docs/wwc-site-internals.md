# WWC site internals — `sites/wwc/build_wwc_pages.py`

_Engineering documentation for the WWC 2026 programme site
(`wwc.statsataglance.com`). Companion to `DEPLOY.md` (infrastructure),
`build-internals.md` (the WNBA build) and `data-sources.md` (per-league feed
capabilities). Written 2026-08-25 at the first build._

## What this site is

A **programme site** for the FIBA Women's Basketball World Cup, Berlin,
4–13 September 2026 — 16 teams, 36 games. It is the second site in this repo
and the first consumer of `core/` that is not the WNBA build.

Its premise is the **WNBA bridge**: 56 current WNBA players are in this field,
and every one of them we publish a page for is linked back to
`wnba.statsataglance.com/players/<slug>/`. That cross-link is the retention
mechanism and half the reason the site exists — see "Cross-linking" below,
because it is built by looking rather than by guessing and that distinction is
load-bearing.

## Why it is not a fork of `build_stats_page.py`

Decided 2026-08-19; recorded here so it is not re-litigated.

Forking would have copied 1,856 lines to obtain four static page types, and
imported sortable tables, tab switching and inline box-score modals that a
programme site has no use for. **The machinery that makes that file big is
precisely the machinery these pages don't have.** The alternative —
extracting a shared component layer first — meant scoping a refactor nobody
had scoped, under a deadline, whose main beneficiary was a different feature
(WNBA team pages) with no deadline at all.

So this emitter is purpose-built and imports only the shared layer:

| Import | What it gives |
|---|---|
| `sag.render.chrome` | design tokens, site footer, scroll fade, usage beacon |
| `sag.seo` | `slugify`, canonical URLs, `sitemap.xml`, `robots.txt` |

Everything else is this site's own components. **That is the point:** a second
consumer of `core/` is what demonstrates the shared layer is league-agnostic
rather than WNBA-shaped with the names filed off. The right seams for a
component layer are learned from the second implementation, not guessed
before the first.

## Surfaces

| URL | Page | Notes |
|---|---|---|
| `/` | **Guide** | The landing page. Event brief + rules explainer. |
| `/games/` | Games | All 36 games, both time zones. |
| `/teams/` | Teams index | 16 cards, grouped, **no tiering**. |
| `/teams/<slug>/` | Team page | ×16. The main surface. |
| `/groups/` | Groups | Tables + fixtures + qualification path, merged. |
| `/leaders/` | Leaders | Always emitted; **in the nav and the sitemap only once a game is final**. |
| `/games/<game_id>/` | Box score | ×0 today; ~36 once games are played. |

**The front door is a switch, not a layout.** `GUIDE_IS_LANDING` in
`sites/wwc/config.py` swaps Guide and Games between `/` and their named path.
`GUIDE_PATH` / `GAMES_PATH` derive from it and **no other line in the emitter
writes a path literal for those two surfaces**, so flipping it cannot leave a
stale link, canonical or sitemap entry behind. Set True 2026-08-26 on the
strongest user feedback we have — *"I didn't know there was a women's
basketball world cup"* — which says a schedule is the wrong thing to open
with.

**Now permanent (2026-08-30).** The provisional note said *decide before the
site is indexed*; the sitemap was submitted 08-29 and accepted 08-30, so that
window has closed and the decision was taken rather than left to lapse. The
switch stays — it is what keeps every path derived instead of typed — but
**changing the front door from here is a redirect, not a boolean.** Treat
`GUIDE_IS_LANDING` as a settled fact the emitter reads, not an open question.

**Leaders shipped 3 September**, held back from the 31 August launch on the
reasoning that a tab leading to an empty board is worse than no tab. Counting
stats only — PPG/RPG/APG/SPG/BPG, and **no rate boards at all**: over a
three-to-eight game group stage a 5-for-8 shooter tops every percentage board
there is, which also moots qualification thresholds exactly where they were
hardest. The page states that in a line rather than leaving it as an absence.

Ranked on the per-game average, with **the total beside it and GP on every
row**, under a "through N games" heading. That is the answer to the small
sample: on 5 September the PPG leader is whoever had the single best game, and
showing the total and the games played makes that *visible* rather than hiding
it behind a minimum-games rule we would then have to defend — the same
instinct as the standings table's "Provisional" label.

The tab **gates on rankable rows** — not on the clock, not on
`results.json`, and not even on the count of final box scores. Each rejected
candidate fails on the same axis, one step closer in:

- *Results* and box scores arrive from different places and can lag each
  other (`game_cell()` already assumes this), so a results gate raises the
  tab while there is nothing to rank.
- *Final box scores* looked like the right gate and is not: FIBA can mark a
  game final before its box score carries player statistics, and a box with
  empty or all-null player lists would light the tab over five empty tables.
  So would a game whose every stat is null, which the null rule empties by
  design.

`compute_leaders()` therefore returns `populated` — did any board get a row —
alongside `games`, and `populated` is what drives the nav, the sitemap and the
`noindex`. The tab appears by itself the moment there is something on it,
which also removes the deploy-timing question: merging before the tournament
changes nothing visible.

The empty state distinguishes its two causes rather than flattening them. "No
games have been played yet" is a false statement about a tournament in
progress, so a run with final box scores but no statistics says *that*
instead. Correct-or-blank applies to prose.

The page is **emitted on every run, including empty**, so the URL never 404s
and the empty state is a rendered artifact rather than an unlooked-at branch.
While empty it carries `noindex,follow` and stays out of the sitemap: the
Phase 1 bet is a search bet and a thin empty page is the one kind Google
should not be finding. Correct-or-blank, aimed at a crawler.

### Two guards, both against silent wrong data

**There is no player id.** The box-score format's join is
`(schedule_key, name)` and nothing else, so any spelling drift between games
splits one player into two and *both halves fall off the board* — no error, no
blank, just a leaderboard missing the player who should be on it. This is the
WNBA `athlete_id` lesson (incident 2026-08-04) with nothing to fall back on.

`_name_key()` normalises **encoding only** — NFC, the three non-space spaces,
runs, case. Those are one string written two ways and merging them is safe;
U+00A0 alone broke every name join in the 2026-09-02 roster capture. It
deliberately does **not** guess at spelling: no normaliser joins Korea's
`Kang Yi-seul` to `Kang Lee-seul`, and one that tried would eventually merge
two different people, which is worse than a split because a split is
reportable and a bad merge is not. So real drift is caught by
`report_name_joins()`, which **reports and never fails** — a late replacement
must not take the site down on a match day. Read the build step's log.

**A null is not a zero.** A null in a category taints that category for that
player and drops her from *that board only*; she is not ranked on a partial
sum, and a missing steal does not cost her the scoring board. Summing nulls as
zero would reproduce, on a published leaderboard, exactly the undercount the
format's own null rule exists to prevent.

**A fixture is refused, not bannered.** A box-score page is one game and can
say on its face what it is; a leaderboard is a blend, and one synthetic game
mixed into real ones is wrong in a way no banner undoes and no reader can
decompose. So `compute_leaders()` raises on any `_fixture` box outside
`--preview`. `validate_leaders.py` gates CI on all of the above.

Fixtures appear on **both** Games and Groups on purpose. "When is my team
playing" and "who is winning this group" are different questions, and a reader
arriving at either page should not be sent to the other.

## The three lifecycle states

Every element on every page must work in all three:

```
Program   now → Sep 3      no games played, no statistics anywhere
Live      Sep 4 → Sep 13   1–8 games played, partial everything
Archive   Sep 14 on        complete
```

**The state is derived from data, never from the clock.** `data/results.json`
absent (or empty) means Program; the presence of a result for a `game_id`
moves that one game forward. Consequences worth internalising:

- The 31 August publish and a local build in December behave identically —
  neither consults a calendar, so neither can drift into the wrong state.
- The Program state is not a degraded state. It is what the site looks like
  for its **entire pre-tournament life**, which is most of the traffic it will
  ever get before Sep 4. The team-page card row is therefore *tournament
  history*, not statistics, and the group tables show a team list with a
  `Played 0` column rather than a grid of dashes.
- `game_cell()` is the single element carrying all three states: tip times →
  score + period → final + box-score link.

## Correct-or-blank

The standing rule of this codebase, and it has teeth here because the
reference data has real gaps.

> **Never render a placeholder that looks like data.**

- Round 1 printed the literal word `club` for every non-WNBA player, which
  reads like a value and tells the reader nothing. This renders `—`.
  `plays_for.club_name` is null for **every** non-WNBA player in
  `wwc2026_teams.json`, so most roster rows are honestly an em dash until
  that field is filled.
- **Mali's coach is `null` and that is deliberate.** FIBA's team profile names
  no head coach and nobody has been identified. The slot renders empty with a
  one-line explanation. Do not "fix" this.
- Box-score cells: a stat the feed did not carry renders `—`, never `0`. A
  fabricated zero is indistinguishable from a real one and silently corrupts
  every total computed off it.
- A **final score is only a link** when the box-score page is really being
  emitted in the same run. Results and box scores arrive from different places
  and can lag each other; the score is the fact, the box score is a bonus.

Same posture as the 2026-07-03 line-score fix.

## Cross-linking to WNBA player pages

`player_slug` is null for every player in the reference data, so the emitter
derives the target and then **verifies it exists**: it lists
`sites/wnba/public/players/` and links a name only when that directory is
really there. 86 links land today, across the 16 team pages and the Guide.

**They open in a new tab** — `CROSS_SITE` (`target="_blank" rel="noopener"`),
carried by all four call sites: `wnba_block()`, `roster_name()`, `box_name()`
and the Guide's `plink()`. The reason is specific and worth not undoing.

A WNBA player page is **not** a dead end — it carries a masthead `/` link and
an "← all players" link, and the Players tab on the WNBA site already links
all 231 names — so a reader who follows one of these is not stranded. What
they cannot do is get **back to the World Cup**, because the WNBA build knows
nothing about this site. The new tab is the only return path that exists, so
until the link is reciprocal it is load-bearing rather than decorative.

Confirmed on the live site 2026-08-30 (Jason): the transition does not read as
abrupt, and the browser's back control closes the new tab and lands the reader
back on the WWC page — which is the exact behaviour the decision assumed and
the reason it is not merely a session trick.

The real fix is a *"playing at the World Cup"* line on the WNBA player page,
which needs the WNBA build to read `wwc2026_teams.json` and therefore moves
WNBA bytes; it is in the backlog under the player-page work, not here.

`rel="noopener"` is stated explicitly rather than relying on the modern
browser default for `target="_blank"` — it costs nothing and this is a link
handed to readers on phones whose browser we do not choose.

Guessing from the name alone would emit 404s. Two names need an explicit
alias, and both are in `ALIASES` in the emitter:

| Reference data says | Our URL is | Why |
|---|---|---|
| Steph Talbot | `stephanie-talbot` | ESPN uses the full first name |
| Megan Gustafson | `megan-dileo` | The schema doc is right that she is *displayed* as Gustafson; our site's URL is nevertheless DiLeo because that is what ESPN calls her. **Display name and link target are different questions.** |

### `wnba.players` beats `roster.players[].wnba`

The two disagree inside the same file, and the emitter prefers the verified
block. This is not inference — the schema doc makes `wnba.players`
authoritative (the three counts derive from it and `validate_teams.py`
asserts they agree), so the denormalised roster boolean is consulted only for
names the `wnba` block has never heard of.

The disagreements as of 2026-08-25, all still worth fixing in the data:

- Six roster entries carry `wnba: false` while the `wnba` block lists five of
  them as **current** — Bibby, Borlase, Fowler (AUS), Linskens, Delaere (BEL).
- Belgium's `wnba` block records Meesseman and Vanloo as **former** while
  their roster rows say `true`. Vanloo has a live 2026 page on our own site,
  which suggests "former" is the stale side of that one.

Without this precedence a former player would be badged as currently playing
in the WNBA, on the exact surface the site's credibility rests on.

## Styling — Option C

Decided 2026-08-23 from rendered mockups in
`wbb-lab/wwc/prototypes/mock-round2/` (a separate repo — reference by path,
not as a link), built as three single-variable steps so each change stood or
fell alone.

**The shared near-black ground, diverging on exactly one token: `--accent`,
amber `#f5a623` → cyan `#35D0FF`.** That token is carried on
`LeagueConfig.accent` and rendered by `chrome.tokens_css(accent)`, which
replaced a module-level `TOKENS_CSS` constant. The other six tokens stay
literally shared, so a second site reads as the same publication with a
different accent rather than as a fork.

Typography differs too and **costs `core/` nothing**, because `font-family`
has always lived per-emitter:

- system sans for prose, nav, headings, card labels
- mono for data — and **mono is SCOPED**, to `.num`, `.big`, `.tla`, `.grp`,
  `.wn` and nothing else. Applying it to `body` is what made round 1 hard to
  read: the Key page's comparison tables are prose in a table, not data.
- `font-variant-numeric: tabular-nums`
- **prose is fluid to the 900px body.** It was capped at ~34em until
  2026-08-28, when the cap was removed at Jason's direction so prose and
  tables reflow together at every width. `body{max-width:900px}` is now the
  only bound on line length. This was a decision, not an accident — put it
  back only by deciding to, not by "fixing" it.
- adjacent prose blocks are separated by `.prose+.prose{margin-top:14px}`. The
  `*{margin:0}` reset removes the default paragraph margin, so without that
  rule consecutive paragraphs render as one undifferentiated block.
- **no webfonts.** Not Inter, not IBM Plex. Single-file, zero third-party
  origins; performance is the brand.

`--pos` / `--neg` are WWC-local (they colour the standings point-difference
column). They are not in the shared token block because no other site uses
them yet; promoting them to `chrome` when a second site wants them is the
cheap direction, inventing shared tokens for one consumer is not.

### The dormant `--bg` literal

`chrome.SCROLL_FADE_CSS` hardcodes `rgba(15,15,15,0)` in its fade gradient —
that is `--bg` written out by hand — and the literal is duplicated at
`build_stats_page.py`'s `.gm-tscroll` rule. **Option C does not move `--bg`,
so it stays correct and dormant on both sites.** The technique is deliberate
and right: fading to bare `transparent` smears grey in some browsers, because
`transparent` is transparent *black*, so you must fade from the background
colour at zero alpha. Only the hardcoded value is wrong. Any future site that
diverges on `--bg` must fix both copies first. Tracked in the backlog.

## `game_id` — it becomes a public URL

A slug that changes is a URL that 404s after it was indexed, so the scheme is
chosen for stability, and `load_schedule()` asserts all 36 are unique.

| Games | Scheme | Example |
|---|---|---|
| Group (24) | `<date>-<team1>-<team2>` | `2026-09-04-japan-mali` |
| Crossover / QF / SF (10) | FIBA's own game number | `qf-29`, `qqf-25`, `sf-33` |
| Third place, Final (2) | the phase | `third-place`, `final` |

Knockout ids **cannot** key off teams — they are `TBD` until the bracket
resolves, and a URL that changes when the draw lands is a URL that breaks.

## Box scores — separate pages, not inline

A **deliberate divergence** from the WNBA site, whose inline modals are right
for a single-file daily page and wrong for a tournament archive. Separate
pages make each game an entity with a URL, a canonical and a sitemap entry:
~36 more indexable pages off one template, on a site whose whole distribution
bet is search.

**The content is the WNBA box score exactly** (Jason, 2026-08-26) — header,
line score, Team Stats with its percentage rows, then a full table per team
split Starters / Bench, carrying `_STAT_COLS` unchanged: MIN, PTS, FG, 3PT,
FT, R, OR, A, S, B, TO, PF, +/−. Minutes are truncated to whole numbers as
they are there. A reader who knows one site should not have to relearn the
other.

Two deliberate differences:

- **Full first names.** The WNBA row renders "C. Clark" via `short_name()`.
  A tournament audience is meeting most of these players for the first time,
  and an initial helps only someone who already knows the surname. Position
  still leads the cell, as it does on the WNBA site; jersey numbers stay on
  the roster table, because at thirteen stat columns the row is already at its
  width budget on a phone.
- **WWC visual language** — national flags, cyan accent, sans/scoped-mono.

The Starters/Bench split is **data-driven**: a feed that does not mark
starters yields one undivided table rather than an invented heading over an
arbitrary five. Team totals are summed from the player rows, and **any missing
component makes the whole total `None`** rather than a quiet undercount — the
same correct-or-blank rule as the cells, one level up, where it matters more
because a total is the number a reader is most likely to quote.

### Data format

One file per game at `sites/wwc/data/boxscores/<game_id>.json`, to be written
by a future FIBA adapter. `game_id` must match `build_wwc_pages.game_id()` for
the corresponding schedule row.

```json
{
  "game_id": "qf-29",
  "status": "final",
  "teams": [
    {"schedule_key": "USA", "score": 98, "linescore": [25, 27, 22, 24],
     "players": [{"number": 22, "name": "A'ja Wilson", "min": "34:36",
                  "pts": 11, "reb": 2, "ast": 0,
                  "fgm": 4, "fga": 12, "tpm": 1, "tpa": 2,
                  "ftm": 2, "fta": 2,
                  "stl": 0, "blk": 0, "tov": 1, "pf": 1}]}
  ]
}
```

**Any stat the feed does not carry must be `null`, never `0`.** The template
renders `null` as an em dash.

### The fixture, and why `--preview` writes somewhere else

`reference/boxscore_fixture.json` is synthetic test data — a plausible
USA–Australia quarter-final — so the template and the format are exercised
now rather than first exercised on a live match day. It lives in `reference/`
by the standing rule ("if I deleted this, could a machine get it back?" No.).

It renders **only** under `--preview`, and `--preview` writes the whole site
to `sites/wwc/preview/` rather than `public/`:

- `public/` is what the workflow `git add`s and what `wrangler deploy`
  uploads, so a fixture written there would go **live on the real hostname**
  even though the sitemap omits it. Excluding it from the sitemap is not
  sufficient protection.
- `sites/*/preview/` is already gitignored, so a preview build cannot be
  committed or deployed by accident.
- **A plain run has no code path that emits the fixture at all.**

Belt and braces: a fixture page also renders a red banner saying what it is,
because a rendered HTML file can be opened, mailed or screenshotted, and
should not depend on how it was reached to be honest about itself.

```
.venv/bin/python sites/wwc/build_wwc_pages.py            # → public/,  no fixture
.venv/bin/python sites/wwc/build_wwc_pages.py --preview  # → preview/, with fixture
```

**There are two fixtures, doing different jobs.** `boxscore_fixture.json` is
one game and exercises the box-score template. `leaders_fixture.json`
(2026-09-03) is *three* fabricated finals across five teams, because one game
cannot exercise what Leaders is made of — a games-played column, a total
beside an average, or the rule that a null excludes a player from one board
and not the rest. Both carry `_fixture: true` per game, both load only under
`--preview`, both banner every page they reach, and neither can enter an
aggregate on a real run: `compute_leaders()` raises rather than banners. The
leaders fixture's numbers come from a seeded generator and are stated as such
in its own `_note`.

### Viewing the built site locally

The site needs a server — its links are root-relative (`/games/`, `/teams/`),
so opening `preview/index.html` over `file://` leaves every one of them dead.
It does NOT need anything else: no emitter change, no build flag, no Worker.
Any static server rooted at the output directory is enough, because a request
for `/games/` resolves to `games/index.html` on its own.

```bash
.venv/bin/python sites/wwc/build_wwc_pages.py --preview
(cd sites/wwc/preview && python3 -m http.server 8788 --bind 127.0.0.1)
# → http://127.0.0.1:8788/
```

Measured 2026-08-28: `/`, `/games/`, `/teams/`, `/groups/` and
`/teams/<slug>/` all return 200 this way. Serve `preview/`, not `public/` —
`public/` is what `wrangler deploy` uploads, and the reason `--preview` exists
is to keep review builds away from it.

One honest limitation. `rel=canonical` and `og:url` are absolute
(`https://wwc.statsataglance.com/…`) and stay absolute in a local build, since
they are what the *published* page must claim. They are metadata, not
navigation — nothing on the page links through them — so local browsing is
unaffected, but do not read them as evidence that you are looking at prod.

## The Guide

Renamed from "Key" 2026-08-25 (`GUIDE_TAB_LABEL`). On a prose-heavy
tournament site "Key" undersells a page that is now the primer for someone who
did not know the event existed.

Its opening brief — *What this is* — was added 2026-08-26 for that reader. It
is written to a hard constraint: brief enough for a phone without burying the
rules sections beneath it, complete enough that someone who knew nothing can
follow the tournament.

**Every number in it is computed from `wwc2026_teams.json` at build time, not
typed.** The edition number, the game count, the venue list, the USA title
count, the count of returning teams, Germany's single prior appearance — all
derived. That is not tidiness: the schema doc's falsifiability rule says a
superlative a game can break must not be stored as a string, and the USA medal
streak is exactly such a claim. `page_guide()` walks `editions` backwards to
find the run, so the sentence updates itself when 2026's result lands instead
of going quietly stale. Writing "since 1994" by hand would also have been
needlessly weak — the computed answer is 1979, twelve editions.

## The group table — FIBA Appendix D

Implemented from **Official Basketball Rules 2024, Appendix D —
Classification of Teams**, D.1.1–D.1.4. It replaced a single-source Wikipedia
summary and settled both items the planning doc marked do-not-publish.

The PDF is FIBA's document, not ours, and this repo is public, so it is not
tracked here. It lives in the lab, at `wbb-lab/fiba/FIBA-group-tiebreak.pdf`
(moved 2026-08-28 from `sites/wwc/reference/`, before it was ever committed).
Nothing in the build reads it — it is the citation behind
`validate_standings.py`, which encodes the rules directly — so its absence
cannot break a build, only an argument about what the rules say.

**The edition matters, and the first pass got it wrong.** The rules were first
implemented from screenshots of the **2026** handbook — which takes effect
1 October, *after* this tournament. Berlin is OBR 2024. The two turned out to
be substantively identical for D.1–D.2, so no behaviour changed, but the
evidence was wrong until the 2024 text was in hand. Worth noting that the
Guide page had been publishing the correct caveat ("Berlin is played under
OBR 2024") the whole time the code was built against the other edition.

**Classification points are 2 for a win and 1 for a LOSS** (0 only for a
forfeit). A 3-0 team has 6, an 0-3 team has 3, nobody has zero. A US reader
assumes 1-for-a-win or reads a `Pts` column as points scored — which is why
the column ships with a tap-to-open legend, and why that legend exists at all.

### The ladder, and why it is not a sort key

```
1. classification points
2. if level, ONLY the games among the tied teams count, re-ranked as a
   mini-table:  record -> point difference -> points scored
3. still level: point difference across all group games, then points scored
4. still level: FIBA world ranking        (we do not hold this)
5. whenever a team separates out, RESTART at step 2 for the rest
```

Step 5 is recursion, so this cannot be expressed as one sort key —
`classify()` is recursive for that reason.

**Wikipedia's four-step summary had the right order of criteria types and was
wrong in three ways that change results**, all of which the rulebook settles:

1. It collapses four criteria into two — difference/points *among the tied
   teams* comes before difference/points *across the group*.
2. "Head-to-head results" is misleading once three teams are tied. It is a
   sub-group mini-table, not a pairwise comparison.
3. D.1.4 — the restart — is absent entirely.

### Provisional vs final ordering

**Appendix D is a rule for the END of the group phase.** D.1.3 opens "If 2 or
more teams have the same win-loss record of *all games in the group*", which
presumes every game is played. Applied to a half-played group it produces
orderings that are indefensible on their face: with only one game finished
among three tied teams, a side at 1-0 and +15 sorts *below* one at 0-2,
because its sub-group record is 0-0 against the other's 0-1.

So while `group_complete()` is False the table is sorted the way a reader
expects — points, then difference, then points scored — and the page says
**Provisional**. The real ladder takes over the moment the last group game is
final. FIBA's own live tables behave the same way.

### It is a recursion, and a sort key gets it wrong

`classify()` walks D.1.3's criteria in order — sub-group record, then
difference and points *among the tied teams*, then difference and points
*across the group*. **The moment any criterion separates the set, every
surviving bucket is re-entered from the start**, so its sub-group is
recomputed from only its own members' games. That is D.1.4, and it is why
this cannot be one sort key.

The first implementation *was* a composite sort key. It passed the three
examples visible in the 2026 screenshots and **failed two of the four that
only appear in the full document**, both six-team groups:

- **Example 5.** The four-team sub-group separates {A,B} from {C,D} on record.
  C and D must then be compared on the single C-D game, which D won. Their
  four-team point differences are -5 and -45, so a sort key ranks C first.
  The rulebook ranks D first.
- **Example 6.** Same shape one level deeper: after C and A clear, B/D/E must
  be re-tallied from *their* three games, not scored on the five-team table.

Had only the three-team examples been on hand, that bug would have shipped —
and it would have been invisible, because a wrong tie-break renders a table
with right records, right totals and wrong order.

### `validate_standings.py`

Checks all **seven** worked examples (D.2.1–D.2.7) and gates CI beside
`validate_teams.py`. It compares **every column**, not just the final order,
so a right answer reached by wrong arithmetic still fails. Examples 5-7 are
six-team groups, which no real WWC group is — they are there precisely
because the deeper recursion is where the logic breaks.

## The roster grid

Three columns — **WNBA team · Other club · Note** — replacing a single
"Plays for" cell (Jason, 2026-08-26).

**Optional columns render per TEAM, only when at least one player on that team
has a value.** A column of twelve identical dashes reads as broken rather than
as honest, and `plays_for.club_name` is null for every player in the file, so
"Other club" would be exactly that on all three teams that currently have
rosters. Each column now appears as its data lands, team by team. Same
lifecycle logic the standings table uses, where W/L/PF/PA do not exist until a
game has been played. Today: USA renders two columns, Australia and Belgium
three (they have former WNBA players), and "Other club" renders nowhere.

Splitting the cell also fixed a real defect. The WNBA-team column now means
"plays there **now**", so a former player gets no badge — her history moves to
the Note column. Previously a former player could be badged as current.

**`wnba.players[].note` is NOT shipped**, and `NOTE_FIELD_IS_READER_SAFE` is
the switch. Of the 41 notes in the file, roughly a third are internal
provenance and hedging — "Not 'Megan DiLeo'", "Basketball Australia's release
still says Chicago", "still-rostered status unconfirmed" — rather than prose
for a reader. Publishing them wholesale would leak our working notes onto a
team page. Until an editorial pass splits them, only a synthesised
former/drafted status ships. It is a flag rather than a deletion because the
moment that pass happens, turning them on is one line.

Notes live on `wnba.players[]`, so a roster player with **no** WNBA connection
has nowhere to put one. Notes for those need a new field on `roster.players[]`.

## Box scores — separate pages, not inline

## Content that is deliberately NOT published

Both source documents live in the private docs repo and carry material marked
do-not-publish. The emitter ships the settled parts only.

~~From `wwc-groups-and-tiebreakers-2026-08-18.md`: how a three-way tie
resolves, and whether standings points are 2-for-a-win / 1-for-a-loss.~~
**Both closed 2026-08-26** from the rulebook itself — see "The group table"
above. That planning doc is superseded on those two rows; Appendix D is the
source.

Worth keeping as a method note. The page shipped with **less** than the
available summary offered, for four days, because that summary was
single-source and its failure mode was invisible — a wrong tie-break renders a
table that looks entirely normal. When the primary source arrived, the summary
turned out to be wrong in three ways that change results. The caution was not
merely defensible; it was correct.

From `wwc-rules-audit-2026-08-17.md`: the ranked list of claims still needing
verification is internal. The reader-facing OBR 2024 note ships; the QA
warning does not.

Round 1 and round 2 mockups also carry annotations addressed to Jason —
`DATA GAP`, `FIELD DOES NOT EXIST`, `⚠ NOT PUBLISHABLE AS-IS`,
`⚠ HUMAN QA REQUIRED`. **Those are spec notes about components, not copy.**
None of them ship.

## Known data gaps

| Gap | Scope | Effect on the page |
|---|---|---|
| `plays_for.club_name` | every non-WNBA player | roster "Plays for" renders `—` |
| "Players to watch" | no field exists | section omitted entirely rather than stubbed |
| `roster.players` detail | 13 of 16 teams | "FIBA has published an N-name pool" |
| `qualification.link` | 11 teams | all point at the same generic Wikipedia page, so "Wuhan tournament →" is not actually a Wuhan-specific link |
| `roster.players[].wnba` | 8 rows | stale; superseded at render time by `wnba.players` (above) |

The "players to watch" gap is the `hooks.json` retrofit trap the sequencing
plan already flagged: the product brief asks for 1–2 players who *aren't*
WNBA-known and there is nowhere in `wwc2026_teams.json` to put them. The
section is omitted rather than stubbed, because a heading over an apology is
worse than no heading.

## Verification

There is no test suite. What exists:

```
.venv/bin/python sites/wwc/validate_teams.py          # 16/16, gates CI
.venv/bin/python sites/wwc/validate_standings.py      # 7/7 Appendix D examples
.venv/bin/python sites/wwc/validate_leaders.py        # 15/15, gates CI
.venv/bin/python sites/wwc/build_wwc_pages.py         # must print 20 in sitemap
.venv/bin/python sites/wnba/golden_check.py check     # WNBA must not move
```

`validate_leaders.py` builds its box scores in-script — no network, no
results, no live data — which is why the Leaders tab was fully testable
before a ball was bounced. Its checks were each proved to bite by breaking
the emitter on a scratch copy and confirming the failure; an assertion nobody
has watched fail is an assertion nobody has tested.

`golden_check.py` is the whole safety net for the accent-token split: it
proves `tokens_css("#f5a623")` is byte-identical to the constant it replaced,
so no WNBA output moved. **If it ever reports a diff after a `core/` change,
the change is wrong — do not re-freeze the goldens to make it pass.**

`validate_teams.py` runs as a CI gate before the build in
`.github/workflows/wwc.yml`, on the same reasoning as `validate_hooks.py` in
the WNBA workflow: an invalid hand-edited reference file should fail loudly
in one step, not ship quietly across 20 pages.
