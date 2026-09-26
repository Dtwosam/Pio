from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_pinball_loss

from .pool_lp_mint_ablation import MINT_ENRICHED_LP_FEATURE_COLUMNS
from .pool_lp_training import prepare_enriched_lp_training_frame


DEFAULT_RETURN_QUANTILES = (0.10, 0.50, 0.90)
DEFAULT_DOWNSIDE_QUANTILES = (0.50, 0.90)


@dataclass(frozen=True)
class TailQuantileMetrics:
    target: str
    quantile: float
    pinball_loss: float
    empirical_below_rate: float
    calibration_error: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TailRiskFold:
    fold_index: int
    validation_start: str
    validation_end: str
    train_rows: int
    purged_rows: int
    validation_rows: int
    pools_in_train: int
    pools_in_validation: int
    net_return_metrics: tuple[TailQuantileMetrics, ...]
    downside_metrics: tuple[TailQuantileMetrics, ...]
    return_quantile_crossing_rate: float
    downside_quantile_crossing_rate: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TailQuantileAggregate:
    target: str
    quantile: float
    folds: int
    mean_pinball_loss: float
    mean_empirical_below_rate: float
    mean_calibration_error: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TailRiskCalibrationReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    decision_times: int
    feature_count: int
    return_quantiles: tuple[float, ...]
    downside_quantiles: tuple[float, ...]
    folds: tuple[TailRiskFold, ...]
    aggregates: tuple[TailQuantileAggregate, ...]
    mean_return_quantile_crossing_rate: float
    mean_downside_quantile_crossing_rate: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _validate_quantiles(
    values: Sequence[float],
    *,
    name: str,
) -> tuple[float, ...]:
    quantiles = tuple(float(value) for value in values)
    if not quantiles:
        raise ValueError(f"{name} must contain at least one quantile")
    if any(not 0.0 < value < 1.0 for value in quantiles):
        raise ValueError(
            f"{name} values must be strictly between 0 and 1"
        )
    if tuple(sorted(set(quantiles))) != quantiles:
        raise ValueError(
            f"{name} must be strictly increasing with no duplicates"
        )
    return quantiles


def _crossing_rate(predictions: list[np.ndarray]) -> float:
    if len(predictions) < 2:
        return 0.0
    stacked = np.vstack(predictions)
    crossing = np.any(np.diff(stacked, axis=0) < 0.0, axis=0)
    return float(np.mean(crossing))


def evaluate_tail_risk_calibration(
    frame: pd.DataFrame,
    *,
    return_quantiles: Sequence[float] = DEFAULT_RETURN_QUANTILES,
    downside_quantiles: Sequence[float] = DEFAULT_DOWNSIDE_QUANTILES,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> TailRiskCalibrationReport:
    """
    Evaluate conditional quantiles for LP net return and downside magnitude.

    Quantiles are statistical descriptions, not economic decision boundaries.
    Training folds are purged so no forward outcome window overlaps validation.
    Calibration is evaluated only on unseen future rows.
    """
    return_q = _validate_quantiles(
        return_quantiles,
        name="return_quantiles",
    )
    downside_q = _validate_quantiles(
        downside_quantiles,
        name="downside_quantiles",
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
            f"missing tail-risk feature columns: {missing}"
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
            "not enough unique decision timestamps for tail-risk "
            "calibration"
        )

    features = list(MINT_ENRICHED_LP_FEATURE_COLUMNS)
    folds: list[TailRiskFold] = []
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

        x_train = train[features]
        x_valid = valid[features]

        y_return_train = train[
            "target_net_return_bps"
        ].to_numpy(dtype=float)
        y_return_valid = valid[
            "target_net_return_bps"
        ].to_numpy(dtype=float)

        y_downside_train = train[
            "target_downside_bps"
        ].to_numpy(dtype=float)
        y_downside_valid = valid[
            "target_downside_bps"
        ].to_numpy(dtype=float)

        return_metrics: list[TailQuantileMetrics] = []
        return_predictions: list[np.ndarray] = []
        for quantile_index, quantile in enumerate(return_q):
            model = HistGradientBoostingRegressor(
                loss="quantile",
                quantile=quantile,
                random_state=(
                    3000
                    + fold_index * 100
                    + quantile_index
                ),
            )
            model.fit(x_train, y_return_train)
            prediction = model.predict(x_valid)
            return_predictions.append(prediction)

            below = float(
                np.mean(y_return_valid <= prediction)
            )
            return_metrics.append(
                TailQuantileMetrics(
                    target="target_net_return_bps",
                    quantile=quantile,
                    pinball_loss=float(
                        mean_pinball_loss(
                            y_return_valid,
                            prediction,
                            alpha=quantile,
                        )
                    ),
                    empirical_below_rate=below,
                    calibration_error=below - quantile,
                )
            )

        downside_metrics: list[TailQuantileMetrics] = []
        downside_predictions: list[np.ndarray] = []
        for quantile_index, quantile in enumerate(downside_q):
            model = HistGradientBoostingRegressor(
                loss="quantile",
                quantile=quantile,
                random_state=(
                    4000
                    + fold_index * 100
                    + quantile_index
                ),
            )
            model.fit(x_train, y_downside_train)
            prediction = np.maximum(
                0.0,
                model.predict(x_valid),
            )
            downside_predictions.append(prediction)

            below = float(
                np.mean(y_downside_valid <= prediction)
            )
            downside_metrics.append(
                TailQuantileMetrics(
                    target="target_downside_bps",
                    quantile=quantile,
                    pinball_loss=float(
                        mean_pinball_loss(
                            y_downside_valid,
                            prediction,
                            alpha=quantile,
                        )
                    ),
                    empirical_below_rate=below,
                    calibration_error=below - quantile,
                )
            )

        fold_index += 1
        folds.append(
            TailRiskFold(
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
                net_return_metrics=tuple(return_metrics),
                downside_metrics=tuple(downside_metrics),
                return_quantile_crossing_rate=_crossing_rate(
                    return_predictions
                ),
                downside_quantile_crossing_rate=_crossing_rate(
                    downside_predictions
                ),
            )
        )
        start += step_decision_times

    if not folds:
        raise ValueError(
            "tail-risk calibration configuration produced no folds"
        )

    aggregates: list[TailQuantileAggregate] = []
    for target, quantiles, field in (
        ("target_net_return_bps", return_q, "net_return_metrics"),
        ("target_downside_bps", downside_q, "downside_metrics"),
    ):
        for quantile in quantiles:
            metrics = [
                next(
                    item
                    for item in getattr(fold, field)
                    if item.quantile == quantile
                )
                for fold in folds
            ]
            aggregates.append(
                TailQuantileAggregate(
                    target=target,
                    quantile=quantile,
                    folds=len(metrics),
                    mean_pinball_loss=float(
                        fmean(
                            item.pinball_loss
                            for item in metrics
                        )
                    ),
                    mean_empirical_below_rate=float(
                        fmean(
                            item.empirical_below_rate
                            for item in metrics
                        )
                    ),
                    mean_calibration_error=float(
                        fmean(
                            item.calibration_error
                            for item in metrics
                        )
                    ),
                )
            )

    return TailRiskCalibrationReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        decision_times=len(times),
        feature_count=len(features),
        return_quantiles=return_q,
        downside_quantiles=downside_q,
        folds=tuple(folds),
        aggregates=tuple(aggregates),
        mean_return_quantile_crossing_rate=float(
            fmean(
                fold.return_quantile_crossing_rate
                for fold in folds
            )
        ),
        mean_downside_quantile_crossing_rate=float(
            fmean(
                fold.downside_quantile_crossing_rate
                for fold in folds
            )
        ),
    )
