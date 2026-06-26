# SPEC.md — Build Plan

This is the execution plan. DECISIONS.md is the *why*; this is the *what* and *how*.
Each component follows the AGENTS.md build loop:

```
1. NAME the component and the one job it does.
2. EXPLAIN the approach + the rejected alternative.
3. WRITE the smallest runnable version (engineer types the load-bearing parts).
4. RUN it. Verify by running, not by reading.
5. CHECK comprehension: engineer explains it back OR predicts a failure mode.
6. LOG the decision + tradeoff in DECISIONS.md.
7. Only then move to the next component.
```

Components are ordered by dependency. Must-have ships first (satisfies the exercise).
Nice-to-have layers on top (demo wow factor). Do not skip ahead.

---

## Data flow

```
User: curl /team/brazil
  │
  ▼
FastAPI endpoint
  │
  ├──► Config: look up "brazil" → {tw: "BR", pm: "BRA"}  (unknown team → 404)
  │
  ├──► Cache check: 30s TTL → return cached market (if fresh), else proceed
  │
  ├──► Kalshi client: fetch per-match market (KXWCGAME, search for pm in ticker)
  │    │   (if no in-play match → fall back to KXMENWORLDCUP-26-{tw})
  │    ▼
  │    Raw market object (decimal strings: last_price_dollars, volume_fp, etc.)
  │
  ├──► Data transforms: dollars string → integer basis points (D27), delta from
  │   │ rolling window, extract fields. Float appears at one line (bp / 10000.0).
  │   ▼
  │   Structured data dict (current_prob, delta_1m, volume, opponent, match_status)
  │
  ├──► [background watcher only] Notification gate: relative delta > threshold?
  │    │   yes → continue to LLM + notify
  │    │   no  → stop
  │
  ├──► LLM client: send structured data + system prompt → narrative prose
  │    ▼
  │    Narrative string
  │
  ├──► Hallucination guard: extract numbers from narrative, compare to source data
  │    │   mismatch → template fallback narrative
  │    │   match    → keep LLM narrative
  │    ▼
  │    Verified narrative string
  │
  ├──► Assemble response JSON (structured fields + narrative)
  │
  ▼
Response: {"team": "brazil", "current_prob": 0.05, "narrative": "...", ...}
```

---

## Must-have: on-demand JSON endpoint

### Component 1: Config

**Job:** Load team mappings, thresholds, and env vars. One source of truth for
configuration.

**Interface:**
```python
# config.py
TEAMS: dict[str, dict[str, str]]  # "brazil" -> {"tw": "BR", "pm": "BRA"}
DEFAULT_THRESHOLD: float           # 0.20 (20% relative delta)
KALSHI_BASE_URL: str               # "https://api.elections.kalshi.com"
CACHE_TTL_SECONDS: int             # 30
OPENROUTER_API_KEY: str            # from env
LLM_MODEL: str                     # from env, e.g. "meta-llama/llama-3.1-8b-instruct"
```

**Engineer types:** The `TEAMS` dict (load-bearing — you verify each code against Kalshi).
**I scaffold:** env var loading, defaults, config loading logic.

**Review questions answered:** "How are team preferences configured?" "How do you handle
different environments?"
**Comprehension check:** "If a user types 'Brazil' with a capital B, does it work? Why or
why not? Where would you fix that?"

**Build order within component:** TEAMS dict first (verify against Kalshi live), then env
vars, then defaults.

---

### Component 2: Kalshi client

**Job:** Fetch market data from Kalshi's public REST API. Given a team name, return the
raw market object(s).

**Interface:**
```python
# main.py (or kalshi.py if extracted)
def fetch_market_for_team(team: str) -> tuple[dict, date | None] | None:
    """Find the team's in-play match market, or fall back to tournament-winner.
    Returns (market, match_date), or None if no market found.
    match_date is None for tournament-winner fallback (labels match_status)."""

def fetch_per_match_market(pm: str) -> tuple[dict, date] | None:
    """Search KXWCGAME events for one containing pm in the ticker.
    Return (team's market, match_date) (not the tie market), or None."""

def fetch_tournament_winner_market(tw: str) -> dict | None:
    """Fetch KXMENWORLDCUP-26-{tw}. Return the market object, or None."""
```

**Engineer types:** The per-match search logic (parsing KXWCGAME event tickers to find the
right market — load-bearing because it's the trickiest API interaction and most likely to
be probed). The fallback logic (in-play → tournament-winner).
**I scaffold:** HTTP calls, error handling, JSON parsing.

**Review questions answered:** "Walk me through the API interactions." "What happens if
Kalshi returns a 500?" "How does authentication work?"
**Comprehension check:** "If I call `fetch_market_for_team('brazil')` and Brazil has a match
today AND a tournament-winner market, which one comes back and why? What line of code makes
that decision?"

**Rejected alternative (state before writing):** WebSocket subscription. Rejected because
WS requires RSA auth (D16) and REST polling is sufficient at our scale.

---

### Component 3: Data transforms

**Job:** Convert raw Kalshi market objects into our structured output fields. Pure
functions, no I/O, no side effects — the most testable part of the system.

**Interface:**
```python
# main.py
def price_to_prob(price_dollars: str) -> int | None:
    """'0.0500' -> 500 (basis points). None if input is None/empty/garbage/garbage.
    Decimal at the string seam, then int. No float arithmetic internally (D27)."""

def compute_delta(rolling_window: list[tuple[float, int]], current_prob: int | None) -> int | None:
    """Given [(timestamp, prob_bp), ...] and current prob_bp, return delta in basis points
    from oldest sample if window spans >=60s, else None."""

def extract_market_fields(market: dict | None, match_date: date | None = None) -> dict:
    """Pull team, opponent, match_status, current_prob, volume, liquidity,
    last_updated from the raw market object. match_date labels scheduled/ongoing/
    closed (D26). None market -> all-None dict (D17)."""
```

**Engineer types:** `compute_delta` (load-bearing — the rolling window logic is the core
data transform and most likely to have edge cases). `price_to_prob` (you type it — it's
short but it's the "never use floats for arithmetic" decision in code).
**I scaffold:** `extract_market_fields` (straightforward dict access with `.get()`).

**Review questions answered:** "How do you handle Kalshi's data format?" "What happens when
Kalshi returns garbage data?"
**Comprehension check:** "If `last_price_dollars` is `None`, what does `price_to_prob`
return? Where does that `None` propagate to in the final response? What does the user see?"

**Test first (TDD):** `price_to_prob` and `compute_delta` get unit tests before
implementation. Given-When-Then for: happy path, None input, empty string, zero, negative,
boundary (exactly 1h window), window < 1h, garbage string.

---

### Component 4: LLM client

**Job:** Take structured data, send to OpenRouter with constrained prompt, return
narrative prose. Isolated behind one function so the provider is swappable.

**Interface:**
```python
# main.py
def generate_narrative(data: dict) -> str:
    """Send structured data to OpenRouter, return narrative string.
    On failure, raise (caller catches and uses template fallback)."""

SYSTEM_PROMPT = "You are a sports commentator writing for a casual fan. Use ONLY the facts provided. Do not invent events, scores, or statistics. If the facts are thin, keep it short."
```

**Engineer types:** The `generate_narrative` function body (load-bearing — the prompt
construction and API call is the LLM integration, most likely to be probed). The system
prompt (you write it — it's the hallucination constraint in code).
**I scaffold:** OpenRouter client setup, env var wiring.

**Review questions answered:** "How does the LLM integration work?" "What's your prompt
strategy?" "How would you swap LLM providers?"
**Comprehension check:** "If OpenRouter returns a 429 (rate limit), what happens? Where in
the code is that caught? What does the user end up seeing?"

**Rejected alternative (state before writing):** Calling OpenAI directly. Rejected because
OpenRouter makes the LLM literally swappable via one env var (D14).

---

### Component 5: Hallucination guard

**Job:** Verify the LLM's narrative against the source data. If numbers don't match or
events are invented, fall back to a deterministic template.

**Interface:**
```python
# main.py
def verify_narrative(narrative: str, source_data: dict) -> bool:
    """Extract numbers from narrative via regex, compare to source_data values.
    Return True if all numbers match, False if any mismatch."""

def template_narrative(data: dict) -> str:
    """Generate a deterministic narrative from structured data.
    'Brazil's current probability is 5.0%, down from 5.2% previously. Volume: 12.5M.'
    Handles None fields gracefully."""
```

**Engineer types:** `verify_narrative` (load-bearing — the regex extraction and comparison
is the hallucination defense, most likely to be probed). `template_narrative` (you type it
— it's the fallback that guarantees the user always gets something).
**I scaffold:** Nothing — both functions are load-bearing.

**Review questions answered:** "How do you prevent hallucination?" "What happens if the LLM
makes something up?"
**Comprehension check:** "If the LLM says 'Brazil's probability is 34%' but the data says
5%, what happens? Which function catches it? What does the user sees instead?"

**Test first (TDD):** Both functions get unit tests before implementation. Given-When-Then
for: correct numbers pass, wrong numbers fail, no numbers pass, extra events fail, None
fields in template, empty narrative.

---

### Component 6: FastAPI endpoint

**Job:** Wire everything together. `GET /team/{team_name}` returns the full JSON response.

**Interface:**
```python
# main.py
@app.get("/team/{team_name}")
async def get_team_sentiment(team_name: str) -> TeamSentiment:
    """1. Look up team in config.
    2. Fetch market data from Kalshi (with 30s cache).
    3. Transform to structured fields.
    4. Generate narrative via LLM.
    5. Verify narrative (fall back to template on mismatch).
    6. Return assembled JSON.
    Unknown team -> HTTP 404 (client error — resource doesn't exist).
    Known team + any failure -> HTTP 200 with null fields + explanatory narrative (D17)."""
```

**Engineer types:** The error handling flow (load-bearing — the 200-with-degraded-narrative
pattern is the most likely architecture probe). The cache logic.
**I scaffold:** FastAPI app setup, route definition, response model.

**Review questions answered:** "Walk me through the data flow." "What are the failure
modes?" "How does error handling work?"
**Comprehension check:** "If I hit `/team/uruguay` and Uruguay has no market, what HTTP
status do I get? What's in the response body? Walk me through the code path."

**Verify by running:** `curl localhost:8000/team/brazil` must return valid JSON with a
narrative field. This is the must-have demo.

---

### Component 7: Tests (TDD where required)

**Job:** Unit-test the deterministic logic. Mock all external calls.

**Files:**
```python
# test_gate.py — notification gate (written in nice-to-have phase)
# test_transforms.py — price_to_prob, compute_delta, extract_market_fields
# test_guard.py — verify_narrative, template_narrative
```

**Engineer types:** The Given-When-Then statements (you write them before implementation,
per AGENTS.md TDD discipline). The test for `compute_delta` edge cases (you write it —
it's the most likely "show me a test you wrote" probe).
**I scaffold:** Mock fixtures, pytest setup, test runner config.

**Review questions answered:** "What's your testing strategy?" "Show me a test you wrote."
**Comprehension check:** "If I delete the boundary test for `compute_delta` (exactly 1h
window), what bug could ship undetected?"

**Test order (TDD):** transforms and guard tests written BEFORE their implementations
(components 3 and 5). Gate tests written with the gate (nice-to-have).

---

## Nice-to-have: background watcher + notifications

### Component 8: Background watcher

**Job:** Poll Kalshi for followed teams on an interval. Maintain rolling windows. Trigger
notifications when the gate fires.

**Interface:**
```python
 # main.py
# Module-level functions + asyncio task (not a class — simpler to explain
# in review, one scrollable file, no __init__ ceremony).
# WATCH_TEAMS loaded from watch_teams.json (not all 48 teams — D34).
_watcher_loop(): """Poll loop over WATCH_TEAMS. Never raises (D17)."""
_poll_team(key): """Fetch one team, record sample, maybe notify."""
lifespan(): """Create/cancel _watcher_task on startup/shutdown."""
```

**Engineer types:** The poll loop (load-bearing — the watcher is the real-time story and
most likely to be probed for "what happens if the API goes down mid-loop?"). The rolling
window update logic.
**I scaffold:** asyncio/scheduling, graceful shutdown.

**Review questions answered:** "How would you scale this to 100x more users/markets?"
"What happens if Kalshi goes down mid-poll?" "How would you add WebSocket support?"
**Comprehension check:** "If the watcher polls every 30s and Kalshi goes down for 5 minutes,
how many failed requests happen? What does the user see during that time? What happens when
Kalshi comes back?"

---

### Component 9: Notification gate

**Job:** Given a team's current probability and its previous probability, decide whether
the change is big enough to notify. Pure function, deterministic, unit-testable.

**Interface:**
```python
# main.py
def should_notify(
    current_bp: int | None,
    previous_bp: int | None,
    threshold: float = DEFAULT_THRESHOLD,
) -> bool:
    """Return True if relative delta >= threshold. Inputs are integer basis points (D27).
    None inputs (cold start) -> False.
    previous_bp == 0: return current_bp > 0 (qualitative shift from zero)."""
```

**Engineer types:** The entire function (load-bearing — this is THE deterministic gate, the
most important testable decision in the system, and the #1 thing reviewers will probe).
**I scaffold:** Nothing. You type all of it.

**Review questions answered:** "How does the notification trigger work?" "Why deterministic
gate instead of LLM?" "What are the failure modes?"
**Comprehension check:** "If a team's probability goes from 5% to 6%, does it notify? What
about 80% to 85%? What's the relative delta in each case? Where is that calculated?"

**Test first (TDD):** Given-When-Then for: delta exceeds threshold, below threshold,
exactly at threshold, zero delta, negative delta, None inputs (cold start), per-team
override threshold, division by zero (previous_prob = 0).

---

### Component 10: macOS notifications

**Job:** Fire a macOS notification when the gate says yes. Use `osascript` (one line, no
dependency).

**Interface:**
```python
# main.py
def send_notification(title: str, message: str) -> None:
    """Fire osascript display notification. Wrap in try/except — never crash the
    watcher if the notification fails."""
```

**Engineer types:** Nothing load-bearing here — it's one `subprocess` call. But you should
type it so you can explain it: "It's a subprocess call to osascript, wrapped in try/except
so a notification failure never crashes the watcher."
**I scaffold:** The function.

**Review questions answered:** "How would you add Slack notifications instead of macOS?"
**Comprehension check:** "If osascript fails (e.g., on a headless server), what happens to
the watcher? Does it keep running?"

---

## Build order summary

```
MUST-HAVE (ship first, satisfies exercise):
  1. Config          → TEAMS dict, env vars, defaults
  2. Kalshi client   → fetch market data (per-match + tournament-winner fallback)
  3. Data transforms → cents→prob, delta, field extraction (TDD)
  4. LLM client      → OpenRouter call, constrained prompt
  5. Hallucination   → verify narrative, template fallback (TDD)
  6. FastAPI endpoint→ wire it all together, cache, error handling
  7. Tests           → gate, transforms, guard, template (TDD where required)

NICE-TO-HAVE (demo wow factor, layers on top):
  8. Watcher         → poll loop, rolling windows, graceful shutdown
  9. Notification gate → relative delta threshold (TDD)
  10. macOS notifs   → osascript subprocess call
```

After each component: RUN it, CHECK comprehension, LOG in DECISIONS.md, then proceed.
Do not batch. The point is durable understanding, not throughput.
