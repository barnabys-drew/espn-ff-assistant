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


def connect(env=None):
    """Return a read-only espn-api League built from .env credentials."""
    env = env or load_env()
    from espn_api.football import League

    return League(
        league_id=int(env["LEAGUE_ID"]),
        year=int(env.get("SEASON", 2026)),
        espn_s2=env["ESPN_S2"],
        swid=env["SWID"],
    )
