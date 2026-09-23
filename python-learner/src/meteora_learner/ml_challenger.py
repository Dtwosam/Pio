from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any

import pandas as pd

from .ml_inference import MLInferenceConfig, score_ml_candidates
from .ml_training import MLV1Bundle


@dataclass(frozen=True)
class MLChallengerCriteria:
    min_comparable_decisions: int = 20
    min_choice_coverage_rate: float = 0.80
    min_mean_uplift_bps: float = 0.0
    min_win_rate: float = 0.50
    min_positive_excess_rate: float = 0.50
    max_single_decision_loss_bps: int = 1_000

    def __post_init__(self) -> None:
        if self.min_comparable_decisions <= 0:
            raise ValueError("min_comparable_decisions must be positive")
        for name in (
            "min_choice_coverage_rate",
            "min_win_rate",
            "min_positive_excess_rate",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.max_single_decision_loss_bps < 0:
            raise ValueError("max_single_decision_loss_bps cannot be negative")


@dataclass(frozen=True)
class MLChallengerDecision:
    pool_address: str
    decision_observed_at: str
    model_strategy: str
    model_half_width: int
    model_center_offset: int
    baseline_strategy: str
    baseline_half_width: int
    baseline_center_offset: int
    model_actual_excess_bps: int
    baseline_actual_excess_bps: int
    realized_uplift_bps: int
    model_actual_net_return_bps: int
    baseline_actual_net_return_bps: int
    model_predicted_score_bps: float
    same_action: bool


@dataclass(frozen=True)
class MLChallengerReport:
    phase3_ready: bool
    validation_start: str
    validation_end: str
    validation_decisions_seen: int
    comparable_decisions: int
    model_choice_decisions: int
    choice_coverage_rate: float
    wins: int
    ties: int
    losses: int
    win_rate: float | None
    mean_model_excess_bps: float | None
    mean_baseline_excess_bps: float | None
    mean_realized_uplift_bps: float | None
    model_positive_excess_rate: float | None
    worst_model_excess_bps: int | None
    worst_baseline_excess_bps: int | None
    criteria: MLChallengerCriteria
    offline_qualified: bool
    policy_actionable: bool
    reasons: tuple[str, ...]
    dropped_groups: tuple[tuple[str, int], ...]
    decisions: tuple[MLChallengerDecision, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def evaluate_ml_challenger(
    bundle: MLV1Bundle,
    frame: pd.DataFrame,
    *,
    phase3_ready: bool,
    inference_config: MLInferenceConfig = MLInferenceConfig(),
    criteria: MLChallengerCriteria = MLChallengerCriteria(),
) -> MLChallengerReport:
    """
    Compare the ML challenger with the deterministic baseline on held-out decisions.

    Only decision groups at/after bundle.validation_start are evaluated. Actual
    forward targets are used only after each policy has selected its action.
    Passing this gate is an offline qualification, never permission to trade.
    """
    required = {
        "pool_address",
        "decision_observed_at",
        "strategy",
        "half_width",
        "center_offset",
        "baseline_selected",
        "target_net_return_bps",
        "target_excess_vs_hold_bps",
        *bundle.feature_columns,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing challenger evaluation columns: {missing}")

    work = frame.copy()
    work["_decision_time"] = pd.to_datetime(
        work["decision_observed_at"],
        utc=True,
        errors="coerce",
    )
    if work["_decision_time"].isna().any():
        raise ValueError("decision_observed_at contains invalid timestamps")

    validation_start = pd.Timestamp(bundle.validation_start)
    validation_end = pd.Timestamp(bundle.validation_end)
    validation = work[
        (work["_decision_time"] >= validation_start)
        & (work["_decision_time"] <= validation_end)
    ].copy()
    if validation.empty:
        raise ValueError("no rows fall inside the model validation window")

    decisions: list[MLChallengerDecision] = []
    drops: dict[str, int] = {}
    groups = list(
        validation.groupby(
            ["pool_address", "_decision_time"],
            sort=True,
            dropna=False,
        )
    )

    model_choice_decisions = 0
    for (pool_address, decision_time), group in groups:
        baseline_rows = group[
            pd.to_numeric(group["baseline_selected"], errors="coerce") == 1
        ]
        if len(baseline_rows) != 1:
            _bump(drops, "baseline_choice_not_unique")
            continue

        inference = score_ml_candidates(
            bundle,
            group,
            config=inference_config,
        )
        choice = inference.research_choice
        if choice is None:
            _bump(drops, "ml_no_eligible_choice")
            continue
        model_choice_decisions += 1

        if choice.row_index not in group.index:
            _bump(drops, "ml_choice_row_missing")
            continue

        model_row = group.loc[choice.row_index]
        baseline_row = baseline_rows.iloc[0]

        model_excess = int(model_row["target_excess_vs_hold_bps"])
        baseline_excess = int(baseline_row["target_excess_vs_hold_bps"])
        model_net = int(model_row["target_net_return_bps"])
        baseline_net = int(baseline_row["target_net_return_bps"])

        decisions.append(
            MLChallengerDecision(
                pool_address=str(pool_address),
                decision_observed_at=pd.Timestamp(decision_time).isoformat(),
                model_strategy=str(model_row["strategy"]),
                model_half_width=int(model_row["half_width"]),
                model_center_offset=int(model_row["center_offset"]),
                baseline_strategy=str(baseline_row["strategy"]),
                baseline_half_width=int(baseline_row["half_width"]),
                baseline_center_offset=int(baseline_row["center_offset"]),
                model_actual_excess_bps=model_excess,
                baseline_actual_excess_bps=baseline_excess,
                realized_uplift_bps=model_excess - baseline_excess,
                model_actual_net_return_bps=model_net,
                baseline_actual_net_return_bps=baseline_net,
                model_predicted_score_bps=choice.risk_adjusted_score_bps,
                same_action=(
                    str(model_row["strategy"]) == str(baseline_row["strategy"])
                    and int(model_row["half_width"])
                    == int(baseline_row["half_width"])
                    and int(model_row["center_offset"])
                    == int(baseline_row["center_offset"])
                ),
            )
        )

    comparable = len(decisions)
    seen = len(groups)
    coverage = comparable / seen if seen else 0.0
    uplift = [item.realized_uplift_bps for item in decisions]
    model_excess = [item.model_actual_excess_bps for item in decisions]
    baseline_excess = [item.baseline_actual_excess_bps for item in decisions]

    wins = sum(value > 0 for value in uplift)
    ties = sum(value == 0 for value in uplift)
    losses = sum(value < 0 for value in uplift)
    win_rate = wins / comparable if comparable else None
    positive_rate = (
        sum(value > 0 for value in model_excess) / comparable
        if comparable
        else None
    )
    mean_uplift = float(mean(uplift)) if uplift else None
    mean_model = float(mean(model_excess)) if model_excess else None
    mean_baseline = float(mean(baseline_excess)) if baseline_excess else None
    worst_model = min(model_excess) if model_excess else None
    worst_baseline = min(baseline_excess) if baseline_excess else None

    reasons: list[str] = []
    checks = [
        (
            phase3_ready,
            "Phase 3 deterministic policy is not promoted",
        ),
        (
            comparable >= criteria.min_comparable_decisions,
            f"comparable_decisions {comparable} < required "
            f"{criteria.min_comparable_decisions}",
        ),
        (
            coverage >= criteria.min_choice_coverage_rate,
            f"choice_coverage_rate {coverage:.6f} < required "
            f"{criteria.min_choice_coverage_rate:.6f}",
        ),
        (
            mean_uplift is not None
            and mean_uplift >= criteria.min_mean_uplift_bps,
            f"mean_realized_uplift_bps "
            f"{mean_uplift if mean_uplift is not None else 'unavailable'} "
            f"< required {criteria.min_mean_uplift_bps:.6f}",
        ),
        (
            win_rate is not None and win_rate >= criteria.min_win_rate,
            f"win_rate "
            f"{win_rate if win_rate is not None else 'unavailable'} "
            f"< required {criteria.min_win_rate:.6f}",
        ),
        (
            positive_rate is not None
            and positive_rate >= criteria.min_positive_excess_rate,
            f"model_positive_excess_rate "
            f"{positive_rate if positive_rate is not None else 'unavailable'} "
            f"< required {criteria.min_positive_excess_rate:.6f}",
        ),
        (
            worst_model is not None
            and worst_model >= -criteria.max_single_decision_loss_bps,
            f"worst_model_excess_bps "
            f"{worst_model if worst_model is not None else 'unavailable'} "
            f"< allowed {-criteria.max_single_decision_loss_bps}",
        ),
    ]
    reasons.extend(message for passed, message in checks if not passed)

    return MLChallengerReport(
        phase3_ready=phase3_ready,
        validation_start=bundle.validation_start,
        validation_end=bundle.validation_end,
        validation_decisions_seen=seen,
        comparable_decisions=comparable,
        model_choice_decisions=model_choice_decisions,
        choice_coverage_rate=coverage,
        wins=wins,
        ties=ties,
        losses=losses,
        win_rate=win_rate,
        mean_model_excess_bps=mean_model,
        mean_baseline_excess_bps=mean_baseline,
        mean_realized_uplift_bps=mean_uplift,
        model_positive_excess_rate=positive_rate,
        worst_model_excess_bps=worst_model,
        worst_baseline_excess_bps=worst_baseline,
        criteria=criteria,
        offline_qualified=not reasons,
        policy_actionable=False,
        reasons=tuple(reasons),
        dropped_groups=tuple(sorted(drops.items())),
        decisions=tuple(decisions),
    )
