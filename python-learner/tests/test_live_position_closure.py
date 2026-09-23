import pytest

from meteora_learner.execution_receipt_ingest import ingest_execution_receipt
from meteora_learner.live_position_closure import finalize_live_position_closure
from meteora_learner.storage import Storage


def seed_position(storage, status="LIQUIDITY_REMOVED"):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_positions(
                position_address, pool_address, status,
                opened_decision_id, opened_signature, opened_at,
                min_bin_id, max_bin_id,
                last_decision_id, last_signature, last_observed_at,
                rebalances, raw_json
            ) VALUES (
                'position', 'pool', ?,
                'enter-decision', 'enter-signature', '2026-09-23T10:00:00+00:00',
                -5, 5,
                'exit-decision', 'exit-signature', '2026-09-23T11:00:00+00:00',
                0, '{}'
            )
            """,
            (status,),
        )


def seed_settlement_receipt(storage):
    storage.save_chain_transaction_events(
        {
            "signature": "settlement-signature",
            "slot": 200,
            "block_time": 300,
            "network_fee_lamports": 5000,
            "compute_units_consumed": 100000,
            "succeeded": True,
            "add_requests": [],
            "rebalance_requests": [],
            "events": [],
        }
    )
    ingest_execution_receipt(
        storage,
        {
            "decision_id": "settlement-decision",
            "signature": "settlement-signature",
            "mode": "LIVE",
            "action": "EXIT",
            "pool_address": "pool",
            "intent_status": "CONFIRMED",
            "slot": 200,
            "block_time": 300,
            "network_fee_lamports": 5000,
            "compute_units_consumed": 100000,
            "succeeded": True,
            "event_count": 0,
            "add_request_count": 0,
            "rebalance_request_count": 0,
        },
    )


def proof(slot=201, closed=True):
    return {
        "position": "position",
        "closed": closed,
        "rpc_context_slot": slot,
    }


def test_confirmed_settlement_closes_liquidity_removed_position(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_position(storage)
    seed_settlement_receipt(storage)

    result = finalize_live_position_closure(
        storage,
        decision_id="settlement-decision",
        proof=proof(),
    )

    assert result.closed is True
    assert result.reused_existing is False
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT status, closed_decision_id, closed_signature
            FROM live_positions
            WHERE position_address = 'position'
            """
        ).fetchone()
        event = conn.execute(
            """
            SELECT action, prior_status, next_status
            FROM live_position_events
            WHERE decision_id = 'settlement-decision'
            """
        ).fetchone()

    assert row == (
        "CLOSED",
        "settlement-decision",
        "settlement-signature",
    )
    assert event == ("CLOSE", "LIQUIDITY_REMOVED", "CLOSED")


def test_closure_proof_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_position(storage)
    seed_settlement_receipt(storage)

    first = finalize_live_position_closure(
        storage,
        decision_id="settlement-decision",
        proof=proof(),
    )
    second = finalize_live_position_closure(
        storage,
        decision_id="settlement-decision",
        proof=proof(),
    )

    assert first.reused_existing is False
    assert second.reused_existing is True
    with storage.connect() as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM live_position_closure_proofs
            """
        ).fetchone()[0] == 1


def test_closure_proof_cannot_precede_settlement_slot(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_position(storage)
    seed_settlement_receipt(storage)

    with pytest.raises(ValueError, match="cannot precede"):
        finalize_live_position_closure(
            storage,
            decision_id="settlement-decision",
            proof=proof(slot=199),
        )


def test_open_position_cannot_be_closed_by_settlement_proof(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_position(storage, status="OPEN")
    seed_settlement_receipt(storage)

    with pytest.raises(ValueError, match="liquidity removed"):
        finalize_live_position_closure(
            storage,
            decision_id="settlement-decision",
            proof=proof(),
        )


def test_negative_closure_proof_is_rejected(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_position(storage)
    seed_settlement_receipt(storage)

    with pytest.raises(ValueError, match="must confirm"):
        finalize_live_position_closure(
            storage,
            decision_id="settlement-decision",
            proof=proof(closed=False),
        )
