# Leader Qualification Rules, as Implemented

_Reference doc. Compares statsataglance's leader-board qualification rules against the two external
authorities we care about: the WNBA's official League Leaders minimums and Basketball-Reference's
WNBA policy._

_Created 2026-07-26 · sources verified against the live pages on that date (see §6)._

**Why this exists:** per the 2026-07-01 decision, our boards deliberately track **WNBA.com's**
qualification rules, not ESPN's. That means our leader *set* can legitimately differ from ESPN's
and Basketball-Reference's while being correct. This doc is the record of what each authority
actually requires, so "our board disagrees with site X" can be diagnosed instead of guessed at.

---

## 1. What we implement

From `compute_leaders()` in `build_stats_page.py` (this repo).

Two derived quantities:

- `scale = TEAM_GP / 44` — prorates each full-season minimum by how far **that player's own team**
  is into the season. `TEAM_GP` is the number of games her team has played; for a player who has
  appeared for more than one team it is her **current** team's count, the same team her combined
  season line is labeled with (the league publishes no rule for this case — see §4b).
- `min_gp = round(0.70 × TEAM_GP)` — the 70%-of-games floor, used on two boards only.

| Board | Our qualifier | At `TEAM_GP` = 29 (`scale` = 0.659) |
|---|---|---|
| Scoring (PPG) | PTS ≥ 525 × scale **or** GP ≥ min_gp | ≥ 346 pts **or** ≥ 20 GP |
| Rebounds (RPG) | TRB ≥ 250 × scale **or** GP ≥ min_gp | ≥ 165 reb **or** ≥ 20 GP |
| Assists (APG) | AST ≥ 150 × scale **or** GP ≥ min_gp | ≥ 99 ast **or** ≥ 20 GP |
| Steals (SPG) | STL ≥ 55 × scale **or** GP ≥ min_gp | ≥ 36 stl **or** ≥ 20 GP |
| Blocks (BPG) | BLK ≥ 40 × scale **or** GP ≥ min_gp | ≥ 26 blk **or** ≥ 20 GP |
| FT% | FTM ≥ 50 × scale | ≥ 33 FTM |
| 3PT% | 3PM ≥ 25 × scale | ≥ 16 3PM |
| eFG% | FGM ≥ 100 × scale | ≥ 66 FGM |
| TS% | FGM ≥ 100 × scale | ≥ 66 FGM |
| Off Reb (ORPG) | GP ≥ 0.70 × TEAM_GP | ≥ 20 GP |
| Turnovers (TPG) | GP ≥ 0.70 × TEAM_GP | ≥ 20 GP |

> **Changed 2026-07-27.** The five counting boards previously applied only the volume branch. The
> games-played branch was added to match the league's disjunction — see §4a. Verified board-neutral
> at the time of the change: all 11 boards identical before and after.

> **Changed 2026-08-04.** Both derived quantities were prorated by `max_GP` — the league-wide
> leader in games played — rather than by each player's own team. Teams are up to four games
> apart in early August, so every player on a team behind the pace faced a cutoff several
> percent too high. On 2026-08-03 this silently dropped two Connecticut players off boards the
> league had them on: **Brittney Griner** (28 blocks against our 29.1 cutoff, which should have
> been 27.3 — she was 3rd on the league's board) and **Aneesah Morrow** (178 rebounds against
> 181.8, should have been 170.5). The league's own wording is "70% of **team** games played,"
> and the volume half prorates on the same basis. Confirmed against all six externally-ranked
> boards: Blocks and Rebounds went from FAIL/WARN to exact matches, and Scoring, Assists,
> Steals and Off Reb were unchanged.
>
> The change is more inclusive for players on teams that have played fewer games, which also
> moved the recomputed percentage boards — eFG%/TS% (Okot in, Astier/Jones out) and FT% (Atkins
> and Nurse in, Ogunbowale and Leite out). **Those boards carry no independent confirmation:**
> the league's percentage feeds return HTTP 500, so the validator recomputes them from official
> totals using our own rule, and both sides move in lockstep. They match by construction, not by
> agreement.

At 29 games, 81 of 228 player-team rows clear the eFG%/TS% floor.

Every constant above (525 / 250 / 150 / 55 / 40 / 100 / 50 / 25) is taken directly from the
official WNBA table in §2, and the `/44` divisor matches the league's stated 44-game basis.

---

## 2. Official WNBA rules (stats.wnba.com)

Verbatim from **[Statistical Minimums to Qualify For WNBA League Leaders](https://stats.wnba.com/help/statminimums/)**:

| Category | Minimums |
|---|---|
| Scoring | 70% of team games played (31 in a 44-game season) **or** 525 points |
| Rebounds | 70% of team games played (31 in a 44-game season) **or** 250 rebounds |
| Offensive rebounds | 70% of team games played (31 in a 44-game season) |
| Defensive rebounds | 70% of team games played (31 in a 44-game season) |
| Field goal % | 100 field goals made |
| Free throw % | 50 free throws made |
| 3PT % | 25 three-point field goals made |
| Assists | 70% of team games played (31 in a 44-game season) **or** 150 assists |
| Steals | 70% of team games played (31 in a 44-game season) **or** 55 steals |
| Blocked shots | 70% of team games played (31 in a 44-game season) **or** 40 blocked shots |
| Minutes | 70% of team games played (31 in a 44-game season) **or** 825 minutes |
| AST/TO ratio | 50 assists |
| STL/TO ratio | 25 steals |

Rookie leaders: 50% of the standards above.

**Notes:**

- The counting-stat rules are a **disjunction** — 70% of games **or** the volume threshold. Either
  one qualifies you. This is the source of the biggest gap in our implementation (§4).
- The percentage minimums (100 FGM / 50 FTM / 25 3PM) are stated as **flat full-season** numbers
  with no proration language. Mid-season the league must be prorating something, or no one would
  qualify for FG% in May — but the page doesn't say so, and the proration we apply is our
  inference, not a documented rule.
- The page carries visible NBA boilerplate ("qualify for NBA League Leaders", "MINIMUMS FOR NBA
  ROOKIE LEADERS"), so treat it as loosely maintained. It is still the league's own published
  statement and the best authority available.
- The league publishes no minimum for **eFG%** or **TS%** — they are not official League Leaders
  categories. Nor for turnovers-per-game.

---

## 3. Basketball-Reference's WNBA policy

From **[WNBA Rate Statistic Requirements](https://www.basketball-reference.com/about/wnba_rate_stat_req.html)**.

**Per-game counting stats** — note the flat **20-game** floor, not a percentage of team games:

| Stat | Season minimum | Career minimum |
|---|---|---|
| PTS/G | 20 G **or** 400 PTS | 2000 PTS |
| TRB/G | 20 G **or** 200 TRB | 1000 TRB |
| AST/G | 20 G **or** 100 AST | 500 AST |
| STL/G | 20 G **or** 35 STL | 250 STL |
| BLK/G | 20 G **or** 35 BLK | 200 BLK |
| MP/G | 20 G **or** 500 MP | 3000 MP |

**Percentage stats:**

| Stat | Season minimum | Career minimum |
|---|---|---|
| FG% | 85 FG | 400 FG |
| FT% | 35 FT | 240 FT |
| 3P% | 20 3P | 50 3P |
| 2P% | 65 2P | 350 2P |
| **eFG%** | **85 FG** | 400 FG |
| **TS%** | **(FGA + 0.44 × FTA) ≥ 125** | ≥ 1000 |

**Rate / advanced stats:** 375 MP for PER, ORB%, DRB%, TRB%, AST%, STL%, BLK%, TOV%, USG%, DRtg,
WS/48; **125 possessions** for ORtg. Rookie leaderboards: 50% of the above.

**Two findings that bear directly on our boards:**

1. **BBRef qualifies eFG% on the FG% floor** (85 FG for both). They independently made the same
   choice we did — borrowing the field-goal-percentage minimum for a stat the league doesn't
   define. That's meaningful external validation of our eFG% rule.
2. **BBRef qualifies TS% on true-shooting attempts** — `FGA + 0.44 × FTA ≥ 125` — *not* on made
   field goals. This is exactly the gap flagged in our own review: TS% counts free throws, so an
   FGM floor gates it on the wrong quantity. BBRef's formula is the natural fix and has precedent.

---

## 4. Where we differ, and whether it matters

| # | Difference | Direction | Assessment |
|---|---|---|---|
| 1 | ~~We omit the "70% of games" branch of the official OR rule.~~ **Closed 2026-07-27** — the branch is now implemented on all five counting boards. | Now **matches** | Was the one real rule mismatch. Quantified in §4a; the analysis is retained because it explains *why* the fix was board-neutral and which board stays most sensitive. |
| 2 | **We prorate the percentage floors** (100 FGM → 66 mid-season); the official page states them flat. | We are **looser** mid-season | Almost certainly matches league practice, but undocumented. Converges to the official number by season's end. |
| 3 | **TS% uses an FGM floor**, where BBRef uses true-shooting attempts. | Varies | Structurally gates TS% on the wrong quantity. No official rule exists to violate; BBRef's `FGA + 0.44×FTA` is the better convention if we ever change it. |
| 4 | **eFG% uses the FG% floor.** | — | Matches BBRef exactly. No change needed. |
| 5 | **Off Reb uses 70% of games**, no volume alternative. | — | Matches the official rule exactly. |
| 6 | **Turnovers uses 70% of games.** | — | No authority defines this; ours is a reasonable house rule. |
| 7 | Our floors are **made-shot** based, so eFG%/TS% boards skew toward efficient interior players. | — | Inherent to the official rule, not a bug. Worth knowing before reading the board as "best shooters." |

**We do not implement** the official Minutes, Defensive Rebounds, AST/TO, or STL/TO boards.

### 4a. How much does the 70%-GP branch matter? _(analysis behind the 2026-07-27 change)_

Measured 2026-07-27 against the season through 7/22 (`max_GP` = 29). Adding the branch grows the
qualifying pool from ~15–31 players to ~125 per board, and changed **no top-10 board** — verified
across all 11 boards, before vs after, identical.

That isn't luck. A player who qualifies via games-played but fails the volume threshold has a hard
ceiling on their per-game rate. Since the volume threshold is itself prorated
(`V_full × max_GP / 44`) and the GP floor is `0.70 × max_GP`:

```
rate < V_threshold / GP  ≤  (V_full × max_GP / 44) / (0.70 × max_GP)  =  V_full / 30.8
```

The `max_GP` cancels — **the ceiling is independent of how far into the season it is.**

| Board | Ceiling for a GP-only qualifier | 10th-best (7/22) | Headroom | Exposed? |
|---|---|---|---|---|
| PPG | 525 / 30.8 = **17.05** | 18.21 | +1.17 | No |
| RPG | 250 / 30.8 = **8.12** | 8.52 | +0.41 | No |
| APG | 150 / 30.8 = **4.87** | 5.44 | +0.57 | No |
| SPG | 55 / 30.8 = **1.79** | 1.62 | −0.17 | **Yes** |
| BPG | 40 / 30.8 = **1.30** | 1.35 | +0.05 | Marginal |

**Reading this:** where the ceiling sits *below* the 10th-best rate, the missing branch can never
change the board — scoring, rebounds and assists are permanently safe. Where it sits *above*, a
qualifying player can displace someone. **Steals is the live case** (ceiling 1.79 vs a 1.62 cut
line); today's best GP-branch-only candidate is Julie Allemand at 1.50 SPG, so nothing is being
excluded right now — but nothing prevents it either, and it would appear without warning as an
unexplained set mismatch against WNBA.com.

Blocks' +0.05 headroom is within noise and should be treated as exposed too.

**Implemented 2026-07-27** in `compute_leaders()` — `q_pts = (PTS >= 525*scale) | q_gp` and the
same for TRB / AST / STL / BLK. The percentage boards were deliberately left alone: the official
rule gives them made-shot minimums with no games-played alternative.

Re-run this table if the qualification constants or the 70% figure ever change; the ceilings are
fixed by the rule, but the cut lines move with the season. **Steals is the board to watch** — it is
the one where a GP-branch qualifier can displace someone, so if our steals board ever diverges from
WNBA.com's, this is the first thing to check.

### 4b. Players who appear for more than one team _(2026-08-04)_

Not all of these are trades. Of the eight multi-team players on 2026-08-03, some were traded, Sug
Sutton was waived and picked up by Dallas, and others are developmental or hardship signings off
another club's roster. The rule below applies to all of them identically, which is why the site
uses Basketball-Reference's neutral `2TM` chip rather than any word implying a transaction type.

The league ranks such a player on her **combined** season line, labeled with her current team. We
do the same: leader boards are computed from `compute_player_season()` (one row per athlete), never
from the per-team frame. Before 2026-08-04 they used the per-team split, which published Kelsey
Plum's 12-game LAS eFG% (60.904) as though it were her season, against the league's 13-game LAS+PHX
figure (61.616).

The Players tab still shows the split — a `2TM` combined row above one indented row per team —
because "what has she done since the move" is a question the season line can't answer.

**Our team attribution is box-score-derived; the league's is roster-derived.** A player's team here
is whoever she last actually *played* for, so between a transaction and her debut we disagree with
stats.wnba.com by design. On 2026-08-04 that covered Aneesah Morrow (on Toronto's roster, still
shown as CON) and Chloe Bibby (Minnesota, still shown as CHI). This is the intended behavior — a
stats site shouldn't credit a player to a team she has no statistics for — and it self-heals on her
first appearance. It costs nothing on identity, since the crosswalk keys `athlete_id → PLAYER_ID`
and persists after the first match. But now that minimums are prorated per team, the window has a
real edge case: Morrow's rebound cutoff is 170.5 against Connecticut's 30 games and would be 164.8
against Toronto's 29. She has 178 and qualifies either way, but a player sitting *between* the two
cutoffs during that window would diverge from the league's board for a genuinely confusing reason.

**Open, and unanswerable from the published rules:** which team's game count prorates the minimum.
We use her **current** team, for consistency with how her line is labeled. The alternative — games
available to her (her old team's games before the move plus her new team's after) — is arguably
more principled but is not what any source documents. This only bites when the two teams' game
counts differ *and* she sits near a cutoff; no multi-team player is currently close to one.

### 4c. Ties _(2026-08-25)_

Qualification decides who is *on* a board; ties decide what a board can honestly say about the
players on it. Two players with the same unrounded value have no order between them, and neither
side of the morning diff pretends otherwise once you look at what it actually publishes:
stats.wnba.com repeats the RANK (`leagueleaders` returned **RANK 1 twice** on 2026-08-25, with the
next player at 3), and our own `nlargest` just falls through to the frame's sort — season points, as
it happens.

Three consequences, all handled as of 2026-08-25:

- **The boards render shared places** — 1, 1, 3 rather than 1, 2, 3 (`_competition_ranks` in
  `build_stats_page.py`; the place is carried on the board frame's index). The player-page badges
  already did this — `compute_card_ranks` uses `rank(method="min")` — so before this change Clark's
  and Thomas's own pages each said "1st in APG" while the leader card ranked one above the other.
- **The daily Bluesky post repeats the tie**, because `emit_social_payload` reads the same
  places. It also emits the top 5 **plus anyone sharing 5th place**, and `build_text` cuts only
  at a place boundary — otherwise the post would drop one half of a tie and imply the other
  half outranked her, which is the whole thing shared places exist to prevent.
- **`validate_stats.py` compares order tie-aware.** Two boards that disagree only *inside* a tie
  group disagree about nothing, so that is a PASS with the tie named in the detail. It used to be a
  FAIL, and because the leaders check gates the post, a dead tie in the day's broadcast category
  silently killed the post — which is exactly what happened on 2026-08-25, when Caitlin Clark and
  Alyssa Thomas each had 290 assists in 35 games. A tie *spanning the 5th/6th slot* is different:
  there the top-5 set itself is ambiguous, so it WARNs rather than passing quietly.

Equality is exact on the unrounded value, in all three places. Two players who merely display the
same tenth are **not** tied and keep separate places — on 2026-08-12, Thomas (8.2121) and Clark
(8.2069) both showed 8.2 and were correctly ranked 1 and 2.

---

## 5. Why our boards differ from ESPN

ESPN (and Yahoo, Fox) show a different leader *set* from ours and from WNBA.com. The observed
behavior is consistent with a games-played minimum of roughly 70% with **no volume alternative** —
so a low-games, high-rate player drops off their board while appearing on ours and the league's.

The canonical case is **Kelsey Plum** (2026: ~12 GP, 23.9 PPG), who appeared at #2 in scoring on our
board and on WNBA.com's League Leaders, and not at all on ESPN's.

⚠️ ESPN does not publish its qualification rule, so the above is **inferred from observed behavior,
not documented**. Treat it as a working explanation rather than a citable fact.

**Per-game values are identical across all these sites** — only *who qualifies* differs. If a
per-game **value** disagrees with another site, that's a data problem, not a qualification problem,
and should be investigated as such.

---

## 6. Sources

- WNBA official: [Statistical Minimums to Qualify For WNBA League Leaders](https://stats.wnba.com/help/statminimums/) — verified 2026-07-26
- WNBA official (alternate page): [Qualifications For League Leaders](https://www.wnba.com/qualifications-for-league-wnba-com-stats)
- Basketball-Reference: [WNBA Rate Statistic Requirements](https://www.basketball-reference.com/about/wnba_rate_stat_req.html) — verified 2026-07-26
- Our implementation: `compute_leaders()` in `build_stats_page.py` (this repo)
- Decision record: `statsataglance-backlog.md`, Decisions Log 2026-07-01 ("Category leaders use the
  WNBA's official League Leaders qualification")

---

## 7. Open questions

1. ~~Should we add the missing 70%-GP branch?~~ **Done 2026-07-27** (§4a). Still unverified in a
   live build — see the note in §7a.
2. **Should TS% move to a true-shooting-attempts floor** (difference #3), following BBRef?
3. **Should the Key tab document these rules** for readers? "Why isn't X on this list?" is a
   natural fan question and currently has no on-site answer.
4. **Does WNBA.com prorate the percentage floors mid-season?** Empirically testable: compare our
   FG%-adjacent boards against theirs early next season, when the gap between flat-100 and
   prorated is widest.

### 7a. Verification status of the 2026-07-27 change

The edit to `compute_leaders()` was made in Cowork, where `build_stats_page.py` **cannot be
imported** (line 97 uses a Python 3.12 f-string; the sandbox runs 3.10). The logic was therefore
verified by replicating it exactly against the live CSVs — all 11 boards identical before and
after — but the actual module has **not been executed**.

Confirm in the local Claude Code session, alongside the other pending work:

- The file imports and `build_stats_page.py` runs clean under Python 3.12.
- Leaders boards render unchanged from the current live site.
- `run_data_guards()` and `run_integrity_checks()` still pass.

The change is uncommitted and sits alongside the P0.5 abbreviation work — see
`statsataglance-docs/product-archive/LAYER2-VALIDATION-HANDOFF.md` §3a for the review gate (separate repo — a reference, not a link).
