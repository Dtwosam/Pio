from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from .pool_market_learning import (
    POOL_MARKET_FEATURE_COLUMNS,
    POOL_MARKET_TARGET_COLUMNS,
)


@dataclass(frozen=True)
class PoolMarketTargetMetrics:
    target: str
    model_mae: float
    baseline_mae: float
    validation_mean: float
    validation_std: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PurgedPoolMarketSplit:
    train_rows: int
    purged_rows: int
    validation_rows: int
    train_decision_start: str
    train_decision_end: str
    train_forward_end: str
    validation_decision_start: str
    validation_decision_end: str


@dataclass
class PoolMarketModelBundle:
    feature_columns: tuple[str, ...]
    target_columns: tuple[str, ...]
    models: dict[str, HistGradientBoostingRegressor]
    split: PurgedPoolMarketSplit
    metrics: tuple[PoolMarketTargetMetrics, ...]
    research_only: bool = True
    policy_actionable: bool = False
    execution_wired: bool = False

    def metadata(self) -> dict[str, Any]:
        return {
            "feature_columns": list(self.feature_columns),
            "target_columns": list(self.target_columns),
            "split": asdict(self.split),
            "metrics": [item.to_record() for item in self.metrics],
            "research_only": self.research_only,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
        }


def _prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    required = {
        "pool_address",
        "decision_observed_at",
        "forward_end_observed_at",
        *POOL_MARKET_FEATURE_COLUMNS,
        *POOL_MARKET_TARGET_COLUMNS,
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(
            f"missing pool-market training columns: {missing}"
        )

    frame = df.copy()
    for column in ("decision_observed_at", "forward_end_observed_at"):
        frame[column] = pd.to_datetime(
            frame[column],
            utc=True,
            errors="coerce",
        )
        if frame[column].isna().any():
            raise ValueError(f"{column} contains invalid timestamps")

    if (
        frame["forward_end_observed_at"]
        <= frame["decision_observed_at"]
    ).any():
        raise ValueError(
            "forward_end_observed_at must be after decision_observed_at"
        )

    numeric = (
        *POOL_MARKET_FEATURE_COLUMNS,
        *POOL_MARKET_TARGET_COLUMNS,
    )
    for column in numeric:
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        )

    if frame[list(numeric)].isna().any(axis=None):
        raise ValueError(
            "pool-market training frame contains missing numeric values"
        )

    finite = np.isfinite(
        frame[list(numeric)].to_numpy(dtype=float)
    )
    if not finite.all():
        raise ValueError(
            "pool-market training frame contains non-finite values"
        )

    return frame.sort_values(
        ["decision_observed_at", "pool_address"],
        kind="stable",
    ).reset_index(drop=True)


def purged_timestamp_split(
    frame: pd.DataFrame,
    *,
    split_fraction: float = 0.8,
) -> tuple[pd.DataFrame, pd.DataFrame, PurgedPoolMarketSplit]:
    """
    Split by decision time and purge training examples whose forward labels
    overlap the validation period.

    A row belongs to training only when both its decision time and its complete
    forward outcome window end strictly before validation begins.
    """
    if not 0.5 <= split_fraction < 1.0:
        raise ValueError(
            "split_fraction must be between 0.5 and 1.0"
        )

    work = _prepare_frame(frame)
    times = (
        work["decision_observed_at"]
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    if len(times) < 2:
        raise ValueError(
            "need at least two unique decision timestamps"
        )

    cut = max(
        1,
        min(
            len(times) - 1,
            int(len(times) * split_fraction),
        ),
    )
    validation_start = pd.Timestamp(times[cut])

    before_validation = (
        work["decision_observed_at"] < validation_start
    )
    complete_before_validation = (
        work["forward_end_observed_at"] < validation_start
    )

    train = work[
        before_validation & complete_before_validation
    ].copy()
    purged = work[
        before_validation & ~complete_before_validation
    ].copy()
    valid = work[
        work["decision_observed_at"] >= validation_start
    ].copy()

    if train.empty or valid.empty:
        raise ValueError(
            "purged timestamp split produced an empty train or validation set"
        )

    split = PurgedPoolMarketSplit(
        train_rows=len(train),
        purged_rows=len(purged),
        validation_rows=len(valid),
        train_decision_start=(
            train["decision_observed_at"].iloc[0].isoformat()
        ),
        train_decision_end=(
            train["decision_observed_at"].iloc[-1].isoformat()
        ),
        train_forward_end=(
            train["forward_end_observed_at"].max().isoformat()
        ),
        validation_decision_start=validation_start.isoformat(),
        validation_decision_end=(
            valid["decision_observed_at"].iloc[-1].isoformat()
        ),
    )
    return train, valid, split


def train_pool_market_models(
    frame: pd.DataFrame,
    *,
    split_fraction: float = 0.8,
    min_train_rows: int = 50,
    min_validation_rows: int = 10,
) -> PoolMarketModelBundle:
    """
    Train research-only regressors for continuous future market outcomes.

    This function deliberately does not:
    - label pools safe/unsafe;
    - produce a hand-written risk score;
    - choose a capital allocation;
    - define an economic acceptance threshold;
    - authorize or wire execution.

    Validation reports predictive error against a simple train-median baseline.
    """
    if min_train_rows < 1 or min_validation_rows < 1:
        raise ValueError(
            "minimum row requirements must be positive"
        )

    prepared = _prepare_frame(frame)
    train, valid, split = purged_timestamp_split(
        prepared,
        split_fraction=split_fraction,
    )

    if len(train) < min_train_rows:
        raise ValueError(
            f"need at least {min_train_rows} purged training rows; "
            f"received {len(train)}"
        )
    if len(valid) < min_validation_rows:
        raise ValueError(
            f"need at least {min_validation_rows} validation rows; "
            f"received {len(valid)}"
        )

    features = list(POOL_MARKET_FEATURE_COLUMNS)
    x_train = train[features]
    x_valid = valid[features]

    models: dict[str, HistGradientBoostingRegressor] = {}
    metrics: list[PoolMarketTargetMetrics] = []

    for index, target in enumerate(POOL_MARKET_TARGET_COLUMNS):
        y_train = train[target].to_numpy(dtype=float)
        y_valid = valid[target].to_numpy(dtype=float)

        model = HistGradientBoostingRegressor(
            random_state=100 + index,
        )
        model.fit(x_train, y_train)
        prediction = model.predict(x_valid)

        baseline_value = float(np.median(y_train))
        baseline_prediction = np.full(
            shape=len(y_valid),
            fill_value=baseline_value,
            dtype=float,
        )

        models[target] = model
        metrics.append(
            PoolMarketTargetMetrics(
                target=target,
                model_mae=float(
                    mean_absolute_error(y_valid, prediction)
                ),
                baseline_mae=float(
                    mean_absolute_error(
                        y_valid,
                        baseline_prediction,
                    )
                ),
                validation_mean=float(np.mean(y_valid)),
                validation_std=float(np.std(y_valid)),
            )
        )

    return PoolMarketModelBundle(
        feature_columns=POOL_MARKET_FEATURE_COLUMNS,
        target_columns=POOL_MARKET_TARGET_COLUMNS,
        models=models,
        split=split,
        metrics=tuple(metrics),
    )


def predict_pool_market_outcomes(
    bundle: PoolMarketModelBundle,
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """
    Return continuous model estimates for research inspection.

    Output is descriptive only and carries no pool rank, action, sizing or
    authorization field.
    """
    missing = sorted(
        set(bundle.feature_columns) - set(frame.columns)
    )
    if missing:
        raise ValueError(
            f"missing pool-market prediction features: {missing}"
        )

    features = frame[list(bundle.feature_columns)].copy()
    for column in bundle.feature_columns:
        features[column] = pd.to_numeric(
            features[column],
            errors="coerce",
        )
    if features.isna().any(axis=None):
        raise ValueError(
            "pool-market prediction features contain missing values"
        )
    if not np.isfinite(
        features.to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "pool-market prediction features contain non-finite values"
        )

    output = pd.DataFrame(index=frame.index)
    for target in bundle.target_columns:
        output[f"predicted_{target}"] = bundle.models[
            target
        ].predict(features)
    return output
