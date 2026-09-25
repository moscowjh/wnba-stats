"""
build_stats_page.py
Builds sites/wnba/wnba-2026-stats-explorer.html from wehoop box score CSVs.
All stats -- including four factors -- are computed from the CSVs.

WHAT THIS WRITES IS A BUILD ARTIFACT, NOT THE PAGE.
`{slug}-{season}-stats-explorer.html` is gitignored (.gitignore: sites/*/*-stats-explorer.html).
CI copies it to sites/wnba/public/index.html (build.yml) and commits *that* -- so
public/index.html is the tracked, deployed page. To read today's live site locally,
open sites/wnba/public/index.html. A local *-stats-explorer.html is only as fresh as
your last local run and will silently render stale data as if it were current.

This script deliberately does NOT write public/index.html: if it did, every local
build would dirty a tracked file and race the daily CI commit.

Usage -- runs from any working directory (Python puts this script's own directory on
sys.path, which is how `from config import WNBA` resolves). Requires `pip install -e core/`:

    python3 sites/wnba/fetch_data.py        # ALWAYS first: sites/*/data/ is gitignored,
                                            # never syncs, and a build alone will happily
                                            # render whatever CSVs you last fetched
    python3 sites/wnba/build_stats_page.py
"""

import json
import os
import re
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from pathlib import Path
from html import escape as esc
from zoneinfo import ZoneInfo

from sag import seo
from sag.render import chrome

from config import WNBA, SEEDS, subpage_tabs

OUTPUT = WNBA.page_output

PYTH_EXP = 13.91  # Pythagorean exponent for basketball
PLAYOFF_SPOTS = 8  # teams that make the WNBA playoffs
ET = ZoneInfo("America/New_York")


_SAG_TODAY_ANNOUNCED = False


def today_et():
    """ET calendar date of 'today'. SAG_TODAY (YYYY-MM-DD) overrides it so the
    golden harness can re-render a pinned snapshot byte-for-byte on any later
    day — the Games tab's today/yesterday split and the social factoid's
    'last night' gate both key off this date."""
    ov = os.environ.get('SAG_TODAY')
    if ov:
        # Say so, once, loudly (2026-09-15). A pinned date silently changes
        # date-gated output — on this build it resurrected the World Cup
        # banner, which is removed by `today > WWC_LAST_DAY` and nothing else,
        # and the preview then looked like a regression that production never
        # had. The harness needs this override; a human previewing the site
        # almost never does, and had no way to tell it was in effect.
        # Printing cannot move any byte of the page.
        global _SAG_TODAY_ANNOUNCED
        if not _SAG_TODAY_ANNOUNCED:
            print(f"  NOTE: SAG_TODAY={ov} — building against a PINNED date, "
                  f"not today. Date-gated output (the Games tab's "
                  f"today/yesterday split, any dated banner) reflects {ov}.")
            _SAG_TODAY_ANNOUNCED = True
        return datetime.strptime(ov, '%Y-%m-%d').date()
    return datetime.now(ET).date()


# ── Formatting helpers ────────────────────────────────────────────────────

def ma(made, att):
    """Format as 'M/A' (made/attempted), rounded to integers."""
    return f"{int(round(made))}/{int(round(att))}"


def fmt_winpct(v):
    """Format win pct as .XXX -- sports convention, no leading zero."""
    if v >= 1.0:
        return '1.000'
    return f"{v:.3f}".lstrip('0')


def pct(num, den):
    """Safe percentage: returns float or '-'."""
    return round(num / den * 100, 1) if den else '-'


def f1(v):
    """Format a float to one decimal, or '-' for None/NaN."""
    if v is None or isinstance(v, str):
        return v if isinstance(v, str) else '-'
    if isinstance(v, float) and np.isnan(v):
        return '-'
    return f'{v:.1f}'


def short_name(full):
    """'Caitlin Clark' -> 'C. Clark'."""
    parts = full.split(' ', 1)
    if len(parts) == 2 and parts[0]:
        return f'{parts[0][0]}. {parts[1]}'
    return full


# ── HTML table helpers ────────────────────────────────────────────────────

def df_to_html(df, table_id, lg_avg=False, data_col=None, data_attr='data-team'):
    """Generic dataframe -> HTML table with sortable headers and optional
    data attributes on rows."""
    rows = ''
    for i, (_, row) in enumerate(df.iterrows()):
        is_lg = lg_avg and i == len(df) - 1
        cls = ' class="lg-avg"' if is_lg else ''
        attr = ''
        if data_col and not is_lg:
            attr = f' {data_attr}="{esc(str(row[data_col]))}"'
        # The Team cell links to that team's page; every other cell is plain.
        # Done on the DISPLAY value only — `row[data_col]` still holds the bare
        # name, which the matchup-filter JS reads via data-team.
        cells = ''.join(
            f'<td>{team_href(v)}</td>' if c == 'Team' else f'<td>{v}</td>'
            for c, v in zip(df.columns, row))
        rows += f'<tr{cls}{attr}>{cells}</tr>\n'
    headers = ''.join(
        f'<th onclick="sortTable(\'{table_id}\',{i})">{h}</th>'
        for i, h in enumerate(df.columns)
    )
    return (
        f'<div class="table-scroll"><div class="table-wrap">'
        f'<table id="{table_id}">'
        f'<thead><tr>{headers}</tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'</table></div></div>'
    )


def ff_to_html(df, table_id):
    """Four-factors table with grouped column headers and data-team
    attribute on each row."""
    rows = ''
    for _, row in df.iterrows():
        is_avg = row['Team'] == 'League Average'
        cls = ' class="lg-avg"' if is_avg else ''
        attr = '' if is_avg else f' data-team="{esc(str(row["Team"]))}"'
        cells = ''.join(
            (f'<td>{team_href(v)}</td>' if c == 'Team'
             else f'<td>{v if pd.notna(v) else "\u2014"}</td>')
            for c, v in zip(df.columns, row))
        rows += f'<tr{cls}{attr}>{cells}</tr>\n'
    return f'''
    <div class="table-scroll"><div class="table-wrap">
      <table id="{table_id}">
        <thead>
          <tr>
            <th rowspan="2" onclick="sortTable('{table_id}',0)">Team</th>
            <th rowspan="2" onclick="sortTable('{table_id}',1)">ORtg</th>
            <th rowspan="2" onclick="sortTable('{table_id}',2)">DRtg</th>
            <th rowspan="2" onclick="sortTable('{table_id}',3)">NRtg</th>
            <th rowspan="2" onclick="sortTable('{table_id}',4)">Pace</th>
            <th colspan="4" class="group-header">Offensive 4 Factors</th>
            <th colspan="4" class="group-header">Defensive 4 Factors</th>
          </tr>
          <tr>
            <th onclick="sortTable('{table_id}',5)">eFG%</th>
            <th onclick="sortTable('{table_id}',6)">TOV%</th>
            <th onclick="sortTable('{table_id}',7)">ORB%</th>
            <th onclick="sortTable('{table_id}',8)">FT/FGA</th>
            <th onclick="sortTable('{table_id}',9)">eFG%</th>
            <th onclick="sortTable('{table_id}',10)">TOV%</th>
            <th onclick="sortTable('{table_id}',11)">DRB%</th>
            <th onclick="sortTable('{table_id}',12)">FT/FGA</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div></div>'''


# ── Data computation ──────────────────────────────────────────────────────

def _read_boxes():
    """Read the box-score CSVs and drop did-not-play rows. EVERY season type.

    The shared base for both loaders below. Splitting it this way keeps the
    read-and-clean step in one place, so the ONLY difference between the
    aggregation frame and the presentation frame is the season filter."""
    player = pd.read_csv(WNBA.player_box)
    team   = pd.read_csv(WNBA.team_box)
    player = player[player['did_not_play'] != True].copy()
    return player, team.copy()


def load_data():
    """Regular-season box scores.  Returns (player_raw, team_raw).

    Filters to regular-season games (season_type == 2) so every downstream
    aggregation, guard, and the Bluesky factoid operate on the same
    regular-season-only frame. This is the right frame for anything that
    *totals* or *ranks* — leaders, standings, four factors, per-game averages:
    WNBA season statistics are regular-season statistics, and folding playoff
    games into them would be wrong, not merely different. Guarded by a column
    check so a pre-migration CSV (no season_type) still builds, treating all
    rows as regular season.

    Anything that *displays a specific game* wants load_all_games() instead —
    see the note there. `build_player_pages.py` shares this loader, so a player
    page's splits and game log are likewise regular-season-only."""
    player_raw, team_raw = _read_boxes()
    if 'season_type' in player_raw.columns:
        player_raw = player_raw[player_raw['season_type'] == 2].copy()
    if 'season_type' in team_raw.columns:
        team_raw = team_raw[team_raw['season_type'] == 2].copy()
    return player_raw, team_raw


def load_all_games():
    """Every completed game, regular season AND postseason.  Returns
    (player_all, team_all) — the frame the Games tab renders from.

    Why this exists, and why it is a SEPARATE function rather than a flag:
    the Games tab is a presentation surface, not an aggregation. It shows
    "yesterday's finals" by selecting rows for one date, so a season filter
    upstream of it does not shrink an average — it makes whole games vanish.
    Before this split, load_data()'s season_type == 2 filter reached the Games
    tab, and the first day of the playoffs would have rendered the day's
    matchups (which come from the separately-fetched, unfiltered
    schedule_today.json) above EMPTY box scores. The data was always correct;
    fetch_data.py tags season_type 2/3 and keeps both.

    Keep the two frames distinct. A future playoff surface that ranks or totals
    postseason play should filter to season_type == 3 explicitly rather than
    reaching for this one."""
    return _read_boxes()


# Core counting stats. A genuine did-not-play row has all of these blank;
# a played game always records at least zeros.
_CORE_STATS = ['minutes', 'points', 'rebounds', 'assists',
               'steals', 'blocks', 'turnovers']


def run_data_guards(player_raw, p_base):
    """Fail the build loudly if the box-score data drifts in a way that would
    silently corrupt per-game averages. Cheap insurance: these have no effect
    in the normal case and only fire when something upstream has broken.

    Guards against two real failure modes:
      1. The did-not-play filter in load_data() silently stops working (e.g. an
         upstream dtype change makes `!= True` a no-op), letting DNP rows inflate
         games-played counts and deflate every per-game average.
      2. Duplicate box scores double-count a player's games.
    """
    # 1. DNP leakage. We test the *symptom* — a row that contributes to GP but
    #    nothing to the stat sums (all core stats blank) — rather than the
    #    did_not_play flag itself, so the check still holds if that flag's dtype
    #    or meaning changes upstream. (A played game records at least zeros, so
    #    legitimate rows with an odd blank field like minutes won't trip this.)
    dnp_leak = int(player_raw[_CORE_STATS].isna().all(axis=1).sum())
    assert dnp_leak == 0, (
        f"{dnp_leak} player row(s) have every core stat blank — did-not-play "
        f"rows are leaking past the load_data() filter and will deflate "
        f"per-game averages. Check the `did_not_play` column upstream."
    )

    # 2. Games-played sanity. A (player, team) row can't show more games than
    #    that team has played. Counting games per team_abbreviation keeps this
    #    correct for players who change teams mid-season: each appears as a separate
    #    per-team row, and each row is bounded by its own team's game count.
    team_games = player_raw.groupby('team_abbreviation')['game_id'].nunique()
    chk = p_base.assign(team_gp=p_base['team_abbreviation'].map(team_games))
    bad = chk[chk['GP'] > chk['team_gp']]
    assert bad.empty, (
        "Player(s) credited with more games than their team has played — "
        "duplicate box scores or DNP leakage likely:\n"
        + bad[['athlete_display_name', 'team_abbreviation', 'GP', 'team_gp']]
            .to_string(index=False)
    )


def run_integrity_checks(team_raw, player_raw, ff_df):
    """Layer-1 build-time invariants for the team + league numbers.

    These are deterministic identities that hold for ANY correct box score, so
    they fire only on real upstream breakage (bad parse, dropped/renamed
    column, duplicated or double-counted game, asymmetric possession math) and
    never in the normal case. Any failure aborts the build so a corrupt day is
    never deployed. Complements run_data_guards() (which protects per-game
    averages) by covering standings, totals, and efficiency.
    """
    tb = team_raw

    # a) Every game has exactly two team rows (no dupes, no orphans).
    counts = tb.groupby('game_id').size()
    bad = counts[counts != 2]
    assert bad.empty, (
        f"{len(bad)} game(s) don't have exactly 2 team rows "
        f"(duplicate or missing box score): {list(bad.index)[:5]}"
    )

    # b) Scoring identity: 2*(FGM-3PM) + 3*3PM + FTM == team_score.
    calc = (2 * (tb['field_goals_made'] - tb['three_point_field_goals_made'])
            + 3 * tb['three_point_field_goals_made'] + tb['free_throws_made'])
    mism = tb[calc != tb['team_score']]
    assert mism.empty, (
        f"Team scoring identity failed for {len(mism)} team-game(s) — "
        "FGM/3PM/FTM do not reconstruct team_score:\n"
        + mism[['game_id', 'team_abbreviation', 'team_score']].head().to_string(index=False)
    )

    # c) Rebound identity: ORB + DRB == total_rebounds.
    rb = tb[(tb['offensive_rebounds'] + tb['defensive_rebounds']) != tb['total_rebounds']]
    assert rb.empty, f"ORB + DRB != total_rebounds for {len(rb)} team-game(s)."

    # d) Team box reconciles with the sum of its player box. FGA/FTA/ORB/points
    #    are pure player aggregates and must match exactly. (Turnovers are
    #    deliberately excluded: ESPN's team total includes team turnovers and
    #    occasionally disagrees with the player sum by a few either way — real
    #    source noise, not corruption, so asserting on it would false-alarm.)
    pg = player_raw.groupby(['game_id', 'team_id']).agg(
        p_fga=('field_goals_attempted', 'sum'),
        p_fta=('free_throws_attempted', 'sum'),
        p_orb=('offensive_rebounds', 'sum'),
        p_pts=('points', 'sum'),
    ).reset_index()
    j = tb.merge(pg, on=['game_id', 'team_id'], how='left')
    for tcol, pcol, label in [('field_goals_attempted', 'p_fga', 'FGA'),
                              ('free_throws_attempted', 'p_fta', 'FTA'),
                              ('offensive_rebounds', 'p_orb', 'ORB'),
                              ('team_score', 'p_pts', 'PTS')]:
        d = j[j[tcol] != j[pcol]]
        assert d.empty, (
            f"Team {label} != sum of player {label} for {len(d)} team-game(s) "
            "— player and team box are out of sync:\n"
            + d[['game_id', 'team_abbreviation', tcol, pcol]].head().to_string(index=False)
        )

    # e) League ORtg must equal league DRtg: every point scored is a point
    #    allowed, so with a shared possession denominator they are identical.
    #    Divergence means the possession math is being applied asymmetrically
    #    (exactly the failure mode behind the June possession-formula bug).
    lg = ff_df[ff_df['Team'] == 'League Average']
    if not lg.empty:
        o, dv = float(lg['ORtg'].iloc[0]), float(lg['DRtg'].iloc[0])
        assert abs(o - dv) <= 0.15, (
            f"League ORtg ({o}) != DRtg ({dv}) — points scored and allowed must "
            "net to zero league-wide; the possession denominator is asymmetric."
        )

    # f) Gross-sanity bands: catch a rating/pace formula that breaks outright
    #    (wrong column, unit error) rather than one that drifts subtly — subtle
    #    drift is Layer 2's job (external reconciliation vs stats.wnba.com).
    for _, r in ff_df[ff_df['Team'] != 'League Average'].iterrows():
        for col, lo, hi in [('ORtg', 80, 125), ('DRtg', 80, 125), ('Pace', 65, 100)]:
            v = float(r[col])
            assert lo <= v <= hi, (
                f"{r['Team']} {col}={v} is outside the sane band [{lo}, {hi}]."
            )


def _compute_streak(team_raw, team_name):
    """Current W/L streak for a team, e.g. 'W3' or 'L2'."""
    games = team_raw[team_raw['team_display_name'] == team_name].sort_values('game_date')
    if games.empty:
        return '-'
    results = games['team_winner'].tolist()
    cur = results[-1]
    count = 0
    for r in reversed(results):
        if r == cur:
            count += 1
        else:
            break
    return f'{"W" if cur else "L"}{count}'


def _compute_last10(team_raw, team_name):
    """Record over last 10 games, e.g. '7-3'."""
    games = team_raw[team_raw['team_display_name'] == team_name].sort_values('game_date')
    last10 = games.tail(10)
    w = int(last10['team_winner'].sum())
    l = len(last10) - w
    return f'{w}-{l}'


def seed_order(team_raw, std):
    """Team display names in ESPN's frozen seed order, or None to keep the
    table's own `WPct, Diff` sort.

    Used only when the seeds file exists AND every team's W-L in our own
    data equals the W-L the file was frozen with. That second condition is
    what makes this safe to leave on all year: the seeds describe ONE table,
    the final regular-season one, and applying them to any other (a
    mid-season snapshot, the golden harness's August data) would publish an
    order the league never ranked. It also doubles as a check that our box
    scores and ESPN's standings agree on every result.

    Why it exists: the league breaks ties on head-to-head, and our sort broke
    them on point differential. WSH won the 2026 season series with IND 2-1,
    ESPN seeds WSH 5 and IND 6, and the site had them the other way round.
    """
    _, _, teams = load_seeds()
    if not teams:
        return None
    tla = (team_raw[['team_display_name', 'team_abbreviation']].drop_duplicates()
           .set_index('team_abbreviation')['team_display_name'].to_dict())
    rec = std.set_index('team_display_name')[['W', 'L']]
    order = []
    for t in sorted(teams, key=lambda t: t['seed']):
        name = tla.get(t['abbr'])
        if name is None or name not in rec.index:
            return None
        if (int(rec.at[name, 'W']), int(rec.at[name, 'L'])) != (t['wins'], t['losses']):
            return None
        order.append(name)
    if len(order) != len(rec):
        return None
    check_seed_tiebreaks(team_raw, teams)
    return order


def check_seed_tiebreaks(team_raw, teams):
    """For every TWO-team tie ESPN resolved, confirm our own box scores give
    the head-to-head to the team ESPN seeded higher. Prints a WARNING on
    disagreement and never fails the build — ESPN's order still stands, but
    a disagreement means one of us has a game wrong. Three-way ties go
    through the league's multi-team steps and are not checked here."""
    by_rec = {}
    for t in teams:
        by_rec.setdefault((t['wins'], t['losses']), []).append(t)
    for tied in by_rec.values():
        if len(tied) != 2:
            continue
        hi, lo = sorted(tied, key=lambda t: t['seed'])
        g = team_raw[team_raw['team_abbreviation'] == hi['abbr']]
        opp = team_raw[team_raw['team_abbreviation'] == lo['abbr']]
        games = set(g['game_id']) & set(opp['game_id'])
        won = int(g[g['game_id'].isin(games)]['team_winner'].sum())
        lost = len(games) - won
        if won > lost:
            print(f"Seeds: {hi['abbr']} ({hi['seed']}) over {lo['abbr']} "
                  f"({lo['seed']}) on head-to-head {won}-{lost} — agrees with ESPN")
        else:
            print(f"WARNING: ESPN seeds {hi['abbr']} {hi['seed']} over "
                  f"{lo['abbr']} {lo['seed']}, but our box scores give the "
                  f"season series {won}-{lost}. ESPN's order is kept; check "
                  "for a missing or mis-scored game.")


def compute_standings(team_raw):
    """Win-loss standings with Pythagorean expected record, streak, last 10."""
    std = team_raw.groupby('team_display_name').agg(
        GP     = ('game_id', 'count'),
        W      = ('team_winner', 'sum'),
        PF_tot = ('team_score', 'sum'),
        PA_tot = ('opponent_team_score', 'sum'),
    ).reset_index()

    std['L']    = std['GP'] - std['W']
    std['WPct'] = std['W'] / std['GP']
    std['Win%'] = std['WPct'].apply(fmt_winpct)

    std['Strk'] = std['team_display_name'].apply(lambda t: _compute_streak(team_raw, t))
    std['L10']  = std['team_display_name'].apply(lambda t: _compute_last10(team_raw, t))

    std['PF']   = (std['PF_tot'] / std['GP']).round(1)
    std['PA']   = (std['PA_tot'] / std['GP']).round(1)
    std['Diff'] = (std['PF'] - std['PA']).round(1)

    std['pyth'] = std['PF_tot']**PYTH_EXP / (std['PF_tot']**PYTH_EXP + std['PA_tot']**PYTH_EXP)
    std['XW']   = (std['pyth'] * std['GP']).round(1)
    std['XL']   = (std['GP'] - std['XW']).round(1)

    std = std.sort_values(['WPct', 'Diff'], ascending=[False, False])
    order = seed_order(team_raw, std)
    if order is not None:
        std = std.set_index('team_display_name').loc[order].reset_index()
    df = std[['team_display_name','W','L','Win%','Strk','L10','PF','PA','Diff','XW','XL']].copy()
    df.columns = ['Team','W','L','Win%','Strk','L10','PF','PA','+/-','XW','XL']
    return df


def _aggregate_players(player_raw, keys):
    """Shared aggregation for the two player frames (see compute_player_base
    and compute_player_season). Sums box-score rows grouped by `keys` and
    derives the per-game and percentage columns, including the unrounded
    '<stat>_raw' twins the leader boards rank on.

    Identity is always the stable ESPN athlete_id, never the display name, so
    a rename (e.g. Megan Gustafson -> Megan DiLeo) or an ESPN spelling drift
    cannot split one athlete into two, and two players who ever share a name
    never merge. Display name, position and current team are resolved from
    each athlete's most-recent game row, so a rename or a team change propagates
    across the whole season automatically."""
    pr = player_raw.copy()
    pr['athlete_id'] = pr['athlete_id'].astype(str)

    # Canonical display name + position + current team = each athlete's
    # most-recent game row. game_date is 'YYYY-MM-DD', so lexical sort ==
    # chronological order.
    latest = pr.sort_values('game_date').groupby('athlete_id').tail(1)
    name_map = latest.set_index('athlete_id')['athlete_display_name']
    pos_map  = latest.set_index('athlete_id')['athlete_position_abbreviation']
    team_map = latest.set_index('athlete_id')['team_abbreviation']

    p = pr.groupby(
        list(keys)
    ).agg(
        GP   = ('game_id', 'count'),
        MIN  = ('minutes', 'sum'),
        PTS  = ('points', 'sum'),
        FGM  = ('field_goals_made', 'sum'),
        FGA  = ('field_goals_attempted', 'sum'),
        TPM  = ('three_point_field_goals_made', 'sum'),
        TPA  = ('three_point_field_goals_attempted', 'sum'),
        FTM  = ('free_throws_made', 'sum'),
        FTA  = ('free_throws_attempted', 'sum'),
        ORB  = ('offensive_rebounds', 'sum'),
        DRB  = ('defensive_rebounds', 'sum'),
        TRB  = ('rebounds', 'sum'),
        AST  = ('assists', 'sum'),
        STL  = ('steals', 'sum'),
        BLK  = ('blocks', 'sum'),
        TOV  = ('turnovers', 'sum'),
        PF   = ('fouls', 'sum'),
        FIRST = ('game_date', 'min'),
    ).reset_index()

    # Attach the canonical name/position resolved above, so downstream tables and
    # leaders read one consistent label per athlete regardless of past renames.
    p['athlete_display_name'] = p['athlete_id'].map(name_map)
    p['athlete_position_abbreviation'] = p['athlete_id'].map(pos_map)
    # Season-combined rows carry no team of their own — label them with the
    # athlete's current team, matching how stats.wnba.com presents the season
    # line of a player who has appeared for more than one team.
    if 'team_abbreviation' not in p.columns:
        p['team_abbreviation'] = p['athlete_id'].map(team_map)

    # Games her team has played — the basis for every leader-board minimum,
    # which the league prorates per team, not league-wide (see compute_leaders).
    # On the season frame this is the CURRENT team's count, the same team the
    # combined line is labeled with. Teams are up to four games apart in early
    # August, so this is not a rounding detail.
    team_games = pr.groupby('team_abbreviation')['game_id'].nunique()
    p['TEAM_GP'] = p['team_abbreviation'].map(team_games).fillna(team_games.max())

    p = p.sort_values('PTS', ascending=False)

    # Counting-stat averages. Each stat keeps two columns: the display value
    # rounded to a tenth, and an unrounded '<stat>_raw' twin that leader boards
    # rank on. Ranking on the rounded value ordered same-tenth players
    # arbitrarily (Leite 6.04 vs Miles 5.96 both display 6.0 — WNBA.com ranks
    # on the unrounded rate), which could scramble the published top 5.
    for col, src in [('PPG','PTS'), ('RPG','TRB'), ('ORPG','ORB'), ('APG','AST'),
                     ('SPG','STL'), ('BPG','BLK'), ('TPG','TOV')]:
        p[col + '_raw'] = p[src] / p['GP']
        p[col] = p[col + '_raw'].round(1)

    # Shooting percentages — use .where() to avoid divide-by-zero NaN warnings
    p['eFG%_raw'] = ((p['FGM'] + 0.5*p['TPM']) / p['FGA'] * 100).where(p['FGA'] > 0)
    p['TS%_raw']  = (p['PTS'] / (2 * (p['FGA'] + 0.44*p['FTA'])) * 100).where(p['FGA'] > 0)
    p['FT%_raw']  = (p['FTM'] / p['FTA'] * 100).where(p['FTA'] > 0)
    p['3PT%_raw'] = (p['TPM'] / p['TPA'] * 100).where(p['TPA'] > 0)
    for col in ('eFG%', 'TS%', 'FT%', '3PT%'):
        p[col] = p[col + '_raw'].round(1)

    return p


def compute_player_base(player_raw):
    """Per-(athlete, team) lines: a player who changes teams mid-season gets one
    row per team she appeared for. Feeds the Players table's split rows and the
    games-played guard, which bounds each row by its own team's game count.

    NOT for leader boards — see compute_player_season for why."""
    return _aggregate_players(player_raw, ['athlete_id', 'team_abbreviation'])


def compute_player_season(player_raw):
    """One combined season line per athlete, regardless of how many teams she
    played for, labeled with her current team. This is what the leader boards
    and the external Layer-2 validator must use.

    The per-team split (compute_player_base) is right for the Players table but
    wrong for leaders, in three ways — all live failure modes, not theory:
      1. Her board line shows only her stint with one team, as
         though it were her whole season (Kelsey Plum, 2026-08-04: we published
         her 12-game LAS eFG% of 60.904 against the league's combined 13-game
         61.616).
      2. Qualification minimums apply to each partial line, so a player who
         clears a percentage board's made-shot minimum on the season can miss
         it on both halves and vanish from a board she leads.
      3. A high-volume player who moves early enough qualifies twice and
         occupies two slots on the same top 10.
    The league computes leaders on the combined line; so do we."""
    return _aggregate_players(player_raw, ['athlete_id'])


def compute_team_stats(team_raw):
    """Per-game team stats with a league average row appended."""
    ts = team_raw.groupby('team_display_name').agg(
        GP   = ('game_id', 'count'),
        PTS  = ('team_score', 'sum'),
        FGM  = ('field_goals_made', 'sum'),
        FGA  = ('field_goals_attempted', 'sum'),
        TPM  = ('three_point_field_goals_made', 'sum'),
        TPA  = ('three_point_field_goals_attempted', 'sum'),
        FTM  = ('free_throws_made', 'sum'),
        FTA  = ('free_throws_attempted', 'sum'),
        ORB  = ('offensive_rebounds', 'sum'),
        DRB  = ('defensive_rebounds', 'sum'),
        TRB  = ('total_rebounds', 'sum'),
        AST  = ('assists', 'sum'),
        STL  = ('steals', 'sum'),
        BLK  = ('blocks', 'sum'),
        TOV  = ('total_turnovers', 'sum'),
        PF   = ('fouls', 'sum'),
    ).reset_index()

    def _row(label, r, gp):
        return {
            'Team':  label,
            'PPG':   round(r['PTS']/gp, 1),
            'FG':    ma(r['FGM']/gp, r['FGA']/gp),
            'FG%':   pct(r['FGM'], r['FGA']),
            '3PT':   ma(r['TPM']/gp, r['TPA']/gp),
            '3PT%':  pct(r['TPM'], r['TPA']),
            'FT':    ma(r['FTM']/gp, r['FTA']/gp),
            'FT%':   pct(r['FTM'], r['FTA']),
            'OR':    round(r['ORB']/gp, 1),
            'DR':    round(r['DRB']/gp, 1),
            'TR':    round(r['TRB']/gp, 1),
            'A':     round(r['AST']/gp, 1),
            'ST':    round(r['STL']/gp, 1),
            'B':     round(r['BLK']/gp, 1),
            'TO':    round(r['TOV']/gp, 1),
            'PF':    round(r['PF']/gp, 1),
        }

    rows = [_row(r['team_display_name'], r, r['GP']) for _, r in ts.iterrows()]
    lg = ts.sum(numeric_only=True)
    rows.append(_row('League Average', lg, lg['GP']))
    return pd.DataFrame(rows)


def compute_four_factors(team_raw, player_raw):
    """Four factors via self-join (offensive + defensive).
    Returns (ff_df, team_list).

    Pace is Pace/40 -- possessions per 40 minutes, adjusted for OT games
    using actual team minutes from the player box (BBRef methodology).
    Ratings use the average of team and opponent possession estimates as
    a common denominator, also matching BBRef.
    """
    team_mins = (player_raw
                 .groupby(['game_id', 'team_id'])['minutes']
                 .sum()
                 .reset_index()
                 .rename(columns={'minutes': 'team_minutes'}))

    away_cols = ['game_id','team_id',
                 'field_goals_made','field_goals_attempted',
                 'three_point_field_goals_made','three_point_field_goals_attempted',
                 'free_throws_made','free_throws_attempted',
                 'offensive_rebounds','defensive_rebounds',
                 'total_turnovers','team_score']
    away = team_raw[away_cols].copy()
    away.columns = ['game_id','opp_id',
                    'opp_fgm','opp_fga','opp_3pm','opp_3pa',
                    'opp_ftm','opp_fta','opp_orb','opp_drb',
                    'opp_tov','opp_pts']

    paired = team_raw.merge(away, left_on=['game_id','opponent_team_id'],
                                  right_on=['game_id','opp_id'])
    paired = paired.merge(team_mins, on=['game_id', 'team_id'])

    ff = paired.groupby('team_display_name').agg(
        GP           = ('game_id', 'count'),
        PTS          = ('team_score', 'sum'),
        FGM          = ('field_goals_made', 'sum'),
        FGA          = ('field_goals_attempted', 'sum'),
        TPM          = ('three_point_field_goals_made', 'sum'),
        FTM          = ('free_throws_made', 'sum'),
        FTA          = ('free_throws_attempted', 'sum'),
        ORB          = ('offensive_rebounds', 'sum'),
        DRB          = ('defensive_rebounds', 'sum'),
        TOV          = ('total_turnovers', 'sum'),
        OPP_PTS      = ('opp_pts', 'sum'),
        OPP_FGM      = ('opp_fgm', 'sum'),
        OPP_FGA      = ('opp_fga', 'sum'),
        OPP_3PM      = ('opp_3pm', 'sum'),
        OPP_FTM      = ('opp_ftm', 'sum'),
        OPP_FTA      = ('opp_fta', 'sum'),
        OPP_ORB      = ('opp_orb', 'sum'),
        OPP_DRB      = ('opp_drb', 'sum'),
        OPP_TOV      = ('opp_tov', 'sum'),
        TEAM_MINUTES = ('team_minutes', 'sum'),
    ).reset_index()

    # Possessions: Basketball-Reference / Dean Oliver estimate. The missed-shot
    # term uses the offensive-rebound RATE (1.07 * ORB% * missed FG), NOT raw
    # ORB. Subtracting raw ORB (the old formula) overcounts possessions because
    # WNBA teams rebound a large share of their misses, which dragged every
    # ORtg/DRtg ~2 points below the official stats.wnba.com figures.
    _tm_orb_pct  = ff['ORB']     / (ff['ORB']     + ff['OPP_DRB'])
    _opp_orb_pct = ff['OPP_ORB'] / (ff['OPP_ORB'] + ff['DRB'])
    ff['POSS']     = (ff['FGA'] + 0.44 * ff['FTA']
                      - 1.07 * _tm_orb_pct * (ff['FGA'] - ff['FGM']) + ff['TOV'])
    ff['OPP_POSS'] = (ff['OPP_FGA'] + 0.44 * ff['OPP_FTA']
                      - 1.07 * _opp_orb_pct * (ff['OPP_FGA'] - ff['OPP_FGM']) + ff['OPP_TOV'])
    ff['POSS_avg'] = (ff['POSS'] + ff['OPP_POSS']) / 2

    ff['ORtg'] = (100 * ff['PTS']     / ff['POSS_avg']).round(1)
    ff['DRtg'] = (100 * ff['OPP_PTS'] / ff['POSS_avg']).round(1)
    ff['NRtg'] = (ff['ORtg'] - ff['DRtg']).round(1)
    ff['Pace'] = (40 * (ff['POSS'] + ff['OPP_POSS']) / (2 * (ff['TEAM_MINUTES'] / 5))).round(1)

    ff['O_eFG%']   = ((ff['FGM'] + 0.5*ff['TPM']) / ff['FGA'] * 100).round(1)
    ff['O_TOV%']   = (ff['TOV'] / (ff['FGA'] + 0.44*ff['FTA'] + ff['TOV']) * 100).round(1)
    ff['O_ORB%']   = (ff['ORB'] / (ff['ORB'] + ff['OPP_DRB']) * 100).round(1)
    ff['O_FT/FGA'] = (ff['FTM'] / ff['FGA']).round(3)

    ff['D_eFG%']   = ((ff['OPP_FGM'] + 0.5*ff['OPP_3PM']) / ff['OPP_FGA'] * 100).round(1)
    ff['D_TOV%']   = (ff['OPP_TOV'] / (ff['OPP_FGA'] + 0.44*ff['OPP_FTA'] + ff['OPP_TOV']) * 100).round(1)
    ff['D_DRB%']   = (ff['DRB'] / (ff['DRB'] + ff['OPP_ORB']) * 100).round(1)
    ff['D_FT/FGA'] = (ff['OPP_FTM'] / ff['OPP_FGA']).round(3)

    ff = ff.sort_values('NRtg', ascending=False)
    team_list = ff['team_display_name'].tolist()

    lg = ff.sum(numeric_only=True)
    _lg_tm_orb_pct  = lg['ORB']     / (lg['ORB']     + lg['OPP_DRB'])
    _lg_opp_orb_pct = lg['OPP_ORB'] / (lg['OPP_ORB'] + lg['DRB'])
    lg_poss     = (lg['FGA'] + 0.44*lg['FTA']
                   - 1.07 * _lg_tm_orb_pct * (lg['FGA'] - lg['FGM']) + lg['TOV'])
    lg_opp_poss = (lg['OPP_FGA'] + 0.44*lg['OPP_FTA']
                   - 1.07 * _lg_opp_orb_pct * (lg['OPP_FGA'] - lg['OPP_FGM']) + lg['OPP_TOV'])
    lg_poss_avg = (lg_poss + lg_opp_poss) / 2
    lg_avg = {
        'team_display_name': 'League Average',
        'ORtg':      round(100 * lg['PTS']     / lg_poss_avg, 1),
        'DRtg':      round(100 * lg['OPP_PTS'] / lg_poss_avg, 1),
        'NRtg':      '',
        'Pace':      round(40 * (lg_poss + lg_opp_poss) / (2 * (lg['TEAM_MINUTES'] / 5)), 1),
        'O_eFG%':    round((lg['FGM'] + 0.5*lg['TPM']) / lg['FGA'] * 100, 1),
        'O_TOV%':    round(lg['TOV'] / (lg['FGA'] + 0.44*lg['FTA'] + lg['TOV']) * 100, 1),
        'O_ORB%':    round(lg['ORB'] / (lg['ORB'] + lg['OPP_DRB']) * 100, 1),
        'O_FT/FGA':  round(lg['FTM'] / lg['FGA'], 3),
        'D_eFG%':    round((lg['OPP_FGM'] + 0.5*lg['OPP_3PM']) / lg['OPP_FGA'] * 100, 1),
        'D_TOV%':    round(lg['OPP_TOV'] / (lg['OPP_FGA'] + 0.44*lg['OPP_FTA'] + lg['OPP_TOV']) * 100, 1),
        'D_DRB%':    round(lg['DRB'] / (lg['DRB'] + lg['OPP_ORB']) * 100, 1),
        'D_FT/FGA':  round(lg['OPP_FTM'] / lg['OPP_FGA'], 3),
    }

    display_cols = ['team_display_name','ORtg','DRtg','NRtg','Pace',
                    'O_eFG%','O_TOV%','O_ORB%','O_FT/FGA',
                    'D_eFG%','D_TOV%','D_DRB%','D_FT/FGA']
    ff_df = pd.concat([
        ff[display_cols],
        pd.DataFrame([lg_avg])[display_cols]
    ], ignore_index=True)
    ff_df.columns = ['Team','ORtg','DRtg','NRtg','Pace',
                     'O_eFG%','O_TOV%','O_ORB%','O_FT/FGA',
                     'D_eFG%','D_TOV%','D_DRB%','D_FT/FGA']
    return ff_df, team_list


def _competition_ranks(vals):
    """Places for a ranked list of values, ties sharing the higher place:
    1, 1, 3 — not 1, 2, 3.

    ⚠️ SECOND COPY EXISTS. `sites/wwc/build_wwc_pages._competition_ranks` is a
    verbatim duplicate, added 2026-09-03 with the WWC Leaders tab. Change one
    and you must change the other: a duplicated tie rule on two sites is how
    the bug described below happened in the first place, and it is now
    duplicated on purpose rather than by accident (moving it into `core/`
    moves the golden-check surface, which was not a thing to do the day before
    a tournament). Unifying them is a backlog row for after the Cup.

    Two players the league calls tied get one number on our board, the way
    stats.wnba.com publishes a duplicated RANK and the way the player-page
    badges have always read (build_player_pages.compute_card_ranks uses
    rank(method="min")). Before this, a dead tie rendered 1 and 2 on the leader
    card while both players' own pages said "1st", and it tripped the morning
    leaders validation, whose FAIL then gated the daily post — 2026-08-25,
    Clark and Thomas, 290 assists in 35 games apiece.

    Equality is exact on the unrounded value, matching compute_card_ranks and
    the build-time assertion that ties the two together. Players who merely
    display the same tenth are NOT tied and keep separate places.
    """
    places, prev_val, prev_place = [], None, 0
    for pos, v in enumerate(vals, start=1):
        place = prev_place if (prev_val is not None and v == prev_val) else pos
        places.append(place)
        prev_val, prev_place = v, place
    return places


def compute_leaders(p_base, full=False):
    """Top-10 category leaders using the WNBA's official League Leaders
    qualification (deliberately not ESPN's 70%-of-games-only rule). Counting
    boards apply the league's "70% of games OR volume" disjunction; percentage
    boards apply made-shot minimums only. Full rules, sources and the rationale:
    docs/wnba-leader-qualification-rules.md

    full=False (the site build) returns display-shaped boards only — renderers
    and emit_social_payload assume the value column is last, so never widen
    them. full=True (validate_stats.py) appends 'athlete_id' and an unrounded
    '_raw' value column for exact external comparison.

    Takes the season-combined frame (compute_player_season), never the
    per-team one."""
    # Tripwire for the 2026-08-04 regression: fed the per-team frame, a player
    # who changed teams is ranked on a partial season, can miss a qualification minimum on
    # both halves, or can occupy two slots on one board. Cheap to assert, and
    # it fires at build time rather than in the next morning's alert email.
    dupes = p_base['athlete_id'][p_base['athlete_id'].duplicated()].tolist()
    assert not dupes, (
        f"compute_leaders got more than one row for athlete(s) {dupes[:5]} — "
        "this is the per-team frame (compute_player_base). Leader boards must "
        "be computed from compute_player_season, which combines a player's "
        "stints into the single season line the league ranks on."
    )

    # Every minimum is prorated by the games HER OWN TEAM has played, not by
    # the league-wide leader in games played. The league's published rule says
    # "70% of team games played", and the volume half prorates on the same
    # basis: on 2026-08-03, Seattle had played 32 games and Connecticut 30, so
    # scaling every Sun player against 32 set their cutoffs ~7% too high and
    # silently dropped Brittney Griner (28 BLK vs a 29.1 cutoff that should
    # have been 27.3) and Aneesah Morrow (178 TRB vs 181.8, should be 170.5)
    # off boards the league had them on. For a multi-team player, TEAM_GP is her
    # current team's count — the same team her combined line is labeled with.
    scale  = p_base['TEAM_GP'] / 44.0
    min_gp = (0.70 * p_base['TEAM_GP']).round().clip(lower=1)

    def top10(df, stat, disp):
        # Rank on the unrounded '<stat>_raw' column (matching how WNBA.com
        # orders players who display the same tenth), show the rounded one.
        raw = stat + '_raw'
        sub = df.dropna(subset=[raw])
        cols = ['athlete_display_name', 'team_abbreviation', 'GP', stat]
        names = ['Player', 'Team', 'GP', disp]
        if full:
            cols += ['athlete_id', raw]
            names += ['athlete_id', '_raw']
        sel = sub.nlargest(10, raw)
        top = sel[cols].copy()
        top.columns = names
        # The board's published place rides on the INDEX, so the column shape
        # the renderers and emit_social_payload depend on (value last) is
        # untouched. Ties share a place — see _competition_ranks.
        top.index = pd.Index(_competition_ranks(sel[raw].tolist()), name='rank')
        return top

    # Games-played branch: 70% of team games, the league's alternative route to
    # qualifying. Also the sole qualifier for the Off Reb and Turnovers boards.
    q_gp   = p_base['GP']  >= min_gp

    # Counting-stat boards use the league's DISJUNCTION: "70% of team games
    # played OR <volume>" (stats.wnba.com/help/statminimums). Volume minimums
    # are the official full-season numbers prorated by season progress; the
    # games-played branch is taken as-is. Implementing only the volume half
    # (as we did through 2026-07-27) is stricter than the league and can
    # silently drop a qualified player — see docs/wnba-leader-qualification-rules.md
    # §4a. Harmless on most boards, because a player entering via the GP branch
    # has a rate ceiling of volume/(44*0.70) that sits below the cut line, but
    # STEALS is genuinely exposed (ceiling 1.79 vs a ~1.62 tenth-best).
    q_pts  = (p_base['PTS'] >= 525 * scale) | q_gp
    q_reb  = (p_base['TRB'] >= 250 * scale) | q_gp
    q_ast  = (p_base['AST'] >= 150 * scale) | q_gp
    q_stl  = (p_base['STL'] >= 55  * scale) | q_gp
    q_blk  = (p_base['BLK'] >= 40  * scale) | q_gp

    # Percentage boards have NO games-played alternative in the official rule —
    # made-shot minimums only. Do not add q_gp to these.
    q_ftm  = p_base['FTM'] >= 50   * scale
    q_3pm  = p_base['TPM'] >= 25   * scale
    q_fgm  = p_base['FGM'] >= 100  * scale

    leaders = {}
    leaders['Scoring']   = top10(p_base[q_pts],  'PPG',  'PPG')
    leaders['3PT%']      = top10(p_base[q_3pm],  '3PT%', '3PT%')
    leaders['eFG%']      = top10(p_base[q_fgm],  'eFG%', 'eFG%')
    leaders['FT%']       = top10(p_base[q_ftm],  'FT%',  'FT%')
    leaders['TS%']       = top10(p_base[q_fgm],  'TS%',  'TS%')
    leaders['Assists']   = top10(p_base[q_ast],  'APG',  'APG')
    leaders['Rebounds']  = top10(p_base[q_reb],  'RPG',  'RPG')
    leaders['Off Reb']   = top10(p_base[q_gp],   'ORPG', 'ORPG')
    leaders['Steals']    = top10(p_base[q_stl],  'SPG',  'SPG')
    leaders['Blocks']    = top10(p_base[q_blk],  'BPG',  'BPG')
    leaders['Turnovers'] = top10(p_base[q_gp],   'TPG',  'TPG')
    return leaders


# Categories broadcast by the daily Bluesky auto-post, in rotation order
# (one per day) — must match BROADCAST_ORDER in post_to_bluesky.py. Sequence is
# intentional (related stats adjacent). Turnovers is excluded — "leader" there
# means MOST turnovers, which would single players out negatively, against the
# site's positive tone.
BROADCAST_CATS = ['Scoring', '3PT%', 'eFG%', 'Assists', 'FT%',
                  'TS%', 'Rebounds', 'Off Reb', 'Blocks', 'Steals']
_PCT_CATS = {'3PT%', 'eFG%', 'FT%', 'TS%'}


def emit_social_payload(player_raw, leaders, display_date, data_through_iso,
                        path=None):
    """Write a small JSON the Bluesky auto-post consumes: top-5 per broadcast
    category plus a deterministic 'last night' factoid. The page is the source
    of truth; this only mirrors the already-computed leaders so the post can
    never drift from the site. Purely additive — never affects the HTML build."""
    import json
    import datetime as _dt

    cats = {}
    for cat in BROADCAST_CATS:
        # Top 5 plus anyone sharing 5th place. A straight head(5) drops a tied
        # player for no reason other than how she sorted inside her own place
        # — on 2026-08-25 that was Leila Lacan, exactly level with Natasha
        # Howard at 1.667 SPG. Whether they all FIT is post_to_bluesky's call.
        board = leaders[cat]
        df = (board if len(board) <= 5
              else board[board.index <= board.index[4]])
        val_col = df.columns[-1]
        rows = []
        # Places come from the board itself, so the post repeats a tie rather
        # than inventing an order for it (1. / 1. / 3.).
        for rank, r in df.iterrows():
            v = r[val_col]
            vs = f"{v:.1f}%" if cat in _PCT_CATS else f"{v:.1f}"
            rows.append({'rank': int(rank), 'player': r['Player'],
                         'team': r['Team'], 'value': vs})
        cats[cat] = rows

    # Deterministic "last night" factoid. "Last night" = the ET calendar day
    # before this build, so we attach it ONLY when the newest games are exactly
    # yesterday. A build that runs after same-day games (a manual midday rebuild,
    # or the rare afternoon slate) then won't mislabel today's game as last
    # night's, and multi-day breaks (All-Star, FIBA) yield no stale factoid.
    factoid = None
    gd = pd.to_datetime(player_raw['game_date'])
    last = gd.max()
    et_today = today_et()
    yesterday = pd.Timestamp(et_today) - pd.Timedelta(days=1)
    if pd.notna(last) and last.normalize() == yesterday:
        ln = player_raw[gd == last].copy()
        if len(ln):
            td = None
            for _, r in ln.iterrows():
                hits = sum(1 for c in ('points', 'rebounds', 'assists', 'steals', 'blocks')
                           if pd.notna(r.get(c)) and r[c] >= 10)
                if hits >= 3:
                    td = r
                    break
            if td is not None:
                factoid = (f"Triple-double last night: {td['athlete_display_name']} "
                           f"({td['team_abbreviation']}) "
                           f"{int(td['points'])}/{int(td['rebounds'])}/{int(td['assists'])}.")
            elif ln['points'].notna().any():
                top = ln.loc[ln['points'].idxmax()]
                factoid = (f"Last night's high: {top['athlete_display_name']} "
                           f"({top['team_abbreviation']}) {int(top['points'])} pts.")

    payload = {
        'through': display_date,
        'through_iso': data_through_iso,
        'generated_utc': _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds'),
        'categories': cats,
        'factoid': factoid,
    }
    # Anchored to the site dir via the config, not to CWD. post_to_bluesky.py
    # resolves the same path from the same config, so writer and reader cannot
    # drift apart the way they would have when both scripts lived in the root.
    path = path or WNBA.social_payload
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return payload


# ── HTML section builders ─────────────────────────────────────────────────

#: Rows that carry a team's NAME but are not a team. These must never link.
_NOT_A_TEAM = {'League Average'}


def team_href(name):
    """Team name as a link to its page, or as plain text when it isn't a team.

    The one place a team page URL is constructed on this site. Uses the same
    `seo.slugify` the emitter uses, so a link here and the directory
    build_team_pages.py writes cannot disagree — the same guarantee
    player_row() relies on for player pages.

    Correct-or-blank: the League Average row appears in the Team column of two
    tables and has no page, so it renders as text. §6b requires team pages and
    these links to ship in ONE merge for exactly this reason — links without
    pages are 404s, and pages without links are undiscoverable.
    """
    s = str(name)
    if not s or s in _NOT_A_TEAM:
        return esc(s)
    return f'<a class="pl" href="/teams/{seo.slugify(s)}/">{esc(s)}</a>'


def _color_cell(col, val):
    """Apply red/green styling to Streak and +/- cells."""
    if col == 'Team':
        return f'<td>{team_href(val)}</td>'
    s = str(val)
    if col == 'Strk':
        if s.startswith('W'):
            return f'<td style="color:#4caf50">{val}</td>'
        elif s.startswith('L'):
            return f'<td style="color:#e05555">{val}</td>'
    elif col == '+/-':
        try:
            v = float(val)
            if v > 0:
                return f'<td style="color:#4caf50">+{val}</td>'
            elif v < 0:
                return f'<td style="color:#e05555">{val}</td>'
        except (ValueError, TypeError):
            pass
    return f'<td>{val}</td>'


def build_standings_section(standings_df, final=False):
    """Standings with an orange dashed playoff cutoff line after 8th place.

    `final` (the postseason) adds the "final regular season" subtitle. No seed
    column and no clinch marks — the dashed cutoff already says who is in."""
    cols = list(standings_df.columns)
    headers = ''.join(
        f'<th onclick="sortTable(\'tbl_standings\',{i})">{h}</th>'
        for i, h in enumerate(cols)
    )
    rows = ''
    for i, (_, row) in enumerate(standings_df.iterrows()):
        cutoff = ' playoff-cutoff' if i == PLAYOFF_SPOTS - 1 else ''
        cells = ''.join(_color_cell(c, v) for c, v in zip(cols, row))
        rows += f'<tr class="{cutoff}">{cells}</tr>\n'
    return (
        '<div id="standings" class="section">\n'
        + ('<h2>Standings <span class="sub">\u2014 final regular season</span></h2>\n'
           if final else '<h2>Standings</h2>\n') +
        '<p class="tab-note"><em>Dashed line = playoff cutoff (top 8)&ensp;|'
        '&ensp;XW/XL = Pythagorean expected record</em></p>\n'
        '<div class="table-scroll"><div class="table-wrap">'
        f'<table id="tbl_standings"><thead><tr>{headers}</tr></thead>'
        f'<tbody>{rows}</tbody></table></div></div>\n'
        '</div>\n'
    )


def build_team_efficiency_section(ff_df, team_options):
    return (
        '<div id="teameff" class="statview active">\n'
        '<h2>Team Efficiency</h2>\n'
        '<div class="matchup-bar">\n'
        '  <label>Matchup &nbsp;</label>\n'
        '  <select id="ff_team1" onchange="filterFF()">\n'
        f'    <option value="">All teams</option>\n    {team_options}\n'
        '  </select>\n'
        '  <span class="vs">vs</span>\n'
        '  <select id="ff_team2" onchange="filterFF()">\n'
        f'    <option value="">\u2014</option>\n    {team_options}\n'
        '  </select>\n'
        '  <button onclick="clearFF()">Clear</button>\n'
        '</div>\n'
        f'{ff_to_html(ff_df, "tbl_ff")}\n'
        '</div>\n'
    )


def build_team_totals_section(team_stats_df, team_options):
    return (
        '<div id="teamtotals" class="statview">\n'
        '<h2>Team Totals \u2014 Per Game</h2>\n'
        '<div class="matchup-bar">\n'
        '  <label>Matchup &nbsp;</label>\n'
        '  <select id="ts_team1" onchange="filterTeamStats()">\n'
        f'    <option value="">All teams</option>\n    {team_options}\n'
        '  </select>\n'
        '  <span class="vs">vs</span>\n'
        '  <select id="ts_team2" onchange="filterTeamStats()">\n'
        f'    <option value="">\u2014</option>\n    {team_options}\n'
        '  </select>\n'
        '  <button onclick="clearTeamStats()">Clear</button>\n'
        '</div>\n'
        f'{df_to_html(team_stats_df, "tbl_team", lg_avg=True, data_col="Team")}\n'
        '</div>\n'
    )


def build_players_section(p_team, p_season, team_abbrevs):
    """Player stats table: abbreviated names with team chip, season totals
    for shooting splits and counting stats, MPG and PPG per-game.

    A player who has appeared for two teams gets a Basketball-Reference-style
    stack: her
    combined season line (chip '2TM') followed by one indented row per team.
    The combined row's data-team is '2TM', which matches no team option, so
    picking a team in the filter narrows to that team's line — filtering to
    LAS should show what a player did *as a Spark*, not her season total."""
    # Column order: Player (chip), MPG, PPG, GP, FG, FG%, 3PT, 3PT%, FT, FT%,
    #               OR, DR, TR, A, ST, B, TO, PF
    col_labels = ['Player','MPG','PPG','GP','FG','FG%','3PT','3PT%',
                  'FT','FT%','OR','DR','TR','A','ST','B','TO','PF']
    headers = ''.join(
        f'<th onclick="sortTable(\'tbl_players\',{i})">{h}</th>'
        for i, h in enumerate(col_labels)
    )

    def player_row(r, team_label, row_cls=''):
        gp = r['GP']
        name = r['athlete_display_name']
        sn = short_name(name)
        # Every row funnels through here, so the 2TM combined row and each
        # per-team stint all link to the same page — slug is trade-safe (no
        # team in it) and comes from the same slugify the emitter uses.
        slug = seo.slugify(name)
        name_cell = (f'<a class="pl" href="/players/{slug}/">{esc(sn)}</a> '
                     f'<span class="tm">{esc(team_label)}</span>')

        cells = (
            f'<td>{name_cell}</td>'
            f'<td>{f1(r["MIN"]/gp)}</td>'
            f'<td>{f1(r["PTS"]/gp)}</td>'
            f'<td>{int(gp)}</td>'
            f'<td>{ma(r["FGM"], r["FGA"])}</td>'
            f'<td>{f1(pct(r["FGM"], r["FGA"]))}</td>'
            f'<td>{ma(r["TPM"], r["TPA"])}</td>'
            f'<td>{f1(pct(r["TPM"], r["TPA"]))}</td>'
            f'<td>{ma(r["FTM"], r["FTA"])}</td>'
            f'<td>{f1(pct(r["FTM"], r["FTA"]))}</td>'
            f'<td>{int(r["ORB"])}</td>'
            f'<td>{int(r["DRB"])}</td>'
            f'<td>{int(r["TRB"])}</td>'
            f'<td>{int(r["AST"])}</td>'
            f'<td>{int(r["STL"])}</td>'
            f'<td>{int(r["BLK"])}</td>'
            f'<td>{int(r["TOV"])}</td>'
            f'<td>{int(r["PF"])}</td>'
        )
        cls = f' class="{row_cls}"' if row_cls else ''
        return (f'<tr{cls} data-team="{esc(team_label)}" '
                f'data-player="{esc(name)}" '
                f'data-gp="{int(gp)}">{cells}</tr>\n')

    # Per-team lines for each athlete, in the order she played for them.
    legs = {aid: g.sort_values('FIRST')
            for aid, g in p_team.groupby('athlete_id')}

    # p_season is sorted by season points, so athletes appear in the same order
    # as before; a multi-team player's stints stay stacked under her season line.
    body = ''
    for _, s in p_season.iterrows():
        rows = legs[s['athlete_id']]
        if len(rows) == 1:
            body += player_row(rows.iloc[0], rows.iloc[0]['team_abbreviation'])
            continue
        body += player_row(s, f'{len(rows)}TM')
        for _, leg in rows.iterrows():
            body += player_row(leg, leg['team_abbreviation'], row_cls='tm-split')

    # Controls: two team dropdowns, min GP, search
    team_opts = ('<option value="">All teams</option>' +
                 ''.join(f'<option value="{esc(t)}">{esc(t)}</option>'
                         for t in sorted(team_abbrevs)))
    controls = (
        '<div class="controls">\n'
        f'  <span><label>Team </label><select id="pA" onchange="filterPlayers()">{team_opts}</select></span>\n'
        f'  <span class="vs">vs</span>\n'
        f'  <span><select id="pB" onchange="filterPlayers()">{team_opts}</select></span>\n'
        '  <span><label>Min GP </label><select id="pmin" onchange="filterPlayers()">'
        '<option value="0">All</option><option value="5">5+</option>'
        '<option value="10">10+</option></select></span>\n'
        '  <input type="text" id="psearch" placeholder="search player\u2026"'
        ' oninput="filterPlayers()">\n'
        '  <button onclick="clearPlayers()">Clear</button>\n'
        '</div>\n'
    )

    return (
        '<div id="players" class="statview">\n'
        '<h2>Players \u2014 Season Stats</h2>\n'
        '<span id="backToLeaders" class="back-link" style="display:none"'
        ' onclick="backToLeaders()">\u2190 Back to Leaders</span>\n'
        f'{controls}'
        '<div class="table-scroll"><div class="table-wrap">'
        f'<table id="tbl_players"><thead><tr>{headers}</tr></thead>'
        f'<tbody>{body}</tbody></table></div></div>\n'
        '</div>\n'
    )


#: The playoff boards (Jason, 2026-09-25): counting stats only, no
#: percentages. (column, board title, total header, average header).
PLAYOFF_BOARDS = [
    ('points', 'Points', 'PTS', 'PPG'),
    ('rebounds', 'Rebounds', 'REB', 'RPG'),
    ('assists', 'Assists', 'AST', 'APG'),
    ('steals', 'Steals', 'STL', 'SPG'),
    ('blocks', 'Blocks', 'BLK', 'BPG'),
    ('three_point_field_goals_made', 'Made 3s', '3PM', '3PG'),
]
PLAYOFF_TOP_N = 10


def check_playoff_points(player_all, team_all):
    """For every playoff game, both teams' player points must sum to the team
    score. Returns a list of mismatch strings, empty when clean.

    Jason's pick for the one cheap check on the Playoffs boards: the Layer-2
    leaders gate validates the Season boards only, and this catches the
    failure that matters most for a counting board — a player row missing or
    doubled."""
    bad = []
    if 'season_type' not in team_all.columns:
        return bad
    post_t = team_all[team_all['season_type'] == 3]
    post_p = player_all[player_all['season_type'] == 3]
    sums = post_p.groupby(['game_id', 'team_abbreviation'])['points'].sum()
    for _, r in post_t.iterrows():
        key = (r['game_id'], r['team_abbreviation'])
        got = int(sums.get(key, 0))
        if got != int(r['team_score']):
            bad.append(f"game {r['game_id']} {r['team_abbreviation']}: "
                       f"players sum to {got}, team scored {int(r['team_score'])}")
    return bad


def compute_playoff_leaders(player_all, team_all):
    """The Playoffs view of Leaders, or None when it must not be shown.

    WWC's `compute_leaders()` rule, applied a second time as
    docs/data-sources.md asked ("one decision, applied twice"): ranked on the
    UNROUNDED per-game average, displayed to a tenth, with GP and the total on
    every row, and NO minimum games — the GP column makes the small sample
    visible instead of hiding it behind a threshold we would have to defend.

    Two deliberate differences from WWC:
      * Ties are the WNBA's tie-safe ranking (the 2026-08-25 incident): tied
        players share a place, printed T4, and a tie AT the cut keeps everyone
        in it rather than truncating at ten.
      * Aggregated on athlete_id from season_type == 3 rows only.

    A null stat is not a zero (WWC's rule): a player with a missing value in a
    category is held off that board rather than ranked on a partial sum.

    None before the first final playoff box score (Season only, no switch),
    and None when check_playoff_points() finds a mismatch — the site still has
    to publish on game day, so that hides the view and warns, never fails.
    """
    if 'season_type' not in player_all.columns:
        return None
    post = player_all[player_all['season_type'] == 3].copy()
    if post.empty:
        return None
    bad = check_playoff_points(player_all, team_all)
    if bad:
        print(f"WARNING: playoff points check failed for {len(bad)} team-game(s) "
              "— Playoffs leaders view HIDDEN this build:")
        for b in bad[:10]:
            print(f"  {b}")
        return None

    post['athlete_id'] = post['athlete_id'].astype(str)
    latest = post.sort_values('game_date').groupby('athlete_id').tail(1).set_index('athlete_id')
    gp = post.groupby('athlete_id')['game_id'].nunique()
    boards = []
    for col, title, tot_h, avg_h in PLAYOFF_BOARDS:
        nulls = post[col].isna().groupby(post['athlete_id']).any()
        tot = post.groupby('athlete_id')[col].sum()
        rows = [{'id': a, 'name': latest.at[a, 'athlete_display_name'],
                 'team': latest.at[a, 'team_abbreviation'], 'gp': int(gp[a]),
                 'total': int(tot[a]), 'avg': float(tot[a]) / int(gp[a])}
                for a in tot.index if not nulls[a] and tot[a] > 0]
        rows.sort(key=lambda x: (-x['avg'], -x['total'], x['name']))
        places = _competition_ranks([x['avg'] for x in rows])
        counts = {}
        for p in places:
            counts[p] = counts.get(p, 0) + 1
        top = []
        for place, x in zip(places, rows):
            if place > PLAYOFF_TOP_N:
                break
            x['place'] = f'T{place}' if counts[place] > 1 else str(place)
            x['first'] = place == 1
            top.append(x)
        boards.append((title, tot_h, avg_h, top))
    return {'games': int(post['game_id'].nunique()), 'boards': boards}


def playoff_gp_caption(lb):
    """WWC's gp_caption(), ported: what the order means, always, and — only
    once the field has actually spread — why GP varies. Triggered by the
    measured spread among ranked players, not by a date; here it fires after
    the first round, when losers stop at 2 or 3 games and survivors play on."""
    gps = [r['gp'] for _, _, _, rows in lb['boards'] for r in rows]
    spread = ('' if not gps or max(gps) - min(gps) < 2 else
              ' A team’s run ends when it is eliminated, so a player can '
              'lead on fewer games than someone still playing.')
    return ('<p class="tab-note"><em>Ranked on the per-game average, with no '
            'minimum games. Games played (GP) and the total are shown for '
            f'every player.{spread}</em></p>')


def _playoff_leaders_html(lb):
    cards = ''
    for title, tot_h, avg_h, rows in lb['boards']:
        body = ''
        for x in rows:
            cls = ' rank-1' if x['first'] else ''
            body += (
                f'<tr class="ldr-row{cls}" data-team="{esc(x["team"])}">'
                f'<td>{esc(x["place"])}</td>'
                f'<td><span class="ldr-name" data-player="{esc(x["name"])}" '
                f'onclick="goToPlayer(this.dataset.player)">{esc(short_name(x["name"]))}'
                f'</span> <span class="tm">{esc(x["team"])}</span></td>'
                f'<td>{x["gp"]}</td><td>{x["total"]}</td>'
                f'<td class="po-v">{x["avg"]:.1f}</td></tr>\n')
        if not body:
            body = '<tr><td colspan="5" class="po-mu">No one yet.</td></tr>'
        cards += (
            f'<div class="leader-card po-lc"><h3>{esc(title)}</h3>'
            f'<table><thead><tr><th>#</th><th>Player</th><th>GP</th>'
            f'<th>{esc(tot_h)}</th><th>{esc(avg_h)}</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>\n')
    n = lb['games']
    return (
        '<div id="ldr-po" class="ldrview active">\n'
        '<h2>Playoff Leaders <span class="sub">— per game — through '
        f'{n} game{"" if n == 1 else "s"}</span></h2>\n'
        f'{playoff_gp_caption(lb)}'
        f'<div class="leaders-grid">{cards}</div>\n'
        '</div>\n')


def build_leaders_section(leaders, team_abbrevs, playoff=None):
    """Leader cards with team/player filter and click-to-player links.

    `playoff` is compute_playoff_leaders()'s result. When it is None the
    section is exactly the Season boards, with no switch — the WWC rule that a
    view is shown only once it has something on it. Otherwise the same
    `.swrap`/`.sw` switch as Stats appears, Playoffs first and the default.
    """
    team_opts = ('<option value="">All teams</option>' +
                 ''.join(f'<option value="{esc(t)}">{esc(t)}</option>'
                         for t in sorted(team_abbrevs)))
    controls = (
        '<div class="controls">\n'
        f'  <span><label>Team </label><select id="ldrTeam" onchange="filterLeaders()">{team_opts}</select></span>\n'
        '  <input type="text" id="ldrSearch" placeholder="search player\u2026"'
        ' oninput="filterLeaders()">\n'
        '  <button onclick="clearLeaders()">Clear</button>\n'
        '</div>\n'
    )

    cards = ''
    for label, ldr_df in leaders.items():
        stat_col = ldr_df.columns[-1]
        safe_id = 'ldr_' + label.replace(' ','_').replace('%','pct').replace('/','_')
        rows = ''
        for rank, r in ldr_df.iterrows():
            # Every share of first place gets the highlight, not just row 0.
            rank_cls = ' rank-1' if rank == 1 else ''
            name = str(r['Player'])
            team = str(r['Team'])
            sn = short_name(name)
            val = r[stat_col]
            val_str = f'{val:.1f}' if isinstance(val, float) else str(val)
            rows += (
                f'<tr class="ldr-row{rank_cls}" data-team="{esc(team)}">'
                f'<td>{rank}. <span class="ldr-name" data-player="{esc(name)}" onclick="goToPlayer(this.dataset.player)">'
                f'{esc(sn)}</span> <span class="tm">{esc(team)}</span></td>'
                f'<td>{val_str}</td></tr>\n'
            )
        cards += (
            f'<div class="leader-card"><h3>{esc(label)}</h3>'
            f'<table id="{safe_id}"><tbody>{rows}</tbody></table></div>\n'
        )

    season = (
        '<h2>Category Leaders <span class="sub">\u2014 per game \u2014 qualified</span></h2>\n'
        f'{controls}'
        f'<div class="leaders-grid">{cards}</div>\n'
    )
    if playoff is None:
        return '<div id="leaders" class="section">\n' + season + '</div>\n'
    # `.ldrview`, not `.statview`: showStat() owns every `.statview` on the
    # page, and the two switches must not fight. Same reason `.statview`
    # could not reuse `.section`.
    return (
        '<div id="leaders" class="section">\n'
        '<div class="swrap">\n'
        '  <button class="sw active" data-lv="ldr-po" '
        'onclick="showLeaders(\'ldr-po\',this)">Playoffs</button>\n'
        '  <button class="sw" data-lv="ldr-rs" '
        'onclick="showLeaders(\'ldr-rs\',this)">Season</button>\n'
        '</div>\n'
        f'{_playoff_leaders_html(playoff)}'
        f'<div id="ldr-rs" class="ldrview">\n{season}</div>\n'
        '</div>\n'
    )


def build_abbreviations_section():
    """Static reference tab explaining all abbreviations used on the site."""
    groups = [
        # Official WNBA team codes (the league's own TLAs, used site-wide since
        # 2026-07: PDX/GSV/LVA/NYL/LAS/WAS are less guessable than the ESPN
        # codes they replaced, so document all fifteen).
        ('Teams', [
            ('ATL', 'Atlanta Dream'), ('CHI', 'Chicago Sky'),
            ('CON', 'Connecticut Sun'), ('DAL', 'Dallas Wings'),
            ('GSV', 'Golden State Valkyries'), ('IND', 'Indiana Fever'),
            ('LAS', 'Los Angeles Sparks'), ('LVA', 'Las Vegas Aces'),
            ('MIN', 'Minnesota Lynx'), ('NYL', 'New York Liberty'),
            ('PDX', 'Portland Fire'), ('PHX', 'Phoenix Mercury'),
            ('SEA', 'Seattle Storm'), ('TOR', 'Toronto Tempo'),
            ('WAS', 'Washington Mystics'),
            ('2TM', 'Combined season total for a player who has appeared for '
                    'two teams — traded, waived and signed, or called up. The '
                    'indented rows beneath it are her stints with each team.'),
        ]),
        ('Standings', [
            ('W', 'Wins'), ('L', 'Losses'),
            ('Win%', 'Winning percentage'),
            ('PF', 'Points For (per game)'), ('PA', 'Points Against (per game)'),
            ('+/-', 'Point differential (PF \u2212 PA)'),
            ('XW', 'Expected wins (Pythagorean)'),
            ('XL', 'Expected losses (Pythagorean)'),
        ]),
        ('Leaders', [
            ('PPG', 'Points per game'), ('RPG', 'Rebounds per game'),
            ('ORPG', 'Offensive rebounds per game'),
            ('APG', 'Assists per game'), ('SPG', 'Steals per game'),
            ('BPG', 'Blocks per game'), ('TPG', 'Turnovers per game'),
            ('TS%', 'True shooting % \u2014 accounts for FTs and 3-pointers'),
            ('eFG%', 'Effective field goal % \u2014 adjusts for 3-pointers'),
        ]),
        ('Team Efficiency', [
            ('ORtg', 'Offensive rating \u2014 points scored per 100 possessions'),
            ('DRtg', 'Defensive rating \u2014 points allowed per 100 possessions'),
            ('NRtg', 'Net rating (ORtg \u2212 DRtg)'),
            ('Pace', 'Possessions per 40 minutes, adjusted for overtime'),
            ('eFG%', 'Effective field goal % \u2014 adjusts for 3-pointers being worth more'),
            ('TOV%', 'Turnover % \u2014 turnovers per possession'),
            ('ORB%', 'Offensive rebound % \u2014 share of available offensive rebounds'),
            ('DRB%', 'Defensive rebound % \u2014 share of available defensive rebounds'),
            ('FT/FGA', 'Free throws made per field goal attempted'),
        ]),
        ('Team Totals & Players', [
            ('GP', 'Games played'), ('MPG', 'Minutes per game'),
            ('PPG', 'Points per game'),
            ('FG', 'Field goals (made/attempted)'), ('FG%', 'Field goal percentage'),
            ('3PT', 'Three-point field goals (made/attempted)'),
            ('3PT%', 'Three-point percentage'),
            ('FT', 'Free throws (made/attempted)'), ('FT%', 'Free throw percentage'),
            ('OR', 'Offensive rebounds'), ('DR', 'Defensive rebounds'),
            ('TR', 'Total rebounds'), ('A', 'Assists'), ('ST', 'Steals'),
            ('B', 'Blocks'), ('TO', 'Turnovers'), ('PF', 'Personal fouls'),
        ]),
    ]
    html = '<div id="abbreviations" class="section">\n<h2>Abbreviations</h2>\n'
    for group_name, items in groups:
        html += f'<h3 class="abbrev-group">{esc(group_name)}</h3>\n<dl class="abbrev-list">\n'
        for abbr, desc in items:
            html += f'  <dt>{esc(abbr)}</dt><dd>{desc}</dd>\n'
        html += '</dl>\n'
    html += '</div>\n'
    return html


# ── Cross-site link to the WWC companion site ───────────────────

# The WNBA side of the bridge. Until 2026-08-31 the cross-linking ran ONE WAY:
# the WWC site sent 86 links to WNBA player pages and nothing came back, which
# is why those links carry target="_blank" — a new tab was the only return
# path that existed. This is the real return path.
#
# Deliberately data-free: no read of wwc2026_teams.json, no per-player logic.
# The per-player reciprocal link ("playing at the World Cup for 🇺🇸 USA") is a
# separate, larger backlog item that wires the WWC data into the player-page
# build. This one is most of the value for a fraction of the work.
#
# target="_blank" rel="noopener" mirrors CROSS_SITE in build_wwc_pages.py so
# the two sites behave symmetrically. The constant is duplicated rather than
# shared because sites never import each other — only core/ is shared — and
# promoting it to core/sag would re-render BOTH sites for a 40-byte string.
CROSS_SITE = 'target="_blank" rel="noopener"'

# Group play opens Sep 4; the final is Sep 13 (sites/wwc/reference/
# wwc_schedule_2026.csv, 36 games). The banner hides itself the day after,
# because "Sep 4–13" left up in December is a defect, not a link. Removing
# the gate is a deliberate act — decide what it should say afterwards first.
WWC_LAST_DAY = date(2026, 9, 13)


def wwc_promo_html(today):
    """The cross-site banner, or '' once the Cup is over. `today` comes from
    today_et(), so SAG_TODAY drives it and the golden harness stays
    deterministic."""
    if today > WWC_LAST_DAY:
        return ''
    return (
        '<div class="xsite">'
        f'<a href="https://wwc.statsataglance.com/" {CROSS_SITE}>'
        'FIBA Women\'s Basketball World Cup 2026</a>'
        ' \u2014 Sep 4\u201313. Our companion site: schedule, groups, '
        'and a guide to the 16 teams.'
        '</div>\n'
    )


# ── Games tab builder ────────────────────────────────────────────────────

def _dow(d):
    """'Sat, Jun 21' from a date string or date object."""
    return pd.to_datetime(d).strftime('%a, %b %-d')


def _fmt_min(m):
    try:    return str(int(round(float(m))))
    except Exception: return '0'


# Keyed on the official WNBA TLAs (mapped from ESPN codes at fetch time —
# see WNBA_TLA in fetch_data.py). A missing key degrades to showing the raw
# code on the Games tab, so keep this in sync with any TLA change.
_CITY = {
    'ATL': 'Atlanta', 'CHI': 'Chicago', 'CON': 'Connecticut', 'DAL': 'Dallas',
    'GSV': 'Golden State', 'IND': 'Indiana', 'LAS': 'Los Angeles', 'LVA': 'Las Vegas',
    'MIN': 'Minnesota', 'NYL': 'New York', 'PHX': 'Phoenix', 'PDX': 'Portland',
    'SEA': 'Seattle', 'TOR': 'Toronto', 'WAS': 'Washington',
}


def _game_ma(m, a):
    return '%d/%d' % (int(m), int(a))


def _game_pct(m, a):
    return ('%.1f%%' % (100.0 * m / a)) if a else '\u2014'


def _record_through(team_raw, abbr, date):
    """W-L record for a team through a given date."""
    t = team_raw[(team_raw['team_abbreviation'] == abbr) & (team_raw['game_date'] <= date)]
    return (int((t['team_score'] > t['opponent_team_score']).sum()),
            int((t['team_score'] < t['opponent_team_score']).sum()))


def _game_sides(player_raw, team_raw, gid, date):
    """Return (away_meta, home_meta) dicts for a game."""
    g = player_raw[player_raw['game_id'] == gid]
    a = g[g['home_away'] == 'away'].iloc[0]
    h = g[g['home_away'] == 'home'].iloc[0]
    def meta(r):
        w, l = _record_through(team_raw, r['team_abbreviation'], date)
        return dict(abbr=r['team_abbreviation'], name=r['team_display_name'],
                    score=int(r['team_score']), w=w, l=l)
    return meta(a), meta(h)


def _team_totals(g, abbr):
    """Sum player stats for one team in a game (played players only)."""
    t = g[(g['team_abbreviation'] == abbr) & (g['did_not_play'] != True) & (g['minutes'].notna())]
    s = lambda c: int(t[c].sum())
    return dict(fg=(s('field_goals_made'), s('field_goals_attempted')),
                tp=(s('three_point_field_goals_made'), s('three_point_field_goals_attempted')),
                ft=(s('free_throws_made'), s('free_throws_attempted')),
                tr=s('rebounds'), orb=s('offensive_rebounds'),
                a=s('assists'), to=s('turnovers'), pf=s('fouls'))


_TS_COLS = ['FG', '3PT', 'FT', 'TR/OR', 'A', 'TO', 'PF']
_STAT_COLS = ['MIN', 'PTS', 'FG', '3PT', 'FT', 'R', 'OR', 'A', 'S', 'B', 'TO', 'PF', '+/\u2212']


def _team_stats_block(g, away, home):
    """Team comparison block (FG, 3PT, FT with percentages, etc.)."""
    head = '<tr class="gm-cols"><th class="gm-pl"></th>' + ''.join('<th>%s</th>' % c for c in _TS_COLS) + '</tr>'
    rows = ''
    for m in (away, home):
        t = _team_totals(g, m['abbr'])
        vals = [_game_ma(*t['fg']), _game_ma(*t['tp']), _game_ma(*t['ft']),
                '%d/%d' % (t['tr'], t['orb']), str(t['a']), str(t['to']), str(t['pf'])]
        pcts = [_game_pct(*t['fg']), _game_pct(*t['tp']), _game_pct(*t['ft']), '', '', '', '']
        rows += ('<tr class="gm-v"><td class="gm-pl">%s</td>%s</tr>'
                 '<tr class="gm-p"><td class="gm-pl"></td>%s</tr>'
                 % (m['abbr'], ''.join('<td>%s</td>' % v for v in vals),
                    ''.join('<td>%s</td>' % p for p in pcts)))
    return '<table class="gm-ts">%s%s</table>' % (head, rows)


def _player_row(r):
    """One player row in the box score."""
    pm = r['plus_minus']
    pm_s = ('+%d' % int(pm)) if pd.notna(pm) and pm > 0 else ('%d' % int(pm) if pd.notna(pm) else '\u2014')
    pos = '' if pd.isna(r['athlete_position_abbreviation']) else r['athlete_position_abbreviation']
    cells = [_fmt_min(r['minutes']), '<b>%d</b>' % int(r['points']),
             _game_ma(r['field_goals_made'], r['field_goals_attempted']),
             _game_ma(r['three_point_field_goals_made'], r['three_point_field_goals_attempted']),
             _game_ma(r['free_throws_made'], r['free_throws_attempted']),
             str(int(r['rebounds'])), str(int(r['offensive_rebounds'])), str(int(r['assists'])),
             str(int(r['steals'])), str(int(r['blocks'])), str(int(r['turnovers'])),
             str(int(r['fouls'])), pm_s]
    return ('<tr><td class="gm-pl"><span class="gm-pos">%s</span> %s</td>%s</tr>'
            % (pos, short_name(r['athlete_display_name']),
               ''.join('<td>%s</td>' % c for c in cells)))


def _team_table(g, meta):
    """Full box score table for one team."""
    t = g[g['team_abbreviation'] == meta['abbr']].copy()
    played = t[(t['did_not_play'] != True) & (t['minutes'].notna())]
    starters = played[played['starter'] == True].sort_values('minutes', ascending=False)
    bench = played[played['starter'] != True].sort_values('minutes', ascending=False)
    head = '<tr class="gm-cols"><th class="gm-pl"></th>%s</tr>' % ''.join(
        '<th>%s</th>' % c for c in _STAT_COLS)
    def section(label, rows):
        if rows.empty: return ''
        return ('<tr class="gm-sec"><td class="gm-pl" colspan="%d">%s</td></tr>%s'
                % (len(_STAT_COLS)+1, label,
                   ''.join(_player_row(r) for _, r in rows.iterrows())))
    cap = '<div class="gm-tcap">%s <span class="gm-rec">%d-%d</span></div>' % (
        meta['name'], meta['w'], meta['l'])
    return ('%s<div class="gm-tscroll"><div class="gm-tw"><table class="gm-bx">%s%s%s</table></div></div>'
            % (cap, head, section('STARTERS', starters), section('BENCH', bench)))


def _line_score(linescores, gid, away, home):
    """Quarter-by-quarter line score from ESPN's OFFICIAL per-team linescores.

    `linescores` is the dict loaded from sites/wnba/data/linescores_2026.json, keyed by
    str(game_id) -> {"home_abbr","away_abbr","home":[...],"away":[...]}.

    Correct-or-blank policy: we never reconstruct quarters from play-by-play.
    If the official line score is missing or fails an integrity check (quarters
    must sum to the team's final total), return '' so the box score simply omits
    the quarter columns rather than showing values that are likely wrong.
    """
    if not linescores:
        return ''
    ls = linescores.get(str(gid))
    if not ls:
        return ''
    lh = ls.get('home') or []
    la = ls.get('away') or []
    if not lh or not la or len(lh) != len(la):
        return ''
    # Integrity guard: official quarters must reconcile to the final totals.
    if sum(lh) != home['score'] or sum(la) != away['score']:
        return ''

    nper = len(lh)
    def period_label(i):
        p = i + 1
        if p <= 4:
            return str(p)
        return 'OT' if nper == 5 else f'OT{p - 4}'
    qhdr = ''.join(f'<th>{period_label(i)}</th>' for i in range(nper)) + '<th class="gm-t">T</th>'
    L = {ls.get('home_abbr'): lh, ls.get('away_abbr'): la}
    def row(m):
        scores = L.get(m['abbr'], [])
        return ('<tr><td class="gm-pl">%s</td>%s<td class="gm-t"><b>%d</b></td></tr>'
                % (m['abbr'], ''.join('<td>%d</td>' % x for x in scores), m['score']))
    return ('<table class="gm-ls"><tr class="gm-cols"><th class="gm-pl"></th>%s</tr>%s%s</table>'
            % (qhdr, row(away), row(home)))


def _box_section(player_raw, team_raw, linescores, gid, date):
    """Full box score section for one game (hidden by default)."""
    g = player_raw[player_raw['game_id'] == gid].copy()
    away, home = _game_sides(player_raw, team_raw, gid, date)
    winner = home if home['score'] > away['score'] else away
    def hd(m):
        cls = ' gm-win' if m is winner else ''
        return ('<div><span class="gm-tm%s">%s</span> <span class="gm-rec">%d-%d</span></div>'
                '<div class="gm-sc%s">%d</div>' % (cls, m['abbr'], m['w'], m['l'], cls, m['score']))
    return ('<section class="gm-box" id="g%d" style="display:none">'
            '<div class="gm-back" onclick="backToGames()">\u2190 Back to Games</div>'
            '<div class="gm-hd">%s<div class="gm-fin">Final</div>%s</div>'
            '<div class="gm-meta">%s</div>%s'
            '<h2 class="gm-h2">Team Stats</h2>%s'
            '<h2 class="gm-h2">Box Score</h2>%s%s</section>'
            % (gid, hd(away), hd(home), _dow(date), _line_score(linescores, gid, away, home),
               _team_stats_block(g, away, home), _team_table(g, away), _team_table(g, home)))


def _result_row(player_raw, team_raw, gid, date):
    """One result row in the games list (clickable)."""
    away, home = _game_sides(player_raw, team_raw, gid, date)
    aw = ' gm-win' if away['score'] > home['score'] else ''
    hw = ' gm-win' if home['score'] > away['score'] else ''
    return ('<div class="gm-row gm-result" onclick="showGame(%d)">'
            '<span class="gm-match"><span class="gm-s%s">%s %d</span> '
            '<span class="gm-dash">\u2013</span> '
            '<span class="gm-s%s">%s %d</span></span>'
            '<span class="gm-chev">\u203A</span></div>'
            % (gid, aw, _CITY.get(away['abbr'], away['abbr']), away['score'],
               hw, _CITY.get(home['abbr'], home['abbr']), home['score']))


def _sched_row(away, home, tip_et):
    """One schedule row (upcoming game)."""
    return ('<div class="gm-row gm-sched"><span class="gm-match">%s vs %s</span>'
            '<span class="gm-when">%s</span></div>'
            % (esc(_CITY.get(away, away)), esc(_CITY.get(home, home)), esc(tip_et)))


def game_slug(date_iso, away_abbr, home_abbr, team_names):
    """/games/YYYY-MM-DD-<away>-<home>/ — the ONE place this URL is formed.

    Lives here rather than in build_box_pages so the Playoffs tab (which links
    to these pages) and the emitter (which writes them) cannot disagree — the
    same guarantee team_href() gives for team pages. Fixture order, away
    first, per the score-orientation rule: naming BOTH teams takes fixture
    order. Full team names rather than TLAs because the URL is a search
    target — the query is "atlanta dream minnesota lynx box score".
    """
    a = seo.slugify(team_names.get(away_abbr, away_abbr))
    h = seo.slugify(team_names.get(home_abbr, home_abbr))
    return f"{date_iso}-{a}-{h}"


def load_series():
    """Per-game playoff-series rows, or [] outside the playoffs.

    Absent for the whole regular season, which is the correct content for a
    regular-season day rather than an error.
    """
    if not WNBA.series.exists():
        return []
    try:
        return json.loads(WNBA.series.read_text()).get('games', [])
    except Exception as e:
        print(f"WARNING: {WNBA.series.name} unreadable ({e}) — series state omitted.")
        return []


def load_upcoming():
    """(status, games) from schedule_upcoming.json. Status "ok" means the
    window is complete; anything else means we do not know the whole schedule
    and must not say "nothing scheduled" (the 2026-08-05 rule)."""
    p = WNBA.schedule_upcoming
    if not p.exists():
        return 'unavailable', []
    try:
        d = json.loads(p.read_text())
        return d.get('status', 'unavailable'), d.get('games', [])
    except Exception:
        return 'unavailable', []


def load_seeds():
    """{team_id: seed} and {TLA: seed} from the frozen seeds file, or two
    empty dicts. Correct-or-blank: no file, no `(n)` anywhere — never a seed
    worked out from our own standings."""
    if not SEEDS.exists():
        return {}, {}, []
    try:
        teams = json.loads(SEEDS.read_text()).get('teams', [])
    except Exception as e:
        print(f"WARNING: {SEEDS.name} unreadable ({e}) — no seeds shown.")
        return {}, {}, []
    return ({int(t['team_id']): int(t['seed']) for t in teams},
            {t['abbr']: int(t['seed']) for t in teams}, teams)


def playoffs_active(upcoming_games=None, series_rows=None):
    """Is it the postseason, as far as the DATA says?

    True as soon as any season-type-3 event is in the schedule window — not
    when the first one is final, or the site would still say "Games" on the
    morning of the biggest day of the year — or once any playoff game has
    been played. Never read from the clock. It goes back to False when the
    season rolls over, because every file this reads is season-keyed.
    """
    if upcoming_games is None:
        _, upcoming_games = load_upcoming()
    if series_rows is None:
        series_rows = load_series()
    return bool(series_rows) or any(
        g.get('season_type') == 3 for g in upcoming_games)


_NAV = None


def nav_tabs():
    """The subpage strip for this build — Playoffs or Games first — read once
    and reused by every player, team and index page the build writes."""
    global _NAV
    if _NAV is None:
        _NAV = subpage_tabs(playoffs_active())
    return _NAV


#: ESPN's network names -> what we print. Blank when ESPN gives none.
TV_DISPLAY = {'USA Net': 'USA'}

#: Which games the higher seed hosts, by series length. Games 1 and 3 in a
#: best of 3; 1, 2, 5 in a best of 5; 1, 2, 5, 7 in a best of 7.
HOSTS = {3: 'Games 1 and 3', 5: 'Games 1, 2 and 5', 7: 'Games 1, 2, 5 and 7'}

_GAME_NO = re.compile(r'\bgame\s+(\d+)', re.I)


def _tv(names):
    return ' / '.join(TV_DISPLAY.get(n, n) for n in names or [])


def _tip(g):
    """Tip time without the " ET" suffix — the format note says every time
    is US Eastern once, so the rows need not repeat it."""
    return (g.get('tip_et') or '').replace(' ET', '')


def _round_of(headline):
    """"First Round - Game 3 If Necessary" -> "First Round". Display only:
    ESPN's capitalisation drifts within a series ("WNBA FINALS - Game 3"),
    so this is never matched on."""
    return (headline or '').split(' - ')[0].strip()


def playoff_box_games(player_all, team_all):
    """[(game_id, date, away, home)] for every playoff game whose box-score
    page build_box_pages WILL write, oldest first.

    The one list both sides read: build_box_pages writes a page for exactly
    these, and the Playoffs tab links a score only when its id is in here.
    That is WWC's `box_ids` rule — a final score must never point at a 404,
    and a result and its box score can arrive from different places.
    Gated on season_type == 3 in the box data, never on a `series` object.
    """
    if 'season_type' not in team_all.columns:
        return []
    post = team_all[team_all['season_type'] == 3]
    out = []
    for gid, date_iso in sorted({(int(r['game_id']), str(r['game_date']))
                                 for _, r in post.iterrows()},
                                key=lambda t: (t[1], t[0])):
        try:
            away, home = _game_sides(player_all, team_all, gid, date_iso)
        except (IndexError, KeyError):
            continue  # correct-or-blank: no orientation, no page, no link
        out.append((gid, date_iso, away, home))
    return out


def _real_team(team_id, abbr):
    """False for ESPN's to-be-determined placeholders (id <= 0, "TBD")."""
    try:
        if team_id is None or int(team_id) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    return bool(abbr) and abbr.upper() != 'TBD'


def build_playoff_model(series_rows, upcoming_games, player_all, team_all,
                        seeds_by_id, box_ids):
    """Every playoff series, from the SCHEDULE plus the RESULTS.

    Unplayed games come from schedule_upcoming.json, played ones from our box
    scores and series_2026.json; the two are joined on the ESPN event id and
    never on a date or a time. A series is the unordered pair of team ids —
    ESPN's series object carries no id of its own.

    Returns a list of series dicts, each with its games sorted by game number.
    Semifinals and Finals appear only when ESPN publishes those events: there
    is no bracket derivation and no "winner of" row (WWC scope rule,
    2026-09-03).
    """
    names = (team_all.drop_duplicates('team_abbreviation')
             .set_index('team_abbreviation')['team_display_name'].to_dict())
    abbr_by_id = {int(r['team_id']): r['team_abbreviation']
                  for _, r in team_all.drop_duplicates('team_id').iterrows()}

    games = {}
    for u in upcoming_games:
        if u.get('season_type') != 3 or not u.get('event_id'):
            continue
        # A postponed or cancelled game is not a game to list.
        if any(x in (u.get('status') or '') for x in ('CANCEL', 'POSTPONE')):
            continue
        # ESPN publishes later rounds before their teams are known: on
        # 2026-09-25 all ten semifinal games were already on the scoreboard
        # as "TBD @ TBD" with placeholder team ids -1 and -2. Listing them —
        # or grouping them, which made a fifth "series" out of two
        # placeholders — is bracket derivation by another name. A game
        # appears once ESPN names both teams, and not before.
        if (not _real_team(u.get('away_id'), u.get('away'))
                or not _real_team(u.get('home_id'), u.get('home'))):
            continue
        for side in ('away', 'home'):
            if u.get(f'{side}_id') is not None:
                abbr_by_id.setdefault(int(u[f'{side}_id']), u[side])
        games[int(u['event_id'])] = {
            'id': int(u['event_id']), 'date': u.get('date', ''),
            'away': u.get('away', ''), 'home': u.get('home', ''),
            'ids': frozenset(int(u[k]) for k in ('away_id', 'home_id')
                             if u.get(k) is not None),
            'tip': _tip(u), 'tbd': bool(u.get('tbd')), 'tv': _tv(u.get('broadcasts')),
            'state': u.get('state', 'pre'), 'headline': u.get('headline', ''),
            'summary': u.get('series_summary', ''), 'total': u.get('series_total'),
            'score': None, 'linked': False, 'final': u.get('state') == 'post',
        }

    for r in series_rows:
        gid = int(r['game_id'])
        g = games.setdefault(gid, {
            'id': gid, 'date': r['game_date'], 'away': '', 'home': '',
            'tip': '', 'tbd': False, 'tv': '', 'state': 'post', 'score': None,
            'linked': False})
        g.update(final=True, state='post', date=r['game_date'],
                 ids=frozenset(int(c['team_id']) for c in r['competitors']),
                 headline=r.get('headline') or g.get('headline', ''),
                 summary=r.get('summary', ''), total=r.get('total_competitions'),
                 completed=bool(r.get('completed')))

    for gid, date_iso, away, home in playoff_box_games(player_all, team_all):
        g = games.get(gid)
        if g is None:
            continue  # a box score with no series row or schedule entry
        g.update(away=away['abbr'], home=home['abbr'], date=date_iso,
                 score=(away['score'], home['score']), final=True, state='post',
                 linked=gid in box_ids,
                 slug=game_slug(date_iso, away['abbr'], home['abbr'], names))

    groups = {}
    for g in games.values():
        if len(g.get('ids') or ()) == 2:
            groups.setdefault(g['ids'], []).append(g)

    series = []
    for ids, gs in groups.items():
        for i, g in enumerate(sorted(gs, key=lambda g: (g['date'], g['id']))):
            m = _GAME_NO.search(g.get('headline') or '')
            g['no'] = int(m.group(1)) if m else i + 1
            g['if_necessary'] = 'if necessary' in (g.get('headline') or '').lower()
            # Orientation unknown: a final whose box score has not arrived
            # and that is outside the schedule window. Name both teams without
            # claiming who was home.
            if not g['away'] or not g['home']:
                g['away'], g['home'] = sorted(abbr_by_id.get(i, str(i)) for i in ids)
                g['vs'] = True
        gs.sort(key=lambda g: (g['no'], g['date']))
        played = [g for g in gs if g.get('final') and 'completed' in g]
        last = played[-1] if played else None
        completed = bool(last and last['completed'])
        # A finished series keeps only the games that were played: ESPN
        # deletes an unneeded "if necessary" game (2025's MIN-GS Game 3
        # simply vanished), and a stale copy must not linger here.
        if completed:
            gs = [g for g in gs if g.get('final')]
        # State as of the most recent game played; before any, the schedule's
        # own wording ("Series starts 9/27").
        summary = (last or {}).get('summary') or next(
            (g['summary'] for g in gs if g.get('summary')), '')
        total = next((g['total'] for g in reversed(gs) if g.get('total')), None)
        tlas = {i: abbr_by_id.get(i, str(i)) for i in ids}
        # Higher seed first. With no seeds file, the Game 1 host leads — an
        # ORDER only; no number is printed, so nothing is claimed.
        if seeds_by_id and all(i in seeds_by_id for i in ids):
            order = sorted(ids, key=lambda i: seeds_by_id[i])
            best = seeds_by_id[order[0]]
        else:
            host = next((g['home'] for g in gs if not g.get('vs')), None)
            order = sorted(ids, key=lambda i: tlas[i] != host)
            best = None
        series.append({
            'ids': ids, 'order': order, 'tlas': tlas,
            'names': {i: names.get(tlas[i], tlas[i]) for i in ids},
            'seeds': {i: seeds_by_id.get(i) for i in ids} if best else {},
            'best_seed': best, 'round': _round_of(gs[0].get('headline')),
            'start': min(g['date'] for g in gs), 'games': gs,
            'summary': summary, 'total': total, 'completed': completed,
        })
    return series


def _rounds(series):
    """[(round name, [series])], newest round first, series in seed order
    (1v8, 2v7, 3v6, 4v5 — keyed on the higher seed), else by first game."""
    by_round = {}
    for s in series:
        by_round.setdefault(s['round'], []).append(s)
    out = []
    for name, ss in by_round.items():
        ss.sort(key=lambda s: (s['best_seed'] if s['best_seed'] is not None
                               else 99, s['start'], min(s['ids'])))
        out.append((name, ss))
    out.sort(key=lambda kv: min(s['start'] for s in kv[1]), reverse=True)
    return out


def _score_html(g):
    """`NYL 80 – MIN 85`, fixture order (it names both teams), winner bold."""
    a, h = g['score']
    aw = ' po-w' if a > h else ''
    hw = ' po-w' if h > a else ''
    return (f'<span class="po-sc{aw}">{esc(g["away"])} {a}</span>'
            f'<span class="po-dash"> &ndash; </span>'
            f'<span class="po-sc{hw}">{esc(g["home"])} {h}</span>')


def _linked(g, inner):
    if g.get('linked') and g.get('slug'):
        return (f'<a class="po-a" href="/games/{g["slug"]}/">{inner} '
                f'<span class="po-arr">&rarr;</span></a>')
    return inner


def _matchup(g):
    """`NYL @ MIN` in fixture order, or `MIN vs NYL` when home is unknown."""
    sep = ' vs ' if g.get('vs') else ' @ '
    return f'{esc(g["away"])}{sep}{esc(g["home"])}'


def _series_game_row(g):
    """One game inside a series: played, scheduled, live or unscheduled."""
    day = _dow(g['date']) if g.get('date') else ''
    if g.get('final'):
        what = (_linked(g, _score_html(g)) if g.get('score')
                else _matchup(g))
        right = '<span class="po-mu">Final</span>'
    elif g.get('state') == 'in':
        what = _matchup(g)
        right = '<span class="po-mu">In progress</span>'
    elif g.get('tbd'):
        what = (_matchup(g)
                + (' &middot; if necessary' if g.get('if_necessary') else ''))
        right = '<span class="po-mu">TBD</span>'
        return (f'<div class="po-gr po-ifn"><span class="po-no">Gm {g["no"]}</span>'
                f'<span class="po-what">{esc(day)} &middot; {what}</span>'
                f'<span class="po-when">{right}</span></div>')
    else:
        what = (_matchup(g)
                + (' &middot; if necessary' if g.get('if_necessary') else ''))
        right = esc(g.get('tip') or '')
    return (f'<div class="po-gr"><span class="po-no">Gm {g["no"]}</span>'
            f'<span class="po-what">{esc(day)} &middot; {what}</span>'
            f'<span class="po-when">{right}</span></div>')


def _series_block(s, newest_first):
    def team(i):
        sd = s['seeds'].get(i)
        return (esc(s['names'][i])
                + (f' <span class="po-sd">({sd})</span>' if sd else ''))
    head = ' vs '.join(team(i) for i in s['order'])
    meta = f' <span class="po-mu">&middot; best of {s["total"]}</span>' if s['total'] else ''
    games = list(reversed(s['games'])) if newest_first else s['games']
    return (f'<div class="po-ser"><div class="po-t">{head}</div>'
            f'<div class="po-s">{esc(s["summary"])}{meta}</div>'
            + ''.join(_series_game_row(g) for g in games) + '</div>')


def build_playoffs_section(series, upcoming_status):
    """The first tab during the postseason, top to bottom (Jason, 2026-09-25):
    format note, next games, latest results, then every series.

    Same section id as Games (`games`), so every existing `/#games` link and
    the box pages' back link keep working; `#playoffs` is an alias.
    """
    all_games = [g for s in series for g in s['games']]
    unplayed = [g for s in series if not s['completed'] for g in s['games']
                if not g.get('final')]
    finished = bool(series) and not unplayed and all(s['completed'] for s in series)
    rounds = _rounds(series)
    out = []

    # 1. Format note, for the round being played now. Says nothing about
    #    reseeding or bracket paths: the build does not need to know, and
    #    Jason is not sure the W uses a fixed bracket.
    if not finished and rounds:
        live = next((ss for _, ss in rounds
                     if any(not s['completed'] for s in ss)), rounds[0][1])
        n = next((s['total'] for s in live if s['total']), None)
        bits = ['Eight teams, seeded 1&ndash;8.']
        if n:
            bits.append(f'This round is best of {n}'
                        + (f'; the higher seed hosts {HOSTS[n]}.' if n in HOSTS else '.'))
        bits.append('Times are US Eastern.')
        out.append(f'<p class="tab-note"><em>{" ".join(bits)}</em></p>')

    # 2. Next games — the earliest date still holding an unplayed game, from
    #    the data, never the clock (WWC's next_games_day()).
    nxt = min((g['date'] for g in unplayed if g.get('date')), default=None)
    if nxt:
        rows = ''
        for g in sorted((g for g in unplayed if g['date'] == nxt),
                        key=lambda g: (g.get('tbd', False), _tip_minutes(g), g['id'])):
            when = ('TBD' if g.get('tbd') else
                    'In progress' if g.get('state') == 'in' else esc(g.get('tip') or ''))
            tv = f'<span class="po-tv">{esc(g["tv"])}</span>' if g.get('tv') else ''
            ifn = ' &middot; if necessary' if g.get('if_necessary') else ''
            rows += (f'<div class="gm-row po-row"><span class="gm-match">'
                     f'{_matchup(g)} '
                     f'<span class="po-mu">&middot; Gm {g["no"]}{ifn}</span></span>'
                     f'<span class="po-when">{when}{tv}</span></div>')
        out.append(f'<div class="gm-daybar">Next games &middot; {esc(_dow(nxt))}</div>{rows}')
    elif upcoming_status != 'ok' and not finished:
        out.append('<div class="gm-daybar">Next games</div>'
                   '<div class="gm-empty">The upcoming schedule is unavailable.</div>')

    # 3. Latest results — the most recent day with finals. Hidden before one.
    played = [g for g in all_games if g.get('final') and g.get('score')]
    if played:
        last = max(g['date'] for g in played)
        rows = ''.join(
            f'<div class="gm-row po-row"><span class="gm-match">'
            f'{_linked(g, _score_html(g))}</span>'
            f'<span class="po-when po-mu">Final</span></div>'
            for g in sorted((g for g in played if g['date'] == last),
                            key=lambda g: g['id']))
        hint = ('<div class="gm-hint">Tap a score for the full box score.</div>'
                if any(g.get('linked') for g in played) else '')
        out.append(f'<div class="gm-daybar">Latest results &middot; '
                   f'{esc(_dow(last))}</div>{rows}{hint}')

    # 4. Every series. Newest round first, so the round being played leads;
    #    once the Finals are over the games flip newest-first too, so the page
    #    opens on the Finals result (WWC archive order).
    for name, ss in rounds:
        out.append(f'<h2>{esc(name) or "Playoffs"}</h2>'
                   + ''.join(_series_block(s, finished) for s in ss))

    return ('<div id="games" class="section active">\n'
            '<section id="games-view">' + ''.join(out) + '</section>'
            '</div>\n')


def _tip_minutes(g):
    t = g.get('tip') or ''
    try:
        d = datetime.strptime(t, '%I:%M %p')
        return d.hour * 60 + d.minute
    except ValueError:
        return 0


def build_games_section(player_raw, team_raw):
    """Build the Games tab: today's schedule + yesterday's results with
    inline box scores. Returns HTML string.

    Takes the ALL-GAMES frames from load_all_games(), not load_data()'s
    regular-season ones — a playoff game is still a game that needs a box
    score. Passing the regular-season frame here renders the day's matchups
    with nothing underneath them."""
    today = today_et()
    yest_et = today - timedelta(days=1)

    # Load today's schedule from JSON (written by fetch_data.py).
    #
    # An empty games list is only meaningful alongside status == "ok". If the
    # fetch failed (status "unavailable") — or the file is missing/unreadable,
    # which is the same epistemic situation — we do NOT know today's slate and
    # must not claim there are no games. Older files predate the status field;
    # treat those as "ok" for backward compatibility.
    sched_path = WNBA.schedule_today
    sched_games = []
    sched_known = False
    if sched_path.exists():
        try:
            sched_data = json.loads(sched_path.read_text())
            sched_games = sched_data.get('games', [])
            sched_known = sched_data.get('status', 'ok') == 'ok'
        except Exception:
            pass

    # Yesterday's completed games from box score data
    yest_ids = sorted(player_raw[player_raw['game_date'] == str(yest_et)]['game_id'].unique())

    # Load OFFICIAL line scores for box-score quarter columns (only if results).
    # Correct-or-blank: if this file is missing/unreadable, _line_score omits
    # the quarter columns rather than guessing from play-by-play.
    linescores = None
    if yest_ids:
        ls_path = WNBA.linescores
        if ls_path.exists():
            try:
                linescores = json.loads(ls_path.read_text())
            except Exception:
                pass

    # Build schedule rows
    if sched_games:
        sched_html = ''.join(
            _sched_row(g['away'], g['home'], g['tip_et'])
            for g in sched_games
        )
    elif sched_known:
        sched_html = '<div class="gm-empty">No games today.</div>'
    else:
        sched_html = ("<div class=\"gm-empty\">Today's schedule is "
                      "unavailable.</div>")

    # Build result rows + box sections
    if yest_ids:
        results_html = ''.join(
            _result_row(player_raw, team_raw, gid, str(yest_et))
            for gid in yest_ids
        )
        results_html += '<div class="gm-hint">Tap a final score to open its box score.</div>'
        box_html = ''.join(
            _box_section(player_raw, team_raw, linescores, gid, str(yest_et))
            for gid in yest_ids
        )
    else:
        results_html = '<div class="gm-empty">No games yesterday.</div>'
        box_html = ''

    return (
        '<div id="games" class="section active">\n'
        '<section id="games-view">'
        '<div class="gm-daybar">Today \u00B7 %s</div>'
        '%s'
        '<div class="gm-daybar">%s</div>'
        '%s'
        '</section>'
        '%s'
        '</div>\n'
        % (_dow(today), sched_html, _dow(yest_et), results_html, box_html)
    )


# ── CSS & JS ──────────────────────────────────────────────────────────────

# Assembled from the shared chrome (sag.render.chrome) plus this page's own
# styles, in the order the monolithic block always had — golden_check.py
# holds the rendered page byte-identical through this split.

# ── Type stacks (variant B, 2026-09-08) ──────────────────────────────────
# Byte-identical to sites/wwc/build_wwc_pages.py's SANS/MONO. The two sites
# are one publication with different accents, so the type stack is shared by
# value even though it is declared twice — `font-family` deliberately lives
# per-emitter rather than in `sag.render.chrome`, which is exactly why this
# change costs `core/` nothing and cannot move WWC's bytes.
#
# The split: sans for anything read as language (prose, nav, names, headings),
# mono for anything read as a quantity. Mono is scoped structurally rather than
# by class — every table on this site puts the entity in the first column and
# numbers in the rest, so `tbody td:not(:first-child)` covers all of them
# without touching a single table generator. The Key tab is a <dl>, not a
# table, so it stays prose automatically; that was the round-2 mock's sharpest
# finding about the old all-mono design.
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"
SANS = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        "'Helvetica Neue',Arial,sans-serif")

# The Games-tab styles, extracted 2026-09-08 so the standalone playoff
# box-score pages (build_box_pages.py) can reuse the SAME rules that style
# the inline box scores. One source of truth: a change here moves both, and
# the two renderings cannot drift apart. Spliced back into PAGE_CSS at the
# position it always occupied, so the tab site's bytes do not move —
# golden_check proves it.
GAMES_CSS = f"""\
  /* ── Games tab ── */
  .gm-daybar{{color:var(--accent);font-size:11px;letter-spacing:.08em;text-transform:uppercase;
    border-bottom:1px solid var(--border);padding:10px 0 5px;margin-top:6px}}
  .gm-daybar:first-child{{margin-top:0}}
  .gm-row{{display:flex;align-items:center;justify-content:space-between;gap:10px;
    padding:11px 2px;border-bottom:1px solid #161618}}
  .gm-row .gm-match{{font-size:15px}}
  .gm-row.gm-sched .gm-match{{color:var(--text)}}
  .gm-row.gm-sched .gm-when{{color:var(--muted);font-size:12px}}
  .gm-row.gm-result{{cursor:pointer}}
  .gm-row.gm-result .gm-s{{color:var(--muted)}}
  .gm-row.gm-result .gm-s.gm-win{{color:var(--text);font-weight:700}}
  .gm-row .gm-dash{{color:var(--muted)}}
  .gm-row .gm-chev{{color:var(--muted);font-size:18px}}
  .gm-row.gm-result:active{{background:var(--surface)}}
  .gm-hint{{color:var(--muted);font-size:11px;margin-top:14px}}
  .gm-empty{{color:var(--muted);font-size:12px;padding:14px 2px}}
  .gm-back{{color:var(--accent);font-size:13px;padding:4px 0 12px;cursor:pointer;display:inline-block}}
  .gm-hd{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:2px}}
  .gm-hd .gm-tm{{font-size:15px;font-weight:700}}
  .gm-hd .gm-sc{{font-size:22px;font-weight:700}}
  .gm-hd .gm-rec{{color:var(--muted);font-weight:400;font-size:12px}}
  .gm-fin{{color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-size:11px;text-align:center}}
  .gm-win{{color:#7ec27e}}
  .gm-meta{{color:var(--muted);font-size:11px;margin:8px 0 14px}}
  .gm-h2{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;
    border-bottom:1px solid var(--border);padding-bottom:5px;margin:18px 0 6px}}
  .gm-ls{{border-collapse:collapse;width:auto;margin:10px 0 20px;font-size:13px}}
  .gm-ls th,.gm-ls td{{padding:3px 10px;text-align:right}}
  .gm-ls .gm-pl{{text-align:left;color:var(--muted)}}.gm-ls .gm-t{{border-left:1px solid var(--border)}}
  .gm-ts{{border-collapse:collapse;width:auto;margin:4px 0 8px;font-size:12px}}
  .gm-ts th,.gm-ts td{{padding:2px 10px;text-align:right;white-space:nowrap}}
  .gm-ts .gm-pl{{text-align:left}}
  .gm-ts .gm-cols th{{color:var(--muted);font-weight:400;border-bottom:1px solid var(--border)}}
  .gm-ts .gm-v td{{padding-top:7px}}.gm-ts .gm-v .gm-pl{{font-weight:700}}
  .gm-ts .gm-p td{{color:var(--muted);font-size:10px;padding-top:0;padding-bottom:3px}}
  .gm-tcap{{font-weight:700;margin:18px 0 3px}}.gm-tcap .gm-rec{{color:var(--muted);font-weight:400;font-size:12px}}
  .gm-tscroll{{position:relative;margin-bottom:6px}}
  .gm-tscroll::after{{content:"";position:absolute;top:0;bottom:0;right:0;width:26px;
    pointer-events:none;opacity:0;transition:opacity .15s ease;z-index:5;
    background:linear-gradient(to right, rgba(15,15,15,0), var(--bg))}}
  .gm-tscroll.more-right::after{{opacity:1}}
  .gm-tw{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
  .gm-bx{{border-collapse:collapse;min-width:520px;width:100%;font-size:12px}}
  .gm-bx th,.gm-bx td{{padding:3px 6px;text-align:right;white-space:nowrap}}
  .gm-bx .gm-pl{{text-align:left;position:sticky;left:0;background:var(--bg);min-width:88px;padding-left:0}}
  .gm-bx .gm-cols th{{border-bottom:1px solid var(--accent);color:var(--muted);font-weight:400}}
  .gm-bx .gm-sec td{{color:var(--accent);font-size:10px;letter-spacing:.1em;padding-top:9px;text-transform:uppercase}}
  .gm-bx tr:not(.gm-cols):not(.gm-sec) td{{border-top:1px solid #18181b}}
  .gm-pos{{color:var(--muted);font-size:10px;display:inline-block;min-width:18px}}
  /* Games-tab quantities that are NOT `tbody td:not(:first-child)` and so are
     missed by the structural rule above: the big final score, the inline
     result-row scores, W-L records, and the line score's period headers
     (1 2 3 4 T). All read as numbers and take the mono face. Team names,
     the day bar and the "Final" label stay in the sans face. */
  .gm-row.gm-result .gm-s,.gm-hd .gm-sc,.gm-hd .gm-rec,
  .gm-tcap .gm-rec,.gm-ls th{{font-family:{MONO};font-variant-numeric:tabular-nums}}"""


PAGE_CSS = (
    chrome.tokens_css(WNBA.accent)
    + f"""\
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:{SANS};background:var(--bg);color:var(--text);
        font-size:13.5px;padding:16px;line-height:1.55;
        -webkit-font-smoothing:antialiased}}
  /* Quantities read as quantities. tabular-nums on every table keeps columns
     of digits aligned even in the sans face (records, ordinals, dates). */
  table{{font-variant-numeric:tabular-nums}}
  tbody td:not(:first-child){{font-family:{MONO};font-variant-numeric:tabular-nums}}
  h1{{color:var(--accent);font-size:18px;margin-bottom:3px;font-weight:700;
      letter-spacing:-.2px}}
  .meta{{color:var(--muted);font-size:11.5px;margin-bottom:14px;max-width:34em}}
"""
    + """\
  /* Cross-site pointer to wwc.statsataglance.com. See wwc_promo_html(). */
  .xsite{font-size:11px;line-height:1.6;margin-bottom:20px;padding:7px 10px;
        border:1px solid var(--border);border-left:2px solid var(--accent);
        background:var(--surface);color:var(--muted)}
  .xsite a{color:var(--accent);text-decoration:none}
  .xsite a:hover{text-decoration:underline}
"""
    + chrome.SITE_FOOTER_CSS
    + """\
  h2{font-size:11.5px;color:var(--accent);text-transform:uppercase;letter-spacing:.9px;
      margin:24px 0 10px;border-bottom:1px solid var(--border);padding-bottom:5px;
      font-weight:700}
  h2 .sub{color:var(--muted);text-transform:none;letter-spacing:0;font-size:11px;
      font-weight:400}
  .tab-note{color:var(--muted);font-size:11.5px;margin-bottom:10px;max-width:34em}
  .tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px}
  .tab{cursor:pointer;padding:5px 12px;border:1px solid var(--border);
        color:var(--muted);background:var(--surface);font-family:inherit;
        font-size:12px;letter-spacing:.3px;font-weight:500}
  .tab.active{border-color:var(--accent);color:var(--accent)}
  /* Teams and Players are real links, not buttons — they leave the page.
     They must be visually indistinguishable from the buttons beside them,
     so the strip reads as one row of peers rather than as tabs-plus-links. */
  a.tab{text-decoration:none;display:inline-block}
  .section{display:none}.section.active{display:block}
  /* The Stats switch (2026-09-15). Three views behind one tab: Efficiency,
     Team Totals, Player Totals. `.statview` is a SECOND, independent
     show/hide layer nested inside `#stats` — it cannot reuse `.section`,
     because showTab() toggles every `.section` on the page and would fight
     this one. Ids are unchanged (`teameff`, `teamtotals`, `players`), which
     is what makes the old deep links alias for free in openTabFromHash. */
  .statview{display:none}.statview.active{display:block}
  /* Deliberately NOT a second strip of boxed tabs — it has to read as
     subordinate to the tab strip above it, or the page appears to have two
     navigations. Text only, on a rule, active marked by an accent underline
     and full --text weight against --muted siblings. */
  .swrap{display:flex;gap:18px;flex-wrap:wrap;
        border-bottom:1px solid var(--border);margin:0 0 14px}
  .sw{cursor:pointer;background:none;border:0;border-bottom:2px solid transparent;
        color:var(--muted);font-family:inherit;font-size:12.5px;
        letter-spacing:.3px;font-weight:500;padding:6px 0 7px;margin-bottom:-1px}
  .sw:hover{color:var(--text)}
  .sw.active{color:var(--text);border-bottom-color:var(--accent)}
"""
    + chrome.SCROLL_FADE_CSS
    + """\
  table{border-collapse:collapse;width:100%;white-space:nowrap}
  thead tr{background:var(--surface)}
  th{padding:7px 11px;text-align:left;color:var(--muted);font-size:11px;
      letter-spacing:.4px;text-transform:uppercase;font-weight:normal;
      border-bottom:1px solid var(--border);
      cursor:pointer;user-select:none}
  th:hover{color:var(--accent)}
  th.group-header{text-align:center;color:var(--accent);border-bottom:1px solid var(--border);
                   border-left:1px solid var(--border);font-size:10px;letter-spacing:1px}
  /* Vertical padding follows variant B (6px -> 8px); horizontal stays at 11px.
     WNBA's widest table carries ~20 numeric columns where WWC's carries a
     handful, so B's 6px horizontal would tighten the columns rather than open
     the rows. The change B is actually making here is vertical rhythm. */
  td{padding:8px 11px;border-bottom:1px solid var(--border)}
  tr:hover td{background:var(--surface)}
  tr.lg-avg td{color:var(--avg);font-style:italic;border-top:1px solid var(--border)}
  /* Playoff cutoff line */
  tr.playoff-cutoff td{border-bottom:2px dashed rgba(218,165,32,0.4)}
  /* Team chip */
  .tm{color:#888;margin-left:5px;font-size:11px}
  /* Player-name link -> her page. Muted underline: legible as a link,
     quiet enough not to turn the table into a wall of accent. */
  .pl{color:var(--text);text-decoration:underline;
      text-decoration-color:rgba(136,136,136,0.4);text-underline-offset:2px}
  .pl:hover{color:var(--accent)}
  /* One stint of a multi-team player's season: subordinate to the combined line
     above it, and still legible as a partial line if the user re-sorts. */
  tr.tm-split td{color:var(--muted)}
  tr.tm-split td:first-child{padding-left:22px}
  /* Controls bar */
  .controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
  .controls label{color:var(--muted);font-size:11px;margin-right:3px}
  .controls .vs{color:var(--muted);font-size:11px}
  .controls button{background:var(--surface);border:1px solid var(--border);
                    color:var(--muted);font-family:inherit;font-size:11px;
                    padding:5px 10px;cursor:pointer}
  .controls button:hover{border-color:var(--accent);color:var(--accent)}
  select{background:var(--surface);border:1px solid var(--border);color:var(--text);
          font-family:inherit;font-size:12px;padding:5px 9px}
  input[type=text]{background:var(--surface);border:1px solid var(--border);
                    color:var(--text);font-family:inherit;font-size:12px;
                    padding:5px 9px;width:170px}
  input[type=text]:focus,select:focus{outline:none;border-color:var(--accent)}
  .matchup-bar{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
  .matchup-bar .vs{color:var(--muted);font-size:11px}
  .matchup-bar button{background:var(--surface);border:1px solid var(--border);
                       color:var(--muted);font-family:inherit;font-size:11px;
                       padding:5px 10px;cursor:pointer}
  .matchup-bar button:hover{border-color:var(--accent);color:var(--accent)}
  .highlight-team td{background:#1f1a0f}
  .highlight-team td:first-child{background:#1f1a0f}
  /* Leaders */
  .leaders-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px}
  .leader-card h3{font-size:11px;color:var(--muted);text-transform:uppercase;
                   letter-spacing:1px;margin-bottom:7px}
  .leader-card table{font-size:12px;width:100%}
  .leader-card td{padding:4px 7px;border-bottom:1px solid var(--border)}
  .leader-card .rank-1 td:first-child{color:var(--accent);font-weight:bold}
  .ldr-name{color:var(--accent);text-decoration:underline;text-decoration-color:rgba(218,165,32,0.4);
      text-underline-offset:2px;cursor:pointer}
  .ldr-name:hover{text-decoration-color:var(--accent)}
  /* Back-to-leaders link (shown only after a leader click) */
  .back-link{display:inline-block;color:var(--accent);cursor:pointer;font-size:13px;
      padding:6px 0;margin-bottom:6px;text-decoration:underline;
      text-decoration-color:rgba(218,165,32,0.4);text-underline-offset:2px}
  .back-link:hover{text-decoration-color:var(--accent)}
  /* Abbreviations */
  .abbrev-group{font-size:12px;color:var(--accent);margin:18px 0 6px;letter-spacing:.5px}
  .abbrev-list{display:grid;grid-template-columns:auto 1fr;gap:3px 14px;margin-bottom:10px}
  .abbrev-list dt{color:var(--accent);font-weight:bold;text-align:right}
  .abbrev-list dd{color:var(--text)}
  /* Sticky first column */
  tbody td:first-child{position:sticky;left:0;z-index:1;background:var(--bg)}
  tr:hover td:first-child{background:var(--surface)}
  tr.lg-avg td:first-child{background:var(--bg)}
  tr.playoff-cutoff td:first-child{background:var(--bg)}
  thead tr:first-child th:first-child{position:sticky;left:0;z-index:4;background:var(--surface)}
"""
    + f"""\
  /* ── Playoffs tab (postseason only; replaces the Series tab) ── */
  .po-row .gm-match{{font-size:14px}}
  .po-when{{color:var(--muted);font-size:12px;text-align:right;white-space:nowrap}}
  .po-tv{{display:block;font-size:10.5px}}
  .po-mu{{color:var(--muted)}}
  .po-a{{color:var(--text);text-decoration:none}}
  .po-arr{{color:var(--accent)}}
  .po-sc{{color:var(--muted);font-family:{MONO};font-variant-numeric:tabular-nums}}
  .po-sc.po-w{{color:var(--text);font-weight:700}}
  .po-dash{{color:var(--muted)}}
  .po-ser{{padding:12px 0 8px;border-bottom:1px solid var(--border)}}
  .po-t{{font-size:14px;font-weight:600}}
  .po-sd{{color:var(--muted);font-weight:400}}
  .po-s{{color:var(--accent);font-size:12px;margin:2px 0 6px}}
  .po-gr{{display:grid;grid-template-columns:3.2em 1fr auto;gap:8px;font-size:12px;
    padding:5px 0;border-top:1px solid #161618}}
  .po-no{{color:var(--muted)}}
  .po-ifn .po-what{{color:var(--muted);font-style:italic}}
  .po-lc th{{color:var(--muted);font-weight:400;text-align:right;
    border-bottom:1px solid var(--border);padding:3px 7px}}
  .po-lc th:nth-child(-n+2){{text-align:left}}
  .po-lc td:nth-child(n+3){{text-align:right}}
  .po-lc td.po-v{{color:var(--accent);font-weight:600}}
  .po-lc td:first-child{{color:var(--muted);width:2.4em}}
  .po-lc tbody td:nth-child(2){{font-family:{SANS}}}
  /* The Leaders switch: a second, independent view layer, like .statview. */
  .ldrview{{display:none}}.ldrview.active{{display:block}}
"""
    + GAMES_CSS
)




PAGE_JS = (
    chrome.usage_js(WNBA.slug)
    + """
function showTab(id, btn) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
  document.querySelectorAll('#'+id+' .table-wrap').forEach(updateScrollFades);
  /* Write the tab into the URL (2026-09-15). Before this the hash never
     moved, so Leaders -> a player page -> Back landed on Games: the browser
     restored `/` and `/` has always meant Games. Harmless while the site was
     one page; a daily annoyance now that the strip sends people to real
     pages. replaceState rather than pushState so a tab switch does not add a
     history entry, and it does NOT fire hashchange, so there is no loop with
     openTabFromHash. */
  if (window.history && history.replaceState) {
    history.replaceState(null, '', '#' + tabHash(id, btn));
  }
  track('tab', id === 'stats' ? statHash() : id);
  /* NOTE for whoever reads the usage data: on a deep link the hash is
     rewritten a second time by showStat a moment later, so the address bar
     ends up correct even though this line ran first against the previous
     view. Only the URL is written twice; the count is not (see `silent`). */
  /* The back-to-leaders link is only relevant right after a leader click;
     hide it on any manual tab switch. goToPlayer re-shows it afterward. */
  const bl = document.getElementById('backToLeaders');
  if (bl) bl.style.display = 'none';
  /* When switching to/from Games, restore the games list view and hide any
     open box score so returning to Games shows the list, not a stale box. */
  const gv = document.getElementById('games-view');
  if (gv) gv.style.display = 'block';
  document.querySelectorAll('.gm-box').forEach(s => s.style.display = 'none');
}

"""
    + chrome.SCROLL_FADE_JS
    + """
let sortState = {};
function sortTable(id, col) {
  const tbl = document.getElementById(id);
  const tbody = tbl.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr:not(.lg-avg)'));
  const avg  = tbody.querySelector('tr.lg-avg');
  const asc  = sortState[id+col] !== true;
  sortState[id+col] = asc;
  rows.sort((a, b) => {
    const av = a.cells[col].textContent.trim();
    const bv = b.cells[col].textContent.trim();
    const an = parseFloat(av), bn = parseFloat(bv);
    if (!isNaN(an) && !isNaN(bn)) return asc ? an-bn : bn-an;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach(r => tbody.appendChild(r));
  if (avg) tbody.appendChild(avg);
}

/* -- Player filter: two-team + search + min GP -- */
function filterPlayers() {
  const a = document.getElementById('pA').value;
  const b = document.getElementById('pB').value;
  const q = document.getElementById('psearch').value.toLowerCase();
  const m = parseInt(document.getElementById('pmin').value, 10) || 0;
  const picks = [a, b].filter(Boolean);

  const tbody = document.querySelector('#tbl_players tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));

  /* When two teams are selected, sort rows by team name so they group */
  if (picks.length === 2) {
    rows.sort((x, y) => {
      const tx = x.getAttribute('data-team') || '';
      const ty = y.getAttribute('data-team') || '';
      return tx.localeCompare(ty);
    });
    rows.forEach(r => tbody.appendChild(r));
  }

  rows.forEach(row => {
    const t = row.getAttribute('data-team') || '';
    const gp = parseInt(row.getAttribute('data-gp'), 10);
    const text = row.textContent.toLowerCase();
    /* Also search against full name in data-player */
    const fullName = (row.getAttribute('data-player') || '').toLowerCase();
    const matchTeam = picks.length === 0 || picks.includes(t);
    const matchSearch = !q || text.includes(q) || fullName.includes(q);
    const matchGP = gp >= m;
    row.style.display = (matchTeam && matchSearch && matchGP) ? '' : 'none';
  });
}

/* -- Clear the Players search box only (keep the Team A/B + Min GP filters,
      which a user sets deliberately). One tap to try another name. -- */
function clearPlayers() {
  document.getElementById('psearch').value = '';
  filterPlayers();
}

/* -- Leader filter: team + search -- */
function filterLeaders() {
  const team = document.getElementById('ldrTeam').value;
  const q = document.getElementById('ldrSearch').value.toLowerCase();
  document.querySelectorAll('.ldr-row').forEach(row => {
    const t = row.getAttribute('data-team') || '';
    const text = row.textContent.toLowerCase();
    const ok = (!team || t === team) && (!q || text.includes(q));
    row.style.display = ok ? '' : 'none';
  });
}

/* -- Clear the Leaders search box (Leaders filter is search + team; this clears
      the search only, matching the Players Clear behavior). -- */
function clearLeaders() {
  document.getElementById('ldrSearch').value = '';
  filterLeaders();
}

/* -- Click a leader name -> jump to Players tab and search -- */
let leadersScrollY = 0;
function goToPlayer(name) {
  /* Remember where we were in the Leaders list so we can return to it */
  leadersScrollY = window.scrollY;
  /* Reset player filters */
  document.getElementById('pA').value = '';
  document.getElementById('pB').value = '';
  document.getElementById('pmin').value = '0';
  document.getElementById('psearch').value = name;
  /* Players is a VIEW inside Stats since 2026-09-15, not a tab of its own.
     Switch the tab first, then the view — showTab hides the back link, and
     we re-show it below. */
  showTab('stats', document.querySelector('.tab[data-tab="stats"]'));
  showStat('players', document.querySelector('.sw[data-sv="players"]'), true);
  filterPlayers();
  /* Reveal the contextual back link and bring the result into view */
  document.getElementById('backToLeaders').style.display = '';
  window.scrollTo(0, 0);
}

/* -- Return from a player jump back to the Leaders tab, restoring scroll -- */
function backToLeaders() {
  const btn = document.querySelector('[data-tab="leaders"]');
  showTab('leaders', btn);
  window.scrollTo(0, leadersScrollY);
}

/* -- Team efficiency matchup filter -- */
function filterFF() {
  const t1 = document.getElementById('ff_team1').value;
  const t2 = document.getElementById('ff_team2').value;
  document.querySelectorAll('#tbl_ff tbody tr').forEach(row => {
    const team = row.getAttribute('data-team') || '';
    const isAvg = row.classList.contains('lg-avg');
    row.classList.remove('highlight-team');
    if (!t1) {
      row.style.display = '';
    } else {
      const show = isAvg || team === t1 || (t2 && team === t2);
      row.style.display = show ? '' : 'none';
      if (show && !isAvg) row.classList.add('highlight-team');
    }
  });
}

function clearFF() {
  document.getElementById('ff_team1').value = '';
  document.getElementById('ff_team2').value = '';
  filterFF();
}

/* -- Team totals matchup filter -- */
function filterTeamStats() {
  const t1 = document.getElementById('ts_team1').value;
  const t2 = document.getElementById('ts_team2').value;
  document.querySelectorAll('#tbl_team tbody tr').forEach(row => {
    const team = row.getAttribute('data-team') || '';
    const isAvg = row.classList.contains('lg-avg');
    row.classList.remove('highlight-team');
    if (!t1) {
      row.style.display = '';
    } else {
      const show = isAvg || team === t1 || (t2 && team === t2);
      row.style.display = show ? '' : 'none';
      if (show && !isAvg) row.classList.add('highlight-team');
    }
  });
}

function clearTeamStats() {
  document.getElementById('ts_team1').value = '';
  document.getElementById('ts_team2').value = '';
  filterTeamStats();
}

/* -- Games tab: show/hide box scores -- */
function showGame(id) {
  document.getElementById('games-view').style.display = 'none';
  document.querySelectorAll('.gm-box').forEach(function(s) { s.style.display = 'none'; });
  var el = document.getElementById('g' + id);
  el.style.display = 'block';
  el.querySelectorAll('.gm-tw').forEach(updateScrollFades);
  track('box', 'game:' + id);
  window.scrollTo(0, 0);
}
function backToGames() {
  document.querySelectorAll('.gm-box').forEach(function(s) { s.style.display = 'none'; });
  document.getElementById('games-view').style.display = 'block';
  window.scrollTo(0, 0);
}
/* Deep links into a tab, added 2026-09-08 with the team pages.
   Every tab is a `display:none` div until a click activates it, so before
   this a fragment like /#teamtotals scrolled to a hidden element and
   appeared to do nothing at all. The team pages' link row points here, so
   without this they would ship as links that silently fail — the same class
   of defect as linking to a 404.
   Guarded on every side: the id must be a known section AND have a matching
   tab button, and the pattern test keeps an attacker-supplied fragment out
   of the selector. An absent or unrecognised hash leaves Games active, which
   is the pre-existing behaviour. Also bound to hashchange so a link followed
   while already on the page works the same as one followed on arrival. */
/* The Stats views. A second show/hide layer inside #stats — see the
   `.statview` note in the CSS for why it cannot reuse `.section`. */
function showStat(view, btn, silent) {
  /* Scoped to #stats: Leaders has a `.sw` switch of its own since the
     playoffs (2026-09-25), and an unscoped selector would clear it. */
  document.querySelectorAll('#stats .statview').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('#stats .sw').forEach(b => b.classList.remove('active'));
  var v = document.getElementById(view);
  if (!v) return;
  v.classList.add('active');
  if (btn) btn.classList.add('active');
  document.querySelectorAll('#' + view + ' .table-wrap').forEach(updateScrollFades);
  if (window.history && history.replaceState) {
    history.replaceState(null, '', '#' + statHash());
  }
  /* Analytics continuity (2026-09-15): usage_report.py groups tab events by
     id, and `teameff` / `teamtotals` / `players` stop existing as tabs today.
     Sending the VIEW rather than a bare `stats` keeps the three separately
     countable, so the series has a rename in it, not a cliff. Cutover date is
     in the Decisions Log.

     `silent` exists because showTab() has already counted the arrival whenever
     it is about to hand off to us — a deep link and a leader click both run
     showTab then showStat, and counting in both would inflate Stats against
     every other tab. Only a real click on the switch counts here. */
  if (!silent) track('tab', statHash());
}

/* Which Stats view is showing, as its URL fragment. Efficiency is the default
   and gets the bare `#stats` so the tab has a clean canonical link. */
function statHash() {
  var v = document.querySelector('#stats .statview.active');
  var id = v ? v.id : 'teameff';
  return id === 'teameff' ? 'stats'
       : id === 'teamtotals' ? 'stats-team-totals'
       : id === 'players' ? 'stats-player-totals' : 'stats';
}

/* The URL fragment for a tab. Stats and Leaders carry their view; the first
   tab says #playoffs during the postseason (its button carries data-hash),
   while its section id stays `games` so every old /#games link still works. */
function tabHash(id, btn) {
  if (id === 'stats') return statHash();
  if (id === 'leaders') return ldrHash();
  return (btn && btn.dataset && btn.dataset.hash) || id;
}

/* The Leaders switch (playoffs only): Playoffs · Season. Same pattern as
   showStat, scoped to #leaders so the two switches never touch each other.
   `silent` as in showStat: showTab has already counted the arrival. */
function showLeaders(view, btn, silent) {
  var v = document.getElementById(view);
  if (!v) return;
  document.querySelectorAll('#leaders .ldrview').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('#leaders .sw').forEach(b => b.classList.remove('active'));
  v.classList.add('active');
  if (btn) btn.classList.add('active');
  if (window.history && history.replaceState) {
    history.replaceState(null, '', '#' + ldrHash());
  }
  if (!silent) track('tab', ldrHash());
}
function ldrHash() {
  var v = document.querySelector('#leaders .ldrview.active');
  if (!v) return 'leaders';
  return v.id === 'ldr-po' ? 'leaders-playoffs' : 'leaders-season';
}
var LDR_HASHES = {'leaders-playoffs': 'ldr-po', 'leaders-season': 'ldr-rs'};
/* Section aliases. `playoffs` is the first tab's name in the postseason. */
var TAB_ALIASES = {'playoffs': 'games'};

/* Fragment -> Stats view. The first three are today's links; the last three
   are the OLD tab ids, kept because the team pages linked to them and people
   may have bookmarks. A dead deep link is the same class of defect as a
   link to a 404 — see the note on openTabFromHash. */
var STAT_HASHES = {
  'stats': 'teameff',
  'stats-team-totals': 'teamtotals',
  'stats-player-totals': 'players',
  'teameff': 'teameff',
  'teamtotals': 'teamtotals',
  'players': 'players'
};

/* `#stats-team-totals?team=MIN` opens Team Totals with the Lynx preselected in
   the matchup filter, which already renders that team plus the highlighted
   league-average row. The TLA is matched against `data-tla` on the options
   themselves, so there is no second lookup table to fall out of sync — see
   `team_options` in main(). `tla` is pattern-tested by the caller before it
   reaches this selector. */
function preselectTeamTotals(tla) {
  var sel = document.getElementById('ts_team1');
  if (!sel) return;
  var opt = sel.querySelector('option[data-tla="' + tla + '"]');
  if (!opt) return;
  sel.value = opt.value;
  filterTeamStats();
}

function openTabFromHash() {
  var raw = (location.hash || '').replace(/^#/, '');
  /* Split the optional query BEFORE validating, so the id pattern stays as
     strict as it was. */
  var q = '', qi = raw.indexOf('?');
  if (qi >= 0) { q = raw.slice(qi + 1); raw = raw.slice(0, qi); }
  if (!/^[a-z][a-z0-9-]*$/.test(raw)) return;

  var view = STAT_HASHES[raw];
  if (view) {
    var tb = document.querySelector('.tab[data-tab="stats"]');
    if (!tb) return;
    showTab('stats', tb);
    showStat(view, document.querySelector('.sw[data-sv="' + view + '"]'), true);
    /* Only Team Totals has a team filter to preselect. The pattern test is
       what keeps an attacker-supplied fragment out of the selector above. */
    var m = /^team=([A-Za-z]{2,4})$/.exec(q);
    if (m && view === 'teamtotals') preselectTeamTotals(m[1].toUpperCase());
    return;
  }

  var lv = LDR_HASHES[raw];
  if (lv) {
    var lb = document.querySelector('.tab[data-tab="leaders"]');
    if (!lb) return;
    showTab('leaders', lb);
    /* Absent before the playoffs: the Season boards are then the whole tab. */
    showLeaders(lv, document.querySelector('#leaders .sw[data-lv="' + lv + '"]'), true);
    return;
  }
  if (TAB_ALIASES[raw]) raw = TAB_ALIASES[raw];

  var sec = document.getElementById(raw);
  var btn = document.querySelector('.tab[data-tab="' + raw + '"]');
  if (sec && btn && sec.classList.contains('section')) showTab(raw, btn);
}
window.addEventListener('DOMContentLoaded', openTabFromHash);
window.addEventListener('hashchange', openTabFromHash);"""
)


# ── Page assembly ─────────────────────────────────────────────────────────

def assemble_page(display_date, data_through_iso,
                  games_html,
                  standings_html, leaders_html, team_eff_html,
                  team_totals_html, players_html, abbreviations_html,
                  playoffs=False):
    """Combine all sections into the final HTML string.

    `playoffs` relabels the first tab Playoffs. Its section id stays `games`
    so `/#games` links keep working, and its button carries
    `data-hash="playoffs"` so the address bar reads `/#playoffs`. The separate
    Series tab of 2026-09-08 is gone: the series live inside this one.
    """
    # The strip, rebuilt 2026-09-15 (handoff wnba-nav-build-handoff-2026-09-14).
    # Two changes of substance, and they had to ship together:
    #
    #   1. Teams and Players become real LINKS to /teams/ and /players/. Those
    #      index pages had ZERO inbound links from the site (Decisions Log
    #      2026-09-09) — the entity pages existed and nothing pointed at them.
    #   2. Efficiency + Team Totals + Players collapse into one Stats tab, so
    #      the strip has room for Teams and Players without growing. An
    #      in-page Players tab and a /players/ link cannot coexist in one
    #      strip, which is why this is one merge and not three.
    #
    # `href` distinguishes a link from a button; both render as `.tab`.
    tabs = [
        ('games', 'Playoffs' if playoffs else 'Games', None),
        ('standings', 'Standings', None),
        ('leaders', 'Leaders', None),
        ('teams', 'Teams', '/teams/'),
        ('players-page', 'Players', '/players/'),
        ('stats', 'Stats', None),
        ('abbreviations', 'Key', None),
    ]

    def _tab(i, tid, name, href):
        active = ' active' if i == 0 else ''
        if href:
            return f'  <a class="tab" href="{href}">{name}</a>'
        hash_attr = ' data-hash="playoffs"' if (playoffs and tid == 'games') else ''
        return (f'  <button class="tab{active}" data-tab="{tid}"{hash_attr} '
                f'onclick="showTab(\'{tid}\',this)">{name}</button>')

    tab_buttons = '\n'.join(_tab(i, *t) for i, t in enumerate(tabs))

    # The Stats section wraps the three view bodies, which are `.statview`
    # rather than `.section` so showTab() does not fight the inner switch.
    stats_section = (
        '<div id="stats" class="section">\n'
        '<div class="swrap">\n'
        '  <button class="sw active" data-sv="teameff" '
        'onclick="showStat(\'teameff\',this)">Efficiency</button>\n'
        '  <button class="sw" data-sv="teamtotals" '
        'onclick="showStat(\'teamtotals\',this)">Team Totals</button>\n'
        '  <button class="sw" data-sv="players" '
        'onclick="showStat(\'players\',this)">Player Totals</button>\n'
        '</div>\n'
        f'{team_eff_html}{team_totals_html}{players_html}'
        '</div>\n'
    )

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="data-through" content="{data_through_iso}">\n'
        '<title>WNBA 2026 \u2014 At a Glance</title>\n'
        '<meta name="description" content="Fast, ad-free WNBA stats you can check at a glance \u2014 leaders, standings, four factors, updated every morning.">\n'
        # Social preview (Open Graph + Twitter) \u2014 image lives at public/og.png,
        # served as a static asset by the Worker. Absolute URLs required.
        # Emitted by `sag.seo.social_tags`, shared with the player pages and the
        # WWC site; it was three hand-rolled copies until 2026-09-02. The two
        # overrides below are NOT decoration: this page deliberately ships a
        # shorter twitter:description than its og:description, and a bespoke
        # og:image:alt. Folding them into one string would be a silent copy
        # change on the live page.
        + "\n".join(seo.social_tags(
            WNBA, "/",
            "WNBA 2026 \u2014 At a Glance",
            "Fast, ad-free WNBA stats you can check at a glance \u2014 leaders, "
            "standings, four factors, updated every morning.",
            image_alt="WNBA 2026 \u2014 At a Glance: fast, ad-free WNBA stats",
            twitter_description="Fast, ad-free WNBA stats \u2014 leaders, "
                                "standings, four factors, updated every morning.",
        )) + "\n"
        + f'<style>\n{PAGE_CSS}\n</style>\n'
        '</head>\n<body>\n\n'
        '<h1>WNBA 2026 \u2014 At a Glance</h1>\n'
        f'<div class="meta">Fast, ad-free — updated through games of {display_date}</div>\n'
        f'{wwc_promo_html(today_et())}\n'
        f'<div class="tabs">\n{tab_buttons}\n</div>\n\n'
        f'{games_html}\n'
        f'{standings_html}\n'
        f'{leaders_html}\n'
        f'{stats_section}\n'
        f'{abbreviations_html}\n'
        + chrome.SITE_FOOTER_HTML
        + f'<script>\n{PAGE_JS}\n</script>\n'
        + chrome.cf_beacon_html(WNBA.cf_analytics_token)
        + '</body>\n</html>'
    )


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    WNBA.ensure_dirs()
    # Two frames, deliberately: player_raw/team_raw are regular-season only and
    # feed every aggregation; player_all/team_all include the postseason and
    # feed only the Games tab, which displays individual games. See load_data()
    # and load_all_games().
    player_raw, team_raw = load_data()
    player_all, team_all = load_all_games()

    through_dt   = pd.to_datetime(player_raw['game_date'].max())
    display_date = f"{through_dt.strftime('%B')} {through_dt.day}"
    data_through_iso = through_dt.strftime('%Y-%m-%d')
    # The PAGE is dated from every game, playoffs included. The regular-season
    # date above stops moving on the last day of the season, and the cron
    # Worker's health check compares the page's `data-through` meta with
    # yesterday on every game day — dated from player_raw, it would have
    # reported a stale site (and auto-rebuilt, then emailed) after every
    # playoff game. The social payload keeps the regular-season date: its
    # numbers are regular-season numbers.
    page_dt = pd.to_datetime(player_all['game_date'].max())
    page_display_date = f"{page_dt.strftime('%B')} {page_dt.day}"
    page_through_iso = page_dt.strftime('%Y-%m-%d')

    standings_df    = compute_standings(team_raw)
    # Two player frames: per-(athlete, team) for the Players table's split
    # rows and the games-played guard, season-combined for the leader boards.
    p_base          = compute_player_base(player_raw)
    p_season        = compute_player_season(player_raw)
    run_data_guards(player_raw, p_base)
    team_stats_df   = compute_team_stats(team_raw)
    ff_df, team_list = compute_four_factors(team_raw, player_raw)
    run_integrity_checks(team_raw, player_raw, ff_df)
    leaders         = compute_leaders(p_season)
    emit_social_payload(player_raw, leaders, display_date, data_through_iso)

    # Team abbreviations for player/leader filters
    team_abbrevs = sorted(p_base['team_abbreviation'].dropna().unique())

    # Full team name options for efficiency and totals matchup bars.
    #
    # Each option carries `data-tla` as of 2026-09-15, so `/#stats-team-totals
    # ?team=MIN` can preselect a team without a second TLA->name table to keep
    # in sync. The pairing is read straight out of the box score, which carries
    # `team_display_name` and `team_abbreviation` on the same row — the DOM
    # becomes the mapping, and it cannot drift from the data that built it.
    _tla = (team_raw[['team_display_name', 'team_abbreviation']]
            .dropna().drop_duplicates()
            .set_index('team_display_name')['team_abbreviation'].to_dict())
    # A name with no TLA would silently produce an option the team pages can
    # never preselect, so say so rather than emit a dud link target.
    _missing = [t for t in team_list if t not in _tla]
    assert not _missing, f"no TLA for team(s): {_missing}"
    team_options = '\n'.join(
        f'<option value="{esc(t)}" data-tla="{esc(_tla[t])}">{esc(t)}</option>'
        for t in sorted(team_list)
    )

    # Postseason, as far as the data says: a playoff event in the schedule
    # window, or a playoff game played. Never the clock.
    series_rows = load_series()
    upcoming_status, upcoming_games = load_upcoming()
    playoffs = playoffs_active(upcoming_games, series_rows)

    # Build each section
    if playoffs:
        seeds_by_id, _, _ = load_seeds()
        box_ids = {gid for gid, *_ in playoff_box_games(player_all, team_all)}
        model = build_playoff_model(series_rows, upcoming_games, player_all,
                                    team_all, seeds_by_id, box_ids)
        games_html = build_playoffs_section(model, upcoming_status)
        print(f"Playoffs: {len(model)} series, {len(box_ids)} box-score link(s), "
              f"schedule window {upcoming_status}")
    else:
        games_html = build_games_section(player_all, team_all)
    standings_html     = build_standings_section(standings_df, final=playoffs)
    leaders_html       = build_leaders_section(
        leaders, team_abbrevs, compute_playoff_leaders(player_all, team_all))
    team_eff_html      = build_team_efficiency_section(ff_df, team_options)
    team_totals_html   = build_team_totals_section(team_stats_df, team_options)
    players_html       = build_players_section(p_base, p_season, team_abbrevs)
    abbreviations_html = build_abbreviations_section()

    html = assemble_page(page_display_date, page_through_iso,
                         games_html,
                         standings_html, leaders_html, team_eff_html,
                         team_totals_html, players_html, abbreviations_html,
                         playoffs)

    OUTPUT.write_text(html)
    print(f"Written -> {OUTPUT}")
    multi = len(p_base) - len(p_season)
    print(f"Players: {len(p_season)} ({multi} multi-team)  "
          f"|  Teams: {len(team_stats_df)-1}  |  FF rows: {len(ff_df)}")


if __name__ == '__main__':
    main()
