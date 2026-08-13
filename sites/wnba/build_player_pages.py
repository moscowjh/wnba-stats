#!/usr/bin/env python3
"""build_player_pages.py — LOCAL CHECKPOINT emitter for per-player pages.

Renders one static page per player into sites/wnba/preview/players/<slug>/
so the Block B design (wbb-lab prototype round 6, settled 2026-08-11) can be
reviewed in a browser against real season data before any production wiring.

NOT wired into build.yml; nothing under preview/ deploys or is tracked.
Deferred to the production-wiring session, per the 2026-08-13 handoff:
the sag package extraction, sitemap/robots/canonical/Schema.org, the
automated rank-assertion, and hooks.json provenance for Tier 1 sentences.

Bio fields (height, college, draft, jersey, experience) come from ESPN's
athlete endpoint via fetch_data.espn_get — the adapter with origin handling;
never construct an ESPN URL any other way. Results are cached in
data/player_bios_<season>.json (gitignored) so repeat runs fetch nothing.

Usage:
    .venv/bin/python sites/wnba/build_player_pages.py             # fetch missing bios, emit all
    .venv/bin/python sites/wnba/build_player_pages.py --no-fetch  # cached bios only
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from html import escape as esc

import pandas as pd

import build_stats_page as bsp
import fetch_data as fd
from config import WNBA

# Both become LeagueConfig fields at the sag extraction (per the settled
# design: WWC's prefix is "No.", and the cutoff anchors on the size of the
# league's honour pool, not a bare integer). Local constants until then.
JERSEY_PREFIX = "#"
RANK_BADGE_TOP_N = 20

BIOS_PATH = WNBA.data_dir / f"player_bios_{WNBA.season}.json"
OUT_DIR = WNBA.site_dir / "preview" / "players"

ESPN_ATHLETE = f"{fd.ESPN_ORIGIN}/apis/common/v3/sports/basketball/wnba/athletes"

# ── Tier 1 sentences ──────────────────────────────────────────────────────
# PLACEHOLDERS for the visual checkpoint, keyed by slug. Production keeps
# these in hooks.json with source URL + date written; none of these ships
# without that provenance pass. Rule: no claim a game could falsify.
TIER1_SENTENCES = {
    "aja-wilson": (
        "The first pick of the 2018 draft out of South Carolina, where she "
        "won the 2017 national championship."
    ),
    "caitlin-clark": (
        "The NCAA's all-time leading scorer — men's or women's — and the "
        "first pick of the 2024 draft."
    ),
    "sonia-citron": (
        "The third pick of the 2025 draft and an All-Rookie selection that "
        "year — the only player in Notre Dame history with 1,700 points, "
        "700 rebounds and 300 assists."
    ),
}

TS_DEFINITION = (
    "<b>True shooting.</b> Points scored per shot, counting three-pointers "
    "and free throws at their real value. One number for how efficiently a "
    "player scores."
)

ORDINAL_WORDS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
    11: "11th", 12: "12th",
}

POSITION_WORDS = {
    "G": "guard", "F": "forward", "C": "center",
    "G/F": "guard-forward", "F/G": "forward-guard",
    "F/C": "forward-center", "C/F": "center-forward",
}


# ── Small helpers ─────────────────────────────────────────────────────────

def slugify(name):
    """'A'ja Wilson' -> 'aja-wilson'. Stable ASCII, trade-safe (no team)."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s


def ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def ordinal_word(n):
    return ORDINAL_WORDS.get(int(n), ordinal(n))


def fmt_ts(v):
    """TS% as the card shows it: 61.2 (percent) -> '.612'."""
    if v is None or pd.isna(v):
        return "—"
    return f"{v / 100:.3f}".lstrip("0")


def parse_height(display_height):
    """ESPN's `6' 1"` -> the card's `6-1`."""
    if not display_height:
        return None
    m = re.match(r"(\d+)'\s*(\d+)", display_height)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def parse_draft(display_draft):
    """'2025: Rd 1, Pk 3 (WAS)' -> ('2025', 1, 3); None if undrafted/absent."""
    if not display_draft:
        return None
    m = re.match(r"(\d{4}): Rd (\d+), Pk (\d+)", display_draft)
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def parse_experience(display_experience):
    """'Rookie' -> 0, '2nd Season' -> 2; None when absent."""
    if not display_experience:
        return None
    if display_experience.strip().lower() == "rookie":
        return 0
    m = re.match(r"(\d+)", display_experience)
    return int(m.group(1)) if m else None


# ── Bio fetch ─────────────────────────────────────────────────────────────

def _trim_bio(ath):
    return {
        "jersey": ath.get("jersey") or ath.get("displayJersey"),
        "height": ath.get("displayHeight"),
        "position": (ath.get("position") or {}).get("abbreviation"),
        "college": (ath.get("college") or {}).get("name"),
        "draft": ath.get("displayDraft"),
        "experience": ath.get("displayExperience"),
    }


def load_or_fetch_bios(athlete_ids, fetch=True):
    """One athlete-endpoint call per player not already cached. ~170 calls on
    the first run, zero after. A hard failure caches {} so one broken id
    can't re-stall every later run; delete the entry to retry it."""
    bios = {}
    if BIOS_PATH.exists():
        bios = json.loads(BIOS_PATH.read_text())
    missing = [a for a in athlete_ids if a not in bios]
    if not fetch or not missing:
        return bios
    print(f"Fetching bios for {len(missing)} players "
          f"({len(bios)} already cached) ...")
    for i, aid in enumerate(missing, 1):
        try:
            data = fd.espn_get(f"{ESPN_ATHLETE}/{aid}")
            bios[aid] = _trim_bio(data.get("athlete", data))
        except Exception as e:  # noqa: BLE001 — cache the miss, keep going
            print(f"  WARNING: bio fetch failed for {aid}: {e}")
            bios[aid] = {}
        if i % 25 == 0 or i == len(missing):
            BIOS_PATH.write_text(json.dumps(bios, indent=1))
            print(f"  {i}/{len(missing)}")
        time.sleep(0.4)  # politeness between calls; retries live in espn_get
    return bios


# ── Ranks for the context cards ───────────────────────────────────────────

def compute_card_ranks(season):
    """Rank among QUALIFIED players for the three counting cards, using the
    same rule as compute_leaders (docs/wnba-leader-qualification-rules.md):
    team-prorated volume floor OR 70% of team games. Rank on the unrounded
    _raw column, ties share the lower ordinal. NaN = not qualified."""
    scale = season["TEAM_GP"] / 44.0
    min_gp = (0.70 * season["TEAM_GP"]).round().clip(lower=1)
    q_gp = season["GP"] >= min_gp
    quals = {
        "PPG": (season["PTS"] >= 525 * scale) | q_gp,
        "RPG": (season["TRB"] >= 250 * scale) | q_gp,
        "APG": (season["AST"] >= 150 * scale) | q_gp,
    }
    return {
        stat: season[stat + "_raw"].where(q).rank(ascending=False, method="min")
        for stat, q in quals.items()
    }


def league_ts_avg(season):
    """League TS% from league totals — every point and attempt, not a mean of
    player rates (which would weight a 10-minute player like a 33-minute one)."""
    return season["PTS"].sum() / (2 * (season["FGA"].sum()
                                       + 0.44 * season["FTA"].sum())) * 100


# ── Sentences ─────────────────────────────────────────────────────────────

def generated_sentence(bio):
    """The Tier 2 sentence, from structured fields only — never a claim a
    game could falsify. Returns None (Tier 3: omit) when the sentence would
    only repeat the identity header: height, year and jersey all render up
    there already, so a sentence carrying nothing else — the shape ESPN's
    data produces for internationals with no college/draft record — adds
    zero information. A thin sentence is worse than none."""
    if not bio:
        return None
    parts = []
    has_new_fact = False
    height = parse_height(bio.get("height"))
    posword = POSITION_WORDS.get(bio.get("position") or "", None)
    exp = parse_experience(bio.get("experience"))
    lead_bits = " ".join(p for p in (height, posword) if p)
    if lead_bits and exp is not None:
        season_word = ("rookie season" if exp == 0
                       else f"{ordinal_word(exp)} season")
        parts.append(f"{lead_bits.capitalize()}, {season_word}.")
    elif lead_bits:
        parts.append(f"{lead_bits.capitalize()}.")
    if bio.get("college"):
        parts.append(f"College: {esc(bio['college'])}.")
        has_new_fact = True
    draft = parse_draft(bio.get("draft"))
    if draft:
        has_new_fact = True
        year, rd, pk = draft
        if rd == 1:
            parts.append(f"Drafted: {year}, {ordinal_word(pk)} overall.")
        else:
            # Later rounds: ESPN's pick numbering isn't verified as overall,
            # so say only what's certain.
            parts.append(f"Drafted: {year}, round {rd}.")
    if bio.get("jersey"):
        parts.append(f"Jersey: {JERSEY_PREFIX}{esc(str(bio['jersey']))}.")
    return " ".join(parts) if has_new_fact else None


# ── Page pieces ───────────────────────────────────────────────────────────

PAGE_CSS = """
:root{--bg:#0f0f0f;--surface:#1a1a1a;--border:#2e2e2e;--text:#e8e8e8;--muted:#888;--accent:#f5a623}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:var(--bg);color:var(--text);
  font-size:13px;padding:14px 10px;max-width:480px;margin:0 auto;line-height:1.45}
a{color:var(--muted)}
.site-hd{font-size:11px;color:var(--muted);margin-bottom:12px}
.site-hd a{text-decoration:none}
.site-hd a:hover{color:var(--accent)}
.pf{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:13px}
.pf h1{color:var(--accent);font-size:15px;margin-bottom:2px;font-weight:normal}
.mu{color:var(--muted);font-size:10px}
.ac{color:var(--accent)}
.hd{display:flex;gap:11px;align-items:flex-start}
.mono{width:60px;height:60px;background:var(--surface);border:1px solid var(--border);
  flex:0 0 auto;display:flex;flex-direction:column;align-items:center;justify-content:center;line-height:1}
.mono .tm{color:var(--muted);font-size:9px}
.mono .num{color:var(--accent);font-size:23px;margin-top:4px}
.slot-ed{font-size:11px;line-height:1.65;margin-top:10px}
.slot-gen{color:var(--muted);font-size:10px;line-height:1.7;margin-top:10px}
.grid4{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-top:10px}
.card{background:var(--surface);padding:7px 8px}
.card .big{font-size:19px}
/* the sub-line reserves its height whether or not it has content — keeps the 2-up grid even */
.card .sub{font-size:10px;color:var(--muted);min-height:14px;line-height:14px}
.card .sub.badge{color:var(--accent)}
details.exp{margin-top:9px}
details.exp>summary{background:var(--surface);border:1px solid var(--border);color:var(--muted);
  font-size:10px;padding:6px 9px;cursor:pointer;list-style:none;text-align:left}
details.exp>summary::-webkit-details-marker{display:none}
details.exp>summary::before{content:"+ "}
details.exp[open]>summary::before{content:"\\2212 "}
details.exp>summary:hover{border-color:var(--accent);color:var(--accent)}
.sec{color:var(--accent);font-size:10px;letter-spacing:1px;text-transform:uppercase;
  border-bottom:1px solid var(--border);padding-bottom:4px;margin:12px 0 6px}
.pf table.s{border-collapse:collapse;width:100%;white-space:nowrap;font-size:10px}
.pf table.s th{color:var(--muted);text-align:left;padding:3px 4px;
  border-bottom:1px solid var(--border);font-weight:normal}
.pf table.s td{padding:3px 4px;border-bottom:1px solid var(--border)}
.scr{overflow-x:auto}
details.inl{display:inline-block}
details.inl>summary{list-style:none;border-bottom:1px dotted var(--muted);cursor:pointer;
  display:inline-block;padding:6px 10px 6px 0;margin:-6px 0 -6px 0}
details.inl>summary::-webkit-details-marker{display:none}
details.inl[open]>summary{color:var(--accent);border-bottom-color:var(--accent)}
details.inl .body{position:absolute;width:215px;background:#000;border:1px solid var(--accent);
  padding:8px 9px;font-size:10px;line-height:1.65;color:var(--text);z-index:20;margin-top:5px}
.site-ft{color:#5a5a5a;font-size:10px;margin-top:14px;line-height:1.8}
.site-ft a{color:#5a5a5a}
"""

# The splits expand is sticky per VISITOR, not per page: a returning reader
# who wants the dense tables open shouldn't re-click on every page.
STICKY_JS = """
var d=document.querySelector('details.exp');
if(d){try{if(localStorage.getItem('sag-expand')==='1')d.open=true}catch(e){}
d.addEventListener('toggle',function(){
  try{localStorage.setItem('sag-expand',d.open?'1':'0')}catch(e){}})}
"""


def card_html(label_html, value, sub_html="", accent=False):
    big_cls = "big ac" if accent else "big"
    return (f'<div class="card"><div class="mu">{label_html}</div>'
            f'<div class="{big_cls}">{value}</div>{sub_html}</div>')


def counting_card(stat, value, rank, accent=False):
    if pd.notna(rank) and rank <= RANK_BADGE_TOP_N:
        sub = f'<div class="sub badge">{ordinal(rank)} in WNBA</div>'
    else:
        sub = '<div class="sub"></div>'
    return card_html(stat, bsp.f1(value), sub, accent=accent)


def ts_card(ts_value, lg_avg):
    label = (f'<details class="inl" name="glossary"><summary>TS%</summary>'
             f'<span class="body">{TS_DEFINITION}</span></details>')
    sub = f'<div class="sub">WNBA avg {fmt_ts(lg_avg)}</div>'
    return card_html(label, fmt_ts(ts_value), sub)


def season_splits_table(row):
    fgp = bsp.pct(row["FGM"], row["FGA"])
    mpg = round(row["MIN"] / row["GP"], 1) if row["GP"] else "-"
    cells = [int(row["GP"]), bsp.f1(mpg), bsp.f1(fgp), bsp.f1(row["3PT%"]),
             bsp.f1(row["FT%"]), bsp.f1(row["SPG"]), bsp.f1(row["BPG"]),
             bsp.f1(row["TPG"])]
    heads = ["G", "MPG", "FG%", "3P%", "FT%", "SPG", "BPG", "TO"]
    return ('<div class="scr"><table class="s"><tr>'
            + "".join(f"<th>{h}</th>" for h in heads) + "</tr><tr>"
            + "".join(f"<td>{c}</td>" for c in cells) + "</tr></table></div>")


def build_game_meta(team_raw):
    """(game_id, team_abbr) -> (opp_abbr, 'W 89-81' result string)."""
    meta = {}
    by_game = {}
    for _, r in team_raw.iterrows():
        by_game.setdefault(r["game_id"], []).append(r)
    for gid, rows in by_game.items():
        if len(rows) != 2:
            continue
        for me, them in ((rows[0], rows[1]), (rows[1], rows[0])):
            wl = "W" if me["team_winner"] else "L"
            res = f'{wl} {int(me["team_score"])}-{int(them["team_score"])}'
            meta[(gid, me["team_abbreviation"])] = (them["team_abbreviation"], res)
    return meta


def game_log_table(games, game_meta):
    heads = ["Date", "Opp", "Res", "MIN", "PTS", "REB", "AST", "STL",
             "BLK", "TO", "FG", "3P", "FT", "+/-"]
    rows = []
    for _, g in games.iterrows():
        opp, res = game_meta.get(
            (g["game_id"], g["team_abbreviation"]), ("?", ""))
        date = pd.Timestamp(g["game_date"]).strftime("%b %-d")
        at = "@" if g["home_away"] == "away" else "vs"
        pm = g.get("plus_minus")
        pm = "-" if pd.isna(pm) else f"{int(pm):+d}"
        cells = [date, f"{at} {opp}", res, int(g["minutes"] or 0),
                 int(g["points"]), int(g["rebounds"]), int(g["assists"]),
                 int(g["steals"]), int(g["blocks"]), int(g["turnovers"]),
                 bsp.ma(g["field_goals_made"], g["field_goals_attempted"]),
                 bsp.ma(g["three_point_field_goals_made"],
                        g["three_point_field_goals_attempted"]),
                 bsp.ma(g["free_throws_made"], g["free_throws_attempted"]),
                 pm]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return ('<div class="scr"><table class="s"><tr>'
            + "".join(f"<th>{h}</th>" for h in heads) + "</tr>"
            + "".join(rows) + "</table></div>")


def render_page(row, bio, ranks, lg_ts, games, game_meta,
                team_names, data_through):
    name = row["athlete_display_name"]
    slug = slugify(name)
    abbr = row["team_abbreviation"]
    team_full = team_names.get(abbr, abbr)
    pos = row["athlete_position_abbreviation"] or ""
    bio = bio or {}

    jersey = bio.get("jersey")
    mono_num = f"{JERSEY_PREFIX}{jersey}" if jersey else (pos or "—")

    id_line1 = " · ".join(p for p in (esc(team_full), esc(pos)) if p)
    exp = parse_experience(bio.get("experience"))
    yr = None if exp is None else ("Rookie" if exp == 0 else f"Yr {exp}")
    line2_bits = [parse_height(bio.get("height")),
                  esc(bio["college"]) if bio.get("college") else None, yr]
    id_line2 = " · ".join(b for b in line2_bits if b)

    editorial = TIER1_SENTENCES.get(slug)
    generated = generated_sentence(bio)

    cards = "".join([
        counting_card("PPG", row["PPG"], ranks["PPG"], accent=True),
        counting_card("RPG", row["RPG"], ranks["RPG"]),
        counting_card("APG", row["APG"], ranks["APG"]),
        ts_card(row["TS%_raw"], lg_ts),
    ])

    parts = [f'<div class="hd">'
             f'<div class="mono"><div class="tm">{esc(abbr)}</div>'
             f'<div class="num">{esc(mono_num)}</div></div>'
             f'<div style="min-width:0"><h1>{esc(name)}</h1>'
             f'<div class="mu">{id_line1}</div>'
             + (f'<div class="mu" style="margin-top:3px">{id_line2}</div>'
                if id_line2 else "")
             + "</div></div>"]
    if editorial:
        parts.append(f'<p class="slot-ed">{esc(editorial)}</p>')
    parts.append(f'<div class="grid4">{cards}</div>')
    if generated:
        parts.append(f'<p class="slot-gen">{generated}</p>')
    parts.append(
        '<details class="exp"><summary>full splits &amp; game log</summary>'
        '<div class="sec">Full season</div>' + season_splits_table(row)
        + '<div class="sec">Game log</div>'
        + game_log_table(games, game_meta) + "</details>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(name)} — WNBA stats at a glance</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="site-hd"><a href="../index.html">← all players</a> · <a href="https://wnba.statsataglance.com">wnba.statsataglance.com</a></div>
<div class="pf">{"".join(parts)}</div>
<div class="site-ft">Stats through games of {data_through} ·
<a href="https://wnba.statsataglance.com">WNBA stats at a glance</a></div>
<script>{STICKY_JS}</script>
</body>
</html>
"""


def render_index(entries, data_through):
    def quick_links(label, subset):
        if not subset:
            return ""
        links = " · ".join(
            f'<a href="{e["slug"]}/index.html">{esc(e["name"])}</a>'
            for e in subset)
        return (f'<div class="site-hd" style="margin-bottom:6px">'
                f'{label}: {links}</div>')

    tier1 = [e for e in entries if e["tier"] == "1"]
    tier3 = [e for e in entries if e["tier"].startswith("3")]
    review_lines = (quick_links("Tier 1 (editorial sentence)", tier1)
                    + quick_links("Tier 3 (no sentence)", tier3[:8]))
    rows = "".join(
        f'<tr><td><a href="{e["slug"]}/index.html">{esc(e["name"])}</a></td>'
        f'<td>{esc(e["team"])}</td><td>{e["gp"]}</td><td>{e["ppg"]}</td>'
        f'<td>{e["badges"] or ""}</td><td>{e["tier"]}</td></tr>'
        for e in entries)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Player pages — local preview</title>
<style>{PAGE_CSS}
table{{border-collapse:collapse;width:100%;font-size:11px}}
th{{color:var(--muted);text-align:left;padding:4px 6px;border-bottom:1px solid var(--border);font-weight:normal}}
td{{padding:4px 6px;border-bottom:1px solid var(--border)}}
</style>
</head>
<body style="max-width:640px">
<div class="site-hd">LOCAL PREVIEW — not deployed · stats through {data_through}</div>
{review_lines}<table><tr><th>Player</th><th>Team</th><th>GP</th><th>PPG</th><th>Badges</th><th>Sentence</th></tr>{rows}</table>
</body>
</html>
"""


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true",
                    help="use cached bios only; missing bios render Tier 3")
    args = ap.parse_args()

    player_raw, team_raw = bsp.load_data()
    season = bsp.compute_player_season(player_raw)
    season = season.reset_index(drop=True)

    data_through = pd.Timestamp(player_raw["game_date"].max()).strftime(
        "%B %-d, %Y")

    bios = load_or_fetch_bios(list(season["athlete_id"]), fetch=not args.no_fetch)
    ranks = compute_card_ranks(season)
    lg_ts = league_ts_avg(season)
    game_meta = build_game_meta(team_raw)
    team_names = (team_raw.drop_duplicates("team_abbreviation")
                  .set_index("team_abbreviation")["team_display_name"]
                  .to_dict())

    # Slug collisions would silently overwrite a page — fail loudly instead.
    slugs = season["athlete_display_name"].map(slugify)
    dupes = slugs[slugs.duplicated()].tolist()
    assert not dupes, f"slug collision: {dupes}"

    pr = player_raw.copy()
    pr["athlete_id"] = pr["athlete_id"].astype(str)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    n_badged = 0
    for i, row in season.iterrows():
        slug = slugify(row["athlete_display_name"])
        bio = bios.get(row["athlete_id"], {})
        row_ranks = {s: ranks[s].loc[i] for s in ranks}
        games = pr[pr["athlete_id"] == row["athlete_id"]].sort_values(
            "game_date", ascending=False)
        html = render_page(row, bio, row_ranks, lg_ts, games, game_meta,
                           team_names, data_through)
        page_dir = OUT_DIR / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(html)

        badges = sum(1 for s in row_ranks
                     if pd.notna(row_ranks[s]) and row_ranks[s] <= RANK_BADGE_TOP_N)
        n_badged += bool(badges)
        tier = ("1" if slug in TIER1_SENTENCES
                else ("2" if generated_sentence(bio) else "3 (none)"))
        entries.append({"slug": slug, "name": row["athlete_display_name"],
                        "team": row["team_abbreviation"], "gp": int(row["GP"]),
                        "ppg": bsp.f1(row["PPG"]), "badges": badges,
                        "tier": tier})

    (OUT_DIR / "index.html").write_text(render_index(entries, data_through))

    n = len(entries)
    t3 = sum(1 for e in entries if e["tier"].startswith("3"))
    print(f"Wrote {n} player pages + index to {OUT_DIR}")
    print(f"  badges: {n_badged}/{n} pages carry at least one "
          f"({n_badged / n:.0%})")
    print(f"  tiers: {len(TIER1_SENTENCES)} hand-written · "
          f"{n - t3 - len(TIER1_SENTENCES)} generated · {t3} omitted")
    print(f"  league TS% avg: {fmt_ts(lg_ts)}")
    print(f"Open: {OUT_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
