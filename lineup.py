"""Roster math shared by the draft board and the waiver wire.

Both tools need the same two things: turn an espn-api Player into a plain dict,
and work out the best legal starting lineup from a set of them. This lives here
rather than in either tool so neither has to import the other.
"""

POSITIONS = ["QB", "RB", "WR", "TE", "D/ST", "K"]

# How deep to pull each position. ESPN returns free agents sorted by percent
# owned, so these depths comfortably cover everyone who will be drafted.
POOL_DEPTH = {"QB": 60, "RB": 160, "WR": 190, "TE": 80, "D/ST": 32, "K": 32}

# Roster slots that are not starting slots and so create no demand.
NON_STARTING_SLOTS = {"BE", "IR", "ER", "Rookie", ""}


def starting_slots(settings):
    """One team's starting slots as {slot_name: count}.

    Unlike the draft board's league-wide demand, this is a single lineup, so
    counts are not multiplied by the size of the league.
    """
    slots = {}
    for slot, count in (getattr(settings, "position_slot_counts", {}) or {}).items():
        if count and slot not in NON_STARTING_SLOTS:
            slots[slot] = int(count)
    return slots


def projection(player, weeks):
    """Projected points over `weeks`, or the whole season if weeks is None.

    player.stats is keyed by scoring period, with week 0 holding season totals,
    so a missing week means no projected game — a bye, or a week the season has
    not reached. Both correctly contribute nothing.
    """
    stats = getattr(player, "stats", None) or {}
    if weeks is None:
        return float(getattr(player, "projected_total_points", 0) or 0)
    total = 0.0
    for week in weeks:
        total += float((stats.get(week) or {}).get("projected_points", 0) or 0)
    return total


def as_row(player, weeks):
    """Flatten an espn-api Player into the plain dict both tools rank on."""
    return {
        "name": player.name,
        "pos": player.position,
        "team": player.proTeam,
        "slots": list(getattr(player, "eligibleSlots", None) or []),
        "proj": round(projection(player, weeks), 1),
        "owned": round(float(getattr(player, "percent_owned", 0) or 0), 1),
        "injury": (getattr(player, "injuryStatus", "") or "").replace("ACTIVE", ""),
        "player_id": getattr(player, "playerId", None),
    }


def best_lineup(rows, slots):
    """Highest-scoring legal lineup from `rows`, as (total, starter_ids).

    Slots are filled scarcest-first: a slot few players are eligible for is
    resolved before a slot many are, so a flex does not greedily consume the
    only player who could have filled a dedicated slot.

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
        eligible = [r for r in rows if slot in r["slots"] and id(r) not in used]
        if not eligible:
            continue
        pick = max(eligible, key=lambda r: r["proj"])
        used.add(id(pick))
        total += pick["proj"]
    return round(total, 1), used


def gain_from_adding(roster, candidate, slots, base_total=None):
    """How much `candidate` would raise the best starting lineup.

    This is the metric both tools rank on in-season. It captures cascades: a
    receiver who displaces your WR2 also bumps that WR2 into the flex over
    someone worse, and both moves count.
    """
    if base_total is None:
        base_total, _ = best_lineup(roster, slots)
    total, _ = best_lineup(roster + [candidate], slots)
    return round(total - base_total, 1)
