"""Kalshi client + FastAPI service — World Cup prediction-market sentiment.

No authentication needed for public market data (D8). REST polling, not
WebSocket — WS requires RSA auth even for public channels (D16), and the
100 req/sec REST limit is generous for our scale.

The background watcher polls a configured subset of teams (WATCH_TEAMS,
loaded from watch_teams.json), not all 48 in TEAMS — matches docs/SPEC.md's
Watcher(teams) design. A team not in WATCH_TEAMS still works on-demand,
but its delta_1m is always null (no watcher warming its window).
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI, HTTPException

from config import (
    CODE_TO_NAME,
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

# Pre-kickoff grace for the scheduled->ongoing transition. World Cup kickoffs
# are reliable within ~30m, so we flip to "ongoing" 30m before occurrence_datetime
# to absorb minor delays. Wider would guess; narrower misses imminent kickoffs.
KICKOFF_GRACE = timedelta(minutes=30)

# Knockout-phase priority: ADVANCE first ("to advance" — including ET/penalties,
# the fan-meaningful probability), KXWCGAME fallback (regulation-time only).
KXWCADVANCE_SERIES = "KXWCADVANCE"
_MATCH_SERIES_PRIORITY = (KXWCADVANCE_SERIES, KXWCGAME_SERIES)

# Cache open markets per series. Shared across all watcher teams per cycle
# so the 48-teams fan-out collapses to 1 fetch per 30s per series.
_open_markets_cache: dict[str, tuple[float, list]] = {}
_OPEN_MARKETS_CACHE_TTL = 30.0


async def _get_open_markets(series: str) -> list:
    """Return /markets?series_ticker={series}&status=open, cached 30s.

    Kalshi filters past settled fixtures server-side via status=open, so the
    bidirectional-abs() past-matches bug is structurally impossible here.
    """
    now = time.monotonic()
    entry = _open_markets_cache.get(series)
    if entry is not None and (now - entry[0]) < _OPEN_MARKETS_CACHE_TTL:
        return entry[1]
    resp = await _fetch_json("markets", {"series_ticker": series, "status": "open"})
    markets = resp.get("markets", [])
    _open_markets_cache[series] = (now, markets)
    return markets


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


# --- Per-match market (rewritten Jun 30) ------------------------------------

# Uses /markets?series_ticker={series}&status=open directly — Kalshi filters
# past settled fixtures server-side, so the bidirectional-abs() past-matches
# bug is structurally impossible. Minute-precision occurrence_datetime from
# the market object replaces ticker-string date parsing. ADVANCE series first
# (knockout-phase fans care about progression), KXWCGAME fallback (group stage).


async def fetch_per_match_market(pm: str) -> dict | None:
    """Find the team's current or next-match open market. Return the market dict.

    Tries KXWCADVANCE first (knockout — advance probability including ET/penalties,
    what fans actually mean), then KXWCGAME (regulation-time only, covers group
    stage). The preference order is product-aware: in knockout rounds the
    KXWCGAME market shows ~96% tie / 1–2% per side, while KXWCADVANCE shows
    the real ~50% advance probability.

    Within each series, picks the market with the nearest occurrence_datetime
    and whose ticker ends with "-{pm}" (team's 3-letter per-match code).
    Returns None if no open market found; caller (fetch_market_for_team) falls
    back to tournament-winner per D15.
    """
    suffix = f"-{pm}"
    now_utc = datetime.now(timezone.utc)
    for series in _MATCH_SERIES_PRIORITY:
        markets = await _get_open_markets(series)
        candidates = []
        for m in markets:
            ticker = m.get("ticker", "")
            if not ticker.endswith(suffix):
                continue
            k = _parse_utc_dt(m.get("occurrence_datetime"))
            if k is None:
                continue  # garbage kickoff — can't rank, skip
            candidates.append((abs((k - now_utc).total_seconds()), m))
        if candidates:
            return min(candidates, key=lambda t: t[0])[1]
    return None


# --- Orchestrator (ENGINEER TYPES THIS) -------------------------------------


async def fetch_market_for_team(team: str) -> dict | None:
    """Given a team name (e.g. "brazil"), return the market dict or None.

    Per-match first (most relevant); tournament-winner fallback (D15).
    """
    codes = get_team(team)
    if codes is None:
        return None
    pm_market = await fetch_per_match_market(codes["pm"])
    if pm_market is not None:
        return pm_market
    return await fetch_tournament_winner_market(codes["tw"])


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


def _parse_utc_dt(s: str | None) -> datetime | None:
    """Parse a Kalshi ISO-8601 timestamp (trailing-Z UTC) to an aware UTC datetime.

    None on missing/garbage input so callers can fall back to the ticker-date
    path rather than crash. Kalshi always returns `...Z` (UTC) for time fields.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # ponytail: Kalshi occurrence_datetime is systematically 3h ahead of real
    # UTC kickoff (e.g. MEX/ECU at 01:00 UTC shows as 04:00Z). Subtract to
    # correct. Verified against FIFA match centre for 4 matches. Remove when
    # Kalshi fixes their data pipeline.
    return dt - timedelta(hours=3)


def _extract_opponent(
    title: str | None,
    yes_sub_title: str | None,
    team_name: str | None = None,
    *,
    ticker: str | None = None,
) -> str | None:
    """Parse opponent from a per-match market ticker or title.

    Primary: extract both 3-letter team codes from the ticker suffix and
    middle (e.g. KXWCADVANCE-26JUN29NEDMAR-NED -> opponent code MAR).
    Look up display name via CODE_TO_NAME.

    Fallback: parse from title string (fragile, kept for unknown ticker
    formats). 'Scotland vs Brazil Winner?' + team_name='Brazil' -> 'Scotland'.
    Tournament-winner titles have no ' vs ' -> None.
    """
    if ticker:
        parts = ticker.split("-")
        if len(parts) == 3 and len(parts[1]) >= 13:
            our_code = parts[2]
            team_codes = parts[1][7:13]
            code1 = team_codes[:3]
            code2 = team_codes[3:6]
            opp_code = code2 if code1 == our_code else (code1 if code2 == our_code else None)
            if opp_code and opp_code in CODE_TO_NAME:
                return CODE_TO_NAME[opp_code]

    if not title or " vs " not in title:
        return None
    title_parts = title.split(" vs ")
    if len(title_parts) != 2:
        return None
    identifier = team_name if team_name is not None else yes_sub_title
    if not identifier:
        return None
    if identifier in title_parts[1]:
        opponent_side = title_parts[0]
    elif identifier in title_parts[0]:
        opponent_side = title_parts[1]
    else:
        return None
    words = opponent_side.split()
    return words[0].rstrip(":") if words else None


def extract_market_fields(
    market: dict | None,
    team_name: str | None = None,
) -> dict:
    """Pull structured fields from a raw Kalshi market object.

    Returns a dict with: opponent, match_status, market, current_prob,
    volume, liquidity, last_updated. All values are None if the market
    is None or fields are missing (D17 graceful degradation).

    current_prob is in basis points (int 0-10000); the endpoint converts
    to float at the JSON output boundary (D9).

    match_status (D26): Kalshi's `status` is 'active' for every per-match market
    until settled, so it can't distinguish scheduled from ongoing from past. The
    market's `occurrence_datetime` (exact UTC kickoff, on every KXWCADVANCE and
    KXWCGAME market) disambiguates: >30m before kickoff -> 'scheduled', within
    30m of/after kickoff -> 'ongoing', until status flips to a settled state. A
    settled status ('finalized'/'closed'/'settled') overrides time. When
    `occurrence_datetime` is missing/garbage and no settled status, falls back
    to 'active' -> 'scheduled'. match_status is only set for per-match markets
    (opponent set).
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

    opponent = _extract_opponent(title, yes_sub, team_name, ticker=ticker)

    # Match status: only meaningful for per-match markets (opponent is not None).
    if opponent is not None:
        if status in ("finalized", "closed", "settled"):
            match_status = "closed"
        else:
            # Primary: the market's `occurrence_datetime` corrected by -3h
            # (Kalshi data pipeline is systematically 3h ahead of real UTC
            # kickoff — verified against FIFA match centre for 4 matches).
            # Supersedes the ticker-string date (D26 fix): the ticker embeds
            # the LOCAL matchday, so a late-ET kickoff crosses the UTC date
            # boundary and the day-granular calc mislabels it.
            kickoff = _parse_utc_dt(market.get("occurrence_datetime"))
            if kickoff is not None:
                # 30m pre-kickoff grace: WC kickoffs are reliable within ~30m, so
                # flip scheduled->ongoing 30m before kickoff to absorb minor
                # delays. Runs "ongoing" until Kalshi finalizes (status flip) —
                # the market is live until settled, so "ongoing" is correct.
                now = datetime.now(timezone.utc)
                match_status = "scheduled" if now < kickoff - KICKOFF_GRACE else "ongoing"
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
    logged in docs/DECISIONS.md, mitigated by the constrained prompt (D18).
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
    fetch_failed = False
    try:
        market = await _get_cached_or_fetch(team_key, fetch_market_for_team)
    except Exception:
        fetch_failed = True
        market = None

    # 3. Extract structured fields (pure, never raises). current_prob is in bp.
    fields = extract_market_fields(market, display_name)

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
