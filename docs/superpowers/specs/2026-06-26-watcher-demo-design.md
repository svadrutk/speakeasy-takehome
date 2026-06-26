# Watcher Demo Design — Standalone CLI + Deterministic Demo Mode

**Date:** 2026-06-26
**Status:** Approved (pending user spec review)
**Goal:** Make the background watcher reliably demonstrable in a live technical review, firing a macOS notification on cue with a defensible "this is the real pipeline, only the data source is synthetic" story.

## Problem

The watcher is baked into the FastAPI `lifespan` (main.py:774-784) and fires
notifications via `osascript` (main.py:664-682). On Railway (Linux container)
`osascript` does not exist, so `send_notification` silently no-ops
(`except Exception: pass` swallows the `FileNotFoundError`). The Railway
deployment therefore fills the rolling window but **never fires a
notification** — so the "nice-to-have" watcher cannot be demoed from Railway.

A second problem: even running locally against live World Cup markets, a
notification only fires on a >=20% relative probability delta, which is not
something you can bet a live demo on occurring inside a 5-minute window.

## Decisions (locked)

1. **Deterministic on cue.** A demo mode feeds a synthetic market move through
   the *same* gate + LLM + osascript pipeline. The data source is synthetic;
   everything downstream is production code. Defensible line: "I swapped the
   data source, not the logic."
2. **Decouple the watcher into a standalone CLI.** Run locally on the Mac so
   `osascript` works; the API stays on Railway. Rejected: running the whole
   `uvicorn` app locally (leaves the watcher coupled to the web server
   lifespan — weakens the "what would you refactor" answer).
3. **LLM narrative with template fallback.** The demo uses the real
   `generate_narrative` (the "LLM decides what to say" half of the product),
   with `template_narrative` as the D17 fallback if the LLM fails. Console
   trace shows which path ran.

## Architecture — one module, two entrypoints

Extract the watcher into a shared module (`watcher.py`) imported by two
entrypoints:

- **`main.py` lifespan** — keeps the window-filler running on Railway so the
  hosted endpoint's `delta_1m` stays non-None (no API-demo regression).
  Notifications no-op there (no `osascript`) — unchanged from today.
- **`watcher/__main__.py`** — the local CLI entrypoint that runs the same loop
  and can actually fire `osascript`.

Shared in-memory state (`_rolling`, `_cache`) moves into the watcher module;
`main.py` imports it from there for the on-demand endpoint. The load-bearing
functions — `should_notify`, `send_notification`, `_poll_team`,
`generate_narrative` — stay recognizably the same code, just relocated.

### The data-source seam

Fetching goes behind one interface:

```
fetch_market(team_key) -> market_dict | None
```

- **Real implementation:** the existing Kalshi REST fetch (no auth, public
  market data).
- **Synthetic implementation (`--demo`):** emits a scripted curve — a few flat
  readings (5000bp = 50%) to build a baseline, then a jump (6500bp = 65%,
  +30% relative) triggered **on cue** (after Enter is pressed, so the engineer
  can narrate "watch this market move"). Everything downstream — record sample
  → `should_notify` → cooldown → LLM → `osascript` — is the real production
  path.

This seam is also the natural unit-testing point: the gate and pipeline can be
exercised without Kalshi.

## CLI surface

- `uv run python -m watcher` — live mode: polls real Kalshi, fires
  notifications on real moves.
- `uv run python -m watcher --demo` — synthetic source, deterministic
  notification on cue. Resets `_last_notified` + `_rolling` on start so every
  run is a clean cold start.

## Demo flow (what the audience sees)

1. Engineer runs `uv run python -m watcher --demo`.
2. Console traces flat readings: `brazil: prev=5000bp curr=5000bp → gate: no`.
3. Engineer says "watch — I'll simulate a market move" and presses Enter.
4. Console: `brazil: prev=5000bp curr=6500bp → gate: YES (delta 30.0% ≥ 20%) → notifying`, then `calling LLM...`.
5. A macOS notification banner appears with an LLM-written narrative body.
6. If the LLM fails, the trace says `LLM failed → template` and the
   notification still fires with `template_narrative`.

The console trace makes the core design point visible: **deterministic gate
decides when, LLM decides what.**

## Failure modes (silent failures to defend against)

1. **Synthetic jump doesn't cross the gate** → no notification. Mitigation:
   script a +30% relative jump (well above the 20% threshold); the console
   trace makes a missed gate immediately obvious.
2. **Cooldown suppresses a re-demo** → second run fires nothing. Mitigation:
   `--demo` resets `_last_notified` + `_rolling` on start.
3. **`osascript` blocked by Focus mode / automation permission not granted**
   → banner silently absent while code reports success. Mitigation: `--demo`
   also echoes the notification (title + body) to the console so the audience
   sees it regardless; pre-run one `osascript` before the demo to clear the
   automation-permission prompt.

Bonus: LLM latency can delay the banner 2-5s. Accepted (the console trace
sets expectations); template fallback covers failure.

## Refactoring impact on main.py

- Move `_watcher_loop`, `_poll_team`, `should_notify`, `send_notification`,
  `_last_notified`, `_rolling`, `_cache`, and the record/cache helpers into
  `watcher.py`.
- `main.py` imports the shared state + functions it still needs for the
  on-demand endpoint (notably `_rolling` for `delta_1m`).
- The `lifespan` stays in `main.py` but calls into `watcher.run_loop()`.
- No behavior change on Railway (watcher runs, notifications no-op).

## Testing

- `should_notify` tests stay (load-bearing defense).
- New: synthetic source produces the expected curve (flat → jump).
- New: demo mode drives the pipeline and calls `send_notification` (mocked)
  exactly once at the jump, never on flat readings.
- New: demo mode resets cooldown state on start (repeat runs fire).

## Out of scope / another week

- A recorded-demo fallback (capture a real notification on video) if live
  conditions ever matter.
- A Slack/webhook transport behind the same `send_notification` interface
  (already noted in main.py comments).
- WebSocket instead of REST polling (the original "another week" upgrade).

## Review talking points this design arms the engineer for

- "Why is the watcher a separate CLI?" — a background daemon's lifecycle
  shouldn't be tied to a web server's lifespan; notifications are inherently
  local.
- "How did you demo it deterministically?" — synthetic data source behind an
  interface; the pipeline is identical to production.
- "What are the failure modes?" — the three silent failures above + the D17
  LLM-to-template fallback.
- "What would you refactor with another week?" — the extraction this design
  *does* is itself the refactor; further: webhook transport, WS instead of
  polling.
