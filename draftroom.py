"""Recover draft state from the draft room's own DOM.

The websocket only carries what happens after you connect, so a board started
mid-draft -- or restarted after a crash -- believes nobody has been drafted and
happily recommends players who went eleven picks ago.

The room itself knows better: the Picks panel lists every pick made, and the
Roster panel lists yours. Reading them once at startup repairs the gap.

Matching is by name rather than by id because the DOM carries names, and the
Roster panel abbreviates the first name ("C. McCaffrey"), so the two panels need
slightly different matching. Both are deliberately conservative: an unmatched
name is left alone rather than guessed at, since wrongly marking a player
drafted removes a real option from the board.
"""
import re

# The roster panel is a plain ESPN table with POS / PLAYER / BYE headers -- it
# carries no "roster" class, so it is found by its content instead. Verified
# against a live draft room.
ROSTER_PANEL_SELECTORS = [
    'table:has-text("POS"):has-text("PLAYER"):has-text("BYE")',
    '[class*="Table"]:has-text("POS"):has-text("PLAYER")',
]

# The header strip carrying round, clock and whose pick it is.
PICK_TRAIN_SELECTORS = [
    '[class*="pick-train__content"]',
    '[class*="pickTrain"]',
]


def normalise(name):
    """Lowercase, strip punctuation and suffixes, so 'James Cook III' matches."""
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", name.lower())
    return re.sub(r"[^a-z ]", "", name).strip()


def surname(name):
    parts = normalise(name).split()
    return parts[-1] if parts else ""


def first_initial(name):
    parts = normalise(name).split()
    return parts[0][0] if parts and parts[0] else ""


def names_in(text, known):
    """Which known player names appear in `text`.

    Substring matching on a normalised full name. Short names could in
    principle collide, so the full name has to appear -- a surname alone is not
    enough here.
    """
    haystack = normalise(text)
    return [n for n in known if normalise(n) and normalise(n) in haystack]


def match_abbreviated(label, known):
    """Match a Roster-panel label like 'C. McCaffrey' to a full name.

    Returns the single unambiguous match, or None. Ambiguity is left unresolved
    on purpose: two players sharing a surname and an initial is exactly the case
    where a guess would put the wrong player on your roster.
    """
    want_surname = surname(label)
    want_initial = first_initial(label)
    if not want_surname:
        return None
    hits = [n for n in known
            if surname(n) == want_surname
            and (not want_initial or first_initial(n) == want_initial)]
    return hits[0] if len(hits) == 1 else None


def panel_text(page, selectors):
    """Text of the first matching panel, or "" if none match.

    Deliberately no whole-page fallback. The page contains the full list of
    *available* players, so falling back to it would report almost everybody as
    drafted -- catastrophically worse than reporting nobody.
    """
    for selector in selectors:
        try:
            node = page.locator(selector).first
            if node.count():
                text = node.inner_text(timeout=3000)
                if text and text.strip():
                    return text
        except Exception:
            continue
    return ""


def current_pick(page):
    """(round, overall_pick, team_on_the_clock) from the header, or Nones.

    The header says "RND 7 OF 16 | 00:35 | ON THE CLOCK: PICK 66 | Some Team",
    which is more trustworthy than anything derived: the socket carries no
    round, and a board seeded mid-draft cannot count picks it never saw.
    """
    text = panel_text(page, PICK_TRAIN_SELECTORS)
    if not text:
        return None, None, None
    rnd = re.search(r"RND\s+(\d+)\s+OF", text, re.I)
    pick = re.search(r"ON THE CLOCK:\s*PICK\s+(\d+)", text, re.I)
    team = re.search(r"ON THE CLOCK:\s*PICK\s+\d+\s*\|?\s*([^|]{2,40})", text)
    return (int(rnd.group(1)) if rnd else None,
            int(pick.group(1)) if pick else None,
            team.group(1).strip() if team else None)


def my_roster_players(page, known):
    """Full names on your own roster, from the abbreviated Roster panel."""
    text = panel_text(page, ROSTER_PANEL_SELECTORS)
    found = []
    for label in re.findall(r"\b[A-Z]\.\s*[A-Z][A-Za-z'\-]+", text):
        match = match_abbreviated(label, known)
        if match and match not in found:
            found.append(match)
    # Full names appear too in some layouts, so take those as well.
    for name in names_in(text, known):
        if name not in found:
            found.append(name)
    return found
