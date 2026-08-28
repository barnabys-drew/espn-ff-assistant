"""The one POST, and the guard rails around it.

Everything else in this repository reads. This is the only module that changes
anything in a real league, which is why it is small enough to read in full
before trusting it.

espn-api is read-only — every method in its requests layer is `requests.get`,
and the sole `requests.post` is commented-out dead auth code. But the write API
is the same league endpoint on a different host, so nothing needs vendoring or
forking: the read endpoint espn-api already built is reused with the host
swapped, and the cookies it is already holding are the credentials.

Three things stand between a payload and a live league, and all three have to
be cleared every time:

1. `ESPN_WRITES=1` must be set in `.env`. Absent, this module refuses before
   building anything.
2. Nothing executes on the first run. Every command previews the exact body it
   would send and stops.
3. Executing requires the confirm token for that body. The token is a hash of
   the payload, so confirming rebuilds the payload from current data and
   re-derives the token — if anything changed since the preview, the tokens
   differ and the write is refused instead of sending something never shown.

There is deliberately no --force, no --yes, and no way to execute in one step.
"""
import json

import requests

import transactions

READ_HOST = "lm-api-reads.fantasy.espn.com"
WRITE_HOST = "lm-api-writes.fantasy.espn.com"

# ESPN rejects a transaction that does not look like it came from its own web
# client, so these are required rather than cosmetic.
WRITE_HEADERS = {
    "content-type": "application/json",
    "x-fantasy-platform": "espn-fantasy-web",
    "x-fantasy-source": "kona",
    "origin": "https://fantasy.espn.com",
    "referer": "https://fantasy.espn.com/",
}

TIMEOUT = 20


class WritesDisabled(Exception):
    """ESPN_WRITES is not set. Nothing was sent."""


class NotConfirmed(Exception):
    """No confirm token, or one that does not match this payload."""


class WriteRejected(Exception):
    """ESPN refused the transaction."""


def writes_enabled(env):
    return str(env.get("ESPN_WRITES", "")).strip().lower() in {"1", "true", "yes"}


def require_writes(env):
    if not writes_enabled(env):
        raise WritesDisabled(
            "Writes are off. Add ESPN_WRITES=1 to .env to enable them.\n"
            "Everything still previews with writes off — only sending is blocked.")


def write_endpoint(league):
    """The league's write URL, derived from the read URL espn-api already built.

    Deriving it rather than rebuilding it means the season, segment and league
    id can only be wrong if reading was already wrong, which is loud.
    """
    endpoint = league.espn_request.LEAGUE_ENDPOINT
    if READ_HOST not in endpoint:
        raise WriteRejected(
            f"Cannot derive a write endpoint from {endpoint!r}: expected host "
            f"{READ_HOST}. espn-api may have changed its endpoints.")
    return endpoint.replace(READ_HOST, WRITE_HOST) + "/transactions/"


def explain(status, body):
    """Turn an HTTP status into something that says what to do about it."""
    detail = (body or "").strip()[:300]
    if status == 401:
        return ("Rejected (401): espn_s2/SWID are expired or cannot write. "
                "Re-run login.py to refresh them.")
    if status == 403:
        return (f"Rejected (403): authenticated, but not allowed to make this "
                f"move. {detail}")
    if status == 404:
        return "Rejected (404): no such league, season, or transaction."
    if status == 409:
        return (f"Rejected (409): the move is illegal right now — roster full, "
                f"player already claimed, or locked. {detail}")
    return f"Rejected (HTTP {status}). {detail}"


def post(league, payload, session=None):
    """POST one transaction. Returns the parsed response.

    Every caller reaches this through `execute`, which is where the guards
    live; this function only speaks HTTP.
    """
    url = write_endpoint(league)
    http = session or requests
    response = http.post(url, json=payload, headers=WRITE_HEADERS,
                         cookies=league.espn_request.cookies, timeout=TIMEOUT)
    if response.status_code not in (200, 201):
        raise WriteRejected(explain(response.status_code, response.text))
    try:
        return response.json()
    except ValueError:
        return {"status": response.status_code}


def preview(payload, summary_lines):
    """The text shown before anything is sent, ending in the confirm token."""
    token = transactions.confirm_token(payload)
    out = ["", "=" * 70, "PREVIEW — nothing has been sent", "=" * 70]
    out.extend(summary_lines)
    out.append("")
    out.append("Exact body that would be POSTed:")
    out.append(json.dumps(payload, indent=2, sort_keys=True))
    out.append("")
    out.append(f"To send it, re-run the same command with:  --confirm {token}")
    out.append("The token covers this exact body. If anything changes before "
               "you confirm,")
    out.append("the token will no longer match and nothing will be sent.")
    return "\n".join(out), token


def execute(league, env, payload, confirm, session=None):
    """Send `payload`, but only with writes enabled and a matching token.

    The token is recomputed from the payload passed in here — which the caller
    has just rebuilt from current data — rather than remembered from the
    preview. That is the whole guarantee: a token minted against a roster that
    has since changed will not match the payload built from the roster as it is
    now.
    """
    require_writes(env)
    expected = transactions.confirm_token(payload)
    if not confirm:
        raise NotConfirmed(f"Not confirmed. Re-run with --confirm {expected}")
    if confirm.strip().lower() != expected:
        raise NotConfirmed(
            f"Confirm token does not match this transaction.\n"
            f"  you passed: {confirm}\n"
            f"  this body:  {expected}\n"
            f"Something changed since the preview. Re-run without --confirm, "
            f"read the new preview, and confirm that.")
    return post(league, payload, session=session)
