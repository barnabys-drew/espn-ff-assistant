"""Unit tests for reading the draft websocket.

Run:  .venv/bin/python -m unittest test_draftsocket -v

The frames here are real ones captured from a live ESPN mock draft, not
invented. That matters: the protocol is undocumented, so a fixture I made up
would only test my assumptions about it.
"""
import unittest

import draftsocket


# Captured verbatim from wss://fantasydraft.espn.com during a live mock.
REAL = [
    "TOKEN 1:123456:2:{SWID}:-1783290851",
    "CLOCK 6 2119 8",
    "AUTOSUGGEST 3121422",
    "AUTODRAFT 8 true",
    "SELECTED 8 4430737 10",
    "SELECTING 1 60000",
    "SELECTED 1 4035538 3 {00000000-0000-0000-0000-000000000001}",
    "CLOCK 6 59499 1",
    "SELECTED 1 4239993 5 {00000000-0000-0000-0000-000000000001}",
    "SELECTED 8 4431299 11",
    "SELECTING 10 60000",
    "SELECTED 10 4360248 6",
    "PING PING%201787693576017",
    "PONG PING%201787693576017",
]


class ParseFrame(unittest.TestCase):
    def test_a_human_pick_carries_a_member_id(self):
        f = draftsocket.parse_frame(
            "SELECTED 1 4035538 3 {00000000-0000-0000-0000-000000000001}")
        self.assertEqual(f["type"], "SELECTED")
        self.assertEqual((f["team_id"], f["player_id"]), (1, 4035538))
        self.assertFalse(f["auto"])

    def test_an_autopick_has_no_member_id(self):
        f = draftsocket.parse_frame("SELECTED 8 4430737 10")
        self.assertEqual((f["team_id"], f["player_id"]), (8, 4430737))
        self.assertIsNone(f["member_id"])
        self.assertTrue(f["auto"])

    def test_clock_is_round_millis_team(self):
        self.assertEqual(
            draftsocket.parse_frame("CLOCK 6 59499 1"),
            {"type": "CLOCK", "round": 6, "millis": 59499, "team_id": 1})

    def test_selecting_names_the_team_on_the_clock(self):
        f = draftsocket.parse_frame("SELECTING 10 60000")
        self.assertEqual((f["team_id"], f["millis"]), (10, 60000))

    def test_autodraft_flag(self):
        self.assertTrue(draftsocket.parse_frame("AUTODRAFT 8 true")["on"])
        self.assertFalse(draftsocket.parse_frame("AUTODRAFT 8 false")["on"])

    def test_housekeeping_frames_are_recognised_not_dropped(self):
        for text in ("PING x", "PONG x", "TOKEN a:b", "INIT AAAA"):
            self.assertIsNotNone(draftsocket.parse_frame(text))

    def test_an_unknown_type_survives_as_itself(self):
        """A protocol change should surface, not vanish."""
        self.assertEqual(draftsocket.parse_frame("NEWTHING 1 2")["type"],
                         "NEWTHING")

    def test_junk_is_ignored(self):
        for bad in ("", "   ", None, b"\x00\x01"):
            self.assertIsNone(draftsocket.parse_frame(bad))

    def test_a_malformed_selected_is_dropped(self):
        self.assertIsNone(draftsocket.parse_frame("SELECTED 1 notanumber 3"))


class State(unittest.TestCase):
    def feed_all(self, my_team=2):
        st = draftsocket.DraftState(my_team_id=my_team)
        new = [st.feed(f) for f in REAL]
        return st, new

    def test_every_pick_in_the_capture_is_recorded(self):
        st, _ = self.feed_all()
        self.assertEqual(len(st.picks), 5)
        self.assertEqual(set(st.taken),
                         {4430737, 4035538, 4239993, 4431299, 4360248})

    def test_taken_maps_player_to_the_team_that_took_him(self):
        st, _ = self.feed_all()
        self.assertEqual(st.taken[4035538], 1)
        self.assertEqual(st.taken[4430737], 8)

    def test_feed_reports_which_frames_were_new_picks(self):
        _, new = self.feed_all()
        self.assertEqual(sum(1 for n in new if n), 5)

    def test_a_replayed_pick_after_reconnect_is_not_double_counted(self):
        st, _ = self.feed_all()
        before = len(st.picks)
        self.assertFalse(st.feed("SELECTED 8 4430737 10"))
        self.assertEqual(len(st.picks), before)

    def test_clock_and_turn_track_the_latest_frame(self):
        st, _ = self.feed_all()
        self.assertEqual(st.on_the_clock, 10)
        self.assertEqual(st.round, 6)

    def test_my_picks_filters_to_my_team(self):
        st, _ = self.feed_all(my_team=1)
        self.assertEqual(st.my_picks, [4035538, 4239993])

    def test_is_my_turn(self):
        st = draftsocket.DraftState(my_team_id=7)
        st.feed("SELECTING 7 60000")
        self.assertTrue(st.is_my_turn)
        st.feed("SELECTING 3 60000")
        self.assertFalse(st.is_my_turn)

    def test_seconds_left_converts_from_millis(self):
        st = draftsocket.DraftState()
        st.feed("CLOCK 6 59499 1")
        self.assertAlmostEqual(st.seconds_left, 59.499)

    def test_autodrafting_teams_are_tracked(self):
        st, _ = self.feed_all()
        self.assertEqual(st.summary()["autodrafting_teams"], [8])

    def test_unknown_frames_are_counted_for_visibility(self):
        st = draftsocket.DraftState()
        st.feed("MYSTERY 1")
        st.feed("MYSTERY 2")
        self.assertEqual(st.unknown, {"MYSTERY": 2})

    def test_state_survives_an_empty_stream(self):
        st = draftsocket.DraftState(my_team_id=2)
        self.assertEqual(st.summary()["picks"], 0)
        self.assertIsNone(st.seconds_left)


class RoomUrl(unittest.TestCase):
    def test_carries_team_and_member(self):
        """Both are required — without them ESPN bounces to the homepage."""
        url = draftsocket.room_url(123456, 2, "{ABC}")
        for fragment in ("leagueId=123456", "teamId=2", "memberId={ABC}",
                         "seasonId=2026"):
            self.assertIn(fragment, url)


if __name__ == "__main__":
    unittest.main()
