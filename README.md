# ESPN Fantasy Football Assistant

Personal tooling for drafting and managing a private ESPN fantasy football team.
Built for the 2026 season. Private repo; nothing here is intended for anyone
else's league.

## What it does

**Values players for *your* league, not in the abstract.** Rankings are value
over replacement, where replacement level comes from your actual roster slots
and league size, so a quarterback's 340 points and a running back's 280 become
comparable. ESPN's projections are already scored by your league's rules —
[verified, not assumed](#3-build-the-draft-board).

**Drafts for you, live.** `livedraft.py` opens the draft room, reads every pick
off the draft websocket as it happens, re-ranks what is left, and — optionally —
makes your pick. It knows whose turn it is, how many picks until yours, which
tiers are emptying, which positions are capped, and what a shared bye week
costs. It has drafted a full roster unattended: 9 picks, 9 socket
confirmations, no failures.

**Works the waiver wire in-season.** `waivers.py` ranks free agents by how much
each would raise your best starting lineup, which is the right question once you
cannot roster everyone.

**Sets the lineup each week.** `startsit.py` runs the same optimiser over your
own roster and reports the gap between the lineup you have set and the best one
available, with the moves that close it — separating a bye from a projection
ESPN has not published, and flagging a starter who has been ruled out.

**Makes the move, once you have read it.** `manage.py` sets the lineup, adds,
drops, claims off waivers and proposes trades. Every command previews the exact
request it would send and stops; sending it takes a second run carrying a
confirm token derived from that body, plus an env switch that is off by default.
[How that works](#7-make-the-move).

Everything else is **read-only**.

## Where it is going

**Near term** — finish the draft path and use it on 2026-08-31. The in-season
read tools are built and verified against a finished season; what is left is
running them on a live roster, and a playoff-weeks column that still waits on
ESPN publishing projections that far out.

**Medium term** — writes are built (`manage.py`) and every payload is tested,
but nothing has been sent to a live league yet: the roster is empty until the
draft. The first real move should be a lineup change, which is reversible.
Accepting or rejecting somebody else's trade stays out until those payloads are
captured from a real one.

**Long term** — own projections. Everything here is a value lens over ESPN's
numbers and never questions them; generating better ones means Bayesian
modelling over real usage data. Deliberately last, and in a specific order,
for reasons in the roadmap.

[`TODO.md`](TODO.md) is the ordered list, including what is known-and-accepted
rather than outstanding.

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

Your own roster is read one week at a time from `box_scores()`, not from
`team.roster`. This is not a stylistic choice: see *Where the weekly numbers
come from* below, because reading it the obvious way silently breaks the
ranking.


### 5. Set your lineup

```bash
.venv/bin/python startsit.py                        # the current week
.venv/bin/python startsit.py --week 5
.venv/bin/python startsit.py --season 2025 --week 10  # a finished week
```

Writes `out/startsit.json`.

The same optimiser the draft board and waiver wire rank on, pointed at your own
roster, so "best lineup" means the same thing everywhere. What is new is the
comparison: your best legal lineup against the lineup you have actually set.
That difference is the only number that says whether to do anything at all, and
it comes with the moves that close it.

Moves are reported as *who comes out* and *who goes in*, not as paired swaps,
because fixing a lineup is rarely one-for-one. Promoting a receiver into a WR
slot can slide the incumbent into the flex and push the flex's running back to
the bench — three players change slots, two of them are decisions, and pairing
them up would invent a correspondence that does not exist.

Two checks a projection column cannot make on its own:

- **A bye is distinguished from an unpublished projection.** Both read 0.0 and
  they mean opposite things: one is settled, the other means come back later.
  Byes come from the pro schedule, so `BYE` and `no proj` are separate notes.
- **A starter listed `OUT` is called out even when his projection still looks
  healthy.** ESPN is slow to zero those, and an unnoticed `OUT` in your flex is
  the cheapest way there is to lose a week.

If ESPN has not published the week yet, every lineup ties at 0.0 and the report
says so instead of ranking noise.


### 6. Draft night: the live board

```bash
.venv/bin/python livedraft.py --league-id <ID>              # board only
.venv/bin/python livedraft.py --league-id <ID> --autopick 15 # dry run
.venv/bin/python livedraft.py --league-id <ID> --autopick 15 --arm
```

**This opens the draft room and you draft in its window.** That is not a
preference. ESPN permits one draft connection per member, and a second one
displaces the first with "Duplicate Connection", so a board cannot watch your
draft room from the side — it has to be your draft room.

#### Why the league API is useless here

During a live draft the league API is not lagging, it is blind. Measured
against a real mock, mid-draft:

| Source | What it said |
|---|---|
| `draftDetail.picks` | all 160 slots at `playerId: -1` |
| `free_agents()` | still offered a player *this account had drafted* |
| `team.roster` | 0 players, for a team holding six |

The draft never touches `lm-api-reads`. It runs on a separate service:

```
wss://fantasydraft.espn.com/game-1/league-<id>/JOIN?...
```

so `livedraft.py` reads that socket instead. The protocol is space-delimited
text, decoded in `draftsocket.py`:

```
SELECTED <teamId> <playerId> <n> [<memberId>]   a pick was made
SELECTING <teamId> <millis>                     that team is on the clock
CLOCK <?> <millisRemaining> <teamId>            tick
AUTODRAFT <teamId> <true|false>                 that team is autopicking
```

The trailing member id appears only on human picks, so its absence marks an
autopick. `CLOCK`'s first field is **not** the round — it read `6` during round
2 and during round 6 — so it is carried uninterpreted and the round comes from
the header strip instead.

The player pool is fetched once at startup and maintained from socket picks
rather than re-polled, because re-polling would actively make it worse.

#### How it decides who to draft

Every candidate is scored through the same chain. Each step exists because the
step before it is wrong in a specific, observed way.

**1. Projection.** ESPN's `projected_total_points`, already scored by your
league's rules. Nothing is re-scored — [verified against the stat
breakdown](#3-build-the-draft-board), not assumed.

**2. Value over replacement.** A projection is only worth what it beats.
Replacement level is the best player still unrostered once every team has
filled its starting slots at that position, derived from *your* roster
configuration and league size. This is what makes a quarterback's 340 and a
running back's 280 comparable, and it is where most manual drafters are simply
guessing.

**3. Lineup gain.** Once you hold players, the question changes from "who is
best" to "who improves my lineup". Gain is your best possible starting lineup
with the player, minus without. It captures cascades: a receiver who displaces
your WR2 also pushes that WR2 into the flex over someone worse, and both moves
count.

**4. Gain over replacement — NEED.** Raw gain is the wrong draft metric on its
own: with slots empty it equals the raw projection, so it ranks quarterbacks
first, which is the exact cross-position error VOR exists to prevent. So NEED
is gain minus the gain a *replacement-level* player at that position would give
— how much better this player is than whoever you would end up with there
anyway. It equals VOR while your roster is empty and diverges as slots fill:
once your QB slot is locked and there is no superflex, a backup quarterback is
worth his full VOR to the league and exactly **0** to you.

**5. Bye collision cost.** A shared bye is priced at roughly one starter-week,
because that week the slot must be filled from the bench regardless of how good
the player is the other sixteen. Enough to break a tie, not enough to pass on a
genuinely better player — passing would be the worse mistake.

**6. Depth discount.** A player behind others at his own position only plays
when one of them cannot, so his value is his gain times roughly the chance he
ever starts, which falls as the queue grows. Without this the board keeps
taking marginal flex upgrades: a real draft finished RB 8 / WR 4, the
running-back cap exactly, with five bench running backs.

**7. Position caps.** Positions already at their league limit drop off the
board entirely, since recommending them is advice that cannot be taken.

Then three things that inform the pick without changing the score:

- **Tiers** — where the drop to the next player is unusually large for that
  position. Inside a tier, take need; between tiers, take the player.
- **Pick distance** — how many picks until your turn, from the snake order.
  Paired with tiers, this flags a position as **thin** when its tier will
  likely empty before you pick again.
- **ADP and WAIT** — where the market actually takes a player. If his ADP is
  comfortably beyond your next pick he is marked `WAIT`, meaning take someone
  who *will not* keep. A value board says who is best; only this says who will
  still be there.

Autopick, when armed, drafts the top row of that ranking at a configurable
number of seconds on the clock — so what is printed and what is acted on are
always the same list.

#### Autopick

`--autopick SECS` drafts your top NEED player when the clock drops below `SECS`
on your turn. It is worth having because ESPN's own autodraft is measurably
worse — it offered De'Von Achane at +90.7 VOR with McCaffrey sitting there at
+140.4.

It is a **dry run unless `--arm`** is passed, and a dry run stops immediately
before the click with everything else already located, so a dry run that fails
proves the real one would have.

Four things make it safe:

- **The room rejects stale candidates.** Its player list excludes drafted
  players, so someone already gone has no row to find and the pick fails rather
  than misfiring. Autopick walks down the board until the room accepts a
  candidate — so it cannot draft a player who is gone even when it does not
  know who is.
- **No DRAFT button exists off-turn** — only a "Draft Help" link — so it cannot
  fire when it is not your pick.
- **One attempt per pick**, keyed on the pick count, so a slow page cannot
  double-draft.
- **The socket must confirm the player intended.** A click is half a pick; if
  the draft service reports someone else, the tool disarms rather than firing
  again.

#### Known gap

The socket carries no history, so a board started mid-draft, or restarted, has
not seen earlier picks. Your own roster is re-read from the room at startup, but
other teams' earlier picks are not — the board says so rather than looking
authoritative, and autopick's fall-through covers the risk. **Start the board
before the draft** and this never arises.

### 7. Make the move

Everything above reads. `manage.py` is the only thing here that changes a real
league.

```bash
.venv/bin/python manage.py pending                    # what is already queued
.venv/bin/python manage.py lineup                     # apply the start/sit lineup
.venv/bin/python manage.py add "Some Guy" --drop "Other Guy"
.venv/bin/python manage.py drop "Some Guy"
.venv/bin/python manage.py claim "Some Guy" --drop "Other Guy"
.venv/bin/python manage.py trade --with 4 --send "Mine" --receive "Theirs"
.venv/bin/python manage.py cancel-trade <id>
```

espn-api cannot do any of this — every method in its requests layer is a `GET`.
But the write API is the same league endpoint on a different host
(`lm-api-reads` → `lm-api-writes`), and every mutation is one `POST` to
`/transactions/` with the same envelope. So nothing is forked or vendored: the
read endpoint espn-api already built is reused with the host swapped, and the
cookies it is already holding are the credentials.

**Three things stand between a payload and your league, every time.**

1. `ESPN_WRITES=1` must be set in `.env`. Without it, previews still work and
   sending is refused.
2. Nothing executes on the first run. Every command prints the exact JSON body
   it would send, and stops.
3. Sending needs the confirm token from that preview: `--confirm <token>`.

The token is a hash of the payload, which is what makes it worth anything. The
confirming run rebuilds the payload from current data and re-derives the token,
so if *anything* moved in between — a player claimed, a different drop chosen,
the scoring period rolled over — the bodies differ, the tokens differ, and the
write is refused rather than executing something you were never shown.

There is no `--force` and no `--yes`. Accepting or rejecting somebody else's
trade is absent on purpose: those payloads were never captured, and guessing at
the shape of a write that moves players between two rosters is not a reasonable
thing to do to a league.

Names are resolved against the live league and must be unambiguous. `"Ty"` lists
the three players it could mean and refuses; it does not pick one.

Two details that bite:

- **ESPN validates the final roster, not each step.** A swap is two `LINEUP`
  items in one transaction, and a player who merely changes slot — an RB sliding
  to flex to make room — has to be listed too, or the roster is briefly illegal
  and the whole transaction is rejected. `manage.py lineup` works this out from
  the optimiser's assignment.
- **espn-api's slot-name lookup cannot be used.** `POSITION_MAP` is
  bidirectional, but its name→id half is missing `BE`, `IR` and `RB/WR/TE`, and
  carries a `FLEX` key for 23 that ESPN never returns — `eligibleSlots` and
  `slot_position` both say `RB/WR/TE`. Those are precisely the slots a lineup
  change moves players between, so `transactions.py` inverts the complete
  int→name half instead.

## Where the weekly numbers come from

Every in-season tool here compares your roster against somebody else's, so both
sides have to be read for the same week. That is harder than it sounds, and
getting it wrong is silent.

espn-api builds a team's roster for **one** scoring period — whichever the
league is currently in. So a rostered player's `stats` carries week 0 (the
season total) and that single week, and nothing else:

```python
>>> henry.stats.keys()
dict_keys([0, 18])
>>> lineup.as_row(henry, [10])["proj"]
0.0          # week 10, from a player projected 272.8 on the season
```

Free agents do not have this problem, because `free_agents(week=N)` is fetched
for that week. So the two halves of the waiver comparison were being read from
different weeks, and the roster half was always zero.

That produces a tool that looks like it works. Your best lineup projects `0.0`,
so every free agent "improves" it by his own projection, `GAIN` becomes a copy
of `PROJ`, and the displacement logic — the entire reason to rank by lineup gain
rather than by points — never runs. Nothing errors and no number looks obviously
wrong unless you already know your lineup is not worth zero.

`box_scores(week)` is the fix, and it is what `weekly.py` wraps. It returns each
rostered player with that week's `projected_points` and `eligibleSlots`, and it
carries `slot_position` — the slot you actually have the player in — which is
what makes a start/sit report possible at all. It costs one request per week, so
a rest-of-season figure is fourteen round trips and the tools print progress.

Two consequences worth knowing:

- **Before the draft there are no rosters to score**, so `box_scores()` raises
  from inside espn-api. Both tools catch that, say so plainly, and carry on;
  `waivers.py` falls back to season totals and marks its `GAIN` column as a
  ranking hint rather than a points figure.
- **ESPN publishes future weeks late and unevenly.** A fourteen-week total built
  from two weeks of data is a rest-of-season number in name only, so the report
  states how many of the weeks it asked for actually carried projections.

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

Since `manage.py` exists, those cookies can change a real league and not just
read one. That is what `ESPN_WRITES` is for: leave it at `0`, and a leaked or
mistakenly-run command still cannot move a player. It is a switch you turn on
for a move you have already read, not a setting to leave on.

Previews print the full request body, `memberId` — your SWID — included. That
is deliberate, since the point is to show exactly what would be sent, but it
means a preview is not something to paste into a chat or an issue.

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

**Own projections** (future, large)

Everything here is a value lens over ESPN's `projected_total_points`. VOR, NEED
and tiers are all downstream of it, and none of them ever question it — the
board is only ever as good as that one number, from one source.

Generating projections instead is a different and much harder project, and the
hard part is data rather than maths. ESPN gives projections and box scores,
which is thin; the predictive signal lives in target share, air yards, snap
share, route participation and red-zone touches. `nflverse` / `nflfastR` is the
public source for that.

Where Bayesian methods genuinely fit, rather than as decoration:

- **Posterior distributions rather than point estimates.** VOR on a mean throws
  away information that matters: a player projected 250 with wide variance and
  one projected 250 with a tight band are different assets — a ceiling play
  when you are an underdog, a floor play when you are favoured. The board
  currently cannot tell them apart.
- **Hierarchical pooling** toward position and archetype priors, which is the
  principled fix for small samples: rookies, injury returns, a receiver who had
  three good games. Naive projections overreact to those.
- **Sequential updating in-season** — prior is the preseason projection,
  evidence is each week, posterior is the updated belief. The most natural fit
  of the three, and it feeds the waiver and start/sit tools directly.

Two notes on sequencing. Build the in-season updating *before* preseason
projections: there is weekly feedback to validate against, the waiver tool
benefits immediately, and a season of calibration beats guessing. And a
half-built projection model is worse than ESPN's, because it would be trusted —
so this stays off the draft-night path until it earns its place.

Cheap first step, high leverage: **regress touchdowns toward expected TDs given
opportunity.** TD rate is enormously noisy year to year and raw projections bake
last season's in as though it were a skill. Small model, well-known edge.

Also cheap and immediately useful: **floor/ceiling columns** from historical
week-to-week variance, which would let the board say something it currently
cannot.

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
| `startsit.py` | Best legal lineup for a week, and the moves to reach it |
| `weekly.py` | Per-week roster projections, which `team.roster` cannot give |
| `manage.py` | Roster moves: lineup, add, drop, claim, trade. The only writer |
| `transactions.py` | Transaction payloads and the confirm token. No I/O |
| `espnwrite.py` | The one POST, and the guard rails around it |
| `livedraft.py` | Draft-night board driven by the draft room websocket |
| `draftsocket.py` | The live draft protocol, and draft state |
| `draftroom.py` | Reading roster and header state out of the room's DOM |
| `draftpick.py` | Clicking DRAFT for a named player |
| `draftclock.py` | Countdown, snake order, when your next pick lands |
| `picks.py` | Completed-draft picks (empty during a live draft) |
| `schedule.py` | Bye weeks and playoff weeks from the league calendar |
| `lineup.py` | Shared roster math: projections and the lineup optimiser |
| `common.py` | `.env` loading, league connection, finding your team |
| `test_draft.py` | Unit tests for the valuation math and the live board |
| `test_waivers.py` | Unit tests for the lineup optimiser and gain ranking |
| `test_picks.py` | Unit tests for completed-draft pick reading |
| `test_draftsocket.py` | Protocol tests, against frames captured live |
| `test_draftroom.py` | Tests for DOM name matching |
| `test_schedule.py` | Unit tests for bye and playoff week derivation |
| `test_startsit.py` | Unit tests for lineup advice, byes, and cascades |
| `test_weekly.py` | Unit tests for per-week reads, including the zero-roster bug |
| `test_transactions.py` | Field-by-field tests of every payload ESPN accepts |
| `test_espnwrite.py` | Tests that a refused write sends nothing |
| `.env.example` | Template; copy to `.env` |
