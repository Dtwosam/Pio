from meteora_learner.quality import assess_candles


def test_candle_quality_detects_stale_data_and_bad_ohlc():
    rows = [
        {
            "candle_time": "2026-09-22T00:00:00+00:00",
            "open": 10.0,
            "high": 9.0,
            "low": 8.0,
            "close": 10.0,
        },
        {
            "candle_time": "2026-09-22T00:05:00+00:00",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
        },
        {
            "candle_time": "2026-09-22T00:10:00+00:00",
            "open": 10.5,
            "high": 11.0,
            "low": 10.0,
            "close": 10.8,
        },
    ]
    checks = assess_candles(
        rows,
        checked_at="2026-09-22T02:00:00+00:00",
        max_age_seconds=900,
    )
    status = {item["check_name"]: item["status"] for item in checks}
    assert status["freshness"] == "FAIL"
    assert status["ohlc_consistency"] == "FAIL"
