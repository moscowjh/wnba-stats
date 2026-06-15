# WNBA Stats at a Glance

A fast, mobile-first WNBA stats site: [wnba.statsataglance.com](https://wnba.statsataglance.com)

Built for checking stats courtside on your phone. No ads, no frameworks, no app to install. One static HTML page that loads instantly.

## What's on it

- **Standings** with Pythagorean expected W/L and playoff cutoff
- **Leaders** with WNBA qualifying minimums
- **Team Efficiency** (Four Factors: offensive/defensive ratings, pace, eFG%, TOV%, ORB%, FT rate)
- **Team Totals** per game with matchup comparison
- **Player Stats** with search, team filtering, and season totals
- **Abbreviations** reference

## How it works

A GitHub Actions workflow runs every morning:

1. Fetches the latest box scores from ESPN's public API
2. Computes all stats in Python (pandas)
3. Bakes everything into a single self-contained HTML file
4. Deploys to Cloudflare Workers

No database, no server, no JavaScript frameworks. The entire site is one HTML file with inline CSS and vanilla JS.

## Data

Game data comes from ESPN's public (undocumented) WNBA API via the [sportsdataverse](https://github.com/sportsdataverse/sportsdataverse-py) Python package. Sports statistics are facts and are not copyrightable under US law (*NBA v. Motorola*, 2nd Cir. 1997).

## Design

Inspired by [plaintextsports.com](https://plaintextsports.com). Monospace font, dark background, minimal UI. Designed to be glanceable on a phone screen with sticky columns and swipe-to-scroll for detailed stats.
