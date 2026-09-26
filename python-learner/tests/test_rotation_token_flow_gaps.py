from meteora_learner.rotation_token_flow_gaps import (
    build_rotation_token_flow_gap_report,
)
from meteora_learner.storage import Storage


def rebalance_event(position="position", pool="pool"):
    return {
        "event_index": 0,
        "parent_ix_index": 1,
        "event": {
            "event_type": "Rebalancing",
            "event": {
                "lb_pair": pool,
                "position": position,
                "owner": "owner",
                "active_bin_id": 10,
                "x_withdrawn_amount": "0",
                "x_added_amount": "0",
                "y_withdrawn_amount": "0",
                "y_added_amount": "0",
                "x_fee_amount": "0",
                "y_fee_amount": "0",
                "old_min_id": 1,
                "old_max_id": 2,
                "new_min_id": 1,
                "new_max_id": 2,
                "reward_one": "0",
                "reward_two": "0",
            },
        },
    }


def save_receipt(
    storage,
    signature,
    *,
    pool="pool",
    position="position",
    capture=None,
):
    payload = {
        "signature": signature,
        "slot": 100,
        "block_time": 1_700_000_000,
        "succeeded": True,
        "events": [rebalance_event(position=position, pool=pool)],
    }
    if capture is not None:
        payload["token_balance_deltas"] = capture
    storage.save_chain_transaction_events(payload)


def save_event_without_receipt(
    storage,
    signature,
    *,
    pool="pool",
    position="position",
):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_transaction_events(
                observed_at, signature, event_index, parent_ix_index,
                slot, block_time, event_type, lb_pair,
                position_address, raw_json
            ) VALUES (
                '2026-09-26T18:00:00+00:00',
                ?, 0, 1, 101, 1700000001,
                'Rebalancing', ?, ?, '{}'
            )
            """,
            (signature, pool, position),
        )


def test_token_flow_gap_report_distinguishes_missing_from_captured_empty(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_receipt(storage, "captured-empty", capture=[])
    save_receipt(storage, "legacy-missing")
    save_event_without_receipt(storage, "receipt-missing")

    report = build_rotation_token_flow_gap_report(
        storage,
        pool_address="pool",
    )

    assert report.rebalance_transactions_seen == 3
    assert report.receipt_present_transactions == 2
    assert report.receipt_missing_transactions == 1
    assert report.token_flow_captured_transactions == 1
    assert report.token_flow_missing_transactions == 2
    assert report.token_delta_rows == 0
    assert [item.signature for item in report.gaps] == [
        "legacy-missing",
        "receipt-missing",
    ]
    assert [item.reason for item in report.gaps] == [
        "TOKEN_FLOW_CAPTURE_MISSING",
        "TRANSACTION_RECEIPT_MISSING",
    ]
    assert report.read_only is True


def test_token_flow_gap_report_counts_delta_rows_without_creating_gap(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_receipt(
        storage,
        "captured",
        capture=[
            {
                "account_index": 1,
                "account_address": "acct",
                "mint": "mint",
                "pre_owner": "owner",
                "post_owner": "owner",
                "pre_amount": "1",
                "post_amount": "2",
                "delta_amount": "1",
                "decimals": 6,
            }
        ],
    )

    report = build_rotation_token_flow_gap_report(storage)

    assert report.rebalance_transactions_seen == 1
    assert report.token_flow_captured_transactions == 1
    assert report.token_flow_missing_transactions == 0
    assert report.token_delta_rows == 1
    assert report.gaps == ()


def test_token_flow_gap_report_filters_pool(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_receipt(storage, "pool-a-gap", pool="pool-a")
    save_receipt(storage, "pool-b-gap", pool="pool-b")

    report = build_rotation_token_flow_gap_report(
        storage,
        pool_address="pool-a",
    )

    assert report.rebalance_transactions_seen == 1
    assert len(report.gaps) == 1
    assert report.gaps[0].signature == "pool-a-gap"
    assert report.gaps[0].pool_addresses == ("pool-a",)


def test_token_flow_gap_report_collapses_multiple_rebalance_events_per_signature(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    payload = {
        "signature": "multi",
        "slot": 100,
        "succeeded": True,
        "events": [
            rebalance_event(position="position-a"),
            {
                **rebalance_event(position="position-b"),
                "event_index": 1,
                "parent_ix_index": 2,
            },
        ],
    }
    storage.save_chain_transaction_events(payload)

    report = build_rotation_token_flow_gap_report(storage)

    assert report.rebalance_transactions_seen == 1
    assert report.token_flow_missing_transactions == 1
    assert len(report.gaps) == 1
    assert report.gaps[0].position_addresses == (
        "position-a",
        "position-b",
    )


def test_token_flow_gap_report_empty_database_is_valid(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = build_rotation_token_flow_gap_report(storage)

    assert report.rebalance_transactions_seen == 0
    assert report.gaps == ()
