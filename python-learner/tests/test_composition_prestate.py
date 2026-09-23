from meteora_learner.composition_prestate import (
    build_composition_prestate_candidates,
    ingest_prestate_verification,
)
from meteora_learner.liquidity_math import Q64
from meteora_learner.storage import Storage


TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def seed_ready_add(storage):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "capture_slot_start": 100,
            "capture_slot_end": 101,
            "clock_unix_timestamp": 1700000000,
            "active_bin_id": 5,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "token_x_program": TOKEN_PROGRAM,
            "token_y_program": TOKEN_PROGRAM,
            "base_fee_rate": "25000",
            "variable_fee_rate": "0",
            "total_fee_rate": "25000",
            "deposit_total_fee_rate": "25000",
            "protocol_share_bps": 1000,
            "collect_fee_mode": 0,
            "supports_limit_order": False,
            "reward_mints": ["11111111111111111111111111111111"] * 2,
            "reward_rates": ["0", "0"],
            "reward_duration_ends": [0, 0],
            "reward_last_update_times": [0, 0],
            "fee_state": {
                "base_factor": 100,
                "filter_period": 30,
                "decay_period": 120,
                "reduction_factor": 5000,
                "variable_fee_control": 0,
                "max_volatility_accumulator": 1000000,
                "base_fee_power_factor": 0,
                "volatility_accumulator": 0,
                "volatility_reference": 0,
                "index_reference": 5,
                "last_update_timestamp": 1699999990,
            },
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": 0,
                    "upper_bin_id": 69,
                    "bins": [
                        {
                            "bin_id": 5,
                            "price": str(Q64),
                            "amount_x": "100000",
                            "amount_y": "100000",
                            "liquidity_supply": str(200000 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": "0",
                            "reward_per_token_stored": ["0", "0"],
                        }
                    ],
                }
            ],
        },
        observed_at="2026-09-23T00:00:00+00:00",
    )
    storage.save_position_events(
        [
            {
                "observed_at": "2026-09-23T00:01:00+00:00",
                "position_address": "position",
                "signature": "sig",
                "ix_index": 2,
                "event_type": "add",
                "block_time": 1700000010,
                "slot": 120,
                "pool_address": "pool",
                "user_address": "user",
                "token_x": "x",
                "token_y": "y",
                "amount_x": "100",
                "amount_y": "200",
                "amount_x_usd": "1",
                "amount_y_usd": "2",
                "total_usd": "3",
                "created_at": "2026-09-23T00:01:00Z",
                "raw": {},
            }
        ]
    )
    storage.save_chain_transaction_events(
        {
            "signature": "sig",
            "slot": 120,
            "block_time": 1700000010,
            "succeeded": True,
            "add_requests": [
                {
                    "instruction_index": 2,
                    "instruction_type": "add_liquidity_by_strategy",
                    "requested_amount_x": "100",
                    "requested_amount_y": "200",
                    "observed_active_id": 5,
                    "max_active_bin_slippage": 2,
                    "min_bin_id": 4,
                    "max_bin_id": 6,
                    "strategy_variant": 6,
                    "strategy_favor_x": False,
                    "explicit_distribution": [],
                    "weighted_distribution": [],
                }
            ],
            "events": [
                {
                    "event_index": 0,
                    "parent_ix_index": 2,
                    "event": {
                        "event_type": "AddLiquidity",
                        "event": {
                            "lb_pair": "pool",
                            "from": "user",
                            "position": "position",
                            "amount_x": "100",
                            "amount_y": "200",
                            "active_bin_id": 5,
                        },
                    },
                }
            ],
        }
    )


def test_prestate_candidate_requires_slot_bounded_matching_capture(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready_add(storage)

    report = build_composition_prestate_candidates(
        str(storage.path),
        position_address="position",
    )

    assert report.add_events == 1
    assert report.verification_ready == 1
    candidate = report.candidates[0]
    assert candidate.snapshot_observed_at == "2026-09-23T00:00:00+00:00"
    assert candidate.capture_slot_start == 100
    assert candidate.capture_slot_end == 101
    assert candidate.verification_addresses == ("pool", "array")
    assert candidate.eligible_for_verification is True


def test_prestate_verification_ingest_is_persisted(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready_add(storage)
    result = ingest_prestate_verification(
        storage,
        {
            "signature": "sig",
            "transaction_slot": 120,
            "capture_slot_start": 100,
            "capture_slot_end": 101,
            "eligible": True,
            "reasons": [],
            "account_checks": [],
        },
        snapshot_observed_at="2026-09-23T00:00:00+00:00",
        pool_address="pool",
    )
    assert result.eligible is True
