from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

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
class ReplayBinResult:
    bin_id: int
    liquidity_share: int
    start_supply: int
    share_bps_of_start_supply: int
    end_x_amount: int
    end_y_amount: int
    fee_x: int
    fee_y: int


@dataclass(frozen=True)
class SmallLPReplayResult:
    pool_address: str
    start_observed_at: str
    end_observed_at: str
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
    max_share_bps: int
    projected_share_fidelity: str
    replay_fidelity: str
    bins: tuple[ReplayBinResult, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _row_by_bin(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(row["bin_id"]): row for row in rows}


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
    """
    Replay a small hypothetical standard-SPL LP across the latest two chain snapshots.

    This is a counterfactual approximation: the historical pool did not actually
    include our liquidity. To control path-distortion error, each projected share
    must remain below max_share_bps of the starting real bin supply.

    Empty-bin first deposits are rejected here because they can materially alter
    whether/when historical swaps cross into the next bin.
    """
    if amount_x < 0 or amount_y < 0:
        raise ValueError("amounts cannot be negative")
    if not 1 <= max_share_bps <= 10_000:
        raise ValueError("max_share_bps must be between 1 and 10000")

    store = ResearchStore(database_path)
    times = store.chain_observation_times(pool_address, limit=2)
    if len(times) < 2:
        raise ValueError("need at least two chain observations for replay")

    end_time, start_time = times[0], times[1]
    start_pool = store.chain_pool_snapshot_at(pool_address, start_time)
    end_pool = store.chain_pool_snapshot_at(pool_address, end_time)
    if start_pool is None or end_pool is None:
        raise ValueError("missing chain pool snapshot")
    if int(start_pool["bin_step"]) != int(end_pool["bin_step"]):
        raise ValueError("bin_step changed across replay interval")

    for side in ("x", "y"):
        start_program = start_pool.get(f"token_{side}_program")
        end_program = end_pool.get(f"token_{side}_program")
        if start_program is None or end_program is None:
            raise ValueError(
                "token program metadata missing; collect fresh chain snapshots before replay"
            )
        if start_program != end_program:
            raise ValueError(f"token {side.upper()} program changed across replay interval")
        if str(start_program) != STANDARD_SPL_TOKEN_PROGRAM:
            raise ValueError(
                f"token {side.upper()} uses unsupported Token-2022/non-standard program"
            )

    start_bins = store.load_bin_liquidity(pool_address, observed_at=start_time)
    end_bins = store.load_bin_liquidity(pool_address, observed_at=end_time)
    start_by_id = _row_by_bin(start_bins)
    end_by_id = _row_by_bin(end_bins)

    price_map = {bin_id: int(str(row["price"])) for bin_id, row in start_by_id.items()}

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

    results: list[ReplayBinResult] = []
    for projected_bin in projected.bins:
        start_row = start_by_id[projected_bin.bin_id]
        end_row = end_by_id.get(projected_bin.bin_id)
        if end_row is None:
            raise ValueError(f"end snapshot does not cover bin {projected_bin.bin_id}")

        start_supply = int(str(start_row["liquidity_supply"]))
        if start_supply <= 0:
            raise ValueError(
                f"small-LP replay rejects empty starting bin {projected_bin.bin_id}"
            )

        share = projected_bin.liquidity_share_minted
        if share * 10_000 > start_supply * max_share_bps:
            actual_bps = share * 10_000 // start_supply
            raise ValueError(
                f"projected share is too large in bin {projected_bin.bin_id}: "
                f"{actual_bps} bps > {max_share_bps} bps limit"
            )

        end_supply = int(str(end_row["liquidity_supply"]))
        if end_supply <= 0:
            end_x = 0
            end_y = 0
        else:
            end_x, end_y = amounts_from_liquidity_share(
                liquidity_share=share,
                bin_amount_x=int(str(end_row["amount_x"])),
                bin_amount_y=int(str(end_row["amount_y"])),
                liquidity_supply=end_supply,
            )

        delta_x = max(
            0,
            int(str(end_row["fee_amount_x_per_token_stored"]))
            - int(str(start_row["fee_amount_x_per_token_stored"])),
        )
        delta_y = max(
            0,
            int(str(end_row["fee_amount_y_per_token_stored"]))
            - int(str(start_row["fee_amount_y_per_token_stored"])),
        )

        # Small-LP counterfactual dilution correction. Historical per-token
        # checkpoints were produced without our share, so reduce them by the
        # starting real-supply fraction after adding the hypothetical share.
        adjusted_delta_x = delta_x * start_supply // (start_supply + share)
        adjusted_delta_y = delta_y * start_supply // (start_supply + share)

        results.append(
            ReplayBinResult(
                bin_id=projected_bin.bin_id,
                liquidity_share=share,
                start_supply=start_supply,
                share_bps_of_start_supply=share * 10_000 // start_supply,
                end_x_amount=end_x,
                end_y_amount=end_y,
                fee_x=fee_from_checkpoint_delta(
                    liquidity_share=share,
                    fee_per_token_delta=adjusted_delta_x,
                ),
                fee_y=fee_from_checkpoint_delta(
                    liquidity_share=share,
                    fee_per_token_delta=adjusted_delta_y,
                ),
            )
        )

    return SmallLPReplayResult(
        pool_address=pool_address,
        start_observed_at=start_time,
        end_observed_at=end_time,
        start_active_bin_id=int(start_pool["active_bin_id"]),
        end_active_bin_id=int(end_pool["active_bin_id"]),
        deposited_x=plan.deposited_x,
        deposited_y=plan.deposited_y,
        idle_x=plan.idle_x,
        idle_y=plan.idle_y,
        ending_x=sum(item.end_x_amount for item in results),
        ending_y=sum(item.end_y_amount for item in results),
        fee_x=sum(item.fee_x for item in results),
        fee_y=sum(item.fee_y for item in results),
        max_share_bps=max_share_bps,
        projected_share_fidelity=projected.fidelity,
        replay_fidelity="SMALL_LP_CHAIN_COUNTERFACTUAL_V1",
        bins=tuple(results),
    )
