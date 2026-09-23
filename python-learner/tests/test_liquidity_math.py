import pytest

from meteora_learner.liquidity_math import (
    Q64,
    amounts_from_liquidity_share,
    fee_from_checkpoint_delta,
    reward_from_checkpoint_delta,
    mint_liquidity_share,
    project_deposit,
    q64_liquidity,
)


def test_q64_liquidity_matches_meteora_rebalance_primitive():
    assert q64_liquidity(10, 20, Q64) == 30 * Q64


def test_existing_bin_share_is_proportional_to_liquidity():
    share = mint_liquidity_share(
        amount_x=10,
        amount_y=20,
        price_q64=Q64,
        bin_amount_x=100,
        bin_amount_y=200,
        liquidity_supply=300 * Q64,
    )
    assert share == 30 * Q64

    post = project_deposit(
        amount_x=10,
        amount_y=20,
        price_q64=Q64,
        bin_amount_x=100,
        bin_amount_y=200,
        liquidity_supply=300 * Q64,
    )
    assert post.post_liquidity_supply == 330 * Q64


def test_empty_bin_initializes_supply_to_deposit_liquidity():
    share = mint_liquidity_share(
        amount_x=5,
        amount_y=0,
        price_q64=2 * Q64,
        bin_amount_x=0,
        bin_amount_y=0,
        liquidity_supply=0,
    )
    assert share == 10 * Q64


def test_share_round_trip_matches_withdrawal_inverse():
    x, y = amounts_from_liquidity_share(
        liquidity_share=30 * Q64,
        bin_amount_x=110,
        bin_amount_y=220,
        liquidity_supply=330 * Q64,
    )
    assert x == 10
    assert y == 20


def test_fee_checkpoint_delta_matches_q64_scaling():
    fee = fee_from_checkpoint_delta(
        liquidity_share=10 * Q64,
        fee_per_token_delta=2 * Q64,
    )
    assert fee == 20


def test_inconsistent_zero_supply_state_is_rejected():
    with pytest.raises(ValueError):
        mint_liquidity_share(
            amount_x=1,
            amount_y=0,
            price_q64=Q64,
            bin_amount_x=5,
            bin_amount_y=0,
            liquidity_supply=0,
        )



def test_reward_checkpoint_delta_uses_same_q64_scaling_as_dynamic_position():
    reward = reward_from_checkpoint_delta(
        liquidity_share=10 * Q64,
        reward_per_token_delta=3 * Q64,
    )
    assert reward == 30
