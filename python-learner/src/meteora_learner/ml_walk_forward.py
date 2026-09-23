from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from statistics import fmean
from typing import Any

import pandas as pd

from .ml_challenger import MLChallengerCriteria, evaluate_ml_challenger
from .ml_inference import MLInferenceConfig
from .ml_training import train_ml_v1_frame


@dataclass(frozen=True)
class MLWalkForwardCriteria:
    min_train_decision_times: int = 30
    validation_decision_times: int = 10
    step_decision_times: int = 10
    min_folds: int = 3
    min_total_comparable_decisions: int = 30
    min_qualified_fold_rate: float = 0.67
    min_mean_fold_uplift_bps: float = 0.0
    min_positive_fold_rate: float = 0.50
    max_worst_fold_mean_uplift_loss_bps: float = 250.0
    training_split_fraction: float = 0.8
    training_min_rows: int = 50

    def __post_init__(self) -> None:
        for name in (
            "min_train_decision_times",
            "validation_decision_times",
            "step_decision_times",
            "min_folds",
            "min_total_comparable_decisions",
            "training_min_rows",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.step_decision_times < self.validation_decision_times:
            raise ValueError(
                "step_decision_times cannot be below validation_decision_times"
            )
        for name in (
            "min_qualified_fold_rate",
            "min_positive_fold_rate",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if not math.isfinite(self.min_mean_fold_uplift_bps):
            raise ValueError(
                "min_mean_fold_uplift_bps must be finite"
            )
        if (
            not math.isfinite(
                self.max_worst_fold_mean_uplift_loss_bps
            )
            or self.max_worst_fold_mean_uplift_loss_bps < 0
        ):
            raise ValueError(
                "max_worst_fold_mean_uplift_loss_bps must be "
                "finite and non-negative"
            )
        if not 0.5 <= self.training_split_fraction < 1.0:
            raise ValueError(
                "training_split_fraction must be between 0.5 and 1.0"
            )


@dataclass(frozen=True)
class MLWalkForwardFold:
    fold_index: int
    train_window_end: str
    validation_start: str
    validation_end: str
    source_train_rows: int
    comparable_decisions: int
    mean_realized_uplift_bps: float | None
    win_rate: float | None
    worst_model_excess_bps: int | None
    offline_qualified: bool
    status: str
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MLWalkForwardReport:
    phase3_ready: bool
    decision_times: int
    folds_attempted: int
    folds_evaluated: int
    qualified_folds: int
    qualified_fold_rate: float
    positive_uplift_folds: int
    positive_fold_rate: float
    total_comparable_decisions: int
    mean_fold_uplift_bps: float | None
    worst_fold_mean_uplift_bps: float | None
    criteria: MLWalkForwardCriteria
    fold_criteria: MLChallengerCriteria
    walk_forward_qualified: bool
    policy_actionable: bool
    reasons: tuple[str, ...]
    folds: tuple[MLWalkForwardFold, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_ml_walk_forward(
    frame: pd.DataFrame,
    *,
    phase3_ready: bool,
    criteria: MLWalkForwardCriteria = MLWalkForwardCriteria(),
    fold_criteria: MLChallengerCriteria = MLChallengerCriteria(
        min_comparable_decisions=5,
        min_choice_coverage_rate=0.60,
        min_mean_uplift_bps=0.0,
        min_win_rate=0.40,
        min_positive_excess_rate=0.40,
        max_single_decision_loss_bps=1_500,
    ),
    inference_config: MLInferenceConfig = MLInferenceConfig(),
) -> MLWalkForwardReport:
    if "decision_observed_at" not in frame.columns:
        raise ValueError(
            "walk-forward dataset is missing decision_observed_at"
        )
    work = frame.copy()
    work["_decision_time"] = pd.to_datetime(
        work["decision_observed_at"],
        utc=True,
        errors="coerce",
    )
    if work["_decision_time"].isna().any():
        raise ValueError(
            "decision_observed_at contains invalid timestamps"
        )

    times = sorted(work["_decision_time"].drop_duplicates().tolist())
    folds: list[MLWalkForwardFold] = []
    start = criteria.min_train_decision_times
    fold_index = 0

    while start + criteria.validation_decision_times <= len(times):
        validation_times = times[
            start : start + criteria.validation_decision_times
        ]
        validation_start = pd.Timestamp(validation_times[0])
        validation_end = pd.Timestamp(validation_times[-1])
        train = work[work["_decision_time"] < validation_start].copy()
        source_train_rows = len(train)
        fold_index += 1

        try:
            bundle = train_ml_v1_frame(
                train.drop(columns=["_decision_time"]),
                split_fraction=criteria.training_split_fraction,
                min_rows=criteria.training_min_rows,
            )
            evaluation_bundle = replace(
                bundle,
                validation_start=validation_start.isoformat(),
                validation_end=validation_end.isoformat(),
            )
            report = evaluate_ml_challenger(
                evaluation_bundle,
                work.drop(columns=["_decision_time"]),
                phase3_ready=phase3_ready,
                inference_config=inference_config,
                criteria=fold_criteria,
            )
            status = (
                "QUALIFIED"
                if report.offline_qualified
                else "REJECTED"
            )
            folds.append(
                MLWalkForwardFold(
                    fold_index=fold_index,
                    train_window_end=pd.Timestamp(
                        times[start - 1]
                    ).isoformat(),
                    validation_start=validation_start.isoformat(),
                    validation_end=validation_end.isoformat(),
                    source_train_rows=source_train_rows,
                    comparable_decisions=report.comparable_decisions,
                    mean_realized_uplift_bps=(
                        report.mean_realized_uplift_bps
                    ),
                    win_rate=report.win_rate,
                    worst_model_excess_bps=(
                        report.worst_model_excess_bps
                    ),
                    offline_qualified=report.offline_qualified,
                    status=status,
                    reasons=report.reasons,
                )
            )
        except (ValueError, TypeError) as exc:
            folds.append(
                MLWalkForwardFold(
                    fold_index=fold_index,
                    train_window_end=pd.Timestamp(
                        times[start - 1]
                    ).isoformat(),
                    validation_start=validation_start.isoformat(),
                    validation_end=validation_end.isoformat(),
                    source_train_rows=source_train_rows,
                    comparable_decisions=0,
                    mean_realized_uplift_bps=None,
                    win_rate=None,
                    worst_model_excess_bps=None,
                    offline_qualified=False,
                    status="FAILED",
                    reasons=(str(exc),),
                )
            )

        start += criteria.step_decision_times

    attempted = len(folds)
    evaluated = sum(item.status != "FAILED" for item in folds)
    qualified = sum(item.offline_qualified for item in folds)
    qualified_rate = qualified / attempted if attempted else 0.0
    uplifts = [
        item.mean_realized_uplift_bps
        for item in folds
        if item.mean_realized_uplift_bps is not None
    ]
    positive = sum(value > 0 for value in uplifts)
    positive_rate = positive / attempted if attempted else 0.0
    total_comparable = sum(
        item.comparable_decisions for item in folds
    )
    mean_uplift = float(fmean(uplifts)) if uplifts else None
    worst_uplift = min(uplifts) if uplifts else None

    reasons: list[str] = []
    checks = (
        (
            phase3_ready,
            "Phase 3 deterministic policy is not promoted",
        ),
        (
            attempted >= criteria.min_folds,
            f"walk-forward folds {attempted} < required "
            f"{criteria.min_folds}",
        ),
        (
            evaluated == attempted and attempted > 0,
            f"walk-forward evaluated folds {evaluated}/{attempted} "
            "include failed folds",
        ),
        (
            total_comparable
            >= criteria.min_total_comparable_decisions,
            f"total comparable decisions {total_comparable} < required "
            f"{criteria.min_total_comparable_decisions}",
        ),
        (
            qualified_rate >= criteria.min_qualified_fold_rate,
            f"qualified fold rate {qualified_rate:.6f} < required "
            f"{criteria.min_qualified_fold_rate:.6f}",
        ),
        (
            mean_uplift is not None
            and mean_uplift
            >= criteria.min_mean_fold_uplift_bps,
            "mean fold uplift is below required minimum",
        ),
        (
            positive_rate >= criteria.min_positive_fold_rate,
            f"positive fold rate {positive_rate:.6f} < required "
            f"{criteria.min_positive_fold_rate:.6f}",
        ),
        (
            worst_uplift is not None
            and worst_uplift
            >= -criteria.max_worst_fold_mean_uplift_loss_bps,
            "worst fold mean uplift exceeds allowed loss",
        ),
    )
    reasons.extend(message for passed, message in checks if not passed)

    return MLWalkForwardReport(
        phase3_ready=phase3_ready,
        decision_times=len(times),
        folds_attempted=attempted,
        folds_evaluated=evaluated,
        qualified_folds=qualified,
        qualified_fold_rate=qualified_rate,
        positive_uplift_folds=positive,
        positive_fold_rate=positive_rate,
        total_comparable_decisions=total_comparable,
        mean_fold_uplift_bps=mean_uplift,
        worst_fold_mean_uplift_bps=worst_uplift,
        criteria=criteria,
        fold_criteria=fold_criteria,
        walk_forward_qualified=not reasons,
        policy_actionable=False,
        reasons=tuple(reasons),
        folds=tuple(folds),
    )
