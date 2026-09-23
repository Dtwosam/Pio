from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
from typing import Any

from .cross_pool_research import CrossPoolResearchReport
from .phase8_validation import audit_persisted_phase8_promotion
from .storage import Storage


BPS = Decimal(10_000)
PORTFOLIO_ALLOCATION_EVIDENCE_TYPE = "PHASE9_PORTFOLIO_ALLOCATION_V1"
PORTFOLIO_CANDIDATE_EVIDENCE_TYPE = "PHASE9_PORTFOLIO_CANDIDATES_V1"


@dataclass(frozen=True)
class PortfolioAllocationCriteria:
    max_positions: int = 3
    min_positions: int = 2
    max_pool_allocation_bps: int = 4_000
    min_range_survival_ratio: float = 0.75
    min_excess_vs_hold_bps: int = 0
    min_position_quote: float = 10.0
    min_budget_utilization_rate: float = 0.75

    def __post_init__(self) -> None:
        if self.max_positions < 1:
            raise ValueError("max_positions must be positive")
        if self.min_positions < 1:
            raise ValueError("min_positions must be positive")
        if self.min_positions > self.max_positions:
            raise ValueError(
                "min_positions cannot exceed max_positions"
            )
        if not 1 <= self.max_pool_allocation_bps <= 10_000:
            raise ValueError(
                "max_pool_allocation_bps must be between 1 and 10000"
            )
        if not 0.0 <= self.min_range_survival_ratio <= 1.0:
            raise ValueError(
                "min_range_survival_ratio must be between 0 and 1"
            )
        if self.min_position_quote < 0:
            raise ValueError("min_position_quote cannot be negative")
        if not 0.0 <= self.min_budget_utilization_rate <= 1.0:
            raise ValueError(
                "min_budget_utilization_rate must be between 0 and 1"
            )


@dataclass(frozen=True)
class PortfolioAllocationItem:
    rank: int
    pool_address: str
    strategy: str
    requested_cap_quote: float
    allocation_quote: float
    allocation_bps_of_budget: int
    excess_vs_hold_initial_bps: int
    range_survival_ratio: float


@dataclass(frozen=True)
class PortfolioAllocationReport:
    phase8_promoted: bool
    research_only: bool
    policy_actionable: bool
    status: str
    budget_quote: float
    allocated_quote: float
    unallocated_quote: float
    budget_utilization_rate: float
    eligible_candidates: int
    selected_positions: int
    max_observed_allocation_bps: int
    criteria: PortfolioAllocationCriteria
    research_qualified: bool
    reasons: tuple[str, ...]
    allocations: tuple[PortfolioAllocationItem, ...]
    candidate_lineage: dict[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _d(value: float | int) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("allocation values must be finite")
    return result


def _water_fill(
    caps: list[Decimal],
    budget: Decimal,
) -> list[Decimal]:
    allocations = [Decimal(0) for _ in caps]
    active = {index for index, cap in enumerate(caps) if cap > 0}
    remaining = budget

    while active and remaining > 0:
        share = remaining / Decimal(len(active))
        progressed = False
        for index in tuple(sorted(active)):
            room = caps[index] - allocations[index]
            amount = min(share, room)
            if amount > 0:
                allocations[index] += amount
                remaining -= amount
                progressed = True
            if allocations[index] >= caps[index]:
                active.remove(index)
        if not progressed:
            break

    return allocations


def research_portfolio_allocation(
    storage: Storage,
    *,
    comparison: CrossPoolResearchReport,
    budget_quote: float,
    criteria: PortfolioAllocationCriteria = PortfolioAllocationCriteria(),
    candidate_lineage: dict[str, Any] | None = None,
) -> PortfolioAllocationReport:
    if budget_quote <= 0:
        raise ValueError("budget_quote must be positive")

    candidate_pools = [
        item.pool_address for item in comparison.candidates
    ]
    if len(candidate_pools) != len(set(candidate_pools)):
        raise ValueError(
            "duplicate pool_address candidates are not allowed"
        )

    phase8_audit = audit_persisted_phase8_promotion(storage)
    phase8_promoted = phase8_audit.current
    budget = _d(budget_quote)
    pool_cap = (
        budget
        * Decimal(criteria.max_pool_allocation_bps)
        / BPS
    )
    min_position = _d(criteria.min_position_quote)

    eligible = [
        item
        for item in comparison.candidates
        if item.policy_authorized
        and item.sized_quote > 0
        and item.range_survival_ratio
            >= criteria.min_range_survival_ratio
        and item.excess_vs_hold_initial_bps
            >= criteria.min_excess_vs_hold_bps
    ]
    selected = eligible[: criteria.max_positions]

    caps: list[Decimal] = []
    kept = []
    for candidate in selected:
        cap = min(_d(candidate.sized_quote), pool_cap)
        if cap < min_position:
            continue
        kept.append(candidate)
        caps.append(cap)

    raw_allocations = _water_fill(caps, budget)
    allocation_items: list[PortfolioAllocationItem] = []
    for candidate, allocation in zip(kept, raw_allocations):
        if allocation < min_position:
            continue
        bps = int(
            allocation * BPS / budget
        )
        allocation_items.append(
            PortfolioAllocationItem(
                rank=candidate.rank,
                pool_address=candidate.pool_address,
                strategy=candidate.strategy,
                requested_cap_quote=float(
                    min(
                        _d(candidate.sized_quote),
                        pool_cap,
                    )
                ),
                allocation_quote=float(allocation),
                allocation_bps_of_budget=bps,
                excess_vs_hold_initial_bps=(
                    candidate.excess_vs_hold_initial_bps
                ),
                range_survival_ratio=(
                    candidate.range_survival_ratio
                ),
            )
        )

    allocated = sum(
        (_d(item.allocation_quote) for item in allocation_items),
        Decimal(0),
    )
    allocated = min(allocated, budget)
    unallocated = budget - allocated
    utilization = float(allocated / budget)
    max_allocation_bps = max(
        (
            item.allocation_bps_of_budget
            for item in allocation_items
        ),
        default=0,
    )

    reasons: list[str] = []
    checks = (
        (
            phase8_promoted,
            "Phase 8 promotion must still be current before Phase 9 allocation research can qualify",
        ),
        (
            len(allocation_items) >= criteria.min_positions,
            f"selected positions {len(allocation_items)} are below "
            f"{criteria.min_positions}",
        ),
        (
            utilization >= criteria.min_budget_utilization_rate,
            f"budget utilization {utilization:.6f} is below "
            f"{criteria.min_budget_utilization_rate:.6f}",
        ),
        (
            max_allocation_bps
            <= criteria.max_pool_allocation_bps,
            "portfolio allocation exceeded per-pool concentration cap",
        ),
    )
    reasons.extend(message for passed, message in checks if not passed)
    if not phase8_promoted:
        reasons.extend(
            f"Phase 8 currentness: {reason}"
            for reason in phase8_audit.reasons
        )
    qualified = not reasons

    if not phase8_promoted:
        status = "RESEARCH_ONLY_PHASE8_BLOCKED"
    elif not allocation_items:
        status = "NO_ELIGIBLE_ALLOCATIONS"
    elif qualified:
        status = "QUALIFIED_RESEARCH"
    else:
        status = "NOT_QUALIFIED"

    return PortfolioAllocationReport(
        phase8_promoted=phase8_promoted,
        research_only=True,
        policy_actionable=False,
        status=status,
        budget_quote=float(budget),
        allocated_quote=float(allocated),
        unallocated_quote=float(unallocated),
        budget_utilization_rate=utilization,
        eligible_candidates=len(eligible),
        selected_positions=len(allocation_items),
        max_observed_allocation_bps=max_allocation_bps,
        criteria=criteria,
        research_qualified=qualified,
        reasons=tuple(reasons),
        allocations=tuple(allocation_items),
        candidate_lineage=candidate_lineage,
    )


def portfolio_candidate_artifact_sha256(
    payload: dict[str, Any],
) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def persist_portfolio_candidate_research(
    storage: Storage,
    *,
    comparison: CrossPoolResearchReport,
    source_inputs: list[dict[str, Any]],
    assumptions: dict[str, Any],
) -> tuple[int, str]:
    payload = {
        "research_only": True,
        "policy_actionable": False,
        "source_inputs": source_inputs,
        "assumptions": assumptions,
        "comparison": comparison.to_record(),
    }
    digest = portfolio_candidate_artifact_sha256(payload)
    evidence = {
        "artifact_sha256": digest,
        **payload,
    }
    evidence_id = storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO_CANDIDATES__",
        as_of=None,
        status="BUILT",
        qualified=bool(comparison.candidates),
        evidence=evidence,
    )
    return evidence_id, digest


def persist_portfolio_allocation_research(
    storage: Storage,
    *,
    report: PortfolioAllocationReport,
) -> int:
    return storage.save_advanced_edge_evidence(
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
        pool_address="__PORTFOLIO__",
        as_of=None,
        status=report.status,
        qualified=report.research_qualified,
        evidence=report.to_record(),
    )
