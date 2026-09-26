from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from .pool_market_learning import (
    POOL_MARKET_FEATURE_COLUMNS,
    POOL_MARKET_TARGET_COLUMNS,
)
from .pool_market_training import _prepare_frame


@dataclass(frozen=True)
class PoolMarketWalkForwardTarget:
    target: str
    model_mae: float
    baseline_mae: float
    mae_improvement: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PoolMarketWalkForwardFold:
    fold_index: int
    validation_start: str
    validation_end: str
    train_rows: int
    purged_rows: int
    validation_rows: int
    pools_in_train: int
    pools_in_validation: int
    targets: tuple[PoolMarketWalkForwardTarget, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PoolMarketWalkForwardAggregate:
    target: str
    folds: int
    mean_model_mae: float
    mean_baseline_mae: float
    mean_mae_improvement: float
    model_better_folds: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PoolMarketWalkForwardReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    decision_times: int
    folds: tuple[PoolMarketWalkForwardFold, ...]
    aggregates: tuple[PoolMarketWalkForwardAggregate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_pool_market_walk_forward(
    frame: pd.DataFrame,
    *,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> PoolMarketWalkForwardReport:
    """
    Evaluate continuous market-outcome prediction with expanding time windows.

    Training examples are purged whenever their forward label window reaches
    into the validation period. Every fold is descriptive only: no economic
    pass/fail threshold, rank, allocation or execution authorization is
    produced.
    """
    for name, value in (
        ("min_train_decision_times", min_train_decision_times),
        ("validation_decision_times", validation_decision_times),
        ("step_decision_times", step_decision_times),
        ("min_train_rows", min_train_rows),
    ):
        if value < 1:
            raise ValueError(f"{name} must be positive")

    prepared = _prepare_frame(frame)
    times = (
        prepared["decision_observed_at"]
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    if len(times) < (
        min_train_decision_times + validation_decision_times
    ):
        raise ValueError(
            "not enough unique decision timestamps for walk-forward evaluation"
        )

    folds: list[PoolMarketWalkForwardFold] = []
    start = min_train_decision_times
    fold_index = 0

    while start + validation_decision_times <= len(times):
        validation_times = times[
            start : start + validation_decision_times
        ]
        validation_start = pd.Timestamp(validation_times[0])
        validation_end = pd.Timestamp(validation_times[-1])

        prior = prepared[
            prepared["decision_observed_at"] < validation_start
        ].copy()
        train = prior[
            prior["forward_end_observed_at"] < validation_start
        ].copy()
        purged_rows = len(prior) - len(train)
        valid = prepared[
            (prepared["decision_observed_at"] >= validation_start)
            & (prepared["decision_observed_at"] <= validation_end)
        ].copy()

        if len(train) < min_train_rows:
            raise ValueError(
                f"fold {fold_index + 1} has only {len(train)} "
                f"purged training rows; need {min_train_rows}"
            )
        if valid.empty:
            raise ValueError(
                f"fold {fold_index + 1} has no validation rows"
            )

        x_train = train[list(POOL_MARKET_FEATURE_COLUMNS)]
        x_valid = valid[list(POOL_MARKET_FEATURE_COLUMNS)]
        target_results: list[PoolMarketWalkForwardTarget] = []

        for target_index, target in enumerate(
            POOL_MARKET_TARGET_COLUMNS
        ):
            y_train = train[target].to_numpy(dtype=float)
            y_valid = valid[target].to_numpy(dtype=float)

            model = HistGradientBoostingRegressor(
                random_state=500
                + fold_index * len(POOL_MARKET_TARGET_COLUMNS)
                + target_index,
            )
            model.fit(x_train, y_train)
            prediction = model.predict(x_valid)

            baseline_value = float(np.median(y_train))
            baseline_prediction = np.full(
                len(y_valid),
                baseline_value,
                dtype=float,
            )

            model_mae = float(
                mean_absolute_error(y_valid, prediction)
            )
            baseline_mae = float(
                mean_absolute_error(
                    y_valid,
                    baseline_prediction,
                )
            )
            target_results.append(
                PoolMarketWalkForwardTarget(
                    target=target,
                    model_mae=model_mae,
                    baseline_mae=baseline_mae,
                    mae_improvement=baseline_mae - model_mae,
                )
            )

        fold_index += 1
        folds.append(
            PoolMarketWalkForwardFold(
                fold_index=fold_index,
                validation_start=validation_start.isoformat(),
                validation_end=validation_end.isoformat(),
                train_rows=len(train),
                purged_rows=purged_rows,
                validation_rows=len(valid),
                pools_in_train=int(
                    train["pool_address"].nunique()
                ),
                pools_in_validation=int(
                    valid["pool_address"].nunique()
                ),
                targets=tuple(target_results),
            )
        )
        start += step_decision_times

    if not folds:
        raise ValueError(
            "walk-forward configuration produced no folds"
        )

    aggregates: list[PoolMarketWalkForwardAggregate] = []
    for target in POOL_MARKET_TARGET_COLUMNS:
        results = [
            next(
                item
                for item in fold.targets
                if item.target == target
            )
            for fold in folds
        ]
        aggregates.append(
            PoolMarketWalkForwardAggregate(
                target=target,
                folds=len(results),
                mean_model_mae=float(
                    fmean(item.model_mae for item in results)
                ),
                mean_baseline_mae=float(
                    fmean(item.baseline_mae for item in results)
                ),
                mean_mae_improvement=float(
                    fmean(
                        item.mae_improvement
                        for item in results
                    )
                ),
                model_better_folds=sum(
                    item.mae_improvement > 0
                    for item in results
                ),
            )
        )

    return PoolMarketWalkForwardReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        decision_times=len(times),
        folds=tuple(folds),
        aggregates=tuple(aggregates),
    )
