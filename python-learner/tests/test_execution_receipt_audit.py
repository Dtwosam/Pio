from meteora_learner.execution_receipt_audit import audit_execution_receipts
from meteora_learner.execution_receipt_ingest import ingest_execution_receipt
from meteora_learner.storage import Storage


def receipt(signature="sig", **overrides):
    payload = {
        "decision_id": f"decision-{signature}",
        "signature": signature,
        "mode": "LIVE",
        "action": "ENTER",
        "pool_address": "pool",
        "intent_status": "CONFIRMED",
        "slot": 10,
        "block_time": 20,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 90000,
        "succeeded": True,
        "event_count": 1,
        "add_request_count": 1,
        "rebalance_request_count": 0,
    }
    payload.update(overrides)
    return payload


def snapshot(signature="sig", **overrides):
    payload = {
        "signature": signature,
        "slot": 10,
        "block_time": 20,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 90000,
        "succeeded": True,
        "add_requests": [],
        "rebalance_requests": [],
        "events": [],
    }
    payload.update(overrides)
    return payload


def test_receipt_audit_reports_missing_snapshot(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    ingest_execution_receipt(storage, receipt())

    report = audit_execution_receipts(storage)

    assert report.clean is False
    assert report.receipts == 1
    assert report.missing_chain_snapshots == ("sig",)
    assert report.reconciled_chain_snapshots == 0


def test_receipt_audit_is_clean_after_matching_snapshot(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    ingest_execution_receipt(storage, receipt())
    storage.save_chain_transaction_events(snapshot())

    report = audit_execution_receipts(storage)

    assert report.clean is True
    assert report.reconciled_chain_snapshots == 1
    assert report.missing_chain_snapshots == ()
    assert report.mismatched_chain_snapshots == ()


def test_receipt_audit_detects_snapshot_added_with_different_outcome(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    ingest_execution_receipt(storage, receipt())
    storage.save_chain_transaction_events(
        snapshot(
            network_fee_lamports=6000,
        )
    )

    report = audit_execution_receipts(storage)

    assert report.clean is False
    assert report.mismatched_chain_snapshots == ("sig",)


def test_receipt_audit_counts_terminal_outcomes(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    ingest_execution_receipt(storage, receipt("ok"))
    ingest_execution_receipt(
        storage,
        receipt(
            "failed",
            intent_status="FAILED",
            succeeded=False,
        ),
    )

    report = audit_execution_receipts(storage)

    assert report.confirmed_receipts == 1
    assert report.failed_receipts == 1
