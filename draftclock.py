"""Where the draft is right now: countdown, whose pick it is, when yours comes.

All read-only, and all from the league payload rather than the draft room:

  draftSettings.date             when the draft starts (epoch ms)
  draftSettings.timePerSelection seconds on each pick clock
  draftSettings.type/pickOrder   snake order, so a pick number maps to a team
  draftDetail.inProgress         whether it is running right now

Knowing your next pick is four away is what decides whether you reach for a
player now or wait a round, and it is the one number a value board cannot tell
you on its own.
"""
import time


def draft_settings(league):
    """Raw draftSettings block, or {} if it cannot be read."""
    try:
        data = league.espn_request.get_league() or {}
    except Exception:
        return {}
    return (data.get("settings") or {}).get("draftSettings") or {}


def draft_state(league):
    """(drafted, in_progress) straight from draftDetail."""
    try:
        data = league.espn_request.get_league_draft() or {}
    except Exception:
        return False, False
    detail = data.get("draftDetail") or {}
    return bool(detail.get("drafted")), bool(detail.get("inProgress"))


def snake_order(pick_order, rounds):
    """Team ids in pick order for a whole snake draft.

    Odd rounds run the order forwards, even rounds backwards — which is the
    entire reason your second pick can be one away from your first, or twenty.
    """
    order = list(pick_order or [])
    if not order:
        return []
    sequence = []
    for rnd in range(rounds):
        sequence.extend(order if rnd % 2 == 0 else order[::-1])
    return sequence


def next_picks_for(sequence, team_id, picks_made, limit=3):
    """Overall pick numbers (1-based) still ahead for one team."""
    return [i + 1 for i, owner in enumerate(sequence)
            if owner == team_id and i >= picks_made][:limit]


# An IR spot is not drafted into, so it is not a round.
NON_DRAFTED_SLOTS = {"IR", "ER", "Rookie", ""}


def roster_size(settings):
    """Rounds a snake draft runs, from the roster it has to fill.

    Only a fallback: the pick grid itself is authoritative when available,
    since ESPN pre-creates every slot and summing roster spots has to guess
    which of them are actually drafted into.
    """
    counts = getattr(settings, "position_slot_counts", None) or {}
    return sum(int(v) for slot, v in counts.items()
               if v and slot not in NON_DRAFTED_SLOTS)


def rounds_from_grid(league, teams):
    """Rounds implied by the pre-created pick grid, or None.

    ESPN lays out every pick up front -- 160 slots for 10 teams over 16 rounds
    -- so dividing gives the real number rather than one inferred from roster
    slots, which overcounts by including IR.
    """
    if not teams:
        return None
    try:
        data = league.espn_request.get_league_draft() or {}
    except Exception:
        return None
    grid = ((data.get("draftDetail") or {}).get("picks") or [])
    return len(grid) // teams if len(grid) >= teams else None


def status(league, team_id, picks_made):
    """Everything the board wants to say about the clock, as a plain dict."""
    ds = draft_settings(league)
    drafted, in_progress = draft_state(league)
    starts_ms = ds.get("date")
    order = ds.get("pickOrder") or []
    rounds = (rounds_from_grid(league, len(order))
              or roster_size(league.settings) or 16)
    sequence = snake_order(order, rounds)

    on_the_clock = (sequence[picks_made]
                    if sequence and picks_made < len(sequence) else None)
    upcoming = next_picks_for(sequence, team_id, picks_made) if team_id else []

    return {
        "starts_at": starts_ms / 1000.0 if starts_ms else None,
        "seconds_until_start": (starts_ms / 1000.0 - time.time()
                                if starts_ms else None),
        "seconds_per_pick": ds.get("timePerSelection"),
        "type": ds.get("type"),
        "rounds": rounds,
        "in_progress": in_progress,
        "complete": drafted,
        "picks_made": picks_made,
        "on_the_clock_team": on_the_clock,
        "is_my_turn": on_the_clock is not None and on_the_clock == team_id,
        "my_next_picks": upcoming,
        "picks_until_mine": (upcoming[0] - 1 - picks_made) if upcoming else None,
    }


def humanise(seconds):
    """A duration a person can read at a glance."""
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    if seconds < 0:
        return "now"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def line(state):
    """One or two lines summarising the clock for the board header."""
    if state["complete"]:
        return "Draft complete."
    if not state["in_progress"]:
        wait = state["seconds_until_start"]
        when = f"starts in {humanise(wait)}" if wait and wait > 0 else "not started"
        return (f"Draft {when} — {state['type'] or '?'}, "
                f"{state['rounds']} rounds, "
                f"{state['seconds_per_pick'] or '?'}s per pick.")

    parts = [f"LIVE — {state['picks_made']} picks in"]
    if state["is_my_turn"]:
        parts.append("*** YOU ARE ON THE CLOCK ***")
    elif state["picks_until_mine"] is not None:
        nxt = state["my_next_picks"][0]
        parts.append(f"your next pick is #{nxt} "
                     f"({state['picks_until_mine']} away)")
        if len(state["my_next_picks"]) > 1:
            parts.append(f"then #{state['my_next_picks'][1]}")
    return "  |  ".join(parts)
