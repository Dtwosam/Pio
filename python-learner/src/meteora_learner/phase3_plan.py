from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .baseline_policy import (
    BaselinePolicyConfig,
    BaselineSelection,
    select_deterministic_baseline,
)
from .capital_sizing import (
    CapitalSizingConfig,
    CapitalSizingResult,
    size_position,
)
from .chain_scan import ChainScanResult, scan_chain_candidates
from .phase2_gate import Phase2PromotionGate
from .phase3_gate import Phase3EntryGate, evaluate_phase3_entry_gate
from .pool_safety import (
    PoolSafetyAssessment,
    PoolSafetyConfig,
    screen_pool_universe,
)
from .strategy import StrategyType


@dataclass(frozen=True)
class Phase3ResearchPlan:
    pool_address: str
    status: str
    pool_safety: PoolSafetyAssessment | None
    scan: ChainScanResult | None
    baseline: BaselineSelection | None
    sizing: CapitalSizingResult | None
    entry_gate: Phase3EntryGate | None
    policy_authorized: bool
    live_execution_ready: bool
    reasons: tuple[str, ...]
    amount_x: int = 0
    amount_y: int = 0
    decision_observed_at: str | None = None
    max_share_bps: int = 500
    favor_x_in_active_bin: bool = False

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_phase3_research_plan(
    database_path: str,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    requested_quote: float,
    account_equity_quote: float,
    cash_quote: float,
    current_deployed_quote: float,
    portfolio_drawdown_bps: int,
    phase2_gate: Phase2PromotionGate | None,
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
    baseline_config: BaselinePolicyConfig = BaselinePolicyConfig(),
    sizing_config: CapitalSizingConfig = CapitalSizingConfig(),
    observation_limit: int = 12,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Sequence[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> Phase3ResearchPlan:
    """
    Build one end-to-end deterministic entry research plan.

    This combines pool safety, chain replay candidate selection, capital sizing,
    and the Phase 3 authorization gate. It does not build or sign a transaction.
    """
    safety_report = screen_pool_universe(
        database_path,
        config=safety_config,
    )
    safety = next(
        (
            item
            for item in safety_report.assessments
            if item.pool_address == pool_address
        ),
        None,
    )
    if safety is None:
        return Phase3ResearchPlan(
            pool_address=pool_address,
            status="POOL_NOT_IN_LOCAL_UNIVERSE",
            pool_safety=None,
            scan=None,
            baseline=None,
            sizing=None,
            entry_gate=None,
            policy_authorized=False,
            live_execution_ready=False,
            reasons=("pool is not present in the latest normalized pool universe",),
        )

    if not safety.accepted:
        return Phase3ResearchPlan(
            pool_address=pool_address,
            status="POOL_REJECTED",
            pool_safety=safety,
            scan=None,
            baseline=None,
            sizing=None,
            entry_gate=None,
            policy_authorized=False,
            live_execution_ready=False,
            reasons=safety.rejection_reasons,
        )

    scan = scan_chain_candidates(
        database_path,
        pool_address=pool_address,
        amount_x=amount_x,
        amount_y=amount_y,
        observation_limit=observation_limit,
        half_widths=half_widths,
        center_offsets=center_offsets,
        strategies=strategies,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
    baseline = select_deterministic_baseline(
        database_path,
        scan=scan,
        phase2_gate=phase2_gate,
        config=baseline_config,
    )
    sizing = size_position(
        account_equity_quote=account_equity_quote,
        cash_quote=cash_quote,
        current_deployed_quote=current_deployed_quote,
        portfolio_drawdown_bps=portfolio_drawdown_bps,
        requested_quote=requested_quote,
        config=sizing_config,
    )
    entry_gate = evaluate_phase3_entry_gate(
        pool_safety=safety,
        baseline=baseline,
        sizing=sizing,
    )

    if entry_gate.entry_authorized:
        status = "POLICY_AUTHORIZED"
    elif entry_gate.research_ready:
        status = "RESEARCH_READY_PHASE2_BLOCKED"
    else:
        status = "ENTRY_REJECTED"

    return Phase3ResearchPlan(
        pool_address=pool_address,
        status=status,
        pool_safety=safety,
        scan=scan,
        baseline=baseline,
        sizing=sizing,
        entry_gate=entry_gate,
        policy_authorized=entry_gate.entry_authorized,
        live_execution_ready=False,
        reasons=entry_gate.reasons,
        amount_x=amount_x,
        amount_y=amount_y,
        decision_observed_at=scan.decision_observed_at,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
