"""End-to-end demo pipeline: gate -> LLM/template -> notification.

Drives _poll_team directly (not the infinite _watcher_loop) through the
synthetic flat-then-jump series. The deterministic gate must stay silent for
the flat polls and fire exactly once at the 5000bp -> 6500bp jump, after which
the (mocked) LLM narrative runs through the real verify_narrative guard and
send_notification is called once. use_cache=False mirrors the demo CLI: without
it the first sample is cached for CACHE_TTL_SECONDS and the jump is never seen.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import main
from watcher.core import _poll_team, reset_demo_state
from watcher.demo import SyntheticFetcher


def test_demo_mode_fires_once_at_jump():
    reset_demo_state()
    fetcher = SyntheticFetcher(jump_after_polls=3)
    with (
        patch("watcher.core.send_notification") as sn,
        patch.object(main, "generate_narrative", new_callable=AsyncMock) as gn,
    ):
        gn.return_value = "Demo moved."
        # Poll 1: 5000bp, cold start (previous_bp=None) -> gate=no
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
        # Poll 2: 5000bp, previous=5000 -> |0| < 0.2*5000 -> gate=no
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
        # Poll 3: 6500bp, previous=5000 -> |1500| >= 1000 -> gate=fires -> notify
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
