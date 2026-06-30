import asyncio

from watcher.demo import SyntheticFetcher


async def _fetch_three(fetcher):
    return [await fetcher("demo") for _ in range(3)]


def test_synthetic_fetcher_jumps_after_n():
    f = SyntheticFetcher(jump_after_polls=3)
    markets = asyncio.run(_fetch_three(f))
    # last_price_dollars: 0.5000, 0.5000, 0.6500
    prices = [float(m["last_price_dollars"]) for m in markets]
    assert prices == [0.5, 0.5, 0.65]
