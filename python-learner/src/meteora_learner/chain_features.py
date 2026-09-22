from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class LiquidityShapeFeatures:
    active_bin_id: int
    occupied_bins: int
    total_liquidity_supply: int
    active_liquidity_supply: int
    near_active_liquidity_ratio: float
    below_active_liquidity_ratio: float
    above_active_liquidity_ratio: float
    liquidity_weighted_distance_bins: float


@dataclass(frozen=True)
class FeeCheckpointActivity:
    matched_bins: int
    bins_with_x_growth: int
    bins_with_y_growth: int
    x_growth_raw: int
    y_growth_raw: int


def _as_int(value: Any) -> int:
    return int(str(value))


def summarize_liquidity_shape(
    bins: Sequence[dict[str, Any]],
    *,
    active_bin_id: int,
    near_radius: int = 5,
) -> LiquidityShapeFeatures:
    if near_radius < 0:
        raise ValueError("near_radius cannot be negative")

    points: list[tuple[int, int]] = []
    for row in bins:
        liquidity = _as_int(row.get("liquidity_supply", 0))
        if liquidity < 0:
            raise ValueError("liquidity_supply cannot be negative")
        if liquidity > 0:
            points.append((int(row["bin_id"]), liquidity))

    total = sum(liquidity for _, liquidity in points)
    if total == 0:
        return LiquidityShapeFeatures(
            active_bin_id=active_bin_id,
            occupied_bins=0,
            total_liquidity_supply=0,
            active_liquidity_supply=0,
            near_active_liquidity_ratio=0.0,
            below_active_liquidity_ratio=0.0,
            above_active_liquidity_ratio=0.0,
            liquidity_weighted_distance_bins=0.0,
        )

    active = sum(liquidity for bin_id, liquidity in points if bin_id == active_bin_id)
    near = sum(
        liquidity
        for bin_id, liquidity in points
        if abs(bin_id - active_bin_id) <= near_radius
    )
    below = sum(liquidity for bin_id, liquidity in points if bin_id < active_bin_id)
    above = sum(liquidity for bin_id, liquidity in points if bin_id > active_bin_id)
    weighted_distance = (
        sum(abs(bin_id - active_bin_id) * liquidity for bin_id, liquidity in points) / total
    )

    return LiquidityShapeFeatures(
        active_bin_id=active_bin_id,
        occupied_bins=len(points),
        total_liquidity_supply=total,
        active_liquidity_supply=active,
        near_active_liquidity_ratio=near / total,
        below_active_liquidity_ratio=below / total,
        above_active_liquidity_ratio=above / total,
        liquidity_weighted_distance_bins=float(weighted_distance),
    )


def fee_checkpoint_activity(
    previous: Sequence[dict[str, Any]],
    current: Sequence[dict[str, Any]],
) -> FeeCheckpointActivity:
    """
    Compare raw per-token fee checkpoints between on-chain snapshots.

    These are activity features only. They are NOT position fee earnings.
    """
    old = {int(row["bin_id"]): row for row in previous}
    new = {int(row["bin_id"]): row for row in current}

    matched = sorted(set(old) & set(new))
    x_growth = 0
    y_growth = 0
    x_bins = 0
    y_bins = 0

    for bin_id in matched:
        dx = _as_int(new[bin_id]["fee_amount_x_per_token_stored"]) - _as_int(
            old[bin_id]["fee_amount_x_per_token_stored"]
        )
        dy = _as_int(new[bin_id]["fee_amount_y_per_token_stored"]) - _as_int(
            old[bin_id]["fee_amount_y_per_token_stored"]
        )
        if dx > 0:
            x_growth += dx
            x_bins += 1
        if dy > 0:
            y_growth += dy
            y_bins += 1

    return FeeCheckpointActivity(
        matched_bins=len(matched),
        bins_with_x_growth=x_bins,
        bins_with_y_growth=y_bins,
        x_growth_raw=x_growth,
        y_growth_raw=y_growth,
    )
