"""Shared helpers: read the gitignored .env and open a league connection.

Imported by probe.py and draft.py. Nothing here writes to the league.
"""
import os
import sys


def load_env(path=".env"):
    if not os.path.exists(path):
        sys.exit(f"No {path} found. Run login.py, or copy .env.example to .env.")
    env = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")

    missing = [k for k in ("ESPN_S2", "SWID", "LEAGUE_ID") if not env.get(k)]
    if missing:
        sys.exit(f"Missing in {path}: {', '.join(missing)}")
    return env


def find_my_team(league, swid=None, team_id=None, strict=True):
    """Locate the user's team by SWID, or by an explicit team_id override.

    strict=False returns None instead of exiting, for callers that can degrade
    gracefully — the draft board still works without knowing whose team is
    whose, it just cannot report lineup gain.
    """
    if team_id is not None:
        for team in league.teams:
            if team.team_id == team_id:
                return team
        if not strict:
            return None
        sys.exit(f"No team with id {team_id}. Teams: "
                 + ", ".join(f"{t.team_id}={t.team_name}" for t in league.teams))

    def norm(value):
        return str(value).strip().strip("{}").upper()

    if swid:
        want = norm(swid)
        for team in league.teams:
            for owner in (team.owners or []):
                ids = owner.values() if isinstance(owner, dict) else [owner]
                if any(norm(i) == want for i in ids):
                    return team

    if not strict:
        return None
    sys.exit("Could not tell which team is yours from the SWID in .env.\n"
             "Re-run with --team-id, using one of:\n  "
             + "\n  ".join(f"{t.team_id}  {t.team_name}" for t in league.teams))


def connect(env=None, year=None, league_id=None):
    """Return a read-only espn-api League built from .env credentials.

    year overrides SEASON from .env and league_id overrides LEAGUE_ID. Both
    exist so a throwaway target -- a past season, a mock draft -- can be read
    without editing .env and having to remember to put it back before a live
    draft.
    """
    env = env or load_env()
    from espn_api.football import League

    return League(
        league_id=int(league_id or env["LEAGUE_ID"]),
        year=int(year or env.get("SEASON", 2026)),
        espn_s2=env["ESPN_S2"],
        swid=env["SWID"],
    )
