#!/usr/bin/env python3
"""build_ivy_pages.py — Ivy League women's basketball, LOCAL PROTOTYPE emitter.

Follows the WWC pattern (`sites/wwc/build_wwc_pages.py`): purpose-built, not a
fork, and importing only the shared layer —

    sag.render.chrome   design tokens (one divergent --accent), footer,
                        scroll fade
    sag.seo             canonical URLs, social tags

Everything else is this site's own components. Reads only what
`fetch_data.py` left under `sites/ivy/data/raw/`; never touches the network.
Writes static HTML into `sites/ivy/public/` (gitignored), which
`.github/workflows/ivy.yml` deploys to the `ivy-hoops` Worker. PRIVATE PREVIEW
until launch: the hostname sits behind Cloudflare Access, every page carries
`noindex`, and robots.txt disallows everything.

Pages:
    /                   Home — draft narrative, last night, tonight/next, the eight
    /schedule/          Every game for all eight, filterable by team
    /games/<event_id>/  One URL per game involving an Ivy team. Before tip it is
                        a GAME PROGRAM; once final the same URL is the box score.

Two lifecycle states per game, derived from the feed's `completed` flag and
never from the clock — the same rule as the WWC site.

Correct-or-blank: a stat ESPN did not carry renders "—", never 0. Non-D-I
opponents are labelled as such, and any number computed for them says it comes
from their games against D-I opponents only (all ESPN carries).

Usage:
    .venv/bin/python sites/ivy/build_ivy_pages.py
"""

import json
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape as esc
from zoneinfo import ZoneInfo

from sag import seo
from sag.render import chrome

from config import (D1_PARENT_GROUP, IVY, IVY_TEAMS, PREV_SEASON,
                    SEASON_LABEL, SHORT_NAME_OVERRIDES, TZ)

RAW = IVY.data_dir / "raw"
#: The build output. Deployed by .github/workflows/ivy.yml straight from CI and
#: NOT committed (gitignored): unlike the frozen WWC archive, every data pull
#: rewrites ~170 game pages, which would churn the public repo daily.
OUT = IVY.public_dir
ET = ZoneInfo(TZ)
DASH = '<span class="mu">—</span>'


# ══ Loading ════════════════════════════════════════════════════════════════

def jload(p):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def num(v):
    """ESPN number-ish → float, or None. '--' and '' are absent, not zero."""
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("value", v.get("displayValue"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def made_att(s):
    try:
        m, a = s.split("-")
        return int(m), int(a)
    except (AttributeError, ValueError):
        return None, None


def lum(hexcol):
    try:
        r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (TypeError, ValueError):
        return None
    f = lambda c: c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b)


class Team:
    def __init__(self, tid, info):
        t = (info or {}).get("team") or {}
        self.id = tid
        self.ivy = tid in IVY_TEAMS
        self.full = t.get("displayName") or f"Team {tid}"
        self.short = (IVY_TEAMS.get(tid) or SHORT_NAME_OVERRIDES.get(tid)
                      or t.get("shortDisplayName") or self.full)
        self.mascot = t.get("name") or ""
        self.abbr = t.get("abbreviation") or self.short[:4].upper()
        grp = t.get("groups") or {}
        # ESPN is inconsistent here (verified 2026-11-10): non-D-I teams come
        # as group "51" with parent "52", as group "51" with NO parent, or with
        # no groups at all. D-I conferences always carry parent "50". When the
        # group says nothing, fall back to logos — every non-D-I team seen so
        # far ships none, every D-I team does.
        parent = (grp.get("parent") or {}).get("id")
        if parent:
            self.d1 = parent == D1_PARENT_GROUP
        elif grp.get("id") == "51":
            self.d1 = False
        else:
            self.d1 = bool(t.get("logos")) or not info
        self.standing = t.get("standingSummary")
        self.color = self._pick_color(t.get("color"), t.get("alternateColor"))

    @staticmethod
    def _pick_color(c, alt):
        # A stripe has to show on #0f0f0f. Princeton is served as #000000 with
        # orange as the alternate; Brown's brown is barely visible either way.
        for col in (c, alt):
            L = lum(col)
            if L is not None and L > .035:
                return "#" + col
        return "#777"


def load_world():
    teams = {}
    for p in sorted((RAW / "teams").glob("*.json")):
        teams[p.stem] = Team(p.stem, jload(p))
    rosters = {p.stem: jload(p) for p in (RAW / "rosters").glob("*.json")}
    scheds = {}
    for p in (RAW / "schedules").glob("*.json"):
        tid, season = p.stem.split("_")
        scheds[(tid, season)] = jload(p)
    summaries = {p.stem: jload(p) for p in (RAW / "summaries").glob("*.json")}
    meta = jload(IVY.data_dir / "fetch_meta.json") or {}
    return teams, rosters, scheds, summaries, meta


def team(teams, tid, name=None, short=None):
    """Every team on an Ivy schedule was fetched; their OPPONENTS (needed for
    recent form) were not, so those are built from the schedule's own names."""
    if tid not in teams:
        teams[tid] = Team(tid, {"team": {"displayName": name or f"Team {tid}",
                                         "shortDisplayName": short,
                                         "logos": [1]}})
    return teams[tid]


# ══ Games ══════════════════════════════════════════════════════════════════

class Side:
    def __init__(self, c):
        self.tid = c["team"]["id"]
        self.name = c["team"].get("displayName")
        self.short = c["team"].get("shortDisplayName") or self.name
        self.home_away = c.get("homeAway")
        self.score = num(c.get("score"))
        self.winner = c.get("winner")
        rank = (c.get("curatedRank") or {}).get("current")
        self.rank = rank if rank and rank <= 25 else None


class Game:
    def __init__(self, ev):
        comp = ev["competitions"][0]
        st = (comp.get("status") or {}).get("type") or {}
        self.id = ev["id"]
        self.utc = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        self.et = self.utc.astimezone(ET)
        self.time_valid = comp.get("timeValid", True)
        self.completed = bool(st.get("completed"))
        self.state = st.get("state")
        self.status_detail = st.get("shortDetail") or st.get("detail") or ""
        self.neutral = bool(comp.get("neutralSite"))
        v = comp.get("venue") or {}
        a = v.get("address") or {}
        self.venue = v.get("fullName")
        self.city = ", ".join(x for x in (a.get("city"), a.get("state")) if x)
        self.tv = [b["media"]["shortName"] for b in comp.get("broadcasts") or []
                   if (b.get("media") or {}).get("shortName")]
        self.note = next((n.get("headline") for n in comp.get("notes") or []
                          if n.get("headline")), None)
        sides = [Side(c) for c in comp["competitors"]]
        self.home = next((s for s in sides if s.home_away == "home"), sides[0])
        self.away = next((s for s in sides if s is not self.home), sides[-1])
        self.sides = (self.away, self.home)

    @property
    def ivy_ids(self):
        return [s.tid for s in self.sides if s.tid in IVY_TEAMS]

    @property
    def conf(self):
        return len(self.ivy_ids) == 2

    def side(self, tid):
        return self.home if self.home.tid == tid else self.away

    def other(self, tid):
        return self.away if self.home.tid == tid else self.home

    @property
    def et_date(self):
        return self.et.date()


def games_from(sched):
    return [Game(ev) for ev in (sched or {}).get("events") or []]


def ivy_games(scheds):
    seen = {}
    for tid in IVY_TEAMS:
        for g in games_from(scheds.get((tid, IVY.season))):
            seen.setdefault(g.id, g)
    return sorted(seen.values(), key=lambda g: (g.utc, g.id))


# ══ Box scores ═════════════════════════════════════════════════════════════

PLAYER_KEYS = ("MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL",
               "BLK", "OREB", "DREB", "PF")


def parse_box(summary):
    """Summary JSON → {tid: {players, totals, line, team_stats}} or None."""
    if not summary:
        return None
    out = {}
    comp = (summary.get("header") or {}).get("competitions", [{}])[0]
    for c in comp.get("competitors") or []:
        tid = c["team"]["id"]
        out[tid] = {"line": [num(x.get("displayValue")) for x in c.get("linescores") or []],
                    "score": num(c.get("score")), "players": [], "ts": {}}
    box = summary.get("boxscore") or {}
    for t in box.get("teams") or []:
        tid = t["team"]["id"]
        if tid in out:
            out[tid]["ts"] = {s["name"]: s.get("displayValue")
                              for s in t.get("statistics") or []}
    for blk in box.get("players") or []:
        tid = blk["team"]["id"]
        if tid not in out:
            continue
        for st in blk.get("statistics") or []:
            labels = st.get("labels") or st.get("names") or []
            for a in st.get("athletes") or []:
                ath = a.get("athlete") or {}
                raw = dict(zip(labels, a.get("stats") or []))
                p = {"id": ath.get("id"),
                     # ESPN sometimes sends a stub with only shortName (seen
                     # 2026-11-05, Harvard DNP "S. Smith": no id, no full name).
                     "name": ath.get("displayName") or ath.get("shortName") or "",
                     "short": ath.get("shortName") or ath.get("displayName") or "",
                     "jersey": ath.get("jersey"),
                     "pos": (ath.get("position") or {}).get("abbreviation"),
                     "starter": bool(a.get("starter")),
                     "dnp": bool(a.get("didNotPlay")) or not a.get("stats"),
                     "reason": a.get("reason")}
                for k in ("MIN", "PTS", "REB", "AST", "TO", "STL", "BLK",
                          "OREB", "DREB", "PF"):
                    p[k] = num(raw.get(k))
                for k in ("FG", "3PT", "FT"):
                    p[k + "m"], p[k + "a"] = made_att(raw.get(k))
                out[tid]["players"].append(p)
    return out


def team_line(b):
    """Team totals for one side of one box: prefer the feed's team block."""
    ts = b["ts"]
    fgm, fga = made_att(ts.get("fieldGoalsMade-fieldGoalsAttempted"))
    tpm, tpa = made_att(ts.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
    ftm, fta = made_att(ts.get("freeThrowsMade-freeThrowsAttempted"))
    g = lambda k: num(ts.get(k))
    return {"pts": b["score"], "fgm": fgm, "fga": fga, "tpm": tpm, "tpa": tpa,
            "ftm": ftm, "fta": fta, "reb": g("totalRebounds"),
            "oreb": g("offensiveRebounds"), "dreb": g("defensiveRebounds"),
            "ast": g("assists"), "stl": g("steals"), "blk": g("blocks"),
            "tov": g("totalTurnovers") if g("totalTurnovers") is not None else g("turnovers"),
            "pf": g("fouls"), "pito": g("turnoverPoints"),
            "fbp": g("fastBreakPoints"), "pip": g("pointsInPaint"),
            "lead": g("largestLead")}


# ══ Season aggregates ═════════════════════════════════════════════════════

def add(acc, k, v):
    if v is None:
        acc["_null_" + k] = True
    else:
        acc[k] = acc.get(k, 0) + v


class Season:
    """Everything computed across completed games, for every team we hold."""

    def __init__(self, teams, scheds, summaries):
        self.games_by_team = {}       # tid → [Game] this season, sorted
        self.team_tot = {}            # tid → totals dict
        self.player_tot = defaultdict(dict)  # tid → athlete_id → totals
        self.last_game_line = {}      # (tid, aid) → (Game, player dict)
        for (tid, season), s in scheds.items():
            if season == IVY.season:
                self.games_by_team[tid] = sorted(games_from(s), key=lambda g: g.utc)
        for tid, gs in self.games_by_team.items():
            tot = {"gp": 0, "boxes": 0}
            for g in gs:
                if not g.completed:
                    continue
                tot["gp"] += 1
                b = parse_box(summaries.get(g.id))
                if not b or tid not in b:
                    continue
                opp = next((k for k in b if k != tid), None)
                if not b[tid]["ts"] or opp is None:
                    continue
                tot["boxes"] += 1
                mine, theirs = team_line(b[tid]), team_line(b[opp])
                for k, v in mine.items():
                    add(tot, k, v)
                for k, v in theirs.items():
                    add(tot, "o_" + k, v)
                for p in b[tid]["players"]:
                    if not p["id"]:
                        continue
                    acc = self.player_tot[tid].setdefault(p["id"], {
                        "gp": 0, "name": p["name"], "jersey": p["jersey"],
                        "pos": p["pos"]})
                    if p["dnp"] or not p.get("MIN"):
                        continue
                    acc["gp"] += 1
                    for k in ("MIN", "PTS", "REB", "AST", "TO", "STL", "BLK",
                              "OREB", "DREB", "PF", "FGm", "FGa", "3PTm",
                              "3PTa", "FTm", "FTa"):
                        add(acc, k, p.get(k))
                    self.last_game_line[(tid, p["id"])] = (g, p)
            self.team_tot[tid] = tot

    # ── records ──
    def record(self, tid, conf_only=False, before=None):
        w = l = 0
        for g in self.games_by_team.get(tid, []):
            if not g.completed or (before and g.utc >= before):
                continue
            if conf_only and not g.conf:
                continue
            s = g.side(tid)
            if s.winner is True:
                w += 1
            elif s.winner is False:
                l += 1
        return w, l

    def streak(self, tid):
        res = [g.side(tid).winner for g in self.games_by_team.get(tid, [])
               if g.completed and g.side(tid).winner is not None]
        if not res:
            return None
        last, n = res[-1], 0
        for r in reversed(res):
            if r != last:
                break
            n += 1
        return f"{'W' if last else 'L'}{n}"

    def recent(self, tid, n=5, before=None):
        gs = [g for g in self.games_by_team.get(tid, [])
              if g.completed and (before is None or g.utc < before)]
        return gs[-n:][::-1]

    def next_game(self, tid, after):
        return next((g for g in self.games_by_team.get(tid, [])
                     if not g.completed and g.utc >= after), None)

    # ── per-game rates ──
    def team_rates(self, tid):
        t = self.team_tot.get(tid) or {}
        n = t.get("boxes") or 0
        if not n:
            return None
        def pg(k):
            return None if t.get("_null_" + k) or k not in t else t[k] / n
        def pct(m, a):
            return None if not t.get(a) else 100 * t[m] / t[a]
        def poss(p=""):
            try:
                return t[p + "fga"] - t[p + "oreb"] + t[p + "tov"] + .44 * t[p + "fta"]
            except KeyError:
                return None
        po, pd = poss(), poss("o_")
        pace = None if po is None or pd is None else (po + pd) / 2 / n
        r = {
            "n": n,
            "ppg": pg("pts"), "oppg": pg("o_pts"),
            "fg": pct("fgm", "fga"), "tp": pct("tpm", "tpa"), "ft": pct("ftm", "fta"),
            "efg": (None if not t.get("fga") else
                    100 * (t["fgm"] + .5 * t["tpm"]) / t["fga"]),
            "tpapg": pg("tpa"),
            "rpg": pg("reb"), "orpg": pg("oreb"),
            "orb": (None if not (t.get("oreb") is not None and t.get("o_dreb")) else
                    100 * t["oreb"] / (t["oreb"] + t["o_dreb"])),
            "apg": pg("ast"), "tov": pg("tov"), "spg": pg("stl"), "bpg": pg("blk"),
            "forced": pg("o_tov"),
            "pace": pace,
            "ortg": None if not po else 100 * t["pts"] / po,
            "drtg": None if not pd else 100 * t["o_pts"] / pd,
        }
        r["diff"] = (None if r["ppg"] is None or r["oppg"] is None
                     else r["ppg"] - r["oppg"])
        return r

    def player_rates(self, tid, aid):
        p = self.player_tot.get(tid, {}).get(aid)
        if not p or not p["gp"]:
            return None
        n = p["gp"]
        def pg(k):
            return None if p.get("_null_" + k) or k not in p else p[k] / n
        return {"gp": n, "mpg": pg("MIN"), "ppg": pg("PTS"), "rpg": pg("REB"),
                "apg": pg("AST"), "spg": pg("STL"), "bpg": pg("BLK"),
                "fg": None if not p.get("FGa") else 100 * p["FGm"] / p["FGa"],
                "tp": None if not p.get("3PTa") else 100 * p["3PTm"] / p["3PTa"],
                "tpm": p.get("3PTm"), "ft": None if not p.get("FTa") else 100 * p["FTm"] / p["FTa"],
                "name": p["name"], "pos": p["pos"], "jersey": p["jersey"]}


# ══ Formatting ═════════════════════════════════════════════════════════════

def f1(v):
    return DASH if v is None else f"{v:.1f}"


def fpct(v):
    return DASH if v is None else f"{v:.1f}%"


def fint(v):
    return DASH if v is None else str(int(round(v)))


def rec(wl):
    return f"{wl[0]}-{wl[1]}"


def day_label(d, long=False):
    return d.strftime("%A, %B %-d" if long else "%a, %b %-d")


def tip(g, with_day=True):
    if not g.time_valid:
        t = "Time TBA"
    else:
        t = g.et.strftime("%-I:%M %p").replace(":00 ", " ") + " ET"
    return f"{day_label(g.et_date)} · {t}" if with_day else t


def tv(g):
    return " · ".join(esc(x) for x in g.tv) if g.tv else ""


def team_name(teams, s, link=True, rank=True):
    """A side's display name, Ivy schools bolded."""
    t = team(teams, s.tid, s.name, s.short)
    rk = f'<span class="rk">{s.rank}</span> ' if (rank and s.rank) else ""
    nm = esc(t.short)
    if t.ivy:
        nm = f'<b>{nm}</b>'
        if link:
            nm = f'<a class="tl" href="{team_path(t.id)}">{nm}</a>'
    tag = "" if t.d1 else ' <span class="tag">non-D-I</span>'
    return f"{rk}{nm}{tag}"


def team_slug(tid):
    return IVY_TEAMS[tid].lower()


def team_path(tid):
    return f"/teams/{team_slug(tid)}/"


def tlink(teams, tid, label=None):
    """A team's name, linked to its team page when it has one."""
    t = teams[tid] if tid in teams else None
    text = esc(label if label is not None else (t.short if t else tid))
    return f'<a class="tl" href="{team_path(tid)}">{text}</a>' if tid in IVY_TEAMS else text


def connector(g):
    return "vs." if g.neutral else "at"


def matchup_html(teams, g):
    return (f'{team_name(teams, g.away)} <span class="mu">{connector(g)}</span> '
            f'{team_name(teams, g.home)}')


def game_path(g):
    return f"/games/{g.id}/"


def score_html(teams, g, link=True):
    """'Columbia 84, Belmont 64' with the winner first."""
    a, h = g.away, g.home
    if a.score is None or h.score is None:
        return esc(g.status_detail)
    w, l = (a, h) if (a.winner or (a.score > h.score)) else (h, a)
    ot = ""
    if "OT" in g.status_detail:
        ot = f' <span class="mu">({esc(g.status_detail.split("/")[-1])})</span>'
    return (f'{team_name(teams, w, rank=False)} <span class="sc">{int(w.score)}</span>, '
            f'<span class="lose">{esc(team(teams, l.tid).short)} '
            f'<span class="sc">{int(l.score)}</span></span>{ot}')


# ══ Presentation ══════════════════════════════════════════════════════════

MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"
SANS = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        "'Helvetica Neue',Arial,sans-serif")
# The "printed programme" voice comes from a SYSTEM serif — no webfont, no
# third-party origin. Iowan Old Style ships on every Apple device, Palatino /
# Georgia everywhere else.
SERIF = ("'Iowan Old Style','Palatino Linotype',Palatino,'Book Antiqua',"
         "Georgia,serif")

SITE_CSS = f"""\
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:{SANS};background:var(--bg);color:var(--text);
    font-size:13.5px;padding:16px;max-width:1040px;margin:0 auto;
    line-height:1.55;-webkit-font-smoothing:antialiased}}
  a{{color:var(--text)}} a:hover{{color:var(--accent)}}
  .num,.sc,.rk,td.n,th.n{{font-family:{MONO};font-variant-numeric:tabular-nums}}
  .mu{{color:var(--muted)}} .ac{{color:var(--accent)}} .lose{{color:var(--muted)}}
  .serif{{font-family:{SERIF}}}
  /* masthead */
  .mast{{border-bottom:3px double var(--border);padding-bottom:10px;margin-bottom:4px}}
  .mast .kick{{color:var(--muted);font-size:10.5px;letter-spacing:2.2px;
    text-transform:uppercase}}
  .mast h1{{font-family:{SERIF};font-size:clamp(21px,6vw,30px);font-weight:600;
    line-height:1.1;letter-spacing:-.2px;margin-top:3px}}
  .mast h1 a{{color:var(--text);text-decoration:none}}
  .mast h1 .leaf{{color:var(--accent)}}
  .mast .brand{{color:var(--accent);font-size:13px;margin-top:6px;font-weight:600;
    letter-spacing:.3px}}
  .mast .brand span{{color:var(--muted);font-weight:400}}
  nav{{display:flex;gap:20px;font-size:14px;margin:4px 0 18px;flex-wrap:wrap;
    border-bottom:1px solid var(--border)}}
  nav a{{text-decoration:none;padding:11px 2px;font-weight:600;color:var(--text);
    margin-bottom:-1px;border-bottom:2px solid transparent}}
  nav a.on{{color:var(--accent);border-bottom-color:var(--accent)}}
  /* section heads: small caps over a hairline, like a programme's running heads */
  h2.sec{{font-size:10.5px;letter-spacing:2px;text-transform:uppercase;
    color:var(--accent);font-weight:700;border-bottom:1px solid var(--border);
    padding-bottom:5px;margin:26px 0 10px;display:flex;justify-content:space-between;
    gap:10px;align-items:baseline}}
  h2.sec .aside{{color:var(--muted);letter-spacing:.3px;text-transform:none;
    font-weight:400;font-size:11px}}
  .draft{{border:1px dashed var(--border);padding:12px 14px 13px;background:var(--surface)}}
  .draft .lab{{font-size:9.5px;letter-spacing:1.6px;text-transform:uppercase;
    color:var(--muted);margin-bottom:6px}}
  .draft .lab b{{color:#e0b44a;font-weight:700}}
  .draft p{{font-family:{SERIF};font-size:16.5px;line-height:1.6}}
  .draft p a{{color:var(--accent);text-decoration:none}}
  .note{{color:var(--muted);font-size:11.5px;line-height:1.6;margin-top:6px}}
  table{{border-collapse:collapse;width:100%;font-size:12.5px;
    font-variant-numeric:tabular-nums}}
  th{{color:var(--muted);text-align:left;font-weight:normal;padding:6px;
    border-bottom:1px solid var(--border);white-space:nowrap;font-size:10.5px;
    letter-spacing:.5px;text-transform:uppercase}}
  td{{padding:7px 6px;border-bottom:1px solid var(--border);vertical-align:top}}
  td.n,th.n{{text-align:right}}
  .rk{{color:var(--muted);font-size:10.5px}}
  .tag{{color:var(--muted);font-size:9.5px;border:1px solid var(--border);
    padding:0 4px;letter-spacing:.3px;white-space:nowrap;font-weight:400}}
  .sc{{font-weight:600}}
  /* game cards (home) */
  .cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:8px}}
  .gc{{display:block;background:var(--surface);border:1px solid var(--border);
    padding:10px 12px;text-decoration:none;color:var(--text);position:relative}}
  .gc:hover{{border-color:var(--accent)}}
  .gc .row{{display:flex;justify-content:space-between;gap:8px;align-items:baseline;
    padding:2px 0}}
  .gc .row.l{{color:var(--muted)}} .gc .row.l b{{font-weight:500}}
  .gc .sc{{font-size:15px}}
  .gc .meta{{color:var(--muted);font-size:11px;margin-top:6px;display:flex;
    justify-content:space-between;gap:8px}}
  .gc .meta .go{{color:var(--accent)}}
  .gc .kick{{font-size:9.5px;letter-spacing:1.4px;text-transform:uppercase;
    color:var(--muted);margin-bottom:4px}}
  .gc .kick.conf{{color:var(--accent)}}
  .sw{{display:inline-block;width:3px;height:12px;margin-right:7px;vertical-align:-1px}}
  /* the eight */
  table.eight td:first-child{{white-space:nowrap}}
  table.eight .nx{{color:var(--muted);font-size:11.5px}}
  /* schedule */
  .chips{{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 12px;position:sticky;
    top:0;background:var(--bg);padding:8px 0;z-index:3}}
  .chip{{font:inherit;font-size:12px;padding:6px 11px;border:1px solid var(--border);
    background:var(--surface);color:var(--muted);cursor:pointer}}
  .chip.on{{border-color:var(--accent);color:var(--accent)}}
  .day{{font-family:{SERIF};font-size:15px;margin:20px 0 4px;color:var(--text);
    font-weight:600}}
  .day .mu{{font-family:{SANS};font-size:11px;font-weight:400}}
  table.sched td{{padding:8px 6px}}
  table.sched td.t{{white-space:nowrap;color:var(--muted);width:92px}}
  table.sched td.tv{{color:var(--muted);font-size:11.5px;white-space:nowrap;text-align:right}}
  table.sched td.r{{white-space:nowrap;text-align:right}}
  table.sched a.res{{text-decoration:none}}
  table.sched .wl{{font-family:{MONO};font-weight:700;font-size:11px;margin-right:4px}}
  table.sched .ven{{color:var(--muted);font-size:11px;display:block}}
  table.sched tr.conf td:first-child{{box-shadow:inset 2px 0 0 var(--accent)}}
  .hide{{display:none}}
  /* ── game program ───────────────────────────────────────────────────── */
  .prog{{border:1px solid var(--border);background:var(--surface);
    padding:18px 16px 16px;position:relative;margin-top:4px}}
  .prog::before{{content:"";position:absolute;inset:4px;border:1px solid var(--border);
    pointer-events:none}}
  .prog .eyebrow{{text-align:center;font-size:10px;letter-spacing:3px;
    text-transform:uppercase;color:var(--accent)}}
  .prog .occasion{{text-align:center;font-family:{SERIF};font-style:italic;
    color:var(--muted);font-size:13.5px;margin-top:2px}}
  .vs{{display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:center;
    margin:16px 0 12px}}
  .vs .tm{{text-align:center}}
  .vs .crest{{width:54px;height:54px;margin:0 auto 7px;border:1px solid var(--border);
    background:var(--bg);display:flex;align-items:center;justify-content:center;
    font-family:{MONO};font-size:11px;letter-spacing:1px;color:var(--muted);
    border-top:4px solid var(--c)}}
  .vs .nm{{font-family:{SERIF};font-size:clamp(20px,5.6vw,32px);font-weight:600;
    line-height:1.05}}
  .vs .mascot{{color:var(--muted);font-size:11.5px;margin-top:2px}}
  .vs .rec{{font-family:{MONO};font-size:12px;margin-top:5px}}
  .vs .rec .mu{{font-size:11px}}
  .vs .mid{{font-family:{SERIF};font-style:italic;color:var(--muted);font-size:17px}}
  .vs .big{{font-family:{MONO};font-size:clamp(30px,9vw,46px);font-weight:700;
    line-height:1;margin-top:6px}}
  .vs .lost .nm,.vs .lost .big{{color:var(--muted)}}
  .facts{{display:flex;flex-wrap:wrap;justify-content:center;gap:4px 18px;
    border-top:1px solid var(--border);padding-top:10px;font-size:12px;
    color:var(--muted)}}
  .facts b{{color:var(--text);font-weight:600}}
  .facts .item{{white-space:nowrap}}
  /* two-up panels */
  .two{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
  /* Grid children default to min-width:auto, so a wide roster table would
     widen its column past the phone instead of scrolling inside table-wrap. */
  .two>*,.cards>*{{min-width:0}}
  .vs{{align-items:end}}
  @media (max-width:720px){{ .two{{grid-template-columns:1fr}} }}
  .panel h3{{font-family:{SERIF};font-size:17px;font-weight:600;margin-bottom:6px;
    display:flex;align-items:center}}
  .panel h3 .mu{{font-family:{SANS};font-size:11px;font-weight:400;margin-left:auto}}
  /* players to watch */
  .watch{{border-left:3px solid var(--c);background:var(--surface);
    padding:9px 11px;margin-bottom:7px}}
  .watch .who{{display:flex;gap:9px;align-items:baseline}}
  .watch .jn{{font-family:{MONO};color:var(--muted);font-size:12px;min-width:24px}}
  .watch .pn{{font-family:{SERIF};font-size:17px;font-weight:600}}
  .watch .bio{{color:var(--muted);font-size:11px;margin-left:33px}}
  .watch .why{{font-size:10px;letter-spacing:1.2px;text-transform:uppercase;
    color:var(--accent);margin:4px 0 0 33px}}
  .watch .line{{display:flex;gap:16px;margin:6px 0 0 33px;flex-wrap:wrap}}
  .watch .line div{{text-align:left}}
  .watch .line .v{{font-family:{MONO};font-size:17px;font-weight:600;line-height:1.1}}
  .watch .line .k{{font-size:9.5px;color:var(--muted);letter-spacing:.8px;
    text-transform:uppercase}}
  .watch .last{{color:var(--muted);font-size:11px;margin:6px 0 0 33px}}
  /* tale of the tape */
  .tape{{max-width:640px;margin:0 auto}}
  .tape .hdr,.tape .r{{display:grid;grid-template-columns:1fr 128px 1fr;
    align-items:center;gap:8px}}
  .tape .hdr{{font-family:{SERIF};font-size:15px;font-weight:600;padding-bottom:6px;
    border-bottom:1px solid var(--border)}}
  .tape .hdr div:first-child{{text-align:right}}
  .tape .r{{padding:6px 0;border-bottom:1px solid var(--border)}}
  .tape .lab{{text-align:center;font-size:10px;letter-spacing:1px;
    text-transform:uppercase;color:var(--muted);line-height:1.25}}
  .tape .v{{font-family:{MONO};font-size:14px;display:flex;align-items:center;gap:8px}}
  .tape .v.a{{justify-content:flex-end}}
  .tape .v.b{{justify-content:flex-start}}
  .tape .bar{{height:4px;background:var(--border);flex:1;max-width:110px;
    position:relative;overflow:hidden}}
  .tape .bar i{{position:absolute;top:0;bottom:0;background:var(--muted)}}
  .tape .v.a .bar i{{right:0}} .tape .v.b .bar i{{left:0}}
  .tape .v.best{{color:var(--accent);font-weight:700}}
  .tape .v.best .bar i{{background:var(--accent)}}
  /* recent form */
  .form td{{padding:5px 6px;font-size:12px}}
  .form .wl{{font-family:{MONO};font-weight:700;width:18px}}
  .form .wl.L{{color:var(--muted)}}
  .form .ha{{color:var(--muted);width:28px}}
  .pips{{display:inline-flex;gap:3px;margin-left:8px;vertical-align:1px}}
  .pips i{{width:9px;height:9px;border:1px solid var(--muted);display:inline-block}}
  .pips i.W{{background:var(--accent);border-color:var(--accent)}}
  /* rosters */
  table.ros td,table.ros th{{padding:5px 5px;font-size:12px}}
  table.ros td.jn{{font-family:{MONO};color:var(--muted);width:26px;text-align:right}}
  table.ros .pn{{font-weight:600;white-space:nowrap}}
  table.ros .ht{{display:block;color:var(--muted);font-size:10.5px;font-weight:400;
    white-space:normal}}
  table.ros td.c{{color:var(--muted);white-space:nowrap}}
  table.ros tr.star .pn{{color:var(--accent)}}
  .h2h td{{font-size:12px}}
  /* ── box score ──────────────────────────────────────────────────────── */
  table.ls{{max-width:620px}}
  table.ls td,table.ls th{{text-align:right;font-family:{MONO};padding:6px 8px}}
  table.ls td:first-child,table.ls th:first-child{{text-align:left;font-family:{SANS}}}
  table.ls td.t{{font-weight:700}}
  table.bx td,table.bx th{{padding:5px 6px;font-size:11.5px;white-space:nowrap}}
  table.bx td:first-child{{white-space:normal;min-width:140px}}
  table.bx td:not(:first-child),table.bx th:not(:first-child){{text-align:right}}
  table.bx td:not(:first-child){{font-family:{MONO}}}
  table.bx tr.sec td{{color:var(--accent);font-size:9.5px;letter-spacing:1px;
    text-transform:uppercase;font-weight:700;font-family:{SANS};text-align:left;
    padding-top:10px}}
  table.bx tr.tot td{{font-weight:700;border-top:1px solid var(--muted)}}
  table.bx tr.pc td{{color:var(--muted);font-size:10.5px;padding-top:0}}
  table.bx .jn{{font-family:{MONO};color:var(--muted);font-size:10.5px;
    display:inline-block;min-width:20px}}
  table.bx .pos{{color:var(--muted);font-size:10px;margin-left:4px}}
  table.bx tr.dnp td{{color:var(--muted)}}
  .ldr{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}
  @media (max-width:560px){{ .ldr{{grid-template-columns:1fr}} }}
  .ldr .c{{background:var(--surface);border:1px solid var(--border);padding:9px 11px}}
  .ldr .k{{font-size:9.5px;letter-spacing:1.2px;text-transform:uppercase;color:var(--muted)}}
  .ldr .p{{display:flex;justify-content:space-between;gap:8px;margin-top:5px;
    align-items:baseline}}
  .ldr .p .v{{font-family:{MONO};font-size:17px;font-weight:700}}
  .ldr .p .who{{font-size:12px}} .ldr .p .who .mu{{font-size:10.5px}}
  a.tl{{color:inherit;text-decoration:none;border-bottom:1px dotted var(--border)}}
  a.tl:hover{{color:var(--accent);border-bottom-color:var(--accent)}}
  h2.sec a.tl,.vs .nm a.tl{{border-bottom:none}}
  /* editorial blocks (files Jason edits) */
  .ed{{font-family:{SERIF};font-size:16px;line-height:1.62}}
  .ed p+p{{margin-top:10px}}
  .ed a{{color:var(--accent);text-decoration:none}}
  .ed h3{{font-size:15px;margin:10px 0 4px}}
  .ed .chk{{background:color-mix(in srgb,#e0b44a 18%,transparent);color:#e0b44a;
    font-family:{SANS};font-size:10.5px;letter-spacing:.4px;padding:0 4px;
    border:1px solid #e0b44a;-webkit-box-decoration-break:clone;box-decoration-break:clone}}
  .edlab{{font-size:9.5px;letter-spacing:1.4px;text-transform:uppercase;
    color:var(--muted);margin-bottom:6px}}
  .edlab b{{color:#e0b44a}}
  .edbox{{border-left:3px solid var(--c,var(--accent));padding:2px 0 2px 14px}}
  .empty{{color:var(--muted);font-size:12px;font-style:italic}}
  /* team page */
  .thd{{display:flex;gap:14px;align-items:flex-start}}
  .thd .crest{{width:64px;height:64px;flex:0 0 auto;border:1px solid var(--border);
    background:var(--bg);border-top:5px solid var(--c);display:flex;align-items:center;
    justify-content:center;font-family:{MONO};font-size:12px;letter-spacing:1px;
    color:var(--muted)}}
  .thd h2{{font-family:{SERIF};font-size:clamp(26px,7vw,38px);font-weight:600;line-height:1}}
  .thd .sub{{color:var(--muted);font-size:12.5px;margin-top:5px;line-height:1.5}}
  .thd .sub b{{color:var(--text);font-weight:600}}
  .tcards{{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:14px}}
  @media (max-width:560px){{ .tcards{{grid-template-columns:repeat(2,1fr)}} }}
  .tcards .c{{background:var(--bg);border:1px solid var(--border);padding:8px 10px;min-width:0}}
  .tcards .k{{font-size:9.5px;letter-spacing:1.2px;text-transform:uppercase;color:var(--muted)}}
  .tcards .v{{font-family:{MONO};font-size:20px;font-weight:700;line-height:1.25}}
  .tcards .s{{font-size:10.5px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap}}
  .pk{{display:flex;gap:10px;align-items:baseline;padding:8px 0;
    border-bottom:1px solid var(--border);flex-wrap:wrap}}
  .pk .jn{{font-family:{MONO};color:var(--muted);font-size:12px;min-width:26px}}
  .pk .pn{{font-family:{SERIF};font-size:16.5px;font-weight:600}}
  .pk .ln{{font-family:{MONO};font-size:12.5px}}
  .pk .why{{color:var(--accent);font-size:10px;letter-spacing:1px;text-transform:uppercase}}
  table.vsivy td.rk2{{color:var(--muted);font-family:{MONO};font-size:11px}}
  table.vsivy tr.top td.n:nth-child(2){{color:var(--accent);font-weight:700}}
  table.tsch td{{padding:7px 6px}}
  table.tsch td.d{{white-space:nowrap;color:var(--muted);width:78px}}
  table.tsch td.r{{text-align:right;white-space:nowrap}}
  table.tsch .wl{{font-family:{MONO};font-weight:700;margin-right:5px}}
  table.tsch tr.L .wl{{color:var(--muted)}}
  table.tsch tr.conf td:first-child{{box-shadow:inset 2px 0 0 var(--accent)}}
  table.tsch .ven{{color:var(--muted);font-size:11px;display:block}}
  table.tsch tr.past td{{}}
  .tix{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px}}
  .tix>*{{min-width:0}}
  .tix a{{display:block;background:var(--surface);border:1px solid var(--border);
    border-top:4px solid var(--c);padding:11px 12px;text-decoration:none;color:var(--text)}}
  .tix a:hover{{border-color:var(--accent);border-top-color:var(--c)}}
  .tix .n{{font-family:{SERIF};font-size:20px;font-weight:600}}
  .tix .m{{color:var(--muted);font-size:11.5px;margin-top:2px}}
  .tix .r{{font-family:{MONO};font-size:13px;margin-top:8px}}
  .tix .x{{color:var(--muted);font-size:11px;margin-top:4px}}
  .crumb{{font-size:11.5px;margin-bottom:8px;color:var(--muted)}}
  .crumb a{{color:var(--muted)}}
"""

PAGE_CSS = (chrome.tokens_css(IVY.accent) + SITE_CSS
            + chrome.SCROLL_FADE_CSS + chrome.SITE_FOOTER_CSS)

NAV = [("/", "Home"), ("/schedule/", "Schedule"), ("/teams/", "Teams")]


def table_scroll(inner):
    return (f'<div class="table-scroll"><div class="table-wrap">{inner}'
            '</div></div>')


def shell(path, title, body, description, as_of, extra_js=""):
    nav = "".join(
        f'<a href="{p}"{" class=\"on\"" if (p == path or (p != "/" and path.startswith(p))) else ""}>{esc(l)}</a>'
        for p, l in NAV)
    social = "\n".join(seo.social_tags(IVY, path, title, description))
    # No usage beacon on a local prototype: it would report site="ivy"
    # pageviews from localhost into the production usage dataset.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(seo.canonical_url(IVY, path))}">
{social}
<style>
{PAGE_CSS}</style>
</head>
<body>
<div class="mast">
  <div class="kick">{SEASON_LABEL} season · data through {esc(as_of)}</div>
  <h1><a href="/">Ivy League <span class="leaf">Women’s Basketball</span></a></h1>
  <div class="brand">stats at a glance <span>· prototype</span></div>
</div>
<nav>{nav}</nav>
{body}
{chrome.SITE_FOOTER_HTML}<script>
{chrome.SCROLL_FADE_JS}{extra_js}</script>
</body>
</html>
"""


def write(path, html):
    p = OUT / path.strip("/") / "index.html" if path != "/" else OUT / "index.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html)


# ══ Home ═══════════════════════════════════════════════════════════════════

def game_card(teams, S, g, show_date=False):
    kick = []
    if g.conf:
        kick.append('<span class="ac">Ivy League game</span>')
    if g.note:
        kick.append(esc(g.note))
    kick_html = f'<div class="kick">{" · ".join(kick)}</div>' if kick else ""
    rows = []
    for s in g.sides:
        t = team(teams, s.tid, s.name, s.short)
        lost = g.completed and s.winner is False
        val = (f'<span class="sc">{int(s.score)}</span>'
               if g.completed and s.score is not None
               else f'<span class="num mu">{rec(S.record(s.tid))}</span>'
               if t.d1 else "")
        pre = "" if s is g.away else f'<span class="mu">{connector(g)}</span> '
        rows.append(f'<div class="row{" l" if lost else ""}"><span>'
                    f'<i class="sw" style="background:{t.color}"></i>{pre}'
                    f'{team_name(teams, s, link=False)}</span>{val}</div>')
    if g.completed:
        left = "Final" + (f' / {esc(g.status_detail.split("/")[-1])}'
                          if "OT" in g.status_detail else "")
        go = "Box score →"
    else:
        left = tip(g, with_day=show_date) + (f" · {tv(g)}" if g.tv else "")
        go = "Game program →"
    return (f'<a class="gc" href="{game_path(g)}">{kick_html}{"".join(rows)}'
            f'<div class="meta"><span>{left}</span><span class="go">{go}</span></div></a>')


def narrative(teams, S, games, today):
    """A DRAFT paragraph generated from the data. Every clause is a fact the
    page can show; nothing here is opinion, and it says so on its face."""
    ivy = list(IVY_TEAMS)
    recs = {t: S.record(t) for t in ivy}
    sents = []
    unbeaten = [t for t in ivy if recs[t][0] and not recs[t][1]]
    winless = [t for t in ivy if recs[t][1] and not recs[t][0]]
    nc = [g for g in games if g.completed and not g.conf]
    w = sum(1 for g in nc for s in g.sides if s.tid in IVY_TEAMS and s.winner)
    l = sum(1 for g in nc for s in g.sides if s.tid in IVY_TEAMS and s.winner is False)
    d1 = [g for g in nc if team(teams, g.other(g.ivy_ids[0]).tid).d1]
    w1 = sum(1 for g in d1 for s in g.sides if s.tid in IVY_TEAMS and s.winner)
    l1 = sum(1 for g in d1 for s in g.sides if s.tid in IVY_TEAMS and s.winner is False)
    if w + l:
        lst = lambda ts: ", ".join(f"{IVY_TEAMS[t]} ({rec(recs[t])})" for t in ts)
        first = min(g.et_date for g in games)
        days = (today - first).days
        s = (f"{days} days into the season, the Ivy League is a combined "
             f"<b>{w}-{l}</b> outside the conference ({w1}-{l1} against Division I)")
        if unbeaten:
            s += f", and {len(unbeaten)} of the eight are still unbeaten: {lst(unbeaten)}"
        sents.append(s + ".")
        if winless:
            sents.append(f"Still looking for a first win: {lst(winless)}.")
    # Best result: biggest D-I win by margin.
    best = None
    for g in d1:
        for s in g.sides:
            if s.tid in IVY_TEAMS and s.winner:
                o = g.other(s.tid)
                m = (s.score or 0) - (o.score or 0)
                if best is None or m > best[0]:
                    best = (m, g, s, o)
    if best:
        m, g, s, o = best
        sents.append(f'The most emphatic Division I result so far: '
                     f'{IVY_TEAMS[s.tid]} {int(s.score)}, {esc(team(teams, o.tid).short)} '
                     f'{int(o.score)} on {g.et.strftime("%b %-d")} '
                     f'(<a href="{game_path(g)}">box score</a>).')
    # Leading scorer, min 2 games.
    lead = None
    for t in ivy:
        for aid in S.player_tot.get(t, {}):
            r = S.player_rates(t, aid)
            if r and r["gp"] >= 2 and r["ppg"] is not None:
                if lead is None or r["ppg"] > lead[0]["ppg"]:
                    lead = (r, t)
    if lead:
        r, t = lead
        sents.append(f"{esc(r['name'])} of {IVY_TEAMS[t]} leads the league in scoring at "
                     f"{r['ppg']:.1f} points a game.")
    # Marquee: next ranked opponent within 7 days.
    soon = [g for g in games if not g.completed
            and today <= g.et_date <= today + timedelta(days=7)
            and any(s.rank for s in g.sides if s.tid not in IVY_TEAMS)]
    if soon:
        g = soon[0]
        iv = g.ivy_ids[0]
        o = g.other(iv)
        sents.append(f'Circle {day_label(g.et_date, long=True)}: {IVY_TEAMS[iv]} '
                     f'{"hosts" if g.home.tid == iv and not g.neutral else "faces"} '
                     f'No. {o.rank} {esc(team(teams, o.tid).short)} '
                     f'(<a href="{game_path(g)}">game program</a>).')
    return " ".join(sents[:5])


def page_home(teams, S, games, now, as_of):
    today = now.astimezone(ET).date()
    yday = today - timedelta(days=1)
    body = []
    text, draft = read_ed("whats_new.md")
    if text:
        lab = ('<b>Draft by Claude — awaiting Jason’s edit</b> · ' if draft else '') + 'What’s new in Ivy WBB'
        body.append(f'<div class="draft"><div class="lab">{lab}</div>'
                    f'<div class="ed">{md(text)}</div></div>')
    else:
        # No written note yet: fall back to the generated placeholder, labelled.
        body.append('<div class="draft"><div class="lab"><b>Draft placeholder</b> · '
                    'what’s new in Ivy WBB — generated from the data, to be replaced '
                    'by a written note</div>'
                    f'<p>{narrative(teams, S, games, today)}</p></div>')

    last = [g for g in games if g.completed and g.et_date == yday]
    head = "Last night"
    if not last:
        done = [g for g in games if g.completed and g.et_date < today]
        if done:
            d = done[-1].et_date
            last = [g for g in done if g.et_date == d]
            head = "Latest results"
    if last:
        body.append(f'<h2 class="sec">{head}<span class="aside">'
                    f'{day_label(last[0].et_date, long=True)}</span></h2>'
                    f'<div class="cards">{"".join(game_card(teams, S, g) for g in last)}</div>')

    # Today's games stay in "Tonight" once final (with their score), rather
    # than vanishing until tomorrow's "Last night".
    todays = [g for g in games if g.et_date == today]
    up = todays or [g for g in games if not g.completed and g.et_date > today]
    if up:
        d = up[0].et_date
        nxt = [g for g in up if g.et_date == d]
        head = "Tonight" if d == today else ("Tomorrow" if d == today + timedelta(1)
                                             else "Next up")
        body.append(f'<h2 class="sec">{head}<span class="aside">'
                    f'{day_label(d, long=True)}</span></h2>'
                    f'<div class="cards">{"".join(game_card(teams, S, g) for g in nxt)}</div>')
        later = [g for g in games if not g.completed
                 and d < g.et_date <= d + timedelta(days=6)]
        if later:
            body.append('<h2 class="sec">Later this week</h2>'
                        f'<div class="cards">{"".join(game_card(teams, S, g, True) for g in later)}</div>')

    # The eight.
    rows = []
    order = sorted(IVY_TEAMS, key=lambda t: (-(S.record(t)[0] / max(1, sum(S.record(t)))),
                                              -S.record(t)[0], IVY_TEAMS[t]))
    for t in order:
        tm = teams[t]
        r = S.team_rates(t) or {}
        ng = S.next_game(t, now)
        nx = ""
        if ng:
            o = ng.other(t)
            where = "vs." if ng.neutral or ng.home.tid == t else "at"
            nx = (f'<a href="{game_path(ng)}">{ng.et.strftime("%b %-d")} {where} '
                  f'{esc(team(teams, o.tid, o.name, o.short).short)}</a>')
        diff = r.get("diff")
        rows.append(
            f'<tr><td><i class="sw" style="background:{tm.color}"></i><b>{tlink(teams, t)}</b></td>'
            f'<td class="n">{rec(S.record(t))}</td><td class="n">{rec(S.record(t, True))}</td>'
            f'<td class="n">{f1(r.get("ppg"))}</td><td class="n">{f1(r.get("oppg"))}</td>'
            f'<td class="n">{DASH if diff is None else f"{diff:+.1f}"}</td>'
            f'<td class="n">{S.streak(t) or DASH}</td><td class="nx">{nx}</td></tr>')
    body.append('<h2 class="sec">The eight<span class="aside">sorted by win %</span></h2>'
                + table_scroll('<table class="eight"><thead><tr><th>Team</th>'
                               '<th class="n">W-L</th><th class="n">Ivy</th><th class="n">PPG</th>'
                               '<th class="n">Opp</th><th class="n">Diff</th>'
                               '<th class="n">Strk</th><th>Next</th></tr></thead><tbody>'
                               + "".join(rows) + '</tbody></table>')
                + '<p class="note">League play opens in January; until then the Ivy '
                  'column reads 0-0 for everyone. PPG / Opp are per game from ESPN box '
                  'scores.</p>')
    write("/", shell("/", "Ivy League Women’s Basketball 2026–27 — stats at a glance",
                     "\n".join(body),
                     "Scores, schedules and game programs for Ivy League women’s basketball.",
                     as_of))


# ══ Schedule ═══════════════════════════════════════════════════════════════

SCHED_JS = """
/* -- Team filter: one attribute per row, a hash so the filter is linkable -- */
(function(){
  var chips=document.querySelectorAll('.chip');
  function apply(k){
    chips.forEach(function(c){c.classList.toggle('on',c.dataset.k===k);});
    document.querySelectorAll('tr[data-t]').forEach(function(r){
      var show=k==='all'||(k==='conf'?r.classList.contains('conf'):(' '+r.dataset.t+' ').indexOf(' '+k+' ')>=0);
      r.classList.toggle('hide',!show);
    });
    document.querySelectorAll('.dblk').forEach(function(b){
      b.classList.toggle('hide',!b.querySelector('tr[data-t]:not(.hide)'));
    });
  }
  chips.forEach(function(c){c.addEventListener('click',function(){
    history.replaceState(null,'',c.dataset.k==='all'?location.pathname:'#'+c.dataset.k);
    apply(c.dataset.k);});});
  var h=location.hash.slice(1);
  apply(document.querySelector('.chip[data-k="'+h+'"]')?h:'all');
})();
"""


def page_schedule(teams, S, games, now, as_of):
    slug = {t: n.lower() for t, n in IVY_TEAMS.items()}
    chips = ['<button class="chip" data-k="all">All</button>'] + [
        f'<button class="chip" data-k="{slug[t]}">{n}</button>'
        for t, n in sorted(IVY_TEAMS.items(), key=lambda x: x[1])] + [
        '<button class="chip" data-k="conf">Ivy vs. Ivy</button>']
    by_day = defaultdict(list)
    for g in games:
        by_day[g.et_date].append(g)
    today = now.astimezone(ET).date()
    blocks = []
    for d in sorted(by_day):
        rows = []
        for g in by_day[d]:
            keys = " ".join(slug[t] for t in g.ivy_ids)
            if g.completed:
                when = "Final" + (" OT" if "OT" in g.status_detail else "")
                if g.conf:
                    # Ivy vs. Ivy: winner's name, then the score, high first.
                    w = g.home if g.home.winner else g.away
                    l = g.other(w.tid)
                    res = (f'{esc(IVY_TEAMS[w.tid])} '
                           f'{int(w.score)}–{int(l.score)}')
                else:
                    # Read from the Ivy side: "W 71–65" means the Ivy team scored 71.
                    s = g.side(g.ivy_ids[0])
                    o = g.other(s.tid)
                    res = (f'<span class="wl">{"W" if s.winner else "L"}</span>'
                           f'{int(s.score)}–{int(o.score)}')
                res = f'<a class="res" href="{game_path(g)}">{res}</a>'
            else:
                when = tip(g, with_day=False)
                res = f'<a href="{game_path(g)}">Program</a>'
            ven = " · ".join(x for x in (g.note, g.venue, g.city) if x)
            if g.neutral:
                ven = "Neutral · " + ven
            rows.append(
                f'<tr data-t="{keys}"{" class=\"conf\"" if g.conf else ""}>'
                f'<td class="t">{when}</td>'
                f'<td>{matchup_html(teams, g)}<span class="ven">{esc(ven)}</span></td>'
                f'<td class="tv">{tv(g)}</td><td class="r">{res}</td></tr>')
        mark = ' <span class="mu">· today</span>' if d == today else ""
        blocks.append(f'<div class="dblk" id="d{d:%Y%m%d}"><div class="day">'
                      f'{day_label(d, long=True)}{mark}</div>'
                      + table_scroll(f'<table class="sched"><tbody>{"".join(rows)}'
                                     '</tbody></table>') + '</div>')
    body = (f'<div class="chips">{"".join(chips)}</div>'
            '<p class="note" style="margin-top:0">All times Eastern. “at” = road game, '
            '“vs.” = neutral site. A green edge marks an Ivy League game. '
            'TV listings are ESPN’s, and are blank where ESPN has none yet.</p>'
            + "".join(blocks))
    write("/schedule/", shell("/schedule/", "Schedule — Ivy League Women’s Basketball 2026–27",
                              body, "Every game for all eight Ivy League women’s "
                              "basketball teams, 2026–27.", as_of, SCHED_JS))


# ══ Game program ══════════════════════════════════════════════════════════

def class_of(a):
    return ((a.get("experience") or {}).get("abbreviation")) or None


def roster_rows(rosters, S, tid):
    """Merge the ESPN roster with season averages, keyed by athlete id. Box-score
    players missing from the roster are appended (ESPN rosters lag transfers)."""
    r = (rosters.get(tid) or {}).get("athletes") or []
    out, seen = [], set()
    for a in r:
        aid = a.get("id")
        seen.add(aid)
        bp = a.get("birthPlace") or {}
        out.append({"id": aid, "name": a.get("displayName") or "",
                    "jersey": a.get("jersey"),
                    "pos": (a.get("position") or {}).get("abbreviation"),
                    "cls": class_of(a), "ht": a.get("displayHeight"),
                    "home": bp.get("displayText") or ", ".join(
                        x for x in (bp.get("city"), bp.get("state") or bp.get("country")) if x),
                    "rates": S.player_rates(tid, aid)})
    for aid, p in S.player_tot.get(tid, {}).items():
        if aid not in seen:
            out.append({"id": aid, "name": p["name"], "jersey": p["jersey"],
                        "pos": p["pos"], "cls": None, "ht": None, "home": None,
                        "rates": S.player_rates(tid, aid), "box_only": True})
    def jkey(p):
        try:
            return (0, int(p["jersey"]))
        except (TypeError, ValueError):
            return (1, p["name"])
    return sorted(out, key=jkey), bool(r)


def pick_watch(players):
    """Up to three distinct players: the team leaders in scoring, rebounding
    and assists. Where one player leads more than one category, the freed
    slot goes to the next-highest scorer, and is labelled exactly that — the
    label must be a true statement about the pick. Needs a game played."""
    pool = [p for p in players if p["rates"] and p["rates"]["gp"]]
    if not pool:
        return []
    cats = [("ppg", "scoring"), ("rpg", "rebounding"), ("apg", "assists")]
    leads = {}
    for k, lab in cats:
        best = max(pool, key=lambda p: p["rates"][k] or -1)
        if best["rates"][k]:
            leads.setdefault(best["id"], []).append(lab)
    chosen = [p for p in pool if p["id"] in leads]
    chosen.sort(key=lambda p: min(i for i, (_, lab) in enumerate(cats)
                                  if lab in leads[p["id"]]))
    for p in chosen:
        p["why"] = "Team leader · " + " & ".join(leads[p["id"]])
    scorers = sorted((p for p in pool if p["id"] not in leads),
                     key=lambda p: -(p["rates"]["ppg"] or -1))
    for p in scorers[:3 - len(chosen)]:
        p["why"] = "Next-highest scorer"
        chosen.append(p)
    return chosen


def watch_html(teams, S, tid, players, color):
    picks = pick_watch(players)
    if not picks:
        return '<p class="note">No box score yet this season to pick from.</p>'
    out = []
    for p in picks:
        r = p["rates"]
        bio = " · ".join(x for x in (p["pos"], p["cls"], p["ht"], p["home"]) if x)
        line = "".join(f'<div><div class="v">{v}</div><div class="k">{k}</div></div>'
                       for k, v in (("PPG", f1(r["ppg"])), ("RPG", f1(r["rpg"])),
                                    ("APG", f1(r["apg"])), ("FG%", fint(r["fg"])),
                                    ("MPG", fint(r["mpg"]))))
        last = S.last_game_line.get((tid, p["id"]))
        lg = ""
        if last:
            g, pl = last
            o = team(teams, g.other(tid).tid, g.other(tid).name, g.other(tid).short)
            bits = [f"{fint(pl.get('PTS'))} pts", f"{fint(pl.get('REB'))} reb",
                    f"{fint(pl.get('AST'))} ast"]
            lg = (f'<div class="last">Last game: {", ".join(bits)} vs. {esc(o.short)}, '
                  f'{g.et.strftime("%b %-d")}</div>')
        out.append(
            f'<div class="watch" style="--c:{color}"><div class="who">'
            f'<span class="jn">{esc(IVY.jersey_prefix + p["jersey"]) if p["jersey"] else ""}</span>'
            f'<span class="pn">{esc(p["name"])}</span></div>'
            f'<div class="bio">{esc(bio)}</div><div class="why">{esc(p["why"])}</div>'
            f'<div class="line">{line}</div>{lg}</div>')
    return "".join(out)


def roster_table(teams, S, tid, rosters):
    players, has_roster = roster_rows(rosters, S, tid)
    t = teams[tid]
    if not players:
        return (f'<p class="note">ESPN publishes no roster for {esc(t.full)}'
                f'{"" if t.d1 else " (a non-Division I program)"}, and no box score '
                'with them has been played yet.</p>'), players
    stars = {p["id"] for p in pick_watch(players)}
    rows = []
    for p in players:
        r = p["rates"] or {}
        rows.append(
            f'<tr{" class=\"star\"" if p["id"] in stars else ""}>'
            f'<td class="jn">{esc(p["jersey"] or "")}</td>'
            f'<td><span class="pn">{esc(p["name"])}</span>'
            f'<span class="ht">{esc(p["home"] or "")}</span></td>'
            f'<td class="c">{esc(p["pos"] or "—")}</td><td class="c">{esc(p["cls"] or "—")}</td>'
            f'<td class="c">{esc((p["ht"] or "—").replace(" ", ""))}</td>'
            f'<td class="n">{r.get("gp") or DASH}</td><td class="n">{f1(r.get("ppg"))}</td>'
            f'<td class="n">{f1(r.get("rpg"))}</td><td class="n">{f1(r.get("apg"))}</td></tr>')
    note = ""
    if not has_roster:
        note = ('<p class="note">ESPN has no roster for this team; players listed are '
                'those who have appeared in a box score.</p>')
    return (table_scroll('<table class="ros"><thead><tr><th class="n">#</th><th>Player</th>'
                         '<th>Pos</th><th>Yr</th><th>Ht</th><th class="n">GP</th>'
                         '<th class="n">PPG</th><th class="n">RPG</th><th class="n">APG</th>'
                         f'</tr></thead><tbody>{"".join(rows)}</tbody></table>') + note), players


TAPE = [
    ("Points per game", "ppg", f1, True),
    ("Opp. points", "oppg", f1, False),
    ("Scoring margin", "diff", lambda v: DASH if v is None else f"{v:+.1f}", True),
    ("Off. rating", "ortg", f1, True),
    ("Def. rating", "drtg", f1, False),
    ("Pace (poss/40)", "pace", f1, None),
    ("Field goal %", "fg", fpct, True),
    ("Effective FG %", "efg", fpct, True),
    ("3-point %", "tp", fpct, True),
    ("3PA per game", "tpapg", f1, None),
    ("Free throw %", "ft", fpct, True),
    ("Rebounds", "rpg", f1, True),
    ("Off. rebound %", "orb", fpct, True),
    ("Assists", "apg", f1, True),
    ("Turnovers", "tov", f1, False),
    ("Opp. turnovers", "forced", f1, True),
    ("Steals", "spg", f1, True),
    ("Blocks", "bpg", f1, True),
]


def tape_html(teams, S, a, b):
    ra, rb = S.team_rates(a), S.team_rates(b)
    if not ra and not rb:
        return '<p class="note">Neither team has a box score yet this season.</p>'
    ra, rb = ra or {}, rb or {}
    ta, tb = teams[a], teams[b]
    rows = [f'<div class="hdr"><div>{esc(ta.short)}</div><div></div>'
            f'<div>{esc(tb.short)}</div></div>']
    for lab, k, fmt, higher in TAPE:
        va, vb = ra.get(k), rb.get(k)
        best_a = best_b = False
        if higher is not None and va is not None and vb is not None and va != vb:
            best_a = (va > vb) == higher
            best_b = not best_a
        # Bars are each side's share of the pair; margin can be negative, so it
        # gets no bar rather than a misleading one.
        if k != "diff" and va is not None and vb is not None and (va + vb) > 0:
            wa, wb = 100 * va / (va + vb), 100 * vb / (va + vb)
            bar_a = f'<span class="bar"><i style="width:{wa:.0f}%"></i></span>'
            bar_b = f'<span class="bar"><i style="width:{wb:.0f}%"></i></span>'
        else:
            bar_a = bar_b = ""
        rows.append(
            f'<div class="r"><div class="v a{" best" if best_a else ""}">{bar_a}{fmt(va)}</div>'
            f'<div class="lab">{lab}</div>'
            f'<div class="v b{" best" if best_b else ""}">{fmt(vb)}{bar_b}</div></div>')
    na, nb = ra.get("n", 0), rb.get("n", 0)
    caveats = [f"Per game, through {na} and {nb} box scores respectively."]
    for t in (ta, tb):
        if not t.d1:
            caveats.append(f"{esc(t.short)} is not a Division I program; ESPN carries only "
                           "its games against D-I opponents, so its figures come from those.")
    caveats.append("Ratings are points per 100 possessions (FGA − OREB + TO + 0.44 × FTA). "
                   "Accent marks the better side; bars show each side’s share.")
    return (f'<div class="tape">{"".join(rows)}</div>'
            f'<p class="note" style="text-align:center">{" ".join(caveats)}</p>')


def form_html(teams, S, tid, before):
    gs = S.recent(tid, 5, before)
    if not gs:
        return '<p class="note">No completed games yet this season.</p>'
    rows, pips = [], []
    for g in gs:
        s, o = g.side(tid), g.other(tid)
        wl = "W" if s.winner else "L"
        pips.append(f'<i class="{wl}"></i>')
        ha = "vs." if g.neutral else ("" if g.home.tid == tid else "at")
        sc = f"{int(s.score)}–{int(o.score)}" if s.score is not None and o.score is not None else ""
        if g.ivy_ids:
            sc = f'<a href="{game_path(g)}">{sc}</a>'
        rk = f'<span class="rk">{o.rank}</span> ' if o.rank else ""
        rows.append(f'<tr><td class="mu">{g.et.strftime("%b %-d")}</td>'
                    f'<td class="wl {wl}">{wl}</td><td class="ha">{ha}</td>'
                    f'<td>{rk}{tlink(teams, o.tid, team(teams, o.tid, o.name, o.short).short)}</td>'
                    f'<td class="n">{sc}</td></tr>')
    return ('<table class="form"><tbody>' + "".join(rows) + '</tbody></table>'), "".join(pips[::-1])


def h2h_html(teams, scheds, g):
    """Meetings between these two this season and last, from the Ivy side's
    schedules (the only schedules we hold for last season)."""
    iv = g.ivy_ids[0]
    opp = g.other(iv).tid
    seen, rows = set(), []
    for season in (PREV_SEASON, IVY.season):
        for x in games_from(scheds.get((iv, season))):
            if x.id in seen or x.id == g.id or not x.completed:
                continue
            if {s.tid for s in x.sides} == {iv, opp}:
                seen.add(x.id)
                s, o = x.side(iv), x.other(iv)
                where = "neutral" if x.neutral else ("home" if x.home.tid == iv else "away")
                res = f"{IVY_TEAMS[iv]} {'won' if s.winner else 'lost'} {int(s.score)}–{int(o.score)}"
                if x.ivy_ids and season == IVY.season:
                    res = f'<a href="{game_path(x)}">{res}</a>'
                lbl = f"{x.et.strftime('%b %-d, %Y')}"
                if x.note:
                    lbl += f' <span class="mu">· {esc(x.note)}</span>'
                rows.append(f'<tr><td>{lbl}</td><td>{res}</td>'
                            f'<td class="mu">{IVY_TEAMS[iv]} {where}</td></tr>')
    if not rows:
        return (f'<p class="note">No meeting between these teams in 2025–26 or so far in '
                f'{SEASON_LABEL}. (Older history is not in this prototype’s data.)</p>')
    return f'<table class="h2h"><tbody>{"".join(rows)}</tbody></table>'


def matchup_header(teams, S, g, final=False, box=None):
    eyebrow = "Box score" if final else "Game program"
    occ = []
    if g.conf:
        occ.append("Ivy League game")
    if g.note:
        occ.append(g.note)
    if not g.conf and not g.note:
        occ.append("Non-conference")
    sides = []
    for s in g.sides:
        t = team(teams, s.tid, s.name, s.short)
        wl = S.record(s.tid, before=g.utc) if not final else S.record(s.tid)
        if t.d1:
            r = f'{rec(wl)}'
            if t.ivy:
                r += f' <span class="mu">· {rec(S.record(s.tid, True))} Ivy</span>'
            elif t.standing:
                r += f' <span class="mu">· {esc(t.standing)}</span>'
        else:
            r = '<span class="mu">Non-Division I</span>'
        rk = f'<span class="rk">No. {s.rank}</span><br>' if s.rank else ""
        lost = final and s.winner is False
        big = (f'<div class="big">{int(s.score)}</div>'
               if final and s.score is not None else "")
        sides.append(
            f'<div class="tm{" lost" if lost else ""}"><div class="crest" style="--c:{t.color}">'
            f'{esc(t.abbr)}</div>{rk}<div class="nm">{tlink(teams, s.tid, t.short)}</div>'
            f'<div class="mascot">{esc(t.mascot)}</div>'
            f'{"" if final else f"<div class=rec>{r}</div>"}{big}</div>')
    mid = connector(g) if not final else ("Final" + (
        f'<br>{esc(g.status_detail.split("/")[-1])}' if "OT" in g.status_detail else ""))
    facts = []
    if final:
        facts.append(f'<span class="item">{day_label(g.et_date, long=True)}, {g.et.year}</span>')
    else:
        facts.append(f'<span class="item"><b>{esc(tip(g))}</b></span>')
    if g.venue:
        facts.append(f'<span class="item">{esc(g.venue)}{", " + esc(g.city) if g.city else ""}'
                     f'{" (neutral)" if g.neutral else ""}</span>')
    if g.tv:
        facts.append(f'<span class="item">TV / stream: <b>{tv(g)}</b></span>')
    return (f'<div class="prog"><div class="eyebrow">{eyebrow}</div>'
            f'<div class="occasion">{esc(" · ".join(occ))}</div>'
            f'<div class="vs">{sides[0]}<div class="mid">{mid}</div>{sides[1]}</div>'
            f'<div class="facts">{"".join(facts)}</div></div>')


def page_program(teams, S, rosters, scheds, g, as_of):
    a, h = g.away.tid, g.home.tid
    ta, th = team(teams, a), team(teams, h)
    body = [f'<div class="crumb"><a href="/schedule/">Schedule</a> › '
            f'{esc(ta.short)} {connector(g)} {esc(th.short)}</div>',
            matchup_header(teams, S, g)]
    if g.state == "in":
        body.append('<p class="note">In progress — the box score replaces this program '
                    'once the game is final.</p>')
    rost = {}
    for tid in (a, h):
        rost[tid] = roster_table(teams, S, tid, rosters)
    # Players to watch
    body.append('<h2 class="sec">Players to watch<span class="aside">picked from season '
                'averages</span></h2><div class="two">'
                + "".join(f'<div class="panel"><h3>{tlink(teams, t)}</h3>'
                          f'{watch_html(teams, S, t, rost[t][1], teams[t].color)}</div>'
                          for t in (a, h)) + '</div>')
    # Tale of the tape
    body.append('<h2 class="sec">Tale of the tape<span class="aside">season to date</span></h2>'
                + tape_html(teams, S, a, h))
    # Recent form
    forms = []
    for t in (a, h):
        f = form_html(teams, S, t, g.utc)
        tbl, pips = f if isinstance(f, tuple) else (f, "")
        note = "" if teams[t].d1 else ('<p class="note">Games vs. Division I opponents '
                                       'only — ESPN carries no others.</p>')
        forms.append(f'<div class="panel"><h3>{tlink(teams, t)}'
                     f'<span class="pips">{pips}</span></h3>{tbl}{note}</div>')
    body.append('<h2 class="sec">Recent form<span class="aside">last five, newest first'
                f'</span></h2><div class="two">{"".join(forms)}</div>')
    body.append('<h2 class="sec">Head to head</h2>' + h2h_html(teams, scheds, g))
    # Rosters
    body.append('<h2 class="sec">Rosters<span class="aside">season averages · ESPN '
                'box scores</span></h2><div class="two">'
                + "".join(f'<div class="panel"><h3><i class="sw" style="background:'
                          f'{teams[t].color};height:15px"></i>{tlink(teams, t, teams[t].full)}'
                          f'<span class="mu">{esc(coach(rosters, t))}</span></h3>{rost[t][0]}</div>'
                          for t in (a, h)) + '</div>')
    title = f"{ta.short} {connector(g)} {th.short}, {g.et.strftime('%b %-d')} — game program"
    write(game_path(g), shell(game_path(g), title, "\n".join(body),
                              f"Game program: {ta.full} {connector(g)} {th.full}, "
                              f"{day_label(g.et_date, long=True)}. Rosters, players to "
                              "watch, tale of the tape.", as_of))


def coach(rosters, tid):
    c = ((rosters.get(tid) or {}).get("coach") or [None])[0]
    if not c:
        return ""
    return f"Head coach {c.get('firstName', '')} {c.get('lastName', '')}".strip()


# ══ Box score ══════════════════════════════════════════════════════════════

def pct_str(m, a):
    if m is None or a is None:
        return DASH
    return "—" if not a else f"{100 * m / a:.0f}%"


def ma(m, a):
    return DASH if m is None or a is None else f"{m}-{a}"


def player_box_table(teams, tid, b):
    ps = b["players"]
    cols = ("MIN", "PTS", "FG", "3PT", "FT", "REB", "OREB", "AST", "TO", "STL", "BLK", "PF")
    head = "".join(f"<th>{c if c != 'OREB' else 'OR'}</th>" for c in cols)

    def cells(p):
        out = []
        for c in cols:
            if c in ("FG", "3PT", "FT"):
                out.append(f"<td>{ma(p.get(c + 'm'), p.get(c + 'a'))}</td>")
            else:
                out.append(f"<td>{fint(p.get(c))}</td>")
        return "".join(out)

    rows = []
    for label, grp in (("Starters", [p for p in ps if p["starter"]]),
                       ("Bench", [p for p in ps if not p["starter"]])):
        if not grp:
            continue
        rows.append(f'<tr class="sec"><td colspan="{len(cols) + 1}">{label}</td></tr>')
        for p in grp:
            nm = (f'<span class="jn">{esc(p["jersey"] or "")}</span>{esc(p["name"])}'
                  f'<span class="pos">{esc(p["pos"] or "")}</span>')
            if p["dnp"]:
                why = esc(p.get("reason") or "DNP")
                rows.append(f'<tr class="dnp"><td>{nm}</td><td colspan="{len(cols)}" '
                            f'style="text-align:left">{why}</td></tr>')
            else:
                rows.append(f"<tr><td>{nm}</td>{cells(p)}</tr>")
    t = team_line(b)
    tot = {"MIN": sum(p.get("MIN") or 0 for p in ps) or None, "PTS": t["pts"],
           "FGm": t["fgm"], "FGa": t["fga"], "3PTm": t["tpm"], "3PTa": t["tpa"],
           "FTm": t["ftm"], "FTa": t["fta"], "REB": t["reb"], "OREB": t["oreb"],
           "AST": t["ast"], "TO": t["tov"], "STL": t["stl"], "BLK": t["blk"], "PF": t["pf"]}
    rows.append(f'<tr class="tot"><td>Team</td>{cells(tot)}</tr>')
    rows.append('<tr class="pc"><td></td><td></td><td></td>'
                f'<td>{pct_str(t["fgm"], t["fga"])}</td><td>{pct_str(t["tpm"], t["tpa"])}</td>'
                f'<td>{pct_str(t["ftm"], t["fta"])}</td><td colspan="7"></td></tr>')
    return table_scroll(f'<table class="bx"><thead><tr><th>Player</th>{head}'
                        f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def page_box(teams, S, g, box, as_of):
    a, h = g.away.tid, g.home.tid
    ta, th = team(teams, a), team(teams, h)
    body = [f'<div class="crumb"><a href="/schedule/">Schedule</a> › '
            f'{esc(ta.short)} {connector(g)} {esc(th.short)}</div>',
            matchup_header(teams, S, g, final=True)]
    if not box or a not in box or h not in box:
        body.append('<p class="note">ESPN has marked this game final but has not '
                    'published a box score for it yet.</p>')
    else:
        # Line score
        n = max(len(box[a]["line"]), len(box[h]["line"]))
        hdr = "".join(f"<th>{i + 1 if i < 4 else ('OT' if n == 5 else f'OT{i - 3}')}</th>"
                      for i in range(n))
        lr = "".join(
            f'<tr><td>{esc(teams[t].short)}</td>'
            + "".join(f"<td>{fint(v)}</td>" for v in box[t]["line"])
            + f'<td class="t">{fint(box[t]["score"])}</td></tr>' for t in (a, h))
        body.append('<h2 class="sec">Line score</h2>' + table_scroll(
            f'<table class="ls"><thead><tr><th></th>{hdr}<th>T</th></tr></thead>'
            f'<tbody>{lr}</tbody></table>'))
        # Leaders
        cards = []
        for k, lab in (("PTS", "Points"), ("REB", "Rebounds"), ("AST", "Assists")):
            rows = []
            for t in (a, h):
                ps = [p for p in box[t]["players"] if p.get(k) is not None]
                if not ps:
                    rows.append(f'<div class="p"><span class="who mu">{esc(teams[t].short)}</span>'
                                f'<span class="v">{DASH}</span></div>')
                    continue
                top = max(p[k] for p in ps)
                who = [p for p in ps if p[k] == top]
                nm = " / ".join(esc(p["short"]) for p in who[:2])
                rows.append(f'<div class="p"><span class="who">{nm} '
                            f'<span class="mu">{esc(teams[t].abbr)}</span></span>'
                            f'<span class="v">{int(top)}</span></div>')
            cards.append(f'<div class="c"><div class="k">{lab}</div>{"".join(rows)}</div>')
        body.append(f'<h2 class="sec">Game leaders</h2><div class="ldr">{"".join(cards)}</div>')
        # Team stats
        la, lh = team_line(box[a]), team_line(box[h])
        trs = [("Field goals", ma(la["fgm"], la["fga"]), ma(lh["fgm"], lh["fga"])),
               ("FG %", pct_str(la["fgm"], la["fga"]), pct_str(lh["fgm"], lh["fga"])),
               ("3-pointers", ma(la["tpm"], la["tpa"]), ma(lh["tpm"], lh["tpa"])),
               ("Free throws", ma(la["ftm"], la["fta"]), ma(lh["ftm"], lh["fta"])),
               ("Rebounds (off.)", f'{fint(la["reb"])} ({fint(la["oreb"])})',
                f'{fint(lh["reb"])} ({fint(lh["oreb"])})'),
               ("Assists", fint(la["ast"]), fint(lh["ast"])),
               ("Turnovers", fint(la["tov"]), fint(lh["tov"])),
               ("Points off turnovers", fint(la["pito"]), fint(lh["pito"])),
               ("Points in the paint", fint(la["pip"]), fint(lh["pip"])),
               ("Fast-break points", fint(la["fbp"]), fint(lh["fbp"])),
               ("Steals / blocks", f'{fint(la["stl"])} / {fint(la["blk"])}',
                f'{fint(lh["stl"])} / {fint(lh["blk"])}'),
               ("Largest lead", fint(la["lead"]), fint(lh["lead"]))]
        body.append('<h2 class="sec">Team stats</h2>' + table_scroll(
            f'<table class="ls"><thead><tr><th></th><th>{esc(ta.abbr)}</th>'
            f'<th>{esc(th.abbr)}</th></tr></thead><tbody>'
            + "".join(f"<tr><td>{l}</td><td>{x}</td><td>{y}</td></tr>" for l, x, y in trs)
            + "</tbody></table>"))
        for t in (a, h):
            body.append(f'<h2 class="sec">{tlink(teams, t, teams[t].full)}</h2>'
                        + player_box_table(teams, t, box[t]))
        body.append('<p class="note">NCAA women’s box scores carry no plus-minus. '
                    'A stat ESPN did not carry shows as —, never as 0.</p>')
    w = g.home if g.home.winner else g.away
    l = g.other(w.tid)
    sc = lambda s: "" if s.score is None else f" {int(s.score)}"
    title = (f"{team(teams, w.tid).short}{sc(w)}, {team(teams, l.tid).short}{sc(l)}"
             f" — box score, {g.et.strftime('%b %-d, %Y')}")
    write(game_path(g), shell(game_path(g), title, "\n".join(body),
                              f"Box score: {ta.full} {connector(g)} {th.full}, "
                              f"{day_label(g.et_date, long=True)}.", as_of))


# ══ Editorial files (Claude drafts, Jason edits) ═══════════════════════════
#
# Prose that a person signs off on lives in sites/ivy/reference/ as small
# Markdown files, NOT in this script. The build renders whatever the file says;
# a missing or empty file renders an honest empty slot, never generated filler.
#
#   reference/whats_new.md                 home page "What's new"
#   reference/profiles/<school>-program.md history and identity — written once,
#                                          true forever (WWC profile rules)
#   reference/profiles/<school>-season.md  this season so far — refreshed by hand
#
# A file whose first line is `<!-- draft -->` is labelled as an unedited Claude
# draft; Jason deletes that line when he has edited it. `[CHECK: ...]` marks a
# claim Claude could not verify from data or an opened source, and renders as
# a visible amber flag so it cannot ship unnoticed.

import re as _re

REF = IVY.site_dir / "reference"


def read_ed(name):
    p = REF / name
    try:
        text = p.read_text().strip()
    except FileNotFoundError:
        return None, False
    draft = text.startswith("<!-- draft -->")
    text = _re.sub(r"^<!--.*?-->\s*", "", text, flags=_re.S).strip()
    return (text or None), draft


def md_inline(t):
    t = esc(t, quote=False)
    t = _re.sub(r"\[CHECK:?\s*([^\]]*)\]",
                lambda m: f'<span class="chk">CHECK{": " + m.group(1) if m.group(1) else ""}</span>', t)
    t = _re.sub(r"\[([^\]]+)\]\((/[^)\s]*|https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    t = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = _re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*", r"<i>\1</i>", t)
    return t


def md(text):
    """Paragraphs, ### headings, **bold**, *italic*, [links](/path), [CHECK].
    Deliberately tiny: zero dependencies, and the files are a few sentences."""
    out = []
    for block in _re.split(r"\n\s*\n", text.strip()):
        block = " ".join(l.strip() for l in block.splitlines())
        if block.startswith("### "):
            out.append(f"<h3>{md_inline(block[4:])}</h3>")
        else:
            out.append(f"<p>{md_inline(block)}</p>")
    return "".join(out)


def ed_block(name, label, empty_note, color=None):
    text, draft = read_ed(name)
    lab = f'<div class="edlab">{esc(label)}'
    if text and draft:
        lab += ' · <b>Draft by Claude — awaiting Jason’s edit</b>'
    lab += '</div>'
    style = f' style="--c:{color}"' if color else ""
    if not text:
        return f'{lab}<div class="edbox"{style}><p class="empty">{esc(empty_note)}</p></div>'
    return f'{lab}<div class="edbox ed"{style}>{md(text)}</div>'


# ══ Team pages ═════════════════════════════════════════════════════════════

def home_venue(S, tid):
    from collections import Counter
    c = Counter((g.venue, g.city) for g in S.games_by_team.get(tid, [])
                if g.home.tid == tid and not g.neutral and g.venue)
    return c.most_common(1)[0][0] if c else (None, None)


def current_rank(S, tid, now):
    """AP rank as ESPN attaches it to the team's NEXT game, else its last."""
    gs = S.games_by_team.get(tid, [])
    nxt = next((g for g in gs if not g.completed and g.utc >= now), None)
    g = nxt or next((g for g in reversed(gs) if g.completed), None)
    return g.side(tid).rank if g else None


def prev_season(scheds, tid):
    gs = [g for g in games_from(scheds.get((tid, PREV_SEASON))) if g.completed]
    w = sum(1 for g in gs if g.side(tid).winner)
    cw = sum(1 for g in gs if g.conf and not g.note and g.side(tid).winner)
    cl = sum(1 for g in gs if g.conf and not g.note and g.side(tid).winner is False)
    return (w, len(gs) - w), (cw, cl)


def ivy_ranks(S, key, higher):
    vals = {t: (S.team_rates(t) or {}).get(key) for t in IVY_TEAMS}
    known = [v for v in vals.values() if v is not None]
    avg = sum(known) / len(known) if known else None
    order = sorted((v for v in known), reverse=bool(higher))
    rank = {t: (order.index(v) + 1 if v is not None and higher is not None else None)
            for t, v in vals.items()}
    return vals, avg, rank


def vs_ivy_table(S, tid):
    rows = []
    for lab, k, fmt, higher in TAPE:
        vals, avg, rank = ivy_ranks(S, k, higher)
        v, r = vals[tid], rank[tid]
        top = r == 1
        rk = "" if r is None else f"{r}{'st' if r == 1 else 'nd' if r == 2 else 'rd' if r == 3 else 'th'}"
        rows.append(f'<tr{" class=\"top\"" if top else ""}><td>{lab}</td>'
                    f'<td class="n">{fmt(v)}</td><td class="n mu">{fmt(avg)}</td>'
                    f'<td class="n rk2">{rk or DASH}</td></tr>')
    return table_scroll('<table class="vsivy"><thead><tr><th>Per game</th>'
                        '<th class="n">Team</th><th class="n">Ivy avg</th>'
                        '<th class="n">Ivy rank</th></tr></thead><tbody>'
                        + "".join(rows) + '</tbody></table>')


def players_to_know(teams, S, tid, players):
    picks = pick_watch(players)
    if not picks:
        return '<p class="note">No box score yet this season to pick from.</p>'
    out = []
    for p in picks:
        r = p["rates"]
        bio = " · ".join(x for x in (p["pos"], p["cls"], p["ht"]) if x)
        out.append(
            f'<div class="pk"><span class="jn">{esc(IVY.jersey_prefix + p["jersey"]) if p["jersey"] else ""}</span>'
            f'<span class="pn">{esc(p["name"])}</span>'
            f'<span class="ln">{f1(r["ppg"])} pts · {f1(r["rpg"])} reb · {f1(r["apg"])} ast'
            f' · {fint(r["fg"])}% FG</span>'
            f'<span class="mu" style="font-size:11px">{esc(bio)}</span>'
            f'<span class="why">{esc(p["why"])}</span></div>')
    return "".join(out)


def team_schedule(teams, S, tid):
    rows = []
    for g in S.games_by_team.get(tid, []):
        s, o = g.side(tid), g.other(tid)
        ha = "vs." if g.neutral else ("" if g.home.tid == tid else "at")
        opp = f'{ha + " " if ha else ""}{team_name(teams, o)}'
        ven = " · ".join(x for x in (g.note, g.venue, g.city) if x)
        if g.completed and s.score is not None and o.score is not None:
            wl = "W" if s.winner else "L"
            res = (f'<a href="{game_path(g)}"><span class="wl">{wl}</span>'
                   f'{int(s.score)}–{int(o.score)}</a>')
            cls = wl
        else:
            res = f'<span class="mu">{tip(g, with_day=False)}</span> <a href="{game_path(g)}">Program</a>'
            cls = ""
        tvs = f' · {tv(g)}' if g.tv and not g.completed else ""
        rows.append(f'<tr class="{cls}{" conf" if g.conf else ""}">'
                    f'<td class="d">{g.et.strftime("%a %b %-d")}</td>'
                    f'<td>{opp}<span class="ven">{esc(ven)}{tvs}</span></td>'
                    f'<td class="r">{res}</td></tr>')
    return table_scroll(f'<table class="tsch"><tbody>{"".join(rows)}</tbody></table>')


def full_roster(teams, S, tid, rosters):
    players, has_roster = roster_rows(rosters, S, tid)
    if not players:
        return '<p class="note">ESPN publishes no roster for this team.</p>', players
    stars = {p["id"] for p in pick_watch(players)}
    rows = []
    for p in players:
        r = p["rates"] or {}
        rows.append(
            f'<tr{" class=\"star\"" if p["id"] in stars else ""}>'
            f'<td class="jn">{esc(p["jersey"] or "")}</td>'
            f'<td><span class="pn">{esc(p["name"])}</span>'
            f'<span class="ht">{esc(p["home"] or "")}</span></td>'
            f'<td class="c">{esc(p["pos"] or "—")}</td><td class="c">{esc(p["cls"] or "—")}</td>'
            f'<td class="c">{esc((p["ht"] or "—").replace(" ", ""))}</td>'
            f'<td class="n">{r.get("gp") or DASH}</td><td class="n">{fint(r.get("mpg"))}</td>'
            f'<td class="n">{f1(r.get("ppg"))}</td><td class="n">{f1(r.get("rpg"))}</td>'
            f'<td class="n">{f1(r.get("apg"))}</td><td class="n">{fint(r.get("fg"))}</td>'
            f'<td class="n">{fint(r.get("tp"))}</td><td class="n">{fint(r.get("ft"))}</td></tr>')
    return table_scroll(
        '<table class="ros"><thead><tr><th class="n">#</th><th>Player</th><th>Pos</th>'
        '<th>Yr</th><th>Ht</th><th class="n">GP</th><th class="n">MIN</th>'
        '<th class="n">PPG</th><th class="n">RPG</th><th class="n">APG</th>'
        '<th class="n">FG%</th><th class="n">3P%</th><th class="n">FT%</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'), players


def page_team(teams, S, rosters, scheds, tid, now, as_of):
    t = teams[tid]
    slug = team_slug(tid)
    venue, city = home_venue(S, tid)
    rk = current_rank(S, tid, now)
    (pw, pl), (pcw, pcl) = prev_season(scheds, tid)
    ng = S.next_game(tid, now)
    nxt = DASH
    if ng:
        o = ng.other(tid)
        where = "vs." if ng.neutral or ng.home.tid == tid else "at"
        nxt = (f'<a class="tl" href="{game_path(ng)}">{where} '
               f'{esc(team(teams, o.tid, o.name, o.short).short)}</a>')
    r = S.team_rates(tid) or {}
    diff = r.get("diff")
    sub = []
    c = coach(rosters, tid)
    if c:
        sub.append(f'<b>{esc(c.replace("Head coach ", ""))}</b>, head coach')
    if venue:
        sub.append(f'{esc(venue)}{", " + esc(city) if city else ""}')
    sub.append(f'Last season {pw}-{pl}, {pcw}-{pcl} Ivy')
    cards = [
        ("Overall", rec(S.record(tid)), f"No. {rk} in ESPN’s ranking" if rk else (S.streak(tid) and f"Streak {S.streak(tid)}") or ""),
        ("Ivy League", rec(S.record(tid, True)), "League play opens in January"),
        ("Margin", DASH if diff is None else f"{diff:+.1f}", f"{f1(r.get('ppg'))} for, {f1(r.get('oppg'))} against"
         .replace('<span class="mu">—</span>', "—")),
        ("Next", ng.et.strftime("%b %-d") if ng else DASH, nxt),
    ]
    cards_html = "".join(f'<div class="c"><div class="k">{k}</div><div class="v">{v}</div>'
                         f'<div class="s">{s_}</div></div>' for k, v, s_ in cards)
    rk_html = f'<span class="rk">No. {rk}</span> ' if rk else ""
    head = (f'<div class="prog"><div class="thd"><div class="crest" style="--c:{t.color}">'
            f'{esc(t.abbr)}</div><div><h2>{rk_html}{esc(t.short)}</h2>'
            f'<div class="sub">{esc(t.mascot)} · {" · ".join(sub)}</div></div></div>'
            f'<div class="tcards">{cards_html}</div></div>')
    roster_html, players = full_roster(teams, S, tid, rosters)
    body = [
        f'<div class="crumb"><a href="/teams/">Teams</a> › {esc(t.short)}</div>', head,
        '<h2 class="sec">The program</h2>'
        + ed_block(f"profiles/{slug}-program.md", "History and identity",
                   "Program profile not written yet.", t.color),
        f'<h2 class="sec">{SEASON_LABEL} so far</h2>'
        + ed_block(f"profiles/{slug}-season.md", "This season",
                   "Season note not written yet.", t.color),
        '<h2 class="sec">Players to know<span class="aside">picked from season averages'
        '</span></h2>' + players_to_know(teams, S, tid, players),
        '<h2 class="sec">Season stats vs. the Ivy League<span class="aside">'
        f'through {r.get("n", 0)} box scores</span></h2>' + vs_ivy_table(S, tid)
        + '<p class="note">Ivy average and rank are across the eight teams’ games so far, '
          'non-conference included, so they mostly reflect who each team has played. '
          'Ratings are points per 100 possessions. Rank 1 is best (lowest for opponent '
          'points, defensive rating and turnovers).</p>',
        '<h2 class="sec">Schedule and results<span class="aside">all times ET · '
        'green edge = Ivy game</span></h2>' + team_schedule(teams, S, tid),
        '<h2 class="sec">Roster<span class="aside">season averages from ESPN box scores'
        '</span></h2>' + roster_html,
    ]
    title = f"{t.full} — {SEASON_LABEL} women’s basketball · Ivy League"
    write(team_path(tid), shell(team_path(tid), title, "\n".join(body),
                                f"{t.full} women’s basketball {SEASON_LABEL}: schedule, "
                                "results, roster and season stats.", as_of))


def page_teams_index(teams, S, rosters, now, as_of):
    cards = []
    for tid in sorted(IVY_TEAMS, key=lambda x: IVY_TEAMS[x]):
        t = teams[tid]
        ng = S.next_game(tid, now)
        nx = ""
        if ng:
            o = ng.other(tid)
            where = "vs." if ng.neutral or ng.home.tid == tid else "at"
            nx = f'Next: {ng.et.strftime("%b %-d")} {where} {esc(team(teams, o.tid, o.name, o.short).short)}'
        c = coach(rosters, tid).replace("Head coach ", "")
        cards.append(f'<a href="{team_path(tid)}" style="--c:{t.color}">'
                     f'<div class="n">{esc(t.short)}</div><div class="m">{esc(t.mascot)}'
                     f'{" · " + esc(c) if c else ""}</div>'
                     f'<div class="r">{rec(S.record(tid))} <span class="mu">· '
                     f'{rec(S.record(tid, True))} Ivy</span></div><div class="x">{nx}</div></a>')
    body = (f'<div class="tix">{"".join(cards)}</div>'
            '<p class="note">Eight schools, fourteen league games each, home and away. '
            'The top four go to the Ivy League Tournament in March.</p>')
    write("/teams/", shell("/teams/", f"Teams — Ivy League Women’s Basketball {SEASON_LABEL}",
                           body, "The eight Ivy League women’s basketball teams.", as_of))


# ══ Main ═══════════════════════════════════════════════════════════════════

def main():
    teams, rosters, scheds, summaries, meta = load_world()
    fetched = meta.get("fetched_at_utc")
    now = (datetime.fromisoformat(fetched.replace("Z", "+00:00")) if fetched
           else datetime.now(timezone.utc))
    as_of = now.astimezone(ET).strftime("%b %-d, %Y")
    S = Season(teams, scheds, summaries)
    games = ivy_games(scheds)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    page_home(teams, S, games, now, as_of)
    page_schedule(teams, S, games, now, as_of)
    page_teams_index(teams, S, rosters, now, as_of)
    for tid in IVY_TEAMS:
        page_team(teams, S, rosters, scheds, tid, now, as_of)
    n_prog = n_box = 0
    for g in games:
        if g.completed:
            page_box(teams, S, g, parse_box(summaries.get(g.id)), as_of)
            n_box += 1
        else:
            page_program(teams, S, rosters, scheds, g, as_of)
            n_prog += 1
    # Pre-launch: block every crawler. At launch, replace with seo.write_robots()
    # and a sitemap, and drop the per-page noindex in shell().
    (OUT / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    print(f"{len(games)} Ivy games: {n_box} box scores, {n_prog} programs → {OUT}")


if __name__ == "__main__":
    main()
