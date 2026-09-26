from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .baseline_policy import q64_value_in_y_atomic
from .chain_replay import (
    continue_small_lp_replay,
    replay_small_lp_history,
)
from .research_store import ResearchStore
from .strategy import StrategyType


@dataclass(frozen=True)
class PaperManagementActionExample:
    pool_address: str
    decision_observed_at: str
    forward_end_observed_at: str
    action: str
    strategy: str
    half_width: int
    center_offset: int
    position_age_observations: int
    entry_active_bin_id: int
    decision_active_bin_id: int
    active_bin_move_since_entry: int
    current_min_bin_id: int
    current_max_bin_id: int
    current_in_range: bool
    distance_to_nearest_edge: int | None
    action_min_bin_id: int | None
    action_max_bin_id: int | None
    decision_inventory_x: int
    decision_inventory_y: int
    decision_value_y_atomic: int
    transition_cost_y_atomic: int
    composition_cost_y_atomic: int
    forward_fee_value_y_atomic: int
    target_action_value_y_atomic: int
    target_action_return_bps: int
    target_excess_vs_hold_bps: int
    target_range_survival_ratio: float | None
    replay_fidelity: str


@dataclass(frozen=True)
class PaperManagementActionDatasetReport:
    pool_address: str
    strategy: str
    half_width: int
    center_offset: int
    position_age_observations: int
    forward_observations: int
    decisions_seen: int
    decisions_built: int
    decisions_dropped: int
    examples_built: int
    drop_reasons: tuple[tuple[str, int], ...]
    research_only: bool
    policy_actionable: bool
    examples: tuple[PaperManagementActionExample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("management dataset timestamps require timezone")
    return parsed.astimezone(timezone.utc)


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
        raise ValueError(
            f"active-bin price missing at {observed_at} for bin {active_bin_id}"
        )
    price = int(str(row["price"]))
    if price <= 0:
        raise ValueError("active-bin Q64 price must be positive")
    return price


def _range_survival(
    active_ids: list[int],
    *,
    min_bin_id: int,
    max_bin_id: int,
) -> float:
    if not active_ids:
        raise ValueError("range survival requires active-bin observations")
    return sum(
        min_bin_id <= active_id <= max_bin_id
        for active_id in active_ids
    ) / len(active_ids)


def _return_bps(*, ending_value: int, starting_value: int) -> int:
    if starting_value <= 0:
        raise ValueError("starting action value must be positive")
    return (ending_value - starting_value) * 10_000 // starting_value


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def build_paper_management_action_dataset(
    database_path: str,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    strategy: StrategyType | str,
    half_width: int,
    rebalance_transition_cost_y_atomic: int,
    exit_transition_cost_y_atomic: int,
    center_offset: int = 0,
    position_age_observations: int = 1,
    forward_observations: int = 2,
    step_observations: int = 1,
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
    max_observed_at: str | None = None,
) -> PaperManagementActionDatasetReport:
    """
    Build no-lookahead PAPER management labels for HOLD / REBALANCE / EXIT.

    Each decision first creates a hypothetical existing position using only
    observations up to the decision time. HOLD then continues the exact minted
    shares forward. REBALANCE explicitly withdraws to the decision inventory
    and redeposits that inventory around the decision active bin. EXIT holds the
    same underlying inventory outside the LP over the forward window.

    Transition costs are caller-supplied measured values in token-Y atomic
    units. No economic threshold or action selection is performed here.

    Accrued pre-decision fees/rewards are common sunk state and are excluded
    from all three forward labels. A decision is dropped if either LP action
    earns unvalued forward rewards.
    """
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if amount_x < 0 or amount_y < 0:
        raise ValueError("amounts cannot be negative")
    if amount_x == 0 and amount_y == 0:
        raise ValueError("at least one token amount must be positive")
    if half_width < 0:
        raise ValueError("half_width cannot be negative")
    if position_age_observations < 1:
        raise ValueError("position_age_observations must be positive")
    if forward_observations < 2:
        raise ValueError("forward_observations must be at least 2")
    if step_observations < 1:
        raise ValueError("step_observations must be positive")
    if rebalance_transition_cost_y_atomic < 0:
        raise ValueError(
            "rebalance_transition_cost_y_atomic cannot be negative"
        )
    if exit_transition_cost_y_atomic < 0:
        raise ValueError("exit_transition_cost_y_atomic cannot be negative")
    if not 1 <= max_share_bps <= 10_000:
        raise ValueError("max_share_bps must be between 1 and 10000")

    strategy_value = StrategyType(strategy)
    store = ResearchStore(database_path)
    times = store.chain_observation_times(
        pool_address,
        limit=None,
        ascending=True,
    )
    if max_observed_at is not None:
        cutoff = _parse_time(max_observed_at)
        times = [
            observed_at
            for observed_at in times
            if _parse_time(observed_at) <= cutoff
        ]

    minimum = position_age_observations + forward_observations
    if len(times) < minimum:
        raise ValueError(
            "not enough chain observations for management action dataset"
        )

    examples: list[PaperManagementActionExample] = []
    drops: dict[str, int] = {}
    decisions_seen = 0
    decisions_built = 0

    last_decision_index = len(times) - forward_observations
    for decision_index in range(
        position_age_observations,
        last_decision_index + 1,
        step_observations,
    ):
        decisions_seen += 1
        entry_index = decision_index - position_age_observations
        entry_times = times[entry_index : decision_index + 1]
        forward_times = times[
            decision_index : decision_index + forward_observations
        ]
        decision_time = times[decision_index]
        forward_end = forward_times[-1]

        try:
            entry_pool = store.chain_pool_snapshot_at(
                pool_address,
                entry_times[0],
            )
            decision_pool = store.chain_pool_snapshot_at(
                pool_address,
                decision_time,
            )
            if entry_pool is None or decision_pool is None:
                raise ValueError("entry/decision pool snapshot missing")

            entry_active = int(entry_pool["active_bin_id"])
            decision_active = int(decision_pool["active_bin_id"])
            current_center = entry_active + center_offset
            current_min = current_center - half_width
            current_max = current_center + half_width

            prior = replay_small_lp_history(
                database_path,
                pool_address=pool_address,
                amount_x=amount_x,
                amount_y=amount_y,
                min_bin_id=current_min,
                max_bin_id=current_max,
                strategy=strategy_value,
                observation_times=entry_times,
                observation_limit=len(entry_times),
                max_share_bps=max_share_bps,
                favor_x_in_active_bin=favor_x_in_active_bin,
            )

            decision_x = prior.ending_x + prior.idle_x
            decision_y = prior.ending_y + prior.idle_y
            decision_price = _active_price(
                store,
                pool_address=pool_address,
                observed_at=decision_time,
                active_bin_id=decision_active,
            )
            decision_value = q64_value_in_y_atomic(
                amount_x=decision_x,
                amount_y=decision_y,
                price_q64=decision_price,
            )
            if decision_value <= 0:
                raise ValueError("decision inventory has no positive value")

            hold = continue_small_lp_replay(
                database_path,
                prior=prior,
                observation_times=forward_times,
                max_share_bps=max_share_bps,
            )

            rebalance_center = decision_active + center_offset
            rebalance_min = rebalance_center - half_width
            rebalance_max = rebalance_center + half_width
            rebalance = replay_small_lp_history(
                database_path,
                pool_address=pool_address,
                amount_x=decision_x,
                amount_y=decision_y,
                min_bin_id=rebalance_min,
                max_bin_id=rebalance_max,
                strategy=strategy_value,
                observation_times=forward_times,
                observation_limit=len(forward_times),
                max_share_bps=max_share_bps,
                favor_x_in_active_bin=favor_x_in_active_bin,
            )

            if (
                hold.reward_one
                or hold.reward_two
                or rebalance.reward_one
                or rebalance.reward_two
            ):
                raise ValueError(
                    "forward rewards are nonzero but not quote-valued"
                )

            forward_end_pool = store.chain_pool_snapshot_at(
                pool_address,
                forward_end,
            )
            if forward_end_pool is None:
                raise ValueError("forward-end pool snapshot missing")
            forward_end_active = int(
                forward_end_pool["active_bin_id"]
            )
            exit_price = _active_price(
                store,
                pool_address=pool_address,
                observed_at=forward_end,
                active_bin_id=forward_end_active,
            )

            hold_inventory_value = q64_value_in_y_atomic(
                amount_x=hold.ending_x + hold.idle_x,
                amount_y=hold.ending_y + hold.idle_y,
                price_q64=exit_price,
            )
            hold_fee_value = q64_value_in_y_atomic(
                amount_x=hold.fee_x,
                amount_y=hold.fee_y,
                price_q64=exit_price,
            )
            hold_value = hold_inventory_value + hold_fee_value

            rebalance_inventory_value = q64_value_in_y_atomic(
                amount_x=rebalance.ending_x + rebalance.idle_x,
                amount_y=rebalance.ending_y + rebalance.idle_y,
                price_q64=exit_price,
            )
            rebalance_fee_value = q64_value_in_y_atomic(
                amount_x=rebalance.fee_x,
                amount_y=rebalance.fee_y,
                price_q64=exit_price,
            )
            rebalance_composition_value = q64_value_in_y_atomic(
                amount_x=rebalance.entry_composition_fee_x,
                amount_y=rebalance.entry_composition_fee_y,
                price_q64=exit_price,
            )
            rebalance_value = (
                rebalance_inventory_value
                + rebalance_fee_value
                - rebalance_composition_value
                - rebalance_transition_cost_y_atomic
            )

            exit_hold_value = q64_value_in_y_atomic(
                amount_x=decision_x,
                amount_y=decision_y,
                price_q64=exit_price,
            )
            exit_value = (
                exit_hold_value - exit_transition_cost_y_atomic
            )

            forward_active_ids = [
                int(
                    store.chain_pool_snapshot_at(
                        pool_address,
                        observed_at,
                    )["active_bin_id"]
                )
                for observed_at in forward_times
            ]
            current_in_range = (
                current_min <= decision_active <= current_max
            )
            edge_distance = (
                min(
                    decision_active - current_min,
                    current_max - decision_active,
                )
                if current_in_range
                else None
            )
            common = {
                "pool_address": pool_address,
                "decision_observed_at": decision_time,
                "forward_end_observed_at": forward_end,
                "strategy": strategy_value.value,
                "half_width": half_width,
                "center_offset": center_offset,
                "position_age_observations": position_age_observations,
                "entry_active_bin_id": entry_active,
                "decision_active_bin_id": decision_active,
                "active_bin_move_since_entry": (
                    decision_active - entry_active
                ),
                "current_min_bin_id": current_min,
                "current_max_bin_id": current_max,
                "current_in_range": current_in_range,
                "distance_to_nearest_edge": edge_distance,
                "decision_inventory_x": decision_x,
                "decision_inventory_y": decision_y,
                "decision_value_y_atomic": decision_value,
            }

            hold_return = _return_bps(
                ending_value=hold_value,
                starting_value=decision_value,
            )
            examples.append(
                PaperManagementActionExample(
                    **common,
                    action="HOLD",
                    action_min_bin_id=current_min,
                    action_max_bin_id=current_max,
                    transition_cost_y_atomic=0,
                    composition_cost_y_atomic=0,
                    forward_fee_value_y_atomic=hold_fee_value,
                    target_action_value_y_atomic=hold_value,
                    target_action_return_bps=hold_return,
                    target_excess_vs_hold_bps=0,
                    target_range_survival_ratio=_range_survival(
                        forward_active_ids,
                        min_bin_id=current_min,
                        max_bin_id=current_max,
                    ),
                    replay_fidelity=hold.replay_fidelity,
                )
            )
            examples.append(
                PaperManagementActionExample(
                    **common,
                    action="REBALANCE",
                    action_min_bin_id=rebalance_min,
                    action_max_bin_id=rebalance_max,
                    transition_cost_y_atomic=(
                        rebalance_transition_cost_y_atomic
                    ),
                    composition_cost_y_atomic=(
                        rebalance_composition_value
                    ),
                    forward_fee_value_y_atomic=rebalance_fee_value,
                    target_action_value_y_atomic=rebalance_value,
                    target_action_return_bps=_return_bps(
                        ending_value=rebalance_value,
                        starting_value=decision_value,
                    ),
                    target_excess_vs_hold_bps=(
                        (rebalance_value - hold_value)
                        * 10_000
                        // decision_value
                    ),
                    target_range_survival_ratio=_range_survival(
                        forward_active_ids,
                        min_bin_id=rebalance_min,
                        max_bin_id=rebalance_max,
                    ),
                    replay_fidelity=rebalance.replay_fidelity,
                )
            )
            examples.append(
                PaperManagementActionExample(
                    **common,
                    action="EXIT",
                    action_min_bin_id=None,
                    action_max_bin_id=None,
                    transition_cost_y_atomic=(
                        exit_transition_cost_y_atomic
                    ),
                    composition_cost_y_atomic=0,
                    forward_fee_value_y_atomic=0,
                    target_action_value_y_atomic=exit_value,
                    target_action_return_bps=_return_bps(
                        ending_value=exit_value,
                        starting_value=decision_value,
                    ),
                    target_excess_vs_hold_bps=(
                        (exit_value - hold_value)
                        * 10_000
                        // decision_value
                    ),
                    target_range_survival_ratio=None,
                    replay_fidelity="UNDERLYING_HOLD_AFTER_EXIT_V1",
                )
            )
            decisions_built += 1
        except ValueError as exc:
            _bump(drops, str(exc))

    return PaperManagementActionDatasetReport(
        pool_address=pool_address,
        strategy=strategy_value.value,
        half_width=half_width,
        center_offset=center_offset,
        position_age_observations=position_age_observations,
        forward_observations=forward_observations,
        decisions_seen=decisions_seen,
        decisions_built=decisions_built,
        decisions_dropped=decisions_seen - decisions_built,
        examples_built=len(examples),
        drop_reasons=tuple(sorted(drops.items())),
        research_only=True,
        policy_actionable=False,
        examples=tuple(examples),
    )
