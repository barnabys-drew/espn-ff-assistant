"""Make a pick in the draft room.

The room is a JS app with no pick API we can call, so this drives the same
controls a person uses: type the name into the player search, open that row,
press DRAFT. Selectors are tried in order because ESPN reskins its draft room
between seasons and a single brittle selector would fail silently.

Nothing here decides *who* to draft. It is handed a name and it clicks, so the
valuation and the clicking stay separable — which also means dry_run can prove
the whole path without a pick being made.
"""

SEARCH_BOXES = [
    'input[placeholder="Player Name"]',
    'input[placeholder*="Player"]',
    'input[type="text"][class*="player"]',
]

DRAFT_BUTTONS = [
    'button:has-text("DRAFT")',
    'button:has-text("Draft")',
    '[class*="draft-button"] button',
]


def find_search(page):
    for selector in SEARCH_BOXES:
        box = page.locator(selector).first
        try:
            if box.count() and box.is_visible():
                return box
        except Exception:
            continue
    return None


def find_draft_button(page, within=None):
    root = within or page
    for selector in DRAFT_BUTTONS:
        button = root.locator(selector).first
        try:
            if button.count() and button.is_visible() and button.is_enabled():
                return button
        except Exception:
            continue
    return None


def pick(page, player_name, dry_run=True, timeout_ms=6000):
    """Attempt to draft `player_name`. Returns (ok, message).

    dry_run stops immediately before the click, after everything else has been
    located -- so a dry run failing means the real thing would have failed too,
    which is the point of it.
    """
    search = find_search(page)
    if not search:
        return False, "could not find the player search box"

    try:
        search.click()
        search.fill("")
        search.type(player_name, delay=25)
        page.wait_for_timeout(1200)
    except Exception as exc:
        return False, f"search failed: {type(exc).__name__}: {exc}"

    # The row for this player, so we do not press a DRAFT button belonging to
    # somebody else further down the list.
    # Scope to rows that actually carry a DRAFT control, so a name appearing
    # in a pick-history panel cannot be mistaken for an available player.
    row = page.locator(
        f'tr:has-text("{player_name}"):has(button)').first
    if not row.count():
        row = page.locator(
            f'[class*="row"]:has-text("{player_name}"):has(button)').first
    if not row.count():
        return False, f"no draftable row for {player_name!r} after searching"

    # The button must be inside this player's row. Falling back to a
    # page-level DRAFT button looks like success but is not: when it is your
    # turn there is always an enabled DRAFT button somewhere, so any row
    # matched anywhere in the DOM -- pick history, a drafted list -- would pair
    # with it and appear to work. Requiring it inside the row is what makes
    # "already drafted" fail closed, since a drafted player has no draftable
    # row in the available list.
    button = find_draft_button(page, within=row)
    if not button:
        return False, (f"{player_name} has no draftable row — already taken, "
                       f"or not in the available list")

    if dry_run:
        return True, f"DRY RUN — would click DRAFT for {player_name}"

    try:
        button.click(timeout=timeout_ms)
    except Exception as exc:
        return False, f"click failed: {type(exc).__name__}: {exc}"
    return True, f"clicked DRAFT for {player_name}"


def confirm_via_socket(state, expected_player_id, my_team_id):
    """Did the socket confirm the pick we actually intended?

    The click is only half of it. A pick is real when the draft service says so,
    and if it says something else -- a different player, another team -- the
    right response is to stop, not to try again.
    """
    for frame in reversed(state.picks):
        if frame["team_id"] == my_team_id:
            if frame["player_id"] == expected_player_id:
                return True, "confirmed by socket"
            return False, (f"socket reports we drafted {frame['player_id']}, "
                           f"not {expected_player_id} — stopping")
    return False, "no pick from us on the socket yet"
