# ESPN Fantasy Football Assistant

Personal tooling for drafting and managing a private ESPN fantasy football team.
Built for the 2026 season.

> **Status:** pre-draft. Auth, league probing, and the value-over-replacement
> draft board are built; the board's math is unit-tested but has not yet been
> run against the live league. Nothing here writes to the league.

## Why this exists

ESPN publishes no official fantasy API. The community wrapper
[`cwendt94/espn-api`](https://github.com/cwendt94/espn-api) covers reads well, but
it is **entirely read-only** — every call in its request layer is `requests.get`,
and the only `requests.post` in the file is commented out. The existing MCP server
for this ([`KBThree13/mcp_espn_ff`](https://github.com/KBThree13/mcp_espn_ff))
wraps that library in 7 read tools and, critically for drafting, never exposes
`free_agents()` or `player_info()` — its player search only looks at players
already on a roster, so pre-draft it finds nobody.

This project starts from the library directly rather than that server.

## Setup

Requires Python 3.12+. On WSL2, a working `DISPLAY` (WSLg) is needed for the
login step, since it opens a real browser window.

```bash
python3 -m venv .venv
.venv/bin/pip install espn-api playwright
.venv/bin/python -m playwright install chromium
```

## Usage

### 1. Log in

```bash
.venv/bin/python login.py
```

Opens a visible Chromium with a persistent profile at `.pwprofile/`. Log into
ESPN by hand — including 2FA — then navigate to your league page. The script
polls for the `espn_s2` and `SWID` cookies, scrapes the league ID out of the URL,
and writes `.env` at mode `600`.

The login is deliberately **not** automated. ESPN authenticates through Disney's
identity provider, which is hostile to scripted logins; driving it by hand once
and reusing the persistent profile is both more reliable and less fragile.

### 2. Probe the league

```bash
.venv/bin/python probe.py
.venv/bin/python probe.py --season 2025   # a past season
```

Prints and saves (`out/league_settings.json`) the settings that drive valuation:
scoring rules, roster slot counts, team list, and whether the draft has happened.

### 3. Build the draft board

```bash
.venv/bin/python draft.py            # overall board + per-position tiers
.venv/bin/python draft.py --pos RB   # one position, deeper
.venv/bin/python draft.py --top 100  # a longer overall board
.venv/bin/python draft.py --season 2025 --week 1   # a past season
```

Writes `out/draft_board.json` and `out/cheatsheet.md`.

The board ranks by **value over replacement**, not raw projection. A position's
replacement level is the projection of the best player still unrostered once
every team has filled its starting slots there — so a QB's 340 points and a
RB's 280 become comparable. Flex slots are allocated to whoever actually wins
them: the best players left past each position's fixed starters, which is why
an RB/WR/TE flex pushes RB replacement deeper than its two fixed slots alone.

Tiers break where the drop to the next player is unusually large for that
position (mean gap plus one standard deviation). Inside a tier, draft for need;
between tiers, take the player.

Two things worth knowing about the inputs:

- ESPN's `projected_total_points` is already scored by *your* league's rules
  when fetched in league context, so no re-scoring happens here. The board is
  only as good as ESPN's projections — it is a value lens over them, not an
  independent forecast.
- The pool is every undrafted player, so `draft.py` also works **mid-draft** as
  a best-available sheet. Re-run it between picks; it flags how many picks are
  already recorded.

## Tests

```bash
.venv/bin/python -m unittest test_draft -v
```

Covers slot parsing, flex allocation, replacement level, and tier breaks with
fixture data — no network, no credentials.

### Testing against a real season

The unit tests prove the valuation math, not the ESPN integration — every
assumption about response shapes is still unverified. `--season` exists to fix
that without editing `.env` and having to remember to change it back before a
live draft:

```bash
.venv/bin/python probe.py --season 2025
.venv/bin/python draft.py --season 2025 --week 1
```

A completed season is the cheapest real test: same league, real roster slots,
real player attributes, and a real draft — so it also exercises the
`picks_recorded > 0` path, which is the mode you are actually in on draft night.
Pass `--week 1`, because a finished season's current week has no projections
left and every player would be skipped as unprojected.

The one thing it cannot test is whether `POOL_DEPTH` reaches replacement level,
since in a completed season the free-agent pool is only the undrafted leftovers.
That needs a league that has not drafted yet.

## Security

Two paths hold live credentials and are both gitignored:

| Path | Contains |
|---|---|
| `.env` | `espn_s2` + `SWID` cookies, mode 600 |
| `.pwprofile/` | A logged-in browser session |

`espn_s2` and `SWID` are **full session cookies, not scoped tokens** — they grant
access to the whole ESPN/Disney account, not just fantasy. Treat them like a
password. Neither path may ever be committed, and `out/` is gitignored too since
dumps can carry league-member names.

## Roadmap

**Draft prep** — built (`draft.py`), pending a live run. Draft is the week of
2026-08-31.
- Remaining: run it once against the real league and sanity-check the pool
  depths in `POOL_DEPTH` are deep enough to reach replacement level at every
  position.
- Possible refinements: bye-week collisions, and blending a second projection
  source so the board is not hostage to ESPN's numbers alone.

**Live draft** (stretch, experimental)
- `espn-api` has no draft websocket — only `refresh_draft()`, which reads
  completed picks. Real-time board state needs browser automation.
- Approach: map the draft-room DOM against an ESPN **mock draft** first, so it is
  tested somewhere harmless rather than during the real draft.

**Roster writes** (post-draft)
- Requires porting write support, which does exist:
  [`garavitgabriel/espn-fantasy-claude-openclaw`](https://github.com/garavitgabriel/espn-fantasy-claude-openclaw)
  has working `league_post()` plus `roster.py` / `lineup.py` / `trades.py`.
  Every mutation is one POST to `lm-api-writes.fantasy.espn.com/.../transactions/`;
  only the JSON body changes.
- It targets **baseball**. The port is mechanical: game slug `flb` → `ffl`, and
  football's lineup slot IDs are already in `espn-api`'s `POSITION_MAP`
  (QB 0, RB 2, WR 4, TE 6, D/ST 16, K 17, BE 20, IR 21, FLEX 23).
- Adopt their safety design: writes off behind an env switch, plus a confirm
  token derived from the payload so the preview you approve is byte-for-byte what
  executes.

**Trades** (post-draft)
- Propose and cancel are reachable via the same endpoint.
- **Accept/reject are not.** Those payloads were never captured upstream. And
  only a single-leg trade was verified live, so multi-player direction
  (`fromTeamId`/`toTeamId` mirroring) is guarded by unit tests, not real usage.

## Files

| File | Purpose |
|---|---|
| `login.py` | Headed browser login; harvests cookies into `.env` |
| `probe.py` | Dumps league scoring, roster slots, teams |
| `draft.py` | VOR draft board, tiers, and cheat sheet |
| `common.py` | `.env` loading and the read-only league connection |
| `test_draft.py` | Unit tests for the valuation math |
| `.env.example` | Template; copy to `.env` |
