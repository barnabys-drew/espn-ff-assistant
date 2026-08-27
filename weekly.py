"""Per-week roster projections, which `team.roster` cannot give you.

espn-api builds a team's roster for a single scoring period — whichever one
the league is currently in — so a rostered player's `stats` carries week 0
(the season total) and that one week, and nothing else. Ask for any specific
week's projection and every player you own comes back 0.0.

That fails silently rather than loudly, and it fails worst exactly where it
matters. The waiver wire ranks free agents by how much each one raises your
best starting lineup. If your own lineup projects 0.0, then every free agent
"improves" it by his own projection, the ranking collapses to raw points, and
the displacement logic that is the entire point of the tool never runs. The
free agents were fine — `free_agents(week=N)` is fetched for that week — so
the two halves of the comparison were being read from different weeks.

`box_scores(week)` is the fix. It is a public espn-api call, it returns every
rostered player with that week's `projected_points` and `eligibleSlots`, and
it also carries `slot_position` — the slot you actually have the player in,
which is what makes a start/sit report possible at all.

The cost is one request per week, so a rest-of-season number is fourteen round
trips. That is why callers are handed a progress hook rather than left to
wonder whether the thing has hung.
"""

from lineup import NON_STARTING_SLOTS, injury_flag


def box_row(player):
    """A BoxPlayer flattened into the dict shape lineup.py ranks on.

    Deliberately the same keys as lineup.as_row, so the optimiser cannot tell
    the two apart, plus `slot_now` — where ESPN currently has the player —
    which as_row has no way to know.
    """
    return {
        "name": player.name,
        "pos": player.position,
        "team": player.proTeam,
        "slots": list(getattr(player, "eligibleSlots", None) or []),
        "proj": round(float(getattr(player, "projected_points", 0) or 0), 1),
        "owned": round(float(getattr(player, "percent_owned", 0) or 0), 1),
        "injury": injury_flag(player),
        "player_id": getattr(player, "playerId", None),
        "slot_now": getattr(player, "slot_position", "") or "",
    }


def find_lineup(box_scores, team_id):
    """The one lineup in a week's box scores that belongs to `team_id`.

    None when the team does not appear, which is a real state rather than an
    error: an odd-sized league gives somebody a bye, and in the playoff weeks
    the teams that missed the bracket have no matchup at all.
    """
    for match in box_scores or []:
        if getattr(getattr(match, "home_team", None), "team_id", None) == team_id:
            return match.home_lineup
        if getattr(getattr(match, "away_team", None), "team_id", None) == team_id:
            return match.away_lineup
    return None


def week_lineup(league, team, week):
    """Your roster for one week as (rows, error).

    The failure comes back as a sentence rather than an exception because
    every caller can still say something useful without it, and because the
    most likely failure before draft night is entirely benign: ESPN has no
    rosters to build a box score from, and raises KeyError from inside
    espn-api rather than saying so.
    """
    try:
        scores = league.box_scores(week)
    except Exception as exc:
        return [], (f"ESPN would not return week {week} ({type(exc).__name__}) "
                    f"— expected before the draft, since there are no rosters "
                    f"to score yet")
    players = find_lineup(scores, getattr(team, "team_id", None))
    if players is None:
        return [], f"your team has no matchup in week {week}"
    return [box_row(p) for p in players], None


def roster_projections(league, team, weeks, on_week=None):
    """Roster rows projected over `weeks`, summed week by week.

    Returns (rows, weeks_with_data, errors). A player is carried at the
    eligibility and slot of the first week he is seen in — neither changes
    across a season in a way that changes the answer — while his projection is
    summed over every week that carries one.

    `weeks_with_data` is the honest denominator. ESPN publishes future weeks
    late and unevenly, so a fourteen-week total built from two weeks of data
    is a rest-of-season number in name only, and the caller has to be able to
    say so.
    """
    merged, order, weeks_with_data, errors = {}, [], [], []
    for week in weeks:
        if on_week:
            on_week(week)
        rows, error = week_lineup(league, team, week)
        if error:
            errors.append(error)
            continue
        if any(row["proj"] for row in rows):
            weeks_with_data.append(week)
        for row in rows:
            key = row["player_id"] if row["player_id"] is not None else row["name"]
            if key in merged:
                merged[key]["proj"] = round(merged[key]["proj"] + row["proj"], 1)
            else:
                merged[key] = dict(row)
                order.append(key)
    return [merged[key] for key in order], weeks_with_data, errors


def started(rows):
    """The rows ESPN currently has in a starting slot."""
    return [r for r in rows if r.get("slot_now") not in NON_STARTING_SLOTS]
