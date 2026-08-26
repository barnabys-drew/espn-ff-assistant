# Priority list

Ordered by when being wrong costs the most, not by how interesting the work is.
Draft is the week of **2026-08-31**.

---

## P0 — before draft night  *(all closed 2026-08-25)*

Everything here is either a correctness bug in something already relied on, or
a gap that would only be discovered live.

**1. ~~NEED collapsing to VOR~~ — not a bug, closed.**
`NEED == VOR` with two RBs rostered looked wrong but is correct. While slots
are still empty, `gain` equals a player's full projection, so
`need = proj - replacement`, which *is* VOR. Reproduced offline, and confirmed
the other direction: once every starting slot is filled, every position drops
to `NEED +0.0` while VOR still reads `+140`, because nobody can crack the
lineup. The degradation is by construction, and it lasts longer than expected
simply because slots stay open for many rounds.

**2. ~~Autopick attempts are not logged~~ — done.**
Every attempt, skip and socket confirmation now goes to `out/livedraft.log`.
Originally:
They print between screen redraws and are gone in ~2.5 seconds, so a failure
during a real draft leaves no evidence. Write every attempt, outcome and socket
confirmation to a file. Small, and it is the difference between diagnosing a
draft-night failure and guessing at it.

**3. ~~Back-to-back picks untested~~ — proven.**
Fired independently 63 seconds apart at a snake turn, both confirmed.
Originally:
At a snake turn you pick twice in a row. The one-attempt-per-pick guard keys on
the pick count, so the second should fire independently — but it has never been
observed, and a turn is exactly where it would fail.

**4. ~~One clean armed run~~ — done.**
Five picks, five socket confirmations, no failures. Originally:
Board started before the draft, correct league, `--arm`, all sixteen rounds.
Everything below P0 assumes this works.

---

**5b. ~~Tier cliffs against your next pick~~ — done.**
A value board says who is best, never whether he will still be there. Tiers are
now computed on the live board and paired with the snake order, so it reports
"RB tier 1: 3 left" against "next pick 14 away" and flags the position as thin.
Both halves already existed; only the joining was missing.

**5c. ~~Roster position caps~~ — done.**
`positionLimits` (QB 4 / RB 8 / WR 8 / TE 3 / K 3 / D/ST 3 here) was unknown to
the tool, so it could recommend a ninth running back — advice that cannot be
taken, and an armed picker attempting it would fail for a reason nothing in the
log would explain. Positions at their cap now drop off the board.

**5e. Bench construction — the real cause was a sign bug.**

Three armed drafts finished RB 8 / WR 4. Two fixes missed before reproducing
against the real end-state found it:

```
Dylan Laube    RB   VOR -54.2   depth 0.50   vor*depth  -27.1
Devin Duvernay WR   VOR -84.8   depth 1.00   vor*depth  -84.8
```

Bench candidates sit **below** replacement level — that is what makes them
bench candidates — so their VOR is negative. Multiplying a negative by 0.5
makes it *larger*, so the depth discount was rewarding depth rather than
penalising it: the more running backs held, the better the next one scored.
Every synthetic test passed because they were built with positive-VOR players.

`discounted()` now divides when the value is negative, so a discount costs
whatever the sign. Verified against the real roster and pool: the bench now
alternates WR/RB instead of taking six straight running backs.

Still to confirm in a live draft rather than a reproduction.

Earlier attempts, both kept because they are correct in themselves:
Sorting is now `(NEED, VOR x depth)`. NEED still leads, so anyone who can crack
the lineup outranks any bench stash; once starters are full every candidate
lands on `NEED 0.0` together and the tiebreak takes over. Simulated over the
same seven bench picks: **RB 8 / WR 4 became RB 4 / WR 4 / QB 3 / TE 3.**

Then bench targets per position, because three quarterbacks was still more than
a person would take and no multiplier was going to fix it. VOR asks the wrong
question about a bench slot: it measures against replacement at the *starting*
threshold, while a bench player's value is how often he plays and how easily he
could be replaced from waivers. In a one-QB league a backup plays on a bye and
an injury, and 32 teams start a quarterback against ~15 rostered, so the wire is
permanently stocked — unplayable and unnecessary at once. So QB and TE get one
backup, K and D/ST none, and RB/WR stay unconstrained. Final simulation:
**RB 5 / WR 5 / QB 2 / TE 2 / K 1 / D-ST 1**, which is what a good drafter
builds.

The diagnosis that led here:

A second armed draft with the discount in place finished **RB 8 / WR 4 again**,
identical to before. Reproduced against the real pool afterwards, and the cause
is not what the discount addresses:

```
Jake Bates      K    VOR  +9.9   depth 0.50   NEED +2.9
Tyjae Spears    RB   VOR +99.6   depth 1.00   NEED +0.0
```

Once every starting slot is filled, **NEED goes to ~0 for every bench
candidate** — nobody can crack the lineup, so gain is zero. Multiplying zero by
a depth factor is still zero, so the discount is inert on precisely the picks it
was written for, and what decides bench order is residual noise: a kicker at
+2.9 outranking a running back worth +99.6.

Worse, on that roster the factors pointed the wrong way. It held 2 RB (capacity
3, so no discount) and 3 WR (capacity 3, so halved) — the discount was pushing
*toward* running backs.

The real conclusion: **NEED is the wrong metric for bench picks.** It measures
today's lineup gain, and a bench player contributes nothing to today's lineup by
definition. Once starters are covered the ranking should switch to something
that still discriminates — VOR times the depth factor is the obvious candidate,
since VOR keeps the +99.6 vs +9.9 distinction that NEED throws away.

The discount itself is sound and tested; it is being applied to a number that
has already collapsed.

Originally:
NEED is now multiplied by a depth factor: once a position's starting slots are
covered, the next player there only plays when somebody ahead of him cannot, so
his value is his lineup gain times roughly the chance he ever starts. With the
roster that draft actually built (7 RB, 1 WR), an eighth running back's NEED of
40 becomes 6.7 while a second receiver's stays at 40. Early rounds are
untouched — with two running backs the third still fills the flex and is not
discounted. Originally:
A full armed draft ended RB 8 / WR 4 / QB 1 / TE 1 / K 1 / D-ST 1 — the running
back cap exactly, with five bench running backs. The starters were built
correctly and quickly, but after that NEED keeps ranking marginal flex upgrades
above everything else, because a bench player's *lineup gain today* is the only
thing it measures. A bench spot is really insurance and upside, which is a
different question, and the model has no concept of it. Once starters are
filled, bench candidates should be ranked on something else — scarcity, upside,
or handcuff value to a starter you already own.

**5d. Positional run detection.**
When six of the last eight picks are running backs, the position is emptying
faster than replacement level knows, because replacement only moves as players
leave the pool. Real drafters react to runs. Not started.

## P1 — first weeks of the season

**5. Re-check whether weekly projections appear once the season starts.**
Pre-season only week 1 carries per-week projections, and `player_info` carries
none. Until that changes, `waivers.py` "rest of season" is a one-week number —
it says so, but the whole rest-of-season path is dormant until this is
answered. Blocks 6 and 7.

**6. Start/sit report.**
The lineup optimiser already exists; this is it run against your own roster,
showing who to bench. Cheapest real feature left.

**7. ~~Bye handling on the live board~~ — done, and it mattered.**
Autopick drafted two running backs onto the same week-13 bye, because the live
board had no bye data at all. Byes now load at startup and, more importantly,
**cost NEED**: a collision is priced at roughly one starter-week, since that
week the slot must be filled from the bench regardless. Enough to flip a near
tie, not enough to pass on a genuinely better player. `--bye-weight 0` disables.

**7b. Playoff-week column on the live board.**
Still missing, and still blocked on ESPN publishing per-week projections.

---

## P2 — writes, post-draft

**8. Read the openclaw fork's transaction payloads.**
Specifically: does it cover waiver *claims* (scheduled, priority/FAAB) or only
immediate free-agent adds? That is still unknown and it shapes everything else
here. Cheap to answer, blocks 9 and 10.

**9. Port write support.**
`league_post()` plus roster/lineup/trades, baseball `flb` → football `ffl`.
Behind an env switch and a confirm token derived from the payload, so what is
previewed is byte-for-byte what executes.

**10. Trade evaluator, then proposals.**
Lineup gain for both sides is read-only and useful on its own. Propose and
cancel are reachable; **accept/reject are not** — those payloads were never
captured upstream.

---

## P3 — larger, and not on the critical path

**11. Floor/ceiling columns** from historical week-to-week variance. Small, and
it lets the board distinguish a ceiling play from a floor play, which it
currently cannot.

**12. Touchdown regression** toward expected TDs given opportunity. TD rate is
noisy year to year and raw projections treat last season's as skill. Small
model, well-known edge.

**13. In-season Bayesian updating** — prior is the preseason projection,
evidence is each week. Feeds the waiver and start/sit tools directly, and
generates the calibration data that 14 would need.

**14. Own projections.** See the README roadmap. Build 13 first: a half-built
projection model is worse than ESPN's, because it would be trusted.

---

---

## Post-draft build (the ~3 weeks between 2026-08-31 and week 1)

**The goal is an edge over other drafters, not over the NFL.** Anyone in the
league who watches more football will out-evaluate players. That is not
winnable with a script, and building as though it were would build the wrong
thing. What is winnable is *decision quality given the same public
information*, because people convert it into worse decisions in predictable
ways: they draft off ADP and gut rather than replacement level, they overweight
what they watched, they forget byes and roster structure in round 11, and they
panic on the clock.

Ordered so each step is checkable before the next depends on it.

**A. ~~Average draft position~~ — done.** ADP turned out to be in the
`kona_player_info` payload `free_agents()` already requests, under
`ownership.averageDraftPosition`; espn-api drops the field, so it is read
from the raw response. The board shows ADP and a **WAIT** marker when a
player should still be available at your next pick. Originally:

The most direct attack on the stated goal, and
it needs no modelling. If the board values a player at pick 20 and the room
takes him at 35, wait and take someone else first — the gap between your
valuation and the market *is* the edge, and it is measurable. Pairs naturally
with tier pressure, which already knows how many picks until your turn:
together they answer "can I afford to wait?" rather than just "who is best?".
Sources: ESPN's own draft rankings via the API, or a public consensus ADP.

**B. Data pipeline.** `nflverse` / `nflfastR` weekly usage — targets, air
yards, snap share, carries, red-zone touches. Nothing downstream exists without
it. Opportunity metrics are the specific place where numbers beat viewing
hours: usage does not care how good a player looked.

**C. Touchdown regression.** Regress touchdowns toward expected TDs given
opportunity. The clearest exploit of the "watched more football" bias — a
player who scored 14 on modest usage looks like a star to someone who watched
him score them and like a regression candidate to a model.

**D. Floor and ceiling** from historical week-to-week variance, so the board
can finally distinguish a ceiling play from a floor play. VOR on a mean cannot.

**E. In-season Bayesian updating**, once games start and there is evidence to
update on. A, B, C and D are what make it possible — the priors and the
ingestion have to exist before week 1 or the data has nowhere to land.

### Validation is the point, not bookkeeping

The three weeks before the season are usable precisely because a projection
model can be tested without the season: build on 2024, project 2025, and
compare against both what happened and what ESPN projected. If it does not beat
ESPN there, it does not get used.

This matters more than it sounds. The tool is currently honest about being a
value lens over someone else's numbers. The moment it generates its own, they
will look authoritative whether or not they are better.

## Known and accepted

- **Drafted mock leagues cannot be left.** ESPN only offers "Leave This
  League" before a draft — verified against a completed league on the team,
  members, settings and league-manager pages. Seven mock teams from testing
  will sit on the account until ESPN retires them. `leaveleagues.py` exists and
  works for the pre-draft case, and refuses the real league unconditionally.

- **The socket carries no history.** A board started mid-draft has not seen
  earlier picks. Your own roster is re-seeded from the room, other teams' picks
  are not, and autopick's fall-through covers the risk. Mitigation is
  procedural: start the board before the draft.
- **One draft connection per member.** The board has to *be* the draft room.
  Not fixable; it is ESPN's rule.
- **`CLOCK`'s first field is unidentified.** It read `6` during round 2 and
  round 6. Carried uninterpreted; round comes from the header instead.
