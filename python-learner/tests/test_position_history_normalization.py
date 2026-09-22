from meteora_learner.position_normalization import normalize_position_history


def event_payload():
    return {
        "events": [
            {
                "signature": "sig",
                "ixIndex": 4,
                "eventType": "add",
                "positionAddress": "position",
                "blockTime": 1700000000,
                "slot": 123,
                "poolAddress": "pool",
                "userAddress": "user",
                "tokenX": "mint-x",
                "tokenY": "mint-y",
                "amountX": "12345678901234567890",
                "amountY": "42",
                "amountXUsd": "10.25",
                "amountYUsd": "2.50",
                "totalUsd": "12.75",
                "createdAt": "2026-09-22T00:00:00Z",
            }
        ]
    }


def test_position_history_normalization_preserves_exact_amount_strings():
    rows = normalize_position_history(
        event_payload(),
        observed_at="2026-09-23T00:00:00+00:00",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["signature"] == "sig"
    assert row["ix_index"] == 4
    assert row["event_type"] == "add"
    assert row["amount_x"] == "12345678901234567890"
    assert row["amount_y"] == "42"
    assert row["total_usd"] == "12.75"


def test_position_history_normalization_skips_incomplete_rows():
    payload = event_payload()
    del payload["events"][0]["signature"]
    assert normalize_position_history(
        payload,
        observed_at="2026-09-23T00:00:00+00:00",
    ) == []
