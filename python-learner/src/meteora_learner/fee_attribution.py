from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .deposit_plan import ProjectedDepositShares
from .liquidity_math import fee_from_checkpoint_delta


@dataclass(frozen=True)
class BinFeeAccrual:
    bin_id: int
    liquidity_share: int
    fee_x_per_token_delta: int
    fee_y_per_token_delta: int
    fee_x: int
    fee_y: int


@dataclass(frozen=True)
class FeeAccrualResult:
    bins: tuple[BinFeeAccrual, ...]
    total_fee_x: int
    total_fee_y: int
    matched_bins: int
    fidelity: str


def _index(rows: Sequence[dict[str, object]]) -> dict[int, dict[str, object]]:
    return {int(row["bin_id"]): row for row in rows}


def accrue_projected_fees(
    projected: ProjectedDepositShares,
    previous_bins: Sequence[dict[str, object]],
    current_bins: Sequence[dict[str, object]],
) -> FeeAccrualResult:
    """
    Attribute fee-checkpoint growth to a fixed hypothetical liquidity share.

    Mirrors Meteora DynamicPosition fee accrual for new fees only. Existing
    fee_pending is zero for a hypothetical fresh deposit.

    The caller must ensure both snapshots represent the same pool and that the
    projected position was continuously open for the interval.
    """
    previous = _index(previous_bins)
    current = _index(current_bins)

    out: list[BinFeeAccrual] = []
    matched = 0

    for item in projected.bins:
        before = previous.get(item.bin_id)
        after = current.get(item.bin_id)
        if before is None or after is None:
            continue
        matched += 1

        before_x = int(str(before["fee_amount_x_per_token_stored"]))
        before_y = int(str(before["fee_amount_y_per_token_stored"]))
        after_x = int(str(after["fee_amount_x_per_token_stored"]))
        after_y = int(str(after["fee_amount_y_per_token_stored"]))

        delta_x = max(0, after_x - before_x)
        delta_y = max(0, after_y - before_y)

        out.append(
            BinFeeAccrual(
                bin_id=item.bin_id,
                liquidity_share=item.liquidity_share_minted,
                fee_x_per_token_delta=delta_x,
                fee_y_per_token_delta=delta_y,
                fee_x=fee_from_checkpoint_delta(
                    liquidity_share=item.liquidity_share_minted,
                    fee_per_token_delta=delta_x,
                ),
                fee_y=fee_from_checkpoint_delta(
                    liquidity_share=item.liquidity_share_minted,
                    fee_per_token_delta=delta_y,
                ),
            )
        )

    fidelity = "ONCHAIN_CHECKPOINT_FIXED_SHARE_V1"
    if matched != len(projected.bins):
        fidelity = "PARTIAL_ONCHAIN_CHECKPOINT_FIXED_SHARE_V1"

    return FeeAccrualResult(
        bins=tuple(out),
        total_fee_x=sum(item.fee_x for item in out),
        total_fee_y=sum(item.fee_y for item in out),
        matched_bins=matched,
        fidelity=fidelity,
    )
