# DECISIONS.md — Running Decision Log

Study guide for the live review. Each entry: what we chose, why, what we rejected, and the
crisp review answer. Update this as we build — it doubles as the README's Tradeoffs section.

---

## D1. Domain: Kalshi + World Cup prediction markets

**Chose:** Kalshi prediction-market data about the World Cup, turned into narrative sentiment
JSON.

**Why:** Personally interesting (live event), Kalshi is an unusual API choice that shows
taste, prediction markets are information-aggregating APIs — exactly the kind of integration
FDEs handle.

**Rejected:** OpenAI/Stripe/GitHub integrations (seen 20 times); pure dev productivity tool
(didn't feel personal enough).

**Review answer:** "I picked Kalshi because prediction markets are a unique data source —
they aggregate human judgment into probabilities. Turning that into something a human can act
on is an integration problem, not just a plumbing problem."

---

## D2. Output shape: JSON with a `narrative` field

**Chose:** `{"market": "...", "current_prob": 0.34, "delta_1m": 0.06, "narrative": "..."}`
— structured JSON where one field is human-readable prose.

**Why:** Typed + developer-friendly (the interface) while carrying human content (the
payload). Demoable in one curl. Synchronous, stateless, easy to reason about.

**Rejected:** Streaming (SSE/WS-out) — more failure modes (backpressure, reconnects, partial
state), harder to demo in 60 min. Markdown-only — a dev can't build on top of it, undercuts
the Speakeasy fit.

**Another week:** Streaming via SSE — the natural upgrade. Same data, push instead of pull.

**Review answer:** "JSON-with-narrative is the right v1 scope: typed, demoable, synchronous.
Streaming is the obvious next step but adds backpressure and reconnection complexity I didn't
want in a 2-3 hour build."

---

## D3. Two-layer design: dev-facing interface, fan-facing content

**Chose:** Developer-facing interface (FastAPI endpoint, typed JSON), fan-facing content (the
narrative field reads like a human wrote it).

**Why:** This split IS the FDE job — bridging raw API complexity to a human outcome.
Demonstrates understanding of both audiences the exercise mentions ("explain to engineers and
customers"). Also kills the frontend problem — no UI needed.

**Rejected:** Pure dev tool (only shows half the job); pure fan tool (no dev-friendly surface
to integrate against).

**Review answer:** "An FDE bridges APIs to human outcomes. The interface is developer-facing
— typed, documented, predictable. The content is fan-facing — the narrative field reads like
a human wrote it. A dev can pipe it into Slack or a dashboard; the fan never touches the code."

---

## D4. Sentiment = market probability + match events, editorialized by LLM

**Chose:** LLM writes the narrative prose; it does NOT make decisions. The data (probability,
delta, events) is deterministic; the LLM is a final transformation step.

**Why:** Keeps the LLM's role explainable and bounded. "The LLM editorializes; it doesn't
decide." Testable: the data layer is pure, the LLM layer is a pure function of the data.

**Rejected:** LLM-as-decision-maker (untestable, nondeterministic, hard to debug);
market-probability-only (too thin, no product thinking).

**Review answer:** "The LLM is a communication layer, not a decision layer. The data is
computed deterministically; the LLM turns it into prose. If the LLM hallucinates, the
structured fields are still correct."

---

## D5. Notification trigger: deterministic gate + LLM content

**Chose:** A relative-probability-delta threshold decides WHEN to notify (testable,
explainable). The LLM decides WHAT to say.

**Why:** Deterministic gate is unit-testable without an LLM. LLM-as-gatekeeper is untestable
and can flood or go silent. This is the FDE pattern: don't trust AI with decisions, only with
communication.

**Rejected:** LLM-judged interestingness (nondeterministic, untestable, flood/silent failure
modes); absolute delta threshold (dumb across the probability range — 5%→10% is huge,
80%→85% is noise).

**Another week:** Learn the threshold per team from user behavior (did they dismiss the
notification?).

**Review answer:** "I chose a deterministic gate because LLM-as-gatekeeper is untestable and
has bad failure modes — it can flood or go silent. Relative delta fixes the low-probability
problem: 5%→6% is 20% relative, 80%→85% is 6% relative."

---

## D6. Team preferences: config file, default threshold overridable per team

**Chose:** TOML/JSON config with teams to follow + a sane default threshold, overridable per
team.

**Why:** ~5 extra lines, shows product thinking (different markets have different
volatility). Keeps config out of code.

**Rejected:** Hardcoded teams (inflexible); database-backed preferences (overkill for a PoC).

---

## D7. Scope staging: must-have endpoint + nice-to-have watcher

**Chose:** Ship the on-demand JSON endpoint first (`curl /team/brazil` → narrative JSON).
Layer the background watcher + macOS notifications on top.

**Why:** The must-have alone satisfies the exercise. If the WebSocket or notifications eat
time, we still have a complete submission. The watcher is the demo-day wow factor but not
load-bearing.

**Rejected:** Building both in parallel (risks an unfinished submission).

---

## D8. Kalshi auth: public market data needs NO auth

**Chose:** Use Kalshi's public REST endpoints (markets, events, order books, trades) with
zero authentication.

**Why:** Kalshi's public market data requires no auth. RSA-key auth is only for
trading/portfolio endpoints, which are out of scope. This is a major de-risk — the must-have
can be built with no credentials at all.

**Rejected:** Implementing RSA signing upfront (unnecessary for read-only market data; would
have eaten the first 30 min for nothing).

**Review answer:** "Kalshi's public market data needs no auth — markets, events, order books,
trades are all public. RSA signing is only for trading, which is out of scope. I verified
this from their API docs before writing any auth code."

**Open question:** Does the WebSocket require auth for public channels (`ticker`,
`orderbook_delta`)? The docs say auth uses RSA-signed headers during the WS upgrade, but it's
unclear if this applies to public channels or only private ones (`fill`). Needs verification
during the spike.

---

## D9. Kalshi data handling: probabilities in cents, never floats

**Chose:** Treat Kalshi money/probability values as integers (cents, 0-100) or decimal
strings. Convert to float only for display, never for arithmetic.

**Why:** Kalshi explicitly warns against floating-point arithmetic on API values.
Probabilities come as cents (0-100); we convert to 0-1 for our output but keep the integer
representation internally.

**Review answer:** "Kalshi returns probabilities as integers in cents (0-100) to avoid float
precision issues. I convert to a 0-1 float only at the output boundary, never for internal
arithmetic."

---

## D10. Real-time: WebSocket preferred, REST polling as fallback

**Chose:** Use the WebSocket (`wss://api.elections.kalshi.com/trade-api/ws/v2`) for the
background watcher if auth allows. Fall back to polling REST every N seconds if WS is painful.

**Why:** Kalshi recommends WS for real-time and rate-limits tight REST polling. But REST rate
limit is 100 req/sec — generous for our scale (a few teams). REST polling is simpler, more
debuggable, and a clean tradeoff to discuss.

**Rejected:** WS-only (risky — auth question unresolved, more failure modes); REST-only
without considering WS (leaves real-time story on the table).

**Review answer:** "I'd prefer WebSocket for real-time, but REST polling at our scale is
totally viable — 100 req/sec is generous when you're watching 3-5 teams. The tradeoff is
elegance vs. debuggability. I'd start with REST and upgrade to WS if the demo needs it."

---

## D11. Tech stack: Python 3.13+, uv, FastAPI, ruff, pytest

**Chose:** FastAPI for the service, uv for package management, ruff for lint/format, pytest
for tests.

**Why:** FastAPI gives typed endpoints + auto docs (developer-friendly interface). uv is the
modern Python pkg manager. ruff is fast and replaces black+isort+flake8. pytest is standard.

**Rejected:** Flask (no built-in type validation/docs); pip (slower, no lockfile by
default); black+isort+flake8 (slower, more tools).

---

## D12. Delta computation: `previous_price_dollars` + in-memory rolling window

**Spike finding:** The candlesticks endpoint (`/markets/{ticker}/candlesticks`) returns 404
at the documented path. May require auth or a different URL. Not reliable for v1.

**Chose:** Use `previous_price_dollars` from the market object for a quick delta, plus an
in-memory rolling window of `(timestamp, prob)` samples for a controlled 1m delta.

**Why:** `previous_price_dollars` is on every market object — no extra API call. But we don't
control what "previous" means (previous trade? previous close?). The in-memory window gives
us a controlled 1m delta once enough history accumulates. Cold start: `delta_1m` is null
with a narrative explaining "we've been tracking this for N minutes."

**Window size — 60s, not 1h (revised):** Originally 1h (SPEC). Reconciled to 60s after
realizing a World Cup match is 90 minutes — a 1h window can't distinguish a goal 3 minutes
ago from one 50 minutes ago. 60s is maximally responsive: a goal-sized swing shows up in
delta within a minute. The threshold (D5, 20% relative) works at 60s — only a real swing
crosses it in a minute, not noise. The `_WINDOW_MAX_AGE=120s` buffer (2x the threshold)
avoids the pruning-equals-threshold trap: if max age == span threshold (both 60s), samples
prune the instant span reaches 60s, so `delta_1m` is structurally always-null for any poll
interval > a few seconds. The 2x buffer lets span reach 60s while old samples are retained.

**Strategy:**
1. On each poll, store `(timestamp, last_price_dollars)` in a rolling window (max 2m old —
   the 2x buffer over the 60s threshold, see above).
2. `delta_1m` = current price - oldest sample in window (if window spans ≥60s).
3. If window < 60s, `delta_1m` = null. Narrative says "We've been tracking for N minutes."
4. Also expose `previous_price_dollars` as a `previous_price` field for reference.

**Rejected:** Candlesticks endpoint (404 at documented path — unreliable); trusting
`previous_price_dollars` alone (unknown window).

**Review answer:** "The candlesticks endpoint returned 404 during my spike, so I use an
in-memory rolling window of price samples. Cold start returns null for delta_1m with a
narrative explaining the tracking duration. I also expose `previous_price_dollars` from the
market object for reference. The rolling window sets up the notification watcher naturally —
it needs state anyway to detect threshold crossings."

---

## D13. Output schema: rich, flat, with volume from market object

**Chose:** Flat JSON with structured fields + narrative. Include volume/liquidity from the
market object (not the order book).

```json
{
  "team": "brazil",
  "opponent": "argentina",
  "match_status": "ongoing",
  "market": "BRAZIL-ARG-...-WIN",
  "current_prob": 0.34,
  "delta_1m": 0.06,
  "volume": 125000,
  "liquidity": 5000,
  "last_updated": "2026-06-23T21:00:00Z",
  "narrative": "Brazil's odds surged 18% after Vinícius's 23' goal..."
}
```

**Why:** Flat is easier to explain than nested. Each field has an obvious purpose. Volume
and liquidity come from the `/markets` response directly — no order-book fetch needed. A
developer gets a complete picture in one call (the FDE value prop).

**Rejected:** Nested `{"data": {...}, "narrative": "..."}` (extra layer to explain);
minimal 4-field schema (too thin to defend — can't tell what match, when, or how reliable);
order-book-derived liquidity (overkill, we don't want betting internals).

**Review answer:** "Flat JSON because nesting adds explanation overhead without value.
Volume and liquidity come free from the market object — they signal whether to trust the
probability without requiring an order-book fetch."

---

## D14. LLM provider: OpenRouter (swappable)

**Chose:** OpenRouter as the LLM gateway. Model configurable via env var (e.g.
`LLM_MODEL=anthropic/claude-3.5-haiku`).

**Why:** OpenRouter makes "the LLM is a swappable commodity" literal — switch providers by
changing one model string, no SDK swap. Better review answer than "I picked OpenAI." The LLM
is isolated behind a single function so swapping is a one-line change.

**Rejected:** OpenAI directly (seen 20 times, locks you to one provider); local/Ollama
(setup friction, slower, worse prose quality, adds failure mode of model-not-running).

**Review answer:** "I used OpenRouter so the LLM is literally swappable — one env var changes
the model. The LLM is isolated behind a single function call. It's a commodity transformation
step, not the differentiator. Kalshi is the differentiator."

---

## D15. Market selection: in-play first, then tournament-winner fallback

**Chose:** `/team/brazil` → find Brazil's nearest in-play match. If none, fall back to
tournament-winner market for that team. User specifies country only, not opponent.

**Why:** Singular response (matches our JSON-with-narrative decision). Clear query pattern.
The fallback handles the "no match right now" case without returning empty — note it in the
narrative ("Brazil has no upcoming match; their tournament-winner odds are...").

**Rejected:** All active markets as a list (fan doesn't want to parse 3 narratives);
primary+secondary split (more complex, less clean for demo).

**Review answer:** "Brazil maps to their current in-play match first. If no match is live, I
fall back to tournament-winner odds and note it in the narrative. One response, one primary
narrative. The fallback means we never return empty."

**Revision (Jun 25):** The fallback was a one-line `or`: `fetch_per_match_market(pm) or fetch_tournament_winner_market(tw)`. When I threaded `match_date` back as a tuple (D26 fix below), `or` broke — a tuple is truthy even when it holds Nones, so the fallback would never fire. Restructured to an explicit `if pm_result is not None: return it; else fall back`. New review answer: "I used `or` for the fallback chain; threading match_date as a tuple made `or` incorrect because tuples are truthy, so I made the fallback explicit. The lesson: `or` is only safe for falsy-on-failure return types — None, empty, 0 — not tuples."

---

## D16. WebSocket auth: required even for public channels

**Finding:** Kalshi's WebSocket docs state: "Authentication is required to establish the
connection; include API key headers during the WebSocket handshake. Some channels carry only
public market data, but the connection itself still requires authentication."

**Impact:** The background watcher needs RSA auth if using WebSocket. REST polling remains
auth-free for public market data.

**Decision:** Start with REST polling for the watcher (no auth needed, simpler, 100 req/sec
is generous for 3-5 teams). Upgrade to WebSocket only if the demo needs sub-second latency
and we've already implemented RSA signing.

---

## Spike findings: Kalshi API (June 23, 2026)

### Per-match markets EXIST (KXWCGAME series)

`KXWCGAME` series has 50+ events, each a specific World Cup match:
- Ticker format: `KXWCGAME-26JUN{DD}{TEAM1_3LETTER}{TEAM2_3LETTER}`
- Examples: `KXWCGAME-26JUN27JORARG` (Jordan vs Argentina, Jun 27),
  `KXWCGAME-26JUN24SCOBRA` (Scotland vs Brazil, Jun 24)
- Each match event has 3 markets: `{EVENT}-{TEAM1}`, `{EVENT}-{TEAM2}`, `{EVENT}-TIE`
- Markets have live prices, volume, bid/ask — all as decimal strings
- Example: `KXWCGAME-26JUN25TURUSA-USA` last_price=$0.53, volume=7M

### Tournament-winner markets (KXMENWORLDCUP series)

48 markets, one per country. Ticker format: `KXMENWORLDCUP-26-{2LETTER_CODE}`
- Top teams: France 20.3%, Argentina 14.2%, Spain 13.4%, England 10.1%, Brazil 5.0%
- Brazil's ticker is `KXMENWORLDCUP-26-BR` (NOT `-BRA` — 2-letter codes, not 3)
- High volume: USA 43M, Portugal 25M, Netherlands 22M

**Team code inconsistency:** Tournament-winner uses 2-letter codes (BR, FR, AR, GB);
per-match uses 3-letter codes (BRA, ARG, FRA, ENG). We need a mapping table.

### Market object fields (key ones)

All prices/volumes are **decimal strings** (never floats for arithmetic):
- `last_price_dollars` — current probability as USD (0.50 = 50% chance)
- `previous_price_dollars` — previous price (unknown window — previous trade? previous close?)
- `yes_bid_dollars` / `yes_ask_dollars` — best bid/ask
- `volume_fp` — total volume (fixed-point string)
- `volume_24h_fp` — 24h volume
- `liquidity_dollars` — available liquidity
- `open_interest_fp` — open interest
- `status` — "active", "closed", "settled"
- `yes_sub_title` / `no_sub_title` — human-readable team name
- `custom_strike.soccer_team` — UUID for the team

### Candlesticks endpoint: 404

`/markets/{ticker}/candlesticks` returns 404 at the documented path. May need auth or
different URL. Not reliable for v1 — using in-memory rolling window instead (see D12).

### No soccer milestones / game stats

Milestones endpoint only returns NBA, NFL, golf, tennis — no soccer. Kalshi's built-in game
stats don't cover the World Cup. **We need football-data.org for match events.**

### No auth needed for all of the above

Every endpoint we tested (events, markets, series, milestones) works with zero auth.

---

## Open questions (to resolve during the build)

1. **Notification threshold value** — start with 20% relative delta, tune during spike.

---

## D17. Error handling: 200 with degraded narrative, never a 5xx

**Chose:** All failures return HTTP 200 with degraded data + explanatory narrative. The
narrative field is always present, always human-readable, even in failure.

**Three failure modes:**
- **Kalshi down/garbage/timeout:** `current_prob: null`, `narrative: "Market data is
  temporarily unavailable."`
- **No market found for team:** `current_prob: null`, `narrative: "No active market found for
  Uruguay."`
- **LLM fails (OpenRouter down, rate limit, garbage):** Structured data intact, narrative
  falls back to deterministic template generated from the data: "Brazil's current
  probability is 5.0%, down from 5.2% previously. Volume: 12.5M."

**Why:** A 200 with null fields + explanatory narrative is more useful to a developer building
on top than a 5xx — their client doesn't crash, they can display "data unavailable" to the
end user. The structured fields are always the source of truth; the narrative always explains
what happened. "The narrative field is always human-readable, even when the data isn't."

**Rejected:** 503 for Kalshi down (client crashes); 404 for no market (says "your request was
wrong" when really "we looked, nothing's there"); 500 for LLM failure (structured data is
fine, only prose failed — no reason to fail the whole response); `narrative: null` for LLM
failure (loses the always-present-narrative principle).

**Review answer:** "Every failure returns 200 with degraded data. The narrative field is
always present and always explains what happened — 'market data unavailable,' 'no market
found,' or a deterministic template if the LLM failed. The structured fields are the source
of truth; the narrative is always human-readable. A developer building on top never gets a
crash — they get null fields and an explanation."

---

## D18. LLM prompt: constrained input, output verification

**Chose:** Structured prompt with explicit data + hard constraint against invention. Output
verified against source data; falls back to template on mismatch.

**Prompt structure:**
- **System prompt:** "You are a sports commentator writing for a casual fan. Use ONLY the
  facts provided. Do not invent events, scores, or statistics. If the facts are thin, keep
  it short."
- **User prompt:** The JSON data (team, opponent, prob, delta, events, volume).
- **Output:** 1-3 sentences.

**Hallucination guard:**
- After the LLM returns, extract numbers from the narrative via regex.
- Compare extracted numbers to the input data.
- If mismatch (narrative says "34%" but data says 5%, or narrative mentions an event not in
  input), fall back to the deterministic template narrative.
- This is testable and explainable.

**Why:** Constrained prompt limits what the LLM can say. Verification catches what slips
through. Template fallback means hallucination never reaches the user. The structured fields
are always correct regardless of what the LLM does.

**Rejected:** Free-form prompt (maximum hallucination risk); pure template (no "human wrote
it" feel, defeats the purpose); no verification (blind trust, undefensible).

**Review answer:** "I constrain the LLM to the provided facts — the system prompt explicitly
forbids invention. After the LLM returns, I extract numbers from the narrative and compare
them to the source data. If there's a mismatch, I fall back to a deterministic template. The
structured fields in the response are always the source of truth — if the LLM hallucinates,
the data still shows the real numbers and the narrative gets replaced with a template."

---

## D19. Caching: in-memory TTL cache, 30s default

**Chose:** In-memory dict with timestamps. 30s TTL, configurable via env (`CACHE_TTL_SECONDS`).

**Why:** 30s is fresh enough for a casual fan, reduces Kalshi load. In-memory dict is ~15
lines, explainable, no dependency. At our scale (one server, a few teams) this is sufficient.

**Another week:** Conditional caching based on match status — cache aggressively when no
match is in play (tournament-winner odds don't move fast between matches), cache lightly or
not at all when a match is live. Product-aware improvement.

**Rejected:** No cache (wasteful, no caching story for review); library like `cachetools`
(overkill for one cache, adds a dependency for ~15 lines of code we can explain).

**Review answer:** "I use an in-memory dict with a 30-second TTL. Fresh enough for a casual
fan, reduces Kalshi calls. With another week I'd make it conditional — cache aggressively
between matches, lightly during live play. At this scale a dict is sufficient; I didn't need
a caching library."

---

## D20. Project structure: start single-file, split if unwieldy, config separate

**Chose:** Start with `main.py` as the single application file. `config.py` (or `config.toml`
+ a loader) is separate from the start. Extract a module only if a section clearly wants its
own file (e.g., the Kalshi client grows past ~80 lines).

**Why:** The evaluators weight "understanding of what was built" highest. A single file they
can scroll through top-to-bottom is easier to grasp than 6 files to jump between. In a
60-minute review, "let me open the one file" is faster than "let me find the right module."
Config is separate because team preferences belong in a config file (per AGENTS.md), and
config-as-code is easier to explain than config-as-data-structure.

**Rejected:** Modules from the start (setup overhead, import ceremony, architecture theater
for a PoC); strictly single-file with config hardcoded (inflexible, violates the config-file
decision).

**Review answer:** "I started with one file because the evaluators weight understanding
highest — a single scrollable file is easier to grasp. Config is separate because team
preferences belong in a config file. If the Kalshi client had grown past 80 lines, I would
have extracted it, but at this scale one file was sufficient."

---

## D21. Testing strategy: unit tests only, mock everything external

**Chose:** Unit tests for deterministic logic, mocked API fixtures, no network calls in
tests. Manual smoke test against real Kalshi API as a separate step.

**What to test (Given-When-Then before writing, per AGENTS.md):**
- **Notification gate (relative delta threshold):** happy path (delta exceeds threshold →
  notify), below threshold (no notify), boundary (exactly at threshold), zero delta,
  negative delta, null delta (cold start), per-team override threshold.
- **Data transforms:** cents string → 0-1 float, delta computation from rolling window,
  empty/missing fields, garbage input.
- **Hallucination guard:** narrative with correct numbers → passes, narrative with wrong
  numbers → falls back to template, narrative with no numbers → passes, narrative with
  extra events not in input → falls back.
- **Template fallback:** generates readable narrative from structured data, handles null
  fields gracefully.

**Why:** The evaluators care about understanding the testing strategy, not coverage metrics.
The deterministic logic is where bugs would hide — the gate, the transforms, the guard.
Mocked fixtures mean tests are fast and deterministic. A flaky integration test that fails
because Kalshi is down would undermine confidence, not build it.

**Rejected:** Integration tests with real API calls (flaky, requires network); VCR/cassettes
(adds dependency, recording setup overkill for PoC).

**Review answer:** "I unit-test the deterministic parts — the gate, the data transforms, the
hallucination guard, the template fallback — with mocked API fixtures. No network calls in
tests. I didn't write integration tests because the API shape is verified during the spike
and the deterministic logic is where bugs would hide. A flaky integration test that fails
because Kalshi is down would undermine confidence."

---

## D22. Team code mapping: hardcoded dict, verified against Kalshi

**Chose:** Hardcoded dict in `config.py` mapping user input → 2-letter and 3-letter codes.
Each mapping verified manually against Kalshi's actual tickers during the build.

```python
TEAMS = {
    "brazil": {"tw": "BR", "pm": "BRA"},
    "argentina": {"tw": "AR", "pm": "ARG"},
    # ... ~48 entries, each verified against Kalshi
}
```

**Lookup logic:**
- Tournament-winner: `KXMENWORLDCUP-26-{tw}` (e.g., `KXMENWORLDCUP-26-BR`)
- Per-match: search KXWCGAME event tickers for ones containing `pm` (e.g., `BRA` in
  `KXWCGAME-26JUN24SCOBRA`)

**Why:** Fast to build, easy to explain ("here's the mapping table"), zero failure modes (no
startup fetch that might fail). World Cup teams are known — the dict won't go stale during
the project. Manual verification against Kalshi ensures we don't guess codes (the spike
showed `KXMENWORLDCUP-26-BRA` returns 404 — it's `BR`, not `BRA`).

**Rejected:** Dynamic mapping from Kalshi at startup (adds startup complexity, depends on
title parsing); ISO 3166 library (overkill for ~48 entries, and Kalshi's codes don't always
match ISO — `GB` for England is not standard ISO).

**Review answer:** "I hardcode the team mapping in config — 48 entries, each verified against
Kalshi's actual tickers. The spike showed their codes aren't predictable: Brazil is `BR` in
tournament-winner but `BRA` in per-match, and England is `GB` which isn't standard ISO. A
hardcoded verified dict has zero failure modes compared to a startup fetch."

---

## D23. Scaffold: uv, httpx async, inline tool config, pinned Python

**Chose:** `pyproject.toml` with inline ruff + pytest config (no separate `ruff.toml`).
`.python-version` pinning 3.13. httpx async as the HTTP client.

**Why:**
- Inline config = one file to explain in review ("here's the project, the deps, and the tool
  config — all in `pyproject.toml`"). A separate `ruff.toml` is one more file to jump between
  for zero benefit at this scale.
- `.python-version` pin: my system default `python3` is 3.14, but AGENTS.md mandates 3.13.
  Without the pin, `uv sync` grabs 3.14 and I get subtle stdlib/typing drift. The pin makes
  the requirement machine-checked, not hope-based.
- httpx async: the FastAPI endpoint is `async def`, so an async client is the idiomatic fit —
  `await client.get()` suspends at network waits, letting the event loop serve other
  requests. Sync `requests` inside `async def` blocks the whole loop (the dangerous
  footgun). The alternative — sync `requests` + `run_in_executor` — is more code to explain,
  not less.

**Rejected:** separate `ruff.toml` (extra file, no benefit at this scale); `requests` sync
(blocks the event loop inside `async def`, or needs `run_in_executor` ceremony); no Python
pin (would silently use 3.14, violating the AGENTS.md convention).

**Review answer:** "I pin Python 3.13 via `.python-version` because my system default is
3.14 and the project convention is 3.13 — the pin makes it machine-checked. I use httpx
async because the endpoint is `async def`, so an async client suspends at network waits
instead of blocking the loop. Tool config lives inline in `pyproject.toml` — one file to
explain, no ceremony at this scale."

---

## D24. Response schema: Pydantic model in models.py

**Chose:** A Pydantic `BaseModel` (`TeamSentiment`) in a separate `models.py`, used as
FastAPI's `response_model`.

**Why:**
- Makes D3 ("typed, documented, predictable interface") literally true — FastAPI generates
  Swagger/OpenAPI docs at `/docs` from the model, validates the output at the framework
  boundary, and gives a reviewer a single file to see the exact contract.
- Optional fields with `None` defaults encode D17 (degraded narrative) in the schema itself
  — the failure mode is visible in the contract, not hidden in code. `narrative` is the only
  required field (always present, always human-readable).
- Building it before the components that produce it is intentional: top-down. The schema is
  the contract that transforms, LLM, guard, and endpoint all produce toward.

**Three-file split (extends D20):** `config.py` = inputs (team mappings, env vars),
`models.py` = outputs (response contract), `main.py` = the wiring. Each has one job.

**Rejected:** returning a bare `dict` (what the SPEC showed) — no contract, no validation,
no auto-docs, the FDE story would be aspirational not literal; `TypedDict` (typing but no
validation or Swagger generation); putting the model in `main.py` (mixes the contract with
the wiring, harder to point at in review).

**Review answer:** "The response is a Pydantic model used as FastAPI's `response_model` —
it validates the output at the framework boundary and generates the Swagger docs at
`/docs`. Every optional field defaults to None, which encodes the degraded-narrative
pattern in the schema itself: when Kalshi is down, the structured fields are null but
`narrative` is always present. I defined the schema before the components that produce it so
every component knew its target shape."

---

## D25. Kalshi client return type: raw dict, no boundary model

**Chose:** The Kalshi client (`fetch_market_for_team` and its helpers) returns the raw
Kalshi market `dict | None`. No intermediate `KalshiMarket` Pydantic model. Structure is
imposed one layer down, in the transforms (Component 3), via `.get()` with defaults.

**Why:** A typed boundary model (the "parse, don't validate" pattern) *raises* on schema
drift — if Kalshi renames `last_price_dollars`, Pydantic throws at the client. But D17
mandates "always 200, degrade gracefully." We *want* a missing field to silently become
`None`, propagate to the transforms, and surface as a "data unavailable" narrative. The
model's signature virtue — loud, early failure — is a vice for this product. Keeping the
client raw also makes the FDE story literal: the client hands off raw API bytes; the
transforms are where *we* impose *our* structure. Two visible layers = "I bridge raw
complexity to human output." Collapsing them hides the seam, and adds a second model to
explain in a 60-minute review where understanding is weighted highest.

**Rejected:** A `KalshiMarket` Pydantic model at the client boundary. Real virtue: catches
Kalshi schema drift loud and early instead of silently masking a rename with `.get(default)`.
Defensible position — but it fights D17 and needs explicit try/except to degrade, adding
ceremony that undercuts the always-200 principle for a PoC.

**Another week:** Add the boundary model once the product is stable and drift detection
matters more than always-degrade. Right now `.get()` defaults could silently mask a Kalshi
schema change — acceptable for v1, worth tightening later.

**Review answer:** "The client returns Kalshi's raw object; the transforms pull fields with
`.get()` and handle missing data. I deliberately did NOT model at the client boundary
because a rigid model would raise on schema drift, and our principle is degrade-gracefully,
never 5xx. The tradeoff: a rename could silently produce nulls. With another week I'd add a
boundary model for drift detection once 'always 200' isn't the dominant constraint. Note
this is only about an *input* model — `TeamSentiment`, the output contract, is still a
Pydantic model so FastAPI validates the response and generates Swagger docs."

---

## D26. Match selection: nearest to today (when multiple per-match events exist)

**Chose:** When a team has multiple KXWCGAME match events (e.g. Brazil has Jun 13, 19, 24),
parse the date from each event ticker and pick the match **nearest to today** —
`min(abs(match_date - date.today()))`.

**Why:** "In-play first" (D15) needs a concrete selection rule when a team has several
matches. All KXWCGAME markets have `status: 'active'` (even past matches, until settled), so
status alone can't distinguish "live now" from "last week." Nearest-to-today is the best
proxy for "most relevant right now" — a match today or tomorrow is more useful than one last
week. The date is parsed from the event ticker itself (`ticker[9:16]` = `"26JUN24"` →
`date(2026, 6, 24)`), so no extra API call is needed.

**Rejected:**
- *First active market* — simplest, but all markets are `'active'` until settled, so it might
  return a past match. Weak "in-play" story.
- *Most recently updated* (`last_updated_ts`) — better proxy for "active right now" but
  requires fetching all markets for all matching events (more API calls) just to compare
  timestamps. Overkill for a PoC.

**Implementation note:** The date is extracted by fixed-position string slicing
(`ticker[9:16]`), verified against 72 live KXWCGAME tickers — all consistent. Fragile if
Kalshi changes the format (e.g. 4-digit year); the defense is to assert the prefix
(`startswith("KXWCGAME-")`) and fail loud rather than silently slice garbage. Not done in v1
for simplicity; noted as another-week hardening.

**Closest-match tracking:** Manual `(event_ticker, match_date)` tuple, replaced when a
candidate is nearer — not `min(matches, key=...)`. Slightly more verbose but the comparison
is visible and explainable line-by-line in review, which matters more than brevity here.

**Review answer:** "All KXWCGAME markets stay 'active' until settled, so status can't tell
live from past. I parse the date from the event ticker — the format is KXWCGAME-26JUN24SCOBRA,
so chars 9-16 are the date — and pick the match nearest today. It's fixed-position slicing,
verified against 72 live tickers. If Kalshi changes the format I'd assert the prefix and fail
loud rather than silently slice garbage. The fallback to tournament-winner only triggers when
a team has zero per-match events — rare for World Cup teams, but the `or` chain handles it."

**Revision (Jun 25) — match_status is now date-derived, not status-derived.** The original code
mapped Kalshi `status: "active"` -> `match_status: "scheduled"`, which D26 *itself* proved wrong
(every market is 'active' until settled). A match today (KXWCGAME-26JUN25JPNSWE) was labeled
"upcoming" while the game was ongoing — caught in live testing. Fix: `fetch_per_match_market`
now returns `(market, match_date)` (the date was previously parsed for selection then discarded
via `chosen_ticker, _ = closest`). `extract_market_fields(market, match_date)` compares to
`date.today()`: future -> "scheduled", today -> "ongoing", past -> "closed". A settled status
("finalized"/"closed"/"settled") overrides the date. When `match_date` is None (tournament-winner,
or a caller that didn't thread it), the old "active" -> "scheduled" fallback applies for
backward-compat. Six new unit tests cover each branch (future/today/past/settled/no-date/tournament).

**Known imprecision (name it before the reviewer does):** we have the match *date*, not kickoff
*time*. So "ongoing" for today is a best-guess — it could be an 8pm kickoff not yet started. The
real fix needs a kickoff timestamp Kalshi doesn't expose in the market object. Honest review
answer: "Today's date gives 'ongoing', which is right for a match in play but premature for an
evening kickoff. Kalshi doesn't expose kickoff time in the market object, so within-day
resolution is the limit of what the date can tell us. I'd accept the imprecision for v1 rather
than guess a time."

**Second conflation to name:** "closed" for a past-but-unsettled market means "the match has been
played", not "the market has settled" — Kalshi keeps the market tradeable until settled. Same
conflation D26 flagged; acceptable for v1 since the fan-facing narrative cares about the sporting
result, not the market's settlement state.

---

## D27. Data transforms: integer basis points internally, float only at the JSON boundary

**Chose:** All probability arithmetic uses integer basis points (0–10000 bp; 5% = 500 bp).
`price_to_prob` converts Kalshi's dollar-string to bp via `int(Decimal(s) * 10000)` — Decimal
is used *only* at the string→int seam, never for arithmetic between values. `compute_delta`
does integer subtraction (`current_bp − oldest_bp`) and returns an int bp delta. The endpoint
divides by 10000 → float at the one output line where the `TeamSentiment` Pydantic model needs
a float. Volume and liquidity are parsed to int the same way (`int(Decimal(s))`).

**Why:** D9 mandates "no float arithmetic." `0.36 − 0.30` in float gives `0.06000000000000005` —
silent corruption. Integer bp arithmetic is drift-free by definition: `3600 − 3000 = 600`,
always. Using Decimal for *arithmetic* would also be drift-free but invites questions about
Decimal context/precision (default 28 digits — fine, but it's a question to answer). Integer
bp has no such surface area. The ×10000 scaling is one convention to explain: Kalshi quotes to
4 decimals of dollars = 2 decimals of percent = 1 bp, so ×10000 gives integer bp.

**The float-drift trap (test #9 catches it):** `int(float("0.0537") * 10000)` can yield `536`
or `537.0000001` → `537` depending on drift — truncation silently off-by-ones. The Decimal
seam avoids this: `int(Decimal("0.0537") * 10000) == 537` exactly, every time.

**Range guard:** `price_to_prob` rejects probabilities outside `[0, 1]` (dollars) → `None`.
A probability >100% or <0% is physically impossible — it means Kalshi returned garbage. `None`
propagates to a "data unavailable" narrative (D17). The guard keeps *valid but extreme*
probabilities (99.99% → 9999 bp, 0.01% → 1 bp); it only rejects the *impossible*. This is the
"what happens when Kalshi returns garbage?" answer: the guard catches it, the user sees null +
explanatory narrative, never a bogus 150%.

**Rejected:**
- *Floats internally + `round(…, 4)`* — drift-prone in principle; "I rounded" is weaker than
  "I never used floats for arithmetic."
- *Decimal for arithmetic* — equally drift-free but adds Decimal context/precision as a review
  question. Integer bp is simpler to explain.
- *No range guard* — a >100% probability would surface as a bogus value (e.g., 15000 bp = 150%).
  D17 says degrade gracefully, not surface nonsense.

**Review answer:** "All probability arithmetic is integer basis points — 5% is 500 bp. The
dollar-string is parsed via Decimal at the string→int seam only; arithmetic between values is
plain int subtraction, which is drift-free by definition. Float appears at one line in the
endpoint where the Pydantic model needs it. The range guard rejects impossible probabilities —
if Kalshi returns 150%, the user sees null plus an explanatory narrative, not 150%. Test #9
catches the float-drift trap: `int(float('0.0537') * 10000)` can off-by-one; the Decimal seam
is exact."

---

## D28. Opponent extraction: from market `title`, not event-ticker parsing

**Chose:** `extract_market_fields` parses the opponent from the market's `title` field by
splitting on `" vs "` and using `yes_sub_title` to identify our team. Example:
`"Scotland vs Brazil Winner?"` + `yes_sub_title="Brazil"` → opponent = `"Scotland"`.
Tournament-winner titles have no `" vs "` → opponent = `None`.

**Why:** The `title` field gives the opponent name in human-readable form — no mapping table,
no fixed-position slicing. The alternative (parsing the event ticker `SCOBRA` → `SCO` + `BRA`)
requires the D26 fixed-position slicing we already flagged as fragile, plus a 3-letter-code-
to-team-name mapping. The title approach is one split + one comparison, and degrades to `None`
on any format change (no crash, just missing opponent — D17).

**Verified against live Kalshi data (June 25, 2026):**
- Per-match: `"Scotland vs Brazil Winner?"` → opponent = `"Scotland"` ✓
- Tournament-winner: `"Will the Brazil win the 2026 Men's World Cup?"` → opponent = `None` ✓

**Rejected:** Event-ticker parsing (`KXWCGAME-26JUN24SCOBRA` → split `SCOBRA` into `SCO` +
`BRA`). Fragile fixed-position slicing (D26), and still needs a code→name mapping to give the
user a readable opponent name. More code, more failure modes, worse output.

**Another week:** If Kalshi changes the title format (no `" vs "`), opponent extraction breaks
silently (returns `None`). A more robust approach would cross-reference the event ticker as a
fallback — but that requires the D26 slicing + mapping table. Not worth it for v1; the `None`
fallback is graceful per D17.

**Review answer:** "I extract the opponent from the market's title field — 'Scotland vs Brazil
Winner?' split on ' vs ' gives me both teams, and `yes_sub_title` identifies which is ours. It's
one split and one comparison, and degrades to None if the format changes. I rejected parsing
the event ticker because that's the same fragile fixed-position slicing I already flagged, plus
it needs a code-to-name mapping table. The title gives the human-readable name directly."

---

## D29. LLM client: OpenRouter, Llama 3.1 8B, bounded backoff, httpx async

**Chose:** OpenRouter as the LLM gateway, `meta-llama/llama-3.1-8b-instruct` as the default
model (free, sufficient for a constrained 1-2 sentence narrative), httpx async for the call,
bounded backoff on 429/5xx (2 retries, 1s then 2s), then raise → template fallback.

**Model choice:** Llama 3.1 8B (free on OpenRouter, 8B params). The prompt is simple and
constrained — turn provided JSON facts into 1-2 sentences. No frontier model needed. OpenRouter
makes it swappable via one env var (`LLM_MODEL`), so upgrading is a config change, not a code
change. Rejected `anthropic/claude-3.5-haiku` (the SPEC default) because that model ID 404s on
OpenRouter — the actual Haiku IDs are `anthropic/claude-3-haiku` or `~anthropic/claude-haiku-
latest`. Chose the free Llama over paid Haiku because the task doesn't warrant paid inference.

**Bounded backoff (the 429 decision):** On 429 or 5xx, retry up to 2 times with `asyncio.sleep
(2 ** attempt)` — 1s, then 2s. On 4xx (except 429), raise immediately (bad auth won't fix
itself). After retries exhausted, raise. The caller (Component 6 endpoint) catches and falls
back to `template_narrative` (D17). Max added latency on a down provider: 3s. This is the
review answer to "what happens on 429?" — *transient errors retry, persistent errors degrade
gracefully.* Rejected immediate-raise (weaker review answer, throws away a fixable blip) and
aggressive 3+ retry (7s+ latency on a down provider — too long for a curl).

**httpx async, not `requests`:** `generate_narrative` is `async def`. Sync `requests.post()`
blocks the entire event loop for the whole HTTP round-trip — no other request gets served while
OpenRouter thinks (the D23 footgun). httpx async suspends at the network wait. The function
reuses `_get_client()`, the httpx singleton from Component 2, for connection pooling.

**System prompt (D18):** "You are a sports commentator writing for a casual fan. Use ONLY the
facts provided. Do not invent events, scores, or statistics. If the facts are thin, keep it
short." Four constraints in one sentence: who, use-only-provided, don't-invent-specifics,
keep-short-when-thin. Rejected the vaguer "Do not hallucinate information" (the LLM still
fabricated color commentary with that wording).

**Known limitation — invented prose:** The tightened prompt reduced numeric hallucination to
zero (numbers match the data across runs), but the 8B model still invents color commentary
("18 million viewers tuning in" — confusing trading *volume* with viewers; "stadium's packed"
— no such data). The hallucination guard (Component 5) catches wrong *numbers* via regex but
not invented *events*. This is a known gap with a crisp review answer: "The guard catches
numeric hallucinations. Invented prose is a gap I'd address with a stricter prompt, a
fact-checking pass, or a larger model with better instruction-following. With another week I'd
add an entity-level check that verifies every noun phrase in the narrative against the input
fields, not just the numbers."

**Revision (Jun 25) — "viewers" hallucination FIXED via labeled prompt.** Root cause was
`generate_narrative` sending `json.dumps(data)` — a bare `{"volume": 18147393, ...}` with no
labels, so the 8B model guessed what the number meant and guessed "viewers." Fix: replaced
`json.dumps` with `_build_user_prompt(data)`, a labeled prompt that defines each field. The
volume line reads "Volume: 19.5M dollars traded on the market. This is the total dollars
wagered (betting activity)." Design choice: I label volume *positively* ("betting activity")
rather than saying "not viewers" — naming a forbidden concept can prime an 8B model toward it.
None fields are OMITTED (volume absent -> no volume line at all -> no surface to misread).
Verified live: `/team/japan` narrative now says "over $19.5 million traded on the prediction
market" instead of "19.4 million fans tuning in." This is the "stricter prompt" the original
D29 review answer prescribed — applied. Eight new unit tests lock down the prompt builder
(volume labeled, volume omitted when None, None-opponent/delta/prob handled, delta up/down/
zero wording). Invented *prose events* (e.g. "stadium's packed") remain a gap — the guard
catches numbers, not nouns — but the highest-impact hallucination (volume-as-viewers) is gone.

**Second prompt revision (Jun 25) — market/match conflation on "ongoing".** Even after the
volume fix, the narrative said "The World Cup prediction market for Japan's match is ongoing"
— conflating the MARKET (tradeable for days before kickoff) with the MATCH. Root cause: the
prompt labeled the field "Match status: ongoing", which the LLM read as the market's status.
Two problems in one label: (1) "Match status" is ambiguous — could be the market's status;
(2) "ongoing" overstates what we know — we have the match DATE, not kickoff TIME, so "today"
is honest but "ongoing" implies in-play. Fix: translate the enum to plain-English game timing
in the prompt — "scheduled" -> "the match is in the future", "ongoing" -> "the match is
today", "closed" -> "the match has been played" — under the label "Game timing" (not "Match
status"). Verified live: narrative now says "Japan takes on Sweden in a World Cup match today"
with no market/match conflation. The structured JSON field stays `match_status: "ongoing"`
(it's documented for developers); only the fan-facing narrative wording changed. Three new
unit tests lock down each status -> wording mapping and assert the ambiguous "Match status:
ongoing" label is gone. Review answer: "The prompt relabels match_status to plain-English game
timing because 'ongoing' under 'Match status' made the LLM say the market was ongoing — the
market's been tradeable for days, what's today is the match. 'Today' is also more honest than
'ongoing' since we have the match date, not the kickoff time, so I can't claim it's in play."

**Rejected:**
- *OpenAI directly* — seen 20 times, locks to one provider, no swap story (D14).
- *Paid Haiku* — the task doesn't warrant paid inference; the free Llama is adequate and the
  model is swappable.
- *Sync `requests`* — blocks the event loop inside `async def` (D23 footgun).
- *Immediate raise on 429* — weaker review answer; a transient blip shouldn't throw away the
  LLM narrative when 1-2s fixes it.
- *Aggressive 3+ retry backoff* — 7s+ latency on a down provider is too long for a curl.

**Review answer:** "I use OpenRouter so the LLM is swappable via one env var. The default is
Llama 3.1 8B — free, and the prompt is simple enough that a frontier model isn't warranted.
The call uses httpx async because `generate_narrative` is `async def` — sync `requests` would
block the event loop. On 429 or 5xx I retry twice with 1s/2s backoff, then raise; the endpoint
catches and falls back to a deterministic template. Max added latency on a down provider is 3s.
The system prompt constrains the LLM to provided facts, but an 8B model still invents color
commentary — the hallucination guard catches wrong numbers, and invented prose is a known gap
I'd address with another week."

---

## D30. Hallucination guard: global-set number comparison + deterministic template fallback

**Chose:** Two functions. `verify_narrative(narrative, source_data) -> bool` extracts every
number from the LLM prose via regex and checks each against an *allowed set* built from
`source_data` in every plausible form. `template_narrative(data) -> str` is the deterministic
fallback the endpoint swaps in when `verify_narrative` returns `False` or the LLM call itself
failed (D17, D29).

**Global-set comparison (the core decision):** The allowed set is built from the display-format
source data as:
- `current_prob p` → `p` and `p*100` (LLM may write `0.05` or `5.0`)
- `delta_1m d` → `d` and `d*100`
- **if both present** → `previous = p - d` and `previous*100` (the LLM expresses delta
  *indirectly* as "from X% to Y%", so the previous probability must be in the allowed set)
- `volume v` → `v`, `v/1e6`, `v/1e9` (raw, millions, billions)

Each prose number must be `math.isclose(n, a, rel_tol=0.02, abs_tol=0.05)` to *some* allowed
number. Any mismatch → `False` → template fallback. Empty narrative → `False` (checked
*before* the no-numbers rule, or `""` passes vacuously — the ordering bug reviewers probe).
No numbers in prose → `True` (thin narrative is allowed by the system prompt).

**Why global-set, not field-aware:** Field-aware (parse which prose number is the prob vs the
volume, compare field-to-field) catches more — e.g., conflating volume `12.5M` as a
probability `12.5%`. But it requires semantically parsing free-form prose, which is fragile
and hard to defend cold in a 60-minute review. Global-set is ~15 lines, explainable in one
breath: "every number the LLM emits must be close to some number in the data." The
field-conflation gap gets logged as a known limitation rather than hidden behind fragile
parsing.

**`template_narrative` — the always-human-readable guarantee:** Pure f-string assembly. `is
None` checks (not `not p` — `0.0` is a real probability). Omits the up/down clause when delta
is `None` or `0`; omits Volume when volume is `None`. Same input → same string, always. Exact
format: `"Brazil's current probability is 5.0%, down from 5.2% previously. Volume: 12.5M."`
This is the D17 guarantee made concrete — the user always gets something human-readable even
when the LLM hallucinates or the provider is down.

**Two known limitations (named before the reviewer finds them):**
1. **Prose-only inventions aren't caught.** `"Brazil won 3-0"` → caught (`3`, `0` not in
   source). `"Brazil looked dominant today"` → *not* caught (no numbers to contradict).
   Mitigated by the constrained system prompt (D18); logged as a v1 gap. The D29 review answer
   already addresses this: "with another week I'd add an entity-level check that verifies every
   noun phrase against the input fields, not just the numbers."
2. **Field-conflation isn't caught.** Volume `12.5M` misread as `12.5%` prob would pass the
   global-set check (both `12.5` and `12500000` are allowed). Field-aware comparison would
   catch it but adds the fragile prose-parsing rejected above. Logged as a v1 gap.

**TDD:** 15 tests in `tests/test_guard.py`, written before implementation (D21). Covers: correct
numbers pass, wrong numbers fail (the 34%-vs-5% case), no numbers pass, empty fails, invented
statistic fails, all-None source + numberless prose passes, all-None source + numbered prose
fails, rounding tolerance passes; template happy-path exact match, None prob → "unavailable",
None delta omits clause, None volume omits clause, positive delta → "up from", zero prob
shows `0.0%` not "unavailable", determinism. Both functions were stubbed with
`NotImplementedError` and the algorithm in the docstring, then implemented to flip the tests
green.

**Rejected:**
- *Field-aware comparison* — catches field-conflation but requires semantically parsing
  free-form prose. Fragile, hard to defend cold. Catches a rare case at the cost of
  complicating the common case.
- *LLM-as-judge* (ask a second LLM to verify the first) — untestable, adds a second failure
  mode, doubles cost and latency. The deterministic guard is testable and explainable.
- *No verification* — blind trust, undefensible. The whole point of D17 is that the
  structured fields are always the source of truth; the guard makes that promise enforceable.
- *Pure template, no LLM at all* — loses the "human wrote it" feel that's the FDE story (D2).

**Another week:** An entity-level check that extracts noun phrases from the narrative and
verifies each against the input field values — catches prose-only inventions. And field-aware
comparison via a second regex pass that maps prose numbers to their nearest plausible source
field — catches field-conflation. Both are natural extensions of the current regex approach.

**Review answer:** "After the LLM returns, I extract every number from its prose via regex and
check each against an allowed set built from the source data — in every form the LLM might
plausibly use, like `0.05` or `5.0`, `12.5M` or `12500000`, and the previous probability
derived from `p - d` since the LLM expresses delta indirectly as 'from X% to Y%'. Any
mismatch and I throw the prose away and substitute a deterministic template — same input, same
string, always. The user never sees a hallucinated number. Two known gaps I'd name first:
prose-only inventions like 'Brazil looked dominant' aren't caught because there are no numbers
to contradict, and field-conflation like volume misread as probability isn't caught because
both forms are in the allowed set. Both are mitigated by the constrained prompt and logged as
v1 limitations — with another week I'd add an entity-level check for the prose gap and a
field-aware pass for the conflation gap. I rejected field-aware comparison for v1 because
parsing free-form prose is fragile and hard to defend in 60 minutes, and I rejected LLM-as-
judge because it's untestable and adds a second failure mode."

---

## D31. Endpoint: 404 for unknown team, 200-degraded for known-team failures

**Chose:** `GET /team/{team_name}` returns HTTP 404 for an unknown team (not in
`TEAMS`) and HTTP 200 with degraded data + explanatory narrative for a known
team whose data couldn't be fetched or generated. The 200-degraded path covers
D17's three modes: Kalshi down/timeout, no market found, LLM fails/hallucinates.

**Why the split:** D17 rejected 404 for *no-market-found* — a known team with no
active market is "we looked, nothing's there," not "your request was wrong." But
an *unknown* team is a genuine client error: the resource doesn't exist. 404 is
the correct REST signal for that, and it lets clients distinguish "I mistyped the
team" from "Kalshi is down." Conflating them under 200 would hide a fixable user
mistake behind the same status as an infrastructure outage.

**The 404 body is still useful:** it lists all supported teams, so a developer
who mistypes gets immediate, actionable feedback — not an opaque error.

**Rejected:**
- *200 for everything (including unknown teams)* — maximally consistent with
  D17's "never crash the client," but conflates client errors with infrastructure
  failures. A weaker review answer: "I can't tell you why your request failed."
- *404 for no-market-found too* — explicitly rejected in D17; a known team with
  no current market is not a wrong request.

**Cache + rolling window (module-level dicts):**
- `_cache: dict[str, tuple[float, dict | None]]` — team_key → (monotonic timestamp,
  raw market). TTL = `CACHE_TTL_SECONDS` (30s, D19). Failed fetches are NOT cached
  (the next request retries immediately). Uses `time.monotonic()` so cache expiry
  is immune to system clock changes.
- `_rolling: dict[str, list[tuple[float, int]]]` — team_key → [(timestamp, prob_bp),
  ...]. Feeds `compute_delta` (D12). Pruned to the last 120s on each append (_WINDOW_MAX_AGE, 2x the 60s delta threshold). The
  on-demand endpoint populates this as a side effect, so repeated calls for the
  same team eventually yield a non-None `delta_1m` (cold-start None until the
  process has accumulated ≥60s of wall-clock span). The watcher (Component 8) shares this window.
- `team_key` (normalized: lowercase, underscores) is the cache/window key, so
  `/team/Brazil` and `/team/brazil` share state. The response `team` field uses
  `team_key`; the narrative display name uses `team_key.replace("_", " ").title()`
  ("brazil" → "Brazil", "south_korea" → "South Korea").

**bp → float at the boundary (D27):** `current_prob` and `delta_1m` are integer
basis points internally; the endpoint divides by 10000.0 at the single line
where the `TeamSentiment` Pydantic model needs a float. `volume` and `liquidity`
are already ints (no conversion).

**Another week:**
- *Stale-while-error:* serve the last good cache entry (with a longer stale TTL)
  when Kalshi is down, instead of degrading to None. Trades freshness for
  availability during outages.
- *Display-name mapping:* `team_key.replace("_", " ").title()` produces "Usa" for
  "usa". A proper display-name field in the `TEAMS` dict would fix this and give
  full control over capitalization.
- *Endpoint tests (Component 7):* the error-handling flow (200-degraded paths,
  404, cache hit/miss, template fallback) is currently verified by manual curl.
  FastAPI's `TestClient` with mocked `fetch_market_for_team` / `generate_narrative`
  would make this deterministic and part of the suite.

**Review answer:** "I split client errors from infrastructure failures. An
unknown team gets 404 — the resource doesn't exist, and the body lists supported
teams so the developer can self-correct. A known team with any failure gets 200
with null fields and an explanatory narrative, per D17 — the request was valid,
the infrastructure failed. I use an in-memory 30s TTL cache so I don't hammer
Kalshi, and a rolling window that feeds delta — it's cold-start None until I have
an hour of samples. All probability arithmetic is integer basis points; floats
appear at one line where the response model needs them. If I had another week I'd
add stale-while-error caching and endpoint tests with TestClient."

---

## D32. extract_market_fields: characterization tests (Component 7)

**Chose:** 8 characterization tests for `extract_market_fields` in
`tests/test_transforms.py`, written AFTER the implementation existed (not pure
TDD — the SPEC noted "scaffolded without TDD"). Covers: None market → all-None
dict, per-match happy path (opponent + scheduled status + all fields),
tournament-winner (no `" vs "` → opponent/match_status None), missing fields →
graceful None (no KeyError), status `"finalized"` → `"closed"`, opponent when
team is first in title, empty dict `{}` → all None, garbage price string →
`current_prob` None (delegation to `price_to_prob`).

**Why characterization, not TDD:** `extract_market_fields` is mostly
straightforward `.get()` dict access — the load-bearing logic (`price_to_prob`,
`compute_delta`, `verify_narrative`) was TDD'd first (D21, D27, D30). The
interesting behavior here is the D28 opponent extraction (split on `" vs "`,
identify our team via `yes_sub_title`) and the graceful-degradation contract
(missing/garbage → None, never crash). Tests lock these down before review so
"show me a test you wrote" has an answer for the field-extraction layer too.

**What I scaffolded:** A `_per_match_market(**overrides)` helper that builds a
base KXWCGAME market dict — each test overrides only the field it exercises.
Reduces duplication while keeping each test readable as a spec ("given a
finalized per-match market, match_status is closed"). Inline dicts for the
tournament-winner and missing-fields tests (different shapes, not worth the
helper).

**Rejected:** Endpoint tests with FastAPI `TestClient` + mocked
`fetch_market_for_team`/`generate_narrative`. D31 already lists these as
"another week." They'd strengthen the "mock all external calls" review answer
but aren't in Component 7's file list, and the 200-degraded/404 flow is verified
by manual curl. Offered to the engineer as an option; deferred to keep the
must-have scope tight.

**Three silent failures the tests expose (review gold):**
1. **Title format change** — if Kalshi switches to `"Scotland v Brazil"` (no
   `s`), `_extract_opponent` returns None → a per-match market silently renders
   as tournament-winner-style (no opponent, no match_status). No crash, wrong
   product. The D28 "another week" gap.
2. **Unknown status passes through raw** — a new status like `"paused"` falls to
   `status or None` → surfaces `"paused"` to the user, violating the documented
   `{live, scheduled, closed, None}` contract.
3. **Empty dict vs None are indistinguishable** — both yield all-None fields, so
   the endpoint can't tell "Kalshi returned garbage empty body" from "no market
   exists." Same narrative either way. Acceptable for v1; a silent conflation.

**Test count:** 41 total (33 prior + 8 new). All pass in 0.18s, no network calls.

**Review answer:** "I unit-test the deterministic logic — the transforms, the
hallucination guard, and the field extraction — with 41 tests, no network calls.
The load-bearing transforms and guard were TDD'd before implementation; the
field-extraction tests are characterization tests written after, locking down
opponent extraction from the market title and the graceful-degradation contract.
Three silent failures the tests make visible: a title-format change silently
drops the opponent, an unknown Kalshi status surfaces raw to the user, and an
empty dict is indistinguishable from no market. If I had another week I'd add
endpoint tests with TestClient to cover the 200-degraded and 404 paths
deterministically."

---

## D33. Match events spike: football-data.org + API-Football (rejected for v1)

**Question:** Can we enrich the narrative with match events (goals, cards, subs
with minutes + scorer names) instead of odds-only? Kalshi's `milestones` endpoint
covers NBA/NFL/golf/tennis — no soccer (see spike notes, "No soccer milestones").

**Two APIs spiked, June 25 2026:**

### football-data.org (free, TIER_ONE)
- World Cup code `WC`, 2026 season present.
- Match list returns 11 matches for Jun 24-25 (Scotland 0-3 Brazil verified).
- **Free tier does NOT include goalscorers, cards, substitutions, or live
  minute.** Match detail keys: `area, awayTeam, competition, group, homeTeam,
  id, lastUpdated, matchday, odds, referees, score, season, stage, status,
  utcDate, venue`. No `goals`, `bookings`, `substitutions`, or `minute` key.
- What you get: final score, halftime score, status (FINISHED/TIMED),
  homeTeam/awayTeam with `tla` (3-letter codes: SCO, BRA — matches Kalshi's `pm`
  codes), matchday, stage, group.
- Rate limit: 10 req/min (`X-Requests-Available-Minute` header).
- **Verdict:** Score-only ("Brazil beat Scotland 3-0, 2-0 at halftime") is
  better than odds-only, but not the "Vinícius 23' goal" narrative we wanted.

### API-Football (api-sports.io v3, free plan)
- World Cup league ID `1`, season `2026` in the league metadata.
- **Free tier returns 0 fixtures for `league=1&season=2026`** — the free plan
  includes all endpoints but limits accessible seasons; current/upcoming seasons
  are gated. Same query for `season=2022` returns all 64 WC fixtures. Paid plan
  ($19/mo) unlocks all competitions and seasons.
- **Event data shape is perfect** (verified on England 6-2 Iran, 2022 WC,
  fixture 855735, 22 events):
  ```json
  {"min": 35, "extra": null, "type": "Goal", "detail": "Normal Goal",
   "player": "J. Bellingham", "team": "England", "assist": "L. Shaw"}
  ```
  Goals: minute + stoppage time + scorer name + assist + team. Cards: player +
  minute + detail (Yellow/Red). Substitutions: playerOut/playerIn + minute.
  Updated every 15 seconds for live matches.
- Rate limit: 100 req/day (free) / 7,500/day ($19/mo).
- **Verdict:** Exactly the data we need for event-level narrative, but the free
  tier gates the 2026 season. A live 2026 WC demo requires the $19/mo plan.

### Google as a data source (investigated, rejected)
- Google has no first-party sports API. The scoreboard OneBox ("Brazil 3-0,
  Vinícius 23'") is rendered from Stats Perform/Sportradar partnerships and
  never exposed as JSON.
- SerpApi and ScrapingBee scrape the Google OneBox and sell it as structured
  JSON (goal_summary with player + minute, red_cards_summary, in_game_time).
  Free tier: 100 searches/month (SerpApi). Returns the same data as API-Football.
- **Rejected:** Three layers of fragility (Google HTML → SerpApi → us), against
  Google's ToS, and the review answer is "I scrape Google via a third party" —
  weaker than "I call a documented API." API-Football gives the same data,
  first-party, for the same price tier.

**Decision:** Neither API is integrated for v1. The must-have (odds-only
narrative from Kalshi) satisfies the exercise. The spike gives the review answer
for free:

**Review answer:** "I spiked two soccer APIs for match events. football-data.org's
free tier gives scores but not goalscorers or event minutes. API-Football has
exactly the data I'd want — goals with minutes, scorer names, assists, cards —
but the free tier gates the current 2026 season; a live demo needs the $19/mo
plan. I also looked at scraping Google's sports OneBox via SerpApi, but rejected
it — three layers of fragility and a weaker review answer than a documented API.
For v1 I kept the scope to Kalshi odds-only; adding match events is a clean
one-week extension — wire API-Football's `/fixtures/events` alongside the Kalshi
fetch, join by team name (their `tla` codes match Kalshi's `pm` codes), and feed
both into the LLM prompt. The hallucination guard would extend to verify event
minutes against the source data the same way it verifies probabilities today."

**Another week (the integration sketch):**
1. Add `FOOTBALL_API_KEY` to config (env var).
2. New client: `fetch_match_events(team_pm: str) -> list[dict]` — call
   `/fixtures?league=1&season=2026&team={id}` to find the fixture, then
   `/fixtures/events?fixture={id}`. Cache alongside the Kalshi cache (same TTL).
3. Extend `extract_market_fields` or a new transform: build `match_events` field
   `[{"minute": 23, "type": "goal", "team": "brazil", "player": "Vinícius"}]`.
4. Feed `match_events` into the LLM prompt alongside the odds data.
5. Extend `verify_narrative` to check event minutes against source (same
   global-set approach: every minute in the prose must match a source event).
6. The `tla` ↔ `pm` mapping is already in `config.TEAMS` — zero new mapping code.

---

## D34. Watcher scope: configured subset (watch_teams.json), not all 48 teams

**Chose:** The watcher polls only a configured subset of teams loaded from
`watch_teams.json` (a flat JSON list at project root), not all 48 entries in
`TEAMS`. Invalid entries (not in `TEAMS`) are warned on stderr and filtered
out. Missing or unreadable file falls back to a hardcoded default
(`brazil,argentina,usa,germany,france`).

**Why this was a fix, not a new feature:** SPEC.md:297-299 specifies
`Watcher.__init__(self, teams: list[str], poll_interval: int)` — a configured
team list was always the design. The implementation (`for team_key in TEAMS:`
at main.py:629) was a **spec deviation** that drifted from the plan. This
decision brings the code back to spec, not a new idea.

**Why a JSON config file, not an env var:** An env var (`WATCH_TEAMS=brazil,...`)
is flat — just team names. JSON keeps the door open for D6's "threshold
overridable per team" without a format change: `["brazil", ...]` today,
`{"brazil": {"threshold": 0.15}, ...}` tomorrow. The file is resolved
`__file__`-relative in config.py so it works regardless of where uvicorn
launches from. Rejected YAML (needs `pyyaml` dep) and TOML (stdlib in 3.13 but
overkill for a flat list).

**Why warn-and-filter, not silent filter or hard fail:**
- *Silent filter* (drop invalid, no warning) — defensible ("config is the
  user's job") but a typo like `brazl` silently produces a 4-team watch list
  with no signal. Hard to debug.
- *Hard fail* (raise on invalid) — too strict for a config file; one typo
  stops the server from booting.
- *Warn and filter* (chosen) — stderr warning per invalid entry, then drop.
  The server boots with the valid subset; the operator sees what was skipped.
  Mechanism is `print(..., file=sys.stderr)` at module load, not a logger —
  config.py has no logging setup, and a one-time startup warning doesn't
  justify adding one. Review answer: "stderr print at import time, not a
  logger, because config loads once and there's no logging setup to hook
  into."

**The honest tradeoff of A (name it before the reviewer does):** A team NOT
in `WATCH_TEAMS` still works on-demand — `curl /team/japan` returns HTTP 200
with `current_prob` + `narrative` populated (the endpoint looks up against the
full `TEAMS` dict, not `WATCH_TEAMS`). But `delta_1m` is **always null** for
that team, because the watcher never warms its rolling window. The endpoint
does call `_record_sample` on every curl, but sporadic curls can't accumulate
60s of span (any gap >120s prunes the old sample per `_WINDOW_MAX_AGE`). This
is the gap that option B (lazy promotion) would close, and it's the rehearsed
"what would you refactor" answer.

**Why not B (lazy promotion) now:** B adds `seen: set[str]` state, eviction
logic (drop from `seen` if not curled in N min), and restart-amnesia semantics
(`seen` is in-memory; process restart forgets interest). Each is a moving part
to defend in a 60-minute review with no demo value. A is the spec's original
design; B is the evolution to name in review, not to ship.

**The scaling insight (the "how would you scale to 100x" answer):** Fewer
watched teams does NOT speed up delta availability — `compute_delta` needs
`span >= 60s` (main.py:249), which is wall-clock-bound, not team-count-bound.
With 5 teams × 0.3s delay = 1.5s per cycle; with 48 teams = 17s per cycle.
Both reach 60s of span in ~1 minute of uptime. The win of A is **cutting
wasted work** (1/10th the API calls, 1/10th the local overhead), not faster
deltas. The real scaling cliff: cycle length = `O(teams) × 0.3s`. At 200
teams → 60s cycle, right at the pruning-equals-threshold trap (main.py:667-670)
— delta_1m goes structurally null. The fix for 100x is decoupling poll cadence
from team count (concurrent fan-out with a semaphore, or the WS spike).

**Changes made:**
1. `config.py`: `_load_watch_teams(path)` + `WATCH_TEAMS` (loaded from
   `watch_teams.json`, warn-and-filter on invalid, default fallback).
2. `main.py:629`: `for team_key in TEAMS:` → `for team_key in WATCH_TEAMS:`.
3. `main.py:1-5`: docstring updated to reflect configured subset, not all 48.
4. `watch_teams.json`: created with default 5-team list.

**Rejected:**
- *B (lazy promotion on curl)* — adds `seen`-set state, eviction, restart
  amnesia. More to defend, no demo value. Named as the "another week" evolution.
- *Env var (`WATCH_TEAMS=brazil,...`)* — flat, can't grow to per-team
  thresholds (D6) without a format change.
- *Silent filter* — a typo silently produces a shorter watch list with no
  signal; hard to debug.
- *Hard fail on invalid* — one typo stops the server from booting; too strict
  for a config file.
- *All 48 (the deviation)* — wasted work warming windows for 43 teams nobody
  curls; scaling cliff at 200 teams.

**Another week:** B (lazy promotion) — `seen: set[str]` populated on any curl,
watcher loops `seen` instead of a static config. Closes the "unwatched team
has no delta" gap. Needs eviction (drop from `seen` if not curled in N min) to
avoid unbounded growth, and persistence (write `seen` to disk) to survive
restarts. The concurrent-fan-out fix for the 100x scaling cliff is a separate
concern.

**Review answer:** "The spec always called for a configured team list —
`Watcher(teams: list[str])` — but the implementation polled all 48 teams,
which was a deviation I corrected. The watch list lives in a JSON file so it
can grow to per-team thresholds without a format change. Invalid entries warn
on stderr and get filtered; the server boots with the valid subset. The honest
tradeoff: a team not in the watch list still works on-demand, but its delta is
always null because nothing warms its window. With another week I'd add lazy
promotion — a team gets watched once someone curls it — which closes that gap
without requiring the operator to predict interest. The scaling cliff is that
cycle length is O(teams) × 0.3s, so at 200 teams you hit the pruning threshold
and delta goes structurally null. The fix for 100x is concurrent fan-out with
a semaphore, not a faster poll."

---

## D35. Watcher tests: no new tests (characterized by existing coverage)

**Chose:** No new unit tests for the `WATCH_TEAMS` change. The existing
`tests/test_watcher.py` (4 tests: happy path, fetch exception, None market,
None prob, multi-poll window building) already cover `_poll_team` — the
change swapped `for team_key in TEAMS` → `for team_key in WATCH_TEAMS`, which
is the loop driver, not the per-team poll logic. `_poll_team` is unchanged.

**Why no test for `_load_watch_teams`:** It's config-loading I/O (file read,
JSON parse, stderr warning), not deterministic transform logic. Testing it
would mean tempfile fixtures + stderr capture + import-reload ceremony for a
function that runs once at import. The D21 principle — "unit-test the
deterministic logic, mock everything external" — puts config loading in the
"verified by running" bucket, not the "unit-tested" bucket. Verified live:
`uv run python -c "from config import WATCH_TEAMS; print(WATCH_TEAMS)"` prints
the configured list; an invalid-entry test confirmed warn-and-filter works.

**Rejected:** A test that patches `WATCH_TEAMS` to a subset and asserts the
loop iterates only those — but the loop is one line (`for team_key in
WATCH_TEAMS:`) and testing `for x in list:` is tautological. No bug it could
catch that import + run doesn't.

**Review answer:** "The watcher tests cover `_poll_team` — the per-team poll
step — which is unchanged. The swap from `TEAMS` to `WATCH_TEAMS` is the loop
driver, not the poll logic, so the existing tests still characterize it. I
didn't unit-test the config loader because it's file I/O that runs once at
import, not deterministic transform logic — I verified it by running the
import and an invalid-entry case live."

---

## D36. Notification gate + macOS notifications (Components 9 + 10)

**Chose:** A deterministic `should_notify(current_bp, previous_bp, threshold)
-> bool` gate decides WHEN to notify (D5). On gate fire: `generate_narrative`
(D29) decides WHAT to say, with `template_narrative` as D17 fallback, then
`send_notification` fires one `osascript` subprocess. A per-team cooldown
(`_last_notified`, 300s default) prevents oscillation spam.

**Gate design (the #1 review probe):**
- **Inputs are integer basis points** (D27 — no float arithmetic in the
  pipeline). 5% = 500 bp. The threshold stays a float (0.20). Comparison form:
  `abs(current_bp - previous_bp) >= threshold * previous_bp` — int on the LHS,
  one float multiply on the RHS. Using `>=` instead of division avoids div-by-
  zero AND is more numerically robust than `abs(delta)/prev >= thr` (single
  multiply vs division). The single float is a ratio comparison, not drift-
  prone money subtraction, so it doesn't violate D27's spirit.
- **`previous` = the LAST sample** (per-poll delta, ~30s ago), NOT the 60s-
  smoothed `delta_1m`. "Something just changed" not "movement over the last
  minute." Rejected `delta_1m` because it uses `window[0][1]` (oldest), not
  `window[-1][1]` (last), and a move 55s ago would keep re-firing on
  consecutive polls.
- **`previous == 0`** (a real 0% reading, e.g. an eliminated team): any nonzero
  move from zero → True ("they're on the board"); 0→0 → False. Rejected
  absolute-delta-fallback (a magic number to defend) and treat-as-cold-start
  (0%→50% would never notify, which is clearly wrong).
- **`None` inputs** (cold start / data blip) → False. No baseline, no notify.
- **Boundary `>=`** (inclusive): exactly at threshold → True. A `>` impl would
  silently miss the SPEC-named boundary case.

**Watcher wiring (`_poll_team`):**
1. Read `previous_bp` from `_rolling[team][-1][1]` BEFORE recording the new
   sample (off-by-one guard: reading after append makes previous==current →
   delta always 0 → gate never fires).
2. Fetch market, extract fields, record sample (unchanged from Component 8).
3. `should_notify(current_bp, previous_bp)` — no per-team threshold override
   wired yet (D6 "another week"); uses `DEFAULT_THRESHOLD`.
4. Cooldown: `if now - _last_notified[team] < COOLDOWN_SECONDS: return`. One
   notification per team per 5 min. Prevents the oscillation silent failure
   (500↔650 every poll = notification every 30s without it).
5. On fire: `generate_narrative(display)` → `verify_narrative` → if fails,
   `template_narrative` (D17). `send_notification(team, narrative)`.

**Why LLM content in the notification (D5 done right), not template-only:**
D5 says "the LLM decides WHAT to say." The gate fires rarely (a 20% relative
delta in 30s is a goal-sized swing), so the LLM call happens maybe a few times
per match — not every poll. The LLM is NOT called when the gate doesn't fire
(that's the whole point of separating "when" from "what"). Template is the D17
fallback for when the LLM is down. Rejected template-only (contradicts D5, and
the notification is the demo wow-factor — a human-written notification pops
better than "Brazil's current probability is 6.5%, up from 5.0% previously.")

**Why NOT reuse the endpoint's full pipeline in the watcher:** The endpoint
calls the LLM on every curl (that's the must-have design — fetch → transform →
LLM → guard → response). Reusing that pipeline in the watcher would call the
LLM on every POLL, even when the gate doesn't fire: 5 teams × 2 polls/min =
~600 LLM calls/hour, ~95% wasted. The D5 pattern is gate first, LLM only on
fire. The watcher builds its OWN display dict and calls `generate_narrative`
only inside the gate-fired branch.

**macOS notifications (Component 10):** `subprocess.run(["osascript", "-e",
'display notification "..." with title "..."'])`, wrapped in `try/except`
with `capture_output=True` and `timeout=5`. Never raises — a notification
failure (headless server, Do Not Disturb, osascript missing) must not crash
the watcher (D17 spirit: the watcher is non-load-bearing, but it fills the
rolling window which IS load-bearing for delta_1m). Double-quotes in title/
message are escaped (`"` → `\\"`) so a `"` in the narrative doesn't break the
AppleScript string literal.

**Known imprecision (name it before the reviewer does):** `_build_user_prompt`
labels the delta "Over the last minute" (D29), but the watcher's per-poll
delta is ~30s. "Over the last minute" is not wrong (30s is within the last
minute) but it's imprecise. The template fallback doesn't mention a timeframe,
so the D17 path is clean. Fix would be a `delta_label` parameter on
`_build_user_prompt` — one more thing to defend for a PoC notification that
fires rarely. Named as an "another week" hardening, not a v1 gap.

**TDD:** 16 tests in `tests/test_gate.py` (written before implementation, per
AGENTS.md). Covers: exceeds/below/exactly-at-threshold/zero/negative delta,
all three None combos, prev=0 both branches, per-team override (lower/higher),
the D5 rationale (high-prob small absolute = noise, high-prob large relative =
fires), extreme bp (collapse to zero, ceiling no movement). 4 new tests in
`tests/test_watcher.py` for the notify wiring: gate fires → notification sent,
gate below threshold → not sent, cooldown suppresses repeat, LLM fails →
template fallback. `send_notification` is mocked in the wiring tests (testing
the mock not the call would be tautological); verified live by calling
`send_notification` directly and seeing the macOS notification appear.

**Test count:** 83 total (63 prior + 16 gate + 4 watcher wiring). All pass in
0.17s, no network calls.

**Rejected:**
- *Absolute-delta threshold* — D5 already rejected (5%→10% is huge, 80%→85% is
  noise; relative fixes that). Test #13 is the case that proves it.
- *LLM-as-gatekeeper* — untestable, can flood or go silent (D5).
- *Gate on `delta_1m` (60s-smoothed)* — re-fires on old moves across consecutive
  polls; semantically "movement over the last minute" not "just changed."
- *Template-only notification body* — contradicts D5; the notification is the
  demo wow-factor.
- *Reuse endpoint's full pipeline in the watcher* — calls LLM on every poll,
  95% wasted. D5 is gate-first, LLM-only-on-fire.
- *No cooldown* — oscillation spam (500↔650 every 30s = notification every
  30s). Named as a silent failure before shipping.
- *`pync`/`node-notifier`* — a dependency for one subprocess line; harder to
  explain than "subprocess to osascript."
- *Absolute-delta-fallback for prev=0* — a magic number to defend. Any nonzero
  move from 0 is a qualitative shift; no magic number needed.

**Another week:**
- *Per-team threshold override* (D6): `WATCH_TEAMS` as `{"brazil": {"threshold":
  0.15}}` instead of a flat list. The gate signature already accepts a
  threshold; the wiring just reads it from config instead of using the default.
- *`delta_label` parameter on `_build_user_prompt`* so the watcher says "Since
  the last check" instead of "Over the last minute."
- *Slack/webhook transport* behind the same `send_notification(title, message)`
  interface — the signature is the only thing a second transport has to satisfy.
- *Directional cooldown* — different cooldowns for up-moves vs down-moves (a
  team surging might warrant more frequent updates than a team fading).

**Review answer:** "The gate is a pure function — `abs(current - previous) >=
threshold * previous` in integer basis points. It compares against the LAST
sample, not the 60s-smoothed delta, because 'something just changed' is the
semantic. prev=0 is a special case: any nonzero move from zero is a
qualitative shift in a prediction market, so I notify. None inputs return
False — no baseline, no notify. On gate fire, I call the LLM for the narrative
with a template fallback, then fire an osascript notification. The LLM is only
called when the gate fires — not every poll — because D5 separates 'when'
(deterministic, cheap) from 'what' (LLM, expensive). A 5-minute per-team
cooldown prevents oscillation spam. The notification itself is a subprocess
call to osascript, wrapped in try/except so a failure never crashes the
watcher. One known imprecision: the prompt says 'over the last minute' but the
gate's delta is ~30s — I'd add a label parameter with another week. With
another week I'd also wire per-team threshold overrides from the config file,
which the gate signature already supports."

---

## D37. Watcher extraction + standalone demo CLI (DI, synthetic source, cache bypass)

**Chose:** Extracted the watcher (poll loop, gate, notify, shared state) out of `main.py`
into a reusable `watcher/core.py` with dependency injection — `fetcher`,
`extract_fields`, `generate_narrative`, `verify_narrative`, `template_narrative` are
injected into `_poll_team`/`_watcher_loop`. Added `watcher/demo.py` (`SyntheticFetcher`:
flat 5000bp then jumps to 6500bp after N polls) and `watcher/__main__.py` (CLI with
`--demo`/`--jump-after`). `main.py` imports the shared state (`_rolling`,
`_get_cached_or_fetch`, `_record_sample`, `_watcher_loop`) and injects the real Kalshi
fetcher + LLM in `lifespan`. The CLI feeds the synthetic move through the REAL pipeline
(gate → LLM/template → osascript) to fire a notification on cue — the live-review demo.

**Why:** Two goals. (1) A standalone CLI you can run without the FastAPI server — the demo
fires a real macOS notification on cue without needing Kalshi to be live or a match to be
in progress. (2) A clean seam so a second data source or a different narrative backend can
be wired without touching `watcher.core`. DI keeps `watcher.core` import-free of `main` (no
circular import): `main → watcher.core`, `watcher.__main__ → main → watcher.core`.

**The cache-bypass fix (real bug found during the build):** `_get_cached_or_fetch` caches
the first fetch for `CACHE_TTL_SECONDS` (30s). In demo mode the `SyntheticFetcher` jumps
5000→6500bp, but after poll 1 the cache returns the stale 5000bp — the gate never sees the
jump and the demo never fires. Added a `use_cache: bool = True` param to
`_poll_team`/`_watcher_loop`: live mode keeps the cache (rate-limit protection, D19); demo
passes `use_cache=False` so each poll hits the fetcher. `reset_demo_state` also clears
`_cache` so a demo starts cold. Default `True` preserves live behavior exactly — `main.py`'s
`lifespan` and the on-demand endpoint are unchanged.

**Rejected:**
- *Keeping the watcher inline in `main.py`* — no standalone CLI, no demo, can't swap the data
  source. The whole point of the demo is a seam.
- *LLM-as-gatekeeper for the demo* — untestable; the deterministic gate (D5) already decides
  when.
- *A `--no-cache` CLI flag instead of `use_cache` on the loop* — the cache decision belongs
  to the data source, not the CLI. `use_cache` is a generic loop concern that the demo (and
  any future non-stationary source) sets to `False`; the CLI is one caller.
- *Per-team synthetic counter (so every team fires)* — `SyntheticFetcher._count` is shared
  across `WATCH_TEAMS`; with `--jump-after 2` only the first-polled team transitions and
  fires (exactly one notification). Acceptable — the demo's purpose is "fire on cue," not
  "fire for every team."

**Another week:**
- *Per-team synthetic counter* so the demo fires one notification per configured team.
- *Graceful SIGINT* in `__main__` (currently `asyncio.run` propagates `KeyboardInterrupt` on
  Ctrl-C — a traceback in the demo, not a bug).
- *A neutral `state.py`* for `_rolling`/`_cache`/`_last_notified` so `main.py` (the service)
  doesn't depend on `watcher.core` (the background watcher) for shared state. Today the
  watcher is the primary window-filler so the home is defensible, but it's a mild inversion.
- *Demo isolation* — `watcher/__main__.py` imports `main` at top level, so `--demo`
  (synthetic, no Kalshi) still pulls in the FastAPI/httpx/LLM tree. A lazy import would make
  the demo lighter.

**Review answer:** "I extracted the watcher into `watcher/core.py` with dependency injection
so I could run it two ways: live, wired to the real Kalshi fetcher and LLM via
`main.lifespan`; and a standalone CLI with `--demo`, which feeds a `SyntheticFetcher` (flat
5000bp, then a jump to 6500bp on cue) through the REAL gate, LLM/template, and osascript
pipeline to fire a notification on cue. The DI seam means `watcher.core` never imports
`main` — no circular import — and a second prediction-market source is just a different
`fetcher` arg. One real bug surfaced during the build: the 30s fetch cache masked the
synthetic jump, so the demo never fired. I added a `use_cache` flag — live keeps the cache
for rate-limit safety, demo bypasses it. The default is `True`, so the live path is
unchanged. A known demo quirk: the synthetic counter is shared across teams, so
`--jump-after 2` fires exactly one notification for the first-polled team — fine for a demo,
a per-team counter with another week."

