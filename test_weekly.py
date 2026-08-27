"""Unit tests for per-week roster projections.

Run:  .venv/bin/python -m unittest test_weekly -v

Same approach as the rest: fakes standing in for espn-api, no network and no
credentials. The bug this module exists to fix is reproduced directly, so a
regression fails here rather than during a waiver run in October.
"""
import unittest

import lineup
import weekly


class FakePlayer:
    """A BoxPlayer as far as box_row is concerned."""

    def __init__(self, name, pos, team="FA", proj=0.0, slot="BE", slots=None,
                 injury="ACTIVE", pid=None, owned=0.0):
        self.name = name
        self.position = pos
        self.proTeam = team
        self.projected_points = proj
        self.slot_position = slot
        self.eligibleSlots = slots if slots is not None else [pos, "BE"]
        self.injuryStatus = injury
        self.playerId = pid
        self.percent_owned = owned


class FakeTeam:
    def __init__(self, team_id):
        self.team_id = team_id


class FakeMatch:
    def __init__(self, home_id, away_id, home_lineup, away_lineup):
        self.home_team = FakeTeam(home_id)
        self.away_team = FakeTeam(away_id)
        self.home_lineup = home_lineup
        self.away_lineup = away_lineup


class FakeLeague:
    """box_scores keyed by week; anything else raises, as ESPN does."""

    def __init__(self, by_week, team_id=1):
        self.by_week = by_week
        self.team_id = team_id
        self.calls = []

    def box_scores(self, week):
        self.calls.append(week)
        if week not in self.by_week:
            raise KeyError("rosterForCurrentScoringPeriod")
        return self.by_week[week]


def league_with(week_projections, team_id=1):
    """A league where our team's one player carries a different proj each week."""
    by_week = {}
    for week, proj in week_projections.items():
        mine = [FakePlayer("Mine", "RB", proj=proj, slot="RB", pid=7)]
        theirs = [FakePlayer("Theirs", "WR", proj=99.0, slot="WR", pid=8)]
        by_week[week] = [FakeMatch(team_id, 2, mine, theirs)]
    return FakeLeague(by_week, team_id)


class BoxRow(unittest.TestCase):
    def test_it_produces_the_same_keys_the_optimiser_reads(self):
        row = weekly.box_row(FakePlayer("A", "RB", proj=10.0))
        for key in ("name", "pos", "team", "slots", "proj", "owned", "injury",
                    "player_id"):
            self.assertIn(key, row)

    def test_it_carries_the_slot_the_player_is_actually_in(self):
        row = weekly.box_row(FakePlayer("A", "RB", slot="RB/WR/TE"))
        self.assertEqual(row["slot_now"], "RB/WR/TE")

    def test_active_is_not_an_injury(self):
        self.assertEqual(weekly.box_row(FakePlayer("A", "RB"))["injury"], "")

    def test_a_defence_reporting_normal_is_not_an_injury_either(self):
        # ESPN says NORMAL for a D/ST rather than ACTIVE. Surfacing it put a
        # status flag on a healthy defence every single week.
        player = FakePlayer("Broncos D/ST", "D/ST", injury="NORMAL")
        self.assertEqual(weekly.box_row(player)["injury"], "")

    def test_a_real_status_still_comes_through(self):
        player = FakePlayer("A", "RB", injury="OUT")
        self.assertEqual(weekly.box_row(player)["injury"], "OUT")

    def test_a_missing_projection_is_zero_rather_than_none(self):
        player = FakePlayer("A", "RB")
        player.projected_points = None
        self.assertEqual(weekly.box_row(player)["proj"], 0.0)


class FindLineup(unittest.TestCase):
    def setUp(self):
        self.home = [FakePlayer("H", "RB")]
        self.away = [FakePlayer("A", "WR")]
        self.scores = [FakeMatch(1, 2, self.home, self.away)]

    def test_it_finds_the_home_side(self):
        self.assertIs(weekly.find_lineup(self.scores, 1), self.home)

    def test_it_finds_the_away_side(self):
        self.assertIs(weekly.find_lineup(self.scores, 2), self.away)

    def test_a_team_with_no_matchup_is_none_not_an_error(self):
        self.assertIsNone(weekly.find_lineup(self.scores, 99))

    def test_no_box_scores_at_all_is_none(self):
        self.assertIsNone(weekly.find_lineup(None, 1))


class WeekLineup(unittest.TestCase):
    def test_it_reads_the_requested_week(self):
        league = league_with({5: 12.0, 6: 3.0})
        rows, error = weekly.week_lineup(league, FakeTeam(1), 6)
        self.assertIsNone(error)
        self.assertEqual(rows[0]["proj"], 3.0)

    def test_espn_raising_becomes_a_sentence_not_an_exception(self):
        league = league_with({5: 12.0})
        rows, error = weekly.week_lineup(league, FakeTeam(1), 9)
        self.assertEqual(rows, [])
        self.assertIn("week 9", error)
        self.assertIn("KeyError", error)

    def test_the_predraft_failure_explains_itself(self):
        league = league_with({})
        _, error = weekly.week_lineup(league, FakeTeam(1), 1)
        self.assertIn("before the draft", error)

    def test_a_team_absent_from_the_week_is_reported_as_such(self):
        league = league_with({5: 12.0}, team_id=1)
        rows, error = weekly.week_lineup(league, FakeTeam(42), 5)
        self.assertEqual(rows, [])
        self.assertIn("no matchup", error)


class RosterProjections(unittest.TestCase):
    def test_projections_are_summed_across_the_weeks(self):
        league = league_with({1: 10.0, 2: 5.5, 3: 4.5})
        rows, weeks, errors = weekly.roster_projections(
            league, FakeTeam(1), [1, 2, 3])
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["proj"], 20.0)

    def test_only_weeks_carrying_data_are_counted_as_covered(self):
        league = league_with({1: 10.0, 2: 0.0, 3: 4.0})
        _, weeks, _ = weekly.roster_projections(league, FakeTeam(1), [1, 2, 3])
        self.assertEqual(weeks, [1, 3])

    def test_an_unreadable_week_is_skipped_and_reported(self):
        league = league_with({1: 10.0, 3: 4.0})
        rows, weeks, errors = weekly.roster_projections(
            league, FakeTeam(1), [1, 2, 3])
        self.assertEqual(rows[0]["proj"], 14.0)
        self.assertEqual(weeks, [1, 3])
        self.assertEqual(len(errors), 1)

    def test_a_player_added_late_accumulates_only_from_when_he_appears(self):
        early = [FakePlayer("Old", "RB", proj=8.0, pid=1)]
        late = [FakePlayer("Old", "RB", proj=8.0, pid=1),
                FakePlayer("New", "WR", proj=6.0, pid=2)]
        league = FakeLeague({1: [FakeMatch(1, 2, early, [])],
                             2: [FakeMatch(1, 2, late, [])]})
        rows, _, _ = weekly.roster_projections(league, FakeTeam(1), [1, 2])
        by_name = {r["name"]: r["proj"] for r in rows}
        self.assertEqual(by_name, {"Old": 16.0, "New": 6.0})

    def test_the_progress_hook_sees_every_week(self):
        league = league_with({1: 1.0, 2: 2.0})
        seen = []
        weekly.roster_projections(league, FakeTeam(1), [1, 2], on_week=seen.append)
        self.assertEqual(seen, [1, 2])

    def test_players_without_ids_are_merged_by_name(self):
        one = [FakePlayer("Nameless", "RB", proj=3.0, pid=None)]
        two = [FakePlayer("Nameless", "RB", proj=4.0, pid=None)]
        league = FakeLeague({1: [FakeMatch(1, 2, one, [])],
                             2: [FakeMatch(1, 2, two, [])]})
        rows, _, _ = weekly.roster_projections(league, FakeTeam(1), [1, 2])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["proj"], 7.0)


class Started(unittest.TestCase):
    def test_bench_and_ir_do_not_count_as_started(self):
        rows = [{"slot_now": "RB"}, {"slot_now": "BE"}, {"slot_now": "IR"},
                {"slot_now": "RB/WR/TE"}]
        self.assertEqual(len(weekly.started(rows)), 2)


class TheBugThisModuleFixes(unittest.TestCase):
    """The failure that made a full roster look identical to an empty one."""

    def test_team_roster_stats_cannot_answer_for_an_arbitrary_week(self):
        # A rostered player as espn-api actually builds him: week 0 for the
        # season, plus whichever week the league is currently in.
        class Rostered:
            name, position, proTeam = "Star RB", "RB", "BAL"
            eligibleSlots = ["RB", "BE"]
            projected_total_points = 272.8
            percent_owned = 99.9
            injuryStatus = "ACTIVE"
            playerId = 1
            stats = {0: {"projected_points": 272.8},
                     18: {"projected_points": 15.3}}

        self.assertEqual(lineup.as_row(Rostered(), [10])["proj"], 0.0)

    def test_a_zero_lineup_makes_every_gain_equal_the_players_own_points(self):
        slots = {"RB": 1}
        roster = [{"name": "Mine", "pos": "RB", "slots": ["RB", "BE"],
                   "proj": 0.0}]
        candidate = {"name": "FA", "pos": "RB", "slots": ["RB", "BE"],
                     "proj": 12.6}
        self.assertEqual(
            lineup.gain_from_adding(roster, candidate, slots), 12.6)

    def test_reading_the_same_week_for_both_restores_the_displacement(self):
        # The same roster read properly: the free agent is only worth the
        # difference, not his whole projection.
        roster = [{"name": "Mine", "pos": "RB", "slots": ["RB", "BE"],
                   "proj": 11.0}]
        candidate = {"name": "FA", "pos": "RB", "slots": ["RB", "BE"],
                     "proj": 12.6}
        self.assertEqual(
            lineup.gain_from_adding(roster, candidate, {"RB": 1}), 1.6)


if __name__ == "__main__":
    unittest.main()
