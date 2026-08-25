# A Better Fantasy Football App — Concept Brief

**Status:** early concept, not committed. Written 2026-08-24.

## The premise

ESPN's fantasy app is widely disliked, and a better one is a viable product.

The first half of that is **verifiably true**, not just a hunch — which is worth
saying, because most product hunches don't survive a search.

## The pain is documented

ESPN's fantasy platform has visible, repeated, public failures:

- The app broke during Thursday Night Football in Week 15 of the 2025 season,
  with thousands of Down Detector reports logged in a half-hour span.
- It failed again on the **first Sunday of the 2025 regular season** — the single
  highest-traffic, highest-stakes day of the fantasy calendar.
- Live scoring stopped displaying during games, which is precisely when the
  product has to work.
- UX complaints are blunt and consistent: *"the layout is garbage and hard to
  follow, it's unbearably slow."*

The failure mode is notable: ESPN breaks **under load, during live games**. That's
an infrastructure problem, not a feature gap. It also means the pain is felt
sharply but intermittently — a few brutal Sundays a season, not daily friction.
That shapes willingness to pay.

## The constraint that reframes the product

Researching the API landscape turned up something that changes the shape of this
idea more than the ESPN complaints do:

| Platform | Official API? | Documented? | **Writes?** |
|---|---|---|---|
| **ESPN** | ❌ none | ❌ reverse-engineered | ⚠️ undocumented endpoints only |
| **Yahoo** | ✅ OAuth 2.0 | ✅ yes | ❌ **read access only** |
| **Sleeper** | ✅ public, no key | ✅ yes | ❌ **read-only** |

**Every fantasy platform API is read-only.** Yahoo states plainly that write
access is not available. Sleeper is read-only by design. ESPN has no official API
at all, and its write endpoints exist only as reverse-engineered POSTs to
`lm-api-writes.fantasy.espn.com`.

So "manage your roster from my app" is not really on the table anywhere. The
defensible product is **decision support and live experience** — which is
fortunately exactly where ESPN fails. You are not competing on the ability to set
a lineup. You are competing on being fast, correct, and up on Sunday.

Also worth flagging: Sleeper's API is free for **non-commercial** use. A paid
product would need to sort out commercial terms rather than assume access.

## The thing that would sink this

If the app is a hosted service that logs into ESPN on a user's behalf, it must
collect that user's `espn_s2` and `SWID` cookies.

Those are **full session cookies, not scoped OAuth tokens.** They grant access to
the entire ESPN/Disney account — not a fantasy-only permission slip. There is no
consent screen, no scope limiting, no revocation UI, and no way for a user to
grant you *just* fantasy read access.

A SaaS holding thousands of those is holding keys to thousands of Disney accounts,
with no way to limit blast radius. That is a serious security liability, a hard
sell on the signup page ("paste your session cookie here" is a phishing pattern),
and a standing target. It is a bigger threat to the business than ESPN's API
changing.

This single fact should drive the architecture.

## Architecture options

### A. Browser extension — the strong candidate

Runs in the user's own browser, reuses their existing ESPN session, and **never
transmits credentials anywhere**. The credential problem largely evaporates: there
is nothing to store, breach, or ask for.

It also fits the actual pain. If ESPN's server-rendered UI is slow and its live
scoring falls over, an extension can render its own fast view over the same page,
compute projections client-side, and stay useful during exactly the load spikes
that break the native app.

Trade-offs: distribution via extension stores, harder monetization, no mobile —
and mobile is where a lot of Sunday fantasy usage lives. Extensions also break on
DOM changes.

### B. Multi-platform analytics, sanctioned sources first

Build the core on **Sleeper** (public, documented) and **Yahoo** (official OAuth),
where access is legitimate and stable. Treat ESPN as best-effort.

Trade-offs: ESPN has the largest user base, so leading with the platforms that
have clean APIs means starting where the pain is smaller. But the foundation is
one nobody can revoke on a whim.

### C. Hosted ESPN companion — highest risk

Server-side, stores user cookies. Best UX and easiest monetization, and the only
option that gets you mobile cleanly. Also inherits the full credential liability
above, plus total dependence on undocumented endpoints ESPN can change without
notice and has no obligation to keep working.

## Honest risk summary

- **No API contract.** ESPN's endpoints are unofficial and can change silently.
  There is no deprecation notice, no support channel, no SLA.
- **Credential liability.** Covered above. Architecture-determining.
- **ToS exposure.** Reverse-engineered access for personal use is one thing;
  a commercial product built on it is a materially different posture.
- **Seasonality.** Fantasy revenue is concentrated in roughly five months, with a
  huge spike at draft season. Churn is structural.
- **Incumbents are free.** ESPN, Yahoo, and Sleeper cost nothing. The bar for
  paid is high, and users tolerate a lot of jank in free products.

## Recommended path

Start with the **browser extension** aimed at ESPN.

It targets the real, documented pain; it sidesteps the credential problem instead
of managing it; it needs no permission from ESPN; and it can be built and shipped
by one person. Most importantly it's a cheap way to find out whether people want
this badly enough to install something — before taking on the cost and liability
of a hosted service.

If it gets traction, the natural expansions are mobile and the sanctioned
multi-platform backend, funded by evidence rather than optimism.

## Relationship to this repo

The personal tool in this repository is useful groundwork regardless. Building it
surfaces which reads are actually available, what ESPN's data model looks like in
practice, where the write endpoints are and what they cost to use, and how badly
the live-draft gap hurts. That's real product research — and it doubles as a
portfolio piece.

Ship the draft tool first. The draft is the week of 2026-08-31.

## Sources

- [ESPN fantasy app failures, Week 15 2025 (Pro Football Network)](https://www.profootballnetwork.com/wrong-espn-fantasy-football-app-calls-mount-fans-frustration/)
- [ESPN fantasy app fails on opening Sunday (PhoneArena)](https://www.phonearena.com/news/ESPNs-Fantasy-Sports-app-fails-on-the-first-Sunday-of-the-NFL-regular-season_id85308)
- [Yahoo Fantasy Sports API developer portal](https://sports.yahoo.com/developer/)
- [Sleeper API documentation](https://docs.sleeper.com/)
- [cwendt94/espn-api](https://github.com/cwendt94/espn-api)
