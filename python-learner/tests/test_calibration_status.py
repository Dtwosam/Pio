from meteora_learner.calibration_status import (
    build_phase2_calibration_evidence,
)
from meteora_learner.storage import Storage


def test_empty_calibration_evidence_is_fail_closed(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = build_phase2_calibration_evidence(str(storage.path))

    assert report.add_positions == 0
    assert report.composition_eligible_samples == 0
    assert report.rebalance_guard_samples == 0
    assert report.transaction_fee_samples == 0
    assert "no exact composition-fee reconciliation samples" in report.evidence_gaps
    assert "no decoded rebalance execution guard samples" in report.evidence_gaps


def test_calibration_evidence_counts_real_receipt_without_calling_it_complete(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_position_events(
        [
            {
                "observed_at": "2026-09-23T00:00:00+00:00",
                "position_address": "position",
                "signature": "sig",
                "ix_index": 1,
                "event_type": "add",
                "block_time": 1700000000,
                "slot": 100,
                "pool_address": "pool",
                "user_address": "user",
                "token_x": "x",
                "token_y": "y",
                "amount_x": "1",
                "amount_y": "2",
                "amount_x_usd": "1",
                "amount_y_usd": "2",
                "total_usd": "3",
                "created_at": "2026-09-23T00:00:00Z",
                "raw": {},
            }
        ]
    )
    storage.save_chain_transaction_events(
        {
            "signature": "sig",
            "slot": 100,
            "block_time": 1700000000,
            "network_fee_lamports": 5000,
            "compute_units_consumed": 100000,
            "succeeded": True,
            "events": [],
        }
    )

    report = build_phase2_calibration_evidence(str(storage.path))

    assert report.add_positions == 1
    assert report.transaction_receipt_samples == 1
    assert report.transaction_fee_samples == 1
    assert report.composition_eligible_samples == 0
    assert "no exact composition-fee reconciliation samples" in report.evidence_gaps
