from meteora_learner.deposit_plan import (
    distribute_standard_spl_deposit,
    project_deposit_shares,
)
from meteora_learner.liquidity_math import Q64
from meteora_learner.strategy import StrategyType


def prices(*bin_ids):
    return {bin_id: Q64 for bin_id in bin_ids}


def test_curve_distribution_matches_side_local_sdk_weights():
    plan = distribute_standard_spl_deposit(
        active_id=0,
        min_bin_id=-2,
        max_bin_id=2,
        amount_x=300,
        amount_y=600,
        strategy=StrategyType.CURVE,
        prices_q64=prices(-2, -1, 0, 1, 2),
    )

    by_bin = {item.bin_id: item for item in plan.bins}
    assert by_bin[-2].amount_y == 100
    assert by_bin[-1].amount_y == 200
    assert by_bin[0].amount_y == 300
    assert by_bin[1].amount_x == 200
    assert by_bin[2].amount_x == 100
    assert plan.idle_x == 0
    assert plan.idle_y == 0


def test_bid_ask_distribution_pushes_amount_toward_edges():
    plan = distribute_standard_spl_deposit(
        active_id=0,
        min_bin_id=-2,
        max_bin_id=2,
        amount_x=300,
        amount_y=600,
        strategy=StrategyType.BID_ASK,
        prices_q64=prices(-2, -1, 0, 1, 2),
    )

    by_bin = {item.bin_id: item for item in plan.bins}
    assert by_bin[-2].amount_y == 300
    assert by_bin[-1].amount_y == 200
    assert by_bin[0].amount_y == 100
    assert by_bin[1].amount_x == 100
    assert by_bin[2].amount_x == 200


def test_flooring_dust_is_reported_not_invented():
    plan = distribute_standard_spl_deposit(
        active_id=0,
        min_bin_id=0,
        max_bin_id=2,
        amount_x=10,
        amount_y=10,
        strategy=StrategyType.SPOT,
        prices_q64=prices(0, 1, 2),
    )
    assert plan.deposited_y == 10
    assert plan.deposited_x == 10
    assert plan.idle_y == 0
    assert plan.idle_x == 0


def test_share_projection_uses_chain_bin_supply():
    plan = distribute_standard_spl_deposit(
        active_id=0,
        min_bin_id=0,
        max_bin_id=0,
        amount_x=0,
        amount_y=30,
        strategy=StrategyType.SPOT,
        prices_q64=prices(0),
    )
    projection = project_deposit_shares(
        plan,
        [
            {
                "bin_id": 0,
                "price": str(Q64),
                "amount_x": "100",
                "amount_y": "200",
                "liquidity_supply": str(300 * Q64),
            }
        ],
    )
    assert projection.bins[0].liquidity_share_minted == 30 * Q64
    assert projection.empty_bin_initializations == 0
    assert projection.fidelity == "SDK_EXISTING_BIN_SHARE_V1"


def test_empty_bin_projection_is_explicitly_lower_fidelity():
    plan = distribute_standard_spl_deposit(
        active_id=0,
        min_bin_id=1,
        max_bin_id=1,
        amount_x=10,
        amount_y=0,
        strategy=StrategyType.SPOT,
        prices_q64=prices(1),
    )
    projection = project_deposit_shares(
        plan,
        [
            {
                "bin_id": 1,
                "price": str(Q64),
                "amount_x": "0",
                "amount_y": "0",
                "liquidity_supply": "0",
            }
        ],
    )
    assert projection.bins[0].liquidity_share_minted == 10 * Q64
    assert projection.empty_bin_initializations == 1
    assert projection.fidelity == "SDK_EXISTING_PLUS_INVARIANT_EMPTY_BIN_V1"
