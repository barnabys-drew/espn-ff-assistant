# Priority list

Ordered by when being wrong costs the most, not by how interesting the work is.
Draft is the week of **2026-08-31**.

---

## P0 — before draft night

Everything here is either a correctness bug in something already relied on, or
a gap that would only be discovered live.

**1. NEED is collapsing to VOR on the live board.**
Seen in a real draft: with two RBs rostered, every row showed `NEED == VOR`
exactly. The replacement-gain term must be coming out zero for every position.
This matters more than it looks — autopick now *sorts* on NEED, so if NEED is
silently just VOR, the late-round fix is doing nothing and the picker drifts
back toward a fourth RB with QB and TE empty. Offline tests do produce NEED
distinct from VOR, so the collapse is specific to the live path.

**2. Autopick attempts are not logged anywhere.**
They print between screen redraws and are gone in ~2.5 seconds, so a failure
during a real draft leaves no evidence. Write every attempt, outcome and socket
confirmation to a file. Small, and it is the difference between diagnosing a
draft-night failure and guessing at it.

**3. Back-to-back picks are untested.**
At a snake turn you pick twice in a row. The one-attempt-per-pick guard keys on
the pick count, so the second should fire independently — but it has never been
observed, and a turn is exactly where it would fail.

**4. One clean armed run, start to finish.**
Board started before the draft, correct league, `--arm`, all sixteen rounds.
Everything below P0 assumes this works.

---

## P1 — first weeks of the season

**5. Re-check whether weekly projections appear once the season starts.**
Pre-season only week 1 carries per-week projections, and `player_info` carries
none. Until that changes, `waivers.py` "rest of season" is a one-week number —
it says so, but the whole rest-of-season path is dormant until this is
answered. Blocks 6 and 7.

**6. Start/sit report.**
The lineup optimiser already exists; this is it run against your own roster,
showing who to bench. Cheapest real feature left.

**7. Bye and playoff columns on the live board.**
Both exist in the static board and were left out of the live path for startup
speed. Worth adding back once draft night is done.

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
