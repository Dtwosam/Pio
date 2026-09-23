from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from .paper_account import (
    PaperAccountSnapshot,
    PaperPositionSnapshot,
    _open_paper_position_in_conn,
    paper_account_snapshot,
    paper_position_snapshot,
)
from .paper_chain_valuation import (
    CounterfactualPlanPreview,
    PaperCounterfactualState,
    _insert_counterfactual_preview,
    prepare_counterfactual_plan,
)
from .phase3_plan import Phase3ResearchPlan
from .phase_promotion import PHASE3, PHASE3_EVIDENCE_TYPE
from .storage import Storage


@dataclass(frozen=True)
class BoundPaperEntryResult:
    opened: bool
    bound: bool
    reason: str | None
    position: PaperPositionSnapshot | None
    account: PaperAccountSnapshot
    counterfactual: PaperCounterfactualState | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _state_from_preview(
    *,
    position_id: str,
    capital_quote: float,
    preview: CounterfactualPlanPreview,
) -> PaperCounterfactualState:
    return PaperCounterfactualState(
        position_id=position_id,
        pool_address=preview.pool_address,
        entry_observed_at=preview.entry_observed_at,
        amount_x_atomic=preview.amount_x_atomic,
        amount_y_atomic=preview.amount_y_atomic,
        idle_x_atomic=preview.idle_x_atomic,
        idle_y_atomic=preview.idle_y_atomic,
        entry_price_q64=preview.entry_price_q64,
        entry_value_y_atomic=preview.entry_value_y_atomic,
        capital_quote=capital_quote,
        max_share_bps=preview.max_share_bps,
        favor_x_active=preview.favor_x_active,
        bins=preview.bins,
    )


def open_bound_deterministic_paper_plan(
    storage: Storage,
    *,
    account_id: str,
    position_id: str,
    event_key: str,
    plan: Phase3ResearchPlan,
    entry_cost_quote: float = 0.0,
) -> BoundPaperEntryResult:
    """
    Atomically open and chain-bind one promoted deterministic Phase 3 paper plan.

    Counterfactual math is preflighted before the transaction. Cash debit,
    paper-position creation, ENTER event and counterfactual state then commit
    together, so a binding failure cannot leave a half-open paper position.
    """
    account = paper_account_snapshot(storage, account_id=account_id)
    if not storage.phase_is_promoted(
        PHASE3,
        evidence_type=PHASE3_EVIDENCE_TYPE,
    ):
        return BoundPaperEntryResult(
            opened=False,
            bound=False,
            reason="Phase 3 deterministic policy is not promoted",
            position=None,
            account=account,
            counterfactual=None,
        )
    if not plan.policy_authorized or plan.entry_gate is None:
        return BoundPaperEntryResult(
            opened=False,
            bound=False,
            reason="Phase 3 plan is not policy-authorized",
            position=None,
            account=account,
            counterfactual=None,
        )
    if plan.entry_gate.proposal is None:
        return BoundPaperEntryResult(
            opened=False,
            bound=False,
            reason="Phase 3 plan has no actionable proposal",
            position=None,
            account=account,
            counterfactual=None,
        )
    capital_quote = float(plan.entry_gate.sized_quote)
    if capital_quote <= 0:
        return BoundPaperEntryResult(
            opened=False,
            bound=False,
            reason="Phase 3 plan has no deployable capital",
            position=None,
            account=account,
            counterfactual=None,
        )

    preview = prepare_counterfactual_plan(storage, plan=plan)
    proposal = plan.entry_gate.proposal
    event_time = preview.entry_observed_at

    with storage.connect() as conn:
        _open_paper_position_in_conn(
            conn,
            event_key=event_key,
            account_id=account_id,
            position_id=position_id,
            pool_address=plan.pool_address,
            policy_source="DETERMINISTIC",
            strategy=proposal.strategy,
            min_bin_id=proposal.min_bin_id,
            max_bin_id=proposal.max_bin_id,
            capital=Decimal(str(capital_quote)),
            cost=Decimal(str(entry_cost_quote)),
            model_id=None,
            event_time=event_time,
        )
        _insert_counterfactual_preview(
            conn,
            position_id=position_id,
            capital_quote=capital_quote,
            preview=preview,
        )

    position = paper_position_snapshot(storage, position_id=position_id)
    return BoundPaperEntryResult(
        opened=True,
        bound=True,
        reason=None,
        position=position,
        account=paper_account_snapshot(storage, account_id=account_id),
        counterfactual=_state_from_preview(
            position_id=position_id,
            capital_quote=capital_quote,
            preview=preview,
        ),
    )
