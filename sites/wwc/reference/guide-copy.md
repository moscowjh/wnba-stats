# Guide page — copy

Every word of the Guide page (`/`) as it currently reads, live values in
place. **Edit freely, including the numbers.** Hand it back and I translate it
into the emitter.

Notes on how the numbers work are at the bottom, out of your way.

---

## FIBA WOMEN'S BASKETBALL WORLD CUP

### Sep 4-13, 2026 | Berlin, Germany

**The FIBA Women's Basketball World Cup** is the world championship of women's
basketball and the sport's biggest event outside the Olympics, held every four
years. Berlin 2026 is the **20th edition**.

**16 national teams** play 36 games over ten days, September 4–13. The format is short and unforgiving. The teams are divided into four groups, and in the first round every team plays the other three in its group. Win your group and you skip straight to the quarter-finals; finish second or third and you play an extra knockout game to reach them.

**The United States** have won 11 of the 19 World Cups, including the past four. The other contenders are France (silver medalists at the 2024 Paris Olympics), Australia (Asia Cup champions), China (2022 World Cup runners-up) and Belgium (EuroBasket champions). All of them return experienced lineups that have played together internationally.

**56 current WNBA players are in this field**, spread across 15 of the 16 teams. The US roster is the most star-studded, with A'ja Wilson, Breanna Stewart, Caitlin Clark and Paige Bueckers among its names. France, which nearly beat the US in Paris in 2024, returns a strong team headlined by Gabby Williams. Even the host nation, **Germany**, which has qualified only once before, carries five WNBA players on its roster.

> Every team's full record is on its own page — see [Teams](/teams/).

## International Rules: Differences and Similarities

## Six differences worth knowing

| Difference                             | What changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Five fouls, not six**                | Players foul out a full personal earlier, so foul rates and minutes lost to foul trouble are not like-for-like with WNBA numbers.                                                                                                                                                                                                                                                                                                                                                      |
| **No defensive three seconds**         | A defender may park in the lane. Legal zone defense, and fewer clean rim attempts than the WNBA game trains you to expect.                                                                                                                                                                                                                                                                                                                                                             |
| **The ball may be played off the rim** | Once it touches the ring it is live. Plays that would be goaltending in the WNBA are legal here.                                                                                                                                                                                                                                                                                                                                                                                       |
| **Possession arrow**                   | After the opening tip there is not another jump ball all tournament. Held balls alternate.                                                                                                                                                                                                                                                                                                                                                                                             |
| **Timeouts**                           | Coach-only, dead-ball-only, one minute, and no mandatory TV timeouts. A trapped ballhandler has no bailout.                                                                                                                                                                                                                                                                                                                                                                            |
| **Court Size & Three-Point Line**      | FIBA's court is 3.9% smaller than the WNBA's. The three-point arc distance is identical at 6.75 m, but in the corners FIBA's line sits closer to the basket at 6.60 m versus 6.71 m in the WNBA — a difference of 11 centimeters (about 4 inches). That gap stems from FIBA's narrower court, which means the sideline cuts into the arc sooner. |

## Not differences, despite what you may read

The WNBA and FIBA play the **same 40 minutes** — four ten-minute quarters —
with the **same 24/14 second shot clock**, use the
same standard size-6 women's basketball, twelve-woman rosters and five on court.

---

---

# Appendix — for the translation step, not for editing

## How this file gets back into the site

Mark it up however suits you: rewrite sentences, cut paragraphs, reorder,
add rows, leave comments in the margin. When you hand it back I diff it
against the live page and fold the changes into `page_guide()` in
`build_wwc_pages.py`. **This file is not wired to the site** — deliberately,
so the rebuild stays an editing step rather than a publish button.

## Sixteen numbers on this page are computed, not typed

They come from `wwc2026_teams.json` at build time, so they update themselves
when the data changes. **You do not need to protect them while editing** — I
re-attach the bindings on the way back in.

What that does mean: **if you change one of these numbers, I will ask about it
rather than take it.** A different value is either a wording preference
("twelve straight" for "12 in a row" — fine, I keep the binding) or a
disagreement with the underlying data — and those need different fixes. The
check is free, so it may as well happen.

| In the copy                             | Comes from                              |
| --------------------------------------- | --------------------------------------- |
| 20th edition                            | `tournament.edition`                    |
| 16 national teams · 16 teams · these 16 | `tournament.team_count`                 |
| 36 games                                | rows in `wwc_schedule_2026.csv`         |
| 4–13 September                          | `tournament.start_date` / `end_date`    |
| Uber Arena and Max-Schmeling-Halle      | `tournament.venues`                     |
| won 11                                  | USA editions with `rank == 1`           |
| of the 19 previous editions             | `tournament.edition - 1`                |
| since 1979 — 12 in a row                | USA's run of consecutive top-3 finishes |
| 56 current WNBA players                 | sum of `wnba.current` across all teams  |
| across 15 of the                        | teams with ≥1 current WNBA player       |
| in 1998, where they finished 11th       | Germany's only prior appearance         |
| with 5 WNBA players                     | Germany's `wnba.current`                |
| 9 of these 16                           | teams also at the 2022 World Cup        |

The one worth knowing about: **"since 1979 — 12 in a row" breaks the moment
the USA finish outside the top three**, which is why it is computed rather
than written down. Computing it also caught me writing "since 1994" from
memory — the real run is twelve editions, not eight.

## Numbers on this page that are NOT computed

These are typed, and they are yours to change freely with no follow-up
question:

- **Australia's "one title, six podiums"** — stable (completed tournaments),
  but it is the obvious next candidate for a binding if you want the file
  fully derived.
- **Every number in "The headline" and both tables** — 40 minutes, 24/14,
  6.75 m, five fouls, 496 team-games, 5,970 player-games, 40÷48. These are
  rules facts and one-off measurements, not live data.

## Two soft spots I would flag to an editor

- **"the sport's biggest event outside the Olympics"** is the only sentence on
  the page with no number behind it. Defensible and standard, but it is a
  positioning claim.
- **"Not differences" has no header row** on purpose — the pattern is
  self-evident after the first row and a header costs vertical space on a
  phone. Easy to add back if you disagree.

## What is deliberately absent

The ranked list of still-unverified claims in
`wwc-rules-audit-2026-08-17.md` is internal QA, not copy. Worth knowing while
you edit: **the WNBA team-foul rule is the weakest link in it**, resting on
secondary sources because the rulebook PDF's text extraction fails partway
through Rule 12B.
