from __future__ import annotations

import argparse
import asyncio

import main  # imports narrative + transforms
from watcher.core import _watcher_loop, reset_demo_state
from watcher.demo import SyntheticFetcher


def _trace(msg: str) -> None:
    print(f"[watcher] {msg}")


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Watcher CLI")
    parser.add_argument("--demo", action="store_true", help="Run with synthetic data source")
    parser.add_argument("--jump-after", type=int, default=3, help="Demo: polls before jump")
    args = parser.parse_args()

    if args.demo:
        reset_demo_state()
        fetcher = SyntheticFetcher(jump_after_polls=args.jump_after)
        asyncio.run(
            _watcher_loop(
                fetcher=fetcher,
                extract_fields=main.extract_market_fields,
                generate_narrative=main.generate_narrative,
                verify_narrative=main.verify_narrative,
                template_narrative=main.template_narrative,
                trace=_trace,
            )
        )
    else:
        asyncio.run(
            _watcher_loop(
                fetcher=main.fetch_market_for_team,
                extract_fields=main.extract_market_fields,
                generate_narrative=main.generate_narrative,
                verify_narrative=main.verify_narrative,
                template_narrative=main.template_narrative,
                trace=None,
            )
        )


if __name__ == "__main__":
    main_cli()
