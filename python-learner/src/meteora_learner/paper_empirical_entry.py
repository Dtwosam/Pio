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
from .paper_candidate_cycle import EmpiricalPaperCandidateCycleReport
from .paper_chain_valuation import (
    CounterfactualPlanPreview,
    PaperCounterfactualState,
    _insert_counterfactual_preview,
    prepare_counterfactual_candidate,
)
from .research_store import ResearchStore
from .storage import Storage


@dataclass(frozen=True)
class EmpiricalPaperEntryResult:
    opened: bool
    bound: bool
    reason: str | None
    position: PaperPositionSnapshot | None
    account: PaperAccountSnapshot
    counterfactual: PaperCounterfactualState | None
    paper_only: bool
    live_authorized: bool

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


def open_empirical_paper_candidate(
    storage: Storage,
    *,
    account_id: str,
    position_id: str,
    event_key: str,
    report: EmpiricalPaperCandidateCycleReport,
    amount_x: int,
    amount_y: int,
    capital_quote: float,
    entry_cost_quote: float = 0.0,
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> EmpiricalPaperEntryResult:
    """Open one chain-bound virtual position from a PAPER-only empirical choice."""
    account = paper_account_snapshot(storage, account_id=account_id)
    if (
        not report.paper_only
        or report.policy_actionable
        or report.live_authorized
        or not report.selection.paper_only
        or report.selection.live_authorized
    ):
        raise ValueError("empirical entry report crossed PAPER-only boundary")
    if report.selection.pool_address != report.pool_address:
        raise ValueError("candidate selection pool does not match cycle report")
    if (
        report.selection.decision_observed_at
        != report.decision_observed_at
    ):
        raise ValueError("candidate selection decision time does not match cycle")
    arm = report.selection.selected_arm
    if arm is None:
        return EmpiricalPaperEntryResult(
            opened=False,
            bound=False,
            reason=f"candidate cycle has no selected arm: {report.selection.status}",
            position=None,
            account=account,
            counterfactual=None,
            paper_only=True,
            live_authorized=False,
        )

    store = ResearchStore(storage.path)
    pool = store.chain_pool_snapshot_at(
        report.pool_address,
        report.decision_observed_at,
    )
    if pool is None:
        raise ValueError("decision-time chain pool snapshot is missing")
    active_bin_id = int(pool["active_bin_id"])
    center = active_bin_id + int(arm.center_offset)
    min_bin_id = center - int(arm.half_width)
    max_bin_id = center + int(arm.half_width)

    preview = prepare_counterfactual_candidate(
        storage,
        pool_address=report.pool_address,
        decision_observed_at=report.decision_observed_at,
        amount_x=amount_x,
        amount_y=amount_y,
        strategy=arm.strategy,
        min_bin_id=min_bin_id,
        max_bin_id=max_bin_id,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )

    capital = Decimal(str(capital_quote))
    cost = Decimal(str(entry_cost_quote))
    if not capital.is_finite() or capital <= 0:
        raise ValueError("capital_quote must be positive and finite")
    if not cost.is_finite() or cost < 0:
        raise ValueError("entry_cost_quote must be non-negative and finite")

    with storage.connect() as conn:
        _open_paper_position_in_conn(
            conn,
            event_key=event_key,
            account_id=account_id,
            position_id=position_id,
            pool_address=report.pool_address,
            policy_source="EMPIRICAL_PAPER",
            strategy=arm.strategy,
            min_bin_id=min_bin_id,
            max_bin_id=max_bin_id,
            capital=capital,
            cost=cost,
            model_id=None,
            event_time=report.decision_observed_at,
        )
        _insert_counterfactual_preview(
            conn,
            position_id=position_id,
            capital_quote=float(capital),
            preview=preview,
        )

    position = paper_position_snapshot(storage, position_id=position_id)
    return EmpiricalPaperEntryResult(
        opened=True,
        bound=True,
        reason=None,
        position=position,
        account=paper_account_snapshot(storage, account_id=account_id),
        counterfactual=_state_from_preview(
            position_id=position_id,
            capital_quote=float(capital),
            preview=preview,
        ),
        paper_only=True,
        live_authorized=False,
    )
