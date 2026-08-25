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
import sys
import time

import draftpick
import draftsocket
from common import connect, find_my_team, load_env
from lineup import (POOL_DEPTH, POSITIONS, as_row, best_lineup,
                    gain_from_adding, starting_slots)
from draft import allocate_flex, parse_slots, replacement_levels


class Board:
    """The pool, valued, with drafted players removed as picks arrive."""

    def __init__(self, league, my_team_id):
        self.league = league
        self.my_team_id = my_team_id
        self.slots = starting_slots(league.settings)
        self.by_id = {}
        self.pool = {}
        self._load()

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
                board.append(r)
        board.sort(key=lambda r: -r["vor"])

        if my_rows and self.slots:
            base_total, _ = best_lineup(my_rows, self.slots)
            repl = {}
            for pos, rows in self.pool.items():
                if rows:
                    idx = min(startable.get(pos, 0), len(rows) - 1)
                    repl[pos] = gain_from_adding(my_rows, rows[idx],
                                                 self.slots, base_total)
            for r in board:
                gain = gain_from_adding(my_rows, r, self.slots, base_total)
                r["need"] = round(gain - repl.get(r["pos"], 0), 1)
        return board


def render(board, state, rows, top, teams):
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
    print()

    head = f"{'PLAYER':<24}{'POS':<5}{'TM':<5}{'BYE':>4}{'PROJ':>8}{'VOR':>8}"
    print(head + (f"{'NEED':>8}" if show_need else ""))
    print("-" * (len(head) + (8 if show_need else 0)))
    for r in rows[:top]:
        line = (f"{r['name'][:23]:<24}{r['pos']:<5}{r['team']:<5}"
                f"{'':>4}{r['proj']:>8.1f}{r['vor']:>+8.1f}")
        if show_need:
            line += f"{r.get('need', 0.0):>+8.1f}"
        print(line)
    if show_need:
        print("\nNEED = value over whoever you would otherwise get in that slot.")
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

    board = Board(league, team.team_id)
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

        rows = board.value([])
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
                        target = rows[0]
                        ok, msg = draftpick.pick(page, target["name"],
                                                 dry_run=not args.arm)
                        print(f"\n>>> AUTOPICK {target['name']}: {msg}",
                              flush=True)
                        if ok and args.arm:
                            page.wait_for_timeout(2500)
                            good, detail = draftpick.confirm_via_socket(
                                state, target["player_id"], team.team_id)
                            print(f">>> {detail}", flush=True)
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
                render(board, state, rows, args.top, len(league.teams))
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
