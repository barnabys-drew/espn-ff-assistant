"""Rank the waiver wire by how much each free agent would improve your lineup.

Run:  .venv/bin/python waivers.py                # rest-of-season adds
      .venv/bin/python waivers.py --week 5       # streaming, week 5 only
      .venv/bin/python waivers.py --pos RB       # one position
      .venv/bin/python waivers.py --season 2025  # a past season, for testing

Reads only. Nothing here adds, drops, or claims anybody — you take the names
off this list and make the moves in ESPN yourself.

The draft board asks "who is the best player available?". In-season that is the
wrong question, because you cannot roster everyone: what matters is whether a
free agent beats somebody you are already starting. So the metric here is
**lineup gain** — your best possible starting lineup with the player added,
minus your best possible starting lineup today.

That framing handles the awkward cases by construction. A great WR is worth
nothing to you if your two WR slots and your flex are already better; a mediocre
TE is worth a lot if your starting TE is on bye. Gain answers both without
special-casing either.

Rest-of-season value is summed from the remaining weeks rather than taken from
projected_total_points, which is a *full-season* number and so still includes
games that have already been played.
"""
import argparse
import json
import os
import sys

import weekly
from common import connect, find_my_team, load_env
from lineup import (POOL_DEPTH, POSITIONS, as_row, best_lineup, projection,
                    starting_slots)


def rank_adds(roster, free_agents, slots, limit=None):
    """Rank free agents by how much each one raises your best starting lineup.

    Returns rows carrying `gain` and the `drop` each add would cost you, worst
    bench player first, since a full roster means every add is also a drop.
    """
    base_total, base_starters = best_lineup(roster, slots)

    bench = sorted((r for r in roster if id(r) not in base_starters),
                   key=lambda r: r["proj"])
    drop = bench[0] if bench else None

    ranked = []
    for fa in free_agents:
        total, _ = best_lineup(roster + [fa], slots)
        gain = round(total - base_total, 1)
        if gain <= 0:
            continue  # cannot crack the lineup: not a claim, whatever the name
        ranked.append(dict(fa, gain=gain,
                           drop=drop["name"] if drop else None,
                           drop_proj=drop["proj"] if drop else None))

    ranked.sort(key=lambda r: -r["gain"])
    if limit:
        ranked = ranked[:limit]
    for i, row in enumerate(ranked, 1):
        row["rank"] = i
    return ranked, base_total, drop


def scoring_weeks(league, week):
    """The weeks to value players over: one week for streaming, else what is left."""
    if week is not None:
        return [week]
    last = int(getattr(league.settings, "reg_season_count", 0) or 17)
    current = int(getattr(league, "current_week", 1) or 1)
    return list(range(max(1, current), last + 1)) or [max(1, current)]


def build(league, team, weeks, depth, only_pos, limit):
    # Not [as_row(p, weeks) for p in team.roster]: a rostered player's stats
    # only ever carry week 0 and the league's current scoring period, so that
    # projects your entire roster at 0.0 for any specific week. Every free
    # agent then "improves" a 0.0 lineup by his own projection and the ranking
    # collapses to raw points. weekly.py reads the same weeks the free agents
    # are read for. See its docstring.
    def note(week):
        if len(weeks) > 1:
            print(f"  reading week {week}...", flush=True)

    roster, roster_weeks, roster_errors = weekly.roster_projections(
        league, team, weeks, on_week=note)
    if roster_errors:
        roster = [as_row(p, weeks) for p in team.roster]

    free_agents, covered = [], set()
    for pos in ([only_pos] if only_pos else POSITIONS):
        for p in league.free_agents(week=weeks[0], size=depth.get(pos, 50),
                                    position=pos):
            # Which of the weeks we are valuing over actually carry a
            # projection. ESPN does not always publish future weeks, and a
            # rest-of-season number summed over one week of data is a
            # rest-of-season number in name only.
            stats = getattr(p, "stats", None) or {}
            covered.update(w for w in weeks
                           if (stats.get(w) or {}).get("projected_points"))
            row = as_row(p, weeks)
            if row["proj"] > 0:
                free_agents.append(row)

    slots = starting_slots(league.settings)
    ranked, base_total, drop = rank_adds(roster, free_agents, slots, limit)

    return {
        "league": league.settings.name,
        "season": league.year,
        "team": team.team_name,
        "weeks": weeks,
        "slots": slots,
        "lineup_total": base_total,
        "drop_candidate": drop,
        "faab_spent": getattr(team, "acquisition_budget_spent", None),
        "waiver_rank": getattr(team, "waiver_rank", None),
        "free_agents_considered": len(free_agents),
        "weeks_with_data": sorted(covered),
        "roster_weeks_read": roster_weeks,
        "roster_errors": roster_errors,
        "adds": ranked,
    }


HEADER = (f"{'#':>3}  {'PLAYER':<22} {'POS':<5} {'TM':<4} {'PROJ':>7} {'GAIN':>7}  "
          f"{'OWN':>6}  DROP")


def format_row(r):
    flag = f" {r['injury'][:3]}" if r["injury"] else ""
    drop = f"{r['drop']} ({r['drop_proj']:.1f})" if r["drop"] else "-"
    return (f"{r['rank']:>3}  {r['name'][:22]:<22} {r['pos']:<5} {r['team']:<4} "
            f"{r['proj']:>7.1f} {r['gain']:>+7.1f}  {r['owned']:>5.1f}%{flag}  {drop}")


def report(data):
    weeks = data["weeks"]
    window = (f"week {weeks[0]}" if len(weeks) == 1
              else f"weeks {weeks[0]}-{weeks[-1]} (rest of season)")

    print("=" * 88)
    print(f"WAIVER WIRE — {data['team']} — {data['league']} ({data['season']})")
    print(f"Valuing over {window}.")
    print("=" * 88)
    print(f"Your best lineup as rostered: {data['lineup_total']:.1f} projected pts")
    if data.get("roster_errors"):
        print(f"! Could not read your roster per week — {data['roster_errors'][0]}. "
              f"Fell back to season-total projections, which are NOT comparable "
              f"to the per-week free agent numbers below. Treat GAIN as a "
              f"ranking hint only.")
    if data["drop_candidate"]:
        d = data["drop_candidate"]
        print(f"Weakest bench player:         {d['name']} ({d['pos']}, "
              f"{d['proj']:.1f}) — the default drop")
    have = data.get("weeks_with_data") or []
    if len(have) < len(weeks):
        missing = len(weeks) - len(have)
        print(f"! Only {len(have)} of {len(weeks)} weeks carry projections "
              f"(missing {missing}). ESPN has not published the rest, so these "
              f"totals cover weeks {have or ['none']} only, not {window}.")
    if data["waiver_rank"]:
        print(f"Waiver priority:              #{data['waiver_rank']}")
    if data["faab_spent"] is not None:
        print(f"FAAB spent so far:            {data['faab_spent']}")

    checked = data["free_agents_considered"]
    noun = "free agent" if checked == 1 else "free agents"
    if not data["adds"]:
        print(f"\nNothing on the wire improves your lineup "
              f"({checked} {noun} checked).")
        return

    print(f"\n--- ADDS THAT IMPROVE YOUR LINEUP "
          f"({len(data['adds'])} of {checked} {noun} checked) ---")
    print(HEADER)
    for r in data["adds"]:
        print(format_row(r))
    print("\nGAIN = projected points added to your best starting lineup over "
          f"{window},\nafter accounting for who they would displace.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--week", type=int,
                    help="value players for this week only (streaming); "
                         "default is the rest of the season")
    ap.add_argument("--pos", choices=POSITIONS, help="only this position")
    ap.add_argument("--top", type=int, default=25, help="rows to show")
    ap.add_argument("--season", type=int,
                    help="season to read (default: SEASON from .env)")
    ap.add_argument("--team-id", type=int,
                    help="your team id, if it cannot be found from your SWID")
    ap.add_argument("--depth", type=int, help="free agents pulled per position")
    args = ap.parse_args()

    env = load_env()
    league = connect(env, year=args.season)
    team = find_my_team(league, swid=env.get("SWID"), team_id=args.team_id)
    weeks = scoring_weeks(league, args.week)
    depth = ({pos: args.depth for pos in POSITIONS} if args.depth else POOL_DEPTH)

    data = build(league, team, weeks, depth, args.pos, args.top)
    report(data)

    os.makedirs("out", exist_ok=True)
    with open("out/waivers.json", "w") as fh:
        json.dump(data, fh, indent=2)
    print("\nWrote out/waivers.json")


if __name__ == "__main__":
    main()
