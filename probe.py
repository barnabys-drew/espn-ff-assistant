"""Connect to the league and dump the settings that drive draft valuation.

Run:  .venv/bin/python probe.py
Reads credentials from .env (gitignored).
"""
import os
import sys
import json


def load_env(path=".env"):
    if not os.path.exists(path):
        sys.exit(f"No {path} found. Copy .env.example to .env and fill it in.")
    env = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def main():
    env = load_env()
    missing = [k for k in ("ESPN_S2", "SWID", "LEAGUE_ID") if not env.get(k)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")

    from espn_api.football import League

    season = int(env.get("SEASON", 2026))
    league = League(
        league_id=int(env["LEAGUE_ID"]),
        year=season,
        espn_s2=env["ESPN_S2"],
        swid=env["SWID"],
    )

    s = league.settings
    print("=" * 60)
    print(f"LEAGUE: {s.name}   ({season})")
    print("=" * 60)
    print(f"Teams:        {len(league.teams)}")
    print(f"Scoring type: {s.scoring_type}")
    for attr in ("reg_season_count", "playoff_team_count", "veto_votes_required",
                 "trade_deadline", "faab", "keeper_count"):
        if hasattr(s, attr):
            print(f"{attr+':':14}{getattr(s, attr)}")

    print("\n--- ROSTER SLOTS ---")
    slots = getattr(s, "position_slot_counts", {}) or {}
    for pos, count in sorted(slots.items(), key=lambda kv: -kv[1]):
        if count:
            print(f"  {pos:10} x{count}")

    print("\n--- SCORING RULES (non-zero) ---")
    fmt = getattr(s, "scoring_format", None) or []
    for rule in fmt:
        pts = rule.get("points", 0)
        if pts:
            print(f"  {rule.get('abbr','?'):8} {pts:+g}   {rule.get('label','')}")

    print("\n--- TEAMS ---")
    for t in league.teams:
        print(f"  id={t.team_id:<3} {t.team_name}")

    print("\n--- DRAFT ---")
    draft = getattr(league, "draft", []) or []
    print(f"  picks recorded: {len(draft)}  (0 = draft has not happened yet)")

    os.makedirs("out", exist_ok=True)
    snapshot = {
        "name": s.name,
        "season": season,
        "team_count": len(league.teams),
        "scoring_type": s.scoring_type,
        "roster_slots": slots,
        "scoring_format": fmt,
        "teams": [{"id": t.team_id, "name": t.team_name} for t in league.teams],
        "draft_picks_recorded": len(draft),
    }
    with open("out/league_settings.json", "w") as fh:
        json.dump(snapshot, fh, indent=2)
    print("\nWrote out/league_settings.json")


if __name__ == "__main__":
    main()
