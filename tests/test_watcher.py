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
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import main
from main import _poll_team


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
    main._rolling.clear()


def _reset_notified():
    main._last_notified.clear()


# --- Window-building + failure contract (pre-existing) ---------------------


def test_poll_team_records_sample():
    # Happy path: fetch succeeds -> one sample appended to _rolling.
    _reset_rolling()
    market = _market_with_prob(3900)
    with patch.object(main, "_get_cached_or_fetch", new_callable=AsyncMock) as mock:
        mock.return_value = (market, None)
        asyncio.run(_poll_team("japan"))
    assert "japan" in main._rolling
    assert len(main._rolling["japan"]) == 1
    assert main._rolling["japan"][0][1] == 3900


def test_poll_team_handles_fetch_exception():
    # D17: fetch raises -> _poll_team swallows, no crash, no sample recorded.
    _reset_rolling()
    with patch.object(main, "_get_cached_or_fetch", new_callable=AsyncMock) as mock:
        mock.side_effect = RuntimeError("Kalshi down")
        asyncio.run(_poll_team("japan"))
    assert "japan" not in main._rolling or len(main._rolling["japan"]) == 0


def test_poll_team_handles_none_market():
    # Fetch returns None (no market found) -> skip, no sample, no crash.
    _reset_rolling()
    with patch.object(main, "_get_cached_or_fetch", new_callable=AsyncMock) as mock:
        mock.return_value = None
        asyncio.run(_poll_team("japan"))
    assert "japan" not in main._rolling or len(main._rolling["japan"]) == 0


def test_poll_team_handles_none_prob():
    # Market exists but price is garbage -> prob None -> _record_sample skips.
    _reset_rolling()
    market = _market_with_prob(3900)
    market["last_price_dollars"] = "garbage"
    with patch.object(main, "_get_cached_or_fetch", new_callable=AsyncMock) as mock:
        mock.return_value = (market, None)
        asyncio.run(_poll_team("japan"))
    assert "japan" not in main._rolling or len(main._rolling["japan"]) == 0


def test_poll_team_multiple_polls_build_window():
    # Simulate the watcher polling 3 times: window should have 3 samples.
    _reset_rolling()
    market = _market_with_prob(3900)
    with patch.object(main, "_get_cached_or_fetch", new_callable=AsyncMock) as mock:
        mock.return_value = (market, None)
        asyncio.run(_poll_team("japan"))
        time.sleep(0.01)
        asyncio.run(_poll_team("japan"))
        time.sleep(0.01)
        asyncio.run(_poll_team("japan"))
    assert len(main._rolling["japan"]) == 3


# --- Notify wiring (gate -> LLM/template -> send_notification) --------------


def test_gate_fires_sends_notification():
    # First poll: 500bp (cold start, previous=None -> no notify).
    # Second poll: 650bp (delta 0.30 >= 0.20 -> gate fires -> notify).
    _reset_rolling()
    _reset_notified()
    m1, m2 = _market_with_prob(500), _market_with_prob(650)
    with (
        patch.object(main, "_get_cached_or_fetch", new_callable=AsyncMock) as mf,
        patch.object(main, "generate_narrative", new_callable=AsyncMock) as ml,
        patch.object(main, "send_notification") as ms,
    ):
        mf.side_effect = [(m1, None), (m2, None)]
        ml.return_value = "Japan surged to 6.5%."
        asyncio.run(_poll_team("japan"))
        asyncio.run(_poll_team("japan"))
    ms.assert_called_once()
    # Title includes the team name; body is the LLM narrative.
    args = ms.call_args.args
    assert "Japan" in args[0]
    assert "6.5%" in args[1]


def test_gate_below_threshold_no_notification():
    # First poll: 500bp. Second poll: 550bp (delta 0.10 < 0.20 -> no notify).
    _reset_rolling()
    _reset_notified()
    m1, m2 = _market_with_prob(500), _market_with_prob(550)
    with (
        patch.object(main, "_get_cached_or_fetch", new_callable=AsyncMock) as mf,
        patch.object(main, "generate_narrative", new_callable=AsyncMock) as ml,
        patch.object(main, "send_notification") as ms,
    ):
        mf.side_effect = [(m1, None), (m2, None)]
        ml.return_value = "Japan nudged up."
        asyncio.run(_poll_team("japan"))
        asyncio.run(_poll_team("japan"))
    ms.assert_not_called()


def test_cooldown_suppresses_repeat():
    # Three polls: 500 (cold), 650 (fires), 800 (would fire but within cooldown).
    # COOLDOWN_SECONDS=300; test runs in <1s -> 3rd call is suppressed.
    _reset_rolling()
    _reset_notified()
    m1, m2, m3 = _market_with_prob(500), _market_with_prob(650), _market_with_prob(800)
    with (
        patch.object(main, "_get_cached_or_fetch", new_callable=AsyncMock) as mf,
        patch.object(main, "generate_narrative", new_callable=AsyncMock) as ml,
        patch.object(main, "send_notification") as ms,
    ):
        mf.side_effect = [(m1, None), (m2, None), (m3, None)]
        ml.return_value = "Japan moved."
        asyncio.run(_poll_team("japan"))
        asyncio.run(_poll_team("japan"))
        asyncio.run(_poll_team("japan"))
    ms.assert_called_once()  # 2nd poll fires; 3rd suppressed by cooldown


def test_llm_fails_uses_template_fallback():
    # D17: generate_narrative raises -> template_narrative -> still notifies.
    _reset_rolling()
    _reset_notified()
    m1, m2 = _market_with_prob(500), _market_with_prob(650)
    with (
        patch.object(main, "_get_cached_or_fetch", new_callable=AsyncMock) as mf,
        patch.object(main, "generate_narrative", new_callable=AsyncMock) as ml,
        patch.object(main, "send_notification") as ms,
    ):
        mf.side_effect = [(m1, None), (m2, None)]
        ml.side_effect = RuntimeError("OpenRouter down")
        asyncio.run(_poll_team("japan"))
        asyncio.run(_poll_team("japan"))
    ms.assert_called_once()
    # Template narrative contains "current probability" (D30 format).
    assert "current probability" in ms.call_args.args[1]
