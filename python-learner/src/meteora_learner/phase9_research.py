from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Sequence

from .adaptive_range import AdaptiveRangeCriteria
from .adaptive_range_validation import (
    AdaptiveRangeValidationCriteria,
    AdaptiveRangeValidationReport,
    validate_adaptive_range_walk_forward,
)
from .phase8_validation import audit_persisted_phase8_promotion
from .market_regime import (
    DLMMRegimeCriteria,
    DLMMRegimeReport,
    classify_dlmm_regime,
)
from .storage import Storage


PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE = (
    "PHASE9_ADAPTIVE_MULTI_POOL_V1"
)


@dataclass(frozen=True)
class Phase9ResearchCriteria:
    min_pools: int = 3
    min_qualified_pools: int = 2
    min_qualified_pool_rate: float = 0.67
    min_mean_survival_uplift_vs_fixed: float = 0.0
    max_mean_width_multiple_vs_fixed: float = 2.0

    def __post_init__(self) -> None:
        if self.min_pools < 1:
            raise ValueError("min_pools must be positive")
        if self.min_qualified_pools < 1:
            raise ValueError("min_qualified_pools must be positive")
        if self.min_qualified_pools > self.min_pools:
            raise ValueError(
                "min_qualified_pools cannot exceed min_pools"
            )
        if not 0.0 <= self.min_qualified_pool_rate <= 1.0:
            raise ValueError(
                "min_qualified_pool_rate must be between 0 and 1"
            )
        if not -1.0 <= self.min_mean_survival_uplift_vs_fixed <= 1.0:
            raise ValueError(
                "min_mean_survival_uplift_vs_fixed must be between -1 and 1"
            )
        if self.max_mean_width_multiple_vs_fixed <= 0:
            raise ValueError(
                "max_mean_width_multiple_vs_fixed must be positive"
            )


@dataclass(frozen=True)
class Phase9PoolResearch:
    pool_address: str
    adaptive: AdaptiveRangeValidationReport
    regime: DLMMRegimeReport
    research_qualified: bool


@dataclass(frozen=True)
class Phase9ResearchReport:
    phase8_promoted: bool
    as_of: str | None
    research_only: bool
    policy_actionable: bool
    status: str
    pools_seen: int
    pools_qualified: int
    qualified_pool_rate: float
    mean_survival_uplift_vs_fixed: float | None
    mean_width_multiple_vs_fixed: float | None
    regimes_seen: tuple[str, ...]
    criteria: Phase9ResearchCriteria
    adaptive_criteria: AdaptiveRangeCriteria
    adaptive_validation_criteria: AdaptiveRangeValidationCriteria
    regime_criteria: DLMMRegimeCriteria
    research_qualified: bool
    reasons: tuple[str, ...]
    pools: tuple[Phase9PoolResearch, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase9_research(
    storage: Storage,
    *,
    pool_addresses: Sequence[str],
    criteria: Phase9ResearchCriteria = Phase9ResearchCriteria(),
    adaptive_criteria: AdaptiveRangeCriteria = AdaptiveRangeCriteria(),
    adaptive_validation_criteria: AdaptiveRangeValidationCriteria = (
        AdaptiveRangeValidationCriteria()
    ),
    regime_criteria: DLMMRegimeCriteria = DLMMRegimeCriteria(),
    as_of: str | None = None,
) -> Phase9ResearchReport:
    pools = tuple(
        sorted(
            {
                str(pool).strip()
                for pool in pool_addresses
                if str(pool).strip()
            }
        )
    )
    if not pools:
        raise ValueError("at least one pool_address is required")

    phase8_audit = audit_persisted_phase8_promotion(storage)
    phase8_promoted = phase8_audit.current

    pool_reports: list[Phase9PoolResearch] = []
    for pool in pools:
        adaptive = validate_adaptive_range_walk_forward(
            storage,
            pool_address=pool,
            adaptive_criteria=adaptive_criteria,
            validation_criteria=adaptive_validation_criteria,
            as_of=as_of,
        )
        regime = classify_dlmm_regime(
            storage,
            pool_address=pool,
            criteria=regime_criteria,
            as_of=as_of,
        )
        pool_reports.append(
            Phase9PoolResearch(
                pool_address=pool,
                adaptive=adaptive,
                regime=regime,
                research_qualified=(
                    adaptive.research_qualified
                    and regime.regime is not None
                ),
            )
        )

    qualified = [
        item for item in pool_reports if item.research_qualified
    ]
    qualified_rate = len(qualified) / len(pool_reports)

    uplifts = [
        item.adaptive.survival_uplift_vs_fixed
        for item in qualified
        if item.adaptive.survival_uplift_vs_fixed is not None
    ]
    width_multiples = [
        item.adaptive.mean_width_multiple_vs_fixed
        for item in qualified
        if item.adaptive.mean_width_multiple_vs_fixed is not None
    ]
    mean_uplift = (
        float(fmean(uplifts)) if uplifts else None
    )
    mean_width_multiple = (
        float(fmean(width_multiples))
        if width_multiples
        else None
    )
    regimes = tuple(
        sorted(
            {
                item.regime.regime
                for item in pool_reports
                if item.regime.regime is not None
            }
        )
    )

    reasons: list[str] = []
    checks = (
        (
            phase8_promoted,
            "Phase 8 promotion must still be current before Phase 9 research can qualify",
        ),
        (
            len(pool_reports) >= criteria.min_pools,
            f"pools seen {len(pool_reports)} are below {criteria.min_pools}",
        ),
        (
            len(qualified) >= criteria.min_qualified_pools,
            f"qualified pools {len(qualified)} are below "
            f"{criteria.min_qualified_pools}",
        ),
        (
            qualified_rate >= criteria.min_qualified_pool_rate,
            f"qualified pool rate {qualified_rate:.6f} is below "
            f"{criteria.min_qualified_pool_rate:.6f}",
        ),
        (
            mean_uplift is not None
            and mean_uplift
            >= criteria.min_mean_survival_uplift_vs_fixed,
            "mean adaptive survival uplift versus fixed is below required minimum",
        ),
        (
            mean_width_multiple is not None
            and mean_width_multiple
            <= criteria.max_mean_width_multiple_vs_fixed,
            "mean adaptive width multiple versus fixed is too large",
        ),
    )
    reasons.extend(message for passed, message in checks if not passed)
    if not phase8_promoted:
        reasons.extend(
            f"Phase 8 currentness: {reason}"
            for reason in phase8_audit.reasons
        )
    research_qualified = not reasons

    if not phase8_promoted:
        status = "RESEARCH_ONLY_PHASE8_BLOCKED"
    elif research_qualified:
        status = "QUALIFIED_RESEARCH"
    else:
        status = "NOT_QUALIFIED"

    return Phase9ResearchReport(
        phase8_promoted=phase8_promoted,
        as_of=as_of,
        research_only=True,
        policy_actionable=False,
        status=status,
        pools_seen=len(pool_reports),
        pools_qualified=len(qualified),
        qualified_pool_rate=qualified_rate,
        mean_survival_uplift_vs_fixed=mean_uplift,
        mean_width_multiple_vs_fixed=mean_width_multiple,
        regimes_seen=regimes,
        criteria=criteria,
        adaptive_criteria=adaptive_criteria,
        adaptive_validation_criteria=adaptive_validation_criteria,
        regime_criteria=regime_criteria,
        research_qualified=research_qualified,
        reasons=tuple(reasons),
        pools=tuple(pool_reports),
    )


def persist_phase9_research(
    storage: Storage,
    *,
    report: Phase9ResearchReport,
) -> int:
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
        pool_address="__MULTI_POOL__",
        as_of=report.as_of,
        status=report.status,
        qualified=report.research_qualified,
        evidence=report.to_record(),
    )
