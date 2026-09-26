from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .baseline_policy import q64_value_in_y_atomic
from .chain_replay import (
    SmallLPReplayResult,
    continue_small_lp_replay,
    replay_small_lp_history,
)
from .quote_registry import DEFAULT_QUOTE_UNIT, token_quote_status
from .research_store import ResearchStore
from .storage import Storage
from .strategy import StrategyType


PAPER_HOLD_VS_REBALANCE_EVIDENCE_TYPE = (
    "PAPER_HOLD_VS_REBALANCE_GROSS_REPLAY_V1"
)


@dataclass(frozen=True)
class PaperHoldVsRebalanceReplayReport:
    pool_address: str
    decision_observed_at: str
    end_observed_at: str
    strategy: str
    min_bin_id: int
    max_bin_id: int
    decision_active_bin_id: int
    rebalance_lower_offset_bins: int
    rebalance_upper_offset_bins: int
    start_x: int
    start_y: int
    start_value_y_atomic: int
    hold_inventory_fee_value_y_atomic: int
    rebalance_inventory_fee_value_y_atomic: int
    rebalance_composition_cost_y_atomic: int
    rebalance_value_after_composition_y_atomic: int
    gross_advantage_before_transition_cost_y_atomic: int
    gross_advantage_before_transition_cost_bps: int | None
    quote_unit: str
    token_y_mint: str | None
    end_y_quote_per_atomic: float | None
    end_y_quote_source: str | None
    end_y_quote_observed_at: str | None
    end_y_quote_age_seconds: int | None
    quote_normalization_complete: bool
    quote_exclusion_reason: str | None
    hold_inventory_fee_value_quote: float | None
    rebalance_inventory_fee_value_quote: float | None
    rebalance_composition_cost_quote: float | None
    gross_advantage_before_transition_cost_quote: float | None
    hold_reward_one: int
    hold_reward_two: int
    rebalance_reward_one: int
    rebalance_reward_two: int
    reward_value_complete: bool
    transition_cost_complete: bool
    economics_complete: bool
    complete_economic_components: tuple[str, ...]
    incomplete_economic_components: tuple[str, ...]
    status: str
    paper_only: bool
    actionable: bool
    live_authorized: bool
    hold_replay_fidelity: str
    rebalance_replay_fidelity: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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


def compare_paper_hold_vs_rebalance_gross(
    database_path: str | Path,
    *,
    prior: SmallLPReplayResult,
    observation_times: Sequence[str],
    min_bin_id: int,
    max_bin_id: int,
    strategy: StrategyType | str,
    max_share_bps: int | None = None,
    favor_x_in_active_bin: bool = False,
    max_quote_age_seconds: int = 300,
) -> PaperHoldVsRebalanceReplayReport:
    """
    Compare HOLD with a fresh redeposit over the same observed forward window.

    The comparison deliberately stops before claiming complete rebalance
    economics. It includes the candidate's modeled composition fee, but excludes
    unresolved transition/network/slippage semantics. Reward token value is also
    left incomplete whenever either path accrues non-zero rewards.

    The result is research evidence only and never authorizes an action.
    """
    if min_bin_id > max_bin_id:
        raise ValueError("min_bin_id cannot exceed max_bin_id")
    if max_quote_age_seconds < 0:
        raise ValueError("max_quote_age_seconds cannot be negative")
    times = [str(value) for value in observation_times]
    if len(times) < 2:
        raise ValueError("observation_times must contain at least two observations")
    if times != sorted(times) or len(set(times)) != len(times):
        raise ValueError(
            "observation_times must be unique and strictly ascending"
        )
    if times[0] != prior.end_observed_at:
        raise ValueError(
            "forward comparison must start at the prior replay end observation"
        )

    hold = continue_small_lp_replay(
        str(database_path),
        prior=prior,
        observation_times=times,
        max_share_bps=max_share_bps,
    )
    start_x = hold.start_position_x + hold.idle_x
    start_y = hold.start_position_y + hold.idle_y
    if start_x < 0 or start_y < 0 or (start_x == 0 and start_y == 0):
        raise ValueError("forward comparison start inventory is empty")

    share_limit = (
        prior.max_share_bps
        if max_share_bps is None
        else max_share_bps
    )
    rebalance = replay_small_lp_history(
        str(database_path),
        pool_address=prior.pool_address,
        amount_x=start_x,
        amount_y=start_y,
        min_bin_id=min_bin_id,
        max_bin_id=max_bin_id,
        strategy=strategy,
        observation_times=times,
        observation_limit=len(times),
        max_share_bps=share_limit,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )

    store = ResearchStore(str(database_path))
    decision_price = _active_price(
        store,
        pool_address=prior.pool_address,
        observed_at=times[0],
        active_bin_id=hold.start_active_bin_id,
    )
    exit_price = _active_price(
        store,
        pool_address=prior.pool_address,
        observed_at=times[-1],
        active_bin_id=hold.end_active_bin_id,
    )

    start_value = q64_value_in_y_atomic(
        amount_x=start_x,
        amount_y=start_y,
        price_q64=decision_price,
    )
    hold_value = q64_value_in_y_atomic(
        amount_x=hold.ending_x + hold.idle_x + hold.fee_x,
        amount_y=hold.ending_y + hold.idle_y + hold.fee_y,
        price_q64=exit_price,
    )
    rebalance_value = q64_value_in_y_atomic(
        amount_x=(
            rebalance.ending_x
            + rebalance.idle_x
            + rebalance.fee_x
        ),
        amount_y=(
            rebalance.ending_y
            + rebalance.idle_y
            + rebalance.fee_y
        ),
        price_q64=exit_price,
    )
    composition_cost = q64_value_in_y_atomic(
        amount_x=rebalance.entry_composition_fee_x,
        amount_y=rebalance.entry_composition_fee_y,
        price_q64=exit_price,
    )
    rebalance_after_composition = rebalance_value - composition_cost
    advantage = rebalance_after_composition - hold_value
    advantage_bps = (
        advantage * 10_000 // hold_value
        if hold_value > 0
        else None
    )

    token_y_mint = None
    end_y_quote_per_atomic = None
    end_y_quote_source = None
    end_y_quote_observed_at = None
    end_y_quote_age_seconds = None
    quote_reason = None
    hold_value_quote = None
    rebalance_value_quote = None
    composition_cost_quote = None
    advantage_quote = None

    decision_pool = store.chain_pool_snapshot_at(
        prior.pool_address,
        times[0],
    )
    end_pool = store.chain_pool_snapshot_at(
        prior.pool_address,
        times[-1],
    )
    if decision_pool is None or end_pool is None:
        quote_reason = "pool token identity missing at comparison boundary"
    else:
        decision_y = str(decision_pool.get("token_y_mint") or "")
        end_y = str(end_pool.get("token_y_mint") or "")
        if not decision_y or not end_y:
            quote_reason = "token Y mint missing at comparison boundary"
        elif decision_y != end_y:
            quote_reason = "token Y mint changed across comparison window"
        else:
            token_y_mint = end_y
            quote = token_quote_status(
                Storage(Path(database_path)),
                token_mint=token_y_mint,
                max_age_seconds=max_quote_age_seconds,
                as_of=times[-1],
                quote_unit=DEFAULT_QUOTE_UNIT,
            )
            end_y_quote_per_atomic = quote.quote_per_atomic
            end_y_quote_source = quote.source
            end_y_quote_observed_at = quote.observed_at
            end_y_quote_age_seconds = quote.age_seconds
            if not quote.available:
                quote_reason = "token Y quote missing at comparison end"
            elif not quote.fresh or quote.quote_per_atomic is None:
                quote_reason = "token Y quote stale at comparison end"
            else:
                hold_value_quote = hold_value * quote.quote_per_atomic
                rebalance_value_quote = (
                    rebalance_value * quote.quote_per_atomic
                )
                composition_cost_quote = (
                    composition_cost * quote.quote_per_atomic
                )
                advantage_quote = advantage * quote.quote_per_atomic

    quote_normalization_complete = quote_reason is None

    reward_value_complete = not any(
        (
            hold.reward_one,
            hold.reward_two,
            rebalance.reward_one,
            rebalance.reward_two,
        )
    )
    # Network fees, rebalance token-flow semantics, and any withdrawal/transition
    # effects are intentionally not inferred from incomplete evidence.
    transition_cost_complete = False
    economics_complete = reward_value_complete and transition_cost_complete
    complete_components = (
        "HOLD_INVENTORY_AND_FEE_VALUE",
        "REBALANCE_INVENTORY_AND_FEE_VALUE",
        "REBALANCE_COMPOSITION_COST",
    )
    incomplete_components = ["REBALANCE_TRANSITION_COST"]
    if not reward_value_complete:
        incomplete_components.append("REWARD_VALUATION")

    return PaperHoldVsRebalanceReplayReport(
        pool_address=prior.pool_address,
        decision_observed_at=times[0],
        end_observed_at=times[-1],
        strategy=StrategyType(strategy).value,
        min_bin_id=min_bin_id,
        max_bin_id=max_bin_id,
        decision_active_bin_id=hold.start_active_bin_id,
        rebalance_lower_offset_bins=(
            min_bin_id - hold.start_active_bin_id
        ),
        rebalance_upper_offset_bins=(
            max_bin_id - hold.start_active_bin_id
        ),
        start_x=start_x,
        start_y=start_y,
        start_value_y_atomic=start_value,
        hold_inventory_fee_value_y_atomic=hold_value,
        rebalance_inventory_fee_value_y_atomic=rebalance_value,
        rebalance_composition_cost_y_atomic=composition_cost,
        rebalance_value_after_composition_y_atomic=(
            rebalance_after_composition
        ),
        gross_advantage_before_transition_cost_y_atomic=advantage,
        gross_advantage_before_transition_cost_bps=advantage_bps,
        quote_unit=DEFAULT_QUOTE_UNIT,
        token_y_mint=token_y_mint,
        end_y_quote_per_atomic=end_y_quote_per_atomic,
        end_y_quote_source=end_y_quote_source,
        end_y_quote_observed_at=end_y_quote_observed_at,
        end_y_quote_age_seconds=end_y_quote_age_seconds,
        quote_normalization_complete=quote_normalization_complete,
        quote_exclusion_reason=quote_reason,
        hold_inventory_fee_value_quote=hold_value_quote,
        rebalance_inventory_fee_value_quote=rebalance_value_quote,
        rebalance_composition_cost_quote=composition_cost_quote,
        gross_advantage_before_transition_cost_quote=advantage_quote,
        hold_reward_one=hold.reward_one,
        hold_reward_two=hold.reward_two,
        rebalance_reward_one=rebalance.reward_one,
        rebalance_reward_two=rebalance.reward_two,
        reward_value_complete=reward_value_complete,
        transition_cost_complete=transition_cost_complete,
        economics_complete=economics_complete,
        complete_economic_components=complete_components,
        incomplete_economic_components=tuple(incomplete_components),
        status="GROSS_COMPARISON_TRANSITION_COST_INCOMPLETE",
        paper_only=True,
        actionable=False,
        live_authorized=False,
        hold_replay_fidelity=hold.replay_fidelity,
        rebalance_replay_fidelity=rebalance.replay_fidelity,
    )


def persist_paper_hold_vs_rebalance_gross(
    storage: Storage,
    *,
    report: PaperHoldVsRebalanceReplayReport,
) -> int:
    if (
        not report.paper_only
        or report.actionable
        or report.live_authorized
        or report.economics_complete
    ):
        raise ValueError(
            "gross HOLD-vs-REBALANCE evidence crossed research-only boundary"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=PAPER_HOLD_VS_REBALANCE_EVIDENCE_TYPE,
        pool_address=report.pool_address,
        as_of=report.decision_observed_at,
        status=report.status,
        qualified=False,
        evidence=report.to_record(),
    )
