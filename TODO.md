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

**5e. Bench construction is RB-heavy, and NEED is the reason.**
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

## Known and accepted

- **The socket carries no history.** A board started mid-draft has not seen
  earlier picks. Your own roster is re-seeded from the room, other teams' picks
  are not, and autopick's fall-through covers the risk. Mitigation is
  procedural: start the board before the draft.
- **One draft connection per member.** The board has to *be* the draft room.
  Not fixable; it is ESPN's rule.
- **`CLOCK`'s first field is unidentified.** It read `6` during round 2 and
  round 6. Carried uninterpreted; round comes from the header instead.
