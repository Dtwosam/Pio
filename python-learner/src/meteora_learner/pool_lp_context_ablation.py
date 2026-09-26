from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from .ml_dataset import ML_FEATURE_COLUMNS
from .pool_lp_learning import ENRICHED_LP_FEATURE_COLUMNS
from .pool_lp_mint_ablation import MINT_ENRICHED_LP_FEATURE_COLUMNS
from .pool_lp_training import (
    ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    ENRICHED_LP_MODEL_TARGET_COLUMNS,
)


@dataclass(frozen=True)
class ContextFeatureAblationTarget:
    target: str
    lp_only_mae: float
    lp_market_mae: float
    lp_market_mint_mae: float
    market_mae_improvement: float
    mint_mae_improvement: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextFeatureAblationFold:
    fold_index: int
    validation_start: str
    validation_end: str
    train_rows: int
    purged_rows: int
    validation_rows: int
    pools_in_train: int
    pools_in_validation: int
    targets: tuple[ContextFeatureAblationTarget, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextFeatureAblationAggregate:
    target: str
    folds: int
    mean_lp_only_mae: float
    mean_lp_market_mae: float
    mean_lp_market_mint_mae: float
    mean_market_mae_improvement: float
    mean_mint_mae_improvement: float
    market_better_folds: int
    mint_better_folds: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextFeatureAblationReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    decision_times: int
    lp_feature_count: int
    market_feature_count: int
    mint_feature_count: int
    full_feature_count: int
    folds: tuple[ContextFeatureAblationFold, ...]
    aggregates: tuple[ContextFeatureAblationAggregate, ...]

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
            f"missing context-ablation columns: {missing}"
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
            "context-ablation frame contains missing numeric values"
        )
    if not np.isfinite(
        work[list(numeric)].to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "context-ablation frame contains non-finite values"
        )

    work["target_downside_bps"] = np.maximum(
        0.0,
        -work["target_net_return_bps"].to_numpy(dtype=float),
    )
    return work.sort_values(
        ["decision_observed_at", "pool_address"],
        kind="stable",
    ).reset_index(drop=True)


def evaluate_context_feature_ablation(
    frame: pd.DataFrame,
    *,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> ContextFeatureAblationReport:
    """
    Measure incremental predictive value of market and mint context.

    All three model stages use identical rows, targets, purged folds, estimator
    family and random seed. Positive market_mae_improvement means LP+market
    improved on LP-only. Positive mint_mae_improvement means adding mint
    context improved on LP+market. No economic policy verdict is produced.
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
            "not enough unique decision timestamps for context ablation"
        )

    folds: list[ContextFeatureAblationFold] = []
    start = min_train_decision_times
    fold_index = 0

    lp_features = list(ML_FEATURE_COLUMNS)
    market_features = list(ENRICHED_LP_FEATURE_COLUMNS)
    full_features = list(MINT_ENRICHED_LP_FEATURE_COLUMNS)

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

        targets: list[ContextFeatureAblationTarget] = []

        for target_index, target in enumerate(
            ENRICHED_LP_MODEL_TARGET_COLUMNS
        ):
            y_train = train[target].to_numpy(dtype=float)
            y_valid = valid[target].to_numpy(dtype=float)
            seed = (
                2000
                + fold_index * len(
                    ENRICHED_LP_MODEL_TARGET_COLUMNS
                )
                + target_index
            )

            stage_predictions: list[np.ndarray] = []
            for features in (
                lp_features,
                market_features,
                full_features,
            ):
                model = HistGradientBoostingRegressor(
                    random_state=seed,
                )
                model.fit(train[features], y_train)
                stage_predictions.append(
                    model.predict(valid[features])
                )

            lp_only_mae = float(
                mean_absolute_error(
                    y_valid,
                    stage_predictions[0],
                )
            )
            lp_market_mae = float(
                mean_absolute_error(
                    y_valid,
                    stage_predictions[1],
                )
            )
            lp_market_mint_mae = float(
                mean_absolute_error(
                    y_valid,
                    stage_predictions[2],
                )
            )
            targets.append(
                ContextFeatureAblationTarget(
                    target=target,
                    lp_only_mae=lp_only_mae,
                    lp_market_mae=lp_market_mae,
                    lp_market_mint_mae=lp_market_mint_mae,
                    market_mae_improvement=(
                        lp_only_mae - lp_market_mae
                    ),
                    mint_mae_improvement=(
                        lp_market_mae - lp_market_mint_mae
                    ),
                )
            )

        fold_index += 1
        folds.append(
            ContextFeatureAblationFold(
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
                targets=tuple(targets),
            )
        )
        start += step_decision_times

    if not folds:
        raise ValueError(
            "context-ablation configuration produced no folds"
        )

    aggregates: list[ContextFeatureAblationAggregate] = []
    for target in ENRICHED_LP_MODEL_TARGET_COLUMNS:
        results = [
            next(
                item
                for item in fold.targets
                if item.target == target
            )
            for fold in folds
        ]
        aggregates.append(
            ContextFeatureAblationAggregate(
                target=target,
                folds=len(results),
                mean_lp_only_mae=float(
                    fmean(item.lp_only_mae for item in results)
                ),
                mean_lp_market_mae=float(
                    fmean(item.lp_market_mae for item in results)
                ),
                mean_lp_market_mint_mae=float(
                    fmean(
                        item.lp_market_mint_mae
                        for item in results
                    )
                ),
                mean_market_mae_improvement=float(
                    fmean(
                        item.market_mae_improvement
                        for item in results
                    )
                ),
                mean_mint_mae_improvement=float(
                    fmean(
                        item.mint_mae_improvement
                        for item in results
                    )
                ),
                market_better_folds=sum(
                    item.market_mae_improvement > 0
                    for item in results
                ),
                mint_better_folds=sum(
                    item.mint_mae_improvement > 0
                    for item in results
                ),
            )
        )

    market_feature_count = (
        len(ENRICHED_LP_FEATURE_COLUMNS)
        - len(ML_FEATURE_COLUMNS)
    )
    mint_feature_count = (
        len(MINT_ENRICHED_LP_FEATURE_COLUMNS)
        - len(ENRICHED_LP_FEATURE_COLUMNS)
    )

    return ContextFeatureAblationReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        decision_times=len(times),
        lp_feature_count=len(ML_FEATURE_COLUMNS),
        market_feature_count=market_feature_count,
        mint_feature_count=mint_feature_count,
        full_feature_count=len(MINT_ENRICHED_LP_FEATURE_COLUMNS),
        folds=tuple(folds),
        aggregates=tuple(aggregates),
    )
