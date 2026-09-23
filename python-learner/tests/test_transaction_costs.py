from meteora_learner.storage import Storage
from meteora_learner.transaction_costs import build_transaction_cost_report


def history_row(signature, event_type, ix_index):
    return {
        "observed_at": "2026-09-23T00:00:00+00:00",
        "position_address": "position",
        "signature": signature,
        "ix_index": ix_index,
        "event_type": event_type,
        "block_time": 1700000000 + ix_index,
        "slot": 100 + ix_index,
        "pool_address": "pool",
        "user_address": "user",
        "token_x": "x",
        "token_y": "y",
        "amount_x": "1",
        "amount_y": "2",
        "amount_x_usd": "1",
        "amount_y_usd": "2",
        "total_usd": "3",
        "created_at": "2026-09-22T00:00:00Z",
        "raw": {},
    }


def tx_snapshot(signature, fee, compute, succeeded=True):
    return {
        "signature": signature,
        "slot": 123,
        "block_time": 1700000000,
        "network_fee_lamports": fee,
        "compute_units_consumed": compute,
        "succeeded": succeeded,
        "events": [],
    }


def test_transaction_cost_report_deduplicates_signatures(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_position_events(
        [
            history_row("sig-a", "add", 1),
            history_row("sig-a", "claim_fee", 2),
            history_row("sig-b", "remove", 3),
        ]
    )
    storage.save_chain_transaction_events(tx_snapshot("sig-a", 5000, 100000))
    storage.save_chain_transaction_events(tx_snapshot("sig-b", 7000, 200000))

    report = build_transaction_cost_report(
        str(storage.path),
        position_address="position",
    )

    assert report.transactions_seen == 2
    assert report.receipts_available == 2
    assert report.fee_samples == 2
    assert report.total_network_fee_lamports == 12000
    assert report.mean_network_fee_lamports == 6000.0
    assert report.median_network_fee_lamports == 6000.0
    assert report.p95_network_fee_lamports == 7000
    assert report.mean_compute_units == 150000.0
    assert report.missing_receipt_signatures == ()
    assert report.samples[0].event_types == ("add", "claim_fee")


def test_transaction_cost_report_keeps_missing_receipts_visible(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_position_events([history_row("missing", "add", 1)])

    report = build_transaction_cost_report(
        str(storage.path),
        position_address="position",
    )

    assert report.transactions_seen == 1
    assert report.receipts_available == 0
    assert report.fee_samples == 0
    assert report.missing_receipt_signatures == ("missing",)
