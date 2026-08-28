"""Make roster moves: set the lineup, add, drop, claim, and propose trades.

Run:  .venv/bin/python manage.py pending
      .venv/bin/python manage.py lineup
      .venv/bin/python manage.py add "Player Name" --drop "Other Guy"
      .venv/bin/python manage.py drop "Player Name"
      .venv/bin/python manage.py claim "Player Name" --drop "Other Guy"
      .venv/bin/python manage.py trade --with 4 --send "Mine" --receive "Theirs"
      .venv/bin/python manage.py cancel-trade 1234567890

This is the only tool here that changes anything. Every command previews the
exact body it would send and stops; sending it takes a second run carrying the
confirm token from the preview, and `ESPN_WRITES=1` in `.env`. See espnwrite.py
for why it is built that way.

Names are resolved against the live league and must be unambiguous. Where two
players could be meant, the command lists them and refuses rather than picking
one — a wrong guess here drops the wrong player off a real roster.
"""
import argparse
import sys

import espnwrite
import schedule
import startsit
import transactions
import weekly
from common import connect, find_my_team, load_env
from draftroom import normalise  # pure name-matching, no browser involved
from lineup import POOL_DEPTH, POSITIONS, as_row, assign_lineup, starting_slots


def resolve(name, rows, what):
    """The one row matching `name`, or exit explaining why not.

    Exact normalised match first, then a unique substring match. Ambiguity is
    never resolved by picking the first: this decides which real player a write
    touches.
    """
    wanted = normalise(name)
    if not wanted:
        sys.exit(f"Give a player name to {what}.")

    exact = [r for r in rows if normalise(r["name"]) == wanted]
    hits = exact or [r for r in rows if wanted in normalise(r["name"])]

    if not hits:
        sys.exit(f"No {what} candidate matching {name!r}.")
    if len(hits) > 1:
        listed = "\n  ".join(f"{r['name']} ({r['pos']}, {r['team']})" for r in hits[:12])
        sys.exit(f"{name!r} matches more than one player. Be more specific:\n"
                 f"  {listed}")
    return hits[0]


def roster_rows(team):
    """Your roster as plain rows. Season projections: a drop needs identity."""
    return [as_row(p, None) for p in team.roster]


def free_agent_rows(league, week, only_pos=None):
    """The available pool, the same population waivers.py ranks."""
    rows = []
    for pos in ([only_pos] if only_pos else POSITIONS):
        for player in league.free_agents(week=week, size=POOL_DEPTH.get(pos, 50),
                                         position=pos):
            rows.append(as_row(player, [week]))
    return rows


def scoring_period(league, week=None):
    """The scoring period a transaction applies to.

    Falls forward to week 1 rather than sending 0: pre-season ESPN reports
    scoringPeriodId 0, and a transaction stamped for period 0 is not a
    transaction for the first week, it is one for no week at all.
    """
    if week:
        return int(week)
    period = int(getattr(league, "scoringPeriodId", 0) or 0)
    return period or int(getattr(league, "current_week", 0) or 0) or 1


# --- commands: each returns (payload, summary lines) ------------------------

def cmd_lineup(league, team, env, args):
    week = args.week or startsit.current_week(league)
    rows, error = weekly.week_lineup(league, team, week)
    if error:
        sys.exit(f"Cannot read week {week}: {error}.")
    if not any(r["proj"] for r in rows):
        sys.exit(f"ESPN has not published week {week} projections yet, so every "
                 f"lineup ties at 0.0. Nothing to set.")

    byes = schedule.fetch_bye_weeks(
        league, int(getattr(league.settings, "reg_season_count", 0) or 14))
    startsit.annotate(rows, byes, week)

    slots = starting_slots(league.settings)
    assignment = assign_lineup(rows, slots)
    moves = transactions.lineup_moves(rows, assignment)
    if not moves:
        sys.exit(f"Week {week} lineup is already optimal. Nothing to change.")

    by_id = {r["player_id"]: r for r in rows}
    lines = [f"SET LINEUP — week {week}", ""]
    for player_id, from_slot, to_slot in moves:
        row = by_id.get(player_id, {})
        note = f"  [{row.get('note')}]" if row.get("note") else ""
        lines.append(f"  {row.get('name', player_id)[:24]:<24} "
                     f"{from_slot:>9} -> {to_slot:<9} "
                     f"{row.get('proj', 0):>6.1f}{note}")

    now = round(sum(r["proj"] for r in weekly.started(rows)), 1)
    best = round(sum(r["proj"] for _, r in assignment if r), 1)
    lines += ["", f"  projected {now:.1f} -> {best:.1f}  ({best - now:+.1f})"]

    payload = transactions.set_lineup(
        team.team_id, env.get("SWID"), scoring_period(league, week), moves)
    return payload, lines


def cmd_add(league, team, env, args):
    week = args.week or startsit.current_week(league)
    pool = free_agent_rows(league, week, args.pos)
    add = resolve(args.player, pool, "add")
    drop = resolve(args.drop, roster_rows(team), "drop") if args.drop else None

    lines = [f"ADD    {add['name']} ({add['pos']}, {add['team']}) "
             f"— immediate, processed on submission"]
    if drop:
        lines.append(f"DROP   {drop['name']} ({drop['pos']}, {drop['team']})")
    else:
        lines.append("DROP   nobody — this needs an open roster spot")

    payload = transactions.add_drop(
        team.team_id, env.get("SWID"), scoring_period(league, args.week),
        add_id=add["player_id"],
        drop_id=drop["player_id"] if drop else None)
    return payload, lines


def cmd_drop(league, team, env, args):
    drop = resolve(args.player, roster_rows(team), "drop")
    lines = [f"DROP   {drop['name']} ({drop['pos']}, {drop['team']})",
             "       This is immediate and cannot be undone except by "
             "re-adding him."]
    payload = transactions.add_drop(
        team.team_id, env.get("SWID"), scoring_period(league, args.week),
        drop_id=drop["player_id"])
    return payload, lines


def cmd_claim(league, team, env, args):
    # Settled before the pool is fetched: a bid that cannot be sent should fail
    # immediately, not after six requests for a player list it will not use.
    uses_faab = bool(getattr(league.settings, "faab", False))
    if args.bid is not None and not uses_faab:
        sys.exit("This league does not use FAAB, so a bid cannot be sent. "
                 "Drop --bid; claims here go in on waiver priority.")
    if uses_faab and args.bid is None:
        sys.exit("This league uses FAAB. Pass --bid with an amount.")

    week = args.week or startsit.current_week(league)
    pool = free_agent_rows(league, week, args.pos)
    add = resolve(args.player, pool, "claim")
    drop = resolve(args.drop, roster_rows(team), "drop") if args.drop else None

    lines = [f"CLAIM  {add['name']} ({add['pos']}, {add['team']})",
             "       Lands PENDING and processes at the next waiver run."]
    if drop:
        lines.append(f"DROP   {drop['name']} ({drop['pos']}) — only if the "
                     f"claim succeeds")
    lines.append(f"       {'bid ' + str(args.bid) if uses_faab else 'waiver priority #' + str(getattr(team, 'waiver_rank', '?'))}")

    payload = transactions.waiver_claim(
        team.team_id, env.get("SWID"), scoring_period(league, args.week),
        add_id=add["player_id"],
        drop_id=drop["player_id"] if drop else None,
        bid=args.bid if uses_faab else None)
    return payload, lines


def cmd_trade(league, team, env, args):
    other = next((t for t in league.teams if t.team_id == args.with_team), None)
    if other is None:
        listed = "\n  ".join(f"{t.team_id}  {t.team_name}" for t in league.teams)
        sys.exit(f"No team with id {args.with_team}. Teams:\n  {listed}")
    if other.team_id == team.team_id:
        sys.exit("You cannot trade with yourself.")

    mine = roster_rows(team)
    theirs = [as_row(p, None) for p in other.roster]
    send = [resolve(n, mine, "send") for n in (args.send or [])]
    receive = [resolve(n, theirs, "receive") for n in (args.receive or [])]
    if not send and not receive:
        sys.exit("A trade needs at least one --send or --receive.")

    lines = [f"PROPOSE TRADE with {other.team_name} (team {other.team_id})", ""]
    for row in send:
        lines.append(f"  you send     {row['name']} ({row['pos']}, {row['team']})")
    for row in receive:
        lines.append(f"  you receive  {row['name']} ({row['pos']}, {row['team']})")
    lines += ["", "  Lands PENDING. They accept or reject it in ESPN — this "
                  "tool cannot", "  accept or reject on anyone's behalf."]

    payload = transactions.propose_trade(
        team.team_id, env.get("SWID"), scoring_period(league, args.week),
        other.team_id,
        [r["player_id"] for r in send],
        [r["player_id"] for r in receive],
        comment=args.comment or "")
    return payload, lines


def cmd_cancel_trade(league, team, env, args):
    lines = [f"CANCEL TRADE PROPOSAL {args.transaction_id}",
             "       Withdraws a proposal you created. Does not reject one "
             "sent to you."]
    payload = transactions.cancel_trade(
        team.team_id, env.get("SWID"), scoring_period(league, args.week),
        args.transaction_id)
    return payload, lines


def cmd_pending(league, team, env, args):
    """Read-only: what is queued, with the ids cancel-trade needs."""
    period = scoring_period(league, args.week)
    raw = transactions.fetch_transactions(league, period)
    rows = transactions.parse_transactions(raw)
    mine = [r for r in rows if r["team_id"] == team.team_id]

    print(f"Pending and recent transactions for {team.team_name}, "
          f"scoring period {period}")
    if not mine:
        print("  nothing")
        return None, None

    # league.player_map is keyed both ways, so it names players who are no
    # longer on the roster -- which is most of the interesting ones here, since
    # a transaction is usually about somebody arriving or leaving.
    names = getattr(league, "player_map", None) or {}
    for row in mine:
        items = ", ".join(
            f"{i['type']} {names.get(i['player_id'], i['player_id'])}"
            for i in row["items"])
        print(f"  [{row['status']:<9}] {row['type']:<15} {items}")
        if row["id"]:
            print(f"      id {row['id']}")
    return None, None


COMMANDS = {
    "lineup": cmd_lineup,
    "add": cmd_add,
    "drop": cmd_drop,
    "claim": cmd_claim,
    "trade": cmd_trade,
    "cancel-trade": cmd_cancel_trade,
    "pending": cmd_pending,
}


def _shared(target, suppress):
    """Declare the options that apply to every command.

    They go on the top-level parser *and* on each subparser, so that
    `manage.py --week 5 drop X` and `manage.py drop X --week 5` both work. That
    matters more than it looks: the preview prints "re-run the same command
    with --confirm <token>", and a user appending that to the end of what they
    just typed puts it after the subcommand. If only the top-level parser knew
    the flag, the instruction the tool prints would be an argparse error.

    The subparser copies default to SUPPRESS so that not passing them leaves
    whatever the top-level parser already set, rather than overwriting it with
    None.
    """
    default = argparse.SUPPRESS if suppress else None
    target.add_argument("--season", type=int, default=default,
                        help="season (default: SEASON from .env)")
    target.add_argument("--team-id", type=int, default=default,
                        help="your team id, if SWID lookup fails")
    target.add_argument("--week", type=int, default=default,
                        help="scoring period (default: current)")
    target.add_argument("--confirm", default=default,
                        help="the token printed by the preview")


def parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    _shared(ap, suppress=False)

    common = argparse.ArgumentParser(add_help=False)
    _shared(common, suppress=True)

    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("pending", parents=[common],
                   help="show queued claims and trade proposals")
    sub.add_parser("lineup", parents=[common],
                   help="set the best legal lineup for a week")

    add = sub.add_parser("add", parents=[common],
                         help="add a free agent immediately")
    add.add_argument("player")
    add.add_argument("--drop", help="player to drop to make room")
    add.add_argument("--pos", choices=POSITIONS, help="narrow the search")

    drop = sub.add_parser("drop", parents=[common], help="drop a player")
    drop.add_argument("player")

    claim = sub.add_parser("claim", parents=[common],
                           help="submit a waiver claim")
    claim.add_argument("player")
    claim.add_argument("--drop", help="player to drop if the claim succeeds")
    claim.add_argument("--bid", type=int, help="FAAB bid, in leagues that use it")
    claim.add_argument("--pos", choices=POSITIONS, help="narrow the search")

    trade = sub.add_parser("trade", parents=[common], help="propose a trade")
    trade.add_argument("--with", dest="with_team", type=int, required=True,
                       help="the other team's id")
    trade.add_argument("--send", action="append", help="repeatable")
    trade.add_argument("--receive", action="append", help="repeatable")
    trade.add_argument("--comment", help="note for the other manager")

    cancel = sub.add_parser("cancel-trade", parents=[common],
                            help="withdraw your proposal")
    cancel.add_argument("transaction_id")

    return ap


def main():
    args = parser().parse_args()
    env = load_env()
    league = connect(env, year=args.season)
    team = find_my_team(league, swid=env.get("SWID"), team_id=args.team_id)

    payload, lines = COMMANDS[args.command](league, team, env, args)
    if payload is None:
        return

    text, token = espnwrite.preview(payload, lines)

    if not args.confirm:
        print(text)
        if not espnwrite.writes_enabled(env):
            print("\nNote: ESPN_WRITES is not set, so confirming would still "
                  "refuse.\nAdd ESPN_WRITES=1 to .env when you want to send "
                  "moves for real.")
        return

    try:
        result = espnwrite.execute(league, env, payload, args.confirm)
    except (espnwrite.WritesDisabled, espnwrite.NotConfirmed,
            espnwrite.WriteRejected) as exc:
        sys.exit(str(exc))

    print("\n".join(lines))
    print(f"\nSENT. ESPN accepted the transaction (token {token}).")
    if isinstance(result, dict) and result.get("id"):
        print(f"Transaction id: {result['id']}")


if __name__ == "__main__":
    main()
