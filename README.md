# ESPN Fantasy Football Assistant

Personal tooling for drafting and managing a private ESPN fantasy football team.
Built for the 2026 season.

> **Status:** pre-draft, and now **verified against the live league**. Auth,
> probing, the draft board, live pick reading and the waiver wire all run
> against real data; a full board builds in **~3.7s**, which is what makes it
> usable on a pick clock. Nothing here writes to the league.

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

.venv/bin/python draft.py --watch        # draft night: redraw every 15s
.venv/bin/python draft.py --watch 10 --need
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
  when fetched in league context, so no re-scoring happens here. **This is
  verified, not assumed:** reconstructing projections from the stat breakdown
  under standard, half-PPR and full-PPR scoring matches ESPN's number to within
  0.1 points under half-PPR — this league's actual `REC` value of 0.5 — and
  misses by 25-60 points under the other two. The board is still only as good
  as ESPN's projections: it is a value lens over them, not an independent
  forecast.
- **Only week 1 carries per-week projections** pre-season; `player_info`
  carries none at all. Anything summing a week range says so when the data is
  not there, rather than presenting a one-week number as a season total.
- The pool is every undrafted player, so `draft.py` also works **mid-draft** as
  a best-available sheet.

#### On draft night

`--watch` redraws the board on a timer until Ctrl-C, so it is already current
when you glance at it rather than something you retype on the clock. Each pass
prints how long the refresh took — the number that decides whether this is
usable on a 60-second timer at all. It also names who was taken since the last
redraw.

Players already drafted are removed two ways: they should stop appearing in
`free_agents()`, and they are subtracted explicitly from the live pick list.
The second is a safety net for the first, and the board reports when it fires
(`N drafted players were still listed as free agents`).

Reading live picks needs `picks.py` rather than `espn-api`, whose own draft
reader begins:

```python
if not data.get('draftDetail', {}).get('drafted'):
    return
```

ESPN sets `drafted` when a draft **completes**, so `league.draft` is empty for
the entire duration of a live draft — it works on a finished season, which is
what makes the gap easy to miss. `picks.py` reads the same payload and parses
picks regardless.

Two columns come from the league calendar rather than from projections:

- **BYE** is the player's bye week, with `(n)` for how many players you already
  roster are out that same week. `9(2)` means drafting him puts a third body on
  your week-9 bye.
- **PLAYOFF** is projected points over the weeks your league actually plays its
  playoffs, derived from the matchup periods past `reg_season_count` and capped
  by bracket size. Season totals say nothing about the weeks that decide the
  title.

Bye weeks come from the pro-schedule endpoint, not from player objects:
`free_agents()` builds its players with `_get_pro_schedule(week)` — a single
week — so a free agent's own `schedule` covers one week and cannot show a bye.
A pro team missing more than one week is treated as an incomplete schedule and
skipped, since a wrong bye week is worse than none.

> PLAYOFF depends on the free-agent payload carrying per-week projections,
> which is **unverified**. If it does not, every value would be zero, so the
> column hides itself and says why rather than printing a column of fabricated
> zeros.

With your team identified (automatically from your `SWID`, or `--team-id`) the
board adds a **NEED** column: value over the player you would otherwise end up
with in that slot, given what you have already drafted. It equals VOR while
your roster is empty and diverges as slots fill — once your QB slot is locked,
a backup QB is worth his full VOR to the league and exactly 0 to you, while a
second TE keeps his value because he can still play flex. `--need` sorts by it.

> Raw lineup gain — the metric the waiver tool uses — is wrong here, and shipped
> broken for one commit. With every slot still empty, gain equals raw
> projection, so it ranked six quarterbacks first: exactly the cross-position
> comparison VOR exists to prevent. Measuring gain *over replacement* fixes it.

### 4. Work the waiver wire

```bash
.venv/bin/python waivers.py              # rest-of-season adds
.venv/bin/python waivers.py --week 5     # streaming: week 5 only
.venv/bin/python waivers.py --pos RB     # one position
```

Writes `out/waivers.json`.

The draft board asks *who is the best player available*. In-season that is the
wrong question, because you cannot roster everyone — what matters is whether a
free agent beats somebody you are already starting. So this ranks by **lineup
gain**: your best possible starting lineup with the player added, minus your
best possible starting lineup today.

That framing handles the awkward cases without special-casing them. A great WR
is worth nothing if your two WR slots and your flex are already better. A
mediocre TE is worth a lot the week your starting TE is on bye. And gains
cascade correctly — signing a WR who displaces your WR2 also bumps your WR2 into
the flex over someone worse, and the number reflects both moves.

Two details that matter:

- **Rest-of-season value is summed from the remaining weeks**, not read off
  `projected_total_points`, which is a full-season figure and still includes
  games already played. Bye weeks fall out of this for free: a week with no
  projected game contributes nothing.
- **Slot eligibility comes from the API** (`eligibleSlots`), not inferred from
  the position string, so superflex, WR/TE, and IDP slots work without the
  optimiser knowing they exist.

Your team is identified by the `SWID` in `.env`. If that lookup fails it will
print the league's teams and ask for `--team-id`.

Injured players are flagged, not filtered — `QUE`/`OUT` shows in the row and the
call is yours.

## Tests

```bash
.venv/bin/python -m unittest discover -p 'test_*.py' -v
```

`test_draft` covers slot parsing, flex allocation, replacement level, and tier
breaks. `test_waivers` covers the lineup optimiser, the projection window, and
the gain ranking — the optimiser especially, since every gain number is a
difference of two of its outputs. All fixture data: no network, no credentials.

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

**In-season management** — waiver wire built (`waivers.py`), pending a live run.
- Still read-only: it tells you who to claim, you make the claim in ESPN.
- Next up, and cheap because the lineup optimiser already exists: a start/sit
  report (the optimiser run against your roster alone, showing who to bench)
  and a trade evaluator (lineup gain for both sides of a proposal).

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
| `draft.py` | VOR draft board, tiers, cheat sheet, live `--watch` board |
| `waivers.py` | Waiver wire ranked by lineup gain |
| `picks.py` | Live draft picks, which `espn-api` will not read |
| `schedule.py` | Bye weeks and playoff weeks from the league calendar |
| `lineup.py` | Shared roster math: projections and the lineup optimiser |
| `common.py` | `.env` loading, league connection, finding your team |
| `test_draft.py` | Unit tests for the valuation math and the live board |
| `test_waivers.py` | Unit tests for the lineup optimiser and gain ranking |
| `test_picks.py` | Unit tests for live pick reading |
| `test_schedule.py` | Unit tests for bye and playoff week derivation |
| `.env.example` | Template; copy to `.env` |
