from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .chain_replay import SmallLPReplayResult, replay_small_lp_history
from .research_store import ResearchStore
from .strategy import StrategyType


@dataclass(frozen=True)
class RebalanceSegmentResult:
    entry_observed_at: str
    exit_observed_at: str
    entry_active_bin_id: int
    exit_active_bin_id: int
    min_bin_id: int
    max_bin_id: int
    exit_reason: str
    input_x: int
    input_y: int
    ending_position_x: int
    ending_position_y: int
    idle_x: int
    idle_y: int
    carry_x: int
    carry_y: int
    fee_x: int
    fee_y: int
    reward_one: int
    reward_two: int
    entry_composition_fee_x: int
    entry_composition_fee_y: int
    replay_fidelity: str


@dataclass(frozen=True)
class RebalanceLifecycleResult:
    pool_address: str
    start_observed_at: str
    end_observed_at: str
    observation_count: int
    initial_x: int
    initial_y: int
    final_principal_x: int
    final_principal_y: int
    fee_x: int
    fee_y: int
    reward_one: int
    reward_two: int
    entry_composition_fee_x: int
    entry_composition_fee_y: int
    entries: int
    rebalances: int
    half_width: int
    center_offset: int
    strategy: str
    reinvest_fees: bool
    lifecycle_fidelity: str
    segments: tuple[RebalanceSegmentResult, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def replay_rebalance_lifecycle(
    database_path: str,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    half_width: int,
    strategy: StrategyType | str,
    center_offset: int = 0,
    observation_limit: int = 24,
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
    reinvest_fees: bool = False,
) -> RebalanceLifecycleResult:
    """
    Replay a deterministic out-of-range rebalance policy on chain snapshots.

    A position holds fixed shares until the observed active bin exits its range.
    At that observation boundary it is withdrawn and, when future observations
    remain, re-entered around the new active bin. Position fee earnings and
    rewards remain separate from principal unless a future validated compounding
    mode is added.

    This model does not infer the exact intra-interval exit time or transaction
    slippage. It is an observation-boundary lifecycle model.
    """
    if amount_x < 0 or amount_y < 0:
        raise ValueError("amounts cannot be negative")
    if amount_x == 0 and amount_y == 0:
        raise ValueError("at least one token amount must be positive")
    if half_width < 0:
        raise ValueError("half_width cannot be negative")
    if observation_limit < 2:
        raise ValueError("observation_limit must be at least 2")
    if reinvest_fees:
        raise ValueError(
            "fee reinvestment is not validated yet; use reinvest_fees=False"
        )

    strategy = StrategyType(strategy)
    store = ResearchStore(database_path)
    times_desc = store.chain_observation_times(
        pool_address,
        limit=observation_limit,
    )
    if len(times_desc) < 2:
        raise ValueError("need at least two chain observations for lifecycle replay")
    times = list(reversed(times_desc))

    pools: list[dict[str, Any]] = []
    for observed_at in times:
        snapshot = store.chain_pool_snapshot_at(pool_address, observed_at)
        if snapshot is None:
            raise ValueError(f"missing chain pool snapshot at {observed_at}")
        pools.append(snapshot)

    principal_x = amount_x
    principal_y = amount_y
    total_fee_x = 0
    total_fee_y = 0
    total_reward_one = 0
    total_reward_two = 0
    total_composition_x = 0
    total_composition_y = 0
    segments: list[RebalanceSegmentResult] = []
    rebalances = 0

    start_index = 0
    while start_index < len(times) - 1:
        entry_active = int(pools[start_index]["active_bin_id"])
        center = entry_active + center_offset
        min_bin_id = center - half_width
        max_bin_id = center + half_width

        end_index = len(times) - 1
        exit_reason = "END_OF_WINDOW"
        for index in range(start_index + 1, len(times)):
            active = int(pools[index]["active_bin_id"])
            if active < min_bin_id or active > max_bin_id:
                end_index = index
                exit_reason = "OUT_OF_RANGE"
                break

        replay: SmallLPReplayResult = replay_small_lp_history(
            database_path,
            pool_address=pool_address,
            amount_x=principal_x,
            amount_y=principal_y,
            min_bin_id=min_bin_id,
            max_bin_id=max_bin_id,
            strategy=strategy,
            observation_times=times[start_index : end_index + 1],
            max_share_bps=max_share_bps,
            favor_x_in_active_bin=favor_x_in_active_bin,
        )

        carry_x = replay.ending_x + replay.idle_x
        carry_y = replay.ending_y + replay.idle_y
        total_fee_x += replay.fee_x
        total_fee_y += replay.fee_y
        total_reward_one += replay.reward_one
        total_reward_two += replay.reward_two
        total_composition_x += replay.entry_composition_fee_x
        total_composition_y += replay.entry_composition_fee_y

        segments.append(
            RebalanceSegmentResult(
                entry_observed_at=times[start_index],
                exit_observed_at=times[end_index],
                entry_active_bin_id=entry_active,
                exit_active_bin_id=int(pools[end_index]["active_bin_id"]),
                min_bin_id=min_bin_id,
                max_bin_id=max_bin_id,
                exit_reason=exit_reason,
                input_x=principal_x,
                input_y=principal_y,
                ending_position_x=replay.ending_x,
                ending_position_y=replay.ending_y,
                idle_x=replay.idle_x,
                idle_y=replay.idle_y,
                carry_x=carry_x,
                carry_y=carry_y,
                fee_x=replay.fee_x,
                fee_y=replay.fee_y,
                reward_one=replay.reward_one,
                reward_two=replay.reward_two,
                entry_composition_fee_x=replay.entry_composition_fee_x,
                entry_composition_fee_y=replay.entry_composition_fee_y,
                replay_fidelity=replay.replay_fidelity,
            )
        )

        principal_x = carry_x
        principal_y = carry_y

        if exit_reason != "OUT_OF_RANGE" or end_index >= len(times) - 1:
            break

        rebalances += 1
        start_index = end_index

    return RebalanceLifecycleResult(
        pool_address=pool_address,
        start_observed_at=times[0],
        end_observed_at=times[-1],
        observation_count=len(times),
        initial_x=amount_x,
        initial_y=amount_y,
        final_principal_x=principal_x,
        final_principal_y=principal_y,
        fee_x=total_fee_x,
        fee_y=total_fee_y,
        reward_one=total_reward_one,
        reward_two=total_reward_two,
        entry_composition_fee_x=total_composition_x,
        entry_composition_fee_y=total_composition_y,
        entries=len(segments),
        rebalances=rebalances,
        half_width=half_width,
        center_offset=center_offset,
        strategy=strategy.value,
        reinvest_fees=False,
        lifecycle_fidelity="OBSERVATION_BOUNDARY_REBALANCE_V1",
        segments=tuple(segments),
    )
