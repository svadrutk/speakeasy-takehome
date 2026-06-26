"""Background watcher: polls Kalshi markets for configured teams and fires
macOS notifications when a team's probability moves meaningfully.

Dependency-injected so the data source and narrative functions can be swapped
(main.py wires the real Kalshi fetcher + LLM; the demo CLI wires a synthetic
fetcher). Shared state (_rolling, _cache, _last_notified) lives here because
the watcher is the primary window-filler; main.py's endpoint imports _rolling
and _record_sample to warm the same window for on-demand curls.

Design: a deterministic gate (should_notify) decides WHEN to notify; the LLM
(only when the gate fires) decides WHAT to say. The watcher never raises
(D17): a notify/poll failure must not take down the window-filler.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import date
from typing import Awaitable, Callable, Optional

from config import (
    CACHE_TTL_SECONDS,
    COOLDOWN_SECONDS,
    DEFAULT_THRESHOLD,
    INTER_TEAM_DELAY_SECONDS,
    WATCH_TEAMS,
)

# Shared state (imported by main.py for endpoint delta computation)
_WINDOW_MAX_AGE: float = 120.0
_rolling: dict[str, list[tuple[float, int]]] = {}
_cache: dict[str, tuple[float, tuple[dict, date | None] | None]] = {}
_last_notified: dict[str, float] = {}


# --- Notification gate (pure) ---
def should_notify(
    current_bp: int | None,
    previous_bp: int | None,
    threshold: float = DEFAULT_THRESHOLD,
) -> bool:
    if current_bp is None or previous_bp is None:
        return False
    if previous_bp == 0:
        return current_bp > 0
    return abs(current_bp - previous_bp) >= threshold * previous_bp


# --- macOS notifications (best-effort) ---
_NOTIF_TITLE_PREFIX = "Kalshi"


def send_notification(title: str, message: str) -> None:
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=5)
    except Exception:
        pass


# --- Internals shared with endpoint ---
def _record_sample(team_key: str, prob_bp: int | None, now: float) -> None:
    if prob_bp is None:
        return
    window = _rolling.setdefault(team_key, [])
    window.append((now, prob_bp))
    _rolling[team_key] = [s for s in window if now - s[0] <= _WINDOW_MAX_AGE]


async def _get_cached_or_fetch(
    team_key: str,
    fetcher: Callable[[str], Awaitable[tuple[dict, date | None] | None]],
) -> tuple[dict, date | None] | None:
    now = time.monotonic()
    entry = _cache.get(team_key)
    if entry is not None and (now - entry[0]) < CACHE_TTL_SECONDS:
        return entry[1]
    result = await fetcher(team_key)
    _cache[team_key] = (now, result)
    return result


TraceFn = Callable[[str], None]


async def _poll_team(
    team_key: str,
    *,
    fetcher: Callable[[str], Awaitable[tuple[dict, date | None] | None]],
    extract_fields: Callable[[dict | None, Optional[date]], dict],
    generate_narrative: Callable[[dict], Awaitable[str]],
    verify_narrative: Callable[[str, dict], bool],
    template_narrative: Callable[[dict], str],
    trace: TraceFn | None = None,
) -> None:
    """Fetch one team's market, record a sample, maybe notify. Never raises.

    Gate compares current vs last sample (~previous poll). On fire: LLM narrative
    with template fallback, then send_notification. Cooldown prevents spam.
    """
    try:
        window = _rolling.get(team_key)
        previous_bp = window[-1][1] if window else None

        fetched = await _get_cached_or_fetch(team_key, fetcher)
        if fetched is None:
            if trace:
                trace(f"{team_key}: no market -> skip")
            return
        market, match_date = fetched
        fields = extract_fields(market, match_date)
        current_bp = fields["current_prob"]
        now = time.monotonic()
        _record_sample(team_key, current_bp, now)

        if trace and previous_bp is not None and current_bp is not None:
            # Log decision context pre-gate
            prev = previous_bp / 100.0
            curr = current_bp / 100.0
            trace(f"{team_key}: prev={prev:.2f}bp curr={curr:.2f}bp")

        if not should_notify(current_bp, previous_bp):
            if trace:
                trace(f"{team_key}: gate=no")
            return

        now_m = time.monotonic()
        last = _last_notified.get(team_key, 0.0)
        if now_m - last < COOLDOWN_SECONDS:
            if trace:
                trace(f"{team_key}: cooldown -> suppress")
            return
        _last_notified[team_key] = now_m

        display_name = team_key.replace("_", " ").title()
        current_prob_f = current_bp / 10000.0 if current_bp is not None else None
        previous_prob_f = previous_bp / 10000.0 if previous_bp is not None else None
        delta_bp = (
            (current_bp - previous_bp)
            if current_bp is not None and previous_bp is not None
            else None
        )
        delta_f = delta_bp / 10000.0 if delta_bp is not None else None
        display = {
            "team": display_name,
            "opponent": fields["opponent"],
            "match_status": fields["match_status"],
            "current_prob": current_prob_f,
            "previous_prob": previous_prob_f,
            "delta_1m": delta_f,
            "volume": fields["volume"],
        }

        try:
            narrative = await generate_narrative(display)
            if not verify_narrative(narrative, display):
                if trace:
                    trace("guard: LLM invalid -> template")
                narrative = template_narrative(display)
        except Exception:
            if trace:
                trace("guard: LLM error -> template")
            narrative = template_narrative(display)

        if trace:
            trace("notifying…")
        send_notification(f"{_NOTIF_TITLE_PREFIX}: {display_name}", narrative)
    except Exception:
        # Never crash the loop
        if trace:
            trace("error: swallowed per D17")
        pass


async def _watcher_loop(
    *,
    fetcher: Callable[[str], Awaitable[tuple[dict, date | None] | None]],
    extract_fields: Callable[[dict | None, Optional[date]], dict],
    generate_narrative: Callable[[dict], Awaitable[str]],
    verify_narrative: Callable[[str, dict], bool],
    template_narrative: Callable[[dict], str],
    trace: TraceFn | None = None,
) -> None:
    while True:
        for team_key in WATCH_TEAMS:
            await _poll_team(
                team_key,
                fetcher=fetcher,
                extract_fields=extract_fields,
                generate_narrative=generate_narrative,
                verify_narrative=verify_narrative,
                template_narrative=template_narrative,
                trace=trace,
            )
            await asyncio.sleep(INTER_TEAM_DELAY_SECONDS)


def reset_demo_state() -> None:
    """Reset rolling + cooldown state for clean demo runs."""
    _rolling.clear()
    _last_notified.clear()


# Public API
__all__ = [
    "_rolling",
    "_cache",
    "_WINDOW_MAX_AGE",
    "_get_cached_or_fetch",
    "_record_sample",
    "_watcher_loop",
    "_poll_team",
    "should_notify",
    "send_notification",
    "reset_demo_state",
]
