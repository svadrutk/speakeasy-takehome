# Speakeasy FDE — Learning Plan & Comprehension Checklist

> **Source:** `docs/HANDOFF.md`, `docs/DECISIONS.md`, `docs/SPEC.md`, `docs/REVIEW_DRILLS.md`, all source files.
> **Goal:** Be able to defend every line in a 45–60 min live review. Understanding is weighted HIGH; complexity is weighted LOW.

---

## Learning Plan (4 passes, ~4–5 hours total)

### Pass 1: Global Picture & API Surface (60 min)

| What to study | Files | Checklist items |
|---|---|---|
| Executive Summary & What Was Built | `HANDOFF.md` §§1–2 | Q1–Q2 |
| System Architecture (Mermaid diagram + data flows) | `HANDOFF.md` §3, `README.md` | Q3–Q4 |
| API endpoints and response schema | `models.py`, `main.py:1109-1156` | Q5–Q7 |
| Running & Deployment | `HANDOFF.md` §10, `Dockerfile`, `railway.toml` | Q24–Q27 |

**Comprehension drill — narrate out loud:**
1. "Walk me from `curl /team/brazil` to the JSON response — every external call, cache hit, transform, and guard. Keep it to 90 seconds."
2. "What are the three degraded narrative modes? When does each one fire?"
3. "If I hit `/team/uruguay` and Uruguay has no market, what status code and body do I get?"

---

### Pass 2: Module Walks (90 min)

#### Module 2a: Config & Team Mapping (20 min)

| File | Key things to find |
|---|---|
| `config.py:22-71` | The `TEAMS` dict — 48 entries, `tw` vs `pm` codes |
| `config.py:76` | `CODE_TO_NAME` reverse mapping for opponent extraction |
| `config.py:109-138` | `_load_watch_teams` — how WATCH_TEAMS is loaded from JSON |
| `config.py:141-146` | `get_team(name)` — normalization logic |

**Questions:**
- "What happens if a user types 'Brazil' (capital B) or 'South Korea' (with space)?"
- "Why are the codes hardcoded instead of fetched from Kalshi at startup? What tradeoff does that make?" (D22)
- "What breaks if `watch_teams.json` is missing? Malformed?"

#### Module 2b: Kalshi Client & Market Selection (25 min)

| Key functions | Lines |
|---|---|
| `_get_open_markets(series)` | `main.py:83-96` |
| `fetch_per_match_market(pm)` | `main.py:123-152` |
| `fetch_tournament_winner_market(tw)` | `main.py:99-111` |
| `fetch_market_for_team(team)` | `main.py:158-169` |

**The #1 interview probe** is the market selection flow. Be able to say:
> "I iterate `(KXWCADVANCE, KXWCGAME)` in priority order. For each series, GET `/markets?series_ticker={series}&status=open` (cached 30s per series), filter to tickers ending with the team's 3-letter `pm` code, parse each market's `occurrence_datetime` as UTC, pick the one with the smallest `abs(kickoff - now_utc)`. Return immediately from the first series that has any candidate. If neither matches, fall through to `fetch_tournament_winner_market`. `status=open` is a server-side filter so past settled fixtures never appear — the bidirectional-abs bug is structurally impossible."

**Questions:**
- "Why `KXWCADVANCE` before `KXWCGAME`? What's the difference?" (D32)
- "What happens when `occurrence_datetime` is `None` or garbage on a market?" (`main.py:146-148`)
- "How many Kalshi API calls does `fetch_market_for_team('brazil')` make at most? At least? When is the cache hit?"
- "What was the bidirectional-abs bug, and why is it structurally impossible now?" (D39)

#### Module 2c: Data Transforms & Basis Points (15 min)

| Key functions | Lines |
|---|---|
| `price_to_prob(price_dollars)` | `main.py:189-199` |
| `compute_delta(rolling_window, current_prob)` | `main.py:178-185` |
| `extract_market_fields(market, team_name)` | `main.py:275-352` |
| `_extract_opponent(...)` | `main.py:229-272` |
| `_parse_utc_dt(s)` | `main.py:212-225` |

**Questions:**
- "Why integer basis points instead of floats? Where would float drift bite us?" (D27)
- "How does `compute_delta` handle cold start? What's the min window span before it returns a value?"
- "How does `extract_market_fields` determine `match_status`? What's the `KICKOFF_GRACE` for?" (D38)
- "Walk through `_extract_opponent` for a KXWCADVANCE ticker like `KXWCADVANCE-26JUN29NEDMAR-NED` with team_name='Netherlands'."
- "What happens if `last_price_dollars` is `None`? Or if it's `"1.50"` (>1.0)?"

#### Module 2d: LLM Pipeline, Guard & Template (20 min)

| Key functions | Lines |
|---|---|
| `SYSTEM_PROMPT` | `main.py:369` |
| `_build_user_prompt(data)` | `main.py:372-439` |
| `generate_narrative(data)` | `main.py:443-482` |
| `verify_narrative(narrative, source_data)` | `main.py:502-538` |
| `template_narrative(data)` | `main.py:542-570` |

**Questions:**
- "What's the bounded backoff strategy on OpenRouter 429/5xx? What about TransportError?" (D29)
- "Why is the user prompt 'labeled' instead of passing raw JSON? What bug did that fix?"
- "How does `verify_narrative` work? What numbers does it compare? What's the global-set limitation?" (D30)
- "What does `template_narrative` produce when `current_prob` is None? When volume is None?"
- "If OpenRouter hangs for 30 seconds, what happens to FastAPI? How is starvation prevented?"

#### Module 2e: Watcher, Gate & Notification Gate (10 min)

| Key functions | Lines |
|---|---|
| `should_notify(current_bp, previous_bp, threshold)` | `watcher/core.py:38-47` |
| `_poll_team(...)` | `watcher/core.py:89-177` |
| `_watcher_loop(...)` | `watcher/core.py:180-202` |
| `_record_sample(team_key, prob_bp, now)` | `watcher/core.py:65-70` |
| `send_notification(title, message)` | `watcher/core.py:54-61` |

**Questions:**
- "What delta does the gate use — the same as `delta_1m` or something different? Why?" (D36 — THE #1 review probe)
- "What happens when `previous_bp == 0` in `should_notify`? Why that special case?"
- "What is the cooldown mechanism and why does it exist? What happens without it?"
- "How does `_poll_team` prevent a single notification failure from crashing the whole watcher?"
- "With multiple Railway replicas, what shared state breaks? How would you fix it?"

---

### Pass 3: Decisions & Tradeoffs (45 min)

**Study:** `docs/DECISIONS.md` (D1–D39) and `HANDOFF.md` §6 "Tradeoffs to Defend in Interview"

**Practice each with the goal/choice/cost structure:**

| Tradeoff | Goal | Choice | What you gave up |
|---|---|---|---|
| In-memory window vs Redis | Fast iteration, zero ops | Dict with TTL | No horizontal scale; crash loses 2m history |
| REST vs WebSocket | Debuggability, no auth | REST polling | Lower freshness vs push latency |
| Deterministic gate vs LLM-judge | Testable SLA, no surprises | Math gate, LLM prose | Can't catch subtle pattern changes |
| Always 200 vs error codes | Client never crashes | Degraded narrative | Hides failures from monitoring |
| Integer bp vs floats | Drift-free arithmetic | Int ×10000 | Must convert at output boundary |
| Single file vs modules | Scrollable in review | One main.py | Harder to navigate over 700 lines |

---

### Pass 4: Quiz & Drills (30 min)

Run through `docs/REVIEW_DRILLS.md`. Key gaps from the existing log:
1. **D36 gate delta vs `delta_1m`** — gate uses per-poll delta (current − last sample), NOT 60s-smoothed window.
2. **Cache layering** — two caches: per-team market (30s TTL) + per-series open-markets (30s TTL) + rolling window (120s max age).
3. **KXWCADVANCE priority** — queried as a full series first, not derived from KXWCGAME fixture.
4. **bidirectional-abs structural impossibility** — `status=open` server-side filter removes past settled fixtures from the candidate pool.

---

## Pre-Flight Cheat Sheet (for interview morning)

### Architecture in 4 bullets
1. **Two layers:** developer-facing typed JSON (FastAPI + Pydantic) + fan-facing `narrative` prose (LLM). The FDE job is bridging raw API to human outcome.
2. **Data flow:** config → Kalshi REST (no auth, 30s cache) → integer bp transforms → rolling window (120s) → OpenRouter LLM (bounded backoff) → hallucination guard → template fallback → always-200 JSON.
3. **Watcher:** poll `WATCH_TEAMS` → per-poll gate delta (not 60s-smoothed) → LLM on fire → osascript notification (5m cooldown). Never raises.
4. **All state is in-memory single-process.** Redis is the "another week" upgrade.

### 5 tradeoffs you can defend
| Tradeoff | Your line |
|---|---|
| In-memory vs Redis | "Crash loses 2m of history — acceptable for a PoC. Swapping to Redis is a storage-layer flip, not a data-model rewrite." |
| REST vs WebSocket | "100 req/sec is generous for 5 teams. WS would be more elegant but needs RSA auth even for public channels (D16)." |
| Deterministic gate | "`should_notify(6500, 5000) is True` never flakes. An LLM judge can't give you that guarantee." |
| Always 200 | "A 200 with null fields + explanation is a response a downstream client can ship. A 500 forces every consumer to build a fallback." |
| Integer bp | "5% is 500 bp. `3600 − 3000 = 600` always. Float `0.36 − 0.30` gives `0.06000000000000005`. The Decimal seam keeps the string→int parse exact." |

### 3 favorite failure modes
1. **Multiple Railway replicas** — `_rolling`, `_cache`, `_last_notified` are per-process. Replica A notifies; replica B doesn't know and notifies again 30s later. Fix: Redis for shared state.
2. **OpenRouter hangs** — httpx timeout is 10s, bounded backoff adds 3s max. If it hangs past that, `asyncio.wait_for` at a higher layer would cut it. Currently, the 10s client timeout + template fallback handles it — no structured data loss, only prose degrades to template.
3. **`KICKOFF_GRACE` mislabeling** — a match kicking off at 10pm local time (UTC+3 = 01:00Z next day) hits `occurrence_datetime` 30m grace correctly because `occurrence_datetime` is UTC. The OLD ticker-string date was local-day-granular and would mislabel it. The fix (D38) is live, but a match with no `occurrence_datetime` falls back to `status` which is always `"active"` → `"scheduled"` even during play.

### Key code references to have at the front of your mind
- Gate delta vs `delta_1m`: `watcher/core.py:107` vs `main.py:179-185`
- `status=open` structural fix: `main.py:85-88`
- Three degraded modes: `main.py:1128-1142`
- Opponent from ticker not title: `main.py:246-254`
- `should_notify(..., previous_bp=0)`: `watcher/core.py:45-46`

---

## Comprehension Checklist (phrased as "I can explain...")

### Global picture & data flow
- [ ] Q1: The overall project — FastAPI service that turns Kalshi World Cup prediction-market odds into JSON + narrative, with optional macOS notification watcher.
- [ ] Q2: The two-audience split — dev-facing typed interface (JSON schema) + fan-facing content (LLM `narrative` field). The FDE thesis.
- [ ] Q3: The Mermaid diagram from README — external APIs → LLM pipeline → service + watcher → rolling window.
- [ ] Q4: The on-demand path (`GET /team/{team}`) step by step, and the watcher path step by step, including caches, rolling window, gate, and cooldown.

### API surface & response schema
- [ ] Q5: Every endpoint — `GET /` (health + teams list) and `GET /team/{name}` (sentiment JSON), including their status codes.
- [ ] Q6: The `TeamSentiment` Pydantic model — each field, when it's None, why `narrative` is always present (D17).
- [ ] Q7: How `main.py:1144-1156` assembles a `TeamSentiment` from transform output, including the bp→float conversion at `main.py:1115-1116`.

### Kalshi integration, caching, and match status
- [ ] Q8: How `config.py` maps team names to `tw` and `pm` codes, and why that mapping was verified against Kalshi (D22).
- [ ] Q9: The per-match market search — `_get_open_markets()`, `fetch_per_match_market()`, including the series priority (KXWCADVANCE → KXWCGAME) and the `status=open` server-side filter (D39).
- [ ] Q10: Why KXWCADVANCE knockout markets are preferred over KXWCGAME in knockout rounds (D32).
- [ ] Q11: How `_extract_opponent` parses opponent from the ticker (3-letter code split) with fallback to the title string.
- [ ] Q12: How `extract_market_fields` computes `match_status` from `occurrence_datetime` with the 30m `KICKOFF_GRACE` (D38).
- [ ] Q13: The cache layering — per-team market cache (30s, `_cache`) + per-series open-markets cache (30s, `_open_markets_cache`). Why two caches exist and what each prevents.

### LLM pipeline, guard, and template
- [ ] Q14: The LLM client — `SYSTEM_PROMPT`, `_build_user_prompt`, `generate_narrative` (OpenRouter URL, headers, bounded backoff on 429/5xx/TransportError, 3s max added latency).
- [ ] Q15: Why the user prompt is labeled (the volume-as-viewers fix), why None fields are omitted, why game timing uses plain English ("the match is today" not "ongoing").
- [ ] Q16: The hallucination guard — how `verify_narrative` extracts numbers from prose, builds the allowed set from source data, uses `math.isclose` for comparison, and falls back on mismatch (D18).
- [ ] Q17: The two known limitations of the guard: (1) catches invented statistics but not invented prose events, (2) global-set comparison can't catch field-conflation (D30).
- [ ] Q18: What `template_narrative` produces for each combination of present/missing fields — e.g., None current_prob, or zero delta, or None volume.

### Watcher, rolling window, and notification gate
- [ ] Q19: The rolling window structure in `watcher/core.py:30-34` — `_WINDOW_MAX_AGE=120s`, how `_record_sample` maintains it, and why the 2x buffer avoids the pruning-equals-threshold trap.
- [ ] Q20: The notification gate (`should_notify`) — basis-point inputs, relative threshold logic, special handling for `previous_bp == 0` (D5).
- [ ] Q21: **THE #1 REVIEW PROBE** — the gate uses per-poll delta (current − last sample, `watcher/core.py:107`), NOT the 60s-smoothed window `delta_1m` from the endpoint. Why: the windowed value would re-fire on consecutive polls for one change; the semantic is "something just changed."
- [ ] Q22: `_poll_team` and `_watcher_loop` — how they fetch markets (cached vs uncached), record samples, apply the gate, enforce the 5-minute cooldown (`_last_notified`), and send notifications via `osascript` (D36).
- [ ] Q23: The dependency injection seam — how `main.py`'s `lifespan` wires real Kalshi, and `watcher/__main__.py` wires `SyntheticFetcher` for demo mode (D37).

### Config, watch_teams, and env vars
- [ ] Q24: `WATCH_TEAMS` — how it's loaded from `watch_teams.json`, validated against `TEAMS`, and why the watcher doesn't poll all 48 (D34).
- [ ] Q25: The key env vars (`KALSHI_BASE_URL`, `CACHE_TTL_SECONDS`, `OPENROUTER_API_KEY`, `LLM_MODEL`, `COOLDOWN_SECONDS`, `WATCH_TEAMS_FILE`) and what changing each does.
- [ ] Q26: How `get_team(name)` normalizes input (case, spaces, hyphens) and returns None for unknown teams — leading to a 404.

### Tests and guarantees
- [ ] Q27: The structure of `tests/` — test_gate.py, test_guard.py, test_transforms.py, test_watcher.py, test_demo*.py (96 total tests).
- [ ] Q28: Why externals (Kalshi, OpenRouter) are mocked, and which logic is exercised deterministically (D21).
- [ ] Q29: At least 3 edge cases covered by tests — delta calculation span < 60s, invalid price strings, gate behavior with previous_bp=0, empty narrative in guard.

### Decisions and tradeoffs (D1–D39)
- [ ] Q30: At least three decisions with full goal/choice/cost structure — e.g., D19 in-memory cache vs Redis, D10 REST vs WebSocket, D5 deterministic gate vs LLM-judge, D17 always 200 vs error codes.
- [ ] Q31: The single-process limitation and what Redis would fix (inconsistent `_rolling`/`_cache`/`_last_notified` across replicas, duplicate notifications).
- [ ] Q32: What you'd refactor with another week (SSE streaming, Redis for shared state, boundary model for Kalshi input, conditional cache TTL based on match status).
