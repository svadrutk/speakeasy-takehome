"""Unit tests for the watcher (Component 8) + notification wiring (9/10).

_poll_team is the testable unit: fetch one team's market, record a sample in
_rolling, and (since the gate wiring) maybe notify. The loop itself (while
True + asyncio.sleep) is hard to unit-test, so we test the single-poll step
and the failure contract (D17: never raises).

Why the watcher exists: without continuous polling, delta_1m is always null
for on-demand curls. The pruning threshold (keep <=60s) equals the span
threshold (need >=60s), so sporadic curls with gaps >60s always wipe
history. The watcher polls at 30s intervals, keeping span pinned at 60 so
every curl after 1 min of watcher uptime gets a non-None delta (D32).

Notify wiring (D5): gate = current vs last sample (per-poll delta). On fire:
generate_narrative (D29) -> send_notification, with template_narrative as D17
fallback. Cooldown (_last_notified) prevents oscillation spam.

Uses asyncio.run() instead of pytest-asyncio to avoid adding a dependency.

Dependency injection (Task 1): _poll_team takes fetcher/extract_fields/
generate_narrative/verify_narrative/template_narrative as keyword-only args.
Tests pass AsyncMock instances or main's real functions directly instead of
patching main's module globals. _get_cached_or_fetch still caches internally,
so multi-poll tests with different markets clear _cache between polls to
force the fetcher to be called each time. send_notification is called by name
from watcher.core, so we patch watcher.core.send_notification.
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import main
from watcher.core import _cache, _last_notified, _poll_team, _rolling


def _market_with_prob(prob_bp: int) -> dict:
    """Build a raw Kalshi market dict whose last_price_dollars yields prob_bp."""
    return {
        "ticker": "KXWCGAME-26JUN25JPNSWE-JPN",
        "title": "Japan vs Sweden Winner?",
        "yes_sub_title": "Japan",
        "status": "active",
        "last_price_dollars": f"{prob_bp / 10000:.4f}",
        "volume_fp": "1000000",
    }


def _reset_rolling():
    _rolling.clear()


def _reset_notified():
    _last_notified.clear()


def _poll(
    team: str = "japan",
    *,
    fetcher: AsyncMock,
    generate_narrative: AsyncMock,
    verify_narrative=lambda *_: True,
) -> None:
    """Call _poll_team with main's real extract/template + injected fetcher/gen."""
    asyncio.run(
        _poll_team(
            team,
            fetcher=fetcher,
            extract_fields=main.extract_market_fields,
            generate_narrative=generate_narrative,
            verify_narrative=verify_narrative,
            template_narrative=main.template_narrative,
        )
    )


# --- Window-building + failure contract (pre-existing) ---------------------


def test_poll_team_records_sample():
    # Happy path: fetch succeeds -> one sample appended to _rolling.
    _reset_rolling()
    _cache.clear()
    market = _market_with_prob(3900)
    _poll(
        fetcher=AsyncMock(return_value=market),
        generate_narrative=AsyncMock(return_value=""),
    )
    assert "japan" in _rolling
    assert len(_rolling["japan"]) == 1
    assert _rolling["japan"][0][1] == 3900


def test_poll_team_handles_fetch_exception():
    # D17: fetch raises -> _poll_team swallows, no crash, no sample recorded.
    _reset_rolling()
    _cache.clear()
    _poll(
        fetcher=AsyncMock(side_effect=RuntimeError("Kalshi down")),
        generate_narrative=AsyncMock(return_value=""),
    )
    assert "japan" not in _rolling or len(_rolling["japan"]) == 0


def test_poll_team_handles_none_market():
    # Fetch returns None (no market found) -> skip, no sample, no crash.
    _reset_rolling()
    _cache.clear()
    _poll(
        fetcher=AsyncMock(return_value=None),
        generate_narrative=AsyncMock(return_value=""),
    )
    assert "japan" not in _rolling or len(_rolling["japan"]) == 0


def test_poll_team_handles_none_prob():
    # Market exists but price is garbage -> prob None -> _record_sample skips.
    _reset_rolling()
    _cache.clear()
    market = _market_with_prob(3900)
    market["last_price_dollars"] = "garbage"
    _poll(
        fetcher=AsyncMock(return_value=market),
        generate_narrative=AsyncMock(return_value=""),
    )
    assert "japan" not in _rolling or len(_rolling["japan"]) == 0


def test_poll_team_multiple_polls_build_window():
    # Simulate the watcher polling 3 times: window should have 3 samples.
    _reset_rolling()
    _cache.clear()
    market = _market_with_prob(3900)
    fetcher = AsyncMock(return_value=market)
    gen = AsyncMock(return_value="")
    for _ in range(3):
        _cache.clear()
        _poll(fetcher=fetcher, generate_narrative=gen)
        time.sleep(0.01)
    assert len(_rolling["japan"]) == 3


# --- Notify wiring (gate -> LLM/template -> send_notification) --------------


def test_gate_fires_sends_notification():
    # First poll: 500bp (cold start, previous=None -> no notify).
    # Second poll: 650bp (delta 0.30 >= 0.20 -> gate fires -> notify).
    _reset_rolling()
    _reset_notified()
    _cache.clear()
    m1, m2 = _market_with_prob(500), _market_with_prob(650)
    fetcher = AsyncMock(side_effect=[m1, m2])
    gen = AsyncMock(return_value="Japan surged to 6.5%.")
    with patch("watcher.core.send_notification") as ms:
        _poll(fetcher=fetcher, generate_narrative=gen)
        _cache.clear()
        _poll(fetcher=fetcher, generate_narrative=gen)
    ms.assert_called_once()
    # Title includes the team name; body is the LLM narrative.
    args = ms.call_args.args
    assert "Japan" in args[0]
    assert "6.5%" in args[1]


def test_gate_below_threshold_no_notification():
    # First poll: 500bp. Second poll: 550bp (delta 0.10 < 0.20 -> no notify).
    _reset_rolling()
    _reset_notified()
    _cache.clear()
    m1, m2 = _market_with_prob(500), _market_with_prob(550)
    fetcher = AsyncMock(side_effect=[m1, m2])
    gen = AsyncMock(return_value="Japan nudged up.")
    with patch("watcher.core.send_notification") as ms:
        _poll(fetcher=fetcher, generate_narrative=gen)
        _cache.clear()
        _poll(fetcher=fetcher, generate_narrative=gen)
    ms.assert_not_called()


def test_cooldown_suppresses_repeat():
    # Three polls: 500 (cold), 650 (fires), 800 (would fire but within cooldown).
    # COOLDOWN_SECONDS=300; test runs in <1s -> 3rd call is suppressed.
    _reset_rolling()
    _reset_notified()
    _cache.clear()
    m1, m2, m3 = _market_with_prob(500), _market_with_prob(650), _market_with_prob(800)
    fetcher = AsyncMock(side_effect=[m1, m2, m3])
    gen = AsyncMock(return_value="Japan moved.")
    with patch("watcher.core.send_notification") as ms:
        for _ in range(3):
            _cache.clear()
            _poll(fetcher=fetcher, generate_narrative=gen)
    ms.assert_called_once()  # 2nd poll fires; 3rd suppressed by cooldown


def test_llm_fails_uses_template_fallback():
    # D17: generate_narrative raises -> template_narrative -> still notifies.
    _reset_rolling()
    _reset_notified()
    _cache.clear()
    m1, m2 = _market_with_prob(500), _market_with_prob(650)
    fetcher = AsyncMock(side_effect=[m1, m2])
    gen = AsyncMock(side_effect=RuntimeError("OpenRouter down"))
    with patch("watcher.core.send_notification") as ms:
        _poll(fetcher=fetcher, generate_narrative=gen)
        _cache.clear()
        _poll(fetcher=fetcher, generate_narrative=gen)
    ms.assert_called_once()
    # Template narrative contains "current probability" (D30 format).
    assert "current probability" in ms.call_args.args[1]
