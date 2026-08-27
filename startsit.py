"""Start/sit: the best legal lineup you can set this week, and what to change.

Run:  .venv/bin/python startsit.py              # the current week
      .venv/bin/python startsit.py --week 5
      .venv/bin/python startsit.py --season 2025 --week 10   # a past week

Reads only. Nothing here moves a player — you take the moves off this list and
make them in ESPN yourself.

The optimiser is the same one the draft board and the waiver wire rank on, so
"best lineup" means the same thing in all three places. What is new here is the
comparison: your best legal lineup against the lineup you have actually set,
which is the only number that tells you whether to do anything at all.

Two things this checks that a projection column cannot. A player on bye
projects 0.0, and so does a player ESPN has simply not projected yet — those
are opposite situations and the report names which one it is, because the
first is settled and the second means come back later. And a starter listed
OUT is called out separately even when his projection still looks healthy:
ESPN is slow to zero those, and an unnoticed OUT in your flex is the single
cheapest way to lose a week.
"""
import argparse
import json
import os

import schedule
import weekly
from common import connect, find_my_team, load_env
from lineup import NON_STARTING_SLOTS, assign_lineup, starting_slots

# Conventional reading order for a lineup. Anything a league defines that is
# not listed falls to the end rather than being dropped, so an IDP or superflex
# league still prints every slot it actually uses.
SLOT_ORDER = ["QB", "RB", "RB/WR", "WR", "WR/TE", "TE", "RB/WR/TE", "OP",
              "D/ST", "K"]

# Statuses worth interrupting for. QUESTIONABLE is deliberately not here: it is
# common enough that flagging it would train you to ignore the flag.
ALARMING = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION"}


def slot_sort_key(slot):
    return (SLOT_ORDER.index(slot) if slot in SLOT_ORDER else len(SLOT_ORDER),
            slot)


def current_week(league):
    """The week to report on when none is given.

    Clamped into the regular season at both ends: `current_week` is 0 before
    the season opens, and runs past the regular season into the playoffs,
    where a start/sit report is still wanted but the default should not
    silently walk off the end of the schedule.
    """
    week = int(getattr(league, "current_week", 0) or 0)
    last = int(getattr(league.settings, "reg_season_count", 0) or 14)
    return max(1, min(week, last)) if week else 1


def annotate(rows, byes, week):
    """Tag each row with why it projects what it projects.

    `note` is the short reason a player is worth a second look, and it is
    ordered by how much it should change your decision: being on bye is
    certain, being ruled out is nearly certain, and an unprojected player is
    not a fact about the player at all.
    """
    for row in rows:
        note = ""
        status = (row.get("injury") or "").upper()
        if byes.get(row.get("team")) == week:
            note = "BYE"
        elif status in ALARMING:
            note = status[:3]
        elif not row["proj"]:
            note = "no proj"
        elif status:
            note = status[:3].title()
        row["note"] = note
    return rows


def moves(rows, slots):
    """What to change, as (optimal, bench, sit, start, totals).

    `sit` and `start` are reported as two lists rather than as paired swaps on
    purpose. Fixing a lineup is rarely one-for-one — promoting a receiver into
    the flex can push the flex incumbent to the bench and pull a running back
    into a starting slot — so pairing them up would invent a correspondence
    that does not exist. The honest summary is who comes out, who goes in, and
    what the whole move is worth together.
    """
    optimal = assign_lineup(rows, slots)
    optimal_ids = {id(row) for _, row in optimal if row is not None}

    set_now = weekly.started(rows)
    now_total = round(sum(row["proj"] for row in set_now), 1)
    best_total = round(sum(row["proj"] for _, row in optimal if row), 1)

    sit = sorted((r for r in set_now if id(r) not in optimal_ids),
                 key=lambda r: -r["proj"])
    start = sorted((r for _, r in optimal
                    if r is not None and r.get("slot_now") in NON_STARTING_SLOTS),
                   key=lambda r: -r["proj"])

    bench = sorted((r for r in rows if id(r) not in optimal_ids),
                   key=lambda r: -r["proj"])
    return optimal, bench, sit, start, now_total, best_total


def build(league, team, week):
    rows, error = weekly.week_lineup(league, team, week)
    slots = starting_slots(league.settings)
    byes = schedule.fetch_bye_weeks(
        league, int(getattr(league.settings, "reg_season_count", 0) or 14))

    data = {
        "league": league.settings.name,
        "season": league.year,
        "team": team.team_name,
        "week": week,
        "slots": slots,
        "error": error,
        "lineup": [], "bench": [], "sit": [], "start": [],
        "set_total": 0.0, "best_total": 0.0, "gain": 0.0,
    }
    if error:
        return data

    annotate(rows, byes, week)
    optimal, bench, sit, start, now_total, best_total = moves(rows, slots)

    data["lineup"] = [{"slot": slot, "player": row}
                      for slot, row in sorted(optimal,
                                              key=lambda pair: slot_sort_key(pair[0]))]
    data["bench"] = bench
    data["sit"] = sit
    data["start"] = start
    data["set_total"] = now_total
    data["best_total"] = best_total
    data["gain"] = round(best_total - now_total, 1)
    data["projected"] = sum(1 for row in rows if row["proj"])
    data["rostered"] = len(rows)
    return data


HEADER = f"{'SLOT':<9} {'PLAYER':<22} {'POS':<5} {'TM':<4} {'PROJ':>7}  NOTE"


def format_row(slot, row, changed=False):
    if row is None:
        return f"{slot:<9} {'— nobody eligible —':<22} {'':<5} {'':<4} {0.0:>7.1f}  EMPTY"
    mark = " *" if changed else ""
    return (f"{slot:<9} {row['name'][:22]:<22} {row['pos']:<5} {row['team']:<4} "
            f"{row['proj']:>7.1f}  {row['note']}{mark}")


def report(data):
    print("=" * 78)
    print(f"START / SIT — {data['team']} — {data['league']} "
          f"({data['season']}) — week {data['week']}")
    print("=" * 78)

    if data["error"]:
        print(f"Cannot read week {data['week']}: {data['error']}.")
        return

    if not data.get("projected"):
        print(f"ESPN has not published week {data['week']} projections yet "
              f"({data['rostered']} players rostered, none projected).")
        print("Every lineup ties at 0.0, so there is nothing to recommend. "
              "Come back closer to kickoff.")
        return

    started_ids = {id(r["player"]) for r in data["lineup"] if r["player"]}
    changed = {id(r) for r in data["start"]}

    print(f"\n--- BEST LINEUP FOR WEEK {data['week']} ---")
    print(HEADER)
    for entry in data["lineup"]:
        print(format_row(entry["slot"], entry["player"],
                         entry["player"] is not None
                         and id(entry["player"]) in changed))

    print(f"\nLineup you have set:  {data['set_total']:>7.1f}")
    print(f"Best legal lineup:    {data['best_total']:>7.1f}")

    if not data["sit"] and not data["start"]:
        print("\nNo changes: your lineup is already the best one available.")
    else:
        print(f"\n--- MOVES (worth {data['gain']:+.1f} pts) ---")
        for row in data["start"]:
            note = f"  [{row['note']}]" if row["note"] else ""
            print(f"  START  {row['name'][:24]:<24} {row['pos']:<5} "
                  f"{row['proj']:>6.1f}{note}")
        for row in data["sit"]:
            note = f"  [{row['note']}]" if row["note"] else ""
            print(f"  SIT    {row['name'][:24]:<24} {row['pos']:<5} "
                  f"{row['proj']:>6.1f}  (in {row['slot_now']}){note}")

    alarms = [r for r in data["lineup"]
              if r["player"] and r["player"]["note"] in
              {s[:3] for s in ALARMING} | {"BYE"}]
    if alarms:
        print("\n! Starting anyway, and worth checking by hand:")
        for entry in alarms:
            row = entry["player"]
            print(f"    {row['name']} ({row['pos']}) is {row['note']} but is "
                  f"still your best {entry['slot']}.")

    if data["bench"]:
        print("\n--- BENCH ---")
        for row in data["bench"]:
            note = f"  {row['note']}" if row["note"] else ""
            print(f"  {row['name'][:24]:<24} {row['pos']:<5} {row['team']:<4} "
                  f"{row['proj']:>6.1f}{note}")

    print("\n* = moving into the lineup. Projections are ESPN's, for this week "
          "only.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--week", type=int,
                    help="week to set a lineup for (default: the current week)")
    ap.add_argument("--season", type=int,
                    help="season to read (default: SEASON from .env)")
    ap.add_argument("--team-id", type=int,
                    help="your team id, if it cannot be found from your SWID")
    args = ap.parse_args()

    env = load_env()
    league = connect(env, year=args.season)
    team = find_my_team(league, swid=env.get("SWID"), team_id=args.team_id)
    week = args.week or current_week(league)

    data = build(league, team, week)
    report(data)

    os.makedirs("out", exist_ok=True)
    with open("out/startsit.json", "w") as fh:
        json.dump(data, fh, indent=2)
    print("\nWrote out/startsit.json")


if __name__ == "__main__":
    main()
