"""Unit tests for bye weeks and playoff weeks.

Run:  .venv/bin/python -m unittest test_schedule -v
"""
import types
import unittest

import schedule


def pro_team(team_id, weeks):
    return {"id": team_id,
            "proGamesByScoringPeriod": {str(w): [{}] for w in weeks}}


def fake_league(pro_teams=None, boom=False, **settings):
    def get_pro_schedule():
        if boom:
            raise RuntimeError("ESPN down")
        return {"settings": {"proTeams": pro_teams or []}}
    final = settings.pop("finalScoringPeriod", 0)
    return types.SimpleNamespace(
        settings=types.SimpleNamespace(**settings),
        finalScoringPeriod=final,
        espn_request=types.SimpleNamespace(get_pro_schedule=get_pro_schedule))


class ParseByeWeeks(unittest.TestCase):
    def test_the_single_missing_week_is_the_bye(self):
        weeks = [w for w in range(1, 15) if w != 9]
        byes = schedule.parse_bye_weeks([pro_team(1, weeks)], 14)
        self.assertEqual(byes, {"ATL": 9})

    def test_a_team_missing_several_weeks_is_skipped(self):
        """An incomplete schedule must not be reported as a bye."""
        weeks = [w for w in range(1, 15) if w not in (5, 9)]
        self.assertEqual(schedule.parse_bye_weeks([pro_team(1, weeks)], 14), {})

    def test_a_team_playing_every_week_has_no_bye(self):
        self.assertEqual(
            schedule.parse_bye_weeks([pro_team(1, range(1, 15))], 14), {})

    def test_the_placeholder_team_is_ignored(self):
        weeks = [w for w in range(1, 15) if w != 9]
        self.assertEqual(schedule.parse_bye_weeks([pro_team(0, weeks)], 14), {})

    def test_several_teams_come_back_together(self):
        byes = schedule.parse_bye_weeks(
            [pro_team(1, [w for w in range(1, 15) if w != 9]),
             pro_team(2, [w for w in range(1, 15) if w != 6])], 14)
        self.assertEqual(byes, {"ATL": 9, "BUF": 6})

    def test_unparseable_week_keys_do_not_raise(self):
        team = {"id": 1, "proGamesByScoringPeriod": {"x": [{}], "9": [{}]}}
        schedule.parse_bye_weeks([team], 14)  # must not raise

    def test_empty_input_is_safe(self):
        for teams in (None, [], [{}]):
            self.assertEqual(schedule.parse_bye_weeks(teams, 14), {})


class FetchByeWeeks(unittest.TestCase):
    def test_reads_through_the_request_layer(self):
        weeks = [w for w in range(1, 15) if w != 9]
        league = fake_league([pro_team(1, weeks)])
        self.assertEqual(schedule.fetch_bye_weeks(league, 14), {"ATL": 9})

    def test_a_failed_request_degrades_to_empty(self):
        self.assertEqual(schedule.fetch_bye_weeks(fake_league(boom=True), 14), {})


class PlayoffWeeks(unittest.TestCase):
    def test_matchup_periods_past_the_regular_season(self):
        league = fake_league(
            reg_season_count=14, playoff_team_count=4,
            matchup_periods={"14": [14], "15": [15], "16": [16], "17": [17]})
        self.assertEqual(schedule.playoff_weeks(league), [15, 16])

    def test_a_six_team_bracket_plays_three_rounds(self):
        league = fake_league(
            reg_season_count=14, playoff_team_count=6,
            matchup_periods={"15": [15], "16": [16], "17": [17]})
        self.assertEqual(schedule.playoff_weeks(league), [15, 16, 17])

    def test_a_multi_week_playoff_period_returns_every_week(self):
        league = fake_league(
            reg_season_count=13, playoff_team_count=2,
            matchup_periods={"14": [14, 15]})
        self.assertEqual(schedule.playoff_weeks(league), [14, 15])

    def test_falls_back_to_everything_after_the_regular_season(self):
        league = fake_league(reg_season_count=14, playoff_team_count=4,
                             matchup_periods={}, finalScoringPeriod=17)
        self.assertEqual(schedule.playoff_weeks(league), [15, 16, 17])

    def test_no_regular_season_length_means_no_answer(self):
        self.assertEqual(schedule.playoff_weeks(fake_league()), [])

    def test_unparseable_periods_are_skipped(self):
        league = fake_league(reg_season_count=14, playoff_team_count=4,
                             matchup_periods={"x": [99], "15": [15]},
                             finalScoringPeriod=0)
        self.assertEqual(schedule.playoff_weeks(league), [15])


class ByeLoad(unittest.TestCase):
    BYES = {"ATL": 9, "BUF": 9, "CHI": 6}

    def rows(self, *teams):
        return [{"team": t} for t in teams]

    def test_counts_your_players_out_in_each_week(self):
        load = schedule.bye_load(self.rows("ATL", "BUF", "CHI"), self.BYES)
        self.assertEqual(load, {9: 2, 6: 1})

    def test_a_team_with_no_known_bye_is_ignored(self):
        self.assertEqual(schedule.bye_load(self.rows("ATL", "ZZZ"), self.BYES),
                         {9: 1})

    def test_an_empty_roster_has_no_load(self):
        self.assertEqual(schedule.bye_load([], self.BYES), {})


if __name__ == "__main__":
    unittest.main()
