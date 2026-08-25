"""Read draft picks while the draft is still running.

espn-api will not do this. BaseLeague._fetch_draft bails out early:

    if not data.get('draftDetail', {}).get('drafted'):
        return

ESPN only sets `drafted` once a draft is **complete**, so `league.draft` is
empty for the entire duration of the one event it would actually be useful for.
It works fine on a finished season, which is exactly why the gap is easy to
miss.

This reads the same mDraftDetail payload and parses the picks regardless of
that flag, which gives two things a live board needs: every player already
taken league-wide, and which of them are yours.
"""


def _order(pick):
    """Sort key for a pick.

    overallPickNumber is what ESPN normally sends; the round/round-pick pair is
    a fallback for payloads that omit it, and round picks never reach 1000.
    """
    if pick["overall"] is not None:
        return pick["overall"]
    return (pick["round"] or 0) * 1000 + (pick["round_pick"] or 0)


def parse_picks(detail):
    """Pick rows out of a raw draftDetail block, in draft order.

    Auction and keeper leagues come through the same shape, so bid_amount and
    keeper are carried rather than assumed absent.
    """
    rows = []
    for p in (detail or {}).get("picks") or []:
        player_id = p.get("playerId")
        if player_id is None or player_id < 0:
            continue  # an empty or forfeited slot, not a player
        rows.append({
            "player_id": player_id,
            "team_id": p.get("teamId"),
            "round": p.get("roundId"),
            "round_pick": p.get("roundPickNumber"),
            "overall": p.get("overallPickNumber"),
            "keeper": bool(p.get("keeper")),
            "bid": p.get("bidAmount"),
        })
    rows.sort(key=_order)
    return rows


def taken_ids(picks):
    """Every player id already drafted, for subtracting from the pool."""
    return {p["player_id"] for p in picks}


def roster_ids(picks, team_id):
    """Player ids drafted by one team, in draft order."""
    if team_id is None:
        return []
    return [p["player_id"] for p in picks if p["team_id"] == team_id]


def fetch_picks(league):
    """Live picks from the league, as (picks, draft_is_complete).

    Returns an empty list rather than raising if the draft has not started or
    the payload is shaped unexpectedly — a board with no pick data is degraded
    but still usable, and draft night is the wrong time to crash.
    """
    try:
        data = league.espn_request.get_league_draft() or {}
    except Exception:
        return [], False
    detail = data.get("draftDetail") or {}
    return parse_picks(detail), bool(detail.get("drafted"))
