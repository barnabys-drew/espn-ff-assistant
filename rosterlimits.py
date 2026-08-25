"""How many of each position a team may roster.

ESPN keeps these in settings.rosterSettings.positionLimits, keyed by *player
position* id -- a different numbering from the lineup-slot ids in
POSITION_MAP -- with -1 meaning no limit. A 10-team league typically allows
QB 4, RB 8, WR 8, TE 3, K 3, D/ST 3.

These are hard caps, not preferences. Recommending a ninth running back is
advice that cannot be taken, and an armed picker attempting it would fail in
the draft room for reasons nothing in the log would explain.
"""

# Player-position ids, which are not the lineup-slot ids used elsewhere.
POSITION_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

NO_LIMIT = -1


def parse_limits(roster_settings):
    """{position: max} for positions that actually have a cap."""
    limits = {}
    for key, value in ((roster_settings or {}).get("positionLimits") or {}).items():
        try:
            pos = POSITION_IDS.get(int(key))
            cap = int(value)
        except (TypeError, ValueError):
            continue
        if pos and cap != NO_LIMIT and cap > 0:
            limits[pos] = cap
    return limits


def fetch_limits(league):
    """Position caps for this league, or {} if they cannot be read."""
    try:
        data = league.espn_request.get_league() or {}
    except Exception:
        return {}
    return parse_limits((data.get("settings") or {}).get("rosterSettings") or {})


def counts(rows):
    """How many of each position are on this roster."""
    tally = {}
    for row in rows:
        tally[row["pos"]] = tally.get(row["pos"], 0) + 1
    return tally


def full_positions(rows, limits):
    """Positions already at their cap, which cannot be drafted again."""
    tally = counts(rows)
    return {pos for pos, cap in limits.items() if tally.get(pos, 0) >= cap}
