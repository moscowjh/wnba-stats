# sites/wwc/reference/ — hand-maintained source data

Per the repo `.gitignore`: **anything here is SOURCE, not a build artifact.** It was typed
or transcribed by a human, there is no upstream that will regenerate it, and it is lost
forever if it isn't tracked. The rule is provenance, not extension.

Convention: `wnba_coaches_2026.csv` carries provenance **per row** (a `source_url` column)
because each row has a different source. Files here whose rows all come from one document
carry provenance **per file**, in this README instead — one source URL repeated 36 times is
noise, not provenance.

---

## `wwc_schedule_2026.csv` — all 36 games

**Source:** FIBA's official *Game Schedule* PDF for the FIBA Women's Basketball World Cup 2026
(Berlin, 4–13 September 2026), as published at
`https://assets.fiba.basketball/image/upload/fiba-womens-basketball-world-cup-208875-game-schedule.pdf`
**Transcribed:** 2026-08-18. **Cross-checked against:**
`https://www.fiba.basketball/en/events/fiba-womens-basketball-world-cup-2026/competition-system`

**Verified, two independent ways:** 36 games = 24 group + 4 qualification-to-QF + 4 QF +
2 SF + 3rd place + Final; and all four groups are complete round-robins (4 teams, 3 games
each, 6 unique pairings). The build script that produced this file asserts both.

**⚠️ The official PDF numbers games only up to 34** — the 3rd place game and the Final carry
**no game number at all**. `game_no` is therefore empty for them, and for every group game
(the PDF doesn't number those either). **Never infer completeness from a max game number:**
doing so silently drops both medal games, on the two highest-traffic days of the tournament.

### Columns

| Column | Notes |
|---|---|
| `game_no` | Official number where the PDF gives one (25–34). Empty for group games and both medal games. |
| `date` | ISO. Note **Fri 11 Sep is dark** — a rest day between QF and SF. |
| `phase` | `group` · `qualification_to_qf` · `quarter_final` · `semi_final` · `third_place` · `final` |
| `tip_cest` / `tip_gmt` / `tip_et` | Venue local is CEST (UTC+2); ET is EDT (UTC−4) in September, i.e. CEST − 6h. |
| `time_tba` | `TRUE` for the 10 games whose tip time FIBA announces only after the previous round. `tip_cest` then holds the two candidate slots and `tip_gmt`/`tip_et` are empty. **A TBA time must never render as a blank that reads like a real one** — same failure as `"No games today"` on 2026-08-05. |
| `venue_slot` | `1` / `2` — two arenas run in parallel on the 8-game days. **Which slot is Berlin Arena vs Max-Schmeling-Halle is NOT recorded**, because the PDF's layout does not state it. Blank rather than guessed. |
| `matchup_rule` | Advancement rule for knockout games (`2nd A - 3rd B`, `W29 - W32`, …). Team names are `TBD` until the group phase resolves. |

### Known assumption, flagged not hidden

The PDF names the quarter-finals **QF1–QF4 by tip time** and **Games 29–32 by pairing**, but
never links the two. This file assumes they map in time order (QF1 = Game 29 … QF4 = Game 32).
Marked inline in `matchup_rule`. Confirm before the bracket ships.
