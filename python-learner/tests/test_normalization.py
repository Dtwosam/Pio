from meteora_learner.normalization import normalize_ohlcv, normalize_timestamp, normalize_volume_history


def test_normalize_timestamp_supports_seconds_and_milliseconds():
    a = normalize_timestamp(1_700_000_000)
    b = normalize_timestamp(1_700_000_000_000)
    assert a == b
    assert a is not None and a.endswith("+00:00")


def test_normalize_ohlcv_supports_nested_data():
    payload = {
        "data": [
            {
                "timestamp": 1_700_000_000,
                "open": "10",
                "high": "12",
                "low": "9",
                "close": "11",
                "volume": "42",
            }
        ]
    }
    rows = normalize_ohlcv("pool", payload, observed_at="2026-09-22T00:00:00+00:00")
    assert len(rows) == 1
    assert rows[0]["close"] == 11.0
    assert rows[0]["pool_address"] == "pool"


def test_normalize_volume_history():
    payload = {"history": [{"date": "2026-09-21T00:00:00Z", "volume_usd": "100", "fees": "2"}]}
    rows = normalize_volume_history("pool", payload, observed_at="2026-09-22T00:00:00+00:00")
    assert rows[0]["volume"] == 100.0
    assert rows[0]["fees"] == 2.0
