from meteora_learner.normalization import normalize_ohlcv, normalize_timestamp, normalize_volume_history


def test_normalize_timestamp_supports_seconds_and_milliseconds():
    a = normalize_timestamp(1_700_000_000)
    b = normalize_timestamp(1_700_000_000_000)
    assert a == b
    assert a is not None and a.endswith("+00:00")


def test_normalize_ohlcv_supports_current_meteora_shape():
    payload = {
        "timeframe": "5m",
        "data": [
            {
                "timestamp": 1_700_000_000,
                "timestamp_str": "2023-11-14T22:13:20Z",
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
                "volume": 42,
            }
        ],
    }
    rows = normalize_ohlcv(
        "pool",
        payload,
        observed_at="2026-09-22T00:00:00+00:00",
        resolution="5m",
    )
    assert len(rows) == 1
    assert rows[0]["close"] == 11.0
    assert rows[0]["resolution"] == "5m"
    assert rows[0]["pool_address"] == "pool"


def test_normalize_volume_history_preserves_protocol_fees():
    payload = {
        "timeframe": "5m",
        "data": [
            {
                "timestamp": 1_700_000_000,
                "timestamp_str": "2023-11-14T22:13:20Z",
                "volume": 100,
                "fees": 2,
                "protocol_fees": 0.2,
            }
        ],
    }
    rows = normalize_volume_history("pool", payload, observed_at="2026-09-22T00:00:00+00:00")
    assert rows[0]["volume"] == 100.0
    assert rows[0]["fees"] == 2.0
    assert rows[0]["protocol_fees"] == 0.2
