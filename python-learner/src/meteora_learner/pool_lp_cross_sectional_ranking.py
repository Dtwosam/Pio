from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean, median
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .pool_lp_learning import ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS
from .pool_lp_mint_ablation import MINT_ENRICHED_LP_FEATURE_COLUMNS


@dataclass(frozen=True)
class CrossSectionRankingPoint:
    decision_observed_at: str
    candidate_rows: int
    pools_seen: int
    opportunity_rank_correlation: float | None
    predicted_top_pool: str
    realized_top_pool: str
    predicted_top_realized_net_return_bps: float
    realized_best_net_return_bps: float
    opportunity_regret_bps: float
    median_candidate_net_return_bps: float
    selected_uplift_vs_median_bps: float
    downside_rank_correlation: float | None
    predicted_low_downside_pool: str
    realized_low_downside_pool: str
    predicted_low_realized_downside_bps: float
    realized_min_downside_bps: float
    downside_regret_bps: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrossSectionRankingFold:
    fold_index: int
    validation_start: str
    validation_end: str
    train_rows: int
    purged_rows: int
    validation_rows: int
    ranking_points: int
    skipped_single_pool_times: int
    points: tuple[CrossSectionRankingPoint, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrossSectionRankingReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    feature_count: int
    decision_times: int
    ranking_points: int
    skipped_single_pool_times: int
    mean_opportunity_rank_correlation: float | None
    mean_opportunity_regret_bps: float
    median_opportunity_regret_bps: float
    mean_selected_uplift_vs_median_bps: float
    mean_downside_rank_correlation: float | None
    mean_downside_regret_bps: float
    median_downside_regret_bps: float
    folds: tuple[CrossSectionRankingFold, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _rank_correlation(
    predicted: np.ndarray,
    actual: np.ndarray,
) -> float | None:
    if len(predicted) < 2:
        return None
    predicted_rank = pd.Series(predicted).rank(method="average")
    actual_rank = pd.Series(actual).rank(method="average")
    value = predicted_rank.corr(actual_rank)
    if pd.isna(value):
        return None
    return float(value)


def _prepare_frame(
    frame: pd.DataFrame,
    feature_columns: tuple[str, ...],
) -> pd.DataFrame:
    required = {
        "pool_address",
        "decision_observed_at",
        "forward_end_observed_at",
        "target_net_return_bps",
        *feature_columns,
        *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"missing cross-sectional ranking columns: {missing}"
        )

    work = frame.copy()
    work["pool_address"] = work["pool_address"].astype(str).str.strip()
    if (work["pool_address"] == "").any():
        raise ValueError("pool_address cannot be empty")

    for column in ("decision_observed_at", "forward_end_observed_at"):
        work[column] = pd.to_datetime(
            work[column],
            utc=True,
            errors="coerce",
        )
        if work[column].isna().any():
            raise ValueError(f"{column} contains invalid timestamps")

    if (
        work["forward_end_observed_at"]
        <= work["decision_observed_at"]
    ).any():
        raise ValueError(
            "forward_end_observed_at must be after decision_observed_at"
        )

    numeric = (*feature_columns, "target_net_return_bps")
    for column in numeric:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    if work[list(numeric)].isna().any(axis=None):
        raise ValueError(
            "cross-sectional ranking frame contains missing numeric values"
        )
    if not np.isfinite(
        work[list(numeric)].to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "cross-sectional ranking frame contains non-finite values"
        )

    work["target_downside_bps"] = np.maximum(
        0.0,
        -work["target_net_return_bps"].to_numpy(dtype=float),
    )
    return work.sort_values(
        ["decision_observed_at", "pool_address"],
        kind="stable",
    ).reset_index(drop=True)


def evaluate_cross_sectional_ranking(
    frame: pd.DataFrame,
    *,
    feature_columns: Sequence[str] = MINT_ENRICHED_LP_FEATURE_COLUMNS,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> CrossSectionRankingReport:
    """
    Evaluate learned opportunity/downside ordering on unseen future candidates.

    Net-return and downside models are trained separately. Ranking statistics
    are descriptive research evidence only: they do not combine opportunity and
    downside into a score, select capital, or authorize a pool.
    """
    features = tuple(str(value) for value in feature_columns)
    if not features or len(set(features)) != len(features):
        raise ValueError(
            "feature_columns must be non-empty and unique"
        )
    for name, value in (
        ("min_train_decision_times", min_train_decision_times),
        ("validation_decision_times", validation_decision_times),
        ("step_decision_times", step_decision_times),
        ("min_train_rows", min_train_rows),
    ):
        if value < 1:
            raise ValueError(f"{name} must be positive")

    prepared = _prepare_frame(frame, features)
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
            "not enough unique decision timestamps for cross-sectional "
            "ranking evaluation"
        )

    folds: list[CrossSectionRankingFold] = []
    all_points: list[CrossSectionRankingPoint] = []
    total_skipped = 0
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
        valid = prepared[
            (prepared["decision_observed_at"] >= validation_start)
            & (prepared["decision_observed_at"] <= validation_end)
        ].copy()
        purged_rows = len(prior) - len(train)

        if len(train) < min_train_rows:
            raise ValueError(
                f"fold {fold_index + 1} has only {len(train)} "
                f"purged training rows; need {min_train_rows}"
            )
        if valid.empty:
            raise ValueError(
                f"fold {fold_index + 1} has no validation rows"
            )

        net_model = HistGradientBoostingRegressor(
            random_state=7000 + fold_index * 2,
        )
        downside_model = HistGradientBoostingRegressor(
            random_state=7001 + fold_index * 2,
        )
        net_model.fit(
            train[list(features)],
            train["target_net_return_bps"].to_numpy(dtype=float),
        )
        downside_model.fit(
            train[list(features)],
            train["target_downside_bps"].to_numpy(dtype=float),
        )

        fold_points: list[CrossSectionRankingPoint] = []
        skipped = 0
        for decision_time, group in valid.groupby(
            "decision_observed_at",
            sort=True,
        ):
            if group["pool_address"].nunique() < 2:
                skipped += 1
                continue

            x = group[list(features)]
            predicted_net = net_model.predict(x)
            predicted_downside = np.maximum(
                0.0,
                downside_model.predict(x),
            )
            actual_net = group[
                "target_net_return_bps"
            ].to_numpy(dtype=float)
            actual_downside = group[
                "target_downside_bps"
            ].to_numpy(dtype=float)

            top_index = int(np.argmax(predicted_net))
            best_index = int(np.argmax(actual_net))
            low_index = int(np.argmin(predicted_downside))
            actual_low_index = int(np.argmin(actual_downside))

            selected_net = float(actual_net[top_index])
            best_net = float(actual_net[best_index])
            median_net = float(np.median(actual_net))
            selected_downside = float(actual_downside[low_index])
            min_downside = float(actual_downside[actual_low_index])

            point = CrossSectionRankingPoint(
                decision_observed_at=pd.Timestamp(
                    decision_time
                ).isoformat(),
                candidate_rows=len(group),
                pools_seen=int(group["pool_address"].nunique()),
                opportunity_rank_correlation=_rank_correlation(
                    predicted_net,
                    actual_net,
                ),
                predicted_top_pool=str(
                    group.iloc[top_index]["pool_address"]
                ),
                realized_top_pool=str(
                    group.iloc[best_index]["pool_address"]
                ),
                predicted_top_realized_net_return_bps=selected_net,
                realized_best_net_return_bps=best_net,
                opportunity_regret_bps=best_net - selected_net,
                median_candidate_net_return_bps=median_net,
                selected_uplift_vs_median_bps=selected_net - median_net,
                downside_rank_correlation=_rank_correlation(
                    predicted_downside,
                    actual_downside,
                ),
                predicted_low_downside_pool=str(
                    group.iloc[low_index]["pool_address"]
                ),
                realized_low_downside_pool=str(
                    group.iloc[actual_low_index]["pool_address"]
                ),
                predicted_low_realized_downside_bps=selected_downside,
                realized_min_downside_bps=min_downside,
                downside_regret_bps=selected_downside - min_downside,
            )
            fold_points.append(point)
            all_points.append(point)

        fold_index += 1
        total_skipped += skipped
        folds.append(
            CrossSectionRankingFold(
                fold_index=fold_index,
                validation_start=validation_start.isoformat(),
                validation_end=validation_end.isoformat(),
                train_rows=len(train),
                purged_rows=purged_rows,
                validation_rows=len(valid),
                ranking_points=len(fold_points),
                skipped_single_pool_times=skipped,
                points=tuple(fold_points),
            )
        )
        start += step_decision_times

    if not all_points:
        raise ValueError(
            "cross-sectional ranking produced no multi-pool validation points"
        )

    opportunity_corr = [
        point.opportunity_rank_correlation
        for point in all_points
        if point.opportunity_rank_correlation is not None
    ]
    downside_corr = [
        point.downside_rank_correlation
        for point in all_points
        if point.downside_rank_correlation is not None
    ]
    opportunity_regret = [
        point.opportunity_regret_bps for point in all_points
    ]
    downside_regret = [
        point.downside_regret_bps for point in all_points
    ]

    return CrossSectionRankingReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        feature_count=len(features),
        decision_times=len(times),
        ranking_points=len(all_points),
        skipped_single_pool_times=total_skipped,
        mean_opportunity_rank_correlation=(
            float(fmean(opportunity_corr))
            if opportunity_corr
            else None
        ),
        mean_opportunity_regret_bps=float(
            fmean(opportunity_regret)
        ),
        median_opportunity_regret_bps=float(
            median(opportunity_regret)
        ),
        mean_selected_uplift_vs_median_bps=float(
            fmean(
                point.selected_uplift_vs_median_bps
                for point in all_points
            )
        ),
        mean_downside_rank_correlation=(
            float(fmean(downside_corr))
            if downside_corr
            else None
        ),
        mean_downside_regret_bps=float(fmean(downside_regret)),
        median_downside_regret_bps=float(
            median(downside_regret)
        ),
        folds=tuple(folds),
    )
