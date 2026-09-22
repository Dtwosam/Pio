from meteora_learner.deposit_plan import ProjectedBinShare, ProjectedDepositShares
from meteora_learner.fee_attribution import accrue_projected_fees
from meteora_learner.liquidity_math import Q64


def projection():
    return ProjectedDepositShares(
        bins=(
            ProjectedBinShare(
                bin_id=10,
                amount_x=0,
                amount_y=100,
                price_q64=Q64,
                existing_liquidity_supply=1000 * Q64,
                liquidity_share_minted=10 * Q64,
            ),
        ),
        total_liquidity_share_minted=10 * Q64,
        empty_bin_initializations=0,
        fidelity="SDK_EXISTING_BIN_SHARE_V1",
    )


def test_projected_fee_accrual_matches_dynamic_position_scaling():
    before = [
        {
            "bin_id": 10,
            "fee_amount_x_per_token_stored": str(3 * Q64),
            "fee_amount_y_per_token_stored": str(5 * Q64),
        }
    ]
    after = [
        {
            "bin_id": 10,
            "fee_amount_x_per_token_stored": str(5 * Q64),
            "fee_amount_y_per_token_stored": str(8 * Q64),
        }
    ]

    result = accrue_projected_fees(projection(), before, after)

    assert result.total_fee_x == 20
    assert result.total_fee_y == 30
    assert result.matched_bins == 1
    assert result.fidelity == "ONCHAIN_CHECKPOINT_FIXED_SHARE_V1"


def test_checkpoint_resets_do_not_create_negative_fees():
    before = [
        {
            "bin_id": 10,
            "fee_amount_x_per_token_stored": str(10 * Q64),
            "fee_amount_y_per_token_stored": str(10 * Q64),
        }
    ]
    after = [
        {
            "bin_id": 10,
            "fee_amount_x_per_token_stored": str(1 * Q64),
            "fee_amount_y_per_token_stored": str(12 * Q64),
        }
    ]

    result = accrue_projected_fees(projection(), before, after)

    assert result.total_fee_x == 0
    assert result.total_fee_y == 20


def test_missing_snapshot_bins_are_marked_partial():
    result = accrue_projected_fees(projection(), [], [])
    assert result.matched_bins == 0
    assert result.fidelity == "PARTIAL_ONCHAIN_CHECKPOINT_FIXED_SHARE_V1"
