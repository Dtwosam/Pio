from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .composition_fee import simulate_active_bin_composition_fee
from .deposit_plan import (
    AtomicDepositPlan,
    ProjectedDepositShares,
    distribute_standard_spl_deposit,
    project_deposit_shares,
)
from .liquidity_math import amounts_from_liquidity_share, fee_from_checkpoint_delta
from .research_store import ResearchStore
from .strategy import StrategyType


STANDARD_SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


@dataclass(frozen=True)
class ReplayIntervalResult:
    start_observed_at: str
    end_observed_at: str
    start_active_bin_id: int
    end_active_bin_id: int
    fee_x: int
    fee_y: int
    max_observed_share_bps: int


@dataclass(frozen=True)
class ReplayBinResult:
    bin_id: int
    liquidity_share: int
    start_supply: int
    share_bps_of_start_supply: int
    max_observed_share_bps: int
    entry_composition_fee_x: int
    entry_composition_fee_y: int
    entry_composition_protocol_fee_x: int
    entry_composition_protocol_fee_y: int
    entry_composition_lp_fee_x: int
    entry_composition_lp_fee_y: int
    end_x_amount: int
    end_y_amount: int
    fee_x: int
    fee_y: int


@dataclass(frozen=True)
class SmallLPReplayResult:
    pool_address: str
    start_observed_at: str
    end_observed_at: str
    observation_count: int
    start_active_bin_id: int
    end_active_bin_id: int
    deposited_x: int
    deposited_y: int
    idle_x: int
    idle_y: int
    ending_x: int
    ending_y: int
    fee_x: int
    fee_y: int
    entry_composition_fee_x: int
    entry_composition_fee_y: int
    entry_composition_protocol_fee_x: int
    entry_composition_protocol_fee_y: int
    entry_composition_lp_fee_x: int
    entry_composition_lp_fee_y: int
    deposit_total_fee_rate: int
    protocol_share_bps: int
    max_share_bps: int
    max_observed_share_bps: int
    projected_share_fidelity: str
    replay_fidelity: str
    intervals: tuple[ReplayIntervalResult, ...]
    bins: tuple[ReplayBinResult, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _row_by_bin(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(row["bin_id"]): row for row in rows}


def _validate_pool_path(
    snapshots: list[dict[str, Any]],
) -> None:
    if not snapshots:
        raise ValueError("pool snapshot path cannot be empty")

    first = snapshots[0]
    expected_bin_step = int(first["bin_step"])
    expected_mints = {
        "x": str(first["token_x_mint"]),
        "y": str(first["token_y_mint"]),
    }

    for snapshot in snapshots:
        if int(snapshot["bin_step"]) != expected_bin_step:
            raise ValueError("bin_step changed across replay path")

        for side in ("x", "y"):
            if str(snapshot[f"token_{side}_mint"]) != expected_mints[side]:
                raise ValueError(f"token {side.upper()} mint changed across replay path")

            program = snapshot.get(f"token_{side}_program")
            if program is None:
                raise ValueError(
                    "token program metadata missing; collect fresh chain snapshots before replay"
                )
            if str(program) != STANDARD_SPL_TOKEN_PROGRAM:
                raise ValueError(
                    f"token {side.upper()} uses unsupported Token-2022/non-standard program"
                )


def _share_bps(share: int, real_supply: int) -> int:
    if real_supply <= 0:
        raise ValueError("real bin supply must be positive")
    return share * 10_000 // real_supply


def replay_small_lp_history(
    database_path: str,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    min_bin_id: int,
    max_bin_id: int,
    strategy: StrategyType | str,
    observation_limit: int = 12,
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> SmallLPReplayResult:
    """
    Replay a small hypothetical standard-SPL LP across the latest chain snapshots.

    The historical pool did not actually include our liquidity, so this is a
    counterfactual approximation. The same hypothetical liquidity share is held
    through the path, while every observation rechecks the small-LP limit and
    every interval dilutes historical fee-checkpoint growth by that interval's
    real starting supply.

    This replay deliberately fails closed when a covered historical bin has zero
    supply. In that case our hypothetical position would itself change whether
    the bin was empty, so the observed path is no longer a safe counterfactual.
    """
    if amount_x < 0 or amount_y < 0:
        raise ValueError("amounts cannot be negative")
    if observation_limit < 2:
        raise ValueError("observation_limit must be at least 2")
    if not 1 <= max_share_bps <= 10_000:
        raise ValueError("max_share_bps must be between 1 and 10000")

    store = ResearchStore(database_path)
    times_desc = store.chain_observation_times(pool_address, limit=observation_limit)
    if len(times_desc) < 2:
        raise ValueError("need at least two chain observations for replay")
    times = list(reversed(times_desc))

    pool_snapshots: list[dict[str, Any]] = []
    bin_snapshots: list[dict[int, dict[str, Any]]] = []
    for observed_at in times:
        pool = store.chain_pool_snapshot_at(pool_address, observed_at)
        if pool is None:
            raise ValueError(f"missing chain pool snapshot at {observed_at}")
        pool_snapshots.append(pool)
        bin_snapshots.append(
            _row_by_bin(store.load_bin_liquidity(pool_address, observed_at=observed_at))
        )

    _validate_pool_path(pool_snapshots)

    start_pool = pool_snapshots[0]
    start_bins = list(bin_snapshots[0].values())
    start_by_id = bin_snapshots[0]
    price_map = {
        bin_id: int(str(row["price"]))
        for bin_id, row in start_by_id.items()
    }

    plan: AtomicDepositPlan = distribute_standard_spl_deposit(
        active_id=int(start_pool["active_bin_id"]),
        min_bin_id=min_bin_id,
        max_bin_id=max_bin_id,
        amount_x=amount_x,
        amount_y=amount_y,
        strategy=strategy,
        prices_q64=price_map,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
    projected: ProjectedDepositShares = project_deposit_shares(plan, start_bins)
    plan_by_id = {item.bin_id: item for item in plan.bins}

    deposit_fee_rate_raw = start_pool.get("deposit_total_fee_rate")
    protocol_share_raw = start_pool.get("protocol_share_bps")
    if deposit_fee_rate_raw is None or protocol_share_raw is None:
        raise ValueError(
            "deposit-time fee metadata missing; collect fresh chain snapshots before replay"
        )
    deposit_total_fee_rate = int(str(deposit_fee_rate_raw))
    protocol_share_bps = int(protocol_share_raw)

    bin_fee_x = {item.bin_id: 0 for item in projected.bins}
    bin_fee_y = {item.bin_id: 0 for item in projected.bins}
    bin_max_share_bps = {item.bin_id: 0 for item in projected.bins}

    # Validate the hypothetical share at every observed pool state, not only entry.
    for snapshot_index, rows in enumerate(bin_snapshots):
        for projected_bin in projected.bins:
            row = rows.get(projected_bin.bin_id)
            if row is None:
                raise ValueError(
                    f"chain snapshot {times[snapshot_index]} does not cover "
                    f"bin {projected_bin.bin_id}"
                )

            supply = int(str(row["liquidity_supply"]))
            if supply <= 0:
                raise ValueError(
                    f"counterfactual invalid: historical bin {projected_bin.bin_id} "
                    f"has zero supply at {times[snapshot_index]}"
                )

            observed_bps = _share_bps(projected_bin.liquidity_share_minted, supply)
            bin_max_share_bps[projected_bin.bin_id] = max(
                bin_max_share_bps[projected_bin.bin_id],
                observed_bps,
            )
            if projected_bin.liquidity_share_minted * 10_000 > supply * max_share_bps:
                raise ValueError(
                    f"projected share is too large in bin {projected_bin.bin_id} "
                    f"at {times[snapshot_index]}: "
                    f"{observed_bps} bps > {max_share_bps} bps limit"
                )

    # Composition fee is an entry cost and occurs once, at the entry active bin.
    composition_by_bin: dict[int, tuple[int, int, int, int, int, int]] = {}
    for projected_bin in projected.bins:
        values = (0, 0, 0, 0, 0, 0)
        if projected_bin.bin_id == int(start_pool["active_bin_id"]):
            start_row = start_by_id[projected_bin.bin_id]
            planned = plan_by_id[projected_bin.bin_id]
            composition = simulate_active_bin_composition_fee(
                amount_x=planned.amount_x,
                amount_y=planned.amount_y,
                price_q64=int(str(start_row["price"])),
                bin_amount_x=int(str(start_row["amount_x"])),
                bin_amount_y=int(str(start_row["amount_y"])),
                liquidity_supply=int(str(start_row["liquidity_supply"])),
                total_fee_rate=deposit_total_fee_rate,
                protocol_share_bps=protocol_share_bps,
            )
            values = (
                composition.composition_fee_x,
                composition.composition_fee_y,
                composition.protocol_fee_x,
                composition.protocol_fee_y,
                composition.lp_fee_x,
                composition.lp_fee_y,
            )
        composition_by_bin[projected_bin.bin_id] = values

    intervals: list[ReplayIntervalResult] = []
    for idx in range(len(times) - 1):
        previous_rows = bin_snapshots[idx]
        current_rows = bin_snapshots[idx + 1]
        interval_fee_x = 0
        interval_fee_y = 0
        interval_max_bps = 0

        for projected_bin in projected.bins:
            before = previous_rows[projected_bin.bin_id]
            after = current_rows[projected_bin.bin_id]
            share = projected_bin.liquidity_share_minted
            previous_supply = int(str(before["liquidity_supply"]))

            delta_x = max(
                0,
                int(str(after["fee_amount_x_per_token_stored"]))
                - int(str(before["fee_amount_x_per_token_stored"])),
            )
            delta_y = max(
                0,
                int(str(after["fee_amount_y_per_token_stored"]))
                - int(str(before["fee_amount_y_per_token_stored"])),
            )

            # Historical checkpoints were produced without our hypothetical share.
            # Approximate dilution using the real supply at the start of this interval.
            adjusted_delta_x = delta_x * previous_supply // (previous_supply + share)
            adjusted_delta_y = delta_y * previous_supply // (previous_supply + share)

            fee_x = fee_from_checkpoint_delta(
                liquidity_share=share,
                fee_per_token_delta=adjusted_delta_x,
            )
            fee_y = fee_from_checkpoint_delta(
                liquidity_share=share,
                fee_per_token_delta=adjusted_delta_y,
            )
            bin_fee_x[projected_bin.bin_id] += fee_x
            bin_fee_y[projected_bin.bin_id] += fee_y
            interval_fee_x += fee_x
            interval_fee_y += fee_y
            interval_max_bps = max(
                interval_max_bps,
                _share_bps(share, previous_supply),
                _share_bps(share, int(str(after["liquidity_supply"]))),
            )

        intervals.append(
            ReplayIntervalResult(
                start_observed_at=times[idx],
                end_observed_at=times[idx + 1],
                start_active_bin_id=int(pool_snapshots[idx]["active_bin_id"]),
                end_active_bin_id=int(pool_snapshots[idx + 1]["active_bin_id"]),
                fee_x=interval_fee_x,
                fee_y=interval_fee_y,
                max_observed_share_bps=interval_max_bps,
            )
        )

    final_rows = bin_snapshots[-1]
    results: list[ReplayBinResult] = []
    for projected_bin in projected.bins:
        start_row = start_by_id[projected_bin.bin_id]
        final_row = final_rows[projected_bin.bin_id]
        share = projected_bin.liquidity_share_minted
        start_supply = int(str(start_row["liquidity_supply"]))
        final_supply = int(str(final_row["liquidity_supply"]))

        end_x, end_y = amounts_from_liquidity_share(
            liquidity_share=share,
            bin_amount_x=int(str(final_row["amount_x"])),
            bin_amount_y=int(str(final_row["amount_y"])),
            liquidity_supply=final_supply,
        )
        (
            composition_fee_x,
            composition_fee_y,
            composition_protocol_x,
            composition_protocol_y,
            composition_lp_x,
            composition_lp_y,
        ) = composition_by_bin[projected_bin.bin_id]

        results.append(
            ReplayBinResult(
                bin_id=projected_bin.bin_id,
                liquidity_share=share,
                start_supply=start_supply,
                share_bps_of_start_supply=_share_bps(share, start_supply),
                max_observed_share_bps=bin_max_share_bps[projected_bin.bin_id],
                entry_composition_fee_x=composition_fee_x,
                entry_composition_fee_y=composition_fee_y,
                entry_composition_protocol_fee_x=composition_protocol_x,
                entry_composition_protocol_fee_y=composition_protocol_y,
                entry_composition_lp_fee_x=composition_lp_x,
                entry_composition_lp_fee_y=composition_lp_y,
                end_x_amount=end_x,
                end_y_amount=end_y,
                fee_x=bin_fee_x[projected_bin.bin_id],
                fee_y=bin_fee_y[projected_bin.bin_id],
            )
        )

    return SmallLPReplayResult(
        pool_address=pool_address,
        start_observed_at=times[0],
        end_observed_at=times[-1],
        observation_count=len(times),
        start_active_bin_id=int(pool_snapshots[0]["active_bin_id"]),
        end_active_bin_id=int(pool_snapshots[-1]["active_bin_id"]),
        deposited_x=plan.deposited_x,
        deposited_y=plan.deposited_y,
        idle_x=plan.idle_x,
        idle_y=plan.idle_y,
        ending_x=sum(item.end_x_amount for item in results),
        ending_y=sum(item.end_y_amount for item in results),
        fee_x=sum(item.fee_x for item in results),
        fee_y=sum(item.fee_y for item in results),
        entry_composition_fee_x=sum(item.entry_composition_fee_x for item in results),
        entry_composition_fee_y=sum(item.entry_composition_fee_y for item in results),
        entry_composition_protocol_fee_x=sum(
            item.entry_composition_protocol_fee_x for item in results
        ),
        entry_composition_protocol_fee_y=sum(
            item.entry_composition_protocol_fee_y for item in results
        ),
        entry_composition_lp_fee_x=sum(item.entry_composition_lp_fee_x for item in results),
        entry_composition_lp_fee_y=sum(item.entry_composition_lp_fee_y for item in results),
        deposit_total_fee_rate=deposit_total_fee_rate,
        protocol_share_bps=protocol_share_bps,
        max_share_bps=max_share_bps,
        max_observed_share_bps=max(
            (item.max_observed_share_bps for item in results),
            default=0,
        ),
        projected_share_fidelity=projected.fidelity,
        replay_fidelity="SMALL_LP_CHAIN_PATH_V2",
        intervals=tuple(intervals),
        bins=tuple(results),
    )


def replay_latest_small_lp_interval(
    database_path: str,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    min_bin_id: int,
    max_bin_id: int,
    strategy: StrategyType | str,
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> SmallLPReplayResult:
    """Backward-compatible two-observation replay wrapper."""
    return replay_small_lp_history(
        database_path,
        pool_address=pool_address,
        amount_x=amount_x,
        amount_y=amount_y,
        min_bin_id=min_bin_id,
        max_bin_id=max_bin_id,
        strategy=strategy,
        observation_limit=2,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
