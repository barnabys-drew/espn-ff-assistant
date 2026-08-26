"""Leave throwaway mock leagues, never the real one.

Run:  .venv/bin/python leaveleagues.py                 # dry run, lists them
      .venv/bin/python leaveleagues.py --confirm       # actually leaves

There is no API for this, so it drives the same "Leave This League" control a
person clicks.

**That control only exists before a draft.** Checked against a drafted league on
the team page, members, settings and league-manager views: none of them offer
it. Once a draft completes you hold a roster in an active league and leaving is
not self-service, so this is only useful for a mock joined and abandoned before
its draft. Left in place for exactly that. That makes it the only genuinely destructive thing in the repo,
and it is built to fail closed:

  * The league named by LEAGUE_ID in .env is refused unconditionally, rather
    than relying on a caller to leave it off a list. Leaving the real league
    by accident is the one failure here that would actually matter, and if
    that id cannot be read the tool refuses everything.
  * Dry run is the default; --confirm is required to click anything.
  * A league whose leave control cannot be found is skipped and reported, not
    forced through some other path.
"""
import argparse
import sys

from common import load_env

def protected_ids(env=None):
    """League ids this tool refuses to touch, whatever it is asked.

    Read from .env rather than hardcoded, so the real league is named in one
    place that is already gitignored instead of committed into source.
    """
    env = env or load_env()
    try:
        return {int(env["LEAGUE_ID"])}
    except (KeyError, TypeError, ValueError):
        # Unreadable LEAGUE_ID means we cannot tell which league to protect,
        # and refusing everything is the safe way to be wrong.
        return None

LEAVE_LINK_TEXTS = ["Leave This League", "Leave League", "Leave this league"]
CONFIRM_TEXTS = ["Leave", "Yes", "Confirm", "OK"]

LEAGUE_URLS = [
    "https://fantasy.espn.com/football/league?leagueId={lid}&seasonId={year}",
    "https://fantasy.espn.com/football/team?leagueId={lid}&seasonId={year}",
    "https://fantasy.espn.com/football/waitingroom?leagueId={lid}&seasonId={year}",
]


def find_leave_control(page):
    """The 'Leave This League' control, or None."""
    for text in LEAVE_LINK_TEXTS:
        try:
            node = page.get_by_text(text, exact=False).first
            if node.count() and node.is_visible():
                return node
        except Exception:
            continue
    return None


def leave_one(page, league_id, year, protected, dry_run=True):
    """Try to leave one league. Returns (ok, message)."""
    if protected is None or league_id in protected:
        return False, "protected — refusing"

    for template in LEAGUE_URLS:
        url = template.format(lid=league_id, year=year)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
        except Exception as exc:
            continue
        control = find_leave_control(page)
        if not control:
            continue
        if dry_run:
            return True, f"DRY RUN — would click Leave on {url.split('?')[0]}"
        try:
            control.click(timeout=8000)
            page.wait_for_timeout(2500)
            for text in CONFIRM_TEXTS:      # a confirmation dialog usually follows
                try:
                    button = page.get_by_role("button", name=text, exact=True).first
                    if button.count() and button.is_visible():
                        button.click(timeout=5000)
                        break
                except Exception:
                    continue
            page.wait_for_timeout(4000)
            return True, "left"
        except Exception as exc:
            return False, f"click failed: {type(exc).__name__}: {exc}"
    return False, "no leave control found on any league page"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("league_ids", nargs="*", type=int)
    ap.add_argument("--confirm", action="store_true",
                    help="actually leave; without this it is a dry run")
    ap.add_argument("--profile", default=".pwprofile")
    ap.add_argument("--season", type=int, default=2026)
    args = ap.parse_args()

    env = load_env()
    protected = protected_ids(env)
    if protected is None:
        sys.exit("Cannot read LEAGUE_ID from .env, so cannot tell which league "
                 "to protect. Refusing to leave anything.")
    targets = [i for i in args.league_ids if i not in protected]
    for i in args.league_ids:
        if i in protected:
            print(f"REFUSING {i}: protected", flush=True)
    if not targets:
        sys.exit("Nothing to do.")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            args.profile, headless=True, viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for lid in targets:
            ok, msg = leave_one(page, lid, args.season, protected,
                                dry_run=not args.confirm)
            print(f"{lid}: {msg}", flush=True)
        ctx.close()


if __name__ == "__main__":
    main()
