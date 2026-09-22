import pytest

from meteora_learner.dlmm_math import bin_price, relative_bin_price
from meteora_learner.simulator import (
    SIMULATOR_FIDELITY,
    create_position,
    simulate_price_path,
    token_totals,
)


def test_spot_position_allocates_y_below_and_x_above_active():
    state = create_position(
        min_bin_id=-2,
        max_bin_id=2,
        active_id=0,
        bin_step=100,
        amount_x=2.0,
        amount_y=100.0,
    )

    assert state.bins[-2].y_amount > 0
    assert state.bins[-1].y_amount > 0
    assert state.bins[0].y_amount > 0
    assert state.bins[1].x_amount > 0
    assert state.bins[2].x_amount > 0
    x, y = token_totals(state)
    assert x == pytest.approx(2.0)
    assert y == pytest.approx(100.0)


def test_upward_move_converts_completed_ask_bin_x_to_y():
    state = create_position(
        min_bin_id=-1,
        max_bin_id=2,
        active_id=0,
        bin_step=100,
        amount_x=2.0,
        amount_y=100.0,
    )
    bin1_x_before = state.bins[1].x_amount
    assert bin1_x_before > 0

    p0 = float(bin_price(0, 100))
    p2 = float(bin_price(2, 100))
    result = simulate_price_path(state, [p0, p2])

    assert state.bins[1].x_amount == 0
    assert state.bins[1].y_amount == pytest.approx(bin1_x_before * state.bins[1].price)
    assert result.crossed_bin_conversions >= 1
    assert result.simulator_fidelity == SIMULATOR_FIDELITY


def test_downward_move_converts_completed_bid_bin_y_to_x():
    state = create_position(
        min_bin_id=-2,
        max_bin_id=1,
        active_id=0,
        bin_step=100,
        amount_x=1.0,
        amount_y=100.0,
    )
    bin_minus1_y_before = state.bins[-1].y_amount
    assert bin_minus1_y_before > 0

    p0 = float(bin_price(0, 100))
    p_minus2 = float(bin_price(-2, 100))
    simulate_price_path(state, [p0, p_minus2])

    assert state.bins[-1].y_amount == 0
    assert state.bins[-1].x_amount == pytest.approx(bin_minus1_y_before / state.bins[-1].price)


def test_real_price_anchor_moves_relative_to_known_active_bin():
    state = create_position(
        min_bin_id=98,
        max_bin_id=103,
        active_id=100,
        bin_step=100,
        amount_x=5.0,
        amount_y=50.0,
        entry_price=10.0,
    )

    price_two_bins_up = float(relative_bin_price(10.0, 2, 100))
    result = simulate_price_path(state, [10.0, price_two_bins_up])

    assert result.ending_active_id == 102
    assert state.bins[101].x_amount == 0


def test_fees_only_accrue_in_range_and_costs_reduce_pnl():
    state = create_position(
        min_bin_id=-1,
        max_bin_id=1,
        active_id=0,
        bin_step=100,
        amount_x=1.0,
        amount_y=100.0,
        entry_cost_quote=1.0,
    )
    p0 = float(bin_price(0, 100))
    p1 = float(bin_price(1, 100))
    p3 = float(bin_price(3, 100))

    result = simulate_price_path(
        state,
        [p0, p1, p3],
        attributable_fee_quote_by_step=[1.0, 2.0, 10.0],
        exit_cost_quote=1.0,
    )

    assert result.fees_quote == pytest.approx(3.0)
    assert result.costs_quote == pytest.approx(2.0)
    assert result.close_in_range_ratio == pytest.approx(2 / 3)
