from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM, replay_latest_small_lp_interval
from meteora_learner.liquidity_math import Q64
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType


def save_snapshot(storage, observed_at, *, y_amount, fee_y_per_token, active=0):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": active,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
            "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
            "base_fee_rate": "1000",
            "variable_fee_rate": "0",
            "total_fee_rate": "1000",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": 0,
                    "upper_bin_id": 0,
                    "bins": [
                        {
                            "bin_id": 0,
                            "price": str(Q64),
                            "amount_x": "0",
                            "amount_y": str(y_amount),
                            "liquidity_supply": str(100 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": str(fee_y_per_token),
                        }
                    ],
                }
            ],
        },
        observed_at=observed_at,
    )


def test_small_lp_replay_marks_inventory_and_diluted_fees(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_snapshot(storage, "2026-09-22T00:00:00+00:00", y_amount=100, fee_y_per_token=0)
    save_snapshot(
        storage,
        "2026-09-22T00:05:00+00:00",
        y_amount=110,
        fee_y_per_token=100 * Q64,
    )

    result = replay_latest_small_lp_interval(
        str(db),
        pool_address="pool",
        amount_x=0,
        amount_y=1,
        min_bin_id=0,
        max_bin_id=0,
        strategy=StrategyType.SPOT,
        max_share_bps=200,
    )

    assert result.deposited_y == 1
    assert result.ending_y == 1
    assert result.bins[0].share_bps_of_start_supply == 100
    assert result.fee_y == 99
    assert result.replay_fidelity == "SMALL_LP_CHAIN_COUNTERFACTUAL_V1"


def test_replay_rejects_position_large_enough_to_distort_history(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    save_snapshot(storage, "2026-09-22T00:00:00+00:00", y_amount=100, fee_y_per_token=0)
    save_snapshot(storage, "2026-09-22T00:05:00+00:00", y_amount=100, fee_y_per_token=0)

    try:
        replay_latest_small_lp_interval(
            str(db),
            pool_address="pool",
            amount_x=0,
            amount_y=10,
            min_bin_id=0,
            max_bin_id=0,
            strategy=StrategyType.SPOT,
            max_share_bps=500,
        )
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("expected small-LP guard to reject oversized replay")



def test_replay_rejects_token_2022_until_transfer_fee_model_exists(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    observed = "2026-09-22T00:00:00+00:00"
    for offset in (0, 5):
        payload = {
            "pool_address": "pool",
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "token_x_program": "Token2022Unsupported",
            "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
            "base_fee_rate": "1000",
            "variable_fee_rate": "0",
            "total_fee_rate": "1000",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": 0,
                    "upper_bin_id": 0,
                    "bins": [
                        {
                            "bin_id": 0,
                            "price": str(Q64),
                            "amount_x": "0",
                            "amount_y": "100",
                            "liquidity_supply": str(100 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": "0",
                        }
                    ],
                }
            ],
        }
        storage.save_chain_pool_snapshot(
            payload,
            observed_at=f"2026-09-22T00:{offset:02d}:00+00:00",
        )

    try:
        replay_latest_small_lp_interval(
            str(db),
            pool_address="pool",
            amount_x=0,
            amount_y=1,
            min_bin_id=0,
            max_bin_id=0,
            strategy=StrategyType.SPOT,
        )
    except ValueError as exc:
        assert "Token-2022" in str(exc)
    else:
        raise AssertionError("expected Token-2022 replay to fail closed")
