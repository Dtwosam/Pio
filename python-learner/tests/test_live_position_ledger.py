import pytest

from meteora_learner.execution_receipt_ingest import ingest_execution_receipt
from meteora_learner.live_execution_effects import apply_live_execution_effect
from meteora_learner.live_position_ledger import apply_live_position_effect
from meteora_learner.storage import Storage


def wrapper(event_type, body):
    return {
        "event_index": 0,
        "parent_ix_index": 0,
        "event": {
            "event_type": event_type,
            "event": body,
        },
    }


def receipt(
    decision,
    signature,
    action,
    *,
    add_count=0,
    rebalance_count=0,
):
    return {
        "decision_id": decision,
        "signature": signature,
        "mode": "LIVE",
        "action": action,
        "pool_address": "pool",
        "intent_status": "CONFIRMED",
        "slot": {
            "enter": 10,
            "rebalance": 20,
            "exit": 30,
        }[signature],
        "block_time": 100,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 90000,
        "succeeded": True,
        "event_count": 1,
        "add_request_count": add_count,
        "rebalance_request_count": rebalance_count,
    }


def save_enter(storage):
    signature = "enter"
    storage.save_chain_transaction_events(
        {
            "signature": signature,
            "slot": 10,
            "block_time": 100,
            "network_fee_lamports": 5000,
            "compute_units_consumed": 90000,
            "succeeded": True,
            "add_requests": [
                {
                    "instruction_index": 0,
                    "instruction_type": "AddLiquidityByStrategy",
                    "requested_amount_x": "100",
                    "requested_amount_y": "200",
                    "observed_active_id": 0,
                    "max_active_bin_slippage": 3,
                    "min_bin_id": -5,
                    "max_bin_id": 5,
                    "strategy_variant": 6,
                    "strategy_favor_x": False,
                }
            ],
            "rebalance_requests": [],
            "events": [
                wrapper(
                    "AddLiquidity",
                    {
                        "lb_pair": "pool",
                        "from": "wallet",
                        "position": "position",
                        "active_bin_id": 0,
                        "amount_x": "100",
                        "amount_y": "200",
                    },
                )
            ],
        }
    )
    ingest_execution_receipt(
        storage,
        receipt(
            "decision-enter",
            signature,
            "ENTER",
            add_count=1,
        ),
    )
    apply_live_execution_effect(storage, "decision-enter")


def save_rebalance(storage):
    signature = "rebalance"
    storage.save_chain_transaction_events(
        {
            "signature": signature,
            "slot": 20,
            "block_time": 110,
            "network_fee_lamports": 5000,
            "compute_units_consumed": 90000,
            "succeeded": True,
            "add_requests": [],
            "rebalance_requests": [
                {
                    "instruction_index": 0,
                    "observed_active_id": 2,
                    "max_active_bin_slippage": 3,
                    "should_claim_fee": False,
                    "should_claim_reward": False,
                    "min_withdraw_x_amount": "0",
                    "max_deposit_x_amount": "1000",
                    "min_withdraw_y_amount": "0",
                    "max_deposit_y_amount": "1000",
                    "shrink_mode": 0,
                }
            ],
            "events": [
                wrapper(
                    "Rebalancing",
                    {
                        "lb_pair": "pool",
                        "position": "position",
                        "owner": "wallet",
                        "active_bin_id": 2,
                        "x_withdrawn_amount": "80",
                        "x_added_amount": "70",
                        "y_withdrawn_amount": "60",
                        "y_added_amount": "50",
                        "x_fee_amount": "1",
                        "y_fee_amount": "2",
                        "old_min_id": -5,
                        "old_max_id": 5,
                        "new_min_id": -3,
                        "new_max_id": 7,
                        "reward_one": "0",
                        "reward_two": "0",
                    },
                )
            ],
        }
    )
    ingest_execution_receipt(
        storage,
        receipt(
            "decision-rebalance",
            signature,
            "REBALANCE",
            rebalance_count=1,
        ),
    )
    apply_live_execution_effect(storage, "decision-rebalance")


def save_exit(storage):
    signature = "exit"
    storage.save_chain_transaction_events(
        {
            "signature": signature,
            "slot": 30,
            "block_time": 120,
            "network_fee_lamports": 5000,
            "compute_units_consumed": 90000,
            "succeeded": True,
            "add_requests": [],
            "rebalance_requests": [],
            "events": [
                wrapper(
                    "RemoveLiquidity",
                    {
                        "lb_pair": "pool",
                        "from": "wallet",
                        "position": "position",
                        "active_bin_id": 2,
                        "amount_x": "120",
                        "amount_y": "240",
                    },
                )
            ],
        }
    )
    ingest_execution_receipt(
        storage,
        receipt("decision-exit", signature, "EXIT"),
    )
    apply_live_execution_effect(storage, "decision-exit")


def test_live_position_lifecycle_is_receipt_driven(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    save_enter(storage)
    entered = apply_live_position_effect(
        storage,
        "decision-enter",
    )
    assert entered.prior_status is None
    assert entered.next_status == "OPEN"
    assert (entered.min_bin_id, entered.max_bin_id) == (-5, 5)

    save_rebalance(storage)
    rebalanced = apply_live_position_effect(
        storage,
        "decision-rebalance",
    )
    assert rebalanced.prior_status == "OPEN"
    assert rebalanced.next_status == "OPEN"
    assert (rebalanced.min_bin_id, rebalanced.max_bin_id) == (-3, 7)

    save_exit(storage)
    exited = apply_live_position_effect(
        storage,
        "decision-exit",
    )
    assert exited.prior_status == "OPEN"
    assert exited.next_status == "LIQUIDITY_REMOVED"

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT status, min_bin_id, max_bin_id, rebalances
            FROM live_positions
            WHERE position_address = 'position'
            """
        ).fetchone()
        events = conn.execute(
            "SELECT COUNT(*) FROM live_position_events"
        ).fetchone()[0]

    assert row == ("LIQUIDITY_REMOVED", -3, 7, 1)
    assert events == 3


def test_live_position_mutation_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_enter(storage)

    first = apply_live_position_effect(storage, "decision-enter")
    second = apply_live_position_effect(storage, "decision-enter")

    assert first.reused_existing is False
    assert second.reused_existing is True
    with storage.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM live_position_events"
        ).fetchone()[0] == 1


def test_rebalance_cannot_create_position_without_enter(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_rebalance(storage)

    with pytest.raises(ValueError, match="existing OPEN"):
        apply_live_position_effect(
            storage,
            "decision-rebalance",
        )
