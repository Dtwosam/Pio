from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .baseline_policy import BaselinePolicyConfig
from .capital_sizing import CapitalSizingConfig
from .cross_pool_research import (
    CrossPoolResearchReport,
    compare_phase3_research_plans,
)
from .phase2_gate import Phase2PromotionGate
from .phase3_plan import Phase3ResearchPlan, build_phase3_research_plan
from .pool_safety import PoolSafetyConfig
from .strategy import StrategyType


@dataclass(frozen=True)
class PoolResearchInput:
    pool_address: str
    amount_x: int
    amount_y: int
    requested_quote: float
    network_cost_y_atomic: int

    def __post_init__(self) -> None:
        if not self.pool_address:
            raise ValueError("pool_address is required")
        if self.amount_x < 0 or self.amount_y < 0:
            raise ValueError("token amounts cannot be negative")
        if self.amount_x == 0 and self.amount_y == 0:
            raise ValueError("at least one token amount must be positive")
        if self.requested_quote <= 0:
            raise ValueError("requested_quote must be positive")
        if self.network_cost_y_atomic < 0:
            raise ValueError("network_cost_y_atomic cannot be negative")


@dataclass(frozen=True)
class MultiPoolResearchReport:
    requested_quote: float
    pools_requested: int
    research_ready_pools: int
    policy_authorized_pools: int
    leader_pool_address: str | None
    live_execution_ready: bool
    comparison: CrossPoolResearchReport
    plans: tuple[Phase3ResearchPlan, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _common_requested_quote(inputs: Sequence[PoolResearchInput]) -> float:
    if not inputs:
        raise ValueError("at least one pool research input is required")
    target = inputs[0].requested_quote
    for item in inputs[1:]:
        tolerance = max(1e-9, abs(target) * 1e-9)
        if abs(item.requested_quote - target) > tolerance:
            raise ValueError(
                "cross-pool research requires the same requested_quote "
                "for every pool"
            )
    return target


def build_multi_pool_research(
    database_path: str,
    *,
    inputs: Sequence[PoolResearchInput],
    account_equity_quote: float,
    cash_quote: float,
    current_deployed_quote: float,
    portfolio_drawdown_bps: int,
    phase2_gate: Phase2PromotionGate | None,
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
    sizing_config: CapitalSizingConfig = CapitalSizingConfig(),
    observation_limit: int = 12,
    observation_times_by_pool: Mapping[str, Sequence[str]] | None = None,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Sequence[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> MultiPoolResearchReport:
    requested_quote = _common_requested_quote(inputs)
    plans: list[Phase3ResearchPlan] = []

    if observation_times_by_pool is not None:
        unknown = sorted(
            set(observation_times_by_pool)
            - {item.pool_address for item in inputs}
        )
        if unknown:
            raise ValueError(
                "observation_times_by_pool contains unknown pools: "
                + ", ".join(unknown)
            )

    for item in inputs:
        plan = build_phase3_research_plan(
            database_path,
            pool_address=item.pool_address,
            amount_x=item.amount_x,
            amount_y=item.amount_y,
            requested_quote=item.requested_quote,
            account_equity_quote=account_equity_quote,
            cash_quote=cash_quote,
            current_deployed_quote=current_deployed_quote,
            portfolio_drawdown_bps=portfolio_drawdown_bps,
            phase2_gate=phase2_gate,
            safety_config=safety_config,
            baseline_config=BaselinePolicyConfig(
                estimated_network_cost_y_atomic=item.network_cost_y_atomic,
            ),
            sizing_config=sizing_config,
            observation_limit=observation_limit,
            observation_times=(
                observation_times_by_pool.get(item.pool_address)
                if observation_times_by_pool is not None
                else None
            ),
            half_widths=half_widths,
            center_offsets=center_offsets,
            strategies=strategies,
            max_share_bps=max_share_bps,
            favor_x_in_active_bin=favor_x_in_active_bin,
        )
        plans.append(plan)

    comparison = compare_phase3_research_plans(plans)
    return MultiPoolResearchReport(
        requested_quote=requested_quote,
        pools_requested=len(inputs),
        research_ready_pools=sum(
            item.entry_gate is not None and item.entry_gate.research_ready
            for item in plans
        ),
        policy_authorized_pools=sum(item.policy_authorized for item in plans),
        leader_pool_address=comparison.leader_pool_address,
        live_execution_ready=False,
        comparison=comparison,
        plans=tuple(plans),
    )
