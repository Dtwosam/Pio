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
    assert report.composition_ineligible_samples == 1
    assert report.composition_ineligibility_reasons
    assert report.composition_ineligibility_reasons[0].reason == (
        "AddLiquidity event decode missing"
    )
    assert report.composition_ineligibility_reasons[0].count == 1
    assert report.add_execution_unmatched_samples == 1
    assert report.add_execution_gap_reasons[0].reason == (
        "add-liquidity request decode missing"
    )
    assert report.add_execution_gap_reasons[0].count == 1
    assert "no exact composition-fee reconciliation samples" in report.evidence_gaps



def test_calibration_gap_reasons_are_counted_and_sorted(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    rows = []
    for idx, signature in enumerate(("sig-a", "sig-b", "sig-c"), start=1):
        rows.append(
            {
                "observed_at": f"2026-09-23T00:0{idx}:00+00:00",
                "position_address": "position",
                "signature": signature,
                "ix_index": idx,
                "event_type": "add",
                "block_time": 1700000000 + idx,
                "slot": 100 + idx,
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
        )
    storage.save_position_events(rows)

    for idx, signature in enumerate(("sig-a", "sig-b"), start=1):
        events = []
        if signature == "sig-a":
            events = [
                {
                    "event_index": 0,
                    "parent_ix_index": idx,
                    "event": {
                        "event_type": "AddLiquidity",
                        "event": {
                            "lb_pair": "pool",
                            "from": "user",
                            "position": "position",
                            "amount_x": "1",
                            "amount_y": "2",
                            "active_bin_id": 10,
                        },
                    },
                }
            ]
        storage.save_chain_transaction_events(
            {
                "signature": signature,
                "slot": 100 + idx,
                "block_time": 1700000000 + idx,
                "succeeded": True,
                "add_requests": [
                    {
                        "instruction_index": idx,
                        "instruction_type": "unsupported_add",
                        "requested_amount_x": "1",
                        "requested_amount_y": "2",
                    }
                ],
                "events": events,
            }
        )

    report = build_phase2_calibration_evidence(str(storage.path))

    reasons = {
        item.reason: item.count
        for item in report.composition_ineligibility_reasons
    }
    assert reasons[
        "unsupported exact allocation instruction: unsupported_add"
    ] == 1
    assert reasons["AddLiquidity event decode missing"] == 1
    assert reasons["decoded Solana transaction snapshot missing"] == 1
    assert report.add_execution_events == 3
    assert report.add_execution_matched_events == 1
    assert report.add_execution_unmatched_samples == 2
    add_reasons = {
        item.reason: item.count
        for item in report.add_execution_gap_reasons
    }
    assert add_reasons["AddLiquidity event decode missing"] == 1
    assert add_reasons["add-liquidity request decode missing"] == 1
