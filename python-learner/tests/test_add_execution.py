from meteora_learner.add_execution import build_add_execution_calibration
from meteora_learner.storage import Storage


def add_history(signature="sig", ix_index=2):
    return {
        "observed_at": "2026-09-23T00:00:00+00:00",
        "position_address": "position",
        "signature": signature,
        "ix_index": ix_index,
        "event_type": "add",
        "block_time": 1700000000,
        "slot": 123,
        "pool_address": "pool",
        "user_address": "user",
        "token_x": "x",
        "token_y": "y",
        "amount_x": "90",
        "amount_y": "180",
        "amount_x_usd": "1",
        "amount_y_usd": "2",
        "total_usd": "3",
        "created_at": "2026-09-22T00:00:00Z",
        "raw": {},
    }


def tx_snapshot():
    return {
        "signature": "sig",
        "slot": 123,
        "block_time": 1700000000,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 100000,
        "succeeded": True,
        "add_requests": [
            {
                "instruction_index": 2,
                "instruction_type": "add_liquidity_by_strategy",
                "requested_amount_x": "100",
                "requested_amount_y": "200",
                "observed_active_id": 10,
                "max_active_bin_slippage": 2,
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
                        "amount_x": "90",
                        "amount_y": "180",
                        "active_bin_id": 11,
                    },
                },
            }
        ],
    }


def test_add_execution_calibration_matches_request_to_actual_event(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_position_events([add_history()])
    storage.save_chain_transaction_events(tx_snapshot())

    report = build_add_execution_calibration(
        str(storage.path),
        position_address="position",
    )

    assert report.add_events == 1
    assert report.request_decodes == 1
    assert report.matched_add_events == 1
    assert report.decode_coverage_rate == 1.0
    assert report.event_match_rate == 1.0
    assert report.guard_samples == 1
    assert report.guard_violations == 0
    sample = report.samples[0]
    assert sample.unused_amount_x == 10
    assert sample.unused_amount_y == 20
    assert sample.underfill_bps_x == 1000
    assert sample.underfill_bps_y == 1000
    assert sample.active_bin_drift == 1
    assert sample.active_bin_guard_passed is True


def test_add_execution_calibration_keeps_missing_decode_visible(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_position_events([add_history(signature="missing", ix_index=1)])

    report = build_add_execution_calibration(
        str(storage.path),
        position_address="position",
    )

    assert report.add_events == 1
    assert report.request_decodes == 0
    assert report.matched_add_events == 0
    assert report.decode_coverage_rate == 0.0
    assert report.samples[0].request_decoded is False
