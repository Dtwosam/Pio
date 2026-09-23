import pytest

from meteora_learner.live_position_outcome import build_live_position_outcome
from meteora_learner.storage import Storage


def seed_closed_position(storage, *, missing_effect_fee=False):
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
                'enter', 'sig-enter', '2026-09-23T10:00:00+00:00',
                -5, 5,
                'settle', 'sig-settle', '2026-09-23T12:00:00+00:00',
                1, 'settle', 'sig-settle', '{}'
            )
            """
        )
        effects = [
            (
                "enter",
                "sig-enter",
                "2026-09-23T10:00:00+00:00",
                "ENTER",
                "-100",
                "-200",
                "3",
                "4",
                "0",
                "0",
                "0",
                "0",
                None if missing_effect_fee else 5000,
            ),
            (
                "rebalance",
                "sig-rebalance",
                "2026-09-23T11:00:00+00:00",
                "REBALANCE",
                "10",
                "-20",
                "0",
                "0",
                "7",
                "8",
                "9",
                "10",
                6000,
            ),
            (
                "exit",
                "sig-exit",
                "2026-09-23T11:30:00+00:00",
                "EXIT",
                "120",
                "250",
                "0",
                "0",
                "0",
                "0",
                "0",
                "0",
                7000,
            ),
        ]
        for row in effects:
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
                ) VALUES (?, ?, ?, ?, 'pool', 'position',
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '{}')
                """,
                row,
            )

        conn.execute(
            """
            INSERT INTO live_execution_receipts(
                decision_id, signature, observed_at, mode, action,
                pool_address, intent_status, slot, block_time,
                network_fee_lamports, compute_units_consumed,
                succeeded, event_count, add_request_count,
                rebalance_request_count, raw_json
            ) VALUES (
                'settle', 'sig-settle',
                '2026-09-23T12:00:00+00:00',
                'LIVE', 'EXIT', 'pool', 'CONFIRMED',
                300, 400, 8000, 100000,
                1, 0, 0, 0, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_position_closure_proofs(
                decision_id, signature, position_address,
                receipt_slot, proof_slot, observed_at, closed, raw_json
            ) VALUES (
                'settle', 'sig-settle', 'position',
                300, 301, '2026-09-23T12:00:01+00:00', 1, '{}'
            )
            """
        )


def test_closed_live_outcome_aggregates_atomic_economics_and_costs(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_closed_position(storage)

    result = build_live_position_outcome(
        storage,
        position_address="position",
    )

    outcome = result.outcome
    assert result.reused_existing is False
    assert outcome.execution_count == 4
    assert outcome.token_x_wallet_delta_atomic == 30
    assert outcome.token_y_wallet_delta_atomic == 30
    assert outcome.composition_fee_x_atomic == 3
    assert outcome.composition_fee_y_atomic == 4
    assert outcome.earned_fee_x_atomic == 7
    assert outcome.earned_fee_y_atomic == 8
    assert outcome.reward_one_atomic == 9
    assert outcome.reward_two_atomic == 10
    assert outcome.network_fee_lamports == 26000
    assert outcome.label_status == "ATOMIC_ONLY"


def test_closed_live_outcome_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_closed_position(storage)

    first = build_live_position_outcome(
        storage,
        position_address="position",
    )
    second = build_live_position_outcome(
        storage,
        position_address="position",
    )

    assert first.reused_existing is False
    assert second.reused_existing is True


def test_atomic_outcome_requires_exact_network_costs(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_closed_position(storage, missing_effect_fee=True)

    with pytest.raises(ValueError, match="network fee"):
        build_live_position_outcome(
            storage,
            position_address="position",
        )


def test_open_position_cannot_create_completed_outcome(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_closed_position(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE live_positions
            SET status = 'OPEN'
            WHERE position_address = 'position'
            """
        )

    with pytest.raises(ValueError, match="CLOSED"):
        build_live_position_outcome(
            storage,
            position_address="position",
        )
