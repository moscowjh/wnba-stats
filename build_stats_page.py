"""
build_stats_page.py
Builds WNBA-2026-stats-explorer.html from wehoop box score CSVs.
All stats -- including four factors -- are computed from the CSVs.

Usage:
    python3 /Users/jasonhhorowitz/projects/basketball-data/WNBA/build_stats_page.py
"""

import pandas as pd
import numpy as np
from pathlib import Path
from html import escape as esc

HERE   = Path(__file__).parent
OUTPUT = HERE / 'WNBA-2026-stats-explorer.html'

PYTH_EXP = 13.91  # Pythagorean exponent for basketball
PLAYOFF_SPOTS = 8  # teams that make the WNBA playoffs


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
    """Load and clean the box-score CSVs.  Returns (player_raw, team_raw)."""
    player_raw = pd.read_csv(HERE / 'wnba_player_box_2026.csv')
    team_raw   = pd.read_csv(HERE / 'wnba_team_box_2026.csv')
    player_raw = player_raw[player_raw['did_not_play'] != True].copy()
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
    #    correct for players traded mid-season: each appears as a separate
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


def compute_standings(team_raw):
    """Win-loss standings with Pythagorean expected record."""
    std = team_raw.groupby('team_display_name').agg(
        GP     = ('game_id', 'count'),
        W      = ('team_winner', 'sum'),
        PF_tot = ('team_score', 'sum'),
        PA_tot = ('opponent_team_score', 'sum'),
    ).reset_index()

    std['L']    = std['GP'] - std['W']
    std['WPct'] = std['W'] / std['GP']
    std['Win%'] = std['WPct'].apply(fmt_winpct)
    std['PF']   = (std['PF_tot'] / std['GP']).round(1)
    std['PA']   = (std['PA_tot'] / std['GP']).round(1)
    std['Diff'] = (std['PF'] - std['PA']).round(1)

    std['pyth'] = std['PF_tot']**PYTH_EXP / (std['PF_tot']**PYTH_EXP + std['PA_tot']**PYTH_EXP)
    std['XW']   = (std['pyth'] * std['GP']).round(1)
    std['XL']   = (std['GP'] - std['XW']).round(1)

    std = std.sort_values(['WPct', 'Diff'], ascending=[False, False])
    df = std[['team_display_name','W','L','Win%','PF','PA','Diff','XW','XL']].copy()
    df.columns = ['Team','W','L','Win%','PF','PA','+/-','XW','XL']
    return df


def compute_player_base(player_raw):
    """Aggregate per-player totals and per-game averages.  Used by both
    the player stats table and the category leaders."""
    p = player_raw.groupby(
        ['athlete_display_name', 'team_abbreviation', 'athlete_position_abbreviation']
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
    ).reset_index()
    p = p.sort_values('PTS', ascending=False)

    # Counting-stat averages
    for col, src in [('PPG','PTS'), ('RPG','TRB'), ('ORPG','ORB'), ('APG','AST'),
                     ('SPG','STL'), ('BPG','BLK'), ('TPG','TOV')]:
        p[col] = (p[src] / p['GP']).round(1)

    # Shooting percentages — use .where() to avoid divide-by-zero NaN warnings
    p['eFG%'] = ((p['FGM'] + 0.5*p['TPM']) / p['FGA'] * 100).where(p['FGA'] > 0).round(1)
    p['TS%']  = (p['PTS'] / (2 * (p['FGA'] + 0.44*p['FTA'])) * 100).where(p['FGA'] > 0).round(1)
    p['FT%']  = (p['FTM'] / p['FTA'] * 100).where(p['FTA'] > 0).round(1)
    p['3PT%'] = (p['TPM'] / p['TPA'] * 100).where(p['TPA'] > 0).round(1)

    return p


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

    ff['POSS']     = ff['FGA'] - ff['ORB'] + ff['TOV'] + 0.44 * ff['FTA']
    ff['OPP_POSS'] = ff['OPP_FGA'] - ff['OPP_ORB'] + ff['OPP_TOV'] + 0.44 * ff['OPP_FTA']
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
    lg_poss     = lg['FGA'] - lg['ORB'] + lg['TOV'] + 0.44*lg['FTA']
    lg_opp_poss = lg['OPP_FGA'] - lg['OPP_ORB'] + lg['OPP_TOV'] + 0.44*lg['OPP_FTA']
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


def compute_leaders(p_base):
    """Top-10 category leaders with prorated WNBA qualifying minimums."""
    max_gp = p_base['GP'].max()
    scale  = max_gp / 44.0
    min_gp = max(1, round(0.70 * max_gp))

    def top10(df, stat, disp):
        sub = df.dropna(subset=[stat])
        top = sub.nlargest(10, stat)[
            ['athlete_display_name', 'team_abbreviation', 'GP', stat]
        ].copy()
        top.columns = ['Player', 'Team', 'GP', disp]
        return top

    q_pts  = p_base['PTS'] >= 525  * scale
    q_reb  = p_base['TRB'] >= 250  * scale
    q_ast  = p_base['AST'] >= 150  * scale
    q_stl  = p_base['STL'] >= 55   * scale
    q_blk  = p_base['BLK'] >= 40   * scale
    q_ftm  = p_base['FTM'] >= 50   * scale
    q_3pm  = p_base['TPM'] >= 25   * scale
    q_fgm  = p_base['FGM'] >= 100  * scale
    q_gp   = p_base['GP']  >= min_gp

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


# ── HTML section builders ─────────────────────────────────────────────────

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
        cells = ''.join(f'<td>{v}</td>' for v in row)
        rows += f'<tr class="{cutoff}">{cells}</tr>\n'
    return (
        '<div id="standings" class="section active">\n'
        '<h2>Standings</h2>\n'
        '<p class="tab-note"><em>Dashed line = playoff cutoff (top 8)</em></p>\n'
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


def build_players_section(p_base, team_abbrevs):
    """Player stats table: abbreviated names with team chip, season totals
    for shooting splits and counting stats, MPG and PPG per-game."""
    # Column order: Player (chip), MPG, PPG, GP, FG, FG%, 3PT, 3PT%, FT, FT%,
    #               OR, DR, TR, A, ST, B, TO, PF
    col_labels = ['Player','MPG','PPG','GP','FG','FG%','3PT','3PT%',
                  'FT','FT%','OR','DR','TR','A','ST','B','TO','PF']
    headers = ''.join(
        f'<th onclick="sortTable(\'tbl_players\',{i})">{h}</th>'
        for i, h in enumerate(col_labels)
    )
    body = ''
    for _, r in p_base.iterrows():
        gp = r['GP']
        name = r['athlete_display_name']
        team = r['team_abbreviation']
        sn = short_name(name)
        name_cell = f'{esc(sn)} <span class="tm">{esc(team)}</span>'

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
        body += (f'<tr data-team="{esc(team)}" data-player="{esc(name)}" '
                 f'data-gp="{int(gp)}">{cells}</tr>\n')

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


# ── CSS & JS ──────────────────────────────────────────────────────────────

PAGE_CSS = """\
  :root {
    --bg:#0f0f0f; --surface:#1a1a1a; --border:#2e2e2e;
    --text:#e8e8e8; --muted:#888; --accent:#f5a623;
    --avg:#aaa;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Courier New',monospace;background:var(--bg);color:var(--text);
        font-size:13px;padding:16px}
  h1{color:var(--accent);font-size:17px;margin-bottom:3px}
  .meta{color:var(--muted);font-size:11px;margin-bottom:24px}
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
  .table-scroll{position:relative;margin-bottom:16px}
  .table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  .table-scroll::after{content:"";position:absolute;top:0;bottom:0;right:0;
    width:28px;pointer-events:none;opacity:0;transition:opacity .15s ease;z-index:5;
    background:linear-gradient(to right, rgba(15,15,15,0), var(--bg))}
  .table-scroll.more-right::after{opacity:1}
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
  /* Controls bar */
  .controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
  .controls label{color:var(--muted);font-size:11px;margin-right:3px}
  .controls .vs{color:var(--muted);font-size:11px}
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
  thead tr:first-child th:first-child{position:sticky;left:0;z-index:4;background:var(--surface)}"""

PAGE_JS = """\
function showTab(id, btn) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
  document.querySelectorAll('#'+id+' .table-wrap').forEach(updateScrollFades);
  /* The back-to-leaders link is only relevant right after a leader click;
     hide it on any manual tab switch. goToPlayer re-shows it afterward. */
  const bl = document.getElementById('backToLeaders');
  if (bl) bl.style.display = 'none';
}

/* -- Horizontal-scroll fade -- */
function updateScrollFades(wrap) {
  const scroll = wrap.closest('.table-scroll');
  if (!scroll) return;
  scroll.classList.toggle('more-right',
    wrap.scrollWidth - wrap.clientWidth - wrap.scrollLeft > 1);
}
function initScrollFades() {
  document.querySelectorAll('.table-wrap').forEach(wrap => {
    updateScrollFades(wrap);
    wrap.addEventListener('scroll', () => updateScrollFades(wrap), {passive:true});
  });
}
window.addEventListener('load', initScrollFades);
window.addEventListener('resize', () =>
  document.querySelectorAll('.table-wrap').forEach(updateScrollFades));

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
}"""


# ── Page assembly ─────────────────────────────────────────────────────────

def assemble_page(display_date, data_through_iso,
                  standings_html, leaders_html, team_eff_html,
                  team_totals_html, players_html, abbreviations_html):
    """Combine all sections into the final HTML string."""
    tabs = [
        ('standings', 'Standings'),
        ('leaders', 'Leaders'),
        ('teameff', 'Team Efficiency'),
        ('teamtotals', 'Team Totals'),
        ('players', 'Players'),
        ('abbreviations', 'Abbreviations'),
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
        '<title>WNBA 2026 \u2014 Stats</title>\n'
        f'<style>\n{PAGE_CSS}\n</style>\n'
        '</head>\n<body>\n\n'
        '<h1>WNBA 2026 \u2014 Season Stats</h1>\n'
        f'<div class="meta">Fast, ad-free — updated {display_date}</div>\n\n'
        f'<div class="tabs">\n{tab_buttons}\n</div>\n\n'
        f'{standings_html}\n'
        f'{leaders_html}\n'
        f'{team_eff_html}\n'
        f'{team_totals_html}\n'
        f'{players_html}\n'
        f'{abbreviations_html}\n'
        f'<script>\n{PAGE_JS}\n</script>\n'
        '<!-- Cloudflare Web Analytics -->'
        '<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
        'data-cf-beacon=\'{"token": "7397748b2cd6455b8887cfe01269a48b"}\'></script>'
        '<!-- End Cloudflare Web Analytics -->\n'
        '</body>\n</html>'
    )


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    player_raw, team_raw = load_data()

    through_dt   = pd.to_datetime(player_raw['game_date'].max())
    display_date = through_dt.strftime('%B %d, %Y')
    data_through_iso = through_dt.strftime('%Y-%m-%d')

    standings_df    = compute_standings(team_raw)
    p_base          = compute_player_base(player_raw)
    run_data_guards(player_raw, p_base)
    team_stats_df   = compute_team_stats(team_raw)
    ff_df, team_list = compute_four_factors(team_raw, player_raw)
    leaders         = compute_leaders(p_base)

    # Team abbreviations for player/leader filters
    team_abbrevs = sorted(p_base['team_abbreviation'].dropna().unique())

    # Full team name options for efficiency and totals matchup bars
    team_options = '\n'.join(
        f'<option value="{esc(t)}">{esc(t)}</option>' for t in sorted(team_list)
    )

    # Build each section
    standings_html     = build_standings_section(standings_df)
    leaders_html       = build_leaders_section(leaders, team_abbrevs)
    team_eff_html      = build_team_efficiency_section(ff_df, team_options)
    team_totals_html   = build_team_totals_section(team_stats_df, team_options)
    players_html       = build_players_section(p_base, team_abbrevs)
    abbreviations_html = build_abbreviations_section()

    html = assemble_page(display_date, data_through_iso,
                         standings_html, leaders_html, team_eff_html,
                         team_totals_html, players_html, abbreviations_html)

    OUTPUT.write_text(html)
    print(f"Written -> {OUTPUT}")
    print(f"Players: {len(p_base)}  |  Teams: {len(team_stats_df)-1}"
          f"  |  FF rows: {len(ff_df)}")


if __name__ == '__main__':
    main()
