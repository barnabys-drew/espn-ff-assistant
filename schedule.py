"""The league calendar: bye weeks and which weeks the playoffs are played in.

Both answer questions the season-total projection cannot. A season total says
nothing about whether two of your running backs sit out the same week, and
nothing about whether a player is any use in the weeks that decide the title.

Bye weeks come from the pro schedule rather than from player objects on
purpose. free_agents() builds its players with _get_pro_schedule(week) — a
single week — so a free agent's own `schedule` covers one week and cannot show
a bye. The pro-schedule endpoint carries the whole season for every team, in
one request.
"""
import math

from espn_api.football.constant import PRO_TEAM_MAP


def parse_bye_weeks(pro_teams, through_week):
    """{team_abbrev: bye_week} from the raw proTeams block.

    A bye is the one week in the regular season with no scheduled game. If a
    team is missing more than one week the schedule is incomplete rather than
    telling us about a bye, so that team is left out entirely — a wrong bye
    week is worse than no bye week.
    """
    byes = {}
    for team in pro_teams or []:
        abbrev = PRO_TEAM_MAP.get(team.get("id"))
        if not abbrev or abbrev == "None":
            continue
        played = set()
        for key in (team.get("proGamesByScoringPeriod") or {}):
            try:
                played.add(int(key))
            except (TypeError, ValueError):
                continue
        missing = [w for w in range(1, through_week + 1) if w not in played]
        if len(missing) == 1:
            byes[abbrev] = missing[0]
    return byes


def fetch_bye_weeks(league, through_week):
    """Bye weeks for every pro team, or {} if the schedule cannot be read."""
    try:
        data = league.espn_request.get_pro_schedule() or {}
    except Exception:
        return {}
    pro_teams = (data.get("settings") or {}).get("proTeams") or []
    return parse_bye_weeks(pro_teams, through_week)


def playoff_weeks(league):
    """The scoring periods your league's playoffs are played in.

    Matchup periods past the regular season are the playoff rounds; how many of
    them count is set by the bracket size, since ESPN may define more periods
    than a league actually plays. Falls back to everything after the regular
    season when the matchup periods are missing or unparseable.
    """
    settings = getattr(league, "settings", None)
    reg = int(getattr(settings, "reg_season_count", 0) or 0)
    if not reg:
        return []

    bracket = int(getattr(settings, "playoff_team_count", 0) or 0)
    rounds = max(1, math.ceil(math.log2(bracket))) if bracket > 1 else 1

    after = []
    for period, weeks in (getattr(settings, "matchup_periods", None) or {}).items():
        try:
            number = int(period)
        except (TypeError, ValueError):
            continue
        if number > reg:
            parsed = []
            for week in (weeks or []):
                try:
                    parsed.append(int(week))
                except (TypeError, ValueError):
                    continue
            after.append((number, parsed))

    after.sort()
    chosen = sorted({w for _, weeks in after[:rounds] for w in weeks})
    if chosen:
        return chosen

    final = int(getattr(league, "finalScoringPeriod", 0) or 0)
    return list(range(reg + 1, final + 1)) if final > reg else []


def bye_load(rows, byes):
    """{week: how many of these players are on bye that week}.

    Run over your own roster, this is what makes a candidate's bye week
    meaningful: week 9 matters if two of your starters are already out then.
    """
    counts = {}
    for row in rows:
        week = byes.get(row.get("team"))
        if week:
            counts[week] = counts.get(week, 0) + 1
    return counts
