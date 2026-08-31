"""FIBA adapter — fetch and parse fiba.basketball's server-rendered pages.

**Moved from `wbb-lab/fiba/` on 2026-08-26**, not imported: the lab imports
production and production never imports the lab, so when the lab produces
something the site needs, the code crosses the boundary deliberately and once.
Origin: `fetch_fiba_game.py` (2026-07-17 spike) and `parse_fiba_game.py`
(2026-07-19, hardened). Findings doc: `wbb-lab/fiba/fiba-wwc-2026-data-spike-findings.md`.

## Why HTML and not an API

FIBA's game pages are Next.js **server-rendered**: the entire data payload
ships inline in the raw HTML as RSC "flight" fragments
(`self.__next_f.push([1,"..."])`). One plain GET yields rosters, the full
player and team box score, quarter line scores, and complete play-by-play with
shot coordinates. No JavaScript execution, no auth, no official API.

There *is* an underlying gateway (`digital-api.fiba.basketball/hapi`) whose
public client subscription key ships to every browser. **Do not build on it** —
it is FIBA's key, not ours, and can rotate without notice.

## Two things that are load-bearing

1. **The browser User-Agent.** FIBA's WAF returns 403 to a default urllib or
   curl UA and 200 to a browser one. Verified on a GitHub Actions runner
   (2026-08-11, run `31508648350`, Azure IP) — the runner behaves exactly like
   a residential IP, so this is not a datacentre-IP problem and the UA is
   required in production too. Same class of failure as the 2026-08-05 ESPN
   Akamai 403.

2. **Anchoring on FIBA's data-field signatures, never on Next.js structure.**
   The UI framework will change; the stat schema will not. The spike's original
   marker-and-outermost-object extractor failed two ways on real payloads:

   - It matched markers inside **i18n dictionaries** — the string "boxscore"
     appears as a UI label, so it happily returned a localisation blob as "the
     box score".
   - It **missed the resolved box score entirely.** FIBA's RSC stream
     serialises the stat block once and then *references* it
     (`$1b:props:gameDetails:c:0:Children:0:Stats`). The block holding the real
     numbers carries no lowercase marker, so only the unusable `$…` placeholder
     copy was ever captured.

   So player lines are signature-matched on `"Id":"P_<id>","Stats":{`, team
   totals on the enclosing `{"Id":"T_<org>"` container, and every other read is
   pinned to an exact key anchor.
"""

import json
import re
import urllib.request

#: The WAF check. See module docstring — this is not decoration.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

FIBA_ORIGIN = "https://www.fiba.basketball"

_FLIGHT_RE = re.compile(
    r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\s*\]\)', re.S)
_MAX_OBJ = 3_000_000


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def flight_payload(html):
    """Concatenate every flight fragment into one string, unescaping the JS
    string literals. Fragments that fail to decode are skipped rather than
    raising — a single malformed chunk must not lose the whole page."""
    parts = []
    for m in _FLIGHT_RE.finditer(html):
        try:
            parts.append(json.loads(f'"{m.group(1)}"'))
        except json.JSONDecodeError:
            pass
    return "".join(parts)


def _balanced(s, start):
    """`s[start:end+1]` for the JSON object/array opening at `s[start]`."""
    open_c = s[start]
    close_c = "}" if open_c == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, min(len(s), start + _MAX_OBJ)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _value_after(payload, key_anchor, opener):
    """Parse the JSON value beginning at `opener` after an exact `key_anchor`.

    Anchors pin a data key precisely (`'"playersTeamA":['`,
    `'"game":{"gameId"'`) so there is no walk and no chance of matching an i18n
    label. Key names never contain `{` or `[`, so the first opener at or after
    the anchor is always the value's.
    """
    pos = payload.find(key_anchor)
    if pos == -1:
        return None
    br = payload.find(opener, pos)
    if br == -1:
        return None
    js = _balanced(payload, br)
    if not js:
        return None
    try:
        return json.loads(js)
    except json.JSONDecodeError:
        return None


def _find_gamedata(payload):
    """The resolved gameData object, located by its `playersTeamA` anchor."""
    for anchor in ('"playersTeamA":[', '"playersTeamA":'):
        pos = payload.find(anchor)
        if pos == -1:
            continue
        # Walk back to the enclosing object's opening brace.
        depth = 0
        for i in range(pos, -1, -1):
            if payload[i] == "}":
                depth += 1
            elif payload[i] == "{":
                if depth == 0:
                    js = _balanced(payload, i)
                    if js:
                        try:
                            gd = json.loads(js)
                        except json.JSONDecodeError:
                            continue
                        if "playersTeamA" in gd:
                            return gd
                    break
                depth -= 1
    return None


# ── Box score ─────────────────────────────────────────────────────────────
# Readable names for FIBA's stat abbreviations. Anything unlisted passes
# through untouched, so a field FIBA adds is never silently dropped.
_STAT_ALIAS = {
    "PTS": "pts", "MIN": "min", "TP": "time_played", "PM": "plus_minus",
    "EFF": "eff", "AS": "ast", "TO": "tov", "ST": "stl", "BS": "blk",
    "BSR": "blk_recv", "PF": "pf", "FD": "fouls_drawn",
    "REB": "reb", "OR": "oreb", "DR": "dreb", "TREB": "team_reb",
    "FGM": "fgm", "FGA": "fga", "FGP": "fg_pct",
    "FG2M": "fg2m", "FG2A": "fg2a", "FG2P": "fg2_pct",
    "FG3M": "fg3m", "FG3A": "fg3a", "FG3P": "fg3_pct",
    "FTM": "ftm", "FTA": "fta", "FTP": "ft_pct",
    "FGIM": "paint_m", "FGIA": "paint_a", "FGIP": "paint_pct",
    "CB": "on_court", "Starter": "starter", "HasPlayed": "played",
}

_PLAYER_RE = re.compile(r'"Id":"P_(\d+)","Stats":\{')
_TEAM_RE = re.compile(r'\{"Id":"T_(\d+)"')


def _alias(stats):
    return {_STAT_ALIAS.get(k, k): v for k, v in stats.items()}


def parse_boxlines(payload):
    """(players_by_personId, teams_by_orgId), keys aliased.

    Player lines sit immediately after their `Id`; team totals sit on the
    enclosing container *after* its Children array, so the whole container is
    matched. The `"PTS" in js` guard is what rejects the placeholder copies
    the RSC stream leaves behind.
    """
    players, teams = {}, {}
    for m in _PLAYER_RE.finditer(payload):
        brace = payload.find("{", m.end() - 1)
        js = _balanced(payload, brace)
        if not js or '"PTS"' not in js:
            continue
        try:
            players[int(m.group(1))] = _alias(json.loads(js))
        except json.JSONDecodeError:
            continue
    for m in _TEAM_RE.finditer(payload):
        org = int(m.group(1))
        if org in teams:
            continue
        js = _balanced(payload, m.start())
        if not js or '"PTS"' not in js:
            continue
        try:
            container = json.loads(js)
        except json.JSONDecodeError:
            continue
        st = container.get("Stats")
        if isinstance(st, dict) and "PTS" in st:
            teams[org] = _alias(st)
    return players, teams


def _roster(players):
    out = {}
    for p in players or []:
        out[p.get("personId")] = {
            "name": f'{p.get("firstName", "")} {p.get("lastName", "")}'.strip(),
            "number": p.get("uniformNumber"),
            "position": p.get("position"),
            "captain": p.get("isCaptain"),
        }
    return out


def parse_game(payload):
    """One game: meta, quarter line score, both rosters, full box score."""
    gd = _find_gamedata(payload)
    if not gd:
        raise ValueError(
            "gameData object not found — FIBA's page structure may have "
            "changed. Check the signature anchors before assuming an outage.")
    game = gd.get("game") or {}
    roster_a, roster_b = _roster(gd.get("playersTeamA")), _roster(gd.get("playersTeamB"))
    players_box, teams_box = parse_boxlines(payload)
    team_a, team_b = game.get("teamA") or {}, game.get("teamB") or {}
    rnd = game.get("round") or {}

    # Quarter line score, differenced from the PBP period headers' cumulative
    # scores. FIBA gives cumulative; a box score wants per-period.
    linescore, prev_a, prev_b = [], 0, 0
    items = (gd.get("playByPlay") or {}).get("items") or {}
    for period in ("Q1", "Q2", "Q3", "Q4", "OT1", "OT2", "OT3"):
        qd = items.get(period)
        if not qd:
            continue
        a, b = qd.get("scoreA", prev_a), qd.get("scoreB", prev_b)
        linescore.append({"period": period, "a": a - prev_a, "b": b - prev_b})
        prev_a, prev_b = a, b

    return {
        "meta": {
            "game_id": game.get("gameId"),
            "status": game.get("statusCode"),
            "round": rnd.get("roundName"),
            "round_code": rnd.get("roundCode"),
            "group": game.get("groupPairingCode"),
            "datetime_utc": game.get("gameDateTimeUTC"),
            "venue": game.get("venueName"),
            "team_a": {"code": team_a.get("code"), "name": team_a.get("officialName"),
                       "org_id": team_a.get("organisationId")},
            "team_b": {"code": team_b.get("code"), "name": team_b.get("officialName"),
                       "org_id": team_b.get("organisationId")},
            "score_a": game.get("teamAScore"),
            "score_b": game.get("teamBScore"),
        },
        "linescore": linescore,
        "rosters": {"A": roster_a, "B": roster_b},
        "box": {
            "A": {"players": {p: players_box[p] for p in roster_a if p in players_box},
                  "team": teams_box.get(team_a.get("organisationId"))},
            "B": {"players": {p: players_box[p] for p in roster_b if p in players_box},
                  "team": teams_box.get(team_b.get("organisationId"))},
        },
    }


_SCHED_RE = re.compile(r'\{"gameId":(\d+),"gameName"')



def _game_number(game_name):
    """FIBA's official game number out of `gameName` ("29924-29-A" -> 29).

    Returns None for anything that does not carry one, including the group
    games, whose middle segment is the group letter ("29919-A-3").
    """
    parts = (game_name or "").split("-")
    if len(parts) == 3 and parts[1].isdigit():
        return int(parts[1])
    return None


def parse_schedule(payload):
    """Every game on an event `/games` page, deduped and sorted by tip.

    Anchored on `{"gameId":N,"gameName"` — the full game objects, which carry
    the same shape as a game page's `game` object. That signature is what
    distinguishes them from the minimal three-game "upcoming" widget, whose
    entries are `{"gameId":N,"status"` with no teams at all.
    """
    seen, out = set(), []
    for m in _SCHED_RE.finditer(payload):
        gid = int(m.group(1))
        if gid in seen:
            continue
        js = _balanced(payload, m.start())
        if not js or '"teamA"' not in js:
            continue
        try:
            g = json.loads(js)
        except json.JSONDecodeError:
            continue
        seen.add(gid)
        ta, tb = g.get("teamA") or {}, g.get("teamB") or {}
        rnd = g.get("round") or {}
        status = {"VALID": "final"}.get(g.get("statusCode"))
        if status is None:
            status = "live" if g.get("isLive") else "scheduled"
        out.append({
            "game_id": gid,
            # FIBA's OFFICIAL game number — the one printed in the schedule
            # and used in "Winner of Game 27". It is not a field of its own;
            # it is the middle segment of `gameName`, e.g. "29924-29-A" -> 29.
            # (`gameNumber` is a different thing: "A", or the within-group
            # index for group games. Do not use it for this.)
            #
            # Worth having because it is an EXACT join key for knockout
            # fixtures, which cannot be joined on teams — they are TBD until
            # the bracket resolves. The alternative, ordering by kickoff time,
            # is unusable while FIBA is still publishing placeholder times.
            "game_number": _game_number(g.get("gameName")),
            "status": status,
            "datetime_utc": g.get("gameDateTimeUTC"),
            # FIBA's own words for how a knockout fixture is filled
            # ("Winner of Game 27", "2nd of group A"), null for group games.
            # Independent confirmation of what a game number refers to.
            "team_a_from": g.get("teamAFrom"),
            "team_b_from": g.get("teamBFrom"),
            # False while the listed time is a placeholder. FIBA sets midnight
            # local for undecided fixtures, so this is the honest signal that a
            # timestamp carries no ordering information.
            "has_real_time": g.get("hasTimeGameDateTime"),
            "round": rnd.get("roundName"),
            "round_code": rnd.get("roundCode"),
            "group": g.get("groupPairingCode"),
            "team_a": {"code": ta.get("code"), "name": ta.get("officialName")},
            "team_b": {"code": tb.get("code"), "name": tb.get("officialName")},
            "score_a": g.get("teamAScore"),
            "score_b": g.get("teamBScore"),
        })
    out.sort(key=lambda x: x["datetime_utc"] or "")
    return out


def event_games_url(event_slug):
    return f"{FIBA_ORIGIN}/en/events/{event_slug}/games"


def game_url(event_slug, game_id, code_a, code_b):
    return (f"{FIBA_ORIGIN}/en/events/{event_slug}/games/"
            f"{game_id}-{code_a}-{code_b}")
