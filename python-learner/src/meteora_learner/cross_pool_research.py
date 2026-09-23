from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .phase3_plan import Phase3ResearchPlan


@dataclass(frozen=True)
class CrossPoolResearchCandidate:
    rank: int
    pool_address: str
    strategy: str
    min_bin_id: int
    max_bin_id: int
    half_width: int
    center_offset: int
    net_return_bps: int
    hold_return_bps: int
    excess_vs_hold_initial_bps: int
    range_survival_ratio: float
    max_observed_share_bps: int
    sized_quote: float
    phase2_ready: bool
    policy_authorized: bool


@dataclass(frozen=True)
class CrossPoolResearchReport:
    plans_seen: int
    comparable_plans: int
    excluded_plans: int
    leader_pool_address: str | None
    ranking_rule: str
    candidates: tuple[CrossPoolResearchCandidate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def compare_phase3_research_plans(
    plans: Sequence[Phase3ResearchPlan],
) -> CrossPoolResearchReport:
    """
    Rank comparable Phase 3 research plans using dimensionless economics.

    Input plans are expected to have been built with comparable capital/notional
    assumptions. Raw token amounts are never used as a cross-pool ranking key.
    """
    comparable: list[tuple[Phase3ResearchPlan, Any, Any]] = []

    for plan in plans:
        if plan.pool_safety is None or not plan.pool_safety.accepted:
            continue
        if plan.baseline is None or plan.baseline.research_choice is None:
            continue
        choice = plan.baseline.research_choice
        economics = choice.economics
        if economics is None:
            continue
        if (
            economics.net_return_bps is None
            or economics.hold_return_bps is None
            or economics.excess_vs_hold_initial_bps is None
            or choice.range_survival_ratio is None
            or choice.max_observed_share_bps is None
        ):
            continue
        comparable.append((plan, choice, economics))

    ordered = sorted(
        comparable,
        key=lambda item: (
            item[2].excess_vs_hold_initial_bps,
            item[2].net_return_bps,
            item[1].range_survival_ratio,
            -item[1].max_observed_share_bps,
            -item[1].half_width,
            -abs(item[1].center_offset),
            item[0].pool_address,
        ),
        reverse=True,
    )

    candidates = tuple(
        CrossPoolResearchCandidate(
            rank=index,
            pool_address=plan.pool_address,
            strategy=choice.strategy,
            min_bin_id=choice.min_bin_id,
            max_bin_id=choice.max_bin_id,
            half_width=choice.half_width,
            center_offset=choice.center_offset,
            net_return_bps=economics.net_return_bps,
            hold_return_bps=economics.hold_return_bps,
            excess_vs_hold_initial_bps=economics.excess_vs_hold_initial_bps,
            range_survival_ratio=choice.range_survival_ratio,
            max_observed_share_bps=choice.max_observed_share_bps,
            sized_quote=(
                plan.sizing.sized_quote if plan.sizing is not None else 0.0
            ),
            phase2_ready=(
                plan.entry_gate.phase2_ready
                if plan.entry_gate is not None
                else False
            ),
            policy_authorized=plan.policy_authorized,
        )
        for index, (plan, choice, economics) in enumerate(ordered, start=1)
    )

    return CrossPoolResearchReport(
        plans_seen=len(plans),
        comparable_plans=len(candidates),
        excluded_plans=len(plans) - len(candidates),
        leader_pool_address=(
            candidates[0].pool_address if candidates else None
        ),
        ranking_rule=(
            "excess_vs_hold_initial_bps, net_return_bps, range_survival, "
            "lower counterfactual share, narrower range"
        ),
        candidates=candidates,
    )
