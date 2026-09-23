from meteora_learner.composition_labels import build_composition_fee_labels
from meteora_learner.storage import Storage


def test_composition_labels_join_api_add_to_chain_events(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_position_events(
        [
            {
                "observed_at": "2026-09-23T00:00:00+00:00",
                "position_address": "position",
                "signature": "sig",
                "ix_index": 4,
                "event_type": "add",
                "block_time": 1700000000,
                "slot": 123,
                "pool_address": "pool",
                "user_address": "user",
                "token_x": "x",
                "token_y": "y",
                "amount_x": "10",
                "amount_y": "20",
                "amount_x_usd": "1",
                "amount_y_usd": "2",
                "total_usd": "3",
                "created_at": "2026-09-22T00:00:00Z",
                "raw": {},
            }
        ]
    )
    storage.save_chain_transaction_events(
        {
            "signature": "sig",
            "slot": 123,
            "block_time": 1700000000,
            "events": [
                {
                    "event_index": 0,
                    "parent_ix_index": 4,
                    "event": {
                        "event_type": "AddLiquidity",
                        "event": {
                            "lb_pair": "pool",
                            "from": "user",
                            "position": "position",
                            "amount_x": "10",
                            "amount_y": "20",
                            "active_bin_id": 5,
                        },
                    },
                },
                {
                    "event_index": 1,
                    "parent_ix_index": 4,
                    "event": {
                        "event_type": "CompositionFee",
                        "event": {
                            "from": "user",
                            "bin_id": 5,
                            "token_x_fee_amount": "3",
                            "token_y_fee_amount": "4",
                            "protocol_token_x_fee_amount": "1",
                            "protocol_token_y_fee_amount": "2",
                        },
                    },
                },
            ],
        }
    )

    report = build_composition_fee_labels(
        str(storage.path),
        position_address="position",
    )

    assert report.add_events == 1
    assert report.chain_add_events_found == 1
    assert report.composition_labeled_adds == 1
    assert report.exact_amount_string_matches == 1
    label = report.labels[0]
    assert label.active_bin_id == 5
    assert label.composition_fee_x == 3
    assert label.composition_fee_y == 4
    assert label.protocol_fee_x == 1
    assert label.protocol_fee_y == 2


def test_composition_label_keeps_missing_chain_decode_visible(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_position_events(
        [
            {
                "observed_at": "2026-09-23T00:00:00+00:00",
                "position_address": "position",
                "signature": "missing",
                "ix_index": 1,
                "event_type": "add",
                "block_time": 1,
                "slot": 2,
                "pool_address": "pool",
                "user_address": "user",
                "token_x": "x",
                "token_y": "y",
                "amount_x": "1.5",
                "amount_y": "2.5",
                "amount_x_usd": "1",
                "amount_y_usd": "2",
                "total_usd": "3",
                "created_at": "2026-09-22T00:00:00Z",
                "raw": {},
            }
        ]
    )

    report = build_composition_fee_labels(
        str(storage.path),
        position_address="position",
    )

    assert report.chain_add_events_found == 0
    assert report.composition_labeled_adds == 0
    assert report.labels[0].exact_amount_string_match is None
