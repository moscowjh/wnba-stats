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
        cells = ''.join(f'<td>{v if pd.notna(v) else "---"}</td>' for v in row)
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
            <th colspan="4" class="group-header">Offensive</th>
            <th colspan="4" class="group-header">Defensive</th>
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


def compute_standings(team_raw):
    """Win-loss standings with Pythagorean expected record."""
    std = team_raw.groupby('team_display_name').agg(
        GP     = ('game_id', 'count'),
        W      = ('team_winner', 'sum'),
        PF_tot = ('team_score', 'sum'),
        PA_tot = ('opponent_team_score', 'sum'),
    ).reset_index()

    std['L']    = std['GP'] - std['W']
    std['Win%'] = (std['W'] / std['GP']).apply(fmt_winpct)
    std['PF']   = (std['PF_tot'] / std['GP']).round(1)
    std['PA']   = (std['PA_tot'] / std['GP']).round(1)
    std['Diff'] = (std['PF'] - std['PA']).round(1)

    std['pyth'] = std['PF_tot']**PYTH_EXP / (std['PF_tot']**PYTH_EXP + std['PA_tot']**PYTH_EXP)
    std['XW']   = (std['pyth'] * std['GP']).round(1)
    std['XL']   = (std['GP'] - std['XW']).round(1)

    std = std.sort_values(['W', 'Diff'], ascending=[False, False])
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


def build_player_stats_df(p_base):
    """Per-game player stats table from the shared aggregation."""
    rows = []
    for _, r in p_base.iterrows():
        gp = r['GP']
        rows.append({
            'Player': r['athlete_display_name'],
            'Team':   r['team_abbreviation'],
            'Pos':    r['athlete_position_abbreviation'],
            'GP':     int(gp),
            'MPG':    round(r['MIN']/gp, 1),
            'PPG':    round(r['PTS']/gp, 1),
            'FG':     ma(r['FGM']/gp, r['FGA']/gp),
            'FG%':    pct(r['FGM'], r['FGA']),
            '3PT':    ma(r['TPM']/gp, r['TPA']/gp),
            '3PT%':   pct(r['TPM'], r['TPA']),
            'FT':     ma(r['FTM']/gp, r['FTA']/gp),
            'FT%':    pct(r['FTM'], r['FTA']),
            'OR':     round(r['ORB']/gp, 1),
            'DR':     round(r['DRB']/gp, 1),
            'TR':     round(r['TRB']/gp, 1),
            'A':      round(r['AST']/gp, 1),
            'ST':     round(r['STL']/gp, 1),
            'B':      round(r['BLK']/gp, 1),
            'TO':     round(r['TOV']/gp, 1),
            'PF':     round(r['PF']/gp, 1),
        })
    return pd.DataFrame(rows)


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

    Pace is Pace/40 — possessions per 40 minutes, adjusted for OT games
    using actual team minutes from the player box (BBRef methodology).
    Ratings use the average of team and opponent possession estimates as
    a common denominator, also matching BBRef.
    """
    # Team minutes per game from player box — needed for OT-adjusted pace.
    # In a regulation 40-min game each team logs 200 minutes (5 × 40);
    # OT adds 25 minutes per team per OT period.
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

    # Possessions (Dean Oliver estimate)
    ff['POSS']     = ff['FGA'] - ff['ORB'] + ff['TOV'] + 0.44 * ff['FTA']
    ff['OPP_POSS'] = ff['OPP_FGA'] - ff['OPP_ORB'] + ff['OPP_TOV'] + 0.44 * ff['OPP_FTA']
    # Average of team and opponent estimates — common denominator for ratings
    ff['POSS_avg'] = (ff['POSS'] + ff['OPP_POSS']) / 2

    # Ratings: both use the averaged denominator (BBRef methodology)
    ff['ORtg'] = (100 * ff['PTS']     / ff['POSS_avg']).round(1)
    ff['DRtg'] = (100 * ff['OPP_PTS'] / ff['POSS_avg']).round(1)
    ff['NRtg'] = (ff['ORtg'] - ff['DRtg']).round(1)
    # Pace/40: normalize to 40 minutes of actual game time (handles OT)
    ff['Pace'] = (40 * (ff['POSS'] + ff['OPP_POSS']) / (2 * (ff['TEAM_MINUTES'] / 5))).round(1)

    # Offensive four factors
    ff['O_eFG%']   = ((ff['FGM'] + 0.5*ff['TPM']) / ff['FGA'] * 100).round(1)
    ff['O_TOV%']   = (ff['TOV'] / (ff['FGA'] + 0.44*ff['FTA'] + ff['TOV']) * 100).round(1)
    ff['O_ORB%']   = (ff['ORB'] / (ff['ORB'] + ff['OPP_DRB']) * 100).round(1)
    ff['O_FT/FGA'] = (ff['FTM'] / ff['FGA']).round(3)

    # Defensive four factors
    ff['D_eFG%']   = ((ff['OPP_FGM'] + 0.5*ff['OPP_3PM']) / ff['OPP_FGA'] * 100).round(1)
    ff['D_TOV%']   = (ff['OPP_TOV'] / (ff['OPP_FGA'] + 0.44*ff['OPP_FTA'] + ff['OPP_TOV']) * 100).round(1)
    ff['D_DRB%']   = (ff['DRB'] / (ff['DRB'] + ff['OPP_ORB']) * 100).round(1)
    ff['D_FT/FGA'] = (ff['OPP_FTM'] / ff['OPP_FGA']).round(3)

    ff = ff.sort_values('NRtg', ascending=False)
    team_list = ff['team_display_name'].tolist()

    # League average row
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
    """Top-10 category leaders with prorated WNBA qualifying minimums.

    Full-season thresholds (44 games) are scaled by max_gp/44 so early-
    season leaders aren't dominated by small-sample outliers.

    Volume minimums (WNBA official):
      Scoring 525 pts | Rebounds 250 | Assists 150 | Steals 55 | Blocks 40
      FT% 50 FTM | 3PT% 25 3PM | FG%/eFG%/TS% 100 FGM
      Off Reb / Turnovers: 70% of max team games played (GP threshold)
    """
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
    # Scoring group
    leaders['Scoring']   = top10(p_base[q_pts],  'PPG',  'PPG')
    leaders['3PT%']      = top10(p_base[q_3pm],  '3PT%', '3PT%')
    leaders['eFG%']      = top10(p_base[q_fgm],  'eFG%', 'eFG%')
    leaders['FT%']       = top10(p_base[q_ftm],  'FT%',  'FT%')
    leaders['TS%']       = top10(p_base[q_fgm],  'TS%',  'TS%')
    leaders['Assists']   = top10(p_base[q_ast],  'APG',  'APG')
    # Other group
    leaders['Rebounds']  = top10(p_base[q_reb],  'RPG',  'RPG')
    leaders['Off Reb']   = top10(p_base[q_gp],   'ORPG', 'ORPG')
    leaders['Steals']    = top10(p_base[q_stl],  'SPG',  'SPG')
    leaders['Blocks']    = top10(p_base[q_blk],  'BPG',  'BPG')
    leaders['Turnovers'] = top10(p_base[q_gp],   'TPG',  'TPG')
    return leaders


# ── HTML section builders ─────────────────────────────────────────────────

def build_standings_section(standings_df):
    return (
        '<div id="standings" class="section active">\n'
        '<h2>Standings</h2>\n'
        f'{df_to_html(standings_df, "tbl_standings")}\n'
        '</div>\n'
    )


def build_four_factors_section(ff_df, team_options):
    return (
        '<div id="fourfactors" class="section">\n'
        '<h2>Four Factors</h2>\n'
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


def build_team_stats_section(team_stats_df, team_options):
    return (
        '<div id="teamstats" class="section">\n'
        '<h2>Team Stats \u2014 Per Game</h2>\n'
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


def build_players_section(player_stats_df, player_options_html):
    return (
        '<div id="players" class="section">\n'
        '<h2>Player Stats \u2014 Per Game</h2>\n'
        '<div class="filter-row">\n'
        '  <div><label>Search &nbsp;</label>\n'
        '    <input type="text" id="playerSearch" onkeyup="filterPlayers()"'
        ' placeholder="player or team...">\n'
        '  </div>\n'
        '  <div><label>Position &nbsp;</label>\n'
        '    <select id="posFilter" onchange="filterPlayers()">\n'
        '      <option value="">All</option>\n'
        '      <option>G</option><option>F</option><option>C</option>\n'
        '      <option>G-F</option><option>F-C</option>\n'
        '    </select>\n'
        '  </div>\n'
        '</div>\n'
        '<div class="matchup-bar">\n'
        '  <label>Compare &nbsp;</label>\n'
        f'  <select id="p_sel1" onchange="filterPlayers()">\n'
        f'    <option value="">\u2014</option>\n    {player_options_html}\n'
        f'  </select>\n'
        f'  <select id="p_sel2" onchange="filterPlayers()">\n'
        f'    <option value="">\u2014</option>\n    {player_options_html}\n'
        f'  </select>\n'
        f'  <select id="p_sel3" onchange="filterPlayers()">\n'
        f'    <option value="">\u2014</option>\n    {player_options_html}\n'
        f'  </select>\n'
        '  <button onclick="clearPlayerCompare()">Clear</button>\n'
        '</div>\n'
        f'{df_to_html(player_stats_df, "tbl_players", data_col="Player", data_attr="data-player")}\n'
        '</div>\n'
    )


def build_leaders_section(leaders):
    parts = [
        '<div id="leaders" class="section">\n'
        '<h2>Category Leaders</h2>\n'
        '<div class="leaders-grid">\n'
    ]
    for label, ldr_df in leaders.items():
        safe_id = 'ldr_' + label.replace(' ','_').replace('%','pct').replace('/','_')
        parts.append(
            f'<div class="leader-card"><h3>{label}</h3>'
            f'{df_to_html(ldr_df, safe_id)}</div>\n'
        )
    parts.append('</div>\n</div>\n')
    return ''.join(parts)


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
  .tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px}
  .tab{cursor:pointer;padding:5px 12px;border:1px solid var(--border);
        color:var(--muted);background:var(--surface);font-family:inherit;
        font-size:12px;letter-spacing:.5px}
  .tab.active{border-color:var(--accent);color:var(--accent)}
  .section{display:none}.section.active{display:block}
  .table-scroll{position:relative;margin-bottom:16px}
  .table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  /* Right-edge fade: signals there are more columns to swipe to, and is
     toggled off by JS (.more-right) at the end of the scroll. Right side
     only — the first column is sticky, so the left never hides identity. */
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
  .leaders-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:18px}
  .leader-card h3{font-size:11px;color:var(--muted);text-transform:uppercase;
                   letter-spacing:1px;margin-bottom:7px}
  .leader-card table{font-size:12px}
  .rank-1 td:first-child{color:var(--accent);font-weight:bold}
  .filter-row{margin-bottom:12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  label{color:var(--muted);font-size:11px}
  input[type=text]{background:var(--surface);border:1px solid var(--border);
                    color:var(--text);font-family:inherit;font-size:12px;
                    padding:5px 9px;width:190px}
  input[type=text]:focus{outline:none;border-color:var(--accent)}
  select{background:var(--surface);border:1px solid var(--border);color:var(--text);
          font-family:inherit;font-size:12px;padding:5px 9px}
  .matchup-bar{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
  .matchup-bar .vs{color:var(--muted);font-size:11px}
  .matchup-bar button{background:var(--surface);border:1px solid var(--border);
                       color:var(--muted);font-family:inherit;font-size:11px;
                       padding:5px 10px;cursor:pointer}
  .matchup-bar button:hover{border-color:var(--accent);color:var(--accent)}
  .highlight-team td{background:#1f1a0f}
  .highlight-team td:first-child{background:#1f1a0f}
  /* Sticky first column — body cells */
  tbody td:first-child{position:sticky;left:0;z-index:1;background:var(--bg)}
  tr:hover td:first-child{background:var(--surface)}
  tr.lg-avg td:first-child{background:var(--bg)}
  /* Sticky corner — first cell of first header row only.
     Scoped to tr:first-child to avoid matching eFG% in the Four Factors
     two-row header, where eFG% is :first-child of the second header row. */
  thead tr:first-child th:first-child{position:sticky;left:0;z-index:4;background:var(--surface)}"""

PAGE_JS = """\
function showTab(id, btn) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
  // Tables in a hidden section measure 0 wide; recompute fades once shown.
  document.querySelectorAll('#'+id+' .table-wrap').forEach(updateScrollFades);
}

/* -- Horizontal-scroll affordance: show a right-edge fade while a table
   has more columns to the right, hide it at the end of the scroll. -- */
function updateScrollFades(wrap) {
  const scroll = wrap.closest('.table-scroll');
  if (!scroll) return;
  const more = wrap.scrollWidth - wrap.clientWidth - wrap.scrollLeft > 1;
  scroll.classList.toggle('more-right', more);
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

/* -- Player stats: unified filter (search + position + compare) -- */
function filterPlayers() {
  const s1 = document.getElementById('p_sel1').value;
  const s2 = document.getElementById('p_sel2').value;
  const s3 = document.getElementById('p_sel3').value;
  const active = [s1, s2, s3].filter(Boolean);

  const q   = document.getElementById('playerSearch').value.toLowerCase();
  const pos = document.getElementById('posFilter').value;

  document.querySelectorAll('#tbl_players tbody tr').forEach(row => {
    row.classList.remove('highlight-team');

    if (active.length > 0) {
      const player = row.getAttribute('data-player') || '';
      const show = active.includes(player);
      row.style.display = show ? '' : 'none';
      if (show) row.classList.add('highlight-team');
    } else {
      const matchQ = !q   || row.textContent.toLowerCase().includes(q);
      const matchP = !pos || row.cells[2].textContent.trim() === pos;
      row.style.display = (matchQ && matchP) ? '' : 'none';
    }
  });
}

function clearPlayerCompare() {
  document.getElementById('p_sel1').value = '';
  document.getElementById('p_sel2').value = '';
  document.getElementById('p_sel3').value = '';
  document.getElementById('playerSearch').value = '';
  document.getElementById('posFilter').value = '';
  filterPlayers();
}

/* -- Four factors matchup filter -- */
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

/* -- Team stats matchup filter -- */
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

// Bold #1 in leader cards
document.querySelectorAll('.leader-card tbody tr:first-child').forEach(r => r.classList.add('rank-1'));"""


# ── Page assembly ─────────────────────────────────────────────────────────

def build_option_lists(team_list, player_stats_df):
    """Build HTML <option> strings for team and player dropdowns."""
    team_options = '\n'.join(
        f'<option value="{esc(t)}">{esc(t)}</option>' for t in sorted(team_list)
    )
    player_pairs = sorted(
        zip(player_stats_df['Player'], player_stats_df['Team']),
        key=lambda x: x[0]
    )
    player_options = '\n'.join(
        f'<option value="{esc(str(n))}">{esc(str(n))} ({esc(str(t))})</option>'
        for n, t in player_pairs
    )
    return team_options, player_options


def assemble_page(display_date, standings_df, ff_df, team_stats_df,
                  player_stats_df, leaders, team_options, player_options):
    """Combine all sections into the final HTML string."""
    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>WNBA 2026 \u2014 Stats</title>\n'
        f'<style>\n{PAGE_CSS}\n</style>\n'
        '</head>\n<body>\n\n'
        '<h1>WNBA 2026 \u2014 Season Stats</h1>\n'
        f'<div class="meta">Stats as of {display_date}</div>\n\n'
        '<div class="tabs">\n'
        '  <button class="tab active" onclick="showTab(\'standings\',this)">Standings</button>\n'
        '  <button class="tab" onclick="showTab(\'fourfactors\',this)">Four Factors</button>\n'
        '  <button class="tab" onclick="showTab(\'teamstats\',this)">Team Stats</button>\n'
        '  <button class="tab" onclick="showTab(\'players\',this)">Players</button>\n'
        '  <button class="tab" onclick="showTab(\'leaders\',this)">Leaders</button>\n'
        '</div>\n\n'
        f'<!-- STANDINGS -->\n{build_standings_section(standings_df)}\n'
        f'<!-- FOUR FACTORS -->\n{build_four_factors_section(ff_df, team_options)}\n'
        f'<!-- TEAM STATS -->\n{build_team_stats_section(team_stats_df, team_options)}\n'
        f'<!-- PLAYER STATS -->\n{build_players_section(player_stats_df, player_options)}\n'
        f'<!-- LEADERS -->\n{build_leaders_section(leaders)}\n'
        f'<script>\n{PAGE_JS}\n</script>\n'
        # Cloudflare Web Analytics beacon (privacy-first, cookieless). The token
        # is a public client-side identifier, safe to commit.
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

    standings_df   = compute_standings(team_raw)
    p_base         = compute_player_base(player_raw)
    player_stats_df = build_player_stats_df(p_base)
    team_stats_df  = compute_team_stats(team_raw)
    ff_df, team_list = compute_four_factors(team_raw, player_raw)
    leaders        = compute_leaders(p_base)

    team_options, player_options = build_option_lists(team_list, player_stats_df)

    html = assemble_page(display_date, standings_df, ff_df, team_stats_df,
                         player_stats_df, leaders, team_options, player_options)

    OUTPUT.write_text(html)
    print(f"Written -> {OUTPUT}")
    print(f"Players: {len(player_stats_df)}  |  Teams: {len(team_stats_df)-1}"
          f"  |  FF rows: {len(ff_df)}")


if __name__ == '__main__':
    main()
