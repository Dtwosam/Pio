from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from .pool_lp_learning import ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS
from .pool_lp_mint_ablation import MINT_ENRICHED_LP_FEATURE_COLUMNS
from .pool_lp_training import ENRICHED_LP_MODEL_TARGET_COLUMNS


@dataclass(frozen=True)
class UnseenPoolTargetMetrics:
    target: str
    model_mae: float
    baseline_mae: float
    mae_improvement: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnseenPoolFold:
    fold_index: int
    held_out_pool: str
    validation_start: str
    validation_end: str
    training_pool_count: int
    train_rows: int
    purged_rows: int
    validation_rows: int
    targets: tuple[UnseenPoolTargetMetrics, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnseenPoolTargetAggregate:
    target: str
    folds: int
    held_out_pools: int
    mean_model_mae: float
    mean_baseline_mae: float
    mean_mae_improvement: float
    model_better_folds: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnseenPoolValidationReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    pools_available: int
    pools_evaluated: int
    feature_count: int
    folds: tuple[UnseenPoolFold, ...]
    aggregates: tuple[UnseenPoolTargetAggregate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "pool_address",
        "decision_observed_at",
        "forward_end_observed_at",
        *MINT_ENRICHED_LP_FEATURE_COLUMNS,
        *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"missing unseen-pool validation columns: {missing}"
        )

    work = frame.copy()
    work["pool_address"] = work["pool_address"].astype(str).str.strip()
    if (work["pool_address"] == "").any():
        raise ValueError("pool_address cannot be empty")

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
        *MINT_ENRICHED_LP_FEATURE_COLUMNS,
        *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    )
    for column in numeric:
        work[column] = pd.to_numeric(
            work[column],
            errors="coerce",
        )
    if work[list(numeric)].isna().any(axis=None):
        raise ValueError(
            "unseen-pool validation frame contains missing numeric values"
        )
    if not np.isfinite(
        work[list(numeric)].to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "unseen-pool validation frame contains non-finite values"
        )

    work["target_downside_bps"] = np.maximum(
        0.0,
        -work["target_net_return_bps"].to_numpy(dtype=float),
    )

    return work.sort_values(
        ["decision_observed_at", "pool_address"],
        kind="stable",
    ).reset_index(drop=True)


def evaluate_unseen_pool_walk_forward(
    frame: pd.DataFrame,
    *,
    held_out_pools: Sequence[str] | None = None,
    min_train_decision_times: int = 20,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 40,
) -> UnseenPoolValidationReport:
    """
    Test whether the full LP+market+mint model generalizes to unseen pools.

    For each held-out pool, that pool is excluded from all training rows.
    Training uses only other pools whose complete forward label window ends
    before validation begins. Validation then uses later decisions from the
    held-out pool. No pool ranking or policy qualification is produced.
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
    available = tuple(
        sorted(prepared["pool_address"].unique().tolist())
    )
    if len(available) < 2:
        raise ValueError(
            "unseen-pool validation needs at least two pools"
        )

    if held_out_pools is None:
        selected = available
    else:
        selected = tuple(
            dict.fromkeys(
                str(pool).strip()
                for pool in held_out_pools
                if str(pool).strip()
            )
        )
        unknown = sorted(set(selected) - set(available))
        if unknown:
            raise ValueError(
                f"held_out_pools are not present in frame: {unknown}"
            )
        if not selected:
            raise ValueError(
                "held_out_pools must contain at least one pool"
            )

    features = list(MINT_ENRICHED_LP_FEATURE_COLUMNS)
    folds: list[UnseenPoolFold] = []
    fold_index = 0

    for held_out in selected:
        pool_rows = prepared[
            prepared["pool_address"] == held_out
        ]
        times = (
            pool_rows["decision_observed_at"]
            .drop_duplicates()
            .sort_values()
            .tolist()
        )
        if len(times) < (
            min_train_decision_times + validation_decision_times
        ):
            raise ValueError(
                f"held-out pool {held_out} does not have enough "
                "decision timestamps"
            )

        start = min_train_decision_times
        while start + validation_decision_times <= len(times):
            validation_times = times[
                start : start + validation_decision_times
            ]
            validation_start = pd.Timestamp(validation_times[0])
            validation_end = pd.Timestamp(validation_times[-1])

            prior_other = prepared[
                (prepared["pool_address"] != held_out)
                & (
                    prepared["decision_observed_at"]
                    < validation_start
                )
            ].copy()
            train = prior_other[
                prior_other["forward_end_observed_at"]
                < validation_start
            ].copy()
            purged_rows = len(prior_other) - len(train)

            valid = pool_rows[
                (
                    pool_rows["decision_observed_at"]
                    >= validation_start
                )
                & (
                    pool_rows["decision_observed_at"]
                    <= validation_end
                )
            ].copy()

            if len(train) < min_train_rows:
                raise ValueError(
                    f"held-out pool {held_out} fold "
                    f"{fold_index + 1} has only {len(train)} "
                    f"purged training rows; need {min_train_rows}"
                )
            if valid.empty:
                raise ValueError(
                    f"held-out pool {held_out} fold "
                    f"{fold_index + 1} has no validation rows"
                )

            target_metrics: list[UnseenPoolTargetMetrics] = []
            for target_index, target in enumerate(
                ENRICHED_LP_MODEL_TARGET_COLUMNS
            ):
                y_train = train[target].to_numpy(dtype=float)
                y_valid = valid[target].to_numpy(dtype=float)

                model = HistGradientBoostingRegressor(
                    random_state=(
                        2400
                        + fold_index * len(
                            ENRICHED_LP_MODEL_TARGET_COLUMNS
                        )
                        + target_index
                    ),
                )
                model.fit(train[features], y_train)
                model_prediction = model.predict(valid[features])

                baseline_value = float(np.median(y_train))
                baseline_prediction = np.full(
                    len(y_valid),
                    baseline_value,
                    dtype=float,
                )

                model_mae = float(
                    mean_absolute_error(
                        y_valid,
                        model_prediction,
                    )
                )
                baseline_mae = float(
                    mean_absolute_error(
                        y_valid,
                        baseline_prediction,
                    )
                )
                target_metrics.append(
                    UnseenPoolTargetMetrics(
                        target=target,
                        model_mae=model_mae,
                        baseline_mae=baseline_mae,
                        mae_improvement=baseline_mae - model_mae,
                    )
                )

            fold_index += 1
            folds.append(
                UnseenPoolFold(
                    fold_index=fold_index,
                    held_out_pool=held_out,
                    validation_start=validation_start.isoformat(),
                    validation_end=validation_end.isoformat(),
                    training_pool_count=int(
                        train["pool_address"].nunique()
                    ),
                    train_rows=len(train),
                    purged_rows=purged_rows,
                    validation_rows=len(valid),
                    targets=tuple(target_metrics),
                )
            )
            start += step_decision_times

    if not folds:
        raise ValueError(
            "unseen-pool validation configuration produced no folds"
        )

    aggregates: list[UnseenPoolTargetAggregate] = []
    for target in ENRICHED_LP_MODEL_TARGET_COLUMNS:
        target_rows = [
            next(
                metric
                for metric in fold.targets
                if metric.target == target
            )
            for fold in folds
        ]
        aggregates.append(
            UnseenPoolTargetAggregate(
                target=target,
                folds=len(target_rows),
                held_out_pools=len(
                    {
                        fold.held_out_pool
                        for fold in folds
                    }
                ),
                mean_model_mae=float(
                    fmean(
                        item.model_mae
                        for item in target_rows
                    )
                ),
                mean_baseline_mae=float(
                    fmean(
                        item.baseline_mae
                        for item in target_rows
                    )
                ),
                mean_mae_improvement=float(
                    fmean(
                        item.mae_improvement
                        for item in target_rows
                    )
                ),
                model_better_folds=sum(
                    item.mae_improvement > 0
                    for item in target_rows
                ),
            )
        )

    return UnseenPoolValidationReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        pools_available=len(available),
        pools_evaluated=len(selected),
        feature_count=len(MINT_ENRICHED_LP_FEATURE_COLUMNS),
        folds=tuple(folds),
        aggregates=tuple(aggregates),
    )
