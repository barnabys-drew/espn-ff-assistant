"""Read an ESPN draft live, off the draft room's own websocket.

The league API is blind during a draft. Picks, rosters and free agents all stay
stale until it completes, because the draft never touches
lm-api-reads.fantasy.espn.com -- it runs entirely over

    wss://fantasydraft.espn.com/game-1/league-<id>/JOIN?...

which is a different service. This reads that socket instead.

The protocol is space-delimited text, not JSON:

    SELECTED <teamId> <playerId> <n> [<memberId>]   a pick was made
    SELECTING <teamId> <millis>                     that team is now on the clock
    CLOCK <round> <millisRemaining> <teamId>        tick
    AUTODRAFT <teamId> <true|false>                 that team is autopicking
    AUTOSUGGEST <playerId>                          ESPN's own suggestion
    PING / PONG / TOKEN / INIT                      housekeeping

One important constraint shapes everything above this module: ESPN allows a
single draft connection per member. A second one displaces the first with
"Duplicate Connection", so a reader cannot watch from the side -- it has to be
the draft room you are drafting in.
"""


def parse_frame(text):
    """Turn one raw frame into a dict, or None if it carries nothing useful.

    Unknown message types come back as {"type": ...} rather than being dropped,
    so a protocol change shows up as an unhandled type instead of silence.
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split()
    if not parts:
        return None
    kind = parts[0].upper()

    def num(index):
        try:
            return int(parts[index])
        except (IndexError, ValueError):
            return None

    if kind == "SELECTED":
        player_id = num(2)
        if player_id is None:
            return None
        # The trailing member id is only present when a person made the pick;
        # an autopick has no member behind it.
        member = parts[4] if len(parts) > 4 else None
        return {"type": "SELECTED", "team_id": num(1), "player_id": player_id,
                "slot": num(3), "member_id": member, "auto": member is None}
    if kind == "SELECTING":
        return {"type": "SELECTING", "team_id": num(1), "millis": num(2)}
    if kind == "CLOCK":
        return {"type": "CLOCK", "round": num(1), "millis": num(2),
                "team_id": num(3)}
    if kind == "AUTODRAFT":
        return {"type": "AUTODRAFT", "team_id": num(1),
                "on": len(parts) > 2 and parts[2].lower() == "true"}
    if kind == "AUTOSUGGEST":
        return {"type": "AUTOSUGGEST", "player_id": num(1)}
    if kind in ("PING", "PONG", "TOKEN", "INIT"):
        return {"type": kind}
    return {"type": kind}


class DraftState:
    """Live draft state, built up frame by frame.

    Deliberately tolerant: frames arrive out of order around a pick, and a
    reconnect replays some of them, so applying the same SELECTED twice must
    not corrupt anything.
    """

    def __init__(self, my_team_id=None):
        self.my_team_id = my_team_id
        self.picks = []                # in arrival order
        self.taken = {}                # player_id -> team_id
        self.on_the_clock = None
        self.millis_left = None
        self.round = None
        self.autodraft = {}            # team_id -> bool
        self.suggestion = None
        self.unknown = {}              # unhandled type -> count

    def apply(self, frame):
        """Apply one parsed frame. Returns True if a new pick was recorded."""
        if not frame:
            return False
        kind = frame["type"]

        if kind == "SELECTED":
            pid = frame["player_id"]
            if pid in self.taken:
                return False           # replayed after a reconnect
            self.taken[pid] = frame["team_id"]
            self.picks.append(frame)
            return True
        if kind == "SELECTING":
            self.on_the_clock = frame["team_id"]
            self.millis_left = frame["millis"]
        elif kind == "CLOCK":
            self.round = frame["round"]
            self.millis_left = frame["millis"]
            self.on_the_clock = frame["team_id"]
        elif kind == "AUTODRAFT":
            self.autodraft[frame["team_id"]] = frame["on"]
        elif kind == "AUTOSUGGEST":
            self.suggestion = frame["player_id"]
        elif kind not in ("PING", "PONG", "TOKEN", "INIT"):
            self.unknown[kind] = self.unknown.get(kind, 0) + 1
        return False

    def feed(self, text):
        """Parse and apply a raw frame."""
        return self.apply(parse_frame(text))

    @property
    def my_picks(self):
        return [p["player_id"] for p in self.picks
                if p["team_id"] == self.my_team_id]

    @property
    def is_my_turn(self):
        return (self.my_team_id is not None
                and self.on_the_clock == self.my_team_id)

    @property
    def seconds_left(self):
        return None if self.millis_left is None else self.millis_left / 1000.0

    def summary(self):
        return {
            "picks": len(self.picks),
            "round": self.round,
            "on_the_clock": self.on_the_clock,
            "seconds_left": self.seconds_left,
            "is_my_turn": self.is_my_turn,
            "my_picks": self.my_picks,
            "autodrafting_teams": sorted(t for t, on in self.autodraft.items() if on),
            "unknown_frames": self.unknown,
        }


def room_url(league_id, team_id, swid, season=2026):
    """The draft room URL. memberId is the SWID, and both it and teamId are
    required -- without them ESPN bounces you to the homepage."""
    return (f"https://fantasy.espn.com/football/draft?leagueId={league_id}"
            f"&seasonId={season}&teamId={team_id}&memberId={swid}")
