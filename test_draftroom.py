"""Unit tests for recovering draft state from the room's DOM.

Run:  .venv/bin/python -m unittest test_draftroom -v

The matching is deliberately conservative, and most of these tests are about
what it refuses to do: a wrong match here silently removes a real option from
the board or puts someone else's player on your roster.
"""
import unittest

import draftroom

KNOWN = ["Christian McCaffrey", "Jahmyr Gibbs", "James Cook III",
         "Amon-Ra St. Brown", "Drake London", "Nico Collins",
         "Jonathan Taylor", "Justin Jefferson", "Josh Allen",
         "Marvin Harrison Jr.", "Michael Pittman Jr."]


class Normalising(unittest.TestCase):
    def test_suffixes_are_dropped(self):
        self.assertEqual(draftroom.normalise("James Cook III"), "james cook")
        self.assertEqual(draftroom.normalise("Marvin Harrison Jr."),
                         "marvin harrison")

    def test_punctuation_is_dropped(self):
        self.assertEqual(draftroom.normalise("Amon-Ra St. Brown"),
                         "amonra st brown")

    def test_surname_is_the_last_word_after_suffixes(self):
        self.assertEqual(draftroom.surname("James Cook III"), "cook")
        self.assertEqual(draftroom.surname("Christian McCaffrey"), "mccaffrey")

    def test_first_initial(self):
        self.assertEqual(draftroom.first_initial("Drake London"), "d")
        self.assertEqual(draftroom.first_initial(""), "")


class NamesIn(unittest.TestCase):
    def test_finds_players_named_in_a_panel(self):
        text = ("Bijan Robinson / ATL RB\nR1, P1 - Dan's Team\n"
                "Christian McCaffrey / SF RB\nR1, P6 - Drew 's Team\n"
                "Jahmyr Gibbs / DET RB\nR1, P3 - Hayley's Team")
        found = draftroom.names_in(text, KNOWN)
        self.assertIn("Christian McCaffrey", found)
        self.assertIn("Jahmyr Gibbs", found)

    def test_matches_across_suffix_differences(self):
        self.assertIn("James Cook III",
                      draftroom.names_in("James Cook / BUF RB", KNOWN))

    def test_a_player_not_present_is_not_reported(self):
        self.assertNotIn("Josh Allen",
                         draftroom.names_in("Jahmyr Gibbs / DET RB", KNOWN))

    def test_empty_text_finds_nobody(self):
        self.assertEqual(draftroom.names_in("", KNOWN), [])


class Abbreviated(unittest.TestCase):
    def test_matches_an_abbreviated_roster_label(self):
        self.assertEqual(draftroom.match_abbreviated("C. McCaffrey", KNOWN),
                         "Christian McCaffrey")
        self.assertEqual(draftroom.match_abbreviated("D. London", KNOWN),
                         "Drake London")

    def test_matches_despite_a_suffix_on_the_full_name(self):
        self.assertEqual(draftroom.match_abbreviated("J. Cook", KNOWN),
                         "James Cook III")

    def test_an_ambiguous_surname_and_initial_is_refused(self):
        """Two Jr.s sharing an initial is exactly where a guess goes wrong."""
        known = ["Marvin Harrison Jr.", "Michael Harrison Jr."]
        self.assertIsNone(draftroom.match_abbreviated("M. Harrison", known))

    def test_an_unknown_surname_is_refused(self):
        self.assertIsNone(draftroom.match_abbreviated("Q. Nobody", KNOWN))

    def test_junk_is_refused(self):
        for junk in ("", "   ", ".", "X."):
            self.assertIsNone(draftroom.match_abbreviated(junk, KNOWN))


class CurrentPick(unittest.TestCase):
    class Header:
        def __init__(self, text):
            self.text = text

        def locator(self, selector):
            page = self

            class L:
                def count(self):
                    return 1 if "pick" in selector.lower() else 0

                @property
                def first(self):
                    return self

                def inner_text(self, timeout=None):
                    return page.text
            return L()

    def test_reads_round_and_pick_from_the_header(self):
        page = self.Header("RND 7 OF 16 | 00:35 | ON THE CLOCK: PICK 66 | "
                           "Drew 's Dangerous Team | PICK 67 | AUTO")
        rnd, pick, team = draftroom.current_pick(page)
        self.assertEqual((rnd, pick), (7, 66))
        self.assertIn("Dangerous", team)

    def test_a_header_it_cannot_read_yields_nothing(self):
        self.assertEqual(draftroom.current_pick(self.Header("nothing useful")),
                         (None, None, None))


class RosterPanel(unittest.TestCase):
    class FakePage:
        def __init__(self, text):
            self.text = text

        def locator(self, selector):
            page = self

            class L:
                def count(self):
                    return 1 if "PLAYER" in selector else 0

                @property
                def first(self):
                    return self

                def inner_text(self, timeout=None):
                    return page.text
            return L()

        def inner_text(self, selector, timeout=None):
            return self.text

    def test_reads_abbreviated_names_off_the_roster_panel(self):
        page = self.FakePage("POS PLAYER BYE\nQB Empty\nRB C. McCaffrey 8\n"
                             "WR D. London 11\nWR N. Collins 8\nTE Empty")
        found = draftroom.my_roster_players(page, KNOWN)
        self.assertEqual(set(found),
                         {"Christian McCaffrey", "Drake London", "Nico Collins"})

    def test_an_empty_roster_yields_nothing(self):
        page = self.FakePage("POS PLAYER BYE\nQB Empty\nRB Empty\nWR Empty")
        self.assertEqual(draftroom.my_roster_players(page, KNOWN), [])


if __name__ == "__main__":
    unittest.main()


class LeagueGuard(unittest.TestCase):
    """Twice in testing the board ran against the wrong draft and never said so."""

    class Titled:
        def __init__(self, title):
            self._title = title

        def title(self):
            return self._title

        def locator(self, selector):
            class L:
                def count(self):
                    return 0
            return L()

    def name(self, title):
        return draftroom.room_league_name(self.Titled(title))

    def test_extracts_the_league_name_from_the_room_title(self):
        self.assertEqual(
            self.name("ESPN Fantasy Football Draft - Tampa Bay Pro H2H "
                      "Points PPR League"),
            "Tampa Bay Pro H2H Points PPR League")

    def test_a_title_without_a_league_yields_nothing(self):
        self.assertEqual(self.name("ESPN - Serving Sports Fans"), "")

    def test_the_same_league_matches(self):
        self.assertTrue(draftroom.leagues_match(
            "Tampa Bay Pro H2H Points PPR League",
            "Tampa Bay Pro H2H Points PPR League"))

    def test_a_different_league_does_not_match(self):
        """The exact failure seen live: drafting Tampa Bay, reading New England."""
        self.assertFalse(draftroom.leagues_match(
            "Tampa Bay Pro H2H Points PPR League",
            "New England Pro H2H Points PPR League"))

    def test_an_abbreviated_room_name_still_matches(self):
        self.assertTrue(draftroom.leagues_match(
            "Tampa Bay Pro H2H Points PPR League", "Tampa Bay Pro H2H"))

    def test_an_unreadable_title_is_not_treated_as_a_mismatch(self):
        """Failing to read the title is not evidence of the wrong league."""
        self.assertTrue(draftroom.leagues_match("Some League", ""))
