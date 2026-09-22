from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class Mode(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class Action(str, Enum):
    SKIP = "SKIP"
    ENTER = "ENTER"
    REBALANCE = "REBALANCE"
    EXIT = "EXIT"


class Candidate(BaseModel):
    pool_address: str
    capital_quote: float = Field(gt=0)
    min_bin_id: int
    max_bin_id: int
    strategy: str = "SPOT"
    fee_yield_24h: float = 0.0
    volatility_24h: float = 0.0
    volume_acceleration: float = 0.0
    liquidity_score: float = 0.0
    estimated_cost_pct: float = 0.0


class ScoredCandidate(Candidate):
    expected_net_return: float
    expected_downside: float
    score: float
    model_version: str
