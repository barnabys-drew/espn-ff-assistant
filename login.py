"""Launch a real browser, let the user log into ESPN by hand, then harvest cookies.

Run:  .venv/bin/python login.py

Opens a visible Chromium with a persistent profile. Log into ESPN normally
(including any 2FA). The script polls for the espn_s2 / SWID cookies and writes
them to .env, which is gitignored. The profile persists, so subsequent runs
usually skip the login entirely.
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.abspath(".pwprofile")
START_URL = "https://www.espn.com/fantasy/football/"
TIMEOUT_S = 600


def harvest(context):
    """Return (espn_s2, swid) from the browser context, or (None, None)."""
    jar = {c["name"]: c["value"] for c in context.cookies()
           if "espn.com" in c.get("domain", "")}
    return jar.get("espn_s2"), jar.get("SWID")


def find_league_ids(context):
    """Scrape any leagueId values visible in open tabs."""
    found = set()
    for page in context.pages:
        try:
            found.update(re.findall(r"leagueId[=/](\d+)", page.url))
        except Exception:
            pass
    return sorted(found)


def write_env(espn_s2, swid, league_id=""):
    existing = {}
    if os.path.exists(".env"):
        with open(".env") as fh:
            for line in fh:
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()

    existing["ESPN_S2"] = espn_s2
    existing["SWID"] = swid
    if league_id:
        existing["LEAGUE_ID"] = league_id
    existing.setdefault("LEAGUE_ID", "")
    existing.setdefault("SEASON", "2026")

    with open(".env", "w") as fh:
        fh.write("# ESPN session cookies — harvested by login.py. Gitignored.\n")
        for k in ("ESPN_S2", "SWID", "LEAGUE_ID", "SEASON"):
            fh.write(f"{k}={existing.get(k,'')}\n")
    os.chmod(".env", 0o600)


def main():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(START_URL, wait_until="domcontentloaded")

        print("=" * 64)
        print("A browser window has opened.")
        print("Log into ESPN, then navigate to YOUR LEAGUE page.")
        print("Waiting for cookies... (Ctrl-C to abort)")
        print("=" * 64)

        deadline = time.time() + TIMEOUT_S
        s2 = swid = None
        while time.time() < deadline:
            s2, swid = harvest(context)
            if s2 and swid:
                leagues = find_league_ids(context)
                if leagues:
                    print(f"\nLogged in. Found league id(s): {', '.join(leagues)}")
                    write_env(s2, swid, leagues[0])
                    print(f"Wrote .env (LEAGUE_ID={leagues[0]}, mode 600)")
                    break
                print("\rLogged in — now open your league page to capture "
                      "the league ID...", end="", flush=True)
            time.sleep(2)
        else:
            if s2 and swid:
                write_env(s2, swid)
                print("\nWrote .env with cookies, but no LEAGUE_ID — add it manually.")
            else:
                sys.exit("\nTimed out without capturing cookies.")

        print("\nDone. You can close the browser window.")
        input("Press Enter to shut down the browser...")
        context.close()


if __name__ == "__main__":
    main()
