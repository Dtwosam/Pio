from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Sequence

from .baseline_policy import (
    BaselinePolicyConfig,
    BaselineProposal,
    ReplayEconomicMetrics,
    replay_economics,
    select_deterministic_baseline,
)
from .chain_replay import replay_small_lp_history
from .chain_scan import ChainCandidateOutcome, scan_chain_candidates
from .phase2_gate import Phase2PromotionGate
from .research_store import ResearchStore
from .strategy import StrategyType


@dataclass(frozen=True)
class WalkForwardStep:
    decision_observed_at: str
    training_start_observed_at: str
    forward_end_observed_at: str
    phase2_ready: bool
    selected: bool
    proposal: BaselineProposal | None
    forward_status: str
    forward_rejection_reason: str | None
    forward_range_survival_ratio: float | None
    forward_economics: ReplayEconomicMetrics | None


@dataclass(frozen=True)
class BaselineWalkForwardReport:
    pool_address: str
    observation_count: int
    lookback_observations: int
    forward_observations: int
    step_observations: int
    steps: int
    selected_steps: int
    forward_valid_steps: int
    economically_complete_steps: int
    positive_excess_steps: int
    total_excess_vs_hold_y_atomic: int | None
    mean_excess_vs_hold_bps: float | None
    phase2_ready: bool
    research_only: bool
    steps_detail: tuple[WalkForwardStep, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _survival_ratio(
    *,
    start_active_bin_id: int,
    intervals: Sequence[Any],
    min_bin_id: int,
    max_bin_id: int,
) -> float:
    active_ids = [start_active_bin_id]
    active_ids.extend(int(item.end_active_bin_id) for item in intervals)
    return sum(
        min_bin_id <= active_id <= max_bin_id
        for active_id in active_ids
    ) / len(active_ids)


def walk_forward_baseline(
    database_path: str,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    phase2_gate: Phase2PromotionGate | None,
    config: BaselinePolicyConfig,
    lookback_observations: int = 12,
    forward_observations: int = 2,
    step_observations: int | None = None,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Sequence[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> BaselineWalkForwardReport:
    """
    Walk forward without using future observations during candidate selection.

    Each training window ends at the decision observation. The chosen shape is
    re-centered there, then evaluated on a forward window beginning at that same
    state and containing only later observations. Forward windows do not overlap.
    """
    if lookback_observations < 2:
        raise ValueError("lookback_observations must be at least 2")
    if forward_observations < 2:
        raise ValueError("forward_observations must be at least 2")

    forward_intervals = forward_observations - 1
    if step_observations is None:
        step_observations = forward_intervals
    if step_observations < forward_intervals:
        raise ValueError(
            "step_observations must be at least forward_observations - 1 "
            "to prevent overlapping forward evaluation"
        )

    store = ResearchStore(database_path)
    times = store.chain_observation_times(
        pool_address,
        limit=None,
        ascending=True,
    )
    minimum = lookback_observations + forward_intervals
    if len(times) < minimum:
        raise ValueError(
            f"need at least {minimum} chain observations for walk-forward evaluation"
        )

    phase2_ready = bool(
        phase2_gate is not None and phase2_gate.promotion_ready
    )

    steps: list[WalkForwardStep] = []
    decision_index = lookback_observations - 1
    while decision_index + forward_observations <= len(times):
        training_times = times[
            decision_index - lookback_observations + 1 : decision_index + 1
        ]
        forward_times = times[
            decision_index : decision_index + forward_observations
        ]

        scan = scan_chain_candidates(
            database_path,
            pool_address=pool_address,
            amount_x=amount_x,
            amount_y=amount_y,
            observation_limit=len(training_times),
            observation_times=training_times,
            half_widths=half_widths,
            center_offsets=center_offsets,
            strategies=strategies,
            max_share_bps=max_share_bps,
            favor_x_in_active_bin=favor_x_in_active_bin,
        )
        selection = select_deterministic_baseline(
            database_path,
            scan=scan,
            phase2_gate=phase2_gate,
            config=config,
        )
        proposal = selection.research_proposal

        if proposal is None:
            steps.append(
                WalkForwardStep(
                    decision_observed_at=training_times[-1],
                    training_start_observed_at=training_times[0],
                    forward_end_observed_at=forward_times[-1],
                    phase2_ready=phase2_ready,
                    selected=False,
                    proposal=None,
                    forward_status="NO_SELECTION",
                    forward_rejection_reason=(
                        "no trailing candidate passed deterministic baseline rules"
                    ),
                    forward_range_survival_ratio=None,
                    forward_economics=None,
                )
            )
            decision_index += step_observations
            continue

        try:
            replay = replay_small_lp_history(
                database_path,
                pool_address=pool_address,
                amount_x=amount_x,
                amount_y=amount_y,
                min_bin_id=proposal.min_bin_id,
                max_bin_id=proposal.max_bin_id,
                strategy=StrategyType(proposal.strategy),
                observation_limit=len(forward_times),
                observation_times=forward_times,
                max_share_bps=max_share_bps,
                favor_x_in_active_bin=favor_x_in_active_bin,
            )
            survival = _survival_ratio(
                start_active_bin_id=replay.start_active_bin_id,
                intervals=replay.intervals,
                min_bin_id=proposal.min_bin_id,
                max_bin_id=proposal.max_bin_id,
            )
            forward_candidate = ChainCandidateOutcome(
                strategy=proposal.strategy,
                half_width=proposal.half_width,
                center_offset=proposal.center_offset,
                min_bin_id=proposal.min_bin_id,
                max_bin_id=proposal.max_bin_id,
                status="ACCEPTED",
                rejection_reason=None,
                range_survival_ratio=survival,
                replay=replay,
            )
            entry_price_row = store.bin_liquidity_at(
                pool_address,
                observed_at=replay.start_observed_at,
                bin_id=replay.start_active_bin_id,
            )
            exit_price_row = store.bin_liquidity_at(
                pool_address,
                observed_at=replay.end_observed_at,
                bin_id=replay.end_active_bin_id,
            )
            if entry_price_row is None or exit_price_row is None:
                raise ValueError("active-bin price missing in forward evaluation")
            economics = replay_economics(
                forward_candidate,
                entry_price_q64=int(str(entry_price_row["price"])),
                exit_price_q64=int(str(exit_price_row["price"])),
                network_cost_y_atomic=config.estimated_network_cost_y_atomic,
            )
        except ValueError as exc:
            steps.append(
                WalkForwardStep(
                    decision_observed_at=training_times[-1],
                    training_start_observed_at=training_times[0],
                    forward_end_observed_at=forward_times[-1],
                    phase2_ready=phase2_ready,
                    selected=True,
                    proposal=proposal,
                    forward_status="REJECTED",
                    forward_rejection_reason=str(exc),
                    forward_range_survival_ratio=None,
                    forward_economics=None,
                )
            )
        else:
            status = "VALID"
            reason = None
            if config.reject_unvalued_rewards and economics.has_unvalued_rewards:
                status = "ECONOMICS_INCOMPLETE"
                reason = "forward reward income has no token-Y valuation"
            elif economics.network_cost_y_atomic is None:
                status = "ECONOMICS_INCOMPLETE"
                reason = "forward network cost has no token-Y valuation"

            steps.append(
                WalkForwardStep(
                    decision_observed_at=training_times[-1],
                    training_start_observed_at=training_times[0],
                    forward_end_observed_at=forward_times[-1],
                    phase2_ready=phase2_ready,
                    selected=True,
                    proposal=proposal,
                    forward_status=status,
                    forward_rejection_reason=reason,
                    forward_range_survival_ratio=survival,
                    forward_economics=economics,
                )
            )

        decision_index += step_observations

    complete = [
        item
        for item in steps
        if item.forward_economics is not None
        and item.forward_economics.excess_vs_hold_y_atomic is not None
        and item.forward_status == "VALID"
    ]
    bps = [
        item.forward_economics.excess_vs_hold_bps
        for item in complete
        if item.forward_economics is not None
        and item.forward_economics.excess_vs_hold_bps is not None
    ]
    excess = [
        item.forward_economics.excess_vs_hold_y_atomic
        for item in complete
        if item.forward_economics is not None
        and item.forward_economics.excess_vs_hold_y_atomic is not None
    ]

    return BaselineWalkForwardReport(
        pool_address=pool_address,
        observation_count=len(times),
        lookback_observations=lookback_observations,
        forward_observations=forward_observations,
        step_observations=step_observations,
        steps=len(steps),
        selected_steps=sum(item.selected for item in steps),
        forward_valid_steps=sum(item.forward_status == "VALID" for item in steps),
        economically_complete_steps=len(complete),
        positive_excess_steps=sum(value > 0 for value in excess),
        total_excess_vs_hold_y_atomic=sum(excess) if excess else None,
        mean_excess_vs_hold_bps=float(mean(bps)) if bps else None,
        phase2_ready=phase2_ready,
        research_only=not phase2_ready,
        steps_detail=tuple(steps),
    )
