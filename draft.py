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
import time

import picks as picks_mod
from common import connect, load_env
from lineup import (NON_STARTING_SLOTS, POOL_DEPTH, POSITIONS, as_row,
                    best_lineup, gain_from_adding, starting_slots)


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


def fetch_pool(league, week, depth, taken=()):
    """Pull undrafted players per position, best projection first.

    `taken` is the set of player ids already drafted, subtracted here as a
    safety net over free_agents(). Whether ESPN drops a player from the
    free-agent view the instant he is picked is not something this code can
    rely on, and recommending a player who went two picks ago is the single
    worst thing a live board can do.
    """
    pool, skipped, stale = {}, 0, 0
    for pos in POSITIONS:
        rows = []
        for p in league.free_agents(week=week, size=depth.get(pos, 50), position=pos):
            if getattr(p, "playerId", None) in taken:
                stale += 1  # still listed as free, but already off the board
                continue
            row = as_row(p, None)
            row["pos"] = pos  # trust the query, not the player's own label
            if row["proj"] <= 0:  # unprojected: practice squad, retired, dupes
                skipped += 1
                continue
            rows.append(row)
        rows.sort(key=lambda r: -r["proj"])
        pool[pos] = rows
    return pool, skipped, stale


def my_roster_rows(league, player_ids):
    """Your drafted players, so the board can also report lineup gain.

    player_info takes a list of ids, so this stays one request however many
    rounds have gone by. It is best-effort: a board without your roster is
    still a useful board, and draft night is the wrong time to crash.
    """
    if not player_ids:
        return []
    try:
        got = league.player_info(playerId=list(player_ids))
    except Exception:
        return []
    if got is None:
        return []
    if not isinstance(got, list):
        got = [got]
    return [as_row(p, None) for p in got]


def build(league, week, depth, my_team_id=None, by_need=False):
    settings = league.settings
    team_count = len(league.teams)

    drafted, draft_complete = picks_mod.fetch_picks(league)
    taken = picks_mod.taken_ids(drafted)
    pool, skipped, stale = fetch_pool(league, week, depth, taken)

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

    # Raw lineup gain is the wrong draft metric on its own: with slots still
    # empty it equals the raw projection, so it ranks quarterbacks first —
    # precisely the cross-position comparison VOR exists to fix. What matters
    # is gain *over replacement*: how much better this player is than whoever
    # you would end up putting in that slot anyway.
    #
    # That degrades to VOR when your roster is empty (both terms measure
    # against nothing) and becomes roster-aware as slots fill: once a starting
    # QB is locked in, a backup QB gains nothing over a replacement-level QB,
    # and need correctly falls to zero while VOR still says he is valuable.
    my_rows = my_roster_rows(league, picks_mod.roster_ids(drafted, my_team_id))
    slots = starting_slots(settings)
    if my_rows and slots:
        base_total, _ = best_lineup(my_rows, slots)
        replacement_gain = {}
        for pos, players in pool.items():
            if players:
                idx = min(startable.get(pos, 0), len(players) - 1)
                replacement_gain[pos] = gain_from_adding(
                    my_rows, players[idx], slots, base_total)
        for player in board:
            player["gain"] = gain_from_adding(my_rows, player, slots, base_total)
            player["need"] = round(
                player["gain"] - replacement_gain.get(player["pos"], 0), 1)
        if by_need:
            board.sort(key=lambda p: (-p["need"], -p["vor"]))
    else:
        base_total = None

    for i, player in enumerate(board, 1):
        player["rank"] = i

    return {
        "league": settings.name,
        "season": league.year,
        "team_count": team_count,
        "picks_seen": len(drafted),
        "picks": drafted,
        "draft_complete": draft_complete,
        "taken_but_still_listed": stale,
        "my_team_id": my_team_id,
        "my_roster": my_rows,
        "my_lineup_total": base_total,
        "sorted_by": "need" if (by_need and my_rows) else "vor",
        "startable": startable,
        "replacement": {k: round(v, 1) for k, v in levels.items()},
        "unprojected_skipped": skipped,
        "board": board,
        "by_position": pool,
    }


def format_row(p, show_need=False):
    flag = f" {p['injury'][:3]}" if p["injury"] else ""
    need = f" {p.get('need', 0.0):>+7.1f}" if show_need else ""
    return (f"{p['rank']:>4}  {p['name'][:22]:<22} {p['pos']:<5} {p['team']:<4} "
            f"{p['proj']:>7.1f} {p['vor']:>+7.1f}{need}   T{p['tier']:<3} "
            f"{p['owned']:>5.1f}%{flag}")


def header(show_need=False):
    need = f" {'NEED':>7}" if show_need else ""
    return (f"{'#':>4}  {'PLAYER':<22} {'POS':<5} {'TM':<4} {'PROJ':>7} "
            f"{'VOR':>7}{need}   {'TIER':<4} {'OWN':>6}")


def roster_summary(rows):
    """Counts by position, e.g. 'RB 3 · WR 2 · QB 1' — what you have so far."""
    counts = {}
    for r in rows:
        counts[r["pos"]] = counts.get(r["pos"], 0) + 1
    return "  ·  ".join(f"{pos} {n}" for pos, n in
                        sorted(counts.items(), key=lambda kv: -kv[1]))


def report(data, top, only_pos):
    show_need = bool(data["my_roster"])
    print("=" * 86)
    print(f"DRAFT BOARD — {data['league']} ({data['season']}), "
          f"{data['team_count']} teams")
    print("=" * 86)

    if data["picks_seen"]:
        state = "complete" if data["draft_complete"] else "in progress"
        print(f"{data['picks_seen']} picks off the board (draft {state}) — this is "
              f"a best-available board.")
    elif data["draft_complete"]:
        print("Draft is complete but no picks came back — pool may be stale.")
    else:
        print("No picks yet — pre-draft board.")

    if data["taken_but_still_listed"]:
        print(f"{data['taken_but_still_listed']} drafted players were still "
              f"listed as free agents and have been removed.")

    if data["my_roster"]:
        print(f"\nYour roster ({len(data['my_roster'])}): "
              f"{roster_summary(data['my_roster'])}")
        print(f"Best lineup so far: {data['my_lineup_total']:.1f} projected pts")
    elif data["my_team_id"] is None:
        print("Team not identified, so NEED is not shown. Pass --team-id to "
              "rank by what your roster still needs.")

    print("\nReplacement level (startable league-wide → baseline projection):")
    for pos in POSITIONS:
        if pos in data["replacement"]:
            print(f"  {pos:<5} {data['startable'].get(pos, 0):>3} startable "
                  f"→ {data['replacement'][pos]:>6.1f} pts")

    if not only_pos:
        by = "NEED" if data["sorted_by"] == "need" else "VOR"
        print(f"\n--- OVERALL, TOP {top} BY {by} ---")
        print(header(show_need))
        for p in data["board"][:top]:
            print(format_row(p, show_need))

    for pos in ([only_pos] if only_pos else POSITIONS):
        players = data["by_position"].get(pos, [])
        if not players:
            continue
        limit = top if only_pos else max(12, data["startable"].get(pos, 0) + 6)
        print(f"\n--- {pos} TIERS ---")
        print(header(show_need))
        last_tier = None
        for p in players[:limit]:
            if last_tier is not None and p["tier"] != last_tier:
                print(f"{'':>4}  {'-' * 60}")
            print(format_row(p, show_need))
            last_tier = p["tier"]

    if show_need:
        print("\nNEED = value over the player you would otherwise end up with "
              "in that slot,\ngiven what you have already drafted. It equals "
              "VOR while your roster is empty\nand diverges as slots fill — a "
              "backup QB is worth VOR to the league and 0 to you.\n"
              "Sort by it with --need.")


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


def save(data, top):
    os.makedirs("out", exist_ok=True)
    with open("out/draft_board.json", "w") as fh:
        json.dump(data, fh, indent=2)
    write_cheatsheet(data, "out/cheatsheet.md", top)


def watch(league, week, depth, my_team_id, args):
    """Re-draw the board on a timer until Ctrl-C.

    Typing a command over and over while a pick clock runs is the wrong
    ergonomics — the board should already be current when you look at it. Each
    pass reports how long the refresh took, which is the number that decides
    whether this is usable on a 60-second timer at all.
    """
    names, previous = {}, None
    try:
        while True:
            started = time.time()
            data = build(league, week, depth, my_team_id, args.need)
            elapsed = time.time() - started

            current = {p["player_id"] for p in data["picks"]}
            fresh = [names.get(i) for i in current - (previous or set())]
            fresh = [n for n in fresh if n]
            # Cache names while players are still in the pool, so that when one
            # disappears next refresh we can still say who went.
            for p in data["board"]:
                names[p["player_id"]] = p["name"]
            previous = current

            print("\033[H\033[J", end="")  # home cursor, clear screen
            report(data, args.top, args.pos)
            if fresh:
                print(f"\nTaken since last refresh: {', '.join(sorted(fresh))}")
            save(data, args.top)
            print(f"\nRefreshed in {elapsed:.1f}s at "
                  f"{time.strftime('%H:%M:%S')} — next in {args.watch}s. "
                  f"Ctrl-C to stop.")
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pos", choices=POSITIONS, help="show only this position")
    ap.add_argument("--top", type=int, default=60, help="rows in the overall board")
    ap.add_argument("--team-id", type=int,
                    help="your team id; enables the GAIN column. Found from "
                         "your SWID automatically when possible")
    ap.add_argument("--need", action="store_true",
                    help="sort by lineup gain rather than VOR — what your "
                         "roster still needs, for the later rounds")
    ap.add_argument("--watch", type=int, nargs="?", const=15, metavar="SECS",
                    help="redraw every SECS seconds (default 15) until Ctrl-C")
    ap.add_argument("--week", type=int, help="scoring period (default: week 1)")
    ap.add_argument("--season", type=int,
                    help="season to build for (default: SEASON from .env). A "
                         "completed season usually wants --week 1 too, since "
                         "its current week has no projections left")
    ap.add_argument("--depth", type=int,
                    help="override players pulled per position")
    args = ap.parse_args()

    env = load_env()
    league = connect(env, year=args.season)
    week = args.week or max(1, getattr(league, "current_week", 1) or 1)
    depth = ({pos: args.depth for pos in POSITIONS} if args.depth else POOL_DEPTH)

    team = find_my_team(league, swid=env.get("SWID"), team_id=args.team_id,
                        strict=False)
    my_team_id = team.team_id if team else None

    if args.watch:
        watch(league, week, depth, my_team_id, args)
        return

    data = build(league, week, depth, my_team_id, args.need)
    if not data["board"]:
        sys.exit(f"No projected players found for {data['season']} week {week}. "
                 f"({data['unprojected_skipped']} unprojected players were "
                 f"skipped.) A completed season carries no projections in its "
                 f"later weeks — try --week 1.")
    report(data, args.top, args.pos)

    save(data, args.top)
    if data["unprojected_skipped"]:
        print(f"\n({data['unprojected_skipped']} unprojected players skipped.)")
    print("\nWrote out/draft_board.json and out/cheatsheet.md")


if __name__ == "__main__":
    main()
