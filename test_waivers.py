"""Unit tests for the waiver-wire ranking.

Run:  .venv/bin/python -m unittest test_waivers -v

Same approach as test_draft: the parts that decide the ranking, exercised
against fixtures, with no network and no credentials. The lineup optimiser is
the piece worth trusting, since every gain number is a difference of two of its
outputs.
"""
import types
import unittest

import waivers


def row(name, pos, proj, slots=None, owned=0.0, injury=""):
    return {"name": name, "pos": pos, "team": "FA", "proj": proj,
            "slots": slots if slots is not None else [pos, "BE"],
            "owned": owned, "injury": injury, "player_id": None}


def fake_player(name="X", pos="RB", stats=None, season_total=0.0, slots=None):
    return types.SimpleNamespace(
        name=name, position=pos, proTeam="BUF",
        stats=stats or {}, projected_total_points=season_total,
        eligibleSlots=slots if slots is not None else [pos, "BE"],
        percent_owned=12.5, injuryStatus="ACTIVE", playerId=1,
    )


class StartingSlots(unittest.TestCase):
    def test_counts_are_per_team_not_league_wide(self):
        settings = types.SimpleNamespace(
            position_slot_counts={"QB": 1, "RB": 2, "WR": 2, "BE": 7})
        self.assertEqual(waivers.starting_slots(settings),
                         {"QB": 1, "RB": 2, "WR": 2})

    def test_bench_ir_and_empty_slots_are_not_starting_slots(self):
        settings = types.SimpleNamespace(
            position_slot_counts={"BE": 6, "IR": 2, "": 1, "TE": 0, "K": 1})
        self.assertEqual(waivers.starting_slots(settings), {"K": 1})

    def test_missing_settings_attribute_is_survivable(self):
        self.assertEqual(waivers.starting_slots(types.SimpleNamespace()), {})


class Projection(unittest.TestCase):
    def test_sums_only_the_requested_weeks(self):
        p = fake_player(stats={1: {"projected_points": 10.0},
                               2: {"projected_points": 12.0},
                               3: {"projected_points": 14.0}})
        self.assertEqual(waivers.projection(p, [2, 3]), 26.0)

    def test_a_bye_week_contributes_nothing(self):
        # Week 2 absent entirely — that is what a bye looks like in the payload.
        p = fake_player(stats={1: {"projected_points": 10.0},
                               3: {"projected_points": 14.0}})
        self.assertEqual(waivers.projection(p, [1, 2, 3]), 24.0)

    def test_none_weeks_falls_back_to_the_season_total(self):
        p = fake_player(stats={1: {"projected_points": 10.0}}, season_total=180.0)
        self.assertEqual(waivers.projection(p, None), 180.0)

    def test_rest_of_season_excludes_games_already_played(self):
        """The whole reason this does not use projected_total_points."""
        p = fake_player(stats={w: {"projected_points": 10.0} for w in range(1, 8)},
                        season_total=70.0)
        self.assertEqual(waivers.projection(p, [5, 6, 7]), 30.0)
        self.assertNotEqual(waivers.projection(p, [5, 6, 7]), p.projected_total_points)

    def test_null_projection_is_treated_as_zero(self):
        p = fake_player(stats={1: {"projected_points": None}})
        self.assertEqual(waivers.projection(p, [1]), 0.0)


class BestLineup(unittest.TestCase):
    def names(self, rows, used):
        return {r["name"] for r in rows if id(r) in used}

    def test_each_slot_takes_the_best_eligible_player(self):
        rows = [row("RB1", "RB", 20), row("RB2", "RB", 15), row("RB3", "RB", 5)]
        total, used = waivers.best_lineup(rows, {"RB": 2})
        self.assertEqual(total, 35.0)
        self.assertEqual(self.names(rows, used), {"RB1", "RB2"})

    def test_a_player_cannot_fill_two_slots(self):
        rows = [row("RB1", "RB", 20, ["RB", "RB/WR/TE"])]
        total, used = waivers.best_lineup(rows, {"RB": 1, "RB/WR/TE": 1})
        self.assertEqual(total, 20.0)
        self.assertEqual(len(used), 1)

    def test_flex_does_not_steal_the_only_player_for_a_dedicated_slot(self):
        """Scarcity ordering: one TE who is also flex-eligible must start at TE."""
        rows = [row("TE1", "TE", 14, ["TE", "RB/WR/TE"]),
                row("RB1", "RB", 12, ["RB", "RB/WR/TE"])]
        total, used = waivers.best_lineup(rows, {"TE": 1, "RB/WR/TE": 1})
        self.assertEqual(total, 26.0)
        self.assertEqual(self.names(rows, used), {"TE1", "RB1"})

    def test_an_unfillable_slot_is_left_empty_rather_than_raising(self):
        rows = [row("RB1", "RB", 20)]
        total, _ = waivers.best_lineup(rows, {"RB": 1, "K": 1})
        self.assertEqual(total, 20.0)

    def test_superflex_can_take_a_quarterback(self):
        rows = [row("QB1", "QB", 25, ["QB", "OP"]),
                row("QB2", "QB", 22, ["QB", "OP"]),
                row("RB1", "RB", 14, ["RB", "OP"])]
        total, used = waivers.best_lineup(rows, {"QB": 1, "OP": 1})
        self.assertEqual(total, 47.0)
        self.assertEqual(self.names(rows, used), {"QB1", "QB2"})

    def test_empty_roster_scores_nothing(self):
        self.assertEqual(waivers.best_lineup([], {"RB": 2}), (0.0, set()))


class RankAdds(unittest.TestCase):
    def setUp(self):
        self.slots = {"RB": 1, "WR": 1}
        self.roster = [row("MyRB", "RB", 10), row("MyWR", "WR", 12),
                       row("Benched", "RB", 3)]

    def test_gain_is_what_the_add_displaces_not_the_raw_projection(self):
        fa = row("BigRB", "RB", 18)
        ranked, base, _ = waivers.rank_adds(self.roster, [fa], self.slots)
        self.assertEqual(base, 22.0)             # 10 + 12
        self.assertEqual(ranked[0]["gain"], 8.0)  # 18 replaces MyRB's 10
        self.assertNotEqual(ranked[0]["gain"], fa["proj"])

    def test_a_player_who_cannot_crack_the_lineup_is_omitted(self):
        ranked, _, _ = waivers.rank_adds(self.roster, [row("ScrubRB", "RB", 4)],
                                         self.slots)
        self.assertEqual(ranked, [])

    def test_results_are_ordered_by_gain(self):
        fas = [row("A", "RB", 14), row("B", "WR", 20), row("C", "RB", 11)]
        ranked, _, _ = waivers.rank_adds(self.roster, fas, self.slots)
        self.assertEqual([r["name"] for r in ranked], ["B", "A", "C"])
        self.assertEqual([r["rank"] for r in ranked], [1, 2, 3])

    def test_drop_is_the_weakest_player_not_in_the_lineup(self):
        ranked, _, drop = waivers.rank_adds(self.roster, [row("BigRB", "RB", 18)],
                                            self.slots)
        self.assertEqual(drop["name"], "Benched")
        self.assertEqual(ranked[0]["drop"], "Benched")

    def test_a_bye_week_hole_makes_a_modest_player_valuable(self):
        """MyWR is on bye (0 this week), so any real WR is a large gain."""
        roster = [row("MyRB", "RB", 10), row("MyWR", "WR", 0)]
        ranked, _, _ = waivers.rank_adds(roster, [row("AnyWR", "WR", 9)], self.slots)
        self.assertEqual(ranked[0]["gain"], 9.0)

    def test_limit_truncates_but_keeps_the_best(self):
        fas = [row(f"P{i}", "RB", 11 + i) for i in range(5)]
        ranked, _, _ = waivers.rank_adds(self.roster, fas, self.slots, limit=2)
        self.assertEqual([r["name"] for r in ranked], ["P4", "P3"])

    def test_empty_wire_is_safe(self):
        ranked, base, _ = waivers.rank_adds(self.roster, [], self.slots)
        self.assertEqual((ranked, base), ([], 22.0))


class FindMyTeam(unittest.TestCase):
    def league(self, owners):
        teams = [types.SimpleNamespace(team_id=1, team_name="Theirs", owners=[]),
                 types.SimpleNamespace(team_id=7, team_name="Mine", owners=owners)]
        return types.SimpleNamespace(teams=teams)

    def test_matches_a_dict_owner_by_swid(self):
        lg = self.league([{"id": "{ABC-123}", "displayName": "someone"}])
        self.assertEqual(waivers.find_my_team(lg, swid="{ABC-123}").team_id, 7)

    def test_matching_ignores_braces_and_case(self):
        lg = self.league([{"id": "{abc-123}"}])
        self.assertEqual(waivers.find_my_team(lg, swid="ABC-123").team_id, 7)

    def test_matches_a_plain_string_owner(self):
        lg = self.league(["{ABC-123}"])
        self.assertEqual(waivers.find_my_team(lg, swid="{ABC-123}").team_id, 7)

    def test_team_id_overrides_the_swid_lookup(self):
        lg = self.league([{"id": "{ABC-123}"}])
        self.assertEqual(waivers.find_my_team(lg, swid="{ABC-123}",
                                              team_id=1).team_name, "Theirs")

    def test_an_unknown_swid_exits_listing_the_teams(self):
        lg = self.league([{"id": "{ABC-123}"}])
        with self.assertRaises(SystemExit) as cm:
            waivers.find_my_team(lg, swid="{NOPE}")
        self.assertIn("--team-id", str(cm.exception))

    def test_an_unknown_team_id_exits(self):
        with self.assertRaises(SystemExit):
            waivers.find_my_team(self.league([]), team_id=99)


class ScoringWeeks(unittest.TestCase):
    def league(self, current, reg_season=17):
        return types.SimpleNamespace(
            current_week=current,
            settings=types.SimpleNamespace(reg_season_count=reg_season))

    def test_an_explicit_week_is_used_alone(self):
        self.assertEqual(waivers.scoring_weeks(self.league(3), 9), [9])

    def test_default_is_the_current_week_through_the_end(self):
        self.assertEqual(waivers.scoring_weeks(self.league(15), None), [15, 16, 17])

    def test_preseason_week_zero_starts_at_one(self):
        self.assertEqual(waivers.scoring_weeks(self.league(0, 14), None),
                         list(range(1, 15)))

    def test_past_the_final_week_still_returns_something(self):
        self.assertEqual(waivers.scoring_weeks(self.league(18, 17), None), [18])


if __name__ == "__main__":
    unittest.main()
