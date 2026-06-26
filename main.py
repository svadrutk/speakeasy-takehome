"""Kalshi client + FastAPI service — World Cup prediction-market sentiment.

No authentication needed for public market data (D8). REST polling, not
WebSocket — WS requires RSA auth even for public channels (D16), and the
100 req/sec REST limit is generous for our scale.

The background watcher polls a configured subset of teams (WATCH_TEAMS,
loaded from watch_teams.json), not all 48 in TEAMS — matches SPEC.md's
Watcher(teams) design. A team not in WATCH_TEAMS still works on-demand,
but its delta_1m is always null (no watcher warming its window).
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI, HTTPException

from config import (
    KALSHI_BASE_URL,
    LLM_MODEL,
    OPENROUTER_API_KEY,
    TEAMS,
    get_team,
)
from models import TeamSentiment
from watcher.core import (
    _get_cached_or_fetch,
    _record_sample,
    _rolling,
    _watcher_loop,
)

# --- HTTP plumbing ----------------------------------------------------------


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Lazy singleton — one client reused for connection pooling."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0, follow_redirects=True)
    return _client


async def _fetch_json(path: str, params: dict[str, str] | None = None) -> dict:
    """GET {KALSHI_BASE_URL}/{path}?{params}. Raise httpx.HTTPError on failure."""
    r = await _get_client().get(f"{KALSHI_BASE_URL}/{path}", params=params)
    r.raise_for_status()
    return r.json()


# --- Constants for per-match search -----------------------------------------

KXWCGAME_SERIES = "KXWCGAME"

MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


# --- Tournament-winner market (scaffolded) ----------------------------------


async def fetch_tournament_winner_market(tw: str) -> dict | None:
    """Fetch KXMENWORLDCUP-26-{tw}. Return the market object, or None if 404.

    Re-raise on other HTTP errors (timeout, 500) — caller handles.
    This is the fallback when no in-play match exists (D15).
    """
    try:
        body = await _fetch_json(f"markets/KXMENWORLDCUP-26-{tw}")
        return body.get("market")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


# --- Per-match market (ENGINEER TYPES THIS) ---------------------------------

# Events list cache: all 48 teams share the same KXWCGAME events response.
# Without this, the watcher's 48 per-cycle calls to fetch_per_match_market each
# hit /events independently -> 48 identical calls per cycle -> 429 rate limit.
# 30s TTL matches CACHE_TTL_SECONDS; the events list changes slowly (matches
# are added days ahead, not second-to-second).
_events_cache: tuple[float, list] | None = None
_EVENTS_CACHE_TTL = 30.0


async def _get_events() -> list:
    """Return the KXWCGAME events list, cached for _EVENTS_CACHE_TTL seconds.

    A failed fetch does NOT update the cache (next call retries immediately).
    Re-raises on HTTP error — caller (fetch_per_match_market) handles.
    """
    global _events_cache
    now = time.monotonic()
    if _events_cache is not None and (now - _events_cache[0]) < _EVENTS_CACHE_TTL:
        return _events_cache[1]
    resp = await _fetch_json("events", {"series_ticker": KXWCGAME_SERIES})
    events = resp.get("events", [])
    _events_cache = (now, events)
    return events


# Markets-per-event cache: multiple teams share the same match event (e.g.
# croatia and ghana both query KXWCGAME-26JUN27CROGHA). Without this cache, the
# watcher makes ~48 /markets calls per cycle even though many are duplicates.
# With it, unique events are fetched once per 30s. Re-raises on HTTP error.
_markets_cache: dict[str, tuple[float, list]] = {}
_MARKETS_CACHE_TTL = 30.0


async def _get_markets_for_event(event_ticker: str) -> list:
    """Return the markets list for an event, cached per event_ticker for 30s."""
    now = time.monotonic()
    entry = _markets_cache.get(event_ticker)
    if entry is not None and (now - entry[0]) < _MARKETS_CACHE_TTL:
        return entry[1]
    resp = await _fetch_json("markets", {"event_ticker": event_ticker})
    markets = resp.get("markets", [])
    _markets_cache[event_ticker] = (now, markets)
    return markets


async def fetch_per_match_market(pm: str) -> tuple[dict, date] | None:
    """Search KXWCGAME events for the team's nearest match. Return (market, match_date).

    The parsed match_date is threaded back so extract_market_fields can label
    scheduled vs ongoing vs closed — Kalshi's 'active' status alone can't (D26).
    Previously match_date was computed for selection then discarded (the `_`).

    Uses _get_events() (cached 30s) instead of hitting /events fresh every call.
    The watcher polls 48 teams per cycle — without the cache that's 48 identical
    /events calls, which trips Kalshi's rate limit (429). With the cache it's 1.

    Steps:
      1. GET /events?series_ticker=KXWCGAME  ->  {"events": [...]}  (cached 30s)
      2. Filter for events whose event_ticker contains pm
         (e.g. "BRA" in "KXWCGAME-26JUN24SCOBRA")
      3. Parse the date from each matching ticker:
         ticker[9:16] = "26JUN24" -> year=2026, month=6, day=24
         Use the MONTHS dict above. Build a date() for each.
      4. Pick the match nearest to today: min(abs(match_date - date.today()))
      5. GET /markets?event_ticker={that event's ticker}  ->  {"markets": [...]}
      6. Return (market, match_date) for the ticker ending "-{pm}"  (NOT -TIE)

    Return None if no matching event or market found.
    Re-raise on HTTP errors (timeout, 500) — caller handles.
    """
    events = await _get_events()

    # Filter for events whose event_ticker contains the team's pm code.
    # Substring match — works because the 3-letter FIFA codes don't collide
    # with each other within a ticker (verified across the live KXWCGAME set).
    matches = [e for e in events if pm in e.get("event_ticker", "")]
    if not matches:
        return None

    # Track the closest match via (event_ticker, match_date); replace when
    # a candidate is nearer to today. Manual tracking (not min()+lambda) so
    # the comparison is visible and explainable in review.
    today = date.today()
    closest: tuple[str, date] | None = None
    for e in matches:
        ticker = e["event_ticker"]
        date_str = ticker[9:16]  # "26JUN24" — verified against live tickers
        year = 2000 + int(date_str[0:2])
        month = MONTHS[date_str[2:5]]
        day = int(date_str[5:7])
        match_date = date(year, month, day)
        if closest is None or abs(match_date - today) < abs(closest[1] - today):
            closest = (ticker, match_date)

    # Fetch markets for the chosen event, return the team's market (not -TIE).
    # Return (market, match_date) — the date labels match_status downstream (D26).
    if closest is None:
        return None
    chosen_ticker, chosen_date = closest
    markets = await _get_markets_for_event(chosen_ticker)
    suffix = f"-{pm}"
    for m in markets:
        if m.get("ticker", "").endswith(suffix):
            return m, chosen_date
    return None


# --- Orchestrator (ENGINEER TYPES THIS) -------------------------------------


async def fetch_market_for_team(team: str) -> tuple[dict, date | None] | None:
    """Given a team name (e.g. "brazil"), return (market, match_date) or None.

    match_date is the parsed date of the chosen per-match event (D26), or None
    for the tournament-winner fallback (no single match date). Threading it lets
    extract_market_fields label scheduled vs ongoing vs closed — Kalshi's
    'active' status alone can't (D26).

    Steps:
      1. Look up team via get_team(team)  ->  {"tw": ..., "pm": ...}  or None
      2. Try fetch_per_match_market(pm) first  ->  (market, match_date) or None
      3. If None, fall back to fetch_tournament_winner_market(tw) -> (market, None)
      4. Return (market, match_date), or None if no market at all

    If get_team returns None (unknown team), return None.
    Raises on network error — the endpoint (Component 6) catches and
    returns a 200 with degraded narrative (D17).
    """
    codes = get_team(team)
    if codes is None:
        return None
    # Per-match first (in-play is most relevant); fall back to tournament-winner.
    # This is the D15 decision point. It used to be a one-line `or`; threading
    # match_date as a tuple broke `or` (a tuple is truthy even holding Nones),
    # so the fallback is now explicit. Tradeoff: one extra line for type-correctness.
    pm_result = await fetch_per_match_market(codes["pm"])
    if pm_result is not None:
        return pm_result  # (market, match_date)
    market = await fetch_tournament_winner_market(codes["tw"])
    if market is None:
        return None
    return market, None  # tournament-winner has no single match date


# --- Data transforms (Component 3) ------------------------------------------
# Pure functions: raw Kalshi dict -> structured fields. No I/O, no side effects.
# Internal numeric type: integer basis points (D9) — no float arithmetic.
# 5% = 500 bp. The endpoint converts bp -> float at the JSON output boundary.


def compute_delta(
    rolling_window: list[tuple[float, int]] | None, current_prob: int | None
) -> int | None:
    if rolling_window is None or current_prob is None or len(rolling_window) == 0:
        return None

    span = rolling_window[-1][0] - rolling_window[0][0]
    if span >= 60:
        return current_prob - rolling_window[0][1]


def price_to_prob(price_dollars: str | None) -> int | None:
    if price_dollars is None:
        return None
    else:
        try:
            decimal: Decimal = Decimal(price_dollars)
            if not (0 <= decimal <= 1):
                return None
            return int(decimal * 10000)
        except (ValueError, InvalidOperation):
            return None


def _parse_int_amount(s: str | None) -> int | None:
    """Parse a Kalshi fixed-point/decimal string to int. None on garbage."""
    if s is None or s == "":
        return None
    try:
        return int(Decimal(s))
    except (ValueError, InvalidOperation):
        return None


def _extract_opponent(title: str | None, yes_sub_title: str | None) -> str | None:
    """Parse opponent from a per-match market title.
    'Scotland vs Brazil Winner?' + yes_sub='Brazil' -> 'Scotland'.
    Tournament-winner titles have no ' vs ' -> None."""
    if not title or not yes_sub_title or " vs " not in title:
        return None
    parts = title.split(" vs ")
    if len(parts) != 2:
        return None
    # Our team's side contains yes_sub_title; opponent is the other side.
    if yes_sub_title in parts[1]:
        opponent_side = parts[0]
    elif yes_sub_title in parts[0]:
        opponent_side = parts[1]
    else:
        return None
    words = opponent_side.split()
    return words[0] if words else None


def extract_market_fields(market: dict | None, match_date: date | None = None) -> dict:
    """Pull structured fields from a raw Kalshi market object.

    Returns a dict with: opponent, match_status, market, current_prob,
    volume, liquidity, last_updated. All values are None if the market
    is None or fields are missing (D17 graceful degradation).

    current_prob is in basis points (int 0-10000); the endpoint converts
    to float at the JSON output boundary (D9).

    match_status (D26): Kalshi's `status` is 'active' for every KXWCGAME
    market until settled, so it can't distinguish scheduled from ongoing
    from past. `match_date` (parsed from the event ticker in
    fetch_per_match_market) disambiguates: future -> 'scheduled', today ->
    'ongoing', past -> 'closed'. A settled status ('finalized'/'closed'/
    'settled') overrides the date. When match_date is None (tournament-
    winner, or a caller that didn't thread it), the old 'active' ->
    'scheduled' fallback applies. match_status is only set for per-match
    markets (opponent is not None).
    """
    if market is None:
        return {
            "opponent": None,
            "match_status": None,
            "market": None,
            "current_prob": None,
            "volume": None,
            "liquidity": None,
            "last_updated": None,
        }

    ticker = market.get("ticker")
    title = market.get("title")
    yes_sub = market.get("yes_sub_title")
    status = market.get("status", "")

    opponent = _extract_opponent(title, yes_sub)

    # Match status: only meaningful for per-match markets (opponent is not None).
    if opponent is not None:
        if status in ("finalized", "closed", "settled"):
            match_status = "closed"
        elif match_date is not None:
            today = date.today()
            if match_date > today:
                match_status = "scheduled"
            elif match_date == today:
                match_status = "ongoing"
            else:
                match_status = "closed"
        elif status == "active":
            match_status = "scheduled"
        else:
            match_status = status or None
    else:
        match_status = None

    return {
        "opponent": opponent,
        "match_status": match_status,
        "market": ticker,
        "current_prob": price_to_prob(market.get("last_price_dollars")),
        "volume": _parse_int_amount(market.get("volume_fp")),
        "liquidity": _parse_int_amount(market.get("liquidity_dollars")),
        "last_updated": market.get("updated_time"),
    }


# --- LLM client (Component 4) -----------------------------------------------
# OpenRouter makes the LLM swappable via one env var (D14). Isolated behind
# one function so swapping providers is a one-line change.
#
# API shape (verified via Context7, June 2026):
#   POST https://openrouter.ai/api/v1/chat/completions
#   Headers: Authorization: Bearer {key}, Content-Type: application/json
#   Body: {"model": ..., "messages": [{role, content}, ...], "max_tokens": ..., "temperature": ...}
#   Response: choices[0].message.content (standard OpenAI shape)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ENGINEER TYPES THIS — the hallucination constraint in code (D18).
# One sentence: who the LLM is, what it must use, what it must NOT do.
SYSTEM_PROMPT = "You are a sports commentator writing for a casual fan. Use ONLY the facts provided. Do not invent events, scores, or statistics. If the facts are thin, keep it short."


def _build_user_prompt(data: dict) -> str:
    """Build a LABELED user prompt from display-format data (D18/D29).

    Why labeled: the old `json.dumps(data)` sent bare numbers under ambiguous
    keys, and the 8B model misread `volume` (betting dollars) as 'viewers'
    (DECISIONS known limitation). Labeling each field attacks the cause — the
    LLM sees 'dollars wagered (betting activity)', not a bare int to guess at.
    None fields are OMITTED (not 'None'): no surface, no hallucination. Volume
    is omitted entirely when None so there's nothing to misread. The prompt
    positively labels volume rather than saying 'not viewers' — naming a
    forbidden concept can prime an 8B model toward it.
    """
    team = data.get("team", "the team")
    opponent = data.get("opponent")
    match_status = data.get("match_status")
    prob = data.get("current_prob")
    delta = data.get("delta_1m")
    volume = data.get("volume")

    lines = [
        f"Write one or two sentences for a casual fan about {team}'s World Cup "
        f"prediction market. Use ONLY the facts below. Do not invent scores, "
        f"events, or statistics. Keep it short if the facts are thin."
    ]
    lines.append(f"- Team: {team}")
    lines.append(
        f"- Opponent: {opponent if opponent is not None else 'no specific opponent (tournament-winner market)'}"
    )
    if match_status is not None:
        # Plain English, not the enum. The raw "ongoing" under "Match status"
        # let the LLM write "the prediction market is ongoing" — conflating the
        # MARKET (tradeable for days) with the MATCH. Describing the GAME's
        # timing removes that conflation. "today" (not "in play") is also honest:
        # we have the match DATE, not kickoff TIME, so we can't claim it's live.
        timing = {
            "scheduled": "the match is in the future",
            "ongoing": "the match is today",
            "closed": "the match has been played",
        }.get(match_status, match_status)
        lines.append(f"- Game timing: {timing}")

    if prob is not None:
        lines.append(f"- Current probability of winning: {prob * 100:.1f}%")
    else:
        lines.append("- Current probability of winning: unavailable")

    if delta is not None and delta != 0:
        prev = prob - delta if prob is not None else None
        direction = "up" if delta > 0 else "down"
        if prev is not None and prob is not None:
            lines.append(
                f"- Over the last minute, probability moved {direction} "
                f"from {prev * 100:.1f}% to {prob * 100:.1f}%"
            )
        else:
            lines.append(f"- Over the last minute, probability moved {direction}")
    elif delta == 0:
        lines.append("- Over the last minute, probability is unchanged")
    else:
        lines.append("- No 1-minute history yet (just started watching this market)")

    if volume is not None:
        lines.append(
            f"- Volume: {volume / 1e6:.1f}M dollars traded on the market. "
            f"This is the total dollars wagered (betting activity)."
        )

    return "\n".join(lines)


# ENGINEER TYPES THE BODY — prompt construction + API call + bounded backoff.
async def generate_narrative(data: dict) -> str:
    """Send structured data to OpenRouter, return narrative prose.

    `data` contains display-format values (floats, not internal bp) — the
    endpoint (Component 6) converts bp -> float before calling this.

    On 429/5xx: retry with bounded backoff (2 retries, 1s then 2s).
    On persistent failure or 4xx (auth/request errors): raise.
    The caller (Component 6 endpoint) catches and falls back to template_narrative (D17).
    """
    body = _build_user_prompt(data)
    request_body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": body},
        ],
        "max_tokens": 150,
        "temperature": 0.7,
    }
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    # Bounded backoff: 2 retries on 429/5xx, sleep 1s then 2s.
    # 4xx (except 429) = bad auth/request, retrying won't help -> raise immediately.
    client = _get_client()
    for attempt in range(3):
        try:
            response = await client.post(OPENROUTER_URL, headers=headers, json=request_body)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                response.raise_for_status()
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except httpx.TransportError:
            if attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            raise
    raise RuntimeError("OpenRouter retries exhausted")


# --- Hallucination guard (Component 5) --------------------------------------
# After the LLM returns prose, verify it against the source data. If the LLM
# invented or altered a number, throw its prose away and substitute a
# deterministic template. The user NEVER sees a hallucinated statistic.
#
# source_data is DISPLAY-FORMAT (floats) -- the same dict handed to
# generate_narrative. The endpoint (Component 6) converts internal basis
# points -> float before calling either function below (D9, D27).
#
# Two known limitations (review gold -- name them before the reviewer does):
#   1. Catches invented STATISTICS, not invented PROSE events. "Brazil won
#      3-0" -> caught (3, 0 not in source). "Brazil looked dominant" -> NOT
#      caught. Mitigated by the constrained system prompt (D18).
#   2. Global-set comparison can't catch field-conflation (volume 12.5M
#      misread as 12.5% prob). Logged as a v1 gap / "another week" fix.


def verify_narrative(narrative: str, source_data: dict) -> bool:
    r"""True if every number in the narrative is consistent with source_data
    (or the narrative has no numbers). False -> fall back to template.

    Global-set comparison: each prose number must be isclose to SOME allowed
    number built from source_data in any plausible form (0.05 or 5.0,
    12.5M or 12500000). Empty narrative -> False (checked BEFORE the
    no-numbers rule, or "" passes vacuously). Allowed set includes
    previous = p - d, since the LLM expresses delta indirectly as "from X%
    to Y%". Known gaps: prose-only inventions, field-conflation -- both
    logged in DECISIONS.md, mitigated by the constrained prompt (D18).
    """
    if not narrative:
        return False

    prose_nums = [float(m) for m in re.findall(r"-?\d+\.?\d*", narrative)]
    if not prose_nums:
        return True  # thin narrative is allowed by the system prompt

    allowed: list[float] = []
    p = source_data.get("current_prob")
    d = source_data.get("delta_1m")
    v = source_data.get("volume")

    if p is not None:
        allowed += [p, p * 100]
    if d is not None:
        allowed += [d, d * 100]
    if p is not None and d is not None:
        prev = p - d
        allowed += [prev, prev * 100]
    if v is not None:
        allowed += [v, v / 1e6, v / 1e9]

    for n in prose_nums:
        if not any(math.isclose(n, a, rel_tol=0.02, abs_tol=0.05) for a in allowed):
            return False
    return True


def template_narrative(data: dict) -> str:
    """Deterministic fallback narrative. Same input -> same string, always.

    None fields are omitted, never shown as "None" (D17: narrative is always
    present and human-readable). Uses `is None` checks -- 0.0 is a real
    probability, not "unavailable". Omits the up/down clause when delta is
    None or 0; omits Volume when volume is None.
    """
    p = data.get("current_prob")
    d = data.get("delta_1m")
    v = data.get("volume")
    team = data.get("team", "The team")

    if p is None:
        return f"{team}'s market data is currently unavailable."

    base = f"{team}'s current probability is {p * 100:.1f}%"

    if d is not None and d != 0:
        prev = p - d
        direction = "down from" if d < 0 else "up from"
        base += f", {direction} {prev * 100:.1f}% previously"

    if v is not None:
        base += f". Volume: {v / 1e6:.1f}M."
    else:
        base += "."

    return base


# --- FastAPI endpoint (Component 6) ------------------------------------------
# Wires Components 1-5 into GET /team/{team_name} -> TeamSentiment JSON.
#
# The orchestrator's real job is ERROR HANDLING (D17): every external call can
# fail, and a known team always gets HTTP 200 with a present `narrative` field.
# Three degraded modes (D17):
#   - Kalshi down/timeout  -> null fields + "temporarily unavailable"
#   - No market on Kalshi  -> null fields + "no active market found for X"
#   - LLM fails/hallucinates -> structured data intact + template_narrative
#
# Unknown team -> HTTP 404 (the resource doesn't exist; distinct from a known
# team whose data we couldn't fetch). See D31.
#
# Internal numeric type is basis points (D27); bp -> float happens at the ONE
# line where the Pydantic model needs a float. The rolling window feeds
# compute_delta (D12) -- cold-start None until the process has >=1m of samples
# for that team. The on-demand endpoint populates the window as a side effect,
# so repeated calls for the same team eventually yield a non-None delta_1m.
# The watcher (Component 8, in watcher/core.py) is the PRIMARY window-filler;
# without it, sporadic curls can't accumulate 1m of span (any gap >60s resets
# to span=0).

_watcher_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _watcher_task
    _watcher_task = asyncio.create_task(
        _watcher_loop(
            fetcher=fetch_market_for_team,
            extract_fields=extract_market_fields,
            generate_narrative=generate_narrative,
            verify_narrative=verify_narrative,
            template_narrative=template_narrative,
        )
    )
    yield
    if _watcher_task:
        _watcher_task.cancel()
        try:
            await _watcher_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Speakeasy",
    description="Kalshi World Cup prediction-market sentiment as developer-consumable JSON.",
    lifespan=lifespan,
)


@app.get("/team/{team_name}", response_model=TeamSentiment)
async def get_team_sentiment(team_name: str) -> TeamSentiment:
    """Return World Cup market sentiment for a team as developer-consumable JSON.

    Flow: config lookup -> Kalshi fetch (30s cache) -> transforms -> LLM
    narrative -> hallucination guard -> assembled TeamSentiment.

    Known team + any failure -> HTTP 200 with null fields + explanatory
    narrative (D17). Unknown team -> HTTP 404 (nonexistent resource, not an
    infrastructure failure -- D31).
    """
    # 1. Config lookup. Unknown team -> 404 (D31: client error, not D17).
    codes = get_team(team_name)
    if codes is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown team '{team_name}'. Supported: {', '.join(sorted(TEAMS))}",
        )

    # Canonical key (shared cache + rolling window regardless of input casing).
    team_key = team_name.strip().lower().replace(" ", "_").replace("-", "_")
    display_name = team_key.replace("_", " ").title()

    # 2. Fetch market (30s cache). ANY error -> degrade to None (D17).
    #    Returns (market, match_date); match_date labels match_status (D26).
    fetch_failed = False
    match_date: date | None = None
    try:
        fetched = await _get_cached_or_fetch(team_key, fetch_market_for_team)
        if fetched is not None:
            market, match_date = fetched
        else:
            market = None
    except Exception:
        fetch_failed = True
        market = None

    # 3. Extract structured fields (pure, never raises). current_prob is in bp.
    fields = extract_market_fields(market, match_date)

    # 4. Update rolling window + compute delta (bp internally, D27).
    now = time.monotonic()
    _record_sample(team_key, fields["current_prob"], now)
    delta_bp = compute_delta(_rolling.get(team_key), fields["current_prob"])

    # 5. bp -> float at the output boundary (D27: the ONE line floats appear).
    current_prob = fields["current_prob"] / 10000.0 if fields["current_prob"] is not None else None
    delta_1m = delta_bp / 10000.0 if delta_bp is not None else None

    # 6. Display-format dict for the LLM, guard, and template (floats, D27).
    display = {
        "team": display_name,
        "opponent": fields["opponent"],
        "match_status": fields["match_status"],
        "current_prob": current_prob,
        "delta_1m": delta_1m,
        "volume": fields["volume"],
    }

    # 7. Narrative (D17). Three degraded modes + the LLM-verified happy path.
    if fetch_failed:
        narrative = f"Market data for {display_name} is temporarily unavailable."
    elif market is None:
        narrative = f"No active market found for {display_name}."
    elif current_prob is None:
        # Market exists but price is missing/garbage -> template handles None.
        narrative = template_narrative(display)
    else:
        # We have a probability -> try the LLM, verify, fall back to template.
        try:
            raw = await generate_narrative(display)
            narrative = raw if verify_narrative(raw, display) else template_narrative(display)
        except Exception:
            narrative = template_narrative(display)

    # 8. Assemble the response (structured fields are the source of truth).
    return TeamSentiment(
        team=team_key,
        opponent=fields["opponent"],
        match_status=fields["match_status"],
        market=fields["market"],
        current_prob=current_prob,
        delta_1m=delta_1m,
        volume=fields["volume"],
        liquidity=fields["liquidity"],
        last_updated=fields["last_updated"],
        narrative=narrative,
    )


@app.get("/")
async def root() -> dict:
    """Health check + supported teams (developer discovery)."""
    return {"status": "ok", "teams": sorted(TEAMS)}


# --- Smoke test -------------------------------------------------------------


async def smoke():
    """Kalshi connectivity smoke test.

    Run via: uv run python -c "import asyncio, main; asyncio.run(main.smoke())"
    Hits the live API (no auth) — verifies TW, per-match, and orchestrator paths.
    """
    m = await fetch_tournament_winner_market("BR")
    if m:
        print(f"TW:  {m['ticker']} last={m['last_price_dollars']}")
    else:
        print("TW:  None")

    pm = await fetch_per_match_market("BRA")
    if pm:
        m2, _ = pm
        print(f"PM:  {m2['ticker']} last={m2['last_price_dollars']}")
    else:
        print("PM:  None")

    orch = await fetch_market_for_team("brazil")
    if orch:
        m3, _ = orch
        print(f"ORCH: {m3['ticker']} last={m3['last_price_dollars']}")
    else:
        print("ORCH: None")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app)
