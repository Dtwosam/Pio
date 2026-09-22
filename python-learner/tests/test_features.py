import pandas as pd

from meteora_learner.features import build_market_features


def test_features_are_time_ordered_and_backward_looking():
    frame = pd.DataFrame(
        {
            "candle_time": [
                "2026-09-22T00:10:00Z",
                "2026-09-22T00:00:00Z",
                "2026-09-22T00:05:00Z",
                "2026-09-22T00:15:00Z",
                "2026-09-22T00:20:00Z",
                "2026-09-22T00:25:00Z",
                "2026-09-22T00:30:00Z",
            ],
            "open": [3, 1, 2, 4, 5, 6, 7],
            "high": [4, 2, 3, 5, 6, 7, 8],
            "low": [2, 0.5, 1, 3, 4, 5, 6],
            "close": [3, 1, 2, 4, 5, 6, 7],
            "volume": [30, 10, 20, 40, 50, 60, 70],
        }
    )
    out = build_market_features(frame)
    assert out.iloc[0]["close"] == 1
    assert out.iloc[-1]["close"] == 7
    assert out.iloc[-1]["momentum_3"] == 0.75
