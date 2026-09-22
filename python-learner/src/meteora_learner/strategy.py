from __future__ import annotations

from enum import Enum


DEFAULT_MAX_WEIGHT = 2000
DEFAULT_MIN_WEIGHT = 200


class StrategyType(str, Enum):
    SPOT = "SPOT"
    CURVE = "CURVE"
    BID_ASK = "BID_ASK"


def _ascending(min_bin_id: int, max_bin_id: int) -> dict[int, int]:
    return {bin_id: bin_id - min_bin_id + 1 for bin_id in range(min_bin_id, max_bin_id + 1)}


def _descending(min_bin_id: int, max_bin_id: int) -> dict[int, int]:
    return {bin_id: max_bin_id - bin_id + 1 for bin_id in range(min_bin_id, max_bin_id + 1)}


def _centered(
    min_bin_id: int,
    max_bin_id: int,
    active_id: int,
    *,
    invert: bool,
) -> dict[int, int]:
    diff_weight = DEFAULT_MAX_WEIGHT - DEFAULT_MIN_WEIGHT
    left_step = diff_weight // (active_id - min_bin_id) if active_id > min_bin_id else 0
    right_step = diff_weight // (max_bin_id - active_id) if max_bin_id > active_id else 0

    out: dict[int, int] = {}
    for bin_id in range(min_bin_id, max_bin_id + 1):
        if bin_id < active_id:
            distance_weight = (active_id - bin_id) * left_step
        elif bin_id > active_id:
            distance_weight = (bin_id - active_id) * right_step
        else:
            distance_weight = 0

        if invert:
            out[bin_id] = DEFAULT_MIN_WEIGHT + distance_weight
        else:
            out[bin_id] = DEFAULT_MAX_WEIGHT - distance_weight
    return out


def strategy_weights(
    min_bin_id: int,
    max_bin_id: int,
    active_id: int,
    strategy: StrategyType | str,
) -> dict[int, int]:
    """Match Meteora's current Spot/Curve/BidAsk strategy weight shapes."""
    if min_bin_id > max_bin_id:
        raise ValueError("min_bin_id cannot exceed max_bin_id")

    strategy = StrategyType(strategy)

    if strategy is StrategyType.SPOT:
        return {bin_id: 1 for bin_id in range(min_bin_id, max_bin_id + 1)}

    if active_id < min_bin_id:
        return _descending(min_bin_id, max_bin_id) if strategy is StrategyType.CURVE else _ascending(min_bin_id, max_bin_id)

    if active_id > max_bin_id:
        return _ascending(min_bin_id, max_bin_id) if strategy is StrategyType.CURVE else _descending(min_bin_id, max_bin_id)

    return _centered(
        min_bin_id,
        max_bin_id,
        active_id,
        invert=strategy is StrategyType.BID_ASK,
    )


def deposit_weights(
    min_bin_id: int,
    max_bin_id: int,
    active_id: int,
    strategy: StrategyType | str,
    *,
    favor_x_in_active_bin: bool = False,
) -> dict[int, int]:
    """
    Match Meteora toAmountsBothSideByStrategy side-local weights.

    This is intentionally separate from strategy_weights, which mirrors the
    centered weight helpers used by autofill/strategy parameter calculations.
    """
    if min_bin_id > max_bin_id:
        raise ValueError("min_bin_id cannot exceed max_bin_id")

    strategy = StrategyType(strategy)

    if strategy is StrategyType.SPOT:
        return {bin_id: 1 for bin_id in range(min_bin_id, max_bin_id + 1)}

    if active_id < min_bin_id:
        return (
            _descending(min_bin_id, max_bin_id)
            if strategy is StrategyType.CURVE
            else _ascending(min_bin_id, max_bin_id)
        )

    if active_id > max_bin_id:
        return (
            _ascending(min_bin_id, max_bin_id)
            if strategy is StrategyType.CURVE
            else _descending(min_bin_id, max_bin_id)
        )

    out: dict[int, int] = {}

    bid_max = active_id - 1 if favor_x_in_active_bin else active_id
    ask_min = active_id if favor_x_in_active_bin else active_id + 1

    if min_bin_id <= bid_max:
        bid = (
            _ascending(min_bin_id, bid_max)
            if strategy is StrategyType.CURVE
            else _descending(min_bin_id, bid_max)
        )
        out.update(bid)

    if ask_min <= max_bin_id:
        ask = (
            _descending(ask_min, max_bin_id)
            if strategy is StrategyType.CURVE
            else _ascending(ask_min, max_bin_id)
        )
        out.update(ask)

    return out
