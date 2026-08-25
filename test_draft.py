"""Unit tests for the draft valuation math.

Run:  .venv/bin/python -m unittest test_draft -v

These cover the parts that decide the board's ordering — slot parsing, flex
allocation, replacement level, tiering — without touching the network.
"""
import unittest

import draft


def players(pos, projections):
    return [{"name": f"{pos}{i}", "pos": pos, "proj": p} for i, p in enumerate(projections, 1)]


class ParseSlots(unittest.TestCase):
    def test_scales_fixed_slots_by_team_count(self):
        base, flex = draft.parse_slots({"QB": 1, "RB": 2, "WR": 2}, 12)
        self.assertEqual(base, {"QB": 12, "RB": 24, "WR": 24})
        self.assertEqual(flex, [])

    def test_dst_is_a_position_not_a_flex(self):
        base, flex = draft.parse_slots({"D/ST": 1}, 10)
        self.assertEqual(base, {"D/ST": 10})
        self.assertEqual(flex, [])

    def test_combo_slot_becomes_flex_demand(self):
        base, flex = draft.parse_slots({"RB/WR/TE": 1}, 12)
        self.assertEqual(base, {})
        self.assertEqual(flex, [(["RB", "WR", "TE"], 12)])

    def test_superflex_includes_quarterbacks(self):
        _, flex = draft.parse_slots({"OP": 1}, 8)
        self.assertEqual(flex, [(["QB", "RB", "WR", "TE"], 8)])

    def test_bench_and_ir_create_no_demand(self):
        base, flex = draft.parse_slots({"BE": 7, "IR": 1, "QB": 1}, 12)
        self.assertEqual(base, {"QB": 12})
        self.assertEqual(flex, [])

    def test_empty_slots_are_ignored(self):
        base, flex = draft.parse_slots({"TE": 0, "": 3, "RB": 1}, 10)
        self.assertEqual(base, {"RB": 10})
        self.assertEqual(flex, [])


class AllocateFlex(unittest.TestCase):
    def setUp(self):
        # Two RBs project above the best remaining WR, so the flex skews RB.
        self.pool = {
            "RB": players("RB", [200, 190, 180, 170, 100]),
            "WR": players("WR", [195, 185, 175, 90, 80]),
        }

    def test_no_flex_leaves_base_untouched(self):
        startable = draft.allocate_flex(self.pool, {"RB": 2, "WR": 2}, [])
        self.assertEqual(startable, {"RB": 2, "WR": 2})

    def test_flex_goes_to_the_best_players_past_the_starters(self):
        # Past 2 RB / 2 WR starters, the best left are RB3 (180) and WR3 (175).
        startable = draft.allocate_flex(self.pool, {"RB": 2, "WR": 2},
                                        [(["RB", "WR"], 2)])
        self.assertEqual(startable, {"RB": 3, "WR": 3})

    def test_a_single_flex_slot_goes_to_the_single_best_leftover(self):
        startable = draft.allocate_flex(self.pool, {"RB": 2, "WR": 2},
                                        [(["RB", "WR"], 1)])
        self.assertEqual(startable, {"RB": 3, "WR": 2})

    def test_flex_never_reuses_a_fixed_starter(self):
        # 4 flex slots over a pool where only 3 non-starters exist per position.
        startable = draft.allocate_flex(self.pool, {"RB": 2, "WR": 2},
                                        [(["RB", "WR"], 4)])
        self.assertEqual(startable["RB"] + startable["WR"], 8)
        self.assertLessEqual(startable["RB"], 5)
        self.assertLessEqual(startable["WR"], 5)

    def test_narrow_slots_are_filled_before_wide_ones(self):
        pool = dict(self.pool, TE=players("TE", [150, 140, 60]))
        startable = draft.allocate_flex(
            pool, {"RB": 2, "WR": 2, "TE": 1},
            [(["RB", "WR", "TE"], 1), (["WR", "TE"], 1)],
        )
        # WR/TE resolves first and takes WR3 (175); the wider slot then takes
        # the best of what is left, RB3 (180).
        self.assertEqual(startable, {"RB": 3, "WR": 3, "TE": 1})


class ReplacementLevels(unittest.TestCase):
    def test_baseline_is_the_first_player_past_the_starters(self):
        pool = {"RB": players("RB", [200, 190, 180, 170])}
        self.assertEqual(draft.replacement_levels(pool, {"RB": 2}), {"RB": 180})

    def test_falls_back_to_the_last_player_when_the_pool_is_short(self):
        pool = {"K": players("K", [130, 125])}
        self.assertEqual(draft.replacement_levels(pool, {"K": 10}), {"K": 125})

    def test_empty_position_is_omitted(self):
        self.assertEqual(draft.replacement_levels({"TE": []}, {"TE": 12}), {})


class AssignTiers(unittest.TestCase):
    def test_breaks_where_the_cliff_is(self):
        rows = [{"vor": v} for v in [100, 98, 96, 40, 38, 36]]
        draft.assign_tiers(rows)
        self.assertEqual([r["tier"] for r in rows], [1, 1, 1, 2, 2, 2])

    def test_even_spacing_stays_one_tier(self):
        rows = [{"vor": v} for v in [100, 90, 80, 70, 60]]
        draft.assign_tiers(rows)
        self.assertEqual({r["tier"] for r in rows}, {1})

    def test_short_and_empty_lists_are_safe(self):
        rows = [{"vor": 10}, {"vor": 5}]
        draft.assign_tiers(rows)
        self.assertEqual([r["tier"] for r in rows], [1, 1])
        draft.assign_tiers([])  # must not raise

    def test_identical_values_stay_one_tier(self):
        rows = [{"vor": 50} for _ in range(4)]
        draft.assign_tiers(rows)
        self.assertEqual({r["tier"] for r in rows}, {1})


class EndToEndOrdering(unittest.TestCase):
    """The whole point: VOR reorders across positions, projections alone don't."""

    def test_a_lower_projected_rb_can_outrank_a_higher_projected_qb(self):
        pool = {
            "QB": players("QB", [340, 335, 330, 325]),
            "RB": players("RB", [300, 200, 190, 180]),
        }
        base, flex = draft.parse_slots({"QB": 1, "RB": 2}, 2)
        startable = draft.allocate_flex(pool, base, flex)
        levels = draft.replacement_levels(pool, startable)

        qb1 = pool["QB"][0]["proj"] - levels["QB"]   # 340 - 330 = 10
        rb1 = pool["RB"][0]["proj"] - levels["RB"]   # 300 - 190 = 110
        self.assertGreater(rb1, qb1)


if __name__ == "__main__":
    unittest.main()
