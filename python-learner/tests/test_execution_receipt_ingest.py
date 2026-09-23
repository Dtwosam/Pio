import pytest

from meteora_learner.execution_receipt_ingest import ingest_execution_receipt
from meteora_learner.storage import Storage


def receipt(**overrides):
    payload = {
        "decision_id": "decision-1",
        "signature": "signature-1",
        "mode": "LIVE",
        "action": "ENTER",
        "pool_address": "pool-1",
        "intent_status": "CONFIRMED",
        "slot": 123,
        "block_time": 456,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 100000,
        "succeeded": True,
        "event_count": 2,
        "add_request_count": 1,
        "rebalance_request_count": 0,
    }
    payload.update(overrides)
    return payload


def chain_snapshot(**overrides):
    payload = {
        "signature": "signature-1",
        "slot": 123,
        "block_time": 456,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 100000,
        "succeeded": True,
        "add_requests": [],
        "rebalance_requests": [],
        "events": [],
    }
    payload.update(overrides)
    return payload


def test_execution_receipt_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    first = ingest_execution_receipt(storage, receipt())
    second = ingest_execution_receipt(storage, receipt())

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.chain_snapshot_reconciled is False
    with storage.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM live_execution_receipts"
        ).fetchone()[0] == 1


def test_execution_receipt_reconciles_stored_chain_snapshot(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(chain_snapshot())

    result = ingest_execution_receipt(storage, receipt())

    assert result.chain_snapshot_reconciled is True


def test_execution_receipt_conflict_with_chain_snapshot_fails(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(
        chain_snapshot(network_fee_lamports=9999)
    )

    with pytest.raises(ValueError, match="conflicts with stored"):
        ingest_execution_receipt(storage, receipt())


def test_decision_id_cannot_change_receipt(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    ingest_execution_receipt(storage, receipt())

    with pytest.raises(ValueError, match="different execution receipt"):
        ingest_execution_receipt(
            storage,
            receipt(network_fee_lamports=5001),
        )


def test_signature_cannot_link_to_two_decisions(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    ingest_execution_receipt(storage, receipt())

    with pytest.raises(ValueError, match="another decision_id"):
        ingest_execution_receipt(
            storage,
            receipt(decision_id="decision-2"),
        )


@pytest.mark.parametrize(
    ("status", "succeeded"),
    [
        ("CONFIRMED", False),
        ("FAILED", True),
    ],
)
def test_terminal_status_must_match_chain_outcome(
    tmp_path,
    status,
    succeeded,
):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError):
        ingest_execution_receipt(
            storage,
            receipt(intent_status=status, succeeded=succeeded),
        )


def test_failed_execution_receipt_is_supported(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    result = ingest_execution_receipt(
        storage,
        receipt(intent_status="FAILED", succeeded=False),
    )

    assert result.reused_existing is False
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT intent_status, succeeded
            FROM live_execution_receipts
            WHERE decision_id = 'decision-1'
            """
        ).fetchone()
    assert row == ("FAILED", 0)
