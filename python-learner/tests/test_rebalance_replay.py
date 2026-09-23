from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.rebalance_replay import replay_rebalance_lifecycle
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType


def save_snapshot(storage, minute, active_id):
    bins = []
    for bin_id in range(-3, 5):
        bins.append(
            {
                "bin_id": bin_id,
                "price": str(Q64),
                "amount_x": "100",
                "amount_y": "100",
                "liquidity_supply": str(200 * Q64),
                "fee_amount_x_per_token_stored": "0",
                "fee_amount_y_per_token_stored": "0",
            }
        )

    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": active_id,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
            "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
            "base_fee_rate": "0",
            "variable_fee_rate": "0",
            "total_fee_rate": "0",
            "deposit_total_fee_rate": "0",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": -3,
                    "upper_bin_id": 4,
                    "bins": bins,
                }
            ],
        },
        observed_at=f"2026-09-22T00:{minute:02d}:00+00:00",
    )


def test_rebalance_lifecycle_reenters_after_observed_range_exit(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_snapshot(storage, 0, 0)
    save_snapshot(storage, 5, 0)
    save_snapshot(storage, 10, 2)
    save_snapshot(storage, 15, 2)

    result = replay_rebalance_lifecycle(
        str(db),
        pool_address="pool",
        amount_x=0,
        amount_y=2,
        half_width=0,
        strategy=StrategyType.SPOT,
        observation_limit=4,
        max_share_bps=500,
    )

    assert result.entries == 2
    assert result.rebalances == 1
    assert result.segments[0].entry_active_bin_id == 0
    assert result.segments[0].exit_active_bin_id == 2
    assert result.segments[0].exit_reason == "OUT_OF_RANGE"
    assert result.segments[1].entry_active_bin_id == 2
    assert result.segments[1].exit_reason == "END_OF_WINDOW"
    assert result.lifecycle_fidelity == "OBSERVATION_BOUNDARY_REBALANCE_V1"


def test_rebalance_lifecycle_holds_when_range_survives(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_snapshot(storage, 0, 0)
    save_snapshot(storage, 5, 1)
    save_snapshot(storage, 10, 2)

    result = replay_rebalance_lifecycle(
        str(db),
        pool_address="pool",
        amount_x=1,
        amount_y=1,
        half_width=2,
        strategy=StrategyType.SPOT,
        observation_limit=3,
        max_share_bps=500,
    )

    assert result.entries == 1
    assert result.rebalances == 0
    assert result.segments[0].exit_reason == "END_OF_WINDOW"


def test_rebalance_lifecycle_does_not_silently_reinvest_fees(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_snapshot(storage, 0, 0)
    save_snapshot(storage, 5, 0)

    try:
        replay_rebalance_lifecycle(
            str(db),
            pool_address="pool",
            amount_x=1,
            amount_y=1,
            half_width=1,
            strategy=StrategyType.SPOT,
            reinvest_fees=True,
        )
    except ValueError as exc:
        assert "not validated" in str(exc)
    else:
        raise AssertionError("expected unvalidated fee compounding to fail closed")
