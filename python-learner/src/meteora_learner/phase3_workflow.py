from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .baseline_policy import BaselinePolicyConfig
from .baseline_walk_forward import BaselineWalkForwardReport, walk_forward_baseline
from .phase3_validation import (
    Phase3PromotionCriteria,
    Phase3PromotionReport,
    evaluate_phase3_promotion,
)
from .phase_promotion import (
    PHASE2,
    PHASE2_EVIDENCE_TYPE,
    persist_phase3_promotion,
)
from .storage import Storage
from .strategy import StrategyType


@dataclass(frozen=True)
class Phase3ValidationInput:
    pool_address: str
    amount_x: int
    amount_y: int
    network_cost_y_atomic: int

    def __post_init__(self) -> None:
        if not self.pool_address:
            raise ValueError("pool_address is required")
        if self.amount_x < 0 or self.amount_y < 0:
            raise ValueError("token amounts cannot be negative")
        if self.amount_x == 0 and self.amount_y == 0:
            raise ValueError("at least one token amount must be positive")
        if self.network_cost_y_atomic < 0:
            raise ValueError("network_cost_y_atomic cannot be negative")


@dataclass(frozen=True)
class Phase3ValidationRun:
    phase2_ready: bool
    promotion: Phase3PromotionReport
    reports: tuple[BaselineWalkForwardReport, ...]
    persisted: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def validate_phase3_from_chain(
    database_path: str,
    *,
    inputs: Sequence[Phase3ValidationInput],
    criteria: Phase3PromotionCriteria = Phase3PromotionCriteria(),
    persist_if_ready: bool = False,
    lookback_observations: int = 12,
    forward_observations: int = 2,
    step_observations: int | None = None,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Sequence[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> Phase3ValidationRun:
    if not inputs:
        raise ValueError("at least one Phase 3 validation input is required")

    storage = Storage(database_path)
    phase2_ready = storage.phase_is_promoted(
        PHASE2,
        evidence_type=PHASE2_EVIDENCE_TYPE,
    )

    reports: list[BaselineWalkForwardReport] = []
    for item in inputs:
        reports.append(
            walk_forward_baseline(
                database_path,
                pool_address=item.pool_address,
                amount_x=item.amount_x,
                amount_y=item.amount_y,
                phase2_gate=None,
                config=BaselinePolicyConfig(
                    estimated_network_cost_y_atomic=item.network_cost_y_atomic,
                ),
                lookback_observations=lookback_observations,
                forward_observations=forward_observations,
                step_observations=step_observations,
                half_widths=half_widths,
                center_offsets=center_offsets,
                strategies=strategies,
                max_share_bps=max_share_bps,
                favor_x_in_active_bin=favor_x_in_active_bin,
            )
        )

    promotion = evaluate_phase3_promotion(
        reports,
        phase2_ready=phase2_ready,
        criteria=criteria,
    )
    persisted = False
    if persist_if_ready and promotion.promotion_ready:
        persist_phase3_promotion(storage, report=promotion)
        persisted = True

    return Phase3ValidationRun(
        phase2_ready=phase2_ready,
        promotion=promotion,
        reports=tuple(reports),
        persisted=persisted,
    )
