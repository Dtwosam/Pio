from meteora_learner.rebalance_execution import (
    build_rebalance_execution_calibration,
)
from meteora_learner.storage import Storage


def snapshot(*, added_x="80", added_y="170"):
    return {
        "signature": "sig",
        "slot": 120,
        "block_time": 1700000000,
        "network_fee_lamports": 7000,
        "compute_units_consumed": 220000,
        "succeeded": True,
        "rebalance_requests": [
            {
                "instruction_index": 3,
                "observed_active_id": 10,
                "max_active_bin_slippage": 2,
                "should_claim_fee": True,
                "should_claim_reward": False,
                "min_withdraw_x_amount": "100",
                "max_deposit_x_amount": "90",
                "min_withdraw_y_amount": "200",
                "max_deposit_y_amount": "180",
                "shrink_mode": 0,
            }
        ],
        "events": [
            {
                "event_index": 0,
                "parent_ix_index": 3,
                "event": {
                    "event_type": "Rebalancing",
                    "event": {
                        "lb_pair": "pool",
                        "position": "position",
                        "owner": "owner",
                        "active_bin_id": 11,
                        "x_withdrawn_amount": "110",
                        "x_added_amount": added_x,
                        "y_withdrawn_amount": "210",
                        "y_added_amount": added_y,
                        "x_fee_amount": "2",
                        "y_fee_amount": "3",
                        "old_min_id": 1,
                        "old_max_id": 5,
                        "new_min_id": 9,
                        "new_max_id": 13,
                        "reward_one": "0",
                        "reward_two": "0",
                    },
                },
            }
        ],
    }


def test_rebalance_execution_guards_match_real_event(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(snapshot())

    report = build_rebalance_execution_calibration(
        str(storage.path),
        position_address="position",
    )

    assert report.rebalance_events == 1
    assert report.request_decodes == 1
    assert report.guard_passes == 1
    assert report.guard_violations == 0
    sample = report.samples[0]
    assert sample.active_bin_drift == 1
    assert sample.withdraw_x_headroom == 10
    assert sample.deposit_x_headroom == 10
    assert sample.withdraw_y_headroom == 10
    assert sample.deposit_y_headroom == 10
    assert sample.network_fee_lamports == 7000
    assert sample.compute_units_consumed == 220000
    assert sample.all_guards_passed is True


def test_rebalance_execution_violation_stays_visible(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(snapshot(added_x="91"))

    report = build_rebalance_execution_calibration(
        str(storage.path),
        position_address="position",
    )

    assert report.guard_passes == 0
    assert report.guard_violations == 1
    assert report.samples[0].deposit_x_guard_passed is False
    assert report.samples[0].deposit_x_headroom == -1


def test_rebalance_execution_missing_request_counts_against_coverage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    payload = snapshot()
    payload["rebalance_requests"] = []
    storage.save_chain_transaction_events(payload)

    report = build_rebalance_execution_calibration(
        str(storage.path),
        position_address="position",
    )

    assert report.rebalance_events == 1
    assert report.request_decodes == 0
    assert report.request_coverage_rate == 0.0
    assert report.samples[0].all_guards_passed is None
