# Data-source capability matrix

_Created 2026-08-05. What each league's data source can and cannot supply, against one normalized model._

**Why this exists.** The sequencing plan (`statsataglance-docs/statsataglance-sequencing-plan.md` — a separate repo, so not a link) commits to a single repo with a shared `core/` and per-league adapters. That only pays off if there is one place recording where each source *diverges* from the shared contract. Before this file, those facts were scattered across three documents and two spike reports, and were being rediscovered rather than looked up.

**The framing that matters:** a missing field is not a fact about a league, it's a **contract gap** — the model defines a field, a source can't supply it, and something downstream has to decide what to render instead. Treat every ✗ below as a design decision already made, not a surprise waiting in a future phase.

**Where this is going.** In Phase 1, when `core/` is extracted, this table should stop being prose and become config — a `LeagueConfig` declaring supported fields, so templates ask "can this league render a plus-minus card?" instead of a human remembering. Until then, this file is the source of truth. See §5.

**Related:** [`build-internals.md`](build-internals.md) (this repo — build internals) · [`wnba-leader-qualification-rules.md`](wnba-leader-qualification-rules.md) (this repo)

**In other repos** (siblings on disk, unrelated in git — references, not links):
`statsataglance-docs/WNBA-licensing-and-feasibility.md` (licensing) ·
`wbb-lab/fiba/fiba-wwc-2026-data-spike-findings.md` (FIBA parser detail) ·
`basketball-data/LBA/` (LBA gaps)

---

## 1. The sources

| League | Source | Access | Status |
|---|---|---|---|
| **WNBA** | ESPN public API, `basketball/wnba` | HTTP JSON, no auth | **Production** — daily build since June 2026 |
| **NCAAW** | ESPN public API, `basketball/womens-college-basketball` | HTTP JSON, no auth | **Verified 2026-08-05**, not built |
| **FIBA WWC** | fiba.basketball SSR pages (RSC flight JSON) | HTTP HTML parse, no auth, browser UA required | **Parser built + validated**, not built into a site |
| **LBA** | HackaStat CSV exports | Manual download (site blocked from sandboxes) | **Prototype only**, parked |

**Transport note that has bitten us twice:** ESPN's `site.api.espn.com` and `site.web.api.espn.com` mirror the same paths and *either* can fail host-wide — see `statsataglance-docs/bug-analysis-2026-08-05-espn-outage.md`. `ESPN_ORIGIN` is the switch. **Every ESPN caller must go through the adapter's origin handling; nothing else may construct an ESPN URL.** A standalone spike script hardcoded the dead host on 2026-08-05 and produced a false negative precisely because it bypassed this.

---

## 2. Player box score

Rows are the normalized model. ✓ = supplied directly · ~ = derivable · ✗ = absent.

| Field | WNBA | NCAAW | FIBA | LBA | Notes |
|---|:--:|:--:|:--:|:--:|---|
| minutes | ✓ | ✓ | ✓ | ✓ | FIBA also gives `TP` (time played, seconds) |
| points | ✓ | ✓ | ✓ | ✓ | |
| FG made / attempted | ✓ | ✓ | ✓ | ✓ | |
| 3P made / attempted | ✓ | ✓ | ✓ | ✓ | |
| FT made / attempted | ✓ | ✓ | ✓ | ✓ | |
| **2P made / attempted** | ~ | ~ | ✓ | ✓ | FG−3P everywhere; FIBA and LBA split it natively |
| rebounds (total) | ✓ | ✓ | ✓ | ✓ | |
| offensive / defensive rebounds | ✓ | ✓ | ✓ | ✓ | |
| assists · steals · blocks · turnovers · fouls | ✓ | ✓ | ✓ | ✓ | |
| **plus-minus** | ✓ | **✗** | ✓ (`PM`) | ✓ | **The one real NCAAW gap.** See §4. |
| starter flag | ✓ | ✓ | ✓ | ✗ | |
| position | ✓ | ✓ | ✓ | ✓ | |
| **efficiency / valutazione** | ✗ | ✗ | ✓ (`EFF`) | ✓ | FIBA and LBA supply it; we recompute rather than trust it |
| **points in the paint** | ✗ | ✗ | ✓ (`FGI*`) | ✗ | FIBA only |
| **USG% / PER / WS / BPM / VORP** | ✗ | ✗ | ✗ | ✓ | LBA only — HackaStat is far richer than the rest |

**NCAAW label set, verbatim from the 2026-08-05 spike:** `MIN PTS FG 3PT FT REB AST TO STL BLK OREB DREB PF`. Note the absence of `+/-`, which the WNBA feed does carry.

---

## 3. Game, schedule, and structure

| Capability | WNBA | NCAAW | FIBA | LBA | Notes |
|---|:--:|:--:|:--:|:--:|---|
| Quarter line scores | ✓ | ✓ | ✓ | ✗ | WNBA uses official ESPN linescores, never PBP-derived (2026-07-03 incident) |
| Play-by-play | ✓ | ✓ | ✓ | ✗ | NCAAW 453 events in the sampled game; FIBA 649, fully inline |
| **PBP shot x/y coordinates** | ✗ | ✗ | ✓ | ✗ | **FIBA only — shot charts are feasible there and nowhere else** |
| Substitution events (on/off) | ~ | ~ | ✓ | ✗ | FIBA `subst` events are explicit |
| Schedule / results index | ✓ | ✓ | ✓ | ~ | LBA schedule CSVs exist but use different team names; needs an alias map |
| Standings | ✓ | ✓ | ✓ | ✓ | |
| **Non-counting-game filter** | ✓ | ✓ | ✓ | n/a | `competitions[0].type`, **not** `season.type` — see §4 |
| **Conference / group id** | n/a | ✓ | ✓ | n/a | NCAAW `team.conferenceId`; FIBA group A–D |
| Counted possessions | ✗ | ✗ | ✗ | ✓ | Everyone else uses the Dean Oliver estimate |
| Venue / referees / attendance | ~ | ~ | ✓ | ✗ | |

**Game length:** all four are **40 minutes, 4×10**. Per-40 normalization is uniform across the family; NBA's 48 is the outlier we don't serve. This is why `core/metrics.py` needs no per-league branching for rate stats.

**Scale:** WNBA 13 teams / ~170 players · FIBA 16 teams / ~190 players · LBA 16 teams / 187 players (as downloaded) · **NCAAW 362 teams / ~4,700 players.** NCAAW is two orders of magnitude larger and is the only one where scope is a real question. Static pages handle it; curation does not.

---

## 4. Gaps that require a decision

### NCAAW has no plus-minus

**Impact:** the player-page layout — sequencing plan §6, in `statsataglance-docs/` — uses four context cards — PPG, RPG, TS%, +/−. NCAAW can render only three of them.

**Not a breakage.** `fetch_data.py` reads it as `_stat("+/-", "")` and `_parse_plus_minus("")` returns NaN, so the adapter degrades gracefully today without modification.

**Decide in Phase 4:** what the fourth card becomes. Candidates — usage rate (derivable), a shooting split, or minutes. **Preferred: make the card set a per-league config value rather than hardcoding a substitute**, since this is exactly the kind of divergence that recurs with every new source.

### ESPN's `season.type` mislabels exhibitions

`season.type=2` ("regular season") has been observed on the Commissioner's Cup final *and* the All-Star game. The WNBA fetcher reads `competitions[0].type` instead. **The spike confirmed NCAAW carries the same field** (returned `STD`), so the fix ports directly — but any new ESPN adapter must apply it from day one rather than rediscovering it through corrupted aggregates. See `statsataglance-docs/statsataglance-backlog.md` decisions log and the 2026-07-01 incident.

### Small-sample leader qualification

Not a data gap but a shared policy gap. FIBA is a 5–8 game tournament; the WNBA playoffs are 3–7 game series. Standard qualification minimums are meaningless at that length. **One decision, applied twice** — settle it during FIBA (Phase 2) and reuse it for the playoffs (Phase 3). Do not solve it separately in each. Existing WNBA rules: [`wnba-leader-qualification-rules.md`](wnba-leader-qualification-rules.md).

### Metrics policy across sources

FIBA and LBA supply efficiency ratings; LBA supplies counted possessions and a full advanced suite. **Policy: recompute everything with statsataglance formulas, keep the source's raw counts.** Otherwise the same stat means different things on different subdomains, which quietly destroys the one thing a multi-sport family is for — that a number reads the same everywhere. The exception is genuinely un-derivable source data (LBA's counted possessions), which should be labeled as such wherever it's shown.

---

## 5. Turning this into config (Phase 1)

When `core/` is extracted, this table should become a declaration rather than a document:

```python
LEAGUES = {
    "wnba":  LeagueConfig(game_minutes=40, periods=4, has_plus_minus=True,
                          has_shot_coords=False, cards=("ppg","rpg","ts","pm")),
    "ncaaw": LeagueConfig(game_minutes=40, periods=4, has_plus_minus=False,
                          has_shot_coords=False, cards=("ppg","rpg","ts","usg")),
    "wwc":   LeagueConfig(game_minutes=40, periods=4, has_plus_minus=True,
                          has_shot_coords=True,  cards=("ppg","rpg","ts","pm")),
}
```

The test of whether the monorepo is working: **adding a fifth league should mean writing an adapter and a config entry, and touching nothing in `render/`.** If a new source forces edits to templates, the contract is leaking and this table is where the leak gets recorded.

---

## 6. Maintenance

Update this file whenever a spike runs, an adapter is written, or a source changes shape. It is short by design — capabilities only. Parser internals belong in the per-source docs linked at the top; licensing belongs in `statsataglance-docs/WNBA-licensing-and-feasibility.md`.

**Every future spike against a third-party API must include a control fetch on a known-good endpoint before testing the unknown one**, so an upstream outage returns "inconclusive" rather than a false negative. `ncaaw_spike.py` implements the pattern; copy it.

| Date | Change |
|---|---|
| 2026-08-05 | Created. Consolidated findings from the NCAAW spike (this session), the FIBA spike (7/17–7/19), the LBA prototype review (7/11), and the WNBA production schema. |
