from types import SimpleNamespace

import pytest

from meteora_learner.baseline_policy import BaselinePolicyConfig
from meteora_learner.baseline_walk_forward import walk_forward_baseline
from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType


def save_snapshot(storage, minute, active_id=0):
    bins = []
    for bin_id in range(-2, 4):
        bins.append(
            {
                "bin_id": bin_id,
                "price": str(Q64),
                "amount_x": "1000",
                "amount_y": "1000",
                "liquidity_supply": str(2000 * Q64),
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
                    "lower_bin_id": -2,
                    "upper_bin_id": 3,
                    "bins": bins,
                }
            ],
        },
        observed_at=f"2026-09-23T00:{minute:02d}:00+00:00",
    )


def test_walk_forward_keeps_selection_before_forward_window(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_snapshot(storage, 0, active_id=0)
    save_snapshot(storage, 5, active_id=0)
    save_snapshot(storage, 10, active_id=1)
    save_snapshot(storage, 15, active_id=1)

    gate = SimpleNamespace(
        promotion_ready=False,
        reasons=("Phase 2 evidence incomplete",),
    )
    report = walk_forward_baseline(
        str(storage.path),
        pool_address="pool",
        amount_x=0,
        amount_y=10,
        phase2_gate=gate,
        config=BaselinePolicyConfig(
            min_range_survival_ratio=0.0,
            require_fee_cost_recovery=False,
            estimated_network_cost_y_atomic=0,
        ),
        lookback_observations=2,
        forward_observations=2,
        half_widths=(0,),
        strategies=(StrategyType.SPOT,),
        max_share_bps=500,
    )

    assert report.steps == 2
    first = report.steps_detail[0]
    assert first.training_start_observed_at == "2026-09-23T00:00:00+00:00"
    assert first.decision_observed_at == "2026-09-23T00:05:00+00:00"
    assert first.forward_end_observed_at == "2026-09-23T00:10:00+00:00"
    assert first.proposal is not None
    assert first.proposal.decision_active_bin_id == 0
    assert first.proposal.min_bin_id == 0
    assert first.proposal.max_bin_id == 0
    assert report.research_only is True


def test_walk_forward_rejects_overlapping_forward_windows(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for minute in (0, 5, 10, 15):
        save_snapshot(storage, minute)

    gate = SimpleNamespace(promotion_ready=False, reasons=())
    with pytest.raises(ValueError, match="prevent overlapping"):
        walk_forward_baseline(
            str(storage.path),
            pool_address="pool",
            amount_x=0,
            amount_y=10,
            phase2_gate=gate,
            config=BaselinePolicyConfig(
                estimated_network_cost_y_atomic=0,
            ),
            lookback_observations=2,
            forward_observations=3,
            step_observations=1,
            half_widths=(0,),
            strategies=(StrategyType.SPOT,),
        )
