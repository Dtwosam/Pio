from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .pool_market_learning import (
    POOL_MARKET_FEATURE_COLUMNS,
    POOL_MARKET_TARGET_COLUMNS,
    build_pool_market_feature_history,
    build_pool_market_learning_dataset,
    load_pool_market_history,
)
from .pool_market_walk_forward import (
    PoolMarketWalkForwardReport,
    evaluate_pool_market_walk_forward,
)


@dataclass(frozen=True)
class CurrentPoolMarketForecast:
    pool_address: str
    observed_at: str
    snapshot_lag_seconds: float
    price: float
    tvl_usd: float
    volume_24h_usd: float
    fees_24h_usd: float
    history_observations: int
    predictions: tuple[tuple[str, float], ...]

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["predictions"] = {
            key: value for key, value in self.predictions
        }
        return record


@dataclass(frozen=True)
class CurrentPoolMarketForecastReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    target_horizon_rows: int
    universe_reference_time: str
    training_rows: int
    training_pools: int
    training_forward_end_max: str
    latest_pools_seen: int
    pools_forecast: int
    pools_dropped_incomplete_features: int
    walk_forward: PoolMarketWalkForwardReport
    forecasts: tuple[CurrentPoolMarketForecast, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "research_only": self.research_only,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
            "target_horizon_rows": self.target_horizon_rows,
            "universe_reference_time": self.universe_reference_time,
            "training_rows": self.training_rows,
            "training_pools": self.training_pools,
            "training_forward_end_max": self.training_forward_end_max,
            "latest_pools_seen": self.latest_pools_seen,
            "pools_forecast": self.pools_forecast,
            "pools_dropped_incomplete_features": (
                self.pools_dropped_incomplete_features
            ),
            "walk_forward": self.walk_forward.to_record(),
            "forecasts": [
                item.to_record() for item in self.forecasts
            ],
        }


def _fit_all_history_models(
    dataset: pd.DataFrame,
) -> dict[str, HistGradientBoostingRegressor]:
    features = dataset[list(POOL_MARKET_FEATURE_COLUMNS)]
    models: dict[str, HistGradientBoostingRegressor] = {}
    for index, target in enumerate(POOL_MARKET_TARGET_COLUMNS):
        model = HistGradientBoostingRegressor(
            random_state=10000 + index,
        )
        model.fit(
            features,
            dataset[target].to_numpy(dtype=float),
        )
        models[target] = model
    return models


def build_current_pool_market_forecast(
    history: pd.DataFrame,
    *,
    horizon_rows: int = 6,
    volatility_window: int = 6,
    drawdown_window: int = 12,
    activity_window: int = 6,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> CurrentPoolMarketForecastReport:
    """
    Forecast continuous future market outcomes for the latest observed pools.

    Historical walk-forward evidence is produced first. Final research models
    are then fit on all fully realized historical examples available in the
    supplied history and applied to each pool's latest complete decision-time
    feature row.

    The output deliberately contains no rank, selected pool, allocation,
    threshold, or execution action.
    """
    learning = build_pool_market_learning_dataset(
        history,
        horizon_rows=horizon_rows,
        volatility_window=volatility_window,
        drawdown_window=drawdown_window,
        activity_window=activity_window,
    )
    dataset = learning.to_frame()
    if dataset.empty:
        raise ValueError(
            "no fully realized market-learning examples are available"
        )

    walk_forward = evaluate_pool_market_walk_forward(
        dataset,
        min_train_decision_times=min_train_decision_times,
        validation_decision_times=validation_decision_times,
        step_decision_times=step_decision_times,
        min_train_rows=min_train_rows,
    )

    feature_history = build_pool_market_feature_history(
        history,
        volatility_window=volatility_window,
        drawdown_window=drawdown_window,
        activity_window=activity_window,
    )
    latest = (
        feature_history
        .sort_values(["pool_address", "observed_at"])
        .groupby("pool_address", sort=True, as_index=False)
        .tail(1)
        .sort_values("pool_address")
        .reset_index(drop=True)
    )
    if latest.empty:
        raise ValueError("no latest pool observations are available")

    reference_time = pd.Timestamp(
        latest["observed_at"].max()
    )
    training_forward_end_max = pd.to_datetime(
        dataset["forward_end_observed_at"],
        utc=True,
    ).max()
    if training_forward_end_max > reference_time:
        raise AssertionError(
            "training target extends beyond universe reference time"
        )

    complete_mask = latest[
        list(POOL_MARKET_FEATURE_COLUMNS)
    ].apply(
        lambda row: all(
            pd.notna(value)
            and np.isfinite(float(value))
            for value in row
        ),
        axis=1,
    )
    ready = latest.loc[complete_mask].copy()
    dropped = int((~complete_mask).sum())
    if ready.empty:
        raise ValueError(
            "no latest pool rows have complete market features"
        )

    models = _fit_all_history_models(dataset)
    x = ready[list(POOL_MARKET_FEATURE_COLUMNS)]
    predicted = {
        target: models[target].predict(x)
        for target in POOL_MARKET_TARGET_COLUMNS
    }

    forecasts: list[CurrentPoolMarketForecast] = []
    for position, (_, row) in enumerate(ready.iterrows()):
        observed = pd.Timestamp(row["observed_at"])
        forecasts.append(
            CurrentPoolMarketForecast(
                pool_address=str(row["pool_address"]),
                observed_at=observed.isoformat(),
                snapshot_lag_seconds=float(
                    (reference_time - observed).total_seconds()
                ),
                price=float(row["price"]),
                tvl_usd=float(row["tvl_usd"]),
                volume_24h_usd=float(row["volume_24h_usd"]),
                fees_24h_usd=float(row["fees_24h_usd"]),
                history_observations=int(
                    row["history_observations"]
                ),
                predictions=tuple(
                    (
                        target,
                        float(predicted[target][position]),
                    )
                    for target in POOL_MARKET_TARGET_COLUMNS
                ),
            )
        )

    return CurrentPoolMarketForecastReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        target_horizon_rows=horizon_rows,
        universe_reference_time=reference_time.isoformat(),
        training_rows=len(dataset),
        training_pools=int(dataset["pool_address"].nunique()),
        training_forward_end_max=pd.Timestamp(
            training_forward_end_max
        ).isoformat(),
        latest_pools_seen=len(latest),
        pools_forecast=len(forecasts),
        pools_dropped_incomplete_features=dropped,
        walk_forward=walk_forward,
        forecasts=tuple(forecasts),
    )


def build_current_pool_market_forecast_from_store(
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
) -> CurrentPoolMarketForecastReport:
    history = load_pool_market_history(
        database_path,
        end_observed_at=as_of,
    )
    return build_current_pool_market_forecast(
        history,
        horizon_rows=horizon_rows,
        volatility_window=volatility_window,
        drawdown_window=drawdown_window,
        activity_window=activity_window,
        min_train_decision_times=min_train_decision_times,
        validation_decision_times=validation_decision_times,
        step_decision_times=step_decision_times,
        min_train_rows=min_train_rows,
    )
