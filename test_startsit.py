"""Unit tests for the start/sit report.

Run:  .venv/bin/python -m unittest test_startsit -v

The parts that decide advice, exercised against fixtures, with no network and
no credentials. The cases worth having are the ones where the answer is not
just "start the higher projection": a bye that is not an injury, a flex that
cascades, and a week ESPN has not projected at all — where the right output is
no advice rather than confident nonsense.
"""
import unittest

import startsit

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "D/ST": 1, "K": 1}
FLEX = ["RB/WR", "WR", "WR/TE", "RB/WR/TE", "OP", "BE", "IR"]
RB_SLOTS = ["RB", "RB/WR", "RB/WR/TE", "OP", "BE", "IR"]


def row(name, pos, proj, slot_now="BE", team="FA", slots=None, injury=""):
    return {"name": name, "pos": pos, "team": team, "proj": proj,
            "slots": slots if slots is not None else [pos, "BE"],
            "owned": 0.0, "injury": injury, "player_id": name,
            "slot_now": slot_now, "note": ""}


class SlotOrder(unittest.TestCase):
    def test_a_lineup_reads_in_conventional_order(self):
        got = sorted(["K", "RB", "QB", "D/ST", "WR"], key=startsit.slot_sort_key)
        self.assertEqual(got, ["QB", "RB", "WR", "D/ST", "K"])

    def test_the_flex_sits_after_the_dedicated_slots(self):
        got = sorted(["RB/WR/TE", "TE", "RB"], key=startsit.slot_sort_key)
        self.assertEqual(got, ["RB", "TE", "RB/WR/TE"])

    def test_an_unknown_slot_falls_to_the_end_rather_than_vanishing(self):
        got = sorted(["LB", "QB"], key=startsit.slot_sort_key)
        self.assertEqual(got, ["QB", "LB"])


class CurrentWeek(unittest.TestCase):
    class League:
        def __init__(self, week, reg=14):
            self.current_week = week
            self.settings = type("S", (), {"reg_season_count": reg})()

    def test_preseason_zero_becomes_week_one(self):
        self.assertEqual(startsit.current_week(self.League(0)), 1)

    def test_an_in_season_week_is_used_as_is(self):
        self.assertEqual(startsit.current_week(self.League(7)), 7)

    def test_it_does_not_walk_past_the_regular_season(self):
        self.assertEqual(startsit.current_week(self.League(17)), 14)


class Annotate(unittest.TestCase):
    def test_a_bye_is_named_a_bye_rather_than_left_as_a_zero(self):
        rows = startsit.annotate([row("A", "RB", 0.0, team="KC")], {"KC": 10}, 10)
        self.assertEqual(rows[0]["note"], "BYE")

    def test_a_bye_in_another_week_is_not_flagged_this_week(self):
        rows = startsit.annotate([row("A", "RB", 9.0, team="KC")], {"KC": 10}, 7)
        self.assertEqual(rows[0]["note"], "")

    def test_being_ruled_out_outranks_a_healthy_looking_projection(self):
        rows = startsit.annotate([row("A", "RB", 14.0, injury="OUT")], {}, 5)
        self.assertEqual(rows[0]["note"], "OUT")

    def test_an_unprojected_player_is_distinguished_from_a_bye(self):
        rows = startsit.annotate([row("A", "RB", 0.0, team="SF")], {"KC": 10}, 10)
        self.assertEqual(rows[0]["note"], "no proj")

    def test_questionable_is_noted_but_not_alarming(self):
        rows = startsit.annotate(
            [row("A", "RB", 9.0, injury="QUESTIONABLE")], {}, 5)
        self.assertEqual(rows[0]["note"], "Que")

    def test_a_bye_outranks_an_injury_because_it_is_certain(self):
        rows = startsit.annotate(
            [row("A", "RB", 0.0, team="KC", injury="OUT")], {"KC": 10}, 10)
        self.assertEqual(rows[0]["note"], "BYE")


class Moves(unittest.TestCase):
    def test_a_correct_lineup_produces_no_moves(self):
        rows = [row("QB1", "QB", 20.0, "QB"),
                row("RB1", "RB", 15.0, "RB", slots=RB_SLOTS),
                row("RB2", "RB", 12.0, "RB", slots=RB_SLOTS),
                row("WR1", "WR", 14.0, "WR", slots=FLEX),
                row("WR2", "WR", 11.0, "WR", slots=FLEX),
                row("TE1", "TE", 9.0, "TE", slots=["TE", "RB/WR/TE", "BE"]),
                row("FX", "RB", 10.0, "RB/WR/TE", slots=RB_SLOTS),
                row("DST", "D/ST", 7.0, "D/ST"),
                row("K1", "K", 8.0, "K"),
                row("Bench", "RB", 2.0, "BE", slots=RB_SLOTS)]
        _, _, sit, start, now, best = startsit.moves(rows, SLOTS)
        self.assertEqual((sit, start), ([], []))
        self.assertEqual(now, best)

    def test_a_benched_player_who_beats_a_starter_is_surfaced(self):
        rows = [row("Starter", "RB", 4.0, "RB", slots=RB_SLOTS),
                row("Benched", "RB", 18.0, "BE", slots=RB_SLOTS)]
        _, _, sit, start, now, best = startsit.moves(rows, {"RB": 1})
        self.assertEqual([r["name"] for r in start], ["Benched"])
        self.assertEqual([r["name"] for r in sit], ["Starter"])
        self.assertEqual((now, best), (4.0, 18.0))

    def test_a_player_on_bye_is_replaced_by_anybody_who_plays(self):
        rows = [row("OnBye", "RB", 0.0, "RB", team="KC", slots=RB_SLOTS),
                row("Playing", "RB", 6.0, "BE", slots=RB_SLOTS)]
        _, _, sit, start, _, best = startsit.moves(rows, {"RB": 1})
        self.assertEqual([r["name"] for r in start], ["Playing"])
        self.assertEqual([r["name"] for r in sit], ["OnBye"])
        self.assertEqual(best, 6.0)

    def test_an_empty_slot_is_reported_rather_than_dropped(self):
        rows = [row("RB1", "RB", 10.0, "RB", slots=RB_SLOTS)]
        optimal, _, _, _, _, _ = startsit.moves(rows, {"RB": 1, "K": 1})
        empty = [slot for slot, player in optimal if player is None]
        self.assertEqual(empty, ["K"])

    def test_a_cascade_counts_every_leg_of_the_move(self):
        # WR2 comes off the bench into a WR slot, the weak receiver he
        # displaces slides into the flex rather than to the bench, and the
        # running back who was in that flex is the one who actually comes out.
        # Only the two ends of that chain are moves; the man in the middle
        # started before and starts now.
        rows = [row("WR1", "WR", 20.0, "WR", slots=FLEX),
                row("WR2", "WR", 12.0, "BE", slots=FLEX),
                row("WeakWR", "WR", 5.0, "WR", slots=FLEX),
                row("FlexRB", "RB", 3.0, "RB/WR/TE", slots=RB_SLOTS)]
        optimal, _, sit, start, now, best = startsit.moves(
            rows, {"WR": 2, "RB/WR/TE": 1})
        self.assertEqual([r["name"] for r in start], ["WR2"])
        self.assertEqual([r["name"] for r in sit], ["FlexRB"])
        self.assertEqual((now, best), (28.0, 37.0))
        # The middle of the chain moved slot without being reported as a move.
        flex = [p["name"] for slot, p in optimal if p and slot == "RB/WR/TE"]
        wr = sorted(p["name"] for slot, p in optimal if p and slot == "WR")
        self.assertEqual(flex, ["WeakWR"])
        self.assertEqual(wr, ["WR1", "WR2"])

    def test_the_higher_projection_keeps_the_flex_when_it_should(self):
        # The same shape with a stronger flex incumbent: the right answer is
        # to drop the weak receiver and leave the flex alone.
        rows = [row("WR1", "WR", 20.0, "WR", slots=FLEX),
                row("WR2", "WR", 12.0, "BE", slots=FLEX),
                row("WeakWR", "WR", 5.0, "WR", slots=FLEX),
                row("FlexRB", "RB", 8.0, "RB/WR/TE", slots=RB_SLOTS)]
        _, _, sit, start, now, best = startsit.moves(
            rows, {"WR": 2, "RB/WR/TE": 1})
        self.assertEqual([r["name"] for r in start], ["WR2"])
        self.assertEqual([r["name"] for r in sit], ["WeakWR"])
        self.assertEqual((now, best), (33.0, 40.0))

    def test_an_ir_player_is_not_counted_as_started(self):
        rows = [row("Hurt", "RB", 0.0, "IR", slots=RB_SLOTS),
                row("Fit", "RB", 9.0, "RB", slots=RB_SLOTS)]
        _, _, _, _, now, _ = startsit.moves(rows, {"RB": 1})
        self.assertEqual(now, 9.0)

    def test_the_bench_is_everyone_the_optimal_lineup_leaves_out(self):
        rows = [row("In", "RB", 10.0, "RB", slots=RB_SLOTS),
                row("Out1", "RB", 4.0, "BE", slots=RB_SLOTS),
                row("Out2", "RB", 2.0, "BE", slots=RB_SLOTS)]
        _, bench, _, _, _, _ = startsit.moves(rows, {"RB": 1})
        self.assertEqual([r["name"] for r in bench], ["Out1", "Out2"])


class NothingToSay(unittest.TestCase):
    """A week ESPN has not projected must produce no advice, not fake advice."""

    def test_an_unprojected_week_gives_every_lineup_the_same_score(self):
        rows = [row("A", "RB", 0.0, "RB", slots=RB_SLOTS),
                row("B", "RB", 0.0, "BE", slots=RB_SLOTS)]
        _, _, _, _, now, best = startsit.moves(rows, {"RB": 1})
        self.assertEqual(now, best)

    def test_the_report_says_so_instead_of_printing_a_lineup(self):
        import io
        import contextlib
        data = {"team": "T", "league": "L", "season": 2026, "week": 3,
                "error": None, "projected": 0, "rostered": 15, "lineup": [],
                "bench": [], "sit": [], "start": [], "set_total": 0.0,
                "best_total": 0.0, "gain": 0.0}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            startsit.report(data)
        self.assertIn("not published week 3 projections", out.getvalue())
        self.assertIn("nothing to recommend", out.getvalue())

    def test_an_unreadable_week_reports_the_reason(self):
        import io
        import contextlib
        data = {"team": "T", "league": "L", "season": 2026, "week": 1,
                "error": "no rosters yet", "lineup": [], "bench": [],
                "sit": [], "start": [], "set_total": 0.0, "best_total": 0.0,
                "gain": 0.0}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            startsit.report(data)
        self.assertIn("no rosters yet", out.getvalue())


if __name__ == "__main__":
    unittest.main()
