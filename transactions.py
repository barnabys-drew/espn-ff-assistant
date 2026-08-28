"""ESPN transaction payloads. Pure functions — nothing here touches the network.

Every mutation ESPN accepts is one POST to a single `/transactions/` endpoint.
Only the body changes, and the bodies share an envelope; the whole write API is
five shapes of `items` list:

    ROSTER          LINEUP items with fromLineupSlotId / toLineupSlotId
    FREEAGENT       ADD (toTeamId) and/or DROP (fromTeamId), immediate
    WAIVER          the same items, plus bidAmount, landing PENDING
    TRADE_PROPOSAL  TRADE items carrying both team ids, plus expirationDate
    TRADE_PROPOSAL  executionType CANCEL + relatedTransactionId, to withdraw

Building a payload here reaches nobody, which is the point rather than a
nicety: a body that mutates a real league is worth testing exhaustively, and it
can only be tested exhaustively if constructing one is free and inert. Sending
one is espnwrite.py's job, and it is the only module that can.

The one exception is `fetch_transactions`, which reads what is already queued.
It is a GET, it changes nothing, and it is kept apart from the parsing it feeds
so that the parsing stays testable against fixtures.
"""
import datetime
import hashlib
import json

from espn_api.football.constant import POSITION_MAP


def _slot_ids():
    """{slot name: slot id}, inverted from the authoritative half of POSITION_MAP.

    POSITION_MAP is bidirectional, but its name→id half cannot be used here. It
    is missing `BE` (20), `IR` (21) and `RB/WR/TE` (23), and it carries a
    `FLEX` key for 23 that ESPN never actually returns — both `eligibleSlots`
    and `slot_position` say `RB/WR/TE`.

    Those are exactly the slots a lineup change moves players between, so the
    shipped reverse map would raise on the ordinary bench-to-flex case and
    quietly accept a `FLEX` that nothing else in the codebase produces. The
    int→name half is complete, so it is inverted instead.
    """
    return {name: number for number, name in POSITION_MAP.items()
            if isinstance(number, int) and name}


SLOT_IDS = _slot_ids()

# Slots a player is not starting from. Kept local rather than imported from
# lineup.py because this module is about what ESPN accepts, not about roster
# maths, and the two lists drifting apart would be a silent mis-slot.
BENCH = "BE"
INJURED_RESERVE = "IR"


def slot_id(name):
    """The numeric id ESPN wants for a slot name."""
    try:
        return SLOT_IDS[name]
    except KeyError:
        raise ValueError(
            f"Unknown lineup slot {name!r}. Known slots: "
            f"{', '.join(sorted(SLOT_IDS))}") from None


def member_id(swid):
    """The SWID as ESPN wants it in a transaction body: braced.

    The cookie is stored braced, but a hand-copied one often is not, and ESPN
    accepts the request and silently attributes it to nobody.
    """
    value = str(swid or "").strip()
    if not value:
        raise ValueError("No SWID: a transaction cannot say who is making it.")
    return value if value.startswith("{") else "{" + value.strip("{}") + "}"


def envelope(team_id, swid, scoring_period, txn_type, items,
             execution_type="EXECUTE", **extra):
    """The shared transaction body. `extra` carries op-specific keys.

    `isLeagueManager` is always False. Sending True asks ESPN to apply the move
    with commissioner powers, which bypasses the rules that make a rejected
    write a useful signal — and which this tool has no business bypassing.
    """
    body = {
        "isLeagueManager": False,
        "teamId": int(team_id),
        "type": txn_type,
        "memberId": member_id(swid),
        "scoringPeriodId": int(scoring_period),
        "executionType": execution_type,
        "items": list(items),
    }
    body.update(extra)
    return body


def set_lineup(team_id, swid, scoring_period, moves):
    """Move players between slots. `moves` is (player_id, from_slot, to_slot).

    Slots are passed by name and converted here, so a caller never handles a
    raw id. A straight swap is two moves in one transaction: ESPN validates the
    final state, not each step, so the bench player and the starter he replaces
    have to travel together or the roster is briefly illegal and the whole
    thing is rejected.
    """
    items = []
    for player_id, from_slot, to_slot in moves:
        items.append({
            "playerId": int(player_id),
            "type": "LINEUP",
            "fromLineupSlotId": slot_id(from_slot),
            "toLineupSlotId": slot_id(to_slot),
        })
    if not items:
        raise ValueError("No lineup moves: nothing to send.")
    return envelope(team_id, swid, scoring_period, "ROSTER", items)


def add_drop(team_id, swid, scoring_period, add_id=None, drop_id=None):
    """Immediate free-agent add and/or drop, processed on submission."""
    items = []
    if add_id is not None:
        items.append({"playerId": int(add_id), "type": "ADD",
                      "toTeamId": int(team_id)})
    if drop_id is not None:
        items.append({"playerId": int(drop_id), "type": "DROP",
                      "fromTeamId": int(team_id)})
    if not items:
        raise ValueError("An add/drop must add somebody, drop somebody, or both.")
    return envelope(team_id, swid, scoring_period, "FREEAGENT", items)


def waiver_claim(team_id, swid, scoring_period, add_id, drop_id=None, bid=None):
    """A waiver claim, which lands PENDING and processes at the next waiver run.

    `bid` is the FAAB amount, and None is correct in a league that runs waiver
    priority instead — sending a bid where there is no budget is not a smaller
    request, it is a differently-shaped one.
    """
    items = [{"playerId": int(add_id), "type": "ADD", "toTeamId": int(team_id)}]
    if drop_id is not None:
        items.append({"playerId": int(drop_id), "type": "DROP",
                      "fromTeamId": int(team_id)})
    return envelope(team_id, swid, scoring_period, "WAIVER", items,
                    bidAmount=bid)


def _expiry(days=2, now=None):
    """When a proposal lapses: the end of the day, `days` from now, in UTC.

    Rounded to the day rather than computed to the second, and that is load
    bearing. The confirm token hashes the whole payload, so a timestamp taken
    at build time would differ between the preview and the confirming run —
    the token would never match and a trade could never be sent at all. Rounding
    makes the body stable for the whole day, which is as long as anyone leaves
    a preview sitting.

    Previewing just before midnight UTC and confirming just after still fails,
    and fails the right way: it refuses rather than sending something the
    preview did not show.
    """
    today = (now or datetime.datetime.now(datetime.timezone.utc)).date()
    return (today + datetime.timedelta(days=days)).strftime(
        "%Y-%m-%dT23:59:59.000Z")


def propose_trade(team_id, swid, scoring_period, other_team_id,
                  send_ids, receive_ids, comment="", expires=None):
    """Propose a trade. `send_ids` leave your team; `receive_ids` come to it."""
    if not send_ids and not receive_ids:
        raise ValueError("A trade proposal has to move at least one player.")
    items = []
    for player_id in (send_ids or []):
        items.append({"playerId": int(player_id), "type": "TRADE",
                      "fromTeamId": int(team_id), "toTeamId": int(other_team_id)})
    for player_id in (receive_ids or []):
        items.append({"playerId": int(player_id), "type": "TRADE",
                      "fromTeamId": int(other_team_id), "toTeamId": int(team_id)})
    return envelope(team_id, swid, scoring_period, "TRADE_PROPOSAL", items,
                    expirationDate=expires or _expiry(), comment=comment)


def cancel_trade(team_id, swid, scoring_period, transaction_id):
    """Withdraw a pending proposal you created.

    Accepting or rejecting somebody else's proposal is deliberately absent:
    those payloads were never captured upstream, and guessing at the shape of a
    write that moves players between two rosters is not a reasonable thing to
    do to a league.
    """
    return envelope(team_id, swid, scoring_period, "TRADE_PROPOSAL", [],
                    execution_type="CANCEL",
                    relatedTransactionId=str(transaction_id))


def lineup_moves(rows, assignment):
    """The (player_id, from_slot, to_slot) triples that reach `assignment`.

    `rows` are this week's roster rows carrying `slot_now`; `assignment` is
    what lineup.assign_lineup returned. Only players whose slot actually
    changes produce a move, so a lineup that is already correct sends nothing
    rather than sending a no-op transaction.

    Injured reserve is left alone. Moving a player out of IR has its own
    eligibility rules — ESPN checks whether he is still designated out — and a
    lineup optimiser that has no notion of that should not be the thing which
    discovers it.
    """
    target = {row["player_id"]: slot
              for slot, row in assignment if row is not None}

    moves = []
    for row in rows:
        now = row.get("slot_now")
        if now == INJURED_RESERVE:
            continue
        wanted = target.get(row["player_id"], BENCH)
        if wanted != now:
            moves.append((row["player_id"], now, wanted))
    return moves


# --- reading what is already queued ----------------------------------------
# espn-api's Transaction class drops the transaction's own id on the way
# through, the same way its Player class drops averageDraftPosition. Cancelling
# a proposal needs that id, so the raw payload is parsed here instead — the
# same approach adp.py and picks.py already take for the same reason.

TRANSACTION_VIEW = "mTransactions2"

PENDING_TYPES = ["FREEAGENT", "WAIVER", "WAIVER_ERROR", "TRADE_PROPOSAL"]


def parse_transactions(payload):
    """Flatten a raw mTransactions2 response into plain rows.

    Keeps the id, which is the only field here that cannot be got any other
    way, and the status, which is what distinguishes a claim still queued from
    one that already failed.
    """
    rows = []
    for entry in (payload or {}).get("transactions") or []:
        items = []
        for item in entry.get("items") or []:
            items.append({
                "type": item.get("type"),
                "player_id": item.get("playerId"),
                "from_team": item.get("fromTeamId"),
                "to_team": item.get("toTeamId"),
            })
        rows.append({
            "id": entry.get("id"),
            "team_id": entry.get("teamId"),
            "type": entry.get("type"),
            "status": entry.get("status"),
            "scoring_period": entry.get("scoringPeriodId"),
            "bid": entry.get("bidAmount"),
            "items": items,
        })
    return rows


def fetch_transactions(league, scoring_period, types=None):
    """Raw transactions for one scoring period. The only I/O in this module.

    Read-only, and separated from parse_transactions so the parsing can be
    tested against fixtures without a league.
    """
    filters = {"transactions": {"filterType": {
        "value": list(types or PENDING_TYPES)}}}
    return league.espn_request.league_get(
        params={"view": TRANSACTION_VIEW, "scoringPeriodId": int(scoring_period)},
        headers={"x-fantasy-filter": json.dumps(filters)}) or {}


def canonical(payload):
    """The payload as one stable string, so it can be hashed and compared."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def confirm_token(payload):
    """A short hash of the exact body that would be POSTed.

    This is what makes "preview then confirm" mean something. The token is
    derived from the payload, so confirming re-derives it from a freshly built
    payload and compares: if anything moved in between — a player claimed, the
    scoring period rolled over, a different drop chosen — the bodies differ,
    the tokens differ, and the write is refused rather than executing something
    that was never shown.
    """
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()[:12]
