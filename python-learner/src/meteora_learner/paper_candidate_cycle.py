from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .chain_features import fee_checkpoint_activity, summarize_liquidity_shape
from .chain_scan import scan_chain_candidates
from .ml_action_dataset import build_ml_action_dataset
from .paper_candidate_policy import (
    EmpiricalPaperCandidateSelection,
    PaperCandidateArm,
    candidate_context_key,
    select_empirical_paper_candidate,
)
from .research_store import ResearchStore
from .storage import Storage
from .strategy import StrategyType


PAPER_EMPIRICAL_CANDIDATE_CYCLE_EVIDENCE_TYPE = (
    "PAPER_EMPIRICAL_CANDIDATE_CYCLE_V1"
)


@dataclass(frozen=True)
class PaperCandidateContext:
    previous_observed_at: str
    decision_observed_at: str
    active_bin_id: int
    active_bin_move_1: int
    below_active_liquidity_ratio: float
    above_active_liquidity_ratio: float
    fee_growth_bins_x: int
    fee_growth_bins_y: int
    context_key: str


@dataclass(frozen=True)
class EmpiricalPaperCandidateCycleReport:
    pool_address: str
    decision_observed_at: str
    context: PaperCandidateContext
    history_examples: int
    history_decision_points: int
    history_candidates_dropped: int
    history_error: str | None
    current_candidates_seen: int
    current_candidates_accepted: int
    current_candidates_rejected: int
    selection: EmpiricalPaperCandidateSelection
    paper_only: bool
    policy_actionable: bool
    live_authorized: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("candidate cycle timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _decision_window(
    store: ResearchStore,
    *,
    pool_address: str,
    lookback_observations: int,
    as_of: str | None,
) -> tuple[list[str], str]:
    if lookback_observations < 2:
        raise ValueError("lookback_observations must be at least 2")

    times = store.chain_observation_times(
        pool_address,
        limit=None,
        ascending=True,
    )
    if as_of is not None:
        cutoff = _parse_time(as_of)
        times = [value for value in times if _parse_time(value) <= cutoff]
    if len(times) < lookback_observations:
        raise ValueError(
            "not enough chain observations for current PAPER candidate window"
        )
    selected = times[-lookback_observations:]
    return selected, selected[-1]


def _current_context(
    store: ResearchStore,
    *,
    pool_address: str,
    previous_observed_at: str,
    decision_observed_at: str,
    near_liquidity_radius: int,
) -> PaperCandidateContext:
    current_pool = store.chain_pool_snapshot_at(
        pool_address,
        decision_observed_at,
    )
    previous_pool = store.chain_pool_snapshot_at(
        pool_address,
        previous_observed_at,
    )
    current_bins = store.load_bin_liquidity(
        pool_address,
        observed_at=decision_observed_at,
    )
    previous_bins = store.load_bin_liquidity(
        pool_address,
        observed_at=previous_observed_at,
    )
    if (
        current_pool is None
        or previous_pool is None
        or not current_bins
        or not previous_bins
    ):
        raise ValueError("current PAPER candidate context is incomplete")

    active_bin_id = int(current_pool["active_bin_id"])
    active_bin_move_1 = (
        active_bin_id - int(previous_pool["active_bin_id"])
    )
    shape = summarize_liquidity_shape(
        current_bins,
        active_bin_id=active_bin_id,
        near_radius=near_liquidity_radius,
    )
    activity = fee_checkpoint_activity(previous_bins, current_bins)
    key = candidate_context_key(
        active_bin_move_1=active_bin_move_1,
        below_active_liquidity_ratio=shape.below_active_liquidity_ratio,
        above_active_liquidity_ratio=shape.above_active_liquidity_ratio,
        fee_growth_bins_x=activity.bins_with_x_growth,
        fee_growth_bins_y=activity.bins_with_y_growth,
    )
    return PaperCandidateContext(
        previous_observed_at=previous_observed_at,
        decision_observed_at=decision_observed_at,
        active_bin_id=active_bin_id,
        active_bin_move_1=active_bin_move_1,
        below_active_liquidity_ratio=shape.below_active_liquidity_ratio,
        above_active_liquidity_ratio=shape.above_active_liquidity_ratio,
        fee_growth_bins_x=activity.bins_with_x_growth,
        fee_growth_bins_y=activity.bins_with_y_growth,
        context_key=key,
    )


def run_empirical_paper_candidate_cycle(
    database_path: str | Path,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    network_cost_y_atomic: int,
    lookback_observations: int = 12,
    forward_observations: int = 2,
    step_observations: int | None = None,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Iterable[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
    near_liquidity_radius: int = 5,
    as_of: str | None = None,
) -> EmpiricalPaperCandidateCycleReport:
    """
    Build one evidence-driven, PAPER-only candidate selection.

    Historical examples are built only from chain observations at or before the
    selected decision snapshot. The selector independently rejects labels whose
    forward window ends after that decision, keeping the workflow no-lookahead.
    Current arms come from replay-valid chain candidates only. The returned
    report never authorizes live capital movement.
    """
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if amount_x < 0 or amount_y < 0 or (amount_x == 0 and amount_y == 0):
        raise ValueError("at least one non-negative token amount must be positive")
    if network_cost_y_atomic < 0:
        raise ValueError("network_cost_y_atomic cannot be negative")
    if near_liquidity_radius < 0:
        raise ValueError("near_liquidity_radius cannot be negative")

    strategy_values = tuple(StrategyType(value) for value in strategies)
    if not strategy_values:
        raise ValueError("at least one strategy is required")

    path = str(database_path)
    store = ResearchStore(path)
    current_times, decision_time = _decision_window(
        store,
        pool_address=pool_address,
        lookback_observations=lookback_observations,
        as_of=as_of,
    )
    context = _current_context(
        store,
        pool_address=pool_address,
        previous_observed_at=current_times[-2],
        decision_observed_at=decision_time,
        near_liquidity_radius=near_liquidity_radius,
    )

    current_scan = scan_chain_candidates(
        path,
        pool_address=pool_address,
        amount_x=amount_x,
        amount_y=amount_y,
        observation_limit=len(current_times),
        observation_times=current_times,
        half_widths=half_widths,
        center_offsets=center_offsets,
        strategies=strategy_values,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
    current_arms = tuple(
        PaperCandidateArm(
            strategy=item.strategy,
            half_width=item.half_width,
            center_offset=item.center_offset,
        )
        for item in current_scan.candidates
        if item.status == "ACCEPTED"
    )
    if not current_arms:
        raise ValueError("current PAPER candidate scan has no accepted arms")

    history_error = None
    try:
        history = build_ml_action_dataset(
            path,
            pool_address=pool_address,
            amount_x=amount_x,
            amount_y=amount_y,
            network_cost_y_atomic=network_cost_y_atomic,
            lookback_observations=lookback_observations,
            forward_observations=forward_observations,
            step_observations=step_observations,
            half_widths=half_widths,
            center_offsets=center_offsets,
            strategies=strategy_values,
            max_share_bps=max_share_bps,
            favor_x_in_active_bin=favor_x_in_active_bin,
            near_liquidity_radius=near_liquidity_radius,
            max_observed_at=decision_time,
        )
        examples = history.examples
        history_decision_points = history.decision_points
        history_candidates_dropped = history.candidates_dropped
    except ValueError as exc:
        if "not enough chain observations for action dataset" not in str(exc):
            raise
        history_error = str(exc)
        examples = ()
        history_decision_points = 0
        history_candidates_dropped = 0

    selection = select_empirical_paper_candidate(
        historical_examples=examples,
        pool_address=pool_address,
        decision_observed_at=decision_time,
        context_key=context.context_key,
        current_candidates=current_arms,
    )
    return EmpiricalPaperCandidateCycleReport(
        pool_address=pool_address,
        decision_observed_at=decision_time,
        context=context,
        history_examples=len(examples),
        history_decision_points=history_decision_points,
        history_candidates_dropped=history_candidates_dropped,
        history_error=history_error,
        current_candidates_seen=current_scan.attempted,
        current_candidates_accepted=current_scan.accepted,
        current_candidates_rejected=current_scan.rejected,
        selection=selection,
        paper_only=True,
        policy_actionable=False,
        live_authorized=False,
    )


def persist_empirical_paper_candidate_cycle(
    storage: Storage,
    *,
    report: EmpiricalPaperCandidateCycleReport,
) -> int:
    if not report.paper_only or report.policy_actionable or report.live_authorized:
        raise ValueError("empirical PAPER candidate report crossed safety boundary")
    return storage.save_advanced_edge_evidence(
        edge_type=PAPER_EMPIRICAL_CANDIDATE_CYCLE_EVIDENCE_TYPE,
        pool_address=report.pool_address,
        as_of=report.decision_observed_at,
        status=report.selection.status,
        qualified=False,
        evidence=report.to_record(),
    )
