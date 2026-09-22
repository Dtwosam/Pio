import pandas as pd

from meteora_learner.range_analysis import evaluate_range_path


def test_range_path_stats():
    frame = pd.DataFrame(
        {
            "candle_time": [
                "2026-09-22T00:00:00Z",
                "2026-09-22T00:05:00Z",
                "2026-09-22T00:10:00Z",
            ],
            "high": [101.0, 103.0, 112.0],
            "low": [99.0, 100.0, 108.0],
            "close": [100.0, 102.0, 110.0],
        }
    )

    stats = evaluate_range_path(frame, lower_price=95.0, upper_price=105.0)

    assert stats.candles == 3
    assert stats.close_in_range_ratio == 2 / 3
    assert stats.survived_all_closes is False
    assert stats.first_close_outside_at == "2026-09-22T00:10:00+00:00"
    assert stats.ending_return_pct == 10.000000000000009
