"""Build a value-over-replacement draft board from the undrafted player pool.

Run:  .venv/bin/python draft.py              # full board + per-position tiers
      .venv/bin/python draft.py --pos RB     # one position
      .venv/bin/python draft.py --top 100    # deeper board
      .venv/bin/python draft.py --season 2025 --week 1   # past season, for testing

Reads only. ESPN's projections come back already scored by *your* league's
rules when fetched in league context, so no re-scoring is needed here — the
work is turning raw projections into positional value.

Value over replacement: a projection is only worth what it beats. The
replacement level for a position is the projection of the best player who
would still be sitting on waivers once every team has filled its starting
slots at that position. VOR = projection - that baseline, which is what makes
a QB's 320 points and a RB's 210 comparable.

Because the pool is "everyone undrafted", the board also works mid-draft as a
best-available sheet: re-run it between picks.
"""
import argparse
import json
import os
import statistics
import sys

from common import connect

POSITIONS = ["QB", "RB", "WR", "TE", "D/ST", "K"]

# How deep to pull each position. ESPN returns free agents sorted by percent
# owned, so these depths comfortably cover everyone who will be drafted.
POOL_DEPTH = {"QB": 60, "RB": 160, "WR": 190, "TE": 80, "D/ST": 32, "K": 32}

# Roster slots that are not starting slots and so create no demand.
NON_STARTING_SLOTS = {"BE", "IR", "ER", "Rookie", ""}


def parse_slots(slot_counts, team_count):
    """Split roster slots into fixed per-position demand and flex demand.

    Returns (base, flex) where base maps position -> league-wide starter count,
    and flex is a list of (eligible_positions, demand) for combination slots.
    """
    base, flex = {}, []
    for slot, count in (slot_counts or {}).items():
        if not count or slot in NON_STARTING_SLOTS:
            continue
        demand = count * team_count
        if slot == "D/ST":  # a real position despite the slash
            base["D/ST"] = base.get("D/ST", 0) + demand
        elif slot == "OP":  # superflex
            flex.append((["QB", "RB", "WR", "TE"], demand))
        elif "/" in slot:
            flex.append((slot.split("/"), demand))
        else:
            base[slot] = base.get(slot, 0) + demand
    return base, flex


def allocate_flex(pool, base, flex):
    """Fill flex slots with the best players left past each position's starters.

    Returns position -> total startable count league-wide. This is what decides
    where replacement level actually falls: in a 12-team league with one
    RB/WR/TE, the flex is mostly running backs, so RB replacement sits deeper
    than its two fixed slots alone would suggest.
    """
    startable = dict(base)
    taken = {pos: base.get(pos, 0) for pos in pool}

    # Narrower slots first — they have fewer players to draw from.
    for members, demand in sorted(flex, key=lambda f: len(f[0])):
        candidates = []
        for pos in members:
            candidates.extend(pool.get(pos, [])[taken.get(pos, 0):])
        candidates.sort(key=lambda p: -p["proj"])
        for player in candidates[:demand]:
            pos = player["pos"]
            taken[pos] = taken.get(pos, 0) + 1
            startable[pos] = startable.get(pos, 0) + 1
    return startable


def replacement_levels(pool, startable):
    """Projection of the first player past the last startable one, per position."""
    levels = {}
    for pos, players in pool.items():
        if not players:
            continue
        idx = min(startable.get(pos, 0), len(players) - 1)
        levels[pos] = players[idx]["proj"]
    return levels


def assign_tiers(players):
    """Group a position's players by scoring cliffs.

    A new tier starts wherever the drop to the next player is unusually large
    for that position — mean gap plus one standard deviation. Tiers are the
    part of a cheat sheet you actually draft off: inside a tier, take need or
    upside; between tiers, take the player.
    """
    if not players:
        return
    for player in players:
        player["tier"] = 1
    if len(players) < 3:
        return

    gaps = [players[i]["vor"] - players[i + 1]["vor"] for i in range(len(players) - 1)]
    threshold = statistics.fmean(gaps) + statistics.pstdev(gaps)
    if threshold <= 0:
        return

    tier = 1
    for i, gap in enumerate(gaps):
        if gap > threshold:
            tier += 1
        players[i + 1]["tier"] = tier


def fetch_pool(league, week, depth):
    """Pull undrafted players per position, best projection first."""
    pool, skipped = {}, 0
    for pos in POSITIONS:
        rows = []
        for p in league.free_agents(week=week, size=depth.get(pos, 50), position=pos):
            proj = round(getattr(p, "projected_total_points", 0) or 0, 1)
            if proj <= 0:  # unprojected: practice squad, retired, duplicates
                skipped += 1
                continue
            rows.append({
                "name": p.name,
                "pos": pos,
                "team": p.proTeam,
                "proj": proj,
                "owned": getattr(p, "percent_owned", -1),
                "injury": (getattr(p, "injuryStatus", "") or "").replace("ACTIVE", ""),
                "player_id": p.playerId,
            })
        rows.sort(key=lambda r: -r["proj"])
        pool[pos] = rows
    return pool, skipped


def build(league, week, depth):
    settings = league.settings
    team_count = len(league.teams)
    pool, skipped = fetch_pool(league, week, depth)

    base, flex = parse_slots(getattr(settings, "position_slot_counts", {}), team_count)
    startable = allocate_flex(pool, base, flex)
    levels = replacement_levels(pool, startable)

    for pos, players in pool.items():
        baseline = levels.get(pos, 0)
        for player in players:
            player["vor"] = round(player["proj"] - baseline, 1)
        assign_tiers(players)

    board = sorted(
        (p for players in pool.values() for p in players),
        key=lambda p: -p["vor"],
    )
    for i, player in enumerate(board, 1):
        player["rank"] = i

    return {
        "league": settings.name,
        "season": league.year,
        "team_count": team_count,
        "picks_recorded": len(getattr(league, "draft", []) or []),
        "startable": startable,
        "replacement": {k: round(v, 1) for k, v in levels.items()},
        "unprojected_skipped": skipped,
        "board": board,
        "by_position": pool,
    }


def format_row(p):
    flag = f" {p['injury'][:3]}" if p["injury"] else ""
    return (f"{p['rank']:>4}  {p['name'][:22]:<22} {p['pos']:<5} {p['team']:<4} "
            f"{p['proj']:>7.1f} {p['vor']:>+7.1f}   T{p['tier']:<3} "
            f"{p['owned']:>5.1f}%{flag}")


HEADER = (f"{'#':>4}  {'PLAYER':<22} {'POS':<5} {'TM':<4} {'PROJ':>7} {'VOR':>7}   "
          f"{'TIER':<4} {'OWN':>6}")


def report(data, top, only_pos):
    print("=" * 78)
    print(f"DRAFT BOARD — {data['league']} ({data['season']}), "
          f"{data['team_count']} teams")
    print("=" * 78)
    if data["picks_recorded"]:
        print(f"NOTE: {data['picks_recorded']} picks already recorded — this is a "
              f"best-available board, not a pre-draft one.\n")

    print("Replacement level (startable league-wide → baseline projection):")
    for pos in POSITIONS:
        if pos in data["replacement"]:
            print(f"  {pos:<5} {data['startable'].get(pos, 0):>3} startable "
                  f"→ {data['replacement'][pos]:>6.1f} pts")

    if not only_pos:
        print(f"\n--- OVERALL, TOP {top} BY VOR ---")
        print(HEADER)
        for p in data["board"][:top]:
            print(format_row(p))

    for pos in ([only_pos] if only_pos else POSITIONS):
        players = data["by_position"].get(pos, [])
        if not players:
            continue
        limit = top if only_pos else max(12, data["startable"].get(pos, 0) + 6)
        print(f"\n--- {pos} TIERS ---")
        print(HEADER)
        last_tier = None
        for p in players[:limit]:
            if last_tier is not None and p["tier"] != last_tier:
                print(f"{'':>4}  {'-' * 60}")
            print(format_row(p))
            last_tier = p["tier"]


def write_cheatsheet(data, path, top):
    lines = [
        f"# Draft cheat sheet — {data['league']} ({data['season']})",
        "",
        f"{data['team_count']} teams. VOR = projected points minus the best "
        "projection still available at that position once every team's starting "
        "slots are filled.",
        "",
        "| Pos | Startable | Replacement |",
        "|---|---|---|",
    ]
    for pos in POSITIONS:
        if pos in data["replacement"]:
            lines.append(f"| {pos} | {data['startable'].get(pos, 0)} | "
                         f"{data['replacement'][pos]:.1f} |")

    lines += ["", f"## Overall top {top}", "",
              "| # | Player | Pos | Tm | Proj | VOR | Tier |", "|---|---|---|---|---|---|---|"]
    for p in data["board"][:top]:
        lines.append(f"| {p['rank']} | {p['name']} | {p['pos']} | {p['team']} | "
                     f"{p['proj']:.1f} | {p['vor']:+.1f} | T{p['tier']} |")

    for pos in POSITIONS:
        players = data["by_position"].get(pos, [])
        if not players:
            continue
        lines += ["", f"## {pos}", ""]
        limit = max(12, data["startable"].get(pos, 0) + 6)
        tier = None
        for p in players[:limit]:
            if p["tier"] != tier:
                tier = p["tier"]
                lines.append(f"\n**Tier {tier}**")
            lines.append(f"- {p['name']} ({p['team']}) — {p['proj']:.1f} proj, "
                         f"{p['vor']:+.1f} VOR")

    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pos", choices=POSITIONS, help="show only this position")
    ap.add_argument("--top", type=int, default=60, help="rows in the overall board")
    ap.add_argument("--week", type=int, help="scoring period (default: week 1)")
    ap.add_argument("--season", type=int,
                    help="season to build for (default: SEASON from .env). A "
                         "completed season usually wants --week 1 too, since "
                         "its current week has no projections left")
    ap.add_argument("--depth", type=int,
                    help="override players pulled per position")
    args = ap.parse_args()

    league = connect(year=args.season)
    week = args.week or max(1, getattr(league, "current_week", 1) or 1)
    depth = ({pos: args.depth for pos in POSITIONS} if args.depth else POOL_DEPTH)

    data = build(league, week, depth)
    if not data["board"]:
        sys.exit(f"No projected players found for {data['season']} week {week}. "
                 f"({data['unprojected_skipped']} unprojected players were "
                 f"skipped.) A completed season carries no projections in its "
                 f"later weeks — try --week 1.")
    report(data, args.top, args.pos)

    os.makedirs("out", exist_ok=True)
    with open("out/draft_board.json", "w") as fh:
        json.dump(data, fh, indent=2)
    write_cheatsheet(data, "out/cheatsheet.md", args.top)
    if data["unprojected_skipped"]:
        print(f"\n({data['unprojected_skipped']} unprojected players skipped.)")
    print("\nWrote out/draft_board.json and out/cheatsheet.md")


if __name__ == "__main__":
    main()
