# Kalshi at the World Cup

## Intro

I have been watching the World Cup fervently in the last couple weeks and try to stay informed with match fixtures and results. However, I don't have the time to watch every match, though I'd like to stay updated on which way the game is leaning during its duration. Incidentally, I've also been following the emerging trend of prediction markets and how they capture and measure the sentiment around certain events. I combined these two concepts into this project -- an API that turns Kalshi World Cup prediction-market odds into developer-consumable JSON with a human-readable narrative field that summarizes the current sentiment. For my own use, I've attached a background watcher that polls this API and fires a MacOS notification on meaningful moves. 

## System Diagram 

```mermaid
flowchart LR
    %% Shared State
    Window[("Rolling Window\n(120s)")]

    %% External Dependencies
    subgraph External ["External APIs"]
        direction TB
        Kalshi[("Kalshi REST")]
        OpenRouter[("OpenRouter REST\n(Llama 3.1)")]
    end

    %% Shared LLM Pipeline
    subgraph LLM ["LLM Pipeline (shared: generate_narrative → verify → template fallback)"]
        direction LR
        XForm["Build Display\n(bp, delta, volume)"]
        Gen["LLM Inference\n(generate_narrative)"]
        Vfy{"Verify +\nTemplate Fallback"}

        XForm --> Gen
        Gen --> Vfy
    end

    %% Synchronous API Path
    subgraph Service ["FastAPI Service"]
        direction LR
        Req(["GET /team/{team}"])
        TeamCache{"Per-Team Market\n(_cache, 30s)"}
        SeriesCache{"Per-Series Open\n(_open_markets_cache, 30s)"}
        Out(["JSON 200 Response"])

        Req --> TeamCache
        SeriesCache -.-> TeamCache
        TeamCache --> XForm
        Vfy --> Out
    end

    %% Asynchronous Background Path
    subgraph Watcher ["Background Watcher (Async)"]
        direction LR
        Poll["Poll watch_teams.json"]
        Gate{"Delta >= Threshold"}
        Notif(["macOS Notification\n(5m cooldown)"])

        Poll --> Gate
        Gate -- fires --> XForm
        Vfy --> Notif
    end

    %% External feeds
    Kalshi --> SeriesCache
    OpenRouter -.-> Gen

    %% Shared State
    Watcher -- "Record sample" --> Window
    Window -. "Read delta" .-> XForm
```

### Key Features
- Kalshi public REST API call needs no auth 
- Watcher shares rolling 2m window with API endpoint 
- LLM is a final transform, never a decision-maker 
- Opponent extracted from market title (" vs " split), not from `yes_sub_title` which changes format 

## Tradeoffs
- Deterministic gate vs LLM-judge: Notifications trigger via strict math (relative delta threshold). The LLM only writes the prose. Ensures testability and prevents hallucinated spam or silent failures.
- Graceful degradation (Always `200 OK`): Kalshi or LLM outages return null data fields with a fallback template narrative. Clients don't crash; developers/fans always get a readable status.
- REST polling vs WebSockets: Used Kalshi's public `REST API` instead of WebSockets. Public `REST` requires zero auth. At our scale (watching 3-5 teams), polling is highly viable and debuggable.
- Integer basis points: All internal probability math uses integers (`5% = 500bp`). Floats only appear at the final `JSON` boundary. Prevents floating-point precision drift. 
- Per-series open-markets caching: Two 30s caches: per-team market results (dedup concurrent curls) and per-series open-markets (`_open_markets_cache`). The series cache collapses the watcher's 48-teams fan-out into 1–2 `/markets?series_ticker={series}&status=open` calls per cycle. Kalshi's server-side `status=open` filter makes the bidirectional-abs() past-match bug structurally impossible — past settled fixtures never appear in the response.
- Knockout-stage advance markets: `fetch_per_match_market` iterates `(KXWCADVANCE, KXWCGAME)` in priority order — ADVANCE ("to advance" including extra time/penalties) is queried as a full series first, not derived from a KXWCGAME fixture. In knockout rounds the regulation-time markets show ~1-2% per side and ~96% tie, while the advance markets show the real ~50% probability fans expect.

## To Improve
- Server-Sent Events (`SSE`): Upgrade the polling endpoint to stream real-time JSON updates to clients.
- Match event enrichment: Wire a secondary sports `API` (like API-Football) to feed live goals and cards into the LLM prompt for richer narratives.


## AI Tools
AI tools featured heavily in the creation of this project. I used my assistant for several purposes: 
- Teach me the components it was making and why it was making it; required it to force me to write key parts of the code for maximum understanding
- Research spikes on specific APIs and practices; Kalshi, OpenRouter, api-football, etc. 
- Generate tests for me in English in Given-When-Then format, then implement TDD (failing tests first, then iterative code until all tests pass)
For more information, please refer to my `AGENTS.md`. 

## Running It
The API is hosted at `https://speakeasy-wc-production.up.railway.app/`. 

To see available teams, hit the root endpoint:
`curl https://speakeasy-wc-production.up.railway.app/`

Then query a specific team (best results with an ongoing/upcoming match):
`curl https://speakeasy-wc-production.up.railway.app/team/brazil`

### Local Setup
Ensure you have [uv](https://github.com/astral-sh/uv) installed, then:
```bash
uv sync
# Requires OPENROUTER_API_KEY in .env for full LLM functionality
uv run uvicorn main:app --reload
```

### Background Watcher
To run the macOS notification watcher locally (ensure watch_teams.json is configured with your target teams):

```
# The --demo flag simulates an odds jump after 2 polling cycles to test notifications
uv run python -m watcher --demo --jump-after 2
```
