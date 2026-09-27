from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .paper_candidate_cycle import (
    EmpiricalPaperCandidateCycleReport,
    run_empirical_paper_candidate_cycle,
)
from .paper_empirical_entry import (
    EmpiricalPaperEntryResult,
    open_empirical_paper_candidate,
)
from .storage import Storage


CandidateRunner = Callable[..., EmpiricalPaperCandidateCycleReport]
EntryOpener = Callable[..., EmpiricalPaperEntryResult]


@dataclass(frozen=True)
class EmpiricalPaperEntryWorkflowReport:
    account_id: str
    position_id: str
    event_key: str
    status: str
    cycle: EmpiricalPaperCandidateCycleReport
    entry: EmpiricalPaperEntryResult | None
    paper_only: bool
    live_authorized: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def run_empirical_paper_entry_workflow(
    storage: Storage,
    *,
    account_id: str,
    position_id: str,
    event_key: str,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    capital_quote: float,
    network_cost_y_atomic: int,
    entry_cost_quote: float = 0.0,
    lookback_observations: int = 12,
    forward_observations: int = 2,
    step_observations: int | None = None,
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
    as_of: str | None = None,
    candidate_runner: CandidateRunner = run_empirical_paper_candidate_cycle,
    entry_opener: EntryOpener = open_empirical_paper_candidate,
) -> EmpiricalPaperEntryWorkflowReport:
    """Select and open one virtual empirical PAPER position, never live capital."""
    if not account_id.strip():
        raise ValueError("account_id is required")
    if not position_id.strip():
        raise ValueError("position_id is required")
    if not event_key.strip():
        raise ValueError("event_key is required")

    cycle = candidate_runner(
        storage.path,
        pool_address=pool_address,
        amount_x=amount_x,
        amount_y=amount_y,
        network_cost_y_atomic=network_cost_y_atomic,
        lookback_observations=lookback_observations,
        forward_observations=forward_observations,
        step_observations=step_observations,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
        as_of=as_of,
    )
    if (
        not cycle.paper_only
        or cycle.policy_actionable
        or cycle.live_authorized
        or not cycle.selection.paper_only
        or cycle.selection.live_authorized
    ):
        raise ValueError("empirical entry workflow crossed PAPER-only boundary")

    if cycle.selection.selected_arm is None:
        return EmpiricalPaperEntryWorkflowReport(
            account_id=account_id,
            position_id=position_id,
            event_key=event_key,
            status="NO_SELECTION",
            cycle=cycle,
            entry=None,
            paper_only=True,
            live_authorized=False,
        )

    entry = entry_opener(
        storage,
        account_id=account_id,
        position_id=position_id,
        event_key=event_key,
        report=cycle,
        amount_x=amount_x,
        amount_y=amount_y,
        capital_quote=capital_quote,
        entry_cost_quote=entry_cost_quote,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
    if not entry.paper_only or entry.live_authorized:
        raise ValueError("empirical entry opener crossed PAPER-only boundary")
    return EmpiricalPaperEntryWorkflowReport(
        account_id=account_id,
        position_id=position_id,
        event_key=event_key,
        status=("OPENED" if entry.opened else "NOT_OPENED"),
        cycle=cycle,
        entry=entry,
        paper_only=True,
        live_authorized=False,
    )
