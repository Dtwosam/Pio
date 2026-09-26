from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from .pool_lp_learning import (
    ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    ENRICHED_LP_FEATURE_COLUMNS,
)


ENRICHED_LP_MODEL_TARGET_COLUMNS = (
    *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    "target_downside_bps",
)


@dataclass(frozen=True)
class EnrichedLPTargetMetrics:
    target: str
    model_mae: float
    baseline_mae: float
    validation_mean: float
    validation_std: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EnrichedLPPurgedSplit:
    train_rows: int
    purged_rows: int
    validation_rows: int
    train_decision_start: str
    train_decision_end: str
    train_forward_end: str
    validation_decision_start: str
    validation_decision_end: str


@dataclass
class EnrichedLPModelBundle:
    feature_columns: tuple[str, ...]
    target_columns: tuple[str, ...]
    models: dict[str, HistGradientBoostingRegressor]
    split: EnrichedLPPurgedSplit
    metrics: tuple[EnrichedLPTargetMetrics, ...]
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


def prepare_enriched_lp_training_frame(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    required = {
        "pool_address",
        "decision_observed_at",
        "forward_end_observed_at",
        *ENRICHED_LP_FEATURE_COLUMNS,
        *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"missing enriched LP training columns: {missing}"
        )

    work = frame.copy()
    for column in (
        "decision_observed_at",
        "forward_end_observed_at",
    ):
        work[column] = pd.to_datetime(
            work[column],
            utc=True,
            errors="coerce",
        )
        if work[column].isna().any():
            raise ValueError(
                f"{column} contains invalid timestamps"
            )

    if (
        work["forward_end_observed_at"]
        <= work["decision_observed_at"]
    ).any():
        raise ValueError(
            "forward_end_observed_at must be after decision_observed_at"
        )

    numeric = (
        *ENRICHED_LP_FEATURE_COLUMNS,
        *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    )
    for column in numeric:
        work[column] = pd.to_numeric(
            work[column],
            errors="coerce",
        )
    if work[list(numeric)].isna().any(axis=None):
        raise ValueError(
            "enriched LP training frame contains missing numeric values"
        )
    if not np.isfinite(
        work[list(numeric)].to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "enriched LP training frame contains non-finite values"
        )

    work["target_downside_bps"] = np.maximum(
        0.0,
        -work["target_net_return_bps"].to_numpy(dtype=float),
    )

    return work.sort_values(
        ["decision_observed_at", "pool_address"],
        kind="stable",
    ).reset_index(drop=True)


def purged_enriched_lp_split(
    frame: pd.DataFrame,
    *,
    split_fraction: float = 0.8,
) -> tuple[pd.DataFrame, pd.DataFrame, EnrichedLPPurgedSplit]:
    if not 0.5 <= split_fraction < 1.0:
        raise ValueError(
            "split_fraction must be between 0.5 and 1.0"
        )

    work = prepare_enriched_lp_training_frame(frame)
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

    prior = work[
        work["decision_observed_at"] < validation_start
    ]
    train = prior[
        prior["forward_end_observed_at"] < validation_start
    ].copy()
    purged_rows = len(prior) - len(train)
    valid = work[
        work["decision_observed_at"] >= validation_start
    ].copy()

    if train.empty or valid.empty:
        raise ValueError(
            "purged enriched LP split produced an empty train or "
            "validation set"
        )

    split = EnrichedLPPurgedSplit(
        train_rows=len(train),
        purged_rows=purged_rows,
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


def train_enriched_lp_models(
    frame: pd.DataFrame,
    *,
    split_fraction: float = 0.8,
    min_train_rows: int = 50,
    min_validation_rows: int = 10,
) -> EnrichedLPModelBundle:
    """
    Train research-only continuous LP outcome models using LP + market context.

    No economic acceptance threshold, pool ranking, capital allocation or
    execution action is produced here.
    """
    if min_train_rows < 1 or min_validation_rows < 1:
        raise ValueError(
            "minimum row requirements must be positive"
        )

    prepared = prepare_enriched_lp_training_frame(frame)
    train, valid, split = purged_enriched_lp_split(
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

    features = list(ENRICHED_LP_FEATURE_COLUMNS)
    x_train = train[features]
    x_valid = valid[features]

    models: dict[str, HistGradientBoostingRegressor] = {}
    metrics: list[EnrichedLPTargetMetrics] = []

    for index, target in enumerate(
        ENRICHED_LP_MODEL_TARGET_COLUMNS
    ):
        y_train = train[target].to_numpy(dtype=float)
        y_valid = valid[target].to_numpy(dtype=float)

        model = HistGradientBoostingRegressor(
            random_state=800 + index,
        )
        model.fit(x_train, y_train)
        prediction = model.predict(x_valid)

        baseline_value = float(np.median(y_train))
        baseline_prediction = np.full(
            len(y_valid),
            baseline_value,
            dtype=float,
        )

        models[target] = model
        metrics.append(
            EnrichedLPTargetMetrics(
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

    return EnrichedLPModelBundle(
        feature_columns=ENRICHED_LP_FEATURE_COLUMNS,
        target_columns=ENRICHED_LP_MODEL_TARGET_COLUMNS,
        models=models,
        split=split,
        metrics=tuple(metrics),
    )


def predict_enriched_lp_outcomes(
    bundle: EnrichedLPModelBundle,
    frame: pd.DataFrame,
) -> pd.DataFrame:
    missing = sorted(
        set(bundle.feature_columns) - set(frame.columns)
    )
    if missing:
        raise ValueError(
            f"missing enriched LP prediction features: {missing}"
        )

    features = frame[list(bundle.feature_columns)].copy()
    for column in bundle.feature_columns:
        features[column] = pd.to_numeric(
            features[column],
            errors="coerce",
        )
    if features.isna().any(axis=None):
        raise ValueError(
            "enriched LP prediction features contain missing values"
        )
    if not np.isfinite(
        features.to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "enriched LP prediction features contain non-finite values"
        )

    output = pd.DataFrame(index=frame.index)
    for target in bundle.target_columns:
        output[f"predicted_{target}"] = bundle.models[
            target
        ].predict(features)
    return output
