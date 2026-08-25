"""Unit tests for live draft-pick reading.

Run:  .venv/bin/python -m unittest test_picks -v

The test that matters most is that picks parse while `drafted` is still false.
That flag is why espn-api's own draft reader returns nothing during a live
draft, and it is the entire reason this module exists.
"""
import types
import unittest

import picks


def pick(player_id, team_id=1, rnd=1, round_pick=1, overall=None, **kw):
    row = {"playerId": player_id, "teamId": team_id, "roundId": rnd,
           "roundPickNumber": round_pick}
    if overall is not None:
        row["overallPickNumber"] = overall
    row.update(kw)
    return row


def fake_league(detail, boom=False):
    def get_league_draft():
        if boom:
            raise RuntimeError("ESPN said no")
        return {"draftDetail": detail} if detail is not None else {}
    return types.SimpleNamespace(
        espn_request=types.SimpleNamespace(get_league_draft=get_league_draft))


class ParsePicks(unittest.TestCase):
    def test_returns_picks_in_draft_order(self):
        rows = picks.parse_picks({"picks": [pick(30, overall=3),
                                            pick(10, overall=1),
                                            pick(20, overall=2)]})
        self.assertEqual([r["player_id"] for r in rows], [10, 20, 30])

    def test_falls_back_to_round_and_round_pick_without_an_overall(self):
        rows = picks.parse_picks({"picks": [pick(30, rnd=2, round_pick=1),
                                            pick(10, rnd=1, round_pick=1),
                                            pick(20, rnd=1, round_pick=9)]})
        self.assertEqual([r["player_id"] for r in rows], [10, 20, 30])

    def test_empty_and_forfeited_slots_are_dropped(self):
        rows = picks.parse_picks({"picks": [pick(None), pick(-1), pick(7)]})
        self.assertEqual([r["player_id"] for r in rows], [7])

    def test_auction_and_keeper_fields_survive(self):
        rows = picks.parse_picks({"picks": [pick(7, bidAmount=34, keeper=True)]})
        self.assertEqual(rows[0]["bid"], 34)
        self.assertTrue(rows[0]["keeper"])

    def test_missing_or_empty_payloads_are_safe(self):
        for detail in (None, {}, {"picks": None}, {"picks": []}):
            self.assertEqual(picks.parse_picks(detail), [])


class TakenAndRoster(unittest.TestCase):
    def setUp(self):
        self.rows = picks.parse_picks({"picks": [
            pick(10, team_id=1, overall=1), pick(20, team_id=7, overall=2),
            pick(30, team_id=1, overall=3), pick(40, team_id=7, overall=4)]})

    def test_taken_ids_covers_every_team(self):
        self.assertEqual(picks.taken_ids(self.rows), {10, 20, 30, 40})

    def test_roster_ids_filters_to_one_team_in_draft_order(self):
        self.assertEqual(picks.roster_ids(self.rows, 7), [20, 40])

    def test_roster_ids_without_a_team_id_is_empty(self):
        self.assertEqual(picks.roster_ids(self.rows, None), [])

    def test_roster_ids_for_a_team_that_has_not_picked(self):
        self.assertEqual(picks.roster_ids(self.rows, 99), [])


class FetchPicks(unittest.TestCase):
    def test_picks_are_read_while_the_draft_is_still_running(self):
        """The regression this module exists for.

        espn-api returns early unless draftDetail.drafted is true, which ESPN
        only sets once the draft is over — so its own reader is empty for the
        whole live draft.
        """
        league = fake_league({"drafted": False,
                              "picks": [pick(10, overall=1), pick(20, overall=2)]})
        rows, complete = picks.fetch_picks(league)
        self.assertEqual([r["player_id"] for r in rows], [10, 20])
        self.assertFalse(complete)

    def test_a_finished_draft_reports_complete(self):
        league = fake_league({"drafted": True, "picks": [pick(10, overall=1)]})
        rows, complete = picks.fetch_picks(league)
        self.assertEqual(len(rows), 1)
        self.assertTrue(complete)

    def test_a_draft_that_has_not_started_yields_nothing(self):
        rows, complete = picks.fetch_picks(fake_league({"drafted": False}))
        self.assertEqual((rows, complete), ([], False))

    def test_a_request_failure_degrades_instead_of_raising(self):
        """A board with no pick data still beats a traceback mid-draft."""
        self.assertEqual(picks.fetch_picks(fake_league(None, boom=True)),
                         ([], False))

    def test_a_payload_without_draft_detail_is_safe(self):
        self.assertEqual(picks.fetch_picks(fake_league(None)), ([], False))


if __name__ == "__main__":
    unittest.main()
