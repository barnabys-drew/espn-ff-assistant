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

from common import connect, load_env
from draft import NON_STARTING_SLOTS, POSITIONS, POOL_DEPTH


def starting_slots(settings):
    """Per-team starting slots as {slot_name: count}.

    Unlike the draft board, this is one team's lineup, so slot counts are not
    multiplied by the size of the league.
    """
    slots = {}
    for slot, count in (getattr(settings, "position_slot_counts", {}) or {}).items():
        if count and slot not in NON_STARTING_SLOTS:
            slots[slot] = int(count)
    return slots


def projection(player, weeks):
    """Projected points for a player over `weeks`, or the season if weeks is None.

    player.stats is keyed by scoring period, with week 0 holding season totals,
    so a missing week means no projected game — a bye, or a season that has not
    reached that week yet. Both correctly contribute nothing.
    """
    stats = getattr(player, "stats", None) or {}
    if weeks is None:
        return float(getattr(player, "projected_total_points", 0) or 0)
    total = 0.0
    for week in weeks:
        total += float((stats.get(week) or {}).get("projected_points", 0) or 0)
    return total


def as_row(player, weeks):
    """Flatten an espn-api Player into the plain dict the ranking works on."""
    return {
        "name": player.name,
        "pos": player.position,
        "team": player.proTeam,
        "slots": [s for s in (getattr(player, "eligibleSlots", None) or [])],
        "proj": round(projection(player, weeks), 1),
        "owned": round(float(getattr(player, "percent_owned", 0) or 0), 1),
        "injury": (getattr(player, "injuryStatus", "") or "").replace("ACTIVE", ""),
        "player_id": getattr(player, "playerId", None),
    }


def best_lineup(rows, slots):
    """Highest-scoring legal lineup from `rows`, as (total, starter_ids).

    Slots are filled scarcest-first: a slot that few players are eligible for is
    resolved before a slot that many are, so a flex does not greedily consume
    the only player who could have filled a dedicated slot. This is the same
    heuristic the draft board uses to allocate league-wide flex demand.

    Eligibility comes from the API (eligibleSlots) rather than being inferred
    from the position string, so odd slots — superflex, WR/TE, IDP — work
    without this function knowing they exist.
    """
    openings = [slot for slot, count in slots.items() for _ in range(count)]
    # Scarcity is measured against this specific player set, not in the
    # abstract: a TE slot is scarce when you roster two tight ends.
    openings.sort(key=lambda slot: sum(1 for r in rows if slot in r["slots"]))

    used, total = set(), 0.0
    for slot in openings:
        eligible = [r for r in rows
                    if slot in r["slots"] and id(r) not in used]
        if not eligible:
            continue
        pick = max(eligible, key=lambda r: r["proj"])
        used.add(id(pick))
        total += pick["proj"]
    return round(total, 1), used


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


def find_my_team(league, swid=None, team_id=None):
    """Locate the user's team by SWID, or by an explicit --team-id override."""
    if team_id is not None:
        for team in league.teams:
            if team.team_id == team_id:
                return team
        sys.exit(f"No team with id {team_id}. Teams: "
                 + ", ".join(f"{t.team_id}={t.team_name}" for t in league.teams))

    def norm(value):
        return str(value).strip().strip("{}").upper()

    if swid:
        want = norm(swid)
        for team in league.teams:
            for owner in (team.owners or []):
                ids = owner.values() if isinstance(owner, dict) else [owner]
                if any(norm(i) == want for i in ids):
                    return team

    sys.exit("Could not tell which team is yours from the SWID in .env.\n"
             "Re-run with --team-id, using one of:\n  "
             + "\n  ".join(f"{t.team_id}  {t.team_name}" for t in league.teams))


def scoring_weeks(league, week):
    """The weeks to value players over: one week for streaming, else what is left."""
    if week is not None:
        return [week]
    last = int(getattr(league.settings, "reg_season_count", 0) or 17)
    current = int(getattr(league, "current_week", 1) or 1)
    return list(range(max(1, current), last + 1)) or [max(1, current)]


def build(league, team, weeks, depth, only_pos, limit):
    roster = [as_row(p, weeks) for p in team.roster]

    free_agents = []
    for pos in ([only_pos] if only_pos else POSITIONS):
        for p in league.free_agents(week=weeks[0], size=depth.get(pos, 50),
                                    position=pos):
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
    if data["drop_candidate"]:
        d = data["drop_candidate"]
        print(f"Weakest bench player:         {d['name']} ({d['pos']}, "
              f"{d['proj']:.1f}) — the default drop")
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
