"""Unit tests for the write guard rails.

Run:  .venv/bin/python -m unittest test_espnwrite -v

Nothing here reaches ESPN: the session is a fake that records what it was
handed. What is being tested is the part that decides whether a POST happens at
all, which is the only code in this repository that can change a real league.

Every test that expects a refusal also asserts that nothing was sent. A guard
that raises after posting is not a guard.
"""
import unittest

import espnwrite
import transactions

SWID = "{AAAA1111-2222-3333-4444-BBBBCCCCDDDD}"
ENABLED = {"ESPN_WRITES": "1", "SWID": SWID}
DISABLED = {"SWID": SWID}

READ_URL = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
            "seasons/2026/segments/0/leagues/12345")


DEFAULT = object()   # distinguishes "not given" from "there is no JSON body"


class FakeResponse:
    def __init__(self, status_code=200, payload=DEFAULT, text=""):
        self.status_code = status_code
        self._payload = {"id": "txn-1"} if payload is DEFAULT else payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records posts instead of making them."""

    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.posts = []

    def post(self, url, json=None, headers=None, cookies=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers,
                           "cookies": cookies})
        return self.response


class FakeLeague:
    def __init__(self, endpoint=READ_URL):
        self.espn_request = type("R", (), {
            "LEAGUE_ENDPOINT": endpoint,
            "cookies": {"SWID": SWID, "espn_s2": "x"},
        })()


def payload():
    return transactions.add_drop(8, SWID, 5, add_id=101)


class WritesSwitch(unittest.TestCase):
    def test_writes_are_off_unless_switched_on(self):
        self.assertFalse(espnwrite.writes_enabled({}))
        self.assertFalse(espnwrite.writes_enabled({"ESPN_WRITES": "0"}))

    def test_the_usual_affirmatives_all_work(self):
        for value in ("1", "true", "TRUE", "yes"):
            self.assertTrue(espnwrite.writes_enabled({"ESPN_WRITES": value}),
                            value)

    def test_require_writes_explains_how_to_enable(self):
        with self.assertRaises(espnwrite.WritesDisabled) as caught:
            espnwrite.require_writes({})
        self.assertIn("ESPN_WRITES=1", str(caught.exception))


class WriteEndpoint(unittest.TestCase):
    def test_the_host_is_swapped_and_the_path_kept(self):
        url = espnwrite.write_endpoint(FakeLeague())
        self.assertTrue(url.startswith("https://lm-api-writes.fantasy.espn.com"))
        self.assertIn("/seasons/2026/segments/0/leagues/12345", url)
        self.assertTrue(url.endswith("/transactions/"))

    def test_the_read_host_never_survives_into_a_write(self):
        self.assertNotIn("lm-api-reads", espnwrite.write_endpoint(FakeLeague()))

    def test_an_unrecognised_endpoint_refuses_rather_than_guessing(self):
        with self.assertRaises(espnwrite.WriteRejected):
            espnwrite.write_endpoint(FakeLeague("https://example.com/whatever"))


class Execute(unittest.TestCase):
    def test_writes_disabled_refuses_and_sends_nothing(self):
        session = FakeSession()
        with self.assertRaises(espnwrite.WritesDisabled):
            espnwrite.execute(FakeLeague(), DISABLED, payload(),
                              transactions.confirm_token(payload()),
                              session=session)
        self.assertEqual(session.posts, [])

    def test_a_missing_token_refuses_and_sends_nothing(self):
        session = FakeSession()
        with self.assertRaises(espnwrite.NotConfirmed):
            espnwrite.execute(FakeLeague(), ENABLED, payload(), None,
                              session=session)
        self.assertEqual(session.posts, [])

    def test_the_refusal_tells_you_the_token_to_use(self):
        with self.assertRaises(espnwrite.NotConfirmed) as caught:
            espnwrite.execute(FakeLeague(), ENABLED, payload(), None)
        self.assertIn(transactions.confirm_token(payload()),
                      str(caught.exception))

    def test_a_wrong_token_refuses_and_sends_nothing(self):
        session = FakeSession()
        with self.assertRaises(espnwrite.NotConfirmed):
            espnwrite.execute(FakeLeague(), ENABLED, payload(), "deadbeef1234",
                              session=session)
        self.assertEqual(session.posts, [])

    def test_a_token_for_a_different_move_does_not_authorise_this_one(self):
        # The case the whole mechanism exists for: a token minted while
        # previewing one drop must not execute a different drop.
        previewed = transactions.add_drop(8, SWID, 5, add_id=101, drop_id=202)
        actual = transactions.add_drop(8, SWID, 5, add_id=101, drop_id=303)
        session = FakeSession()
        with self.assertRaises(espnwrite.NotConfirmed):
            espnwrite.execute(FakeLeague(), ENABLED, actual,
                              transactions.confirm_token(previewed),
                              session=session)
        self.assertEqual(session.posts, [])

    def test_a_matching_token_sends_exactly_the_payload(self):
        session = FakeSession()
        body = payload()
        result = espnwrite.execute(FakeLeague(), ENABLED, body,
                                   transactions.confirm_token(body),
                                   session=session)
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(session.posts[0]["json"], body)
        self.assertEqual(result, {"id": "txn-1"})

    def test_the_token_is_accepted_case_insensitively_and_trimmed(self):
        session = FakeSession()
        body = payload()
        token = transactions.confirm_token(body).upper()
        espnwrite.execute(FakeLeague(), ENABLED, body, f"  {token} ",
                          session=session)
        self.assertEqual(len(session.posts), 1)

    def test_the_request_carries_the_headers_espn_requires(self):
        session = FakeSession()
        body = payload()
        espnwrite.execute(FakeLeague(), ENABLED, body,
                          transactions.confirm_token(body), session=session)
        headers = session.posts[0]["headers"]
        self.assertEqual(headers["x-fantasy-platform"], "espn-fantasy-web")
        self.assertEqual(headers["content-type"], "application/json")

    def test_the_request_carries_the_session_cookies(self):
        session = FakeSession()
        body = payload()
        espnwrite.execute(FakeLeague(), ENABLED, body,
                          transactions.confirm_token(body), session=session)
        self.assertEqual(session.posts[0]["cookies"]["SWID"], SWID)


class Rejections(unittest.TestCase):
    def reject_with(self, status, text=""):
        session = FakeSession(FakeResponse(status_code=status, text=text))
        body = payload()
        with self.assertRaises(espnwrite.WriteRejected) as caught:
            espnwrite.execute(FakeLeague(), ENABLED, body,
                              transactions.confirm_token(body), session=session)
        return str(caught.exception)

    def test_401_points_at_the_credentials(self):
        self.assertIn("login.py", self.reject_with(401))

    def test_409_explains_it_is_the_move_not_the_credentials(self):
        message = self.reject_with(409, "roster full")
        self.assertIn("illegal", message)
        self.assertIn("roster full", message)

    def test_404_is_named(self):
        self.assertIn("404", self.reject_with(404))

    def test_an_unexpected_status_is_still_reported(self):
        self.assertIn("500", self.reject_with(500))

    def test_a_201_is_success_not_a_rejection(self):
        session = FakeSession(FakeResponse(status_code=201))
        body = payload()
        espnwrite.execute(FakeLeague(), ENABLED, body,
                          transactions.confirm_token(body), session=session)
        self.assertEqual(len(session.posts), 1)

    def test_a_success_with_no_json_body_does_not_blow_up(self):
        session = FakeSession(FakeResponse(status_code=200, payload=None))
        body = payload()
        result = espnwrite.execute(FakeLeague(), ENABLED, body,
                                   transactions.confirm_token(body),
                                   session=session)
        self.assertEqual(result, {"status": 200})


class Preview(unittest.TestCase):
    def test_it_says_nothing_has_been_sent(self):
        text, _ = espnwrite.preview(payload(), ["ADD somebody"])
        self.assertIn("nothing has been sent", text)

    def test_it_shows_the_token_that_would_execute(self):
        body = payload()
        text, token = espnwrite.preview(body, [])
        self.assertEqual(token, transactions.confirm_token(body))
        self.assertIn(f"--confirm {token}", text)

    def test_it_shows_the_body_itself(self):
        text, _ = espnwrite.preview(payload(), [])
        self.assertIn('"type": "FREEAGENT"', text)
        self.assertIn('"playerId": 101', text)

    def test_the_summary_lines_are_included(self):
        text, _ = espnwrite.preview(payload(), ["ADD  Some Guy"])
        self.assertIn("ADD  Some Guy", text)


if __name__ == "__main__":
    unittest.main()
