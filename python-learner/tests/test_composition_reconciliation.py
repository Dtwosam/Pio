from meteora_learner.composition_fee import simulate_active_bin_composition_fee
from meteora_learner.composition_reconciliation import (
    build_composition_fee_reconciliation,
)
from meteora_learner.liquidity_math import Q64
from meteora_learner.storage import Storage


TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def seed(storage):
    observed = "2026-09-23T00:00:00+00:00"
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
            "base_fee_rate": "10000000",
            "variable_fee_rate": "0",
            "total_fee_rate": "10000000",
            "deposit_total_fee_rate": "10000000",
            "protocol_share_bps": 1000,
            "collect_fee_mode": 0,
            "supports_limit_order": False,
            "reward_mints": ["11111111111111111111111111111111"] * 2,
            "reward_rates": ["0", "0"],
            "reward_duration_ends": [0, 0],
            "reward_last_update_times": [0, 0],
            "fee_state": {
                "base_factor": 40000,
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
                            "bin_id": 4,
                            "price": str(Q64),
                            "amount_x": "0",
                            "amount_y": "100000",
                            "liquidity_supply": str(100000 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": "0",
                            "reward_per_token_stored": ["0", "0"],
                        },
                        {
                            "bin_id": 5,
                            "price": str(Q64),
                            "amount_x": "100000",
                            "amount_y": "100000",
                            "liquidity_supply": str(200000 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": "0",
                            "reward_per_token_stored": ["0", "0"],
                        },
                        {
                            "bin_id": 6,
                            "price": str(Q64),
                            "amount_x": "100000",
                            "amount_y": "0",
                            "liquidity_supply": str(100000 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": "0",
                            "reward_per_token_stored": ["0", "0"],
                        },
                    ],
                }
            ],
        },
        observed_at=observed,
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
                "amount_x": "20000",
                "amount_y": "20000",
                "amount_x_usd": "1",
                "amount_y_usd": "2",
                "total_usd": "3",
                "created_at": "2026-09-23T00:01:00Z",
                "raw": {},
            }
        ]
    )

    active_amount_y = 10_000
    expected = simulate_active_bin_composition_fee(
        amount_x=0,
        amount_y=active_amount_y,
        price_q64=Q64,
        bin_amount_x=100000,
        bin_amount_y=100000,
        liquidity_supply=200000 * Q64,
        total_fee_rate=10_000_000,
        protocol_share_bps=1000,
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
                    "requested_amount_x": "20000",
                    "requested_amount_y": "20000",
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
                            "amount_x": "20000",
                            "amount_y": "20000",
                            "active_bin_id": 5,
                        },
                    },
                },
                {
                    "event_index": 1,
                    "parent_ix_index": 2,
                    "event": {
                        "event_type": "CompositionFee",
                        "event": {
                            "from": "user",
                            "bin_id": 5,
                            "token_x_fee_amount": str(expected.composition_fee_x),
                            "token_y_fee_amount": str(expected.composition_fee_y),
                            "protocol_token_x_fee_amount": str(expected.protocol_fee_x),
                            "protocol_token_y_fee_amount": str(expected.protocol_fee_y),
                        },
                    },
                },
            ],
        }
    )
    storage.save_composition_prestate_verification(
        {
            "signature": "sig",
            "transaction_slot": 120,
            "capture_slot_start": 100,
            "capture_slot_end": 101,
            "eligible": True,
            "reasons": [],
            "account_checks": [
                {
                    "address": "pool",
                    "target_signature_seen": True,
                    "conflicting_transactions": [],
                    "eligible": True,
                },
                {
                    "address": "array",
                    "target_signature_seen": True,
                    "conflicting_transactions": [],
                    "eligible": True,
                },
            ],
        },
        snapshot_observed_at=observed,
        pool_address="pool",
    )


def test_exact_composition_reconciliation_matches_verified_prestate(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)

    report = build_composition_fee_reconciliation(
        str(storage.path),
        position_address="position",
    )

    assert report.add_events == 1
    assert report.eligible_samples == 1
    assert report.exact_samples == 1
    assert report.mismatched_samples == 0
    assert report.exact_rate == 1.0
    sample = report.entries[0].sample
    assert sample is not None
    assert sample.active_bin_amount_x == 0
    assert sample.active_bin_amount_y == 10000
    assert sample.target_total_fee_rate == 10_000_000
    assert sample.exact_match is True


def test_composition_reconciliation_requires_verifier_verdict(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    with storage.connect() as conn:
        conn.execute("DELETE FROM composition_prestate_verifications")

    report = build_composition_fee_reconciliation(
        str(storage.path),
        position_address="position",
    )

    assert report.eligible_samples == 0
    assert "verification has not been ingested" in str(report.entries[0].reason)



def test_zero_fee_without_composition_event_is_not_formula_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            DELETE FROM chain_transaction_events
            WHERE signature = 'sig' AND event_type = 'CompositionFee'
            """
        )

    report = build_composition_fee_reconciliation(
        str(storage.path),
        position_address="position",
    )

    assert report.eligible_samples == 0
    assert report.exact_samples == 0
    assert (
        report.entries[0].reason
        == "no positive CompositionFee event for formula validation"
    )
