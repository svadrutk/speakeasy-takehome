from __future__ import annotations


class SyntheticFetcher:
    """Synthetic market source for demo: flat at 5000bp, then jump to 6500bp on cue."""

    def __init__(self, jump_after_polls: int = 3):
        self._count = 0
        self._jump_after = jump_after_polls

    async def __call__(self, team_key: str) -> dict | None:
        self._count += 1
        jump = self._count >= self._jump_after
        prob_bp = 6500 if jump else 5000
        market = {
            "ticker": "KXWCGAME-26JUN25DEMO-DEM",
            "title": "Demo vs Opponent Winner?",
            "yes_sub_title": "Demo",
            "status": "active",
            "last_price_dollars": f"{prob_bp / 10000:.4f}",
            "volume_fp": "1000000",
        }
        return market
