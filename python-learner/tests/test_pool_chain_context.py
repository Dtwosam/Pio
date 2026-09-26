from __future__ import annotations

import pandas as pd
import pytest

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.pool_chain_context import (
    attach_pool_chain_context_from_store,
)
from meteora_learner.storage import Storage


def _snapshot(
    pool: str,
    *,
    active_bin_id: int,
    liquidity: int,
    fee_x: int,
    fee_y: int,
) -> dict:
    return {
        "pool_address": pool,
        "active_bin_id": active_bin_id,
        "bin_step": 25,
        "token_x_mint": f"{pool}-X",
        "token_y_mint": f"{pool}-Y",
        "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
        "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
        "base_fee_rate": "100",
        "variable_fee_rate": "20",
        "total_fee_rate": "120",
        "deposit_total_fee_rate": "140",
        "protocol_share_bps": 1000,
        "collect_fee_mode": 0,
        "supports_limit_order": True,
        "bin_arrays": [
            {
                "index": 0,
                "address": f"{pool}-BIN-ARRAY",
                "bins": [
                    {
                        "bin_id": active_bin_id - 1,
                        "price": "0.9",
                        "amount_x": "0",
                        "amount_y": "0",
                        "liquidity_supply": str(liquidity // 2),
                        "fee_amount_x_per_token_stored": str(
                            max(0, fee_x - 1)
                        ),
                        "fee_amount_y_per_token_stored": str(
                            max(0, fee_y - 1)
                        ),
                        "reward_per_token_stored_0": "0",
                        "reward_per_token_stored_1": "0",
                    },
                    {
                        "bin_id": active_bin_id,
                        "price": "1.0",
                        "amount_x": "0",
                        "amount_y": "0",
                        "liquidity_supply": str(liquidity),
                        "fee_amount_x_per_token_stored": str(fee_x),
                        "fee_amount_y_per_token_stored": str(fee_y),
                        "reward_per_token_stored_0": "0",
                        "reward_per_token_stored_1": "0",
                    },
                ],
            }
        ],
    }


def test_chain_context_exposes_current_and_previous_dynamics(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_pool_snapshot(
        _snapshot(
            "POOL",
            active_bin_id=10,
            liquidity=100,
            fee_x=5,
            fee_y=7,
        ),
        observed_at="2026-01-01T01:00:00+00:00",
    )
    storage.save_chain_pool_snapshot(
        _snapshot(
            "POOL",
            active_bin_id=11,
            liquidity=200,
            fee_x=15,
            fee_y=17,
        ),
        observed_at="2026-01-01T02:00:00+00:00",
    )

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T02:30:00Z",
            }
        ]
    )

    enriched, report = attach_pool_chain_context_from_store(
        str(storage.path),
        decisions,
        near_radius=2,
    )
    row = enriched.iloc[0]

    assert report.rows_with_chain_context == 1
    assert report.rows_with_previous_chain_snapshot == 1
    assert row["chain_observation_count"] == 2.0
    assert row["chain_has_previous_snapshot"] == 1.0
    assert row["chain_snapshot_age_seconds"] == 1800.0
    assert row["chain_seconds_since_previous_snapshot"] == 3600.0
    assert row["chain_active_bin_id"] == 11.0
    assert row["chain_active_bin_change"] == 1.0
    assert row["chain_total_liquidity_log10_change"] > 0.0
    assert row["chain_fee_checkpoint_x_growth_bins"] > 0.0
    assert row["chain_fee_checkpoint_y_growth_bins"] > 0.0
    assert row["chain_fee_checkpoint_x_growth_log10"] > 0.0


def test_future_chain_refresh_cannot_leak_backward(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_pool_snapshot(
        _snapshot(
            "POOL",
            active_bin_id=10,
            liquidity=100,
            fee_x=5,
            fee_y=7,
        ),
        observed_at="2026-01-01T01:00:00+00:00",
    )

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T01:30:00Z",
            }
        ]
    )
    before, _ = attach_pool_chain_context_from_store(
        str(storage.path),
        decisions,
    )

    storage.save_chain_pool_snapshot(
        _snapshot(
            "POOL",
            active_bin_id=99,
            liquidity=999999,
            fee_x=999999,
            fee_y=999999,
        ),
        observed_at="2026-01-01T03:00:00+00:00",
    )
    after, _ = attach_pool_chain_context_from_store(
        str(storage.path),
        decisions,
    )

    assert before.iloc[0]["chain_observation_count"] == 1.0
    assert after.iloc[0]["chain_observation_count"] == 1.0
    assert before.iloc[0]["chain_active_bin_id"] == pytest.approx(
        after.iloc[0]["chain_active_bin_id"]
    )
    assert before.iloc[0][
        "chain_total_liquidity_log10"
    ] == pytest.approx(
        after.iloc[0]["chain_total_liquidity_log10"]
    )


def test_single_chain_snapshot_is_usable_with_explicit_no_previous_flag(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_pool_snapshot(
        _snapshot(
            "POOL",
            active_bin_id=10,
            liquidity=100,
            fee_x=5,
            fee_y=7,
        ),
        observed_at="2026-01-01T01:00:00+00:00",
    )

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T01:30:00Z",
            }
        ]
    )
    enriched, report = attach_pool_chain_context_from_store(
        str(storage.path),
        decisions,
    )
    row = enriched.iloc[0]

    assert report.rows_with_chain_context == 1
    assert report.rows_with_previous_chain_snapshot == 0
    assert row["chain_has_previous_snapshot"] == 0.0
    assert row["chain_active_bin_change"] == 0.0
    assert row["chain_total_liquidity_log10_change"] == 0.0
    assert row["chain_fee_checkpoint_x_growth_log10"] == 0.0
