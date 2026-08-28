"""Unit tests for transaction payloads.

Run:  .venv/bin/python -m unittest test_transactions -v

These bodies mutate a real league, so they are tested field by field rather
than by shape. Nothing here touches the network — building a payload is inert
by design, which is what makes this level of coverage affordable.
"""
import json
import unittest

import transactions

SWID = "{AAAA1111-2222-3333-4444-BBBBCCCCDDDD}"


def row(name, pos, player_id, slot_now="BE", proj=0.0, team="FA"):
    return {"name": name, "pos": pos, "team": team, "proj": proj,
            "slots": [pos, "BE"], "owned": 0.0, "injury": "",
            "player_id": player_id, "slot_now": slot_now, "note": ""}


class SlotIds(unittest.TestCase):
    """The shipped reverse map is unusable; these are the cases that prove it."""

    def test_the_flex_is_known_by_the_name_espn_actually_returns(self):
        self.assertEqual(transactions.slot_id("RB/WR/TE"), 23)

    def test_the_bench_has_an_id(self):
        self.assertEqual(transactions.slot_id("BE"), 20)

    def test_injured_reserve_has_an_id(self):
        self.assertEqual(transactions.slot_id("IR"), 21)

    def test_the_ordinary_slots_are_right(self):
        for name, number in [("QB", 0), ("RB", 2), ("WR", 4), ("TE", 6),
                             ("D/ST", 16), ("K", 17)]:
            self.assertEqual(transactions.slot_id(name), number, name)

    def test_the_empty_slot_name_is_not_a_slot(self):
        self.assertNotIn("", transactions.SLOT_IDS)

    def test_an_unknown_slot_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            transactions.slot_id("BENCH")

    def test_the_error_lists_what_would_have_worked(self):
        with self.assertRaises(ValueError) as caught:
            transactions.slot_id("FLEX")
        self.assertIn("RB/WR/TE", str(caught.exception))


class MemberId(unittest.TestCase):
    def test_a_braced_swid_is_left_alone(self):
        self.assertEqual(transactions.member_id(SWID), SWID)

    def test_an_unbraced_swid_is_braced(self):
        self.assertEqual(transactions.member_id("ABC-123"), "{ABC-123}")

    def test_an_empty_swid_raises_rather_than_sending_anonymously(self):
        with self.assertRaises(ValueError):
            transactions.member_id("")


class Envelope(unittest.TestCase):
    def test_it_never_claims_league_manager_powers(self):
        body = transactions.envelope(8, SWID, 3, "ROSTER", [])
        self.assertIs(body["isLeagueManager"], False)

    def test_the_standard_fields_are_present_and_typed(self):
        body = transactions.envelope("8", SWID, "3", "ROSTER", [])
        self.assertEqual(body["teamId"], 8)
        self.assertEqual(body["scoringPeriodId"], 3)
        self.assertEqual(body["executionType"], "EXECUTE")
        self.assertEqual(body["type"], "ROSTER")

    def test_extra_keys_are_merged(self):
        body = transactions.envelope(8, SWID, 3, "WAIVER", [], bidAmount=12)
        self.assertEqual(body["bidAmount"], 12)


class SetLineup(unittest.TestCase):
    def test_a_bench_to_flex_move_uses_the_right_ids(self):
        body = transactions.set_lineup(8, SWID, 5, [(101, "BE", "RB/WR/TE")])
        self.assertEqual(body["type"], "ROSTER")
        self.assertEqual(body["items"], [{
            "playerId": 101, "type": "LINEUP",
            "fromLineupSlotId": 20, "toLineupSlotId": 23}])

    def test_a_swap_travels_as_two_items_in_one_transaction(self):
        body = transactions.set_lineup(
            8, SWID, 5, [(101, "BE", "RB"), (202, "RB", "BE")])
        self.assertEqual(len(body["items"]), 2)

    def test_no_moves_raises_rather_than_sending_an_empty_transaction(self):
        with self.assertRaises(ValueError):
            transactions.set_lineup(8, SWID, 5, [])


class AddDrop(unittest.TestCase):
    def test_an_add_goes_to_your_team(self):
        body = transactions.add_drop(8, SWID, 5, add_id=101)
        self.assertEqual(body["type"], "FREEAGENT")
        self.assertEqual(body["items"],
                         [{"playerId": 101, "type": "ADD", "toTeamId": 8}])

    def test_a_drop_comes_from_your_team(self):
        body = transactions.add_drop(8, SWID, 5, drop_id=202)
        self.assertEqual(body["items"],
                         [{"playerId": 202, "type": "DROP", "fromTeamId": 8}])

    def test_add_and_drop_ride_together_in_that_order(self):
        body = transactions.add_drop(8, SWID, 5, add_id=101, drop_id=202)
        self.assertEqual([i["type"] for i in body["items"]], ["ADD", "DROP"])

    def test_an_empty_move_raises(self):
        with self.assertRaises(ValueError):
            transactions.add_drop(8, SWID, 5)


class WaiverClaim(unittest.TestCase):
    def test_a_claim_is_a_waiver_not_a_free_agent_add(self):
        body = transactions.waiver_claim(8, SWID, 5, add_id=101)
        self.assertEqual(body["type"], "WAIVER")

    def test_no_faab_sends_a_null_bid_rather_than_zero(self):
        # Zero is a bid of nothing; None is no bid at all, which is what a
        # waiver-priority league expects.
        body = transactions.waiver_claim(8, SWID, 5, add_id=101, bid=None)
        self.assertIsNone(body["bidAmount"])

    def test_a_faab_bid_is_carried(self):
        body = transactions.waiver_claim(8, SWID, 5, add_id=101, bid=17)
        self.assertEqual(body["bidAmount"], 17)

    def test_the_conditional_drop_is_included(self):
        body = transactions.waiver_claim(8, SWID, 5, add_id=101, drop_id=202)
        self.assertEqual([i["type"] for i in body["items"]], ["ADD", "DROP"])


class Trades(unittest.TestCase):
    def test_a_proposal_moves_players_in_both_directions(self):
        body = transactions.propose_trade(8, SWID, 5, 4, [101], [202])
        self.assertEqual(body["type"], "TRADE_PROPOSAL")
        sent = [i for i in body["items"] if i["playerId"] == 101][0]
        got = [i for i in body["items"] if i["playerId"] == 202][0]
        self.assertEqual((sent["fromTeamId"], sent["toTeamId"]), (8, 4))
        self.assertEqual((got["fromTeamId"], got["toTeamId"]), (4, 8))

    def test_a_proposal_carries_an_expiry(self):
        body = transactions.propose_trade(8, SWID, 5, 4, [101], [])
        self.assertTrue(body["expirationDate"].endswith("Z"))

    def test_the_expiry_is_stable_so_a_trade_can_actually_be_confirmed(self):
        # A timestamp taken to the second differs between the previewing run
        # and the confirming one, so the token never matches and the trade can
        # never be sent. Rounding to the day is what makes confirm work.
        one = transactions.propose_trade(8, SWID, 5, 4, [101], [])
        two = transactions.propose_trade(8, SWID, 5, 4, [101], [])
        self.assertEqual(transactions.confirm_token(one),
                         transactions.confirm_token(two))

    def test_the_expiry_is_two_days_out_at_the_end_of_that_day(self):
        import datetime
        now = datetime.datetime(2026, 9, 10, 14, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(transactions._expiry(2, now=now),
                         "2026-09-12T23:59:59.000Z")

    def test_an_explicit_expiry_wins(self):
        body = transactions.propose_trade(8, SWID, 5, 4, [101], [],
                                          expires="2026-10-01T00:00:00.000Z")
        self.assertEqual(body["expirationDate"], "2026-10-01T00:00:00.000Z")

    def test_an_empty_proposal_raises(self):
        with self.assertRaises(ValueError):
            transactions.propose_trade(8, SWID, 5, 4, [], [])

    def test_a_cancellation_carries_no_items_and_the_related_id(self):
        body = transactions.cancel_trade(8, SWID, 5, "12345")
        self.assertEqual(body["items"], [])
        self.assertEqual(body["executionType"], "CANCEL")
        self.assertEqual(body["relatedTransactionId"], "12345")


class LineupMoves(unittest.TestCase):
    def test_an_already_correct_lineup_produces_nothing(self):
        rows = [row("A", "RB", 1, slot_now="RB", proj=10.0)]
        assignment = [("RB", rows[0])]
        self.assertEqual(transactions.lineup_moves(rows, assignment), [])

    def test_a_promotion_and_a_demotion_are_both_emitted(self):
        starter = row("Starter", "RB", 1, slot_now="RB", proj=4.0)
        benched = row("Benched", "RB", 2, slot_now="BE", proj=18.0)
        rows = [starter, benched]
        assignment = [("RB", benched)]
        moves = sorted(transactions.lineup_moves(rows, assignment))
        self.assertEqual(moves, [(1, "RB", "BE"), (2, "BE", "RB")])

    def test_a_player_moving_between_starting_slots_is_emitted(self):
        player = row("Mover", "RB", 1, slot_now="RB", proj=9.0)
        assignment = [("RB/WR/TE", player)]
        self.assertEqual(transactions.lineup_moves([player], assignment),
                         [(1, "RB", "RB/WR/TE")])

    def test_injured_reserve_is_left_alone(self):
        hurt = row("Hurt", "RB", 1, slot_now="IR", proj=0.0)
        self.assertEqual(transactions.lineup_moves([hurt], []), [])

    def test_a_starter_left_out_of_the_assignment_goes_to_the_bench(self):
        dropped = row("Dropped", "WR", 1, slot_now="WR", proj=2.0)
        self.assertEqual(transactions.lineup_moves([dropped], []),
                         [(1, "WR", "BE")])

    def test_the_moves_build_a_payload_that_round_trips(self):
        starter = row("Starter", "RB", 1, slot_now="RB")
        benched = row("Benched", "RB", 2, slot_now="BE")
        moves = transactions.lineup_moves([starter, benched], [("RB", benched)])
        body = transactions.set_lineup(8, SWID, 5, moves)
        self.assertEqual(len(body["items"]), 2)
        for item in body["items"]:
            self.assertIn(item["fromLineupSlotId"], (2, 20))
            self.assertIn(item["toLineupSlotId"], (2, 20))


class ConfirmToken(unittest.TestCase):
    def test_the_same_payload_gives_the_same_token(self):
        one = transactions.add_drop(8, SWID, 5, add_id=101)
        two = transactions.add_drop(8, SWID, 5, add_id=101)
        self.assertEqual(transactions.confirm_token(one),
                         transactions.confirm_token(two))

    def test_key_order_does_not_change_the_token(self):
        one = {"a": 1, "b": 2}
        two = {"b": 2, "a": 1}
        self.assertEqual(transactions.confirm_token(one),
                         transactions.confirm_token(two))

    def test_a_different_player_gives_a_different_token(self):
        one = transactions.add_drop(8, SWID, 5, add_id=101)
        two = transactions.add_drop(8, SWID, 5, add_id=999)
        self.assertNotEqual(transactions.confirm_token(one),
                            transactions.confirm_token(two))

    def test_a_different_drop_gives_a_different_token(self):
        one = transactions.add_drop(8, SWID, 5, add_id=101, drop_id=202)
        two = transactions.add_drop(8, SWID, 5, add_id=101, drop_id=303)
        self.assertNotEqual(transactions.confirm_token(one),
                            transactions.confirm_token(two))

    def test_a_rolled_over_scoring_period_gives_a_different_token(self):
        # The case the token exists for: confirming next week what was
        # previewed this week must not execute.
        one = transactions.add_drop(8, SWID, 5, add_id=101)
        two = transactions.add_drop(8, SWID, 6, add_id=101)
        self.assertNotEqual(transactions.confirm_token(one),
                            transactions.confirm_token(two))

    def test_the_token_is_short_enough_to_retype(self):
        token = transactions.confirm_token({"a": 1})
        self.assertEqual(len(token), 12)

    def test_canonical_form_is_valid_json(self):
        body = transactions.waiver_claim(8, SWID, 5, add_id=101)
        self.assertEqual(json.loads(transactions.canonical(body)), body)


class ParseTransactions(unittest.TestCase):
    PAYLOAD = {"transactions": [
        {"id": "abc-123", "teamId": 8, "type": "WAIVER", "status": "PENDING",
         "scoringPeriodId": 5, "bidAmount": None,
         "items": [{"type": "ADD", "playerId": 101, "toTeamId": 8}]},
        {"id": "def-456", "teamId": 4, "type": "TRADE_PROPOSAL",
         "status": "PENDING", "scoringPeriodId": 5,
         "items": [{"type": "TRADE", "playerId": 202,
                    "fromTeamId": 4, "toTeamId": 8}]},
    ]}

    def test_the_id_survives_which_is_the_whole_point(self):
        rows = transactions.parse_transactions(self.PAYLOAD)
        self.assertEqual([r["id"] for r in rows], ["abc-123", "def-456"])

    def test_status_is_kept_so_a_failed_claim_is_distinguishable(self):
        rows = transactions.parse_transactions(self.PAYLOAD)
        self.assertEqual(rows[0]["status"], "PENDING")

    def test_items_are_flattened(self):
        rows = transactions.parse_transactions(self.PAYLOAD)
        self.assertEqual(rows[0]["items"][0]["player_id"], 101)
        self.assertEqual(rows[1]["items"][0]["from_team"], 4)

    def test_an_empty_payload_is_no_rows_rather_than_an_error(self):
        self.assertEqual(transactions.parse_transactions({}), [])
        self.assertEqual(transactions.parse_transactions(None), [])


if __name__ == "__main__":
    unittest.main()
