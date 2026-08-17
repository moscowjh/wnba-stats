"""
build_stats_page.py
Builds sites/wnba/wnba-2026-stats-explorer.html from wehoop box score CSVs.
All stats -- including four factors -- are computed from the CSVs.

Usage:
    python3 /Users/jasonhhorowitz/projects/basketball-data/WNBA/build_stats_page.py
"""

import json
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from html import escape as esc
from zoneinfo import ZoneInfo

from sag import seo
from sag.render import chrome

from config import WNBA

OUTPUT = WNBA.page_output

PYTH_EXP = 13.91  # Pythagorean exponent for basketball
PLAYOFF_SPOTS = 8  # teams that make the WNBA playoffs
ET = ZoneInfo("America/New_York")


def today_et():
    """ET calendar date of 'today'. SAG_TODAY (YYYY-MM-DD) overrides it so the
    golden harness can re-render a pinned snapshot byte-for-byte on any later
    day — the Games tab's today/yesterday split and the social factoid's
    'last night' gate both key off this date."""
    ov = os.environ.get('SAG_TODAY')
    if ov:
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
        cells = ''.join(f'<td>{v}</td>' for v in row)
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
        cells = ''.join(f'<td>{v if pd.notna(v) else "\u2014"}</td>' for v in row)
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

def load_data():
    """Load and clean the box-score CSVs.  Returns (player_raw, team_raw).

    Filters to regular-season games (season_type == 2) so every downstream
    aggregation, guard, and the Bluesky factoid operate on the same
    regular-season-only frame. Postseason rows (season_type == 3), once the
    playoffs begin, are carried in the CSVs but excluded here; a future playoff
    view can select them explicitly. Guarded by a column check so a pre-migration
    CSV (no season_type) still builds, treating all rows as regular season."""
    player_raw = pd.read_csv(WNBA.player_box)
    team_raw   = pd.read_csv(WNBA.team_box)
    if 'season_type' in player_raw.columns:
        player_raw = player_raw[player_raw['season_type'] == 2]
    if 'season_type' in team_raw.columns:
        team_raw = team_raw[team_raw['season_type'] == 2]
    player_raw = player_raw[player_raw['did_not_play'] != True].copy()
    team_raw = team_raw.copy()
    return player_raw, team_raw


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
        top = sub.nlargest(10, raw)[cols].copy()
        top.columns = names
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
        df = leaders[cat].head(5)
        val_col = df.columns[-1]
        rows = []
        for rank, (_, r) in enumerate(df.iterrows(), start=1):
            v = r[val_col]
            vs = f"{v:.1f}%" if cat in _PCT_CATS else f"{v:.1f}"
            rows.append({'rank': rank, 'player': r['Player'],
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

def _color_cell(col, val):
    """Apply red/green styling to Streak and +/- cells."""
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


def build_standings_section(standings_df):
    """Standings with an orange dashed playoff cutoff line after 8th place."""
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
        '<h2>Standings</h2>\n'
        '<p class="tab-note"><em>Dashed line = playoff cutoff (top 8)&ensp;|'
        '&ensp;XW/XL = Pythagorean expected record</em></p>\n'
        '<div class="table-scroll"><div class="table-wrap">'
        f'<table id="tbl_standings"><thead><tr>{headers}</tr></thead>'
        f'<tbody>{rows}</tbody></table></div></div>\n'
        '</div>\n'
    )


def build_team_efficiency_section(ff_df, team_options):
    return (
        '<div id="teameff" class="section">\n'
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
        '<div id="teamtotals" class="section">\n'
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
        '<div id="players" class="section">\n'
        '<h2>Players \u2014 Season Stats</h2>\n'
        '<span id="backToLeaders" class="back-link" style="display:none"'
        ' onclick="backToLeaders()">\u2190 Back to Leaders</span>\n'
        f'{controls}'
        '<div class="table-scroll"><div class="table-wrap">'
        f'<table id="tbl_players"><thead><tr>{headers}</tr></thead>'
        f'<tbody>{body}</tbody></table></div></div>\n'
        '</div>\n'
    )


def build_leaders_section(leaders, team_abbrevs):
    """Leader cards with team/player filter and click-to-player links."""
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
        for i, (_, r) in enumerate(ldr_df.iterrows()):
            rank_cls = ' rank-1' if i == 0 else ''
            name = str(r['Player'])
            team = str(r['Team'])
            sn = short_name(name)
            val = r[stat_col]
            val_str = f'{val:.1f}' if isinstance(val, float) else str(val)
            rows += (
                f'<tr class="ldr-row{rank_cls}" data-team="{esc(team)}">'
                f'<td>{i+1}. <span class="ldr-name" data-player="{esc(name)}" onclick="goToPlayer(this.dataset.player)">'
                f'{esc(sn)}</span> <span class="tm">{esc(team)}</span></td>'
                f'<td>{val_str}</td></tr>\n'
            )
        cards += (
            f'<div class="leader-card"><h3>{esc(label)}</h3>'
            f'<table id="{safe_id}"><tbody>{rows}</tbody></table></div>\n'
        )

    return (
        '<div id="leaders" class="section">\n'
        '<h2>Category Leaders <span class="sub">\u2014 per game \u2014 qualified</span></h2>\n'
        f'{controls}'
        f'<div class="leaders-grid">{cards}</div>\n'
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


def build_games_section(player_raw, team_raw):
    """Build the Games tab: today's schedule + yesterday's results with
    inline box scores. Returns HTML string."""
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
PAGE_CSS = (
    chrome.TOKENS_CSS
    + """\
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Courier New',monospace;background:var(--bg);color:var(--text);
        font-size:13px;padding:16px}
  h1{color:var(--accent);font-size:17px;margin-bottom:3px}
  .meta{color:var(--muted);font-size:11px;margin-bottom:24px}
"""
    + chrome.SITE_FOOTER_CSS
    + """\
  h2{font-size:12px;color:var(--accent);text-transform:uppercase;letter-spacing:1px;
      margin:24px 0 10px;border-bottom:1px solid var(--border);padding-bottom:5px}
  h2 .sub{color:var(--muted);text-transform:none;letter-spacing:0;font-size:11px}
  .tab-note{color:var(--muted);font-size:11px;margin-bottom:10px}
  .tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px}
  .tab{cursor:pointer;padding:5px 12px;border:1px solid var(--border);
        color:var(--muted);background:var(--surface);font-family:inherit;
        font-size:12px;letter-spacing:.5px}
  .tab.active{border-color:var(--accent);color:var(--accent)}
  .section{display:none}.section.active{display:block}
"""
    + chrome.SCROLL_FADE_CSS
    + """\
  table{border-collapse:collapse;width:100%;white-space:nowrap}
  thead tr{background:var(--surface)}
  th{padding:7px 11px;text-align:left;color:var(--muted);font-size:11px;
      letter-spacing:.5px;border-bottom:1px solid var(--border);
      cursor:pointer;user-select:none}
  th:hover{color:var(--accent)}
  th.group-header{text-align:center;color:var(--accent);border-bottom:1px solid var(--border);
                   border-left:1px solid var(--border);font-size:10px;letter-spacing:1px}
  td{padding:6px 11px;border-bottom:1px solid var(--border)}
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
  /* ── Games tab ── */
  .gm-daybar{color:var(--accent);font-size:11px;letter-spacing:.08em;text-transform:uppercase;
    border-bottom:1px solid var(--border);padding:10px 0 5px;margin-top:6px}
  .gm-daybar:first-child{margin-top:0}
  .gm-row{display:flex;align-items:center;justify-content:space-between;gap:10px;
    padding:11px 2px;border-bottom:1px solid #161618}
  .gm-row .gm-match{font-size:15px}
  .gm-row.gm-sched .gm-match{color:var(--text)}
  .gm-row.gm-sched .gm-when{color:var(--muted);font-size:12px}
  .gm-row.gm-result{cursor:pointer}
  .gm-row.gm-result .gm-s{color:var(--muted)}
  .gm-row.gm-result .gm-s.gm-win{color:var(--text);font-weight:700}
  .gm-row .gm-dash{color:var(--muted)}
  .gm-row .gm-chev{color:var(--muted);font-size:18px}
  .gm-row.gm-result:active{background:var(--surface)}
  .gm-hint{color:var(--muted);font-size:11px;margin-top:14px}
  .gm-empty{color:var(--muted);font-size:12px;padding:14px 2px}
  .gm-back{color:var(--accent);font-size:13px;padding:4px 0 12px;cursor:pointer;display:inline-block}
  .gm-hd{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:2px}
  .gm-hd .gm-tm{font-size:15px;font-weight:700}
  .gm-hd .gm-sc{font-size:22px;font-weight:700}
  .gm-hd .gm-rec{color:var(--muted);font-weight:400;font-size:12px}
  .gm-fin{color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-size:11px;text-align:center}
  .gm-win{color:#7ec27e}
  .gm-meta{color:var(--muted);font-size:11px;margin:8px 0 14px}
  .gm-h2{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;
    border-bottom:1px solid var(--border);padding-bottom:5px;margin:18px 0 6px}
  .gm-ls{border-collapse:collapse;width:auto;margin:10px 0 20px;font-size:13px}
  .gm-ls th,.gm-ls td{padding:3px 10px;text-align:right}
  .gm-ls .gm-pl{text-align:left;color:var(--muted)}.gm-ls .gm-t{border-left:1px solid var(--border)}
  .gm-ts{border-collapse:collapse;width:auto;margin:4px 0 8px;font-size:12px}
  .gm-ts th,.gm-ts td{padding:2px 10px;text-align:right;white-space:nowrap}
  .gm-ts .gm-pl{text-align:left}
  .gm-ts .gm-cols th{color:var(--muted);font-weight:400;border-bottom:1px solid var(--border)}
  .gm-ts .gm-v td{padding-top:7px}.gm-ts .gm-v .gm-pl{font-weight:700}
  .gm-ts .gm-p td{color:var(--muted);font-size:10px;padding-top:0;padding-bottom:3px}
  .gm-tcap{font-weight:700;margin:18px 0 3px}.gm-tcap .gm-rec{color:var(--muted);font-weight:400;font-size:12px}
  .gm-tscroll{position:relative;margin-bottom:6px}
  .gm-tscroll::after{content:"";position:absolute;top:0;bottom:0;right:0;width:26px;
    pointer-events:none;opacity:0;transition:opacity .15s ease;z-index:5;
    background:linear-gradient(to right, rgba(15,15,15,0), var(--bg))}
  .gm-tscroll.more-right::after{opacity:1}
  .gm-tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
  .gm-bx{border-collapse:collapse;min-width:520px;width:100%;font-size:12px}
  .gm-bx th,.gm-bx td{padding:3px 6px;text-align:right;white-space:nowrap}
  .gm-bx .gm-pl{text-align:left;position:sticky;left:0;background:var(--bg);min-width:88px;padding-left:0}
  .gm-bx .gm-cols th{border-bottom:1px solid var(--accent);color:var(--muted);font-weight:400}
  .gm-bx .gm-sec td{color:var(--accent);font-size:10px;letter-spacing:.1em;padding-top:9px;text-transform:uppercase}
  .gm-bx tr:not(.gm-cols):not(.gm-sec) td{border-top:1px solid #18181b}
  .gm-pos{color:var(--muted);font-size:10px;display:inline-block;min-width:18px}"""
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
  track('tab', id);
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
  /* Switch to players tab (showTab hides the back link; we re-show it below) */
  const btn = document.querySelector('[data-tab="players"]');
  showTab('players', btn);
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
}"""
)


# ── Page assembly ─────────────────────────────────────────────────────────

def assemble_page(display_date, data_through_iso,
                  games_html,
                  standings_html, leaders_html, team_eff_html,
                  team_totals_html, players_html, abbreviations_html):
    """Combine all sections into the final HTML string."""
    tabs = [
        ('games', 'Games'),
        ('standings', 'Standings'),
        ('leaders', 'Leaders'),
        ('teameff', 'Efficiency'),
        ('teamtotals', 'Team Totals'),
        ('players', 'Players'),
        ('abbreviations', 'Key'),
    ]
    tab_buttons = '\n'.join(
        f'  <button class="tab{" active" if i == 0 else ""}" '
        f'data-tab="{tid}" onclick="showTab(\'{tid}\',this)">{name}</button>'
        for i, (tid, name) in enumerate(tabs)
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
        '<meta property="og:type" content="website">\n'
        '<meta property="og:site_name" content="statsataglance">\n'
        '<meta property="og:title" content="WNBA 2026 \u2014 At a Glance">\n'
        '<meta property="og:description" content="Fast, ad-free WNBA stats you can check at a glance \u2014 leaders, standings, four factors, updated every morning.">\n'
        '<meta property="og:url" content="https://wnba.statsataglance.com/">\n'
        '<meta property="og:image" content="https://wnba.statsataglance.com/og.png">\n'
        '<meta property="og:image:width" content="1200">\n'
        '<meta property="og:image:height" content="630">\n'
        '<meta property="og:image:alt" content="WNBA 2026 \u2014 At a Glance: fast, ad-free WNBA stats">\n'
        '<meta name="twitter:card" content="summary_large_image">\n'
        '<meta name="twitter:title" content="WNBA 2026 \u2014 At a Glance">\n'
        '<meta name="twitter:description" content="Fast, ad-free WNBA stats \u2014 leaders, standings, four factors, updated every morning.">\n'
        '<meta name="twitter:image" content="https://wnba.statsataglance.com/og.png">\n'
        f'<style>\n{PAGE_CSS}\n</style>\n'
        '</head>\n<body>\n\n'
        '<h1>WNBA 2026 \u2014 At a Glance</h1>\n'
        f'<div class="meta">Fast, ad-free — updated through games of {display_date}</div>\n\n'
        f'<div class="tabs">\n{tab_buttons}\n</div>\n\n'
        f'{games_html}\n'
        f'{standings_html}\n'
        f'{leaders_html}\n'
        f'{team_eff_html}\n'
        f'{team_totals_html}\n'
        f'{players_html}\n'
        f'{abbreviations_html}\n'
        + chrome.SITE_FOOTER_HTML
        + f'<script>\n{PAGE_JS}\n</script>\n'
        + chrome.cf_beacon_html(WNBA.cf_analytics_token)
        + '</body>\n</html>'
    )


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    WNBA.ensure_dirs()
    player_raw, team_raw = load_data()

    through_dt   = pd.to_datetime(player_raw['game_date'].max())
    display_date = f"{through_dt.strftime('%B')} {through_dt.day}"
    data_through_iso = through_dt.strftime('%Y-%m-%d')

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

    # Full team name options for efficiency and totals matchup bars
    team_options = '\n'.join(
        f'<option value="{esc(t)}">{esc(t)}</option>' for t in sorted(team_list)
    )

    # Build each section
    games_html         = build_games_section(player_raw, team_raw)
    standings_html     = build_standings_section(standings_df)
    leaders_html       = build_leaders_section(leaders, team_abbrevs)
    team_eff_html      = build_team_efficiency_section(ff_df, team_options)
    team_totals_html   = build_team_totals_section(team_stats_df, team_options)
    players_html       = build_players_section(p_base, p_season, team_abbrevs)
    abbreviations_html = build_abbreviations_section()

    html = assemble_page(display_date, data_through_iso,
                         games_html,
                         standings_html, leaders_html, team_eff_html,
                         team_totals_html, players_html, abbreviations_html)

    OUTPUT.write_text(html)
    print(f"Written -> {OUTPUT}")
    multi = len(p_base) - len(p_season)
    print(f"Players: {len(p_season)} ({multi} multi-team)  "
          f"|  Teams: {len(team_stats_df)-1}  |  FF rows: {len(ff_df)}")


if __name__ == '__main__':
    main()
