import pytest

from meteora_learner.composition_fee import (
    composition_fee_amount,
    protocol_fee_amount,
    simulate_active_bin_composition_fee,
)
from meteora_learner.liquidity_math import Q64


def test_composition_fee_formula_matches_source_backed_docs():
    fee = composition_fee_amount(10_000, 10_000_000)
    assert fee == 101


def test_protocol_share_is_basis_points_of_composition_fee():
    assert protocol_fee_amount(101, 1_000) == 10


def test_balanced_active_bin_deposit_has_no_composition_fee():
    result = simulate_active_bin_composition_fee(
        amount_x=10_000,
        amount_y=10_000,
        price_q64=Q64,
        bin_amount_x=100_000,
        bin_amount_y=100_000,
        liquidity_supply=200_000 * Q64,
        total_fee_rate=10_000_000,
        protocol_share_bps=1_000,
    )
    assert result.amount_x_into_bin == 10_000
    assert result.amount_y_into_bin == 10_000
    assert result.composition_fee_x == 0
    assert result.composition_fee_y == 0


def test_y_heavy_active_deposit_pays_composition_fee_in_y():
    result = simulate_active_bin_composition_fee(
        amount_x=0,
        amount_y=20_000,
        price_q64=Q64,
        bin_amount_x=100_000,
        bin_amount_y=100_000,
        liquidity_supply=200_000 * Q64,
        total_fee_rate=10_000_000,
        protocol_share_bps=1_000,
    )
    assert result.amount_x_into_bin > 0
    assert result.amount_y_into_bin < 20_000
    assert result.composition_fee_x == 0
    assert result.composition_fee_y > 0
    assert result.protocol_fee_y == result.composition_fee_y // 10
    assert result.lp_fee_y == result.composition_fee_y - result.protocol_fee_y


def test_x_heavy_active_deposit_pays_composition_fee_in_x():
    result = simulate_active_bin_composition_fee(
        amount_x=20_000,
        amount_y=0,
        price_q64=Q64,
        bin_amount_x=100_000,
        bin_amount_y=100_000,
        liquidity_supply=200_000 * Q64,
        total_fee_rate=10_000_000,
        protocol_share_bps=1_000,
    )
    assert result.amount_y_into_bin > 0
    assert result.amount_x_into_bin < 20_000
    assert result.composition_fee_x > 0
    assert result.composition_fee_y == 0


def test_empty_bin_has_no_composition_fee():
    result = simulate_active_bin_composition_fee(
        amount_x=10,
        amount_y=20,
        price_q64=Q64,
        bin_amount_x=0,
        bin_amount_y=0,
        liquidity_supply=0,
        total_fee_rate=10_000_000,
        protocol_share_bps=1_000,
    )
    assert result.composition_fee_x == 0
    assert result.composition_fee_y == 0
    assert result.amount_x_into_bin == 10
    assert result.amount_y_into_bin == 20


def test_invalid_fee_rate_is_rejected():
    with pytest.raises(ValueError):
        composition_fee_amount(100, 100_000_001)
