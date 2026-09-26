from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

import numpy as np
import pandas as pd

from .pool_lp_mint_ablation import MINT_ENRICHED_LP_FEATURE_COLUMNS
from .pool_lp_training import prepare_enriched_lp_training_frame


@dataclass(frozen=True)
class FeatureDriftMetric:
    feature: str
    train_median: float
    validation_median: float
    median_shift: float
    train_iqr: float
    validation_iqr: float
    train_lower_quantile: float
    train_upper_quantile: float
    validation_below_reference_rate: float
    validation_above_reference_rate: float
    validation_reference_escape_rate: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureDriftFold:
    fold_index: int
    validation_start: str
    validation_end: str
    train_rows: int
    purged_rows: int
    validation_rows: int
    pools_in_train: int
    pools_in_validation: int
    reference_lower_quantile: float
    reference_upper_quantile: float
    mean_reference_escape_rate: float
    max_reference_escape_rate: float
    features: tuple[FeatureDriftMetric, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureDriftAggregate:
    feature: str
    folds: int
    mean_median_shift: float
    mean_validation_reference_escape_rate: float
    max_validation_reference_escape_rate: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureDriftReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    decision_times: int
    feature_count: int
    reference_lower_quantile: float
    reference_upper_quantile: float
    folds: tuple[FeatureDriftFold, ...]
    aggregates: tuple[FeatureDriftAggregate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_feature_drift(
    frame: pd.DataFrame,
    *,
    reference_lower_quantile: float = 0.05,
    reference_upper_quantile: float = 0.95,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> FeatureDriftReport:
    """
    Describe how later LP+market+mint feature distributions differ from the
    earlier training distribution.

    The reference quantile band is descriptive. Escape rates and distribution
    shifts are reported as evidence only; this function does not define a
    "too much drift" threshold or trading action.
    """
    if not 0.0 < reference_lower_quantile < 0.5:
        raise ValueError(
            "reference_lower_quantile must be between 0 and 0.5"
        )
    if not 0.5 < reference_upper_quantile < 1.0:
        raise ValueError(
            "reference_upper_quantile must be between 0.5 and 1"
        )
    if reference_lower_quantile >= reference_upper_quantile:
        raise ValueError(
            "reference quantiles must be ordered"
        )

    for name, value in (
        ("min_train_decision_times", min_train_decision_times),
        ("validation_decision_times", validation_decision_times),
        ("step_decision_times", step_decision_times),
        ("min_train_rows", min_train_rows),
    ):
        if value < 1:
            raise ValueError(f"{name} must be positive")

    required = set(MINT_ENRICHED_LP_FEATURE_COLUMNS)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"missing feature-drift columns: {missing}"
        )

    prepared = prepare_enriched_lp_training_frame(frame)
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
            "not enough unique decision timestamps for feature drift"
        )

    features = tuple(MINT_ENRICHED_LP_FEATURE_COLUMNS)
    folds: list[FeatureDriftFold] = []
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

        metrics: list[FeatureDriftMetric] = []
        for feature in features:
            train_values = train[feature].to_numpy(dtype=float)
            valid_values = valid[feature].to_numpy(dtype=float)

            train_q25, train_median, train_q75 = np.quantile(
                train_values,
                (0.25, 0.50, 0.75),
            )
            valid_q25, valid_median, valid_q75 = np.quantile(
                valid_values,
                (0.25, 0.50, 0.75),
            )
            lower, upper = np.quantile(
                train_values,
                (
                    reference_lower_quantile,
                    reference_upper_quantile,
                ),
            )

            below_rate = float(
                np.mean(valid_values < lower)
            )
            above_rate = float(
                np.mean(valid_values > upper)
            )
            escape_rate = below_rate + above_rate

            metrics.append(
                FeatureDriftMetric(
                    feature=feature,
                    train_median=float(train_median),
                    validation_median=float(valid_median),
                    median_shift=float(
                        valid_median - train_median
                    ),
                    train_iqr=float(train_q75 - train_q25),
                    validation_iqr=float(
                        valid_q75 - valid_q25
                    ),
                    train_lower_quantile=float(lower),
                    train_upper_quantile=float(upper),
                    validation_below_reference_rate=below_rate,
                    validation_above_reference_rate=above_rate,
                    validation_reference_escape_rate=escape_rate,
                )
            )

        fold_index += 1
        folds.append(
            FeatureDriftFold(
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
                reference_lower_quantile=reference_lower_quantile,
                reference_upper_quantile=reference_upper_quantile,
                mean_reference_escape_rate=float(
                    fmean(
                        item.validation_reference_escape_rate
                        for item in metrics
                    )
                ),
                max_reference_escape_rate=float(
                    max(
                        item.validation_reference_escape_rate
                        for item in metrics
                    )
                ),
                features=tuple(metrics),
            )
        )
        start += step_decision_times

    if not folds:
        raise ValueError(
            "feature-drift configuration produced no folds"
        )

    aggregates: list[FeatureDriftAggregate] = []
    for feature in features:
        rows = [
            next(
                item
                for item in fold.features
                if item.feature == feature
            )
            for fold in folds
        ]
        aggregates.append(
            FeatureDriftAggregate(
                feature=feature,
                folds=len(rows),
                mean_median_shift=float(
                    fmean(item.median_shift for item in rows)
                ),
                mean_validation_reference_escape_rate=float(
                    fmean(
                        item.validation_reference_escape_rate
                        for item in rows
                    )
                ),
                max_validation_reference_escape_rate=float(
                    max(
                        item.validation_reference_escape_rate
                        for item in rows
                    )
                ),
            )
        )

    return FeatureDriftReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        decision_times=len(times),
        feature_count=len(features),
        reference_lower_quantile=reference_lower_quantile,
        reference_upper_quantile=reference_upper_quantile,
        folds=tuple(folds),
        aggregates=tuple(aggregates),
    )
