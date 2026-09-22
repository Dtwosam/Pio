from meteora_learner.research import inventory_backtest_from_store
from meteora_learner.storage import Storage


def test_inventory_backtest_uses_stored_pool_and_candles(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    observed = "2026-09-22T01:00:00+00:00"

    storage.save_pool_snapshot(
        {"address": "pool", "current_price": 10.2, "bin_step": 100, "active_id": 102},
        observed_at=observed,
    )
    storage.save_ohlcv_candles(
        [
            {
                "pool_address": "pool",
                "source": "meteora",
                "candle_time": f"2026-09-22T00:{minute:02d}:00+00:00",
                "resolution": "5m",
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1,
                "observed_at": observed,
                "raw": {},
            }
            for minute, price in [(0, 10.0), (5, 10.1), (10, 10.2)]
        ]
    )

    results = inventory_backtest_from_store(
        db,
        pool_address="pool",
        capital_quote=100,
        half_widths=(1,),
    )

    assert len(results) == 3
    assert {item["strategy"] for item in results} == {"SPOT", "CURVE", "BID_ASK"}
    assert all(item["fee_model"] == "ZERO" for item in results)
