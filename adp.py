"""Average draft position: where the market actually takes a player.

ESPN carries this in the same kona_player_info payload free_agents() already
requests, under ownership.averageDraftPosition — espn-api's Player class simply
drops the field on the way through, so it has to be read from the raw response.

ADP is market data, not a projection, so unlike everything else here it needs
no validation: it records where players actually go. The edge is the gap
against your own valuation. If the board wants a player at pick 20 and the room
takes him at 35, you can take somebody else first and still have him — which is
the question a value board cannot answer on its own.
"""
import json

# Same shape free_agents() uses, so we see the same population.
FILTER_STATUS = ["FREEAGENT", "WAIVERS"]


def _filter(slot_id, size):
    return {"players": {"filterStatus": {"value": FILTER_STATUS},
                        "filterSlotIds": {"value": [slot_id]},
                        "limit": size,
                        "sortPercOwned": {"sortPriority": 1,
                                          "sortAsc": False}}}


def parse_adp(payload):
    """{player_id: {"adp": float, "auction": float, "rank": int}} from a raw response.

    A player with no recorded ADP is omitted rather than defaulted. Treating
    "unknown" as "goes last" would make every unranked player look like a
    bargain, which is the wrong way to be wrong.
    """
    out = {}
    for entry in (payload or {}).get("players") or []:
        player = entry.get("player") or {}
        pid = player.get("id")
        if pid is None:
            continue
        own = player.get("ownership") or {}
        adp = own.get("averageDraftPosition")
        if adp in (None, 0):
            continue
        ranks = (player.get("draftRanksByRankType") or {})
        ppr = ranks.get("PPR") or ranks.get("STANDARD") or {}
        out[pid] = {"adp": round(float(adp), 1),
                    "auction": own.get("auctionValueAverage"),
                    "rank": ppr.get("rank")}
    return out


def fetch_adp(league, slot_ids, week=1, size=200):
    """ADP for the free-agent pool, or {} if it cannot be read.

    Best effort by design: a board without ADP is the board we had all along,
    so a failure here should cost a column rather than the tool.
    """
    found = {}
    for slot_id in slot_ids:
        try:
            payload = league.espn_request.league_get(
                params={"view": "kona_player_info", "scoringPeriodId": week},
                headers={"x-fantasy-filter": json.dumps(_filter(slot_id, size))})
        except Exception:
            continue
        found.update(parse_adp(payload))
    return found


def wait_verdict(adp, picks_away, current_pick, cushion=0.75):
    """Whether a player is likely to still be there at your next pick.

    Compared against your next pick number with a cushion, because ADP is an
    average: a player whose ADP is exactly your next pick goes earlier half the
    time. The cushion asks for the gap to be meaningfully larger than the wait,
    so "wait" means likely rather than a coin flip.
    """
    if adp is None or picks_away is None or current_pick is None:
        return None
    next_pick = current_pick + picks_away
    return adp > next_pick + cushion * picks_away
