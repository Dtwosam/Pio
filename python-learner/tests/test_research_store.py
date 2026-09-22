from meteora_learner.research_store import ResearchStore
from meteora_learner.storage import Storage


def test_research_store_reads_latest_snapshot_and_ordered_candles(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)

    storage.save_pool_snapshot(
        {"address": "pool", "current_price": 10, "bin_step": 25, "active_id": 100},
        observed_at="2026-09-22T00:00:00+00:00",
    )
    storage.save_pool_snapshot(
        {"address": "pool", "current_price": 11, "bin_step": 25, "active_id": 110},
        observed_at="2026-09-22T01:00:00+00:00",
    )

    storage.save_ohlcv_candles(
        [
            {
                "pool_address": "pool",
                "source": "meteora",
                "candle_time": "2026-09-22T00:10:00+00:00",
                "resolution": "5m",
                "open": 10.1,
                "high": 10.2,
                "low": 10.0,
                "close": 10.1,
                "volume": 1,
                "observed_at": "2026-09-22T01:00:00+00:00",
                "raw": {},
            },
            {
                "pool_address": "pool",
                "source": "meteora",
                "candle_time": "2026-09-22T00:05:00+00:00",
                "resolution": "5m",
                "open": 10,
                "high": 10.1,
                "low": 9.9,
                "close": 10,
                "volume": 1,
                "observed_at": "2026-09-22T01:00:00+00:00",
                "raw": {},
            },
        ]
    )

    store = ResearchStore(db)
    latest = store.latest_pool_snapshot("pool")
    candles = store.load_ohlcv("pool", limit=10)

    assert latest is not None
    assert latest["current_price"] == 11.0
    assert latest["active_bin_id"] == 110
    assert [row["close"] for row in candles] == [10.0, 10.1]


def test_research_store_reads_chain_and_position_snapshots(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    observed = "2026-09-22T02:00:00+00:00"

    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": 100,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 1,
                    "lower_bin_id": 70,
                    "upper_bin_id": 139,
                    "bins": [
                        {
                            "bin_id": 100,
                            "amount_x": "1",
                            "amount_y": "2",
                            "liquidity_supply": "3",
                            "fee_amount_x_per_token_stored": "4",
                            "fee_amount_y_per_token_stored": "5",
                        }
                    ],
                }
            ],
        },
        observed_at=observed,
    )
    storage.save_chain_position_snapshot(
        {
            "position_address": "position",
            "pool_address": "pool",
            "owner": "owner",
            "fee_owner": "owner",
            "lower_bin_id": 100,
            "upper_bin_id": 100,
            "total_x_amount": "1",
            "total_y_amount": "2",
            "fee_x": "3",
            "fee_y": "4",
            "reward_one": "0",
            "reward_two": "0",
            "last_updated_at": 1,
            "total_claimed_fee_x_amount": "0",
            "total_claimed_fee_y_amount": "0",
            "bins": [
                {
                    "bin_id": 100,
                    "price": "10",
                    "bin_x_amount": "10",
                    "bin_y_amount": "20",
                    "bin_liquidity": "30",
                    "position_liquidity": "3",
                    "position_x_amount": "1",
                    "position_y_amount": "2",
                    "position_fee_x_amount": "3",
                    "position_fee_y_amount": "4",
                    "position_reward_amounts": ["0", "0"],
                }
            ],
        },
        observed_at=observed,
    )

    store = ResearchStore(db)
    chain = store.latest_chain_pool_snapshot("pool")
    bins = store.load_bin_liquidity("pool")
    position = store.latest_position_snapshot("position")
    position_bins = store.load_position_bins("position")

    assert chain is not None and chain["active_bin_id"] == 100
    assert bins[0]["liquidity_supply"] == "3"
    assert position is not None and position["fee_x"] == "3"
    assert position_bins[0]["position_liquidity"] == "3"

