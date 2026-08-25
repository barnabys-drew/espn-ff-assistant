"""Unit tests for roster position caps.

Run:  .venv/bin/python -m unittest test_rosterlimits -v
"""
import unittest

import rosterlimits

# Captured from a live 10-team league. -1 means no limit.
REAL = {"positionLimits": {"0": 0, "1": 4, "2": 8, "3": 8, "4": 3, "5": 3,
                           "6": -1, "16": 3, "17": -1}}


class ParseLimits(unittest.TestCase):
    def test_reads_the_real_settings_block(self):
        self.assertEqual(rosterlimits.parse_limits(REAL),
                         {"QB": 4, "RB": 8, "WR": 8, "TE": 3, "K": 3, "D/ST": 3})

    def test_no_limit_is_not_a_limit(self):
        self.assertNotIn("TE", rosterlimits.parse_limits(
            {"positionLimits": {"4": -1}}))

    def test_a_zero_cap_is_ignored_rather_than_blocking_everything(self):
        """Position id 0 comes back as 0 and is not a real position."""
        self.assertEqual(rosterlimits.parse_limits(
            {"positionLimits": {"0": 0}}), {})

    def test_unknown_position_ids_are_skipped(self):
        self.assertEqual(rosterlimits.parse_limits(
            {"positionLimits": {"99": 5}}), {})

    def test_junk_is_survivable(self):
        for bad in (None, {}, {"positionLimits": None},
                    {"positionLimits": {"x": "y"}}):
            self.assertEqual(rosterlimits.parse_limits(bad), {})


class FullPositions(unittest.TestCase):
    LIMITS = {"QB": 4, "RB": 8, "TE": 3}

    def roster(self, **by_pos):
        return [{"pos": p} for p, n in by_pos.items() for _ in range(n)]

    def test_a_position_at_its_cap_is_full(self):
        self.assertEqual(
            rosterlimits.full_positions(self.roster(TE=3), self.LIMITS), {"TE"})

    def test_below_the_cap_is_not_full(self):
        self.assertEqual(
            rosterlimits.full_positions(self.roster(TE=2), self.LIMITS), set())

    def test_over_the_cap_still_counts_as_full(self):
        self.assertIn("TE", rosterlimits.full_positions(
            self.roster(TE=5), self.LIMITS))

    def test_an_empty_roster_fills_nothing(self):
        self.assertEqual(rosterlimits.full_positions([], self.LIMITS), set())

    def test_positions_without_a_cap_are_never_full(self):
        self.assertEqual(
            rosterlimits.full_positions(self.roster(WR=99), self.LIMITS), set())

    def test_counts_tallies_by_position(self):
        self.assertEqual(rosterlimits.counts(self.roster(RB=2, WR=3)),
                         {"RB": 2, "WR": 3})
