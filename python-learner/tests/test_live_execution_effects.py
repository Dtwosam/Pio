import pytest

from meteora_learner.execution_receipt_ingest import ingest_execution_receipt
from meteora_learner.live_execution_effects import (
    apply_live_execution_effect,
    derive_live_execution_effect,
)
from meteora_learner.storage import Storage


def receipt(action="ENTER", event_count=1, **overrides):
    payload = {
        "decision_id": "decision-1",
        "signature": "signature-1",
        "mode": "LIVE",
        "action": action,
        "pool_address": "pool",
        "intent_status": "CONFIRMED",
        "slot": 123,
        "block_time": 456,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 100000,
        "succeeded": True,
        "event_count": event_count,
        "add_request_count": 0,
        "rebalance_request_count": 0,
    }
    payload.update(overrides)
    return payload


def event(event_type, body, index=0):
    return {
        "event_index": index,
        "parent_ix_index": 0,
        "event": {
            "event_type": event_type,
            "event": body,
        },
    }


def snapshot(events, succeeded=True):
    return {
        "signature": "signature-1",
        "slot": 123,
        "block_time": 456,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 100000,
        "succeeded": succeeded,
        "add_requests": [],
        "rebalance_requests": [],
        "events": events,
    }


def add_event(amount_x="100", amount_y="200"):
    return event(
        "AddLiquidity",
        {
            "lb_pair": "pool",
            "from": "wallet",
            "position": "position",
            "active_bin_id": 1,
            "amount_x": amount_x,
            "amount_y": amount_y,
        },
    )


def composition_event(index=1):
    return event(
        "CompositionFee",
        {
            "from": "wallet",
            "bin_id": 1,
            "token_x_fee_amount": "3",
            "token_y_fee_amount": "4",
            "protocol_token_x_fee_amount": "1",
            "protocol_token_y_fee_amount": "1",
        },
        index=index,
    )


def test_confirmed_entry_derives_atomic_wallet_effects(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(
        snapshot([add_event(), composition_event()])
    )
    ingest_execution_receipt(
        storage,
        receipt(event_count=2),
    )

    result = apply_live_execution_effect(storage, "decision-1")

    assert result.reused_existing is False
    effect = result.effect
    assert effect.position_address == "position"
    assert effect.token_x_wallet_delta_atomic == -100
    assert effect.token_y_wallet_delta_atomic == -200
    assert effect.composition_fee_x_atomic == 3
    assert effect.composition_fee_y_atomic == 4
    assert effect.network_fee_lamports == 5000


def test_rebalance_derives_withdraw_add_fee_and_reward_effects(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    rebalance = event(
        "Rebalancing",
        {
            "lb_pair": "pool",
            "position": "position",
            "owner": "wallet",
            "active_bin_id": 2,
            "x_withdrawn_amount": "80",
            "x_added_amount": "50",
            "y_withdrawn_amount": "40",
            "y_added_amount": "60",
            "x_fee_amount": "7",
            "y_fee_amount": "8",
            "old_min_id": -5,
            "old_max_id": 5,
            "new_min_id": -4,
            "new_max_id": 6,
            "reward_one": "9",
            "reward_two": "10",
        },
    )
    storage.save_chain_transaction_events(snapshot([rebalance]))
    ingest_execution_receipt(
        storage,
        receipt(action="REBALANCE"),
    )

    effect = derive_live_execution_effect(storage, "decision-1")

    assert effect.token_x_wallet_delta_atomic == 30
    assert effect.token_y_wallet_delta_atomic == -20
    assert effect.earned_fee_x_atomic == 7
    assert effect.earned_fee_y_atomic == 8
    assert effect.reward_one_atomic == 9
    assert effect.reward_two_atomic == 10


def test_confirmed_exit_derives_returned_inventory(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    removed = event(
        "RemoveLiquidity",
        {
            "lb_pair": "pool",
            "from": "wallet",
            "position": "position",
            "active_bin_id": 1,
            "amount_x": "25",
            "amount_y": "75",
        },
    )
    storage.save_chain_transaction_events(snapshot([removed]))
    ingest_execution_receipt(
        storage,
        receipt(action="EXIT"),
    )

    effect = derive_live_execution_effect(storage, "decision-1")

    assert effect.token_x_wallet_delta_atomic == 25
    assert effect.token_y_wallet_delta_atomic == 75


def test_failed_execution_records_network_cost_only(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(snapshot([], succeeded=False))
    ingest_execution_receipt(
        storage,
        receipt(
            event_count=0,
            intent_status="FAILED",
            succeeded=False,
        ),
    )

    result = apply_live_execution_effect(storage, "decision-1")

    effect = result.effect
    assert effect.position_address is None
    assert effect.token_x_wallet_delta_atomic == 0
    assert effect.token_y_wallet_delta_atomic == 0
    assert effect.network_fee_lamports == 5000


def test_effect_requires_receipt_event_count_to_match_chain(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(snapshot([add_event()]))
    ingest_execution_receipt(
        storage,
        receipt(event_count=2),
    )

    with pytest.raises(ValueError, match="event count"):
        derive_live_execution_effect(storage, "decision-1")


def test_live_effect_apply_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_transaction_events(snapshot([add_event()]))
    ingest_execution_receipt(storage, receipt())

    first = apply_live_execution_effect(storage, "decision-1")
    second = apply_live_execution_effect(storage, "decision-1")

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.effect == second.effect
    with storage.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM live_execution_effects"
        ).fetchone()[0] == 1
