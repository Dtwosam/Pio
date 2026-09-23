from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .baseline_policy import replay_economics
from .chain_features import fee_checkpoint_activity, summarize_liquidity_shape
from .chain_replay import replay_small_lp_history
from .chain_scan import ChainCandidateOutcome, scan_chain_candidates
from .composition_fee import FEE_PRECISION
from .ml_dataset import MLTrainingExample
from .research_store import ResearchStore
from .strategy import StrategyType


@dataclass(frozen=True)
class MLActionDatasetReport:
    pool_address: str
    decision_points: int
    candidates_seen: int
    examples_built: int
    candidates_dropped: int
    drop_reasons: tuple[tuple[str, int], ...]
    examples: tuple[MLTrainingExample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def _active_price(
    store: ResearchStore,
    *,
    pool_address: str,
    observed_at: str,
    active_bin_id: int,
) -> int:
    row = store.bin_liquidity_at(
        pool_address,
        observed_at=observed_at,
        bin_id=active_bin_id,
    )
    if row is None:
        raise ValueError("active-bin price missing")
    return int(str(row["price"]))


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


def build_ml_action_dataset(
    database_path: str,
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
    strategies: Sequence[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
    near_liquidity_radius: int = 5,
) -> MLActionDatasetReport:
    """
    Label every replay-valid candidate action at each no-lookahead decision point.

    The trailing window creates action features. Each action is then recentered
    at the decision active bin and replayed only over the forward window.
    """
    if network_cost_y_atomic < 0:
        raise ValueError("network_cost_y_atomic cannot be negative")
    if lookback_observations < 2 or forward_observations < 2:
        raise ValueError("lookback and forward observations must be at least 2")
    if near_liquidity_radius < 0:
        raise ValueError("near_liquidity_radius cannot be negative")

    forward_intervals = forward_observations - 1
    if step_observations is None:
        step_observations = forward_intervals
    if step_observations < forward_intervals:
        raise ValueError("forward evaluation windows cannot overlap")

    store = ResearchStore(database_path)
    times = store.chain_observation_times(
        pool_address,
        limit=None,
        ascending=True,
    )
    if len(times) < lookback_observations + forward_intervals:
        raise ValueError("not enough chain observations for action dataset")

    examples: list[MLTrainingExample] = []
    drops: dict[str, int] = {}
    candidates_seen = 0
    decision_points = 0

    decision_index = lookback_observations - 1
    while decision_index + forward_observations <= len(times):
        decision_points += 1
        training_times = times[
            decision_index - lookback_observations + 1 : decision_index + 1
        ]
        forward_times = times[
            decision_index : decision_index + forward_observations
        ]
        previous_time = times[decision_index - 1]
        decision_time = times[decision_index]

        current_pool = store.chain_pool_snapshot_at(pool_address, decision_time)
        previous_pool = store.chain_pool_snapshot_at(pool_address, previous_time)
        current_bins = store.load_bin_liquidity(
            pool_address,
            observed_at=decision_time,
        )
        previous_bins = store.load_bin_liquidity(
            pool_address,
            observed_at=previous_time,
        )
        if (
            current_pool is None
            or previous_pool is None
            or not current_bins
            or not previous_bins
        ):
            raise ValueError("decision-time chain state is incomplete")

        active_id = int(current_pool["active_bin_id"])
        shape = summarize_liquidity_shape(
            current_bins,
            active_bin_id=active_id,
            near_radius=near_liquidity_radius,
        )
        activity = fee_checkpoint_activity(previous_bins, current_bins)
        total_liquidity = shape.total_liquidity_supply
        active_ratio = (
            shape.active_liquidity_supply / total_liquidity
            if total_liquidity > 0
            else 0.0
        )
        fee_rate_raw = current_pool.get("deposit_total_fee_rate")
        if fee_rate_raw is None:
            raise ValueError("decision-time deposit fee rate is missing")
        fee_rate_bps = int(str(fee_rate_raw)) * 10_000 / FEE_PRECISION

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

        for candidate in scan.candidates:
            candidates_seen += 1
            if candidate.status != "ACCEPTED" or candidate.replay is None:
                _bump(drops, "trailing_replay_rejected")
                continue
            if candidate.range_survival_ratio is None:
                _bump(drops, "trailing_survival_missing")
                continue

            try:
                trailing = replay_economics(
                    candidate,
                    entry_price_q64=_active_price(
                        store,
                        pool_address=pool_address,
                        observed_at=candidate.replay.start_observed_at,
                        active_bin_id=candidate.replay.start_active_bin_id,
                    ),
                    exit_price_q64=_active_price(
                        store,
                        pool_address=pool_address,
                        observed_at=candidate.replay.end_observed_at,
                        active_bin_id=candidate.replay.end_active_bin_id,
                    ),
                    network_cost_y_atomic=network_cost_y_atomic,
                )
            except ValueError:
                _bump(drops, "trailing_economics_incomplete")
                continue

            if (
                trailing.excess_vs_hold_bps is None
                or trailing.net_return_bps is None
            ):
                _bump(drops, "trailing_normalized_economics_missing")
                continue
            if trailing.has_unvalued_rewards:
                _bump(drops, "trailing_rewards_unvalued")
                continue

            center = scan.decision_active_bin_id + candidate.center_offset
            forward_min = center - candidate.half_width
            forward_max = center + candidate.half_width

            try:
                forward_replay = replay_small_lp_history(
                    database_path,
                    pool_address=pool_address,
                    amount_x=amount_x,
                    amount_y=amount_y,
                    min_bin_id=forward_min,
                    max_bin_id=forward_max,
                    strategy=StrategyType(candidate.strategy),
                    observation_limit=len(forward_times),
                    observation_times=forward_times,
                    max_share_bps=max_share_bps,
                    favor_x_in_active_bin=favor_x_in_active_bin,
                )
                forward_survival = _survival_ratio(
                    start_active_bin_id=forward_replay.start_active_bin_id,
                    intervals=forward_replay.intervals,
                    min_bin_id=forward_min,
                    max_bin_id=forward_max,
                )
                forward_candidate = ChainCandidateOutcome(
                    strategy=candidate.strategy,
                    half_width=candidate.half_width,
                    center_offset=candidate.center_offset,
                    min_bin_id=forward_min,
                    max_bin_id=forward_max,
                    status="ACCEPTED",
                    rejection_reason=None,
                    range_survival_ratio=forward_survival,
                    replay=forward_replay,
                )
                forward = replay_economics(
                    forward_candidate,
                    entry_price_q64=_active_price(
                        store,
                        pool_address=pool_address,
                        observed_at=forward_replay.start_observed_at,
                        active_bin_id=forward_replay.start_active_bin_id,
                    ),
                    exit_price_q64=_active_price(
                        store,
                        pool_address=pool_address,
                        observed_at=forward_replay.end_observed_at,
                        active_bin_id=forward_replay.end_active_bin_id,
                    ),
                    network_cost_y_atomic=network_cost_y_atomic,
                )
            except ValueError:
                _bump(drops, "forward_replay_rejected")
                continue

            if (
                forward.net_return_bps is None
                or forward.excess_vs_hold_initial_bps is None
            ):
                _bump(drops, "forward_normalized_targets_missing")
                continue
            if forward.has_unvalued_rewards:
                _bump(drops, "forward_rewards_unvalued")
                continue

            strategy = candidate.strategy
            examples.append(
                MLTrainingExample(
                    pool_address=pool_address,
                    decision_observed_at=decision_time,
                    forward_end_observed_at=forward_times[-1],
                    strategy=strategy,
                    strategy_spot=int(strategy == "SPOT"),
                    strategy_curve=int(strategy == "CURVE"),
                    strategy_bid_ask=int(strategy == "BID_ASK"),
                    half_width=candidate.half_width,
                    center_offset=candidate.center_offset,
                    range_width_bins=forward_max - forward_min + 1,
                    active_bin_id=active_id,
                    active_bin_move_1=(
                        active_id - int(previous_pool["active_bin_id"])
                    ),
                    deposit_fee_rate_bps=float(fee_rate_bps),
                    occupied_bins=shape.occupied_bins,
                    active_liquidity_ratio=float(active_ratio),
                    near_active_liquidity_ratio=shape.near_active_liquidity_ratio,
                    below_active_liquidity_ratio=shape.below_active_liquidity_ratio,
                    above_active_liquidity_ratio=shape.above_active_liquidity_ratio,
                    liquidity_weighted_distance_bins=(
                        shape.liquidity_weighted_distance_bins
                    ),
                    fee_growth_bins_x=activity.bins_with_x_growth,
                    fee_growth_bins_y=activity.bins_with_y_growth,
                    trailing_range_survival_ratio=candidate.range_survival_ratio,
                    trailing_excess_vs_hold_bps=trailing.excess_vs_hold_bps,
                    trailing_net_return_bps=trailing.net_return_bps,
                    trailing_max_observed_share_bps=(
                        candidate.replay.max_observed_share_bps
                    ),
                    target_net_return_bps=forward.net_return_bps,
                    target_excess_vs_hold_bps=(
                        forward.excess_vs_hold_initial_bps
                    ),
                    target_range_survival_ratio=forward_survival,
                    target_positive_excess=int(
                        forward.excess_vs_hold_initial_bps > 0
                    ),
                )
            )

        decision_index += step_observations

    return MLActionDatasetReport(
        pool_address=pool_address,
        decision_points=decision_points,
        candidates_seen=candidates_seen,
        examples_built=len(examples),
        candidates_dropped=candidates_seen - len(examples),
        drop_reasons=tuple(sorted(drops.items())),
        examples=tuple(examples),
    )
