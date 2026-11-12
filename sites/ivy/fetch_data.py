#!/usr/bin/env python3
"""fetch_data.py — pull everything the Ivy prototype needs from ESPN NCAAW.

LOCAL PROTOTYPE. Writes raw ESPN JSON under `sites/ivy/data/` (gitignored by
the repo-wide `sites/*/data/` rule). The build script reads only these files,
so a build never touches the network.

What is fetched, and why each piece:

    teams/{id}                  every Ivy team AND every opponent: colours,
                                conference group (D-I vs not), record
    teams/{id}/schedule         every Ivy team (this season + last, for
                                head-to-head) and every opponent (this season,
                                for records and recent form)
    teams/{id}/roster           every team in the program: number, position,
                                class, height, hometown
    summary?event=              every COMPLETED game of every team above —
                                the box scores that season averages, tale of
                                the tape and the Ivy box-score pages are
                                computed from. Cached on disk forever: a final
                                box score does not change.

URL rule (docs/data-sources.md): nothing here hand-builds an ESPN host. The
adapter `sag.adapters.espn` has scoreboard and summary builders only, so the
three team-level builders below live in this site script and are anchored on
`espn.ESPN_ORIGIN` — the same env-var switch every other caller uses. If they
prove out they belong in the adapter (noted in the prototype README as a core/
change, deliberately NOT made here).

Usage:
    .venv/bin/python sites/ivy/fetch_data.py
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from sag.adapters import espn

from config import (ESPN_LEAGUE, IVY, IVY_TEAMS, PREV_SEASON)

# ── URL builders (site-local; see module docstring) ─────────────────────────
_BASE = f"/apis/site/v2/sports/basketball/{ESPN_LEAGUE}"


def team_url(team_id):
    return f"{espn.ESPN_ORIGIN}{_BASE}/teams/{team_id}"


def schedule_url(team_id, season):
    return f"{espn.ESPN_ORIGIN}{_BASE}/teams/{team_id}/schedule?season={season}"


def roster_url(team_id):
    return f"{espn.ESPN_ORIGIN}{_BASE}/teams/{team_id}/roster"


RAW = IVY.data_dir / "raw"
DIRS = {k: RAW / k for k in ("teams", "schedules", "rosters", "summaries")}


def get(url, tries=3):
    for i in range(tries):
        try:
            return espn.fetch_json(url)
        except Exception as e:  # noqa: BLE001 — report and retry, then give up
            if i == tries - 1:
                print(f"  ! {url}: {e}", file=sys.stderr)
                return None
            time.sleep(1.5 * (i + 1))


def save(path, payload):
    path.write_text(json.dumps(payload))


def pmap(fn, items, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def completed_ids(schedule):
    out = []
    for ev in (schedule or {}).get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        st = ((comp.get("status") or {}).get("type") or {})
        if st.get("completed"):
            out.append(ev["id"])
    return out


def main():
    for d in DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # 1. Ivy schedules, both seasons.
    ivy_ids = list(IVY_TEAMS)
    sched = {}

    def fetch_sched(args):
        tid, season = args
        payload = get(schedule_url(tid, season))
        if payload is not None:
            save(DIRS["schedules"] / f"{tid}_{season}.json", payload)
        return (tid, season), payload

    for key, payload in pmap(fetch_sched, [(t, s) for t in ivy_ids
                                           for s in (IVY.season, PREV_SEASON)]):
        sched[key] = payload
    print(f"ivy schedules: {len(sched)}")

    # 2. Opponents seen this season.
    teams = set(ivy_ids)
    for t in ivy_ids:
        for ev in (sched.get((t, IVY.season)) or {}).get("events") or []:
            for c in ev["competitions"][0]["competitors"]:
                teams.add(c["team"]["id"])
    opps = sorted(teams - set(ivy_ids))
    print(f"opponents: {len(opps)}")

    for key, payload in pmap(fetch_sched, [(t, IVY.season) for t in opps]):
        sched[key] = payload

    # 3. Team info + rosters for everyone.
    def fetch_team(tid):
        info = get(team_url(tid))
        if info is not None:
            save(DIRS["teams"] / f"{tid}.json", info)
        roster = get(roster_url(tid))
        if roster is not None:
            save(DIRS["rosters"] / f"{tid}.json", roster)
        return tid, info is not None, roster is not None

    res = pmap(fetch_team, sorted(teams))
    print(f"teams: {sum(r[1] for r in res)}/{len(res)} info, "
          f"{sum(r[2] for r in res)}/{len(res)} rosters")

    # 4. Summaries for every completed game of every team (this season), and
    #    for every Ivy game of either season that has not been cached.
    want = set()
    for (tid, season), payload in sched.items():
        if season == IVY.season:
            want.update(completed_ids(payload))
    todo = [e for e in sorted(want)
            if not (DIRS["summaries"] / f"{e}.json").exists()]

    def fetch_summary(eid):
        payload = get(espn.summary_url(ESPN_LEAGUE, eid))
        if payload is None:
            return eid, False
        # Only cache a summary the feed itself calls final; anything else is
        # re-fetched next run.
        st = (((payload.get("header") or {}).get("competitions") or [{}])[0]
              .get("status") or {}).get("type") or {}
        if st.get("completed"):
            # Drop the heavy parts the build never reads.
            for k in ("plays", "news", "videos", "winprobability",
                      "pickcenter", "odds", "againstTheSpread", "ticketsInfo"):
                payload.pop(k, None)
            save(DIRS["summaries"] / f"{eid}.json", payload)
        return eid, True

    res = pmap(fetch_summary, todo)
    print(f"summaries: {len(want)} completed games, {len(todo)} fetched, "
          f"{sum(1 for r in res if not r[1])} failed")

    save(IVY.data_dir / "fetch_meta.json", {
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "origin": espn.ESPN_ORIGIN,
        "teams": len(teams),
    })
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
