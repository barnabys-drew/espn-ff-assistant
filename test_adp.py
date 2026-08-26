"""Unit tests for average draft position.

Run:  .venv/bin/python -m unittest test_adp -v

ADP is market data rather than a projection, so these test the reading and the
wait decision, not any claim about who is good.
"""
import unittest

import adp

# Shaped like the real kona_player_info response.
PAYLOAD = {"players": [
    {"player": {"id": 4429795, "fullName": "Jahmyr Gibbs",
                "ownership": {"averageDraftPosition": 1.44,
                              "auctionValueAverage": 66.96},
                "draftRanksByRankType": {"PPR": {"rank": 1},
                                         "STANDARD": {"rank": 2}}}},
    {"player": {"id": 4430807, "fullName": "Bijan Robinson",
                "ownership": {"averageDraftPosition": 2.5,
                              "auctionValueAverage": 65.44},
                "draftRanksByRankType": {"STANDARD": {"rank": 3}}}},
    {"player": {"id": 999, "fullName": "Undrafted Guy",
                "ownership": {"averageDraftPosition": 0}}},
    {"player": {"id": 998, "fullName": "No Ownership Block"}},
    {"noPlayer": True},
]}


class ParseAdp(unittest.TestCase):
    def setUp(self):
        self.parsed = adp.parse_adp(PAYLOAD)

    def test_reads_adp_and_auction_value(self):
        self.assertEqual(self.parsed[4429795]["adp"], 1.4)
        self.assertEqual(self.parsed[4429795]["auction"], 66.96)

    def test_prefers_the_ppr_rank_when_both_exist(self):
        self.assertEqual(self.parsed[4429795]["rank"], 1)

    def test_falls_back_to_standard_rank(self):
        self.assertEqual(self.parsed[4430807]["rank"], 3)

    def test_a_player_with_no_adp_is_omitted_not_defaulted(self):
        """Treating unknown as 'goes last' would make everyone a bargain."""
        self.assertNotIn(999, self.parsed)
        self.assertNotIn(998, self.parsed)

    def test_malformed_entries_are_skipped(self):
        self.assertEqual(len(self.parsed), 2)

    def test_empty_payloads_are_safe(self):
        for bad in (None, {}, {"players": None}, {"players": []}):
            self.assertEqual(adp.parse_adp(bad), {})


class WaitVerdict(unittest.TestCase):
    def test_a_player_going_well_after_your_next_pick_can_wait(self):
        # at pick 10, next pick 20 away (pick 30); ADP 60 is comfortably later
        self.assertTrue(adp.wait_verdict(60.0, 20, 10))

    def test_a_player_going_before_your_next_pick_cannot_wait(self):
        self.assertFalse(adp.wait_verdict(22.0, 20, 10))

    def test_an_adp_equal_to_your_next_pick_is_not_a_wait(self):
        """ADP is an average: he goes earlier half the time."""
        self.assertFalse(adp.wait_verdict(30.0, 20, 10))

    def test_a_short_wait_needs_less_cushion(self):
        # next pick is 2 away (pick 12); ADP 20 is safe
        self.assertTrue(adp.wait_verdict(20.0, 2, 10))

    def test_missing_information_yields_no_verdict(self):
        self.assertIsNone(adp.wait_verdict(None, 5, 10))
        self.assertIsNone(adp.wait_verdict(30.0, None, 10))
        self.assertIsNone(adp.wait_verdict(30.0, 5, None))


class FetchAdp(unittest.TestCase):
    class League:
        def __init__(self, payload=None, boom=False):
            self.payload, self.boom = payload, boom
            self.espn_request = self

        def league_get(self, params=None, headers=None):
            if self.boom:
                raise RuntimeError("ESPN said no")
            return self.payload

    def test_merges_across_position_slots(self):
        got = adp.fetch_adp(self.League(PAYLOAD), slot_ids=[2, 4])
        self.assertEqual(set(got), {4429795, 4430807})

    def test_a_failure_costs_the_column_not_the_tool(self):
        self.assertEqual(adp.fetch_adp(self.League(boom=True), slot_ids=[2]), {})
