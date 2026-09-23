from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ExecutionMode(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class ExecutionAction(str, Enum):
    SKIP = "SKIP"
    ENTER = "ENTER"
    REBALANCE = "REBALANCE"
    EXIT = "EXIT"


class TradeProposal(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    decision_id: UUID
    mode: ExecutionMode
    action: ExecutionAction
    pool_address: str
    capital_quote: float
    account_equity_quote: float
    portfolio_deployed_quote: float
    daily_drawdown_pct: float
    min_bin_id: int
    max_bin_id: int
    strategy: str
    expected_net_return_pct: float
    expected_downside_pct: float
    model_version: str
    data_age_seconds: int

    def to_record(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ExecutionRiskConfig(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    max_capital_per_position_pct: float
    max_total_deployed_pct: float
    max_daily_drawdown_pct: float
    min_expected_edge_pct: float
    max_expected_downside_pct: float
    max_data_age_seconds: int

    def to_record(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
