from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from .pool_lp_learning import (
    ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    ENRICHED_LP_FEATURE_COLUMNS,
)
from .pool_lp_training import ENRICHED_LP_MODEL_TARGET_COLUMNS
from .pool_token_mint_context import (
    TOKEN_MINT_CONTEXT_FEATURE_COLUMNS,
    TokenMintContextReport,
    attach_token_mint_context_from_store,
)


MINT_ENRICHED_LP_FEATURE_COLUMNS = (
    *ENRICHED_LP_FEATURE_COLUMNS,
    *TOKEN_MINT_CONTEXT_FEATURE_COLUMNS,
)


@dataclass(frozen=True)
class MintEnrichedLPTrainingFrameReport:
    rows_seen: int
    rows_with_chain_pool_state: int
    rows_with_both_mints: int
    rows_ready: int
    rows_dropped_missing_chain: int
    rows_dropped_missing_mint_context: int
    feature_columns: tuple[str, ...]
    target_columns: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MintFeatureAblationTarget:
    target: str
    base_model_mae: float
    mint_model_mae: float
    mint_mae_improvement: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MintFeatureAblationFold:
    fold_index: int
    validation_start: str
    validation_end: str
    train_rows: int
    purged_rows: int
    validation_rows: int
    pools_in_train: int
    pools_in_validation: int
    targets: tuple[MintFeatureAblationTarget, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MintFeatureAblationAggregate:
    target: str
    folds: int
    mean_base_model_mae: float
    mean_mint_model_mae: float
    mean_mint_mae_improvement: float
    mint_better_folds: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MintFeatureAblationReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    decision_times: int
    base_feature_count: int
    mint_feature_count: int
    combined_feature_count: int
    folds: tuple[MintFeatureAblationFold, ...]
    aggregates: tuple[MintFeatureAblationAggregate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_mint_enriched_lp_training_frame(
    database_path: str,
    market_enriched_lp_frame: pd.DataFrame,
) -> tuple[pd.DataFrame, MintEnrichedLPTrainingFrameReport]:
    """
    Add as-of token/mint context to an already market-enriched LP frame.

    Rows with incomplete mint evidence are excluded from this training frame
    rather than filled with invented values. The exclusion is descriptive and
    does not imply that the underlying pool is economically unsafe.
    """
    required = {
        "pool_address",
        "decision_observed_at",
        "forward_end_observed_at",
        *ENRICHED_LP_FEATURE_COLUMNS,
        *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    }
    missing = sorted(required - set(market_enriched_lp_frame.columns))
    if missing:
        raise ValueError(
            f"missing market-enriched LP columns: {missing}"
        )

    enriched, context = attach_token_mint_context_from_store(
        database_path,
        market_enriched_lp_frame,
    )

    for column in (
        *ENRICHED_LP_FEATURE_COLUMNS,
        *TOKEN_MINT_CONTEXT_FEATURE_COLUMNS,
        *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    ):
        enriched[column] = pd.to_numeric(
            enriched[column],
            errors="coerce",
        )

    missing_chain = enriched[
        "mint_chain_snapshot_age_seconds"
    ].isna()
    missing_mint_context = (
        ~missing_chain
        & enriched[list(TOKEN_MINT_CONTEXT_FEATURE_COLUMNS)]
        .isna()
        .any(axis=1)
    )
    incomplete_base = enriched[
        [
            *ENRICHED_LP_FEATURE_COLUMNS,
            *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
        ]
    ].isna().any(axis=1)

    numeric = enriched[
        [
            *MINT_ENRICHED_LP_FEATURE_COLUMNS,
            *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
        ]
    ].replace([np.inf, -np.inf], np.nan)
    nonfinite = numeric.isna().any(axis=1)

    ready = ~(
        missing_chain
        | missing_mint_context
        | incomplete_base
        | nonfinite
    )
    training = enriched.loc[ready].copy()

    report = MintEnrichedLPTrainingFrameReport(
        rows_seen=len(enriched),
        rows_with_chain_pool_state=context.rows_with_chain_pool_state,
        rows_with_both_mints=context.rows_with_both_mints,
        rows_ready=len(training),
        rows_dropped_missing_chain=int(missing_chain.sum()),
        rows_dropped_missing_mint_context=int(
            missing_mint_context.sum()
        ),
        feature_columns=MINT_ENRICHED_LP_FEATURE_COLUMNS,
        target_columns=ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    )
    return training, report


def _prepare_ablation_frame(frame: pd.DataFrame) -> pd.DataFrame:
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
            f"missing mint ablation columns: {missing}"
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
            "mint ablation frame contains missing numeric values"
        )
    if not np.isfinite(
        work[list(numeric)].to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "mint ablation frame contains non-finite values"
        )

    work["target_downside_bps"] = np.maximum(
        0.0,
        -work["target_net_return_bps"].to_numpy(dtype=float),
    )

    return work.sort_values(
        ["decision_observed_at", "pool_address"],
        kind="stable",
    ).reset_index(drop=True)


def evaluate_mint_feature_ablation(
    frame: pd.DataFrame,
    *,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> MintFeatureAblationReport:
    """
    Compare LP+market models with and without token/mint context.

    Both models use exactly the same complete rows, purged time folds, targets,
    estimator family and random seed. Positive mint_mae_improvement means the
    mint-enriched model had lower MAE on that unseen fold. No pass/fail policy
    threshold or trading action is produced.
    """
    for name, value in (
        ("min_train_decision_times", min_train_decision_times),
        ("validation_decision_times", validation_decision_times),
        ("step_decision_times", step_decision_times),
        ("min_train_rows", min_train_rows),
    ):
        if value < 1:
            raise ValueError(f"{name} must be positive")

    prepared = _prepare_ablation_frame(frame)
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
            "not enough unique decision timestamps for mint-feature "
            "ablation"
        )

    folds: list[MintFeatureAblationFold] = []
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

        x_base_train = train[
            list(ENRICHED_LP_FEATURE_COLUMNS)
        ]
        x_base_valid = valid[
            list(ENRICHED_LP_FEATURE_COLUMNS)
        ]
        x_mint_train = train[
            list(MINT_ENRICHED_LP_FEATURE_COLUMNS)
        ]
        x_mint_valid = valid[
            list(MINT_ENRICHED_LP_FEATURE_COLUMNS)
        ]

        targets: list[MintFeatureAblationTarget] = []
        for target_index, target in enumerate(
            ENRICHED_LP_MODEL_TARGET_COLUMNS
        ):
            y_train = train[target].to_numpy(dtype=float)
            y_valid = valid[target].to_numpy(dtype=float)
            seed = (
                1600
                + fold_index * len(
                    ENRICHED_LP_MODEL_TARGET_COLUMNS
                )
                + target_index
            )

            base_model = HistGradientBoostingRegressor(
                random_state=seed,
            )
            mint_model = HistGradientBoostingRegressor(
                random_state=seed,
            )
            base_model.fit(x_base_train, y_train)
            mint_model.fit(x_mint_train, y_train)

            base_mae = float(
                mean_absolute_error(
                    y_valid,
                    base_model.predict(x_base_valid),
                )
            )
            mint_mae = float(
                mean_absolute_error(
                    y_valid,
                    mint_model.predict(x_mint_valid),
                )
            )
            targets.append(
                MintFeatureAblationTarget(
                    target=target,
                    base_model_mae=base_mae,
                    mint_model_mae=mint_mae,
                    mint_mae_improvement=base_mae - mint_mae,
                )
            )

        fold_index += 1
        folds.append(
            MintFeatureAblationFold(
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
            "mint-feature ablation configuration produced no folds"
        )

    aggregates: list[MintFeatureAblationAggregate] = []
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
            MintFeatureAblationAggregate(
                target=target,
                folds=len(results),
                mean_base_model_mae=float(
                    fmean(
                        item.base_model_mae
                        for item in results
                    )
                ),
                mean_mint_model_mae=float(
                    fmean(
                        item.mint_model_mae
                        for item in results
                    )
                ),
                mean_mint_mae_improvement=float(
                    fmean(
                        item.mint_mae_improvement
                        for item in results
                    )
                ),
                mint_better_folds=sum(
                    item.mint_mae_improvement > 0
                    for item in results
                ),
            )
        )

    return MintFeatureAblationReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        decision_times=len(times),
        base_feature_count=len(ENRICHED_LP_FEATURE_COLUMNS),
        mint_feature_count=len(TOKEN_MINT_CONTEXT_FEATURE_COLUMNS),
        combined_feature_count=len(MINT_ENRICHED_LP_FEATURE_COLUMNS),
        folds=tuple(folds),
        aggregates=tuple(aggregates),
    )
