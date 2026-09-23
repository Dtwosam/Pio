from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Sequence

from .baseline_walk_forward import BaselineWalkForwardReport


@dataclass(frozen=True)
class Phase3PromotionCriteria:
    min_pools: int = 3
    min_total_steps: int = 30
    min_complete_steps: int = 20
    min_selection_rate: float = 0.50
    min_complete_rate: float = 0.50
    min_positive_excess_rate: float = 0.55
    min_mean_excess_vs_hold_bps: float = 0.0
    max_single_step_loss_bps: int = 1_000

    def __post_init__(self) -> None:
        for name in ("min_pools", "min_total_steps", "min_complete_steps"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "min_selection_rate",
            "min_complete_rate",
            "min_positive_excess_rate",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.max_single_step_loss_bps < 0:
            raise ValueError("max_single_step_loss_bps cannot be negative")


@dataclass(frozen=True)
class Phase3PromotionReport:
    phase2_ready: bool
    reports_seen: int
    pools_seen: int
    total_steps: int
    selected_steps: int
    complete_steps: int
    positive_excess_steps: int
    selection_rate: float
    complete_rate: float
    positive_excess_rate: float | None
    mean_excess_vs_hold_bps: float | None
    worst_excess_vs_hold_bps: int | None
    best_excess_vs_hold_bps: int | None
    criteria: Phase3PromotionCriteria
    promotion_ready: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase3_promotion(
    reports: Sequence[BaselineWalkForwardReport],
    *,
    phase2_ready: bool,
    criteria: Phase3PromotionCriteria = Phase3PromotionCriteria(),
) -> Phase3PromotionReport:
    if not reports:
        raise ValueError("at least one walk-forward report is required")

    total_steps = sum(report.steps for report in reports)
    selected_steps = sum(report.selected_steps for report in reports)
    pools_seen = len({report.pool_address for report in reports})

    complete_excess_bps: list[int] = []
    for report in reports:
        for step in report.steps_detail:
            economics = step.forward_economics
            if (
                step.forward_status == "VALID"
                and economics is not None
                and economics.excess_vs_hold_bps is not None
            ):
                complete_excess_bps.append(int(economics.excess_vs_hold_bps))

    complete_steps = len(complete_excess_bps)
    positive_steps = sum(value > 0 for value in complete_excess_bps)
    selection_rate = selected_steps / total_steps if total_steps else 0.0
    complete_rate = complete_steps / total_steps if total_steps else 0.0
    positive_rate = (
        positive_steps / complete_steps if complete_steps else None
    )
    mean_excess = (
        float(mean(complete_excess_bps))
        if complete_excess_bps
        else None
    )
    worst = min(complete_excess_bps) if complete_excess_bps else None
    best = max(complete_excess_bps) if complete_excess_bps else None

    reasons: list[str] = []
    checks = [
        (
            phase2_ready,
            "Phase 2 promotion gate is not ready",
        ),
        (
            pools_seen >= criteria.min_pools,
            f"pools_seen {pools_seen} < required {criteria.min_pools}",
        ),
        (
            total_steps >= criteria.min_total_steps,
            f"total_steps {total_steps} < required {criteria.min_total_steps}",
        ),
        (
            complete_steps >= criteria.min_complete_steps,
            f"complete_steps {complete_steps} < required "
            f"{criteria.min_complete_steps}",
        ),
        (
            selection_rate >= criteria.min_selection_rate,
            f"selection_rate {selection_rate:.6f} < required "
            f"{criteria.min_selection_rate:.6f}",
        ),
        (
            complete_rate >= criteria.min_complete_rate,
            f"complete_rate {complete_rate:.6f} < required "
            f"{criteria.min_complete_rate:.6f}",
        ),
        (
            positive_rate is not None
            and positive_rate >= criteria.min_positive_excess_rate,
            (
                f"positive_excess_rate "
                f"{positive_rate if positive_rate is not None else 'unavailable'} "
                f"< required {criteria.min_positive_excess_rate:.6f}"
            ),
        ),
        (
            mean_excess is not None
            and mean_excess >= criteria.min_mean_excess_vs_hold_bps,
            (
                f"mean_excess_vs_hold_bps "
                f"{mean_excess if mean_excess is not None else 'unavailable'} "
                f"< required {criteria.min_mean_excess_vs_hold_bps:.6f}"
            ),
        ),
        (
            worst is not None
            and worst >= -criteria.max_single_step_loss_bps,
            (
                f"worst_excess_vs_hold_bps "
                f"{worst if worst is not None else 'unavailable'} "
                f"< allowed {-criteria.max_single_step_loss_bps}"
            ),
        ),
    ]
    reasons.extend(message for passed, message in checks if not passed)

    return Phase3PromotionReport(
        phase2_ready=phase2_ready,
        reports_seen=len(reports),
        pools_seen=pools_seen,
        total_steps=total_steps,
        selected_steps=selected_steps,
        complete_steps=complete_steps,
        positive_excess_steps=positive_steps,
        selection_rate=selection_rate,
        complete_rate=complete_rate,
        positive_excess_rate=positive_rate,
        mean_excess_vs_hold_bps=mean_excess,
        worst_excess_vs_hold_bps=worst,
        best_excess_vs_hold_bps=best,
        criteria=criteria,
        promotion_ready=not reasons,
        reasons=tuple(reasons),
    )
