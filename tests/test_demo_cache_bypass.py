"""Regression: demo mode must bypass the fetch cache so the synthetic jump is seen.

Without use_cache=False, _get_cached_or_fetch returns the stale first sample for
CACHE_TTL_SECONDS (30s) and the gate never fires within the demo window.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import main
from watcher.core import _poll_team, _cache, _rolling, _last_notified
from watcher.demo import SyntheticFetcher


def test_demo_bypasses_cache_and_fires_at_jump():
    _rolling.clear()
    _last_notified.clear()
    _cache.clear()
    fetcher = SyntheticFetcher(jump_after_polls=2)
    with (
        patch("watcher.core.send_notification") as sn,
        patch.object(main, "generate_narrative", new_callable=AsyncMock) as gn,
    ):
        gn.return_value = "Demo moved."
        # Poll 1: 5000bp (cold start, previous=None -> no notify)
        asyncio.run(
            _poll_team(
                "demo",
                fetcher=fetcher,
                extract_fields=main.extract_market_fields,
                generate_narrative=main.generate_narrative,
                verify_narrative=main.verify_narrative,
                template_narrative=main.template_narrative,
                use_cache=False,
            )
        )
        # Poll 2: 6500bp (jump -> gate fires -> notify)
        asyncio.run(
            _poll_team(
                "demo",
                fetcher=fetcher,
                extract_fields=main.extract_market_fields,
                generate_narrative=main.generate_narrative,
                verify_narrative=main.verify_narrative,
                template_narrative=main.template_narrative,
                use_cache=False,
            )
        )
    sn.assert_called_once()
    # If cache were NOT bypassed, poll 2 would return the cached 5000bp and sn would NOT be called.


def test_demo_with_cache_does_not_fire_within_window():
    """Sanity check proving the bug: with use_cache=True (default), the cached
    first sample masks the jump and the gate does not fire on poll 2."""
    _rolling.clear()
    _last_notified.clear()
    _cache.clear()
    fetcher = SyntheticFetcher(jump_after_polls=2)
    with (
        patch("watcher.core.send_notification") as sn,
        patch.object(main, "generate_narrative", new_callable=AsyncMock) as gn,
    ):
        gn.return_value = "Demo moved."
        asyncio.run(
            _poll_team(
                "demo",
                fetcher=fetcher,
                extract_fields=main.extract_market_fields,
                generate_narrative=main.generate_narrative,
                verify_narrative=main.verify_narrative,
                template_narrative=main.template_narrative,
                use_cache=True,
            )
        )
        asyncio.run(
            _poll_team(
                "demo",
                fetcher=fetcher,
                extract_fields=main.extract_market_fields,
                generate_narrative=main.generate_narrative,
                verify_narrative=main.verify_narrative,
                template_narrative=main.template_narrative,
                use_cache=True,
            )
        )
    sn.assert_not_called()
