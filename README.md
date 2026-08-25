# ESPN Fantasy Football Assistant

Personal tooling for drafting and managing a private ESPN fantasy football team.
Built for the 2026 season.

> **Status:** pre-draft setup. Auth and league probing work; draft valuation is
> the next build. Nothing here writes to the league yet.

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
```

Prints and saves (`out/league_settings.json`) the settings that drive valuation:
scoring rules, roster slot counts, team list, and whether the draft has happened.

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

**Draft prep** (current focus — draft is the week of 2026-08-31)
- Pull the undrafted pool via `free_agents()`. ESPN's `projected_total_points`
  already reflects *your league's* scoring when fetched in league context, so
  projections come pre-adjusted.
- Compute value over replacement by position from roster slots and league size.
- Emit tiers and a cheat sheet.

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
| `.env.example` | Template; copy to `.env` |
