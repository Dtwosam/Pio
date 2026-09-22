import pytest

from meteora_learner.chain_features import fee_checkpoint_activity, summarize_liquidity_shape


def test_liquidity_shape_uses_supply_around_active_bin():
    bins = [
        {"bin_id": 98, "liquidity_supply": "10"},
        {"bin_id": 100, "liquidity_supply": "30"},
        {"bin_id": 101, "liquidity_supply": "20"},
        {"bin_id": 110, "liquidity_supply": "40"},
    ]
    features = summarize_liquidity_shape(bins, active_bin_id=100, near_radius=2)

    assert features.total_liquidity_supply == 100
    assert features.active_liquidity_supply == 30
    assert features.near_active_liquidity_ratio == pytest.approx(0.6)
    assert features.below_active_liquidity_ratio == pytest.approx(0.1)
    assert features.above_active_liquidity_ratio == pytest.approx(0.6)
    assert features.liquidity_weighted_distance_bins == pytest.approx(4.4)


def test_fee_checkpoint_activity_is_not_confused_by_resets():
    previous = [
        {
            "bin_id": 100,
            "fee_amount_x_per_token_stored": "10",
            "fee_amount_y_per_token_stored": "20",
        },
        {
            "bin_id": 101,
            "fee_amount_x_per_token_stored": "50",
            "fee_amount_y_per_token_stored": "50",
        },
    ]
    current = [
        {
            "bin_id": 100,
            "fee_amount_x_per_token_stored": "15",
            "fee_amount_y_per_token_stored": "27",
        },
        {
            "bin_id": 101,
            "fee_amount_x_per_token_stored": "5",
            "fee_amount_y_per_token_stored": "55",
        },
    ]

    activity = fee_checkpoint_activity(previous, current)
    assert activity.matched_bins == 2
    assert activity.bins_with_x_growth == 1
    assert activity.bins_with_y_growth == 2
    assert activity.x_growth_raw == 5
    assert activity.y_growth_raw == 12
