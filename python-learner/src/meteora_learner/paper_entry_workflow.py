from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .baseline_policy import BaselinePolicyConfig
from .capital_sizing import CapitalSizingConfig
from .paper_account import paper_account_snapshot
from .paper_entry import BoundPaperEntryResult, open_bound_deterministic_paper_plan
from .phase3_plan import Phase3ResearchPlan, build_phase3_research_plan
from .pool_safety import PoolSafetyConfig
from .storage import Storage
from .strategy import StrategyType


@dataclass(frozen=True)
class PaperPhase3EntryWorkflowResult:
    account_id: str
    position_id: str
    current_deployed_quote: float
    plan: Phase3ResearchPlan
    entry: BoundPaperEntryResult

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def paper_deployed_capital_quote(
    storage: Storage,
    *,
    account_id: str,
) -> float:
    with storage.connect() as conn:
        account = conn.execute(
            "SELECT 1 FROM paper_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError(f"unknown paper account: {account_id}")
        row = conn.execute(
            """
            SELECT COALESCE(SUM(CAST(entry_capital_quote AS REAL)), 0)
            FROM paper_positions
            WHERE account_id = ? AND status = 'OPEN'
            """,
            (account_id,),
        ).fetchone()
    return float(row[0] or 0.0)


def build_and_open_bound_phase3_paper_entry(
    storage: Storage,
    *,
    account_id: str,
    position_id: str,
    event_key: str,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    requested_quote: float,
    network_cost_y_atomic: int,
    entry_cost_quote: float = 0.0,
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
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
) -> PaperPhase3EntryWorkflowResult:
    """
    Build a deterministic Phase 3 plan from persisted state and open it in paper.

    Account equity, cash, drawdown and current deployed capital come from the
    persistent paper ledger. Phase 2/3 readiness comes from persisted promotion
    evidence. Callers cannot inject those state values.
    """
    account = paper_account_snapshot(storage, account_id=account_id)
    deployed = paper_deployed_capital_quote(
        storage,
        account_id=account_id,
    )

    plan = build_phase3_research_plan(
        str(storage.path),
        pool_address=pool_address,
        amount_x=amount_x,
        amount_y=amount_y,
        requested_quote=requested_quote,
        account_equity_quote=account.account_equity_quote,
        cash_quote=account.cash_quote,
        current_deployed_quote=deployed,
        portfolio_drawdown_bps=account.drawdown_bps,
        phase2_gate=None,
        safety_config=safety_config,
        baseline_config=BaselinePolicyConfig(
            estimated_network_cost_y_atomic=network_cost_y_atomic,
        ),
        sizing_config=sizing_config,
        observation_limit=observation_limit,
        half_widths=half_widths,
        center_offsets=center_offsets,
        strategies=strategies,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
    entry = open_bound_deterministic_paper_plan(
        storage,
        account_id=account_id,
        position_id=position_id,
        event_key=event_key,
        plan=plan,
        entry_cost_quote=entry_cost_quote,
    )
    return PaperPhase3EntryWorkflowResult(
        account_id=account_id,
        position_id=position_id,
        current_deployed_quote=deployed,
        plan=plan,
        entry=entry,
    )
