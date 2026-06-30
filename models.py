"""Response schema — the developer-facing interface contract (D3).

This is what `GET /team/{team_name}` returns. Every component produces toward
this shape: transforms fill the structured fields, the LLM fills `narrative`,
the endpoint assembles them into this model.

Optional fields with None defaults encode D17 (degraded narrative): when Kalshi
is down or no market is found, the structured fields are null but `narrative`
is always present and explains what happened.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TeamSentiment(BaseModel):
    team: str = Field(..., description="The team requested, e.g. 'brazil'")
    opponent: str | None = Field(
        None, description="Opponent if a per-match market, null for tournament-winner fallback"
    )
    match_status: str | None = Field(
        None, description="'ongoing', 'scheduled', 'closed', or null for tournament-winner"
    )
    market: str | None = Field(None, description="Kalshi market ticker, null if no market found")
    current_prob: float | None = Field(
        None, description="Win probability 0-1, null if market data unavailable"
    )
    delta_1m: float | None = Field(
        None, description="Probability change over 1m, null during cold start (<1m of tracking)"
    )
    volume: int | None = Field(None, description="Total market volume, null if no market")
    liquidity: int | None = Field(
        None, description="Available liquidity in dollars, null if no market"
    )
    last_updated: str | None = Field(
        None, description="ISO timestamp of last market update, null if no market"
    )
    narrative: str = Field(
        ..., description="Human-readable sentiment. ALWAYS present, even in failure (D17)."
    )
