from __future__ import annotations

from typing import Any, Mapping


BASIS_POINT_MAX = 10_000
MAX_FEE_RATE = 100_000_000
VARIABLE_FEE_SCALE = 100_000_000_000


def _required_int(state: Mapping[str, Any], key: str) -> int:
    value = state.get(key)
    if value is None:
        raise ValueError(f"missing fee state field: {key}")
    return int(value)


def deposit_total_fee_rate_at_timestamp(
    *,
    bin_step: int,
    active_bin_id: int,
    target_timestamp: int,
    fee_state: Mapping[str, Any],
) -> int:
    """
    Mirror LbPair::update_references + update_volatility_accumulator + get_total_fee.

    This lets a slot-bounded prestate be replayed at the add transaction's clock
    instead of reusing a fee rate computed at the earlier snapshot timestamp.
    """
    if bin_step <= 0:
        raise ValueError("bin_step must be positive")

    base_factor = _required_int(fee_state, "fee_base_factor")
    filter_period = _required_int(fee_state, "fee_filter_period")
    decay_period = _required_int(fee_state, "fee_decay_period")
    reduction_factor = _required_int(fee_state, "fee_reduction_factor")
    variable_fee_control = _required_int(fee_state, "fee_variable_fee_control")
    max_volatility_accumulator = _required_int(
        fee_state,
        "fee_max_volatility_accumulator",
    )
    base_fee_power_factor = _required_int(
        fee_state,
        "fee_base_fee_power_factor",
    )
    volatility_accumulator = _required_int(
        fee_state,
        "fee_volatility_accumulator",
    )
    volatility_reference = _required_int(
        fee_state,
        "fee_volatility_reference",
    )
    index_reference = _required_int(fee_state, "fee_index_reference")
    last_update_timestamp = _required_int(
        fee_state,
        "fee_last_update_timestamp",
    )

    elapsed = target_timestamp - last_update_timestamp
    if elapsed < 0:
        raise ValueError("target timestamp predates fee-state last update")

    if elapsed >= filter_period:
        index_reference = active_bin_id
        if elapsed < decay_period:
            volatility_reference = (
                volatility_accumulator * reduction_factor // BASIS_POINT_MAX
            )
        else:
            volatility_reference = 0

    delta_id = abs(index_reference - active_bin_id)
    effective_volatility = min(
        volatility_reference + delta_id * BASIS_POINT_MAX,
        max_volatility_accumulator,
    )

    base_fee = (
        base_factor
        * bin_step
        * 10
        * (10**base_fee_power_factor)
    )

    variable_fee = 0
    if variable_fee_control > 0:
        square_vfa_bin = (effective_volatility * bin_step) ** 2
        raw_variable_fee = variable_fee_control * square_vfa_bin
        variable_fee = (
            raw_variable_fee + VARIABLE_FEE_SCALE - 1
        ) // VARIABLE_FEE_SCALE

    return min(base_fee + variable_fee, MAX_FEE_RATE)
