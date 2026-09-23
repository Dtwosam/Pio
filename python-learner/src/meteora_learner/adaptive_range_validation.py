from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .adaptive_range import (
    AdaptiveRangeCriteria,
    _historical_max_displacements,
    _nearest_rank,
)
from .phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from .storage import Storage


ADAPTIVE_RANGE_WALK_FORWARD_EVIDENCE_TYPE = "ADAPTIVE_RANGE_WALK_FORWARD_V1"


@dataclass(frozen=True)
class AdaptiveRangeValidationCriteria:
    fixed_half_width_bins: int = 5
    min_decisions: int = 20
    min_adaptive_survival_rate: float = 0.75
    min_survival_uplift_vs_fixed: float = 0.0
    max_mean_width_multiple_vs_fixed: float = 2.0
    max_cap_exceeded_rate: float = 0.10

    def __post_init__(self) -> None:
        if self.fixed_half_width_bins < 0:
            raise ValueError("fixed_half_width_bins cannot be negative")
        if self.min_decisions < 1:
            raise ValueError("min_decisions must be positive")
        if not 0.0 <= self.min_adaptive_survival_rate <= 1.0:
            raise ValueError(
                "min_adaptive_survival_rate must be between 0 and 1"
            )
        if not -1.0 <= self.min_survival_uplift_vs_fixed <= 1.0:
            raise ValueError(
                "min_survival_uplift_vs_fixed must be between -1 and 1"
            )
        if self.max_mean_width_multiple_vs_fixed <= 0:
            raise ValueError(
                "max_mean_width_multiple_vs_fixed must be positive"
            )
        if not 0.0 <= self.max_cap_exceeded_rate <= 1.0:
            raise ValueError(
                "max_cap_exceeded_rate must be between 0 and 1"
            )


@dataclass(frozen=True)
class AdaptiveRangeDecisionResult:
    observed_at: str
    active_bin_id: int
    raw_half_width_bins: int
    adaptive_half_width_bins: int
    future_max_displacement_bins: int
    adaptive_survived: bool
    fixed_survived: bool
    range_cap_exceeded: bool


@dataclass(frozen=True)
class AdaptiveRangeValidationReport:
    pool_address: str
    as_of: str | None
    phase8_promoted: bool
    research_only: bool
    policy_actionable: bool
    status: str
    decisions: int
    adaptive_survival_rate: float | None
    fixed_survival_rate: float | None
    survival_uplift_vs_fixed: float | None
    mean_adaptive_half_width_bins: float | None
    fixed_half_width_bins: int
    mean_width_multiple_vs_fixed: float | None
    cap_exceeded_rate: float | None
    adaptive_criteria: AdaptiveRangeCriteria
    validation_criteria: AdaptiveRangeValidationCriteria
    research_qualified: bool
    reasons: tuple[str, ...]
    decision_results: tuple[AdaptiveRangeDecisionResult, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def validate_adaptive_range_walk_forward(
    storage: Storage,
    *,
    pool_address: str,
    adaptive_criteria: AdaptiveRangeCriteria = AdaptiveRangeCriteria(),
    validation_criteria: AdaptiveRangeValidationCriteria = (
        AdaptiveRangeValidationCriteria()
    ),
    as_of: str | None = None,
) -> AdaptiveRangeValidationReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")

    phase8_promoted = storage.phase_is_promoted(
        PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
    )

    with storage.connect() as conn:
        if as_of is None:
            rows = conn.execute(
                """
                SELECT observed_at, active_bin_id
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                ORDER BY julianday(observed_at) ASC, id ASC
                """,
                (pool_address,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT observed_at, active_bin_id
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                  AND julianday(observed_at) <= julianday(?)
                ORDER BY julianday(observed_at) ASC, id ASC
                """,
                (pool_address, as_of),
            ).fetchall()

    observed_at = [str(row[0]) for row in rows]
    active_bins = [int(row[1]) for row in rows]
    holding = adaptive_criteria.holding_observations
    results: list[AdaptiveRangeDecisionResult] = []

    for decision_index in range(len(active_bins) - holding):
        history_start = max(
            0,
            decision_index
            + 1
            - adaptive_criteria.lookback_observations,
        )
        history = active_bins[history_start : decision_index + 1]
        windows = _historical_max_displacements(
            history,
            holding,
        )
        if len(windows) < adaptive_criteria.min_historical_windows:
            continue

        raw_width = _nearest_rank(
            windows,
            adaptive_criteria.target_coverage,
        )
        width = min(
            adaptive_criteria.max_half_width_bins,
            max(adaptive_criteria.min_half_width_bins, raw_width),
        )
        future = active_bins[
            decision_index + 1 : decision_index + holding + 1
        ]
        entry = active_bins[decision_index]
        future_displacement = max(
            abs(value - entry)
            for value in future
        )
        results.append(
            AdaptiveRangeDecisionResult(
                observed_at=observed_at[decision_index],
                active_bin_id=entry,
                raw_half_width_bins=raw_width,
                adaptive_half_width_bins=width,
                future_max_displacement_bins=future_displacement,
                adaptive_survived=future_displacement <= width,
                fixed_survived=(
                    future_displacement
                    <= validation_criteria.fixed_half_width_bins
                ),
                range_cap_exceeded=(
                    raw_width
                    > adaptive_criteria.max_half_width_bins
                ),
            )
        )

    decisions = len(results)
    if decisions == 0:
        adaptive_survival = None
        fixed_survival = None
        uplift = None
        mean_width = None
        width_multiple = None
        cap_rate = None
    else:
        adaptive_survival = (
            sum(item.adaptive_survived for item in results)
            / decisions
        )
        fixed_survival = (
            sum(item.fixed_survived for item in results)
            / decisions
        )
        uplift = adaptive_survival - fixed_survival
        mean_width = (
            sum(item.adaptive_half_width_bins for item in results)
            / decisions
        )
        width_multiple = (
            mean_width / validation_criteria.fixed_half_width_bins
            if validation_criteria.fixed_half_width_bins > 0
            else (
                1.0
                if mean_width == 0
                else float("inf")
            )
        )
        cap_rate = (
            sum(item.range_cap_exceeded for item in results)
            / decisions
        )

    reasons: list[str] = []
    checks = (
        (
            decisions >= validation_criteria.min_decisions,
            f"walk-forward decisions {decisions} are below "
            f"{validation_criteria.min_decisions}",
        ),
        (
            adaptive_survival is not None
            and adaptive_survival
            >= validation_criteria.min_adaptive_survival_rate,
            "adaptive survival rate is below required minimum",
        ),
        (
            uplift is not None
            and uplift
            >= validation_criteria.min_survival_uplift_vs_fixed,
            "adaptive survival uplift versus fixed width is below required minimum",
        ),
        (
            width_multiple is not None
            and width_multiple
            <= validation_criteria.max_mean_width_multiple_vs_fixed,
            "adaptive mean width is too large versus fixed baseline",
        ),
        (
            cap_rate is not None
            and cap_rate
            <= validation_criteria.max_cap_exceeded_rate,
            "adaptive range cap is exceeded too often",
        ),
    )
    reasons.extend(message for passed, message in checks if not passed)
    qualified = not reasons

    if not phase8_promoted:
        status = "RESEARCH_ONLY_PHASE8_BLOCKED"
    elif qualified:
        status = "QUALIFIED_RESEARCH"
    else:
        status = "NOT_QUALIFIED"

    return AdaptiveRangeValidationReport(
        pool_address=pool_address,
        as_of=as_of,
        phase8_promoted=phase8_promoted,
        research_only=True,
        policy_actionable=False,
        status=status,
        decisions=decisions,
        adaptive_survival_rate=adaptive_survival,
        fixed_survival_rate=fixed_survival,
        survival_uplift_vs_fixed=uplift,
        mean_adaptive_half_width_bins=mean_width,
        fixed_half_width_bins=validation_criteria.fixed_half_width_bins,
        mean_width_multiple_vs_fixed=width_multiple,
        cap_exceeded_rate=cap_rate,
        adaptive_criteria=adaptive_criteria,
        validation_criteria=validation_criteria,
        research_qualified=qualified,
        reasons=tuple(reasons),
        decision_results=tuple(results),
    )


def persist_adaptive_range_validation(
    storage: Storage,
    *,
    report: AdaptiveRangeValidationReport,
) -> int:
    return storage.save_advanced_edge_evidence(
        edge_type=ADAPTIVE_RANGE_WALK_FORWARD_EVIDENCE_TYPE,
        pool_address=report.pool_address,
        as_of=report.as_of,
        status=report.status,
        qualified=report.research_qualified,
        evidence=report.to_record(),
    )
