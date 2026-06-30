# Review Drills — Spaced Repetition Log

Format per entry:
- Date / Question # — one-line question
- Your answer (summary)
- What you missed
- The crisp answer to internalize
- Next review: (date) — interval doubles on correct, resets on wrong

---

## Q1 — Architecture overview / data flow (DONE 2026-06-30, result: SHAKY → PASS-on-retry)

**Question:** Walk me through the system — request to response, including every external call, the cache, the rolling window, where the LLM fits.

**First attempt: SHAKY. Second attempt: PASS (still has gaps).**

**Fixed on retry (good):**
- `/teams` now correctly described as static `TEAMS` dict from config.py, not a Kalshi fetch.
- "Kalshi's string formats into integer basis points" — source-is-strings now correct.
- Watcher gate uses *per-poll* delta (current − last sample), explicitly distinguished from the windowed `delta_1m`. Reasoned correctly: windowed value re-fires on consecutive polls for one change.
- LLM only fires on gate-fire in the watcher; always fires in the endpoint.

**What I got wrong:**
1. Said `/teams` hits Kalshi and transforms markets → team names. **Wrong:** it returns static `TEAMS` dict from config.py, verified once at spike time. Static because WC team list is fixed for the tournament — runtime fetch is wasted I/O + a runtime failure mode.
2. Said transform converts "float cents" → integer basis points. **Wrong:** Kalshi gives integer cents (0–100) OR **decimal strings**, never floats. We parse strings/ints → integer bp (×100). Floats don't exist anywhere internal. The drift story is "we never accept floats upstream," not "we fix floats."
3. **THE BIG ONE — conflated `delta_1m` with the gate's delta.** Said the rolling-window delta feeds the notification threshold. **Wrong:**
   - `delta_1m` = `current − window[0]` (oldest), gated on `span ≥ 60s`. Returned by the **endpoint** as the developer-facing field.
   - The **watcher gate** uses `current − previous`, where `previous = _rolling[team][-1][1]` — *last sample (~30s ago)*, NOT the 60s-smoothed windowed value. Reason: a 55s-old move would keep re-firing on consecutive polls if you used windowed. Semantic is "something just changed," not "trend over the last minute."
   - D36 marks this as THE #1 review probe. Must answer from memory.
4. Said LLM is called for "both formats." **Wrong:** watcher calls the LLM ONLY on gate fire, not every poll. D5 separates *when* (deterministic, cheap) from *what* (LLM, expensive). Every-30s polling + every-poll-LLM = burns OpenRouter quota for no reason.

**What I omitted:**
- Error handling (D17) entirely: always-200 + degraded narrative, never 5xx. Three modes: Kalshi down/timeout/garbage → nulls + "temporarily unavailable"; no market found → nulls + "no active market for X"; LLM fails/hallucinates → structured data intact + `template_narrative`. D31 adds 404 for *unknown* team (genuine client error), 200-degraded for *known team with no market* (we looked, nothing's there) — deliberate REST split.
- KXWCADVANCE knockout-advance series (D32): fetch_per_match_market checks advance markets BEFORE KXWCGAME fallback. In knockout rounds, a tied regulation shows ~96% tie / 1–2% per side; the advance market reflects "will they progress." Product-aware code, smartest bit I skipped.
- Three caches, not one: (a) market cache per team, 30s, the endpoint's; (b) events cache shared across all watcher teams — prevents 48 identical `/events` calls per cycle → 429 rate limit; (c) markets-per-event cache — multiple teams share a match event (croatia & ghana both query KXWCGAME-26JUN27CROGHA).
- 30m pre-kickoff grace for `scheduled → ongoing` (D38). `occurrence_datetime` replaced ticker-string date because late-ET kickoffs cross the UTC date boundary and the day-grained calc mislabels them.
- The FDE thesis (open with this): developer-facing typed interface, fan-facing prose `narrative` field; the LLM writes prose, it does NOT make decisions. Say it in the first 20 seconds.

**Crisp answer to internalize (memorize shape, not words):**
"Two endpoints. `/teams` returns a static verified list — WC teams don't change minute-to-minute, so a runtime fetch is wasted I/O. `GET /team/{name}`: 404 if unknown team — that's a client error; everything else is 200-degraded. Inside: per-team market cache (30s TTL) → if cold, pick nearest match using `occurrence_datetime`, preferring KXWCADVANCE in knockout rounds before KXWCGAME. Parse to integer basis points — Kalshi gives decimals/cents, never floats; we keep ints internally and only divide by 10000 at the JSON boundary. Record sample into the rolling window; `delta_1m` is current minus the oldest, returned only if span ≥ 60s. Hand display-format float values to the LLM with a labeled, constrained prompt; verify the prose with a regex number-comparison guard, fall back to a deterministic template on mismatch. The watcher is separate: it polls the configured subset, and the notification gate uses the PER-POLL delta — current minus the last sample, NOT the smooth 60s value — because the semantic is 'something just changed,' not 'trend over the last minute.' Gate fires → call LLM → notify with a per-team 5-minute cooldown so oscillation spam can never happen."

**Next review:** 2026-07-01 (1 day). On correct → 2d → 4d → 8d. On wrong → reset to 1d.

## Q1 — STILL-MISSING items (would still get probed — drill these even though Q1 passed)

1. **Cache layering — say all three.** (a) Per-team **market** cache, 30s TTL — the endpoint's first stop. (b) **Events** cache — shared across all watcher teams; without it, 48 identical `/events` calls per cycle → 429. (c) **Markets-per-event** cache — croatia & ghana both hit `KXWCGAME-26JUN27CROGHA`; one `/markets` call instead of two. Don't conflate them — they exist for different reasons.

2. **`delta_1m` cold-start.** Null until ≥ 60s of *contiguous* samples. Any gap > 60s resets span to 0. That's why the **watcher is the primary window-filler**, not the endpoint. A sporadic curl for an unwatched team returns `delta_1m: null` *forever*. Defend this when asked.

3. **LLM-fail mode ≠ Kalshi-down mode.** Three distinct degraded modes:
   - Kalshi down/timeout/garbage → null structured fields + "temporarily unavailable" narrative.
   - No market found → null structured fields + "no active market for X" narrative.
   - **LLM fails or hallucinates → structured data INTACT, only narrative falls back to `template_narrative`.** Don't roll this into "null fields" — losing probability data on an LLM outage is exactly the failure the design avoids.

4. **KXWCADVANCE — the knockout-advance series (D32).** `fetch_per_match_market` checks `KXWCADVANCE` FIRST, falls back to `KXWCGAME`. Reason: in knockout rounds the regulation market shows ~96% tie / 1–2% per side; the *advance* market ("to advance including ET/penalties") is what fans actually mean. Smartest piece of product-aware code I wrote — lead with it, don't skip it.

5. **Gate edge cases I didn't mention on either attempt:**
   - `previous == 0` → any nonzero move from zero is True ("they're on the board"). Without this, 0% → 50% never fires.
   - `None` inputs → False (cold start, no baseline, no notify).
   - **Per-team 5-minute cooldown** (COOLDOWN_SECONDS, default 300). Prevents oscillation spam: 500↔650 bouncing every poll would notify every 30s without it.

6. **FDE thesis — OPEN WITH THIS.** Developer-facing typed interface (the JSON envelope with `current_prob`, `delta_1m`, etc.) + fan-facing prose `narrative` field. The LLM writes prose; it does NOT make decisions. Two audiences, two layers — that's the FDE demonstration. First sentence of the answer, not absent.

**Next review:** 2026-07-01. These items still need drilling even though Q1 passed.

---

## Q2 — Match selection: which market, which field, which edge case (DONE 2026-06-30, result: BOMBED)

**Question:** When a team hasn't kicked off yet but Kalshi has two upcoming matches (last group-stage in 2 days, quarterfinal in 5 days), walk through exactly how the code picks which market to serve. Which series, which field, what edge case does this surface about team→market mapping?

**What I got wrong (three inverted descriptions of my own code):**

1. **Said "I check the KXWCADVANCE market first."** THE OPPOSITE. `fetch_per_match_market` (main.py:153):
   - (a) `GET /events?series_ticker=KXWCGAME` — fetches the *regulation* series events first
   - (b) Filter for events whose ticker contains the team's `pm` code (substring match, line 183)
   - (c) `min(abs(match_date - today))` picks the closest KXWCGAME event (line 199)
   - (d) THEN derive ONE advance ticker by string-replacing `KXWCGAME-` → `KXWCADVANCE-` on the chosen ticker (line 213)
   - (e) `GET /markets/{that_one_advance_ticker}` directly (line 216); 200 + market → use it; 404 → fall through to KXWCGAME (line 226).
   
   So KXWCGAME events list is the *input*. ADVANCE is **preferred when present for the already-chosen fixture**, NOT consulted first as a series. The flow is "pick closest KXWCGAME fixture, then probe that fixture's ADVANCE variant."

2. **Said "it'll return the quarterfinal, even though it's not the closest match."** FALSE. The ADVANCE swap only happens on a fixture that ALREADY WON the nearest-by-date competition. Group-stage 2 days away vs quarterfinal 5 days away → group-stage wins (2 < 5) → swap to ADVANCE → 404 (no ADVANCE for group-stage) → fall through → returns the KXWCGAME group-stage market. NOT the quarterfinal. I described my code doing the opposite of what it does.

3. **Said "if there were multiple matches returned from the ADVANCE market, it would use the timestamp field from Kalshi's data to calculate the closest match."** This loop DOES NOT EXIST. The code never enumerates ADVANCE events and picks closest among them. It computes ONE advance ticker by string swap on the already-chosen KXWCGAME fixture (line 213), fetches that ONE market directly (line 216), returns-or-falls-through. No second nearest-picker loop. I described a function that isn't in the codebase.

**The ACTUAL edge case I missed (the real question):**

Line 199: `abs(match_date - today) < abs(closest[1] - today)` is **BIDIRECTIONAL**. A team that played 2 days ago (distance 2) and plays in 5 days (distance 5) → the **past** match wins. **No filter against settled/past fixtures exists.** Kalshi returns settled markets at their resolution prices (1.0/0.0/0.0), so the endpoint could serve a resolved market as if it were the team's next match.

Mitigations I SHOULD have articulated:
- `match_status` labeling (D38) DOES flag it `"closed"` in the output, prompt wording "the match has been played" — soft mitigation, surfaces staleness to humans.
- The *probability* served is still 1.0/0.0, not the upcoming match's — a developer consuming the JSON gets `current_prob: 1.0` and a `"closed"` flag = confusing or wrong.
- Tournament-phase-conditional: late-knockout (past = weeks away, future = days away) → safe. Group stage (played yesterday, plays in 3 days) → buggy.

Also: `match_date` is parsed off the TICKER STRING (`ticker[9:16]` → `"26JUN24"` → date(2026,6,24)), NOT from a market field. I called it "the timestamp field from Kalshi's data" — wrong granularity. The market's `occurrence_datetime` is a different field used later for `scheduled/ongoing/closed` distinction (30m grace), NOT for match selection.

**Crisp answer to internalize:**
> "I do NOT check ADVANCE first. Flow: GET `/events?series_ticker=KXWCGAME`, filter for events whose ticker contains the team's `pm` code, pick nearest by `abs(match_date - today)` where `match_date` comes from the ticker string itself (`ticker[9:16]`), then derive ONE `KXWCADVANCE-...` ticker from that chosen fixture by string-swap, fetch that single market, prefer it if 200, fall through to KXWCGAME on 404. There is no second nearest-picker over ADVANCE markets. The latent edge case is the bidirectional `abs()`: past, settled fixtures can outrank future ones by date distance. `match_status: "closed"` surfaces the distinction in the output, but the probability served for a past fixture is the resolved 1.0/0.0, not the upcoming match's. Tournament-phase-conditional — late knockout is safe (pasts are weeks away), but mid-group-stage (team played yesterday, plays in 3 days) would serve the settled match. Fix would be a filter against `set_event_position` or past dates, or only-future-`abs()`."

**The pattern (broader recall — this applies beyond Q2):** When asked "what does this code do with multiple X," FIRST READ THE LOOP. Reach for product framing ("fans want advance") only AFTER I've correctly described the iteration. Technical answer first, product story second — never the other way around in an interview. The interviewer reads code while I talk; inverted descriptions of the code cost me credibility in the first sentence.

**Next review:** 2026-07-01 (1 day — bombed, interval stays short). On correct → 2d. On wrong → re-explain the code out loud before retrying.

---

## Q2 — POST-FIX update (rewritten Jun 30: /markets?status=open replaces events-based picker)

**What changed in code:** The entire `fetch_per_match_market` was rewritten. The old flow (`/events` → ticker-string date parse → `abs(match_date - today)` → probe ADVANCE ticker) is gone. New flow: for each series in `(KXWCADVANCE, KXWCGAME)`, GET `/markets?series_ticker={series}&status=open` (cached 30s), filter by `ticker.endswith(f"-{pm}")`, pick nearest by `abs(occurrence_datetime - now_utc)`. Return best match from first series with any candidates.

The bidirectional-abs bug is **structurally impossible** — `status=open` is a server-side Kalshi filter. Past settled fixtures never appear in the response. No `if match_date < today` guard needed.

**Behavioral changes:**
- ADVANCE is truly first priority now — consulted as a full `/markets` sweep, not derived from a KXWCGAME fixture.
- `occurrence_datetime` (UTC, minute-precise) replaces ticker-string date parsing (day-granular, local-timezone). No month dict, no `ticker[9:16]` slicing.
- Cache reduced from three to two: per-team market cache (30s) + per-series open-markets cache (30s). Events and markets-per-event caches eliminated.
- `match_date` threading removed from `extract_market_fields`, `fetch_market_for_team`, and the endpoint handler — `match_status` now relies entirely on `occurrence_datetime` (grace path: `status == "active"` → "scheduled").

**Updated crisp answer to internalize:**
> "I iterate `(KXWCADVANCE, KXWCGAME)` in priority order. For each series, GET `/markets?series_ticker={series}&status=open` (cached 30s per series — collapses the watcher's 48-teams fan-out to 1-2 calls per cycle), filter to tickers ending with the team's 3-letter `pm` code, parse each market's `occurrence_datetime` as UTC, pick the one with the smallest `abs(kickoff - now_utc)`. Return immediately from the first series that has any candidate. If neither series matches (team has no open markets — group stage, won't play), fall through to `fetch_tournament_winner_market`. The key structural change: `status=open` is a server-side filter so past settled fixtures never enter the candidate pool. No date parsing, no past-match guard, no bidirectional-abs bug possible. The `match_status` downstream uses `occurrence_datetime` with 30m pre-kickoff grace — minute-precision from the market field itself, not the ticker string."

**Interview narrative upgrade:**
> "After I fixed the bidirectional-abs bug with a `if match_date < today` guard, I kept looking at the flow and realized the root cause wasn't the guard — it was the architecture. We were reconstructing match dates from ticker strings because we were iterating `/events` (which has no kickoff field) instead of `/markets` (which has `occurrence_datetime`). So I scrapped the events-based picker and replaced it with a direct `/markets?series_ticker={series}&status=open` sweep. Server-side `status=open` makes the bidirectional-abs bug structurally impossible — there is no date comparison over past fixtures because they never appear in the response. Now ADVANCE is truly first priority (queried as a full series, not derived from a KXWCGAME fixture), and the picker uses minute-precision UTC kickoff from the market field itself. Three caches became two. The old ticker-string date parsing is gone entirely."

**Next review:** 2026-07-01 (still 1-day interval; full pipeline rewrite, fresh drill).

---



---

