from meteora_learner.position_normalization import normalize_position_pnl


def test_position_pnl_normalization():
    payload = {
        "positions": [
            {
                "positionAddress": "position",
                "lowerBinId": 10,
                "upperBinId": 20,
                "minPrice": "1.0",
                "maxPrice": "2.0",
                "feePerTvl24h": "0.25",
                "isClosed": True,
                "pnlUsd": "12.5",
                "pnlPctChange": "5.0",
                "allTimeDeposits": {"total": {"usd": "100"}},
                "allTimeWithdrawals": {"total": {"usd": "108"}},
                "allTimeFees": {"total": {"usd": "4.5"}},
            }
        ]
    }

    rows = normalize_position_pnl(payload)
    assert rows == [
        {
            "position_address": "position",
            "lower_bin_id": 10,
            "upper_bin_id": 20,
            "min_price": 1.0,
            "max_price": 2.0,
            "created_at": None,
            "closed_at": None,
            "is_closed": True,
            "is_out_of_range": None,
            "pnl_usd": 12.5,
            "pnl_pct_change": 5.0,
            "fee_per_tvl_24h": 0.25,
            "deposits_usd": 100.0,
            "withdrawals_usd": 108.0,
            "fees_usd": 4.5,
            "pool_active_bin_id": None,
            "pool_active_price": None,
        }
    ]
