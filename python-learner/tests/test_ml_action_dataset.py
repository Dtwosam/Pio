from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.ml_action_dataset import build_ml_action_dataset
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType


def save_snapshot(storage, minute, active_id, checkpoint):
    bins = []
    for bin_id in range(-3, 4):
        bins.append(
            {
                "bin_id": bin_id,
                "price": str(Q64),
                "amount_x": "1000",
                "amount_y": "1000",
                "liquidity_supply": str(2000 * Q64),
                "fee_amount_x_per_token_stored": "0",
                "fee_amount_y_per_token_stored": str(checkpoint),
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
                    "upper_bin_id": 3,
                    "bins": bins,
                }
            ],
        },
        observed_at=f"2026-09-23T00:{minute:02d}:00+00:00",
    )


def test_action_dataset_labels_multiple_actions_at_same_decision(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_snapshot(storage, 0, 0, 0)
    save_snapshot(storage, 5, 0, Q64)
    save_snapshot(storage, 10, 1, 2 * Q64)

    report = build_ml_action_dataset(
        str(storage.path),
        pool_address="pool",
        amount_x=0,
        amount_y=10,
        network_cost_y_atomic=0,
        lookback_observations=2,
        forward_observations=2,
        half_widths=(0, 1),
        center_offsets=(0,),
        strategies=(StrategyType.SPOT,),
        max_share_bps=500,
    )

    assert report.decision_points == 1
    assert report.examples_built == 2
    assert {item.half_width for item in report.examples} == {0, 1}
    assert {
        item.decision_observed_at for item in report.examples
    } == {"2026-09-23T00:05:00+00:00"}
    assert {
        item.forward_end_observed_at for item in report.examples
    } == {"2026-09-23T00:10:00+00:00"}
    assert sum(item.baseline_selected for item in report.examples) == 1
