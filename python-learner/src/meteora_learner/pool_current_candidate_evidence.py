from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import pandas as pd

from .pool_api_metadata_context import (
    POOL_API_METADATA_FEATURE_COLUMNS,
    attach_pool_api_metadata_from_store,
)
from .pool_chain_context import (
    POOL_CHAIN_CONTEXT_FEATURE_COLUMNS,
    attach_pool_chain_context_from_store,
)
from .pool_execution_cost_context import (
    POOL_EXECUTION_COST_FEATURE_COLUMNS,
    attach_execution_cost_context_from_store,
)
from .pool_market_current_forecast import (
    CurrentPoolMarketForecastReport,
    build_current_pool_market_forecast_from_store,
)
from .pool_token_mint_context import (
    TOKEN_MINT_CONTEXT_FEATURE_COLUMNS,
    attach_token_mint_context_from_store,
)


@dataclass(frozen=True)
class CurrentPoolCandidateEvidence:
    pool_address: str
    universe_reference_time: str
    market_observed_at: str
    market_snapshot_lag_seconds: float
    current_price: float
    current_tvl_usd: float
    current_volume_24h_usd: float
    current_fees_24h_usd: float
    market_history_observations: int
    market_forecasts: tuple[tuple[str, float], ...]
    api_context_available: bool
    mint_context_available: bool
    chain_context_available: bool
    execution_context_available: bool
    complete_context_groups: int
    api_context: tuple[tuple[str, float | None], ...]
    mint_context: tuple[tuple[str, float | None], ...]
    chain_context: tuple[tuple[str, float | None], ...]
    execution_context: tuple[tuple[str, float | None], ...]

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        for key in (
            "market_forecasts",
            "api_context",
            "mint_context",
            "chain_context",
            "execution_context",
        ):
            record[key] = dict(record[key])
        return record


@dataclass(frozen=True)
class CurrentPoolCandidateEvidenceReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    universe_reference_time: str
    pools_seen: int
    pools_with_api_context: int
    pools_with_mint_context: int
    pools_with_chain_context: int
    pools_with_execution_context: int
    pools_with_all_context_groups: int
    source_forecast: CurrentPoolMarketForecastReport
    candidates: tuple[CurrentPoolCandidateEvidence, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "research_only": self.research_only,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
            "universe_reference_time": self.universe_reference_time,
            "pools_seen": self.pools_seen,
            "pools_with_api_context": self.pools_with_api_context,
            "pools_with_mint_context": self.pools_with_mint_context,
            "pools_with_chain_context": self.pools_with_chain_context,
            "pools_with_execution_context": (
                self.pools_with_execution_context
            ),
            "pools_with_all_context_groups": (
                self.pools_with_all_context_groups
            ),
            "source_forecast": self.source_forecast.to_record(),
            "candidates": [
                item.to_record() for item in self.candidates
            ],
        }


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _context_pairs(
    row: pd.Series,
    columns: tuple[str, ...],
) -> tuple[tuple[str, float | None], ...]:
    return tuple(
        (column, _finite_or_none(row.get(column)))
        for column in columns
    )


def _available(
    row: pd.Series,
    required_indicator: str,
) -> bool:
    return _finite_or_none(row.get(required_indicator)) is not None


def build_current_pool_candidate_evidence(
    database_path: str,
    *,
    as_of: str | None = None,
    horizon_rows: int = 6,
    volatility_window: int = 6,
    drawdown_window: int = 12,
    activity_window: int = 6,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> CurrentPoolCandidateEvidenceReport:
    """
    Consolidate current research evidence for every forecastable pool.

    This is an evidence surface, not a selector. Candidate order is stable by
    pool address and no score, rank, winner, allocation, threshold or action is
    produced.
    """
    forecast = build_current_pool_market_forecast_from_store(
        database_path,
        as_of=as_of,
        horizon_rows=horizon_rows,
        volatility_window=volatility_window,
        drawdown_window=drawdown_window,
        activity_window=activity_window,
        min_train_decision_times=min_train_decision_times,
        validation_decision_times=validation_decision_times,
        step_decision_times=step_decision_times,
        min_train_rows=min_train_rows,
    )

    reference_time = forecast.universe_reference_time
    rows = []
    forecast_by_pool = {}
    for item in forecast.forecasts:
        forecast_by_pool[item.pool_address] = item
        rows.append(
            {
                "pool_address": item.pool_address,
                "decision_observed_at": reference_time,
            }
        )
    if not rows:
        raise ValueError(
            "current pool forecast contains no candidate pools"
        )

    frame = pd.DataFrame(rows)
    frame, _ = attach_pool_api_metadata_from_store(
        database_path,
        frame,
    )
    frame, _ = attach_token_mint_context_from_store(
        database_path,
        frame,
    )
    frame, _ = attach_pool_chain_context_from_store(
        database_path,
        frame,
    )
    frame, _ = attach_execution_cost_context_from_store(
        database_path,
        frame,
    )
    frame = frame.sort_values("pool_address").reset_index(drop=True)

    candidates: list[CurrentPoolCandidateEvidence] = []
    api_count = 0
    mint_count = 0
    chain_count = 0
    execution_count = 0
    all_count = 0

    for _, row in frame.iterrows():
        pool = str(row["pool_address"])
        item = forecast_by_pool[pool]

        api_available = _available(
            row,
            "api_snapshot_age_seconds",
        )
        mint_available = _available(
            row,
            "mint_chain_snapshot_age_seconds",
        ) and _available(
            row,
            "mint_x_snapshot_age_seconds",
        ) and _available(
            row,
            "mint_y_snapshot_age_seconds",
        )
        chain_available = _available(
            row,
            "chain_snapshot_age_seconds",
        )
        execution_available = _available(
            row,
            "execution_history_samples",
        )

        api_count += int(api_available)
        mint_count += int(mint_available)
        chain_count += int(chain_available)
        execution_count += int(execution_available)

        complete_groups = sum(
            (
                api_available,
                mint_available,
                chain_available,
                execution_available,
            )
        )
        all_count += int(complete_groups == 4)

        candidates.append(
            CurrentPoolCandidateEvidence(
                pool_address=pool,
                universe_reference_time=reference_time,
                market_observed_at=item.observed_at,
                market_snapshot_lag_seconds=(
                    item.snapshot_lag_seconds
                ),
                current_price=item.price,
                current_tvl_usd=item.tvl_usd,
                current_volume_24h_usd=item.volume_24h_usd,
                current_fees_24h_usd=item.fees_24h_usd,
                market_history_observations=(
                    item.history_observations
                ),
                market_forecasts=item.predictions,
                api_context_available=api_available,
                mint_context_available=mint_available,
                chain_context_available=chain_available,
                execution_context_available=execution_available,
                complete_context_groups=complete_groups,
                api_context=_context_pairs(
                    row,
                    POOL_API_METADATA_FEATURE_COLUMNS,
                ),
                mint_context=_context_pairs(
                    row,
                    TOKEN_MINT_CONTEXT_FEATURE_COLUMNS,
                ),
                chain_context=_context_pairs(
                    row,
                    POOL_CHAIN_CONTEXT_FEATURE_COLUMNS,
                ),
                execution_context=_context_pairs(
                    row,
                    POOL_EXECUTION_COST_FEATURE_COLUMNS,
                ),
            )
        )

    return CurrentPoolCandidateEvidenceReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        universe_reference_time=reference_time,
        pools_seen=len(candidates),
        pools_with_api_context=api_count,
        pools_with_mint_context=mint_count,
        pools_with_chain_context=chain_count,
        pools_with_execution_context=execution_count,
        pools_with_all_context_groups=all_count,
        source_forecast=forecast,
        candidates=tuple(candidates),
    )
