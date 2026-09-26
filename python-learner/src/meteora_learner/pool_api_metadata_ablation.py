from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from .pool_api_metadata_context import (
    POOL_API_METADATA_FEATURE_COLUMNS,
    PoolAPIMetadataContextReport,
    attach_pool_api_metadata_from_store,
)
from .pool_lp_mint_ablation import MINT_ENRICHED_LP_FEATURE_COLUMNS
from .pool_lp_training import ENRICHED_LP_MODEL_TARGET_COLUMNS


API_METADATA_ENRICHED_LP_FEATURE_COLUMNS = (
    *MINT_ENRICHED_LP_FEATURE_COLUMNS,
    *POOL_API_METADATA_FEATURE_COLUMNS,
)


@dataclass(frozen=True)
class APIMetadataTrainingFrameReport:
    rows_seen: int
    rows_with_snapshot: int
    rows_with_complete_metadata: int
    rows_ready: int
    rows_dropped_missing_snapshot: int
    rows_dropped_incomplete_metadata: int
    feature_columns: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class APIMetadataAblationTarget:
    target: str
    base_mae: float
    metadata_mae: float
    metadata_mae_improvement: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class APIMetadataAblationFold:
    fold_index: int
    validation_start: str
    validation_end: str
    train_rows: int
    purged_rows: int
    validation_rows: int
    pools_in_train: int
    pools_in_validation: int
    targets: tuple[APIMetadataAblationTarget, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class APIMetadataAblationAggregate:
    target: str
    folds: int
    mean_base_mae: float
    mean_metadata_mae: float
    mean_metadata_mae_improvement: float
    metadata_better_folds: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class APIMetadataAblationReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    decision_times: int
    base_feature_count: int
    metadata_feature_count: int
    combined_feature_count: int
    folds: tuple[APIMetadataAblationFold, ...]
    aggregates: tuple[APIMetadataAblationAggregate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_api_metadata_enriched_lp_frame(
    database_path: str,
    mint_enriched_lp_frame: pd.DataFrame,
) -> tuple[pd.DataFrame, APIMetadataTrainingFrameReport]:
    enriched, context = attach_pool_api_metadata_from_store(
        database_path,
        mint_enriched_lp_frame,
    )

    for column in API_METADATA_ENRICHED_LP_FEATURE_COLUMNS:
        enriched[column] = pd.to_numeric(
            enriched[column],
            errors="coerce",
        )

    missing_snapshot = enriched[
        "api_snapshot_age_seconds"
    ].isna()
    incomplete_metadata = (
        ~missing_snapshot
        & enriched[list(POOL_API_METADATA_FEATURE_COLUMNS)]
        .isna()
        .any(axis=1)
    )
    incomplete_base = enriched[
        list(MINT_ENRICHED_LP_FEATURE_COLUMNS)
    ].isna().any(axis=1)

    numeric = enriched[
        list(API_METADATA_ENRICHED_LP_FEATURE_COLUMNS)
    ].replace([np.inf, -np.inf], np.nan)
    nonfinite = numeric.isna().any(axis=1)

    ready = ~(
        missing_snapshot
        | incomplete_metadata
        | incomplete_base
        | nonfinite
    )
    frame = enriched.loc[ready].copy()

    return frame, APIMetadataTrainingFrameReport(
        rows_seen=len(enriched),
        rows_with_snapshot=context.rows_with_snapshot,
        rows_with_complete_metadata=(
            context.rows_with_complete_metadata
        ),
        rows_ready=len(frame),
        rows_dropped_missing_snapshot=int(
            missing_snapshot.sum()
        ),
        rows_dropped_incomplete_metadata=int(
            incomplete_metadata.sum()
        ),
        feature_columns=API_METADATA_ENRICHED_LP_FEATURE_COLUMNS,
    )


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "pool_address",
        "decision_observed_at",
        "forward_end_observed_at",
        *API_METADATA_ENRICHED_LP_FEATURE_COLUMNS,
        *ENRICHED_LP_MODEL_TARGET_COLUMNS,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"missing API metadata ablation columns: {missing}"
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
        *API_METADATA_ENRICHED_LP_FEATURE_COLUMNS,
        *ENRICHED_LP_MODEL_TARGET_COLUMNS,
    )
    for column in numeric:
        work[column] = pd.to_numeric(
            work[column],
            errors="coerce",
        )
    if work[list(numeric)].isna().any(axis=None):
        raise ValueError(
            "API metadata ablation frame contains missing numeric values"
        )
    if not np.isfinite(
        work[list(numeric)].to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "API metadata ablation frame contains non-finite values"
        )

    return work.sort_values(
        ["decision_observed_at", "pool_address"],
        kind="stable",
    ).reset_index(drop=True)


def evaluate_api_metadata_ablation(
    frame: pd.DataFrame,
    *,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> APIMetadataAblationReport:
    """
    Measure incremental value of normalized pool API metadata.

    Base and metadata models use identical complete rows, targets, purged time
    folds, estimator family and seeds. No economic pass/fail verdict is emitted.
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
            "not enough unique decision timestamps for API metadata "
            "ablation"
        )

    base_features = list(MINT_ENRICHED_LP_FEATURE_COLUMNS)
    metadata_features = list(
        API_METADATA_ENRICHED_LP_FEATURE_COLUMNS
    )
    folds: list[APIMetadataAblationFold] = []
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

        targets: list[APIMetadataAblationTarget] = []
        for target_index, target in enumerate(
            ENRICHED_LP_MODEL_TARGET_COLUMNS
        ):
            y_train = train[target].to_numpy(dtype=float)
            y_valid = valid[target].to_numpy(dtype=float)
            seed = (
                5000
                + fold_index * len(
                    ENRICHED_LP_MODEL_TARGET_COLUMNS
                )
                + target_index
            )

            base_model = HistGradientBoostingRegressor(
                random_state=seed,
            )
            metadata_model = HistGradientBoostingRegressor(
                random_state=seed,
            )
            base_model.fit(train[base_features], y_train)
            metadata_model.fit(
                train[metadata_features],
                y_train,
            )

            base_mae = float(
                mean_absolute_error(
                    y_valid,
                    base_model.predict(valid[base_features]),
                )
            )
            metadata_mae = float(
                mean_absolute_error(
                    y_valid,
                    metadata_model.predict(
                        valid[metadata_features]
                    ),
                )
            )

            targets.append(
                APIMetadataAblationTarget(
                    target=target,
                    base_mae=base_mae,
                    metadata_mae=metadata_mae,
                    metadata_mae_improvement=(
                        base_mae - metadata_mae
                    ),
                )
            )

        fold_index += 1
        folds.append(
            APIMetadataAblationFold(
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
            "API metadata ablation configuration produced no folds"
        )

    aggregates: list[APIMetadataAblationAggregate] = []
    for target in ENRICHED_LP_MODEL_TARGET_COLUMNS:
        rows = [
            next(
                item
                for item in fold.targets
                if item.target == target
            )
            for fold in folds
        ]
        aggregates.append(
            APIMetadataAblationAggregate(
                target=target,
                folds=len(rows),
                mean_base_mae=float(
                    fmean(item.base_mae for item in rows)
                ),
                mean_metadata_mae=float(
                    fmean(item.metadata_mae for item in rows)
                ),
                mean_metadata_mae_improvement=float(
                    fmean(
                        item.metadata_mae_improvement
                        for item in rows
                    )
                ),
                metadata_better_folds=sum(
                    item.metadata_mae_improvement > 0
                    for item in rows
                ),
            )
        )

    return APIMetadataAblationReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        decision_times=len(times),
        base_feature_count=len(base_features),
        metadata_feature_count=len(
            POOL_API_METADATA_FEATURE_COLUMNS
        ),
        combined_feature_count=len(metadata_features),
        folds=tuple(folds),
        aggregates=tuple(aggregates),
    )
