# WWC 2026 team reference data — schema v2.1.0

`wwc2026_teams.json` is the single source of truth. Views (spreadsheet, HTML table) are generated from it and are disposable.

One record per team, 16 records. Field set frozen before profile writing starts.

## What changed from v1.0.0

v1 was written before the round-1 team page prototype and the existing `sites/wwc/reference/` files were in view. Three things came out of that:

1. **`olympic_record` added.** The prototype renders an Olympics card beside the World Cup card. A team-history schema without it would have been retrofitted 16 times.
2. **`schedule_key` added.** `wwc_schedule_2026.csv` identifies teams by UPPERCASE full name (`PUERTO RICO`, `TÜRKIYE`), not by three-letter code. `code` stays the primary key; `schedule_key` is the join. Both are needed — a validator now asserts the join is exact.
3. **`coach.us_connection`** replaced a bare coach name. On the Mali frame the connection hook lands on the coach, not a player, so the coach record has to carry a sourced editorial summary rather than a string.

Also: `sources` became a list of objects with verbatim quotes, matching the `hooks.json` convention, and `flag` was added for the TLA/flag block.

## Top level

| Field | Notes |
|---|---|
| `_schema` | Version, conventions, validator contract. Mirrors the `hooks.json` `_schema` block. |
| `tournament` | Name, edition, host, venues, dates, and `qualification_summary` with the route breakdown. |
| `_context.czechoslovakia` | The Czechoslovakia record, kept but explicitly **not counted**. See below. |
| `_corrections` | Facts that were wrong in v1 and are now fixed, kept so they are not reintroduced. Same spirit as `hooks.json` `_rejected`. |
| `teams` | 16 records. |

## Per-team fields

### Identity

`code` (FIBA TLA, primary key) · `name` (as FIBA writes it — `Türkiye`, `Czechia`, `Korea`) · `flag` (emoji) · `schedule_key` (joins the schedule CSV) · `name_variants` (every other form you'll meet in a feed, plus nicknames) · `group`.

### `coach`

`{ name, since, us_connection: { has, summary, confidence }, confidence, verified_on, sources[], note }`

`us_connection.summary` is written to be usable as profile prose, not as a lookup value. **Six** of the sixteen coaches have a documented WNBA / NCAA / USA Basketball connection:

| Team | Coach | The link |
|---|---|---|
| USA | Kara Lawson | Tennessee; 5th pick 2003; 2005 WNBA champion; Duke head coach |
| Australia | Sandy Brondello | WNBA player; 2014 Mercury title; 2024 Liberty title |
| Germany | Olaf Lange | Liberty staff 2022-25; married to Brondello |
| Belgium | Mike Thibault | USA assistant at the 2008 and 2024 Olympics and 2006 and 2022 World Cups; 2019 Mystics title with Meesseman |
| Japan | Corey Gaines | Won the 2009 WNBA title as Mercury head coach |
| Nigeria | Rena Wakama | Born in Raleigh; NCAA at Western Carolina; Chicago Sky assistant |

Two of those are a single story: **Brondello and Lange are married and coach against each other's groups** — Australia in C, Germany in A.

**Mali's coach is `null` and that is deliberate.** An earlier version named Hamchétou Maïga-Ba on wiki sourcing. Her FIBA player profile records no coaching role and FIBA's Mali team profile names no coach at all. Nobody has been identified in her place — a genuine unknown, not a swap. Her playing record is preserved in `_context.maiga_ba`.

⚠️ **The falsifiability rule applies to `us_connection.summary`.** It is prose that ships. An earlier version called Thibault the WNBA's all-time coaching wins leader; Cheryl Reeve passed him in 2026. A superlative a game can break must not be stored in a data field any more than it may appear in a profile.

### `wwc_record` and `olympic_record`

`{ appearances_count, best_finish: { result, rank, years[] }, editions[], most_recent_year (Olympics only), confidence, verified_on, sources[], note }`

`editions` is the atom store: one `{year, rank}` per **completed** tournament. 2026 is never in it. `appearances_count` and `best_finish` derive from `editions` wherever it is non-null, and a validator asserts they agree — that check already caught a wrong USA appearance count.

Storing atoms rather than summaries is the `hooks.json` lesson one level down. It makes "first appearance since 1994", "nine of these sixteen were at the last one" and "medals in four straight Olympics but one World Cup medal ever" free rather than fresh research.

### `qualification`

`{ route, route_short, tournament, city, country, dates, placement, link, note, confidence }`

`route`: `host` | `continental_cup_champion` | `qualifying_tournament`. `route_short` is the display token (`HOST` / `CC` / `QT`) the prototype's third card needs.

The field breaks down 1 host + 4 continental champions + 11 from qualifying tournaments, allocated Istanbul 3 / San Juan 3 / Wuhan 3 / **Villeurbanne 2**. Villeurbanne sent one fewer because its six-team field held both the host and a continental champion. All five directly-qualified teams played a qualifying tournament anyway, so route and city genuinely answer different questions — which is why this is not one field.

### `wnba` — added in v2.1.0

`{ current, former, drafted_only, total_connected, roster_basis, as_of, confidence, players[], note, sources[] }`

The site's whole premise is the WNBA bridge, and the reference data could not answer "who should a WNBA fan look for on this team?" Now it can.

`players[]`: `{ name, position, status, wnba_team, wnba_team_full, note, confidence }`. `status` is `current` | `former` | `drafted_only` — the third covers players whose rights are held but who have never played, which for Spain is five people and is a story in itself.

The three counts derive from `players` and the validator asserts it.

`roster_basis` records what list the block was built from: `final_12`, `pool_N`, or **`proxy_*`**. Proxy means **no squad has been announced** and membership is inference — true of China, Germany and Nigeria. The validator forbids a proxy basis from claiming `high` confidence.

| | Current | Former | Drafted only |
|---|---|---|---|
| USA | 12 | – | – |
| France | 11 | 2 | 1 |
| Australia | 8 | 1 | – |
| Spain | 5 | 1 | 5 |
| Germany | 5 (proxy) | – | – |
| Belgium | 3 | 2 | – |
| China, Czechia, Italy | 2 | | |
| Japan, Mali, Hungary, Korea, Nigeria, Türkiye | 1 | | |
| **Puerto Rico** | **0** | 2 | – |

56 current WNBA players across the field. Puerto Rico is the only team with none.

**Name traps caught during collection**, all recorded in player `note` fields: Eliška Hamzová now plays as **Joklová** and FIBA still uses the old name; **Jihyun Park** (Sparks, current) and **Park Ji-su** (Aces, former) are unrelated players in the same Korean pool; Megan **Gustafson**, not "DiLeo"; Puerto Rico's Brianna Jones is not Atlanta's Brionna Jones. Kelsey Plum is at **Phoenix** and Angel Reese at **Atlanta** as of 2026 — printing Sparks or Sky would be wrong.

**Do not print a WNBA team for Elizabeth Balogun (Nigeria)** — the Toronto link is unconfirmed press speculation. And **Iliana Rupert is a current WNBA player who is NOT in France's pool**; she withdrew injured.

### `roster`

`{ status, player_count, as_of, source, players[] }`

`status`: `final` | `pool` | `not_announced` — currently **2 / 10 / 4**.

**Rule: `player_count > 12` means a pool, whatever the source calls it.** FIBA's own tracker labels several pools as final, including Mali at 23 and Spain at 21. Only USA and Australia have final twelves.

`players[]`: `{ number, name, position, plays_for: { type, wnba_team, club_country, club_name }, player_slug, wnba }`. `player_slug` is for linking to `wnba.statsataglance.com/players/<slug>/`. `wnba` is tri-state — `true`, `false`, `null` for unchecked — deliberately not defaulted to `false`, because a silent `false` on an unchecked player is exactly the error that survives into a published post.

### `profile` / `profile_status`

`profile_status`: `empty` | `draft` | `published`. **All 16 are currently `draft`** — 41 to 51 words, against a 55-word cap.

The validator enforces two profile rules mechanically: the word cap, and a ban on future-facing words (`will`, `upcoming`, `this week`, `hope`, `expect`). That second check caught a "viewers will know" in the Hungary draft that had gone past me.

## Conventions

- `null` = exists, not researched. **Never write a profile claim off a null.**
- `[]` = researched, genuinely none.
- `confidence`: `high` cross-validated across two independent sources · `medium` single source · `low` single source that conflicted with something else.

## Settled: the Czechoslovakia question

**FIBA does not count the Czechoslovakia era in Czechia's record.** Confirmed directly from FIBA's Czechia History tab, which shows Played 3 — 2006 (7th), 2010 (2nd, as hosts), 2014 (9th).

Czechia's record therefore starts at 1993 and the same convention is applied to their Olympic record. The Czechoslovakia record (8 appearances, six podiums, silver in 1964 and 1971) is preserved in `_context.czechoslovakia` so the question does not get reopened, and so it stays available as profile colour without contaminating the count.

## Known gaps

Filled and validated: identity, group, coach (16/16), World Cup record (16/16), Olympic record (16/16), qualification, roster status and count.

| Gap | Teams |
|---|---|
| `roster.players` detail | 13 of 16 — blocked on FIBA publishing final twelves |
| `olympic_record.editions` | 1 (Korea) |
| `player_slug` on named players | all — best filled from your own site data, not re-researched |
| `coach.name` | 1 (Mali) — genuinely unknown, not merely unresearched |
| `qualification.placement` | 16 — finishing position within the March qualifying tournament |

One value remains uncertain and is flagged in a `note` field rather than silently shipped:

1. **Mali's 2008 Olympic placing** (12th) is single-source, though that 2008 is their only Olympics is confirmed.

**Every World Cup record is now `high` confidence and complete.** All 16 teams have a full `editions` array.

**Resolved 2026-08-18, all from FIBA History tabs:** Czechia (3 appearances, post-1993 convention) · Korea (17, every edition since 1959) · Mali (2) · Hungary (5, fourth appearance is 1986 not 1990).

**The method that worked.** FIBA's per-team **History tab** — the 1ST / 2ND / 3RD / PLAYED tiles plus the year-by-year table — resolved every disputed World Cup figure in this file, and its medal tiles are a built-in checksum on its own table. It should be the first stop for any future World Cup history question, ahead of Wikipedia and well ahead of FIBA's own articles.

⚠️ **A caution that came out of resolving Korea.** FIBA's team-profile article says Korea "debuted in 1964" and calls 2026 their "17th edition in a row." FIBA's own History tab lists 1959 and totals 17 played, which makes 2026 the 18th. **FIBA's prose and FIBA's data disagree, and the prose is wrong.** Prefer the History tab. The article stat boxes remain useful, but a narrative claim in FIBA copy is not a source.

## Validator contract

`sites/wwc/validate_teams.py` should assert: 16 records · unique codes · 4 per group · `schedule_key` set matches the schedule CSV exactly · group assignment agrees with the schedule CSV · `editions` length equals `appearances_count` and implies the stored `best_finish` · no `final` roster over 12 · `roster.status` in enum.

All of those pass as of 2026-08-18.
