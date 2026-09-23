import pytest

from meteora_learner.fee_state import deposit_total_fee_rate_at_timestamp


def state(**overrides):
    values = {
        "fee_base_factor": 100,
        "fee_filter_period": 30,
        "fee_decay_period": 120,
        "fee_reduction_factor": 5000,
        "fee_variable_fee_control": 1000,
        "fee_max_volatility_accumulator": 1_000_000,
        "fee_base_fee_power_factor": 0,
        "fee_volatility_accumulator": 40_000,
        "fee_volatility_reference": 30_000,
        "fee_index_reference": 8,
        "fee_last_update_timestamp": 1000,
    }
    values.update(overrides)
    return values


def test_fee_state_keeps_reference_inside_filter_window():
    rate = deposit_total_fee_rate_at_timestamp(
        bin_step=25,
        active_bin_id=10,
        target_timestamp=1010,
        fee_state=state(),
    )
    expected_vol = 30_000 + 2 * 10_000
    expected_variable = (
        1000 * (expected_vol * 25) ** 2 + 100_000_000_000 - 1
    ) // 100_000_000_000
    assert rate == 100 * 25 * 10 + expected_variable


def test_fee_state_decays_reference_after_filter_period():
    rate = deposit_total_fee_rate_at_timestamp(
        bin_step=25,
        active_bin_id=10,
        target_timestamp=1060,
        fee_state=state(),
    )
    expected_vol = 40_000 * 5000 // 10_000
    expected_variable = (
        1000 * (expected_vol * 25) ** 2 + 100_000_000_000 - 1
    ) // 100_000_000_000
    assert rate == 100 * 25 * 10 + expected_variable


def test_fee_state_resets_reference_after_decay_period():
    rate = deposit_total_fee_rate_at_timestamp(
        bin_step=25,
        active_bin_id=10,
        target_timestamp=1200,
        fee_state=state(),
    )
    assert rate == 100 * 25 * 10


def test_fee_state_rejects_timestamp_before_last_update():
    with pytest.raises(ValueError, match="predates"):
        deposit_total_fee_rate_at_timestamp(
            bin_step=25,
            active_bin_id=10,
            target_timestamp=999,
            fee_state=state(),
        )
