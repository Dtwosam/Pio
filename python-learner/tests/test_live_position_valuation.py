import json

import pytest

from meteora_learner.live_position_outcome import build_live_position_outcome
from meteora_learner.live_position_valuation import (
    WSOL_MINT,
    value_live_position_outcome,
)
from meteora_learner.quote_registry import save_token_quote
from meteora_learner.storage import Storage


T1 = "2026-09-23T09:00:00+00:00"
T2 = "2026-09-23T09:10:00+00:00"
T3 = "2026-09-23T09:20:00+00:00"


def seed(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_positions(
                position_address, pool_address, status,
                opened_decision_id, opened_signature, opened_at,
                min_bin_id, max_bin_id,
                last_decision_id, last_signature, last_observed_at,
                rebalances, closed_decision_id, closed_signature, raw_json
            ) VALUES (
                'position', 'pool', 'CLOSED',
                'enter', 'sig-enter', ?, -5, 5,
                'settle', 'sig-settle', ?, 0,
                'settle', 'sig-settle', '{}'
            )
            """,
            (T1, T3),
        )
        for observed_at in (T1, T2, T3):
            conn.execute(
                """
                INSERT INTO chain_pool_snapshots(
                    observed_at, pool_address, active_bin_id, bin_step,
                    token_x_mint, token_y_mint,
                    reward_mint_0, reward_mint_1, raw_json
                ) VALUES (?, 'pool', 0, 10, 'x', 'y', 'r0', NULL, '{}')
                """,
                (observed_at,),
            )

        effects = [
            (
                'enter', 'sig-enter', T1,
                '-100', '-200', '2', '0',
                '0', '0', '0', '0', 5000,
            ),
            (
                'exit', 'sig-exit', T2,
                '90', '210', '0', '0',
                '0', '0', '0', '0', 5000,
            ),
            (
                'settle', 'sig-settle', T3,
                '0', '0', '0', '0',
                '10', '20', '3', '0', 5000,
            ),
        ]
        for (
            decision, signature, observed_at,
            dx, dy, cx, cy, fx, fy, r0, r1, network,
        ) in effects:
            conn.execute(
                """
                INSERT INTO live_execution_effects(
                    decision_id, signature, observed_at, action,
                    pool_address, position_address,
                    token_x_wallet_delta_atomic,
                    token_y_wallet_delta_atomic,
                    composition_fee_x_atomic,
                    composition_fee_y_atomic,
                    earned_fee_x_atomic, earned_fee_y_atomic,
                    reward_one_atomic, reward_two_atomic,
                    network_fee_lamports, chain_event_count, raw_json
                ) VALUES (?, ?, ?, 'EXIT', 'pool', 'position',
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '{}')
                """,
                (
                    decision, signature, observed_at,
                    dx, dy, cx, cy, fx, fy, r0, r1, network,
                ),
            )

        conn.execute(
            """
            INSERT INTO live_execution_receipts(
                decision_id, signature, observed_at, mode, action,
                pool_address, intent_status, slot, block_time,
                network_fee_lamports, compute_units_consumed, succeeded,
                event_count, add_request_count, rebalance_request_count,
                raw_json
            ) VALUES (
                'settle', 'sig-settle', ?, 'LIVE', 'EXIT',
                'pool', 'CONFIRMED', 30, 120,
                5000, 90000, 1, 3, 0, 0, '{}'
            )
            """,
            (T3,),
        )
        conn.execute(
            """
            INSERT INTO live_position_closure_proofs(
                decision_id, signature, position_address,
                receipt_slot, proof_slot, observed_at, closed, raw_json
            ) VALUES (
                'settle', 'sig-settle', 'position',
                30, 31, ?, 1, '{}'
            )
            """,
            (T3,),
        )

        outcome = {
            "position_address": "position",
            "pool_address": "pool",
            "opened_decision_id": "enter",
            "closed_decision_id": "settle",
            "execution_count": 3,
            "token_x_wallet_delta_atomic": -10,
            "token_y_wallet_delta_atomic": 10,
            "composition_fee_x_atomic": 2,
            "composition_fee_y_atomic": 0,
            "earned_fee_x_atomic": 10,
            "earned_fee_y_atomic": 20,
            "reward_one_atomic": 3,
            "reward_two_atomic": 0,
            "network_fee_lamports": 15000,
            "label_status": "ATOMIC_ONLY",
        }
        conn.execute(
            """
            INSERT INTO live_position_outcomes(
                position_address, pool_address,
                opened_decision_id, closed_decision_id,
                execution_count,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic,
                composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, label_status,
                created_at, raw_json
            ) VALUES (
                'position', 'pool', 'enter', 'settle', 3,
                '-10', '10', '2', '0', '10', '20',
                '3', '0', 15000, 'ATOMIC_ONLY', ?, ?
            )
            """,
            (T3, json.dumps(outcome, sort_keys=True, separators=(",", ":"))),
        )

    for observed_at in (T1, T2, T3):
        save_token_quote(
            storage,
            token_mint="x",
            quote_per_atomic=2,
            source="TEST",
            observed_at=observed_at,
        )
        save_token_quote(
            storage,
            token_mint="y",
            quote_per_atomic=1,
            source="TEST",
            observed_at=observed_at,
        )
        save_token_quote(
            storage,
            token_mint="r0",
            quote_per_atomic=5,
            source="TEST",
            observed_at=observed_at,
        )
        save_token_quote(
            storage,
            token_mint=WSOL_MINT,
            quote_per_atomic=0.0000001,
            source="TEST",
            observed_at=observed_at,
        )


def test_live_outcome_valuation_uses_execution_time_cashflows(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)

    result = value_live_position_outcome(
        storage,
        position_address="position",
        max_age_seconds=60,
    )

    assert result.reused_existing is False
    value = result.valuation
    assert value.principal_cashflow_quote == "-10"
    assert value.composition_cost_quote == "4"
    assert value.fee_income_quote == "40"
    assert value.reward_income_quote == "15"
    assert value.network_cost_quote == "0.0015"
    assert value.realized_pnl_quote == "40.9985"
    assert value.entry_outflow_quote == "404.0005"
    assert value.realized_return_bps == 1014

    with storage.connect() as conn:
        assert conn.execute(
            """
            SELECT label_status
            FROM live_position_outcomes
            WHERE position_address = 'position'
            """
        ).fetchone()[0] == "VALUED"


def test_live_valuation_is_idempotent_and_outcome_rebuild_stays_valid(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)

    first = value_live_position_outcome(
        storage,
        position_address="position",
        max_age_seconds=60,
    )
    second = value_live_position_outcome(
        storage,
        position_address="position",
        max_age_seconds=60,
    )

    assert first.reused_existing is False
    assert second.reused_existing is True

    rebuilt = build_live_position_outcome(
        storage,
        position_address="position",
    )
    assert rebuilt.reused_existing is True
    assert rebuilt.outcome.label_status == "VALUED"


def test_future_only_reward_quote_is_rejected(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            DELETE FROM token_quote_observations
            WHERE token_mint = 'r0'
            """
        )
    save_token_quote(
        storage,
        token_mint="r0",
        quote_per_atomic=5,
        source="FUTURE",
        observed_at="2026-09-23T09:21:00+00:00",
    )

    with pytest.raises(ValueError, match="no persisted"):
        value_live_position_outcome(
            storage,
            position_address="position",
            max_age_seconds=60,
        )


def test_missing_settlement_effect_blocks_valuation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    with storage.connect() as conn:
        conn.execute(
            "DELETE FROM live_execution_effects WHERE decision_id = 'settle'"
        )

    with pytest.raises(ValueError, match="every execution"):
        value_live_position_outcome(
            storage,
            position_address="position",
            max_age_seconds=60,
        )
