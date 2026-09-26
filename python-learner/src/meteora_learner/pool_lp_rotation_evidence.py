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
class RotationTransitionPoint:
    decision_observed_at: str
    previous_leader_pool: str
    new_leader_pool: str
    predicted_new_leader_net_return_bps: float
    predicted_stay_net_return_bps: float
    predicted_switch_advantage_bps: float
    realized_new_leader_net_return_bps: float
    realized_stay_net_return_bps: float
    realized_switch_advantage_bps: float
    switch_was_beneficial: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RotationEvidenceFold:
    fold_index: int
    validation_start: str
    validation_end: str
    train_rows: int
    purged_rows: int
    validation_rows: int
    transition_opportunities: int
    leader_changes: int
    skipped_missing_stay_pool: int
    transitions: tuple[RotationTransitionPoint, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RotationEvidenceReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    switching_transition_costs_included: bool
    feature_count: int
    decision_times: int
    transition_opportunities: int
    leader_changes: int
    skipped_missing_stay_pool: int
    leader_change_rate: float
    beneficial_change_rate: float | None
    mean_predicted_switch_advantage_bps: float | None
    mean_realized_switch_advantage_bps: float | None
    median_realized_switch_advantage_bps: float | None
    predicted_realized_advantage_correlation: float | None
    folds: tuple[RotationEvidenceFold, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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
            f"missing rotation-evidence columns: {missing}"
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
            "rotation-evidence frame contains missing numeric values"
        )
    if not np.isfinite(
        work[list(numeric)].to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "rotation-evidence frame contains non-finite values"
        )

    return work.sort_values(
        ["decision_observed_at", "pool_address"],
        kind="stable",
    ).reset_index(drop=True)


def _correlation(
    predicted: list[float],
    realized: list[float],
) -> float | None:
    if len(predicted) < 2:
        return None
    x = np.asarray(predicted, dtype=float)
    y = np.asarray(realized, dtype=float)
    if np.std(x) == 0.0 or np.std(y) == 0.0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def evaluate_rotation_transition_evidence(
    frame: pd.DataFrame,
    *,
    feature_columns: Sequence[str] = MINT_ENRICHED_LP_FEATURE_COLUMNS,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> RotationEvidenceReport:
    """
    Measure whether learned leader changes translate into realized improvement.

    The evaluator records the continuous predicted advantage of switching away
    from the previous learned leader and the realized advantage afterward.
    It deliberately does not choose a switching threshold or include unobserved
    transition costs such as exit/re-entry costs.
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
            "not enough unique decision timestamps for rotation evidence"
        )

    folds: list[RotationEvidenceFold] = []
    all_transitions: list[RotationTransitionPoint] = []
    total_opportunities = 0
    total_changes = 0
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

        model = HistGradientBoostingRegressor(
            random_state=8000 + fold_index,
        )
        model.fit(
            train[list(features)],
            train["target_net_return_bps"].to_numpy(dtype=float),
        )

        previous_leader: str | None = None
        fold_transitions: list[RotationTransitionPoint] = []
        opportunities = 0
        changes = 0
        skipped = 0

        for decision_time, group in valid.groupby(
            "decision_observed_at",
            sort=True,
        ):
            if group["pool_address"].nunique() < 2:
                continue

            local = group.copy()
            local["_predicted_net"] = model.predict(
                local[list(features)]
            )

            pool_rows = []
            for pool, pool_group in local.groupby(
                "pool_address",
                sort=True,
            ):
                best_index = pool_group["_predicted_net"].idxmax()
                pool_rows.append(local.loc[best_index])
            pool_frame = pd.DataFrame(pool_rows).reset_index(drop=True)

            leader_index = pool_frame["_predicted_net"].idxmax()
            leader = str(
                pool_frame.loc[leader_index, "pool_address"]
            )

            if previous_leader is None:
                previous_leader = leader
                continue

            opportunities += 1
            if leader == previous_leader:
                continue

            stay_rows = pool_frame[
                pool_frame["pool_address"] == previous_leader
            ]
            if stay_rows.empty:
                skipped += 1
                previous_leader = leader
                continue

            changes += 1
            new_row = pool_frame.loc[leader_index]
            stay_row = stay_rows.iloc[0]

            predicted_new = float(new_row["_predicted_net"])
            predicted_stay = float(stay_row["_predicted_net"])
            realized_new = float(
                new_row["target_net_return_bps"]
            )
            realized_stay = float(
                stay_row["target_net_return_bps"]
            )
            realized_advantage = realized_new - realized_stay

            point = RotationTransitionPoint(
                decision_observed_at=pd.Timestamp(
                    decision_time
                ).isoformat(),
                previous_leader_pool=previous_leader,
                new_leader_pool=leader,
                predicted_new_leader_net_return_bps=predicted_new,
                predicted_stay_net_return_bps=predicted_stay,
                predicted_switch_advantage_bps=(
                    predicted_new - predicted_stay
                ),
                realized_new_leader_net_return_bps=realized_new,
                realized_stay_net_return_bps=realized_stay,
                realized_switch_advantage_bps=realized_advantage,
                switch_was_beneficial=realized_advantage > 0.0,
            )
            fold_transitions.append(point)
            all_transitions.append(point)
            previous_leader = leader

        fold_index += 1
        total_opportunities += opportunities
        total_changes += changes
        total_skipped += skipped
        folds.append(
            RotationEvidenceFold(
                fold_index=fold_index,
                validation_start=validation_start.isoformat(),
                validation_end=validation_end.isoformat(),
                train_rows=len(train),
                purged_rows=purged_rows,
                validation_rows=len(valid),
                transition_opportunities=opportunities,
                leader_changes=changes,
                skipped_missing_stay_pool=skipped,
                transitions=tuple(fold_transitions),
            )
        )
        start += step_decision_times

    predicted_advantages = [
        item.predicted_switch_advantage_bps
        for item in all_transitions
    ]
    realized_advantages = [
        item.realized_switch_advantage_bps
        for item in all_transitions
    ]

    return RotationEvidenceReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        switching_transition_costs_included=False,
        feature_count=len(features),
        decision_times=len(times),
        transition_opportunities=total_opportunities,
        leader_changes=total_changes,
        skipped_missing_stay_pool=total_skipped,
        leader_change_rate=(
            total_changes / total_opportunities
            if total_opportunities > 0
            else 0.0
        ),
        beneficial_change_rate=(
            sum(item.switch_was_beneficial for item in all_transitions)
            / len(all_transitions)
            if all_transitions
            else None
        ),
        mean_predicted_switch_advantage_bps=(
            float(fmean(predicted_advantages))
            if predicted_advantages
            else None
        ),
        mean_realized_switch_advantage_bps=(
            float(fmean(realized_advantages))
            if realized_advantages
            else None
        ),
        median_realized_switch_advantage_bps=(
            float(median(realized_advantages))
            if realized_advantages
            else None
        ),
        predicted_realized_advantage_correlation=_correlation(
            predicted_advantages,
            realized_advantages,
        ),
        folds=tuple(folds),
    )
