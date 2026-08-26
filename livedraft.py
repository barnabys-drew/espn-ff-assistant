"""Live draft board, driven by the draft room's own websocket.

Run:  .venv/bin/python livedraft.py --league-id <LEAGUE_ID>

This owns the browser. ESPN allows one draft connection per member, so a second
one displaces the first -- which means the board cannot watch your draft room
from the side, it has to *be* your draft room. Draft in the window this opens.

The player pool is fetched once at startup and maintained locally from socket
picks, rather than re-polled. That is not an optimisation: during a draft the
league API still lists drafted players as free agents, so re-polling would
actively make the pool worse.
"""
import argparse
import datetime
import os
import sys
import time

import draftclock
import draftpick
import draftroom
import draftsocket
import rosterlimits
import schedule as schedule_mod
from common import connect, find_my_team, load_env
from lineup import (POOL_DEPTH, POSITIONS, as_row, best_lineup,
                    depth_factor, gain_from_adding, starting_slots)
from draft import (allocate_flex, assign_tiers, parse_slots,
                   replacement_levels)


class Board:
    """The pool, valued, with drafted players removed as picks arrive."""

    def __init__(self, league, my_team_id, bye_weight=1.0):
        self.league = league
        self.my_team_id = my_team_id
        self.slots = starting_slots(league.settings)
        self.bye_weight = bye_weight
        self.by_id = {}
        self.pool = {}
        self._load()
        weeks = int(getattr(league.settings, "reg_season_count", 0) or 17)
        self.byes = schedule_mod.fetch_bye_weeks(league, weeks)
        self.limits = rosterlimits.fetch_limits(league)
        print(f"  bye weeks for {len(self.byes)} pro teams; position caps: "
              f"{self.limits or 'none published'}", flush=True)

    def _load(self):
        print("Fetching the player pool once (the API cannot be trusted "
              "to update mid-draft)...", flush=True)
        for pos in POSITIONS:
            rows = []
            for p in self.league.free_agents(week=1, size=POOL_DEPTH.get(pos, 50),
                                             position=pos):
                row = as_row(p, None)
                row["pos"] = pos
                if row["proj"] > 0 and row["player_id"]:
                    rows.append(row)
                    self.by_id[row["player_id"]] = row
            rows.sort(key=lambda r: -r["proj"])
            self.pool[pos] = rows
        print(f"  {len(self.by_id)} projected players loaded.", flush=True)

    def name(self, player_id):
        row = self.by_id.get(player_id)
        return row["name"] if row else f"player {player_id}"

    def remove(self, player_id):
        row = self.by_id.get(player_id)
        if row:
            self.pool[row["pos"]] = [r for r in self.pool[row["pos"]]
                                     if r["player_id"] != player_id]
        return row

    def value(self, my_rows):
        """Re-rank what is left. Returns rows carrying vor and need."""
        team_count = len(self.league.teams) or 10
        base, flex = parse_slots(
            getattr(self.league.settings, "position_slot_counts", {}), team_count)
        startable = allocate_flex(self.pool, base, flex)
        levels = replacement_levels(self.pool, startable)

        board = []
        for pos, rows in self.pool.items():
            baseline = levels.get(pos, 0)
            for r in rows:
                r["vor"] = round(r["proj"] - baseline, 1)
            assign_tiers(rows)      # tiers are per position, off VOR
            board.extend(rows)
        board.sort(key=lambda r: -r["vor"])

        # A position at its cap cannot be drafted again, so recommending one
        # would be advice that cannot be taken -- and an armed picker would
        # fail in the room for a reason nothing in the log would explain.
        full = rosterlimits.full_positions(my_rows, self.limits)
        if full:
            board = [r for r in board if r["pos"] not in full]

        if my_rows and self.slots:
            base_total, _ = best_lineup(my_rows, self.slots)
            repl = {}
            for pos, rows in self.pool.items():
                if rows:
                    idx = min(startable.get(pos, 0), len(rows) - 1)
                    repl[pos] = gain_from_adding(my_rows, rows[idx],
                                                 self.slots, base_total)
            # A bye collision costs roughly one starter-week: that week the
            # slot has to be filled from the bench regardless of how good the
            # player is the other sixteen. Scaled to a single game so it breaks
            # ties rather than overriding real value -- passing on a much
            # better player to dodge a bye is the worse mistake.
            load = schedule_mod.bye_load(my_rows, self.byes)
            games = max(1, len(self.league.settings.matchup_periods or {}) or 17)
            for r in board:
                gain = gain_from_adding(my_rows, r, self.slots, base_total)
                need = gain - repl.get(r["pos"], 0)
                r["bye"] = self.byes.get(r["team"])
                r["bye_clash"] = load.get(r["bye"], 0) if r["bye"] else 0
                if r["bye_clash"]:
                    r["bye_cost"] = round(
                        (r["proj"] / games) * r["bye_clash"] * self.bye_weight, 1)
                    need -= r["bye_cost"]
                else:
                    r["bye_cost"] = 0.0
                # A player behind others at his position only plays when one
                # of them cannot. Applied after the bye cost so both reflect
                # how often he is actually in the lineup.
                r["depth"] = round(depth_factor(r, my_rows, self.slots), 2)
                r["need"] = round(need * r["depth"], 1)
            # Once a roster exists, NEED is the better ranking and the one the
            # board prints -- autopick takes rows[0], so the two must agree.
            # VOR would keep offering a fourth running back with the QB and TE
            # slots still empty.
            board.sort(key=lambda r: (-r.get("need", 0), -r["vor"]))
        return board


LOG_PATH = "out/livedraft.log"


def log(message):
    """Autopick attempts scroll away between screen redraws in a couple of
    seconds, which leaves a draft-night failure with no evidence at all."""
    os.makedirs("out", exist_ok=True)
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    with open(LOG_PATH, "a") as fh:
        fh.write(f"{stamp}  {message}\n")


def tier_pressure(rows, picks_away):
    """Per position: how many are left in the best remaining tier.

    The question a value board cannot answer on its own is whether the player
    it likes will still be there. Three left in a tier with fourteen picks
    until your turn is a very different situation from eleven left.
    """
    out = {}
    for row in rows:
        pos = row["pos"]
        if pos not in out:
            out[pos] = {"tier": row.get("tier"), "left": 0,
                        "best": row["name"]}
        if row.get("tier") == out[pos]["tier"]:
            out[pos]["left"] += 1
    for pos, info in out.items():
        info["at_risk"] = (picks_away is not None
                           and info["left"] <= max(1, picks_away // 3))
    return out


def render(board, state, rows, top, teams, upcoming=None):
    my_rows = [board.by_id[p] for p in state.my_picks if p in board.by_id]
    show_need = bool(my_rows)

    print("\033[H\033[J", end="")
    print("=" * 96)
    clock = f"{state.seconds_left:.0f}s" if state.seconds_left is not None else "--"
    turn = ("*** YOU ARE ON THE CLOCK ***" if state.is_my_turn
            else f"team {state.on_the_clock} picking")
    rnd = state.round_number(teams) or "?"
    print(f"LIVE DRAFT  |  round {rnd}  |  "
          f"{len(state.picks)} picks  |  {clock}  |  {turn}")
    print("=" * 96)

    if my_rows:
        counts = {}
        for r in my_rows:
            counts[r["pos"]] = counts.get(r["pos"], 0) + 1
        print("Your roster: " + "  ".join(f"{p} {n}" for p, n in
                                          sorted(counts.items())))
    if state.picks:
        recent = [f"{board.name(p['player_id'])}" for p in state.picks[-4:]]
        print("Just gone:   " + ", ".join(recent))

    picks_away = None
    if upcoming:
        nxt = [p for p in upcoming if p > len(state.picks)]
        if nxt:
            picks_away = nxt[0] - 1 - len(state.picks)
            print(f"Next pick:   #{nxt[0]} ({picks_away} away)"
                  + (f", then #{nxt[1]}" if len(nxt) > 1 else ""))

    pressure = tier_pressure(rows, picks_away)
    urgent = [f"{p} tier {i['tier']}: {i['left']} left"
              for p, i in sorted(pressure.items()) if i["at_risk"]]
    if urgent:
        print("Thin:        " + "  |  ".join(urgent))
    print()

    head = (f"{'PLAYER':<24}{'POS':<5}{'TM':<5}{'BYE':>6}{'PROJ':>8}"
            f"{'VOR':>8}{'TIER':>6}")
    print(head + (f"{'NEED':>8}" if show_need else ""))
    print("-" * (len(head) + (8 if show_need else 0)))
    for r in rows[:top]:
        bye = ""
        if r.get("bye"):
            bye = (f"{r['bye']}({r['bye_clash']})" if r.get("bye_clash")
                   else str(r["bye"]))
        line = (f"{r['name'][:23]:<24}{r['pos']:<5}{r['team']:<5}"
                f"{bye:>6}{r['proj']:>8.1f}{r['vor']:>+8.1f}"
                f"{('T' + str(r.get('tier', '?'))):>6}")
        if show_need:
            line += f"{r.get('need', 0.0):>+8.1f}"
        print(line)
    if show_need:
        print("\nNEED = value over whoever you would otherwise get in that "
              "slot, less a bye-collision cost.")
        print("BYE  = bye week; (n) = players you already roster who are out "
              "that week.")
    print(f"\n[{time.strftime('%H:%M:%S')}] Ctrl-C to stop.", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--league-id", type=int, required=True)
    ap.add_argument("--team-id", type=int)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--profile", default=".pwprofile",
                    help="browser profile to drive (needs the ESPN login)")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--autopick", type=int, metavar="SECS",
                    help="draft the top NEED player when the clock drops "
                         "below SECS on your turn. Dry run unless --arm is "
                         "also given")
    ap.add_argument("--bye-weight", type=float, default=1.0,
                    help="how many starter-weeks a bye collision is treated as "
                         "costing (0 disables bye penalties)")
    ap.add_argument("--arm", action="store_true",
                    help="actually click DRAFT. Without this, --autopick only "
                         "reports what it would have done")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    env = load_env()
    league = connect(env, year=args.season, league_id=args.league_id)
    team = find_my_team(league, swid=env.get("SWID"), team_id=args.team_id,
                        strict=False)
    if not team:
        sys.exit("Could not work out which team is yours — pass --team-id.")
    print(f"{league.settings.name}: you are team {team.team_id} "
          f"({team.team_name})", flush=True)

    board = Board(league, team.team_id, bye_weight=args.bye_weight)
    state = draftsocket.DraftState(my_team_id=team.team_id)
    url = draftsocket.room_url(args.league_id, team.team_id, env["SWID"],
                               args.season)

    pending = {"dirty": True}

    def on_frame(payload):
        if state.feed(payload):
            pending["dirty"] = True

    def hook(ws):
        if "fantasydraft" in ws.url:
            print(f"connected: {ws.url[:70]}...", flush=True)
            ws.on("framereceived", on_frame)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            args.profile, headless=False,
            viewport={"width": 1500, "height": 950},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("websocket", hook)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print("Draft room open. Draft in THIS window.", flush=True)

        # The socket only carries what happens from now on, so a board started
        # mid-draft would believe nobody had been drafted. The room knows.
        page.wait_for_timeout(6000)

        # Twice in testing the board ran against a different league than the
        # one being drafted, and reported confidently the whole time. The room
        # knows which draft it is showing, so ask it.
        seen = draftroom.room_league_name(page)
        if seen and not draftroom.leagues_match(league.settings.name, seen):
            ctx.close()
            sys.exit(f"Wrong draft room.\n  --league-id {args.league_id} is "
                     f"{league.settings.name!r}\n  but the room is showing "
                     f"{seen!r}.\nRe-run with the league id you are actually "
                     f"drafting in.")
        if seen:
            print(f"room confirms: {seen}", flush=True)

        known = [r["name"] for r in board.by_id.values()]
        by_name = {r["name"]: pid for pid, r in board.by_id.items()}
        try:
            mine = draftroom.my_roster_players(page, known)
        except Exception as exc:
            mine = []
            print(f"could not read your roster: {type(exc).__name__}: {exc}",
                  flush=True)
        mine_ids = [by_name[n] for n in mine if n in by_name]
        seeded = state.seed(mine_ids, mine_ids)
        if seeded:
            print(f"seeded {seeded} players already on your roster: "
                  f"{', '.join(mine)}", flush=True)
        else:
            print("no players on your roster yet.", flush=True)
        print("NOTE: other teams' earlier picks are not seeded — the board may "
              "still list players who are gone. Autopick skips them "
              "automatically.", flush=True)
        pending["dirty"] = True

        rows = board.value([])
        ds = draftclock.draft_settings(league)
        order = ds.get("pickOrder") or []
        sequence = draftclock.snake_order(
            order, draftclock.rounds_from_grid(league, len(order))
            or draftclock.roster_size(league.settings) or 16)
        attempted = set()
        try:
            while True:
                page.wait_for_timeout(700)

                if args.autopick and state.is_my_turn and rows:
                    left = state.seconds_left
                    marker = len(state.picks)
                    if (left is not None and left <= args.autopick
                            and marker not in attempted):
                        attempted.add(marker)
                        # A player already drafted is not in the room's list at
                        # all, so his row cannot be found -- which makes "not
                        # found" a reliable signal to move down the board
                        # rather than an error.
                        target, ok, msg = None, False, "no candidate"
                        for candidate in rows[:6]:
                            ok, msg = draftpick.pick(page, candidate["name"],
                                                     dry_run=not args.arm)
                            target = candidate
                            if ok:
                                break
                            print(f">>> skipping {candidate['name']}: {msg}",
                                  flush=True)
                            log(f"SKIP {candidate['name']}: {msg}")
                        print(f"\n>>> AUTOPICK {target['name'] if target else '?'}"
                              f": {msg}", flush=True)
                        log(f"AUTOPICK {target['name'] if target else '?'}"
                            f" armed={args.arm} ok={ok}: {msg}")
                        if ok and args.arm:
                            page.wait_for_timeout(2500)
                            good, detail = draftpick.confirm_via_socket(
                                state, target["player_id"], team.team_id)
                            print(f">>> {detail}", flush=True)
                            log(f"CONFIRM ok={good}: {detail}")
                            if not good:
                                print(">>> disarming after an unconfirmed "
                                      "pick", flush=True)
                                args.arm = False
                        page.wait_for_timeout(2500)
                if pending["dirty"]:
                    for pick in state.picks:
                        board.remove(pick["player_id"])
                    my_rows = [board.by_id[p] for p in state.my_picks
                               if p in board.by_id]
                    rows = board.value(my_rows)
                    pending["dirty"] = False
                render(board, state, rows, args.top, len(league.teams),
                       draftclock.next_picks_for(
                           sequence, team.team_id, len(state.picks)))
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
