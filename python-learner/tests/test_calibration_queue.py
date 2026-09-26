from meteora_learner.calibration_queue import (
    _inspect_command,
    _verify_command,
    build_calibration_work_queue,
)
from meteora_learner.storage import Storage


def add_row(signature="sig"):
    return {
        "observed_at": "2026-09-23T00:00:00+00:00",
        "position_address": "position",
        "signature": signature,
        "ix_index": 2,
        "event_type": "add",
        "block_time": 1700000000,
        "slot": 120,
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


def test_queue_requests_missing_transaction_decode(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_position_events([add_row()])

    queue = build_calibration_work_queue(str(storage.path))

    assert queue.positions_seen == 1
    assert queue.inspect_transaction_tasks == 1
    item = queue.items[0]
    assert item.task_type == "INSPECT_TRANSACTION"
    assert "inspect-transaction-events-env" in str(item.shell_command)
    assert "$RPC_URL" not in str(item.shell_command)
    assert "$SOLANA_RPC_URL" not in str(item.shell_command)


def test_queue_marks_missing_historical_prestate_as_future_sample(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_position_events([add_row()])
    storage.save_chain_transaction_events(
        {
            "signature": "sig",
            "slot": 120,
            "block_time": 1700000000,
            "succeeded": True,
            "add_requests": [
                {
                    "instruction_index": 2,
                    "instruction_type": "add_liquidity_by_strategy",
                    "requested_amount_x": "1",
                    "requested_amount_y": "2",
                    "observed_active_id": 5,
                    "max_active_bin_slippage": 1,
                    "min_bin_id": 4,
                    "max_bin_id": 6,
                    "strategy_variant": 6,
                    "strategy_favor_x": False,
                    "explicit_distribution": [],
                    "weighted_distribution": [],
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
                            "amount_x": "1",
                            "amount_y": "2",
                            "active_bin_id": 5,
                        },
                    },
                }
            ],
        }
    )

    queue = build_calibration_work_queue(str(storage.path))

    assert queue.inspect_transaction_tasks == 0
    assert queue.future_prestate_samples_needed == 1
    item = next(
        item
        for item in queue.items
        if item.task_type == "NEED_FUTURE_PRESTATE_SAMPLE"
    )
    assert item.shell_command is None
    assert "cannot be reconstructed safely" in item.reason



def test_phase2_work_queue_commands_keep_rpc_url_out_of_argv():
    inspect = _inspect_command("sig")
    verify = _verify_command(
        signature="sig",
        capture_slot_start=100,
        capture_slot_end=101,
        addresses=("pool", "array"),
        snapshot_observed_at="2026-09-23T00:00:00+00:00",
        pool_address="pool",
    )

    assert "inspect-transaction-events-env sig" in inspect
    assert "verify-prestate-env sig 100 101 pool array" in verify
    for command in (inspect, verify):
        assert '"$RPC_URL"' not in command
        assert '"$SOLANA_RPC_URL"' not in command
