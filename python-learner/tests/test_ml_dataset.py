from meteora_learner.baseline_policy import BaselinePolicyConfig
from meteora_learner.baseline_walk_forward import walk_forward_baseline
from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.ml_dataset import (
    ML_FEATURE_COLUMNS,
    ML_TARGET_COLUMNS,
    build_ml_dataset,
)
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType


def save_snapshot(storage, minute, *, active_id, fee_checkpoint):
    bins = []
    for bin_id in range(-1, 3):
        bins.append(
            {
                "bin_id": bin_id,
                "price": str(Q64),
                "amount_x": "1000",
                "amount_y": "1000",
                "liquidity_supply": str(2000 * Q64),
                "fee_amount_x_per_token_stored": "0",
                "fee_amount_y_per_token_stored": str(fee_checkpoint),
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
            "deposit_total_fee_rate": "1000000",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": -1,
                    "upper_bin_id": 2,
                    "bins": bins,
                }
            ],
        },
        observed_at=f"2026-09-23T00:{minute:02d}:00+00:00",
    )


def test_ml_dataset_uses_decision_state_and_forward_labels(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_snapshot(storage, 0, active_id=0, fee_checkpoint=0)
    save_snapshot(storage, 5, active_id=1, fee_checkpoint=Q64)
    save_snapshot(storage, 10, active_id=1, fee_checkpoint=2 * Q64)

    report = walk_forward_baseline(
        str(storage.path),
        pool_address="pool",
        amount_x=0,
        amount_y=10,
        phase2_gate=None,
        config=BaselinePolicyConfig(
            min_range_survival_ratio=0.0,
            require_fee_cost_recovery=False,
            estimated_network_cost_y_atomic=0,
        ),
        lookback_observations=2,
        forward_observations=2,
        half_widths=(1,),
        strategies=(StrategyType.SPOT,),
        max_share_bps=500,
    )

    dataset = build_ml_dataset(str(storage.path), [report])

    assert dataset.examples_built == 1
    example = dataset.examples[0]
    assert example.decision_observed_at == "2026-09-23T00:05:00+00:00"
    assert example.forward_end_observed_at == "2026-09-23T00:10:00+00:00"
    assert example.active_bin_id == 1
    assert example.active_bin_move_1 == 1
    assert example.fee_growth_bins_y > 0
    assert example.target_range_survival_ratio >= 0
    assert set(ML_FEATURE_COLUMNS).isdisjoint(ML_TARGET_COLUMNS)

    frame = dataset.to_frame()
    assert len(frame) == 1
    for column in ML_FEATURE_COLUMNS + ML_TARGET_COLUMNS:
        assert column in frame.columns
