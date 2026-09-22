import pytest

from meteora_learner.validation import ValidationPair, summarize_validation


def test_validation_summary_measures_bias_and_error():
    summary = summarize_validation(
        [
            ValidationPair("a", predicted_pnl_usd=10, actual_pnl_usd=8, predicted_return_pct=5, actual_return_pct=4),
            ValidationPair("b", predicted_pnl_usd=-2, actual_pnl_usd=-4, predicted_return_pct=-1, actual_return_pct=-2),
        ]
    )

    assert summary.positions == 2
    assert summary.pnl_mae_usd == pytest.approx(2.0)
    assert summary.pnl_rmse_usd == pytest.approx(2.0)
    assert summary.pnl_bias_usd == pytest.approx(2.0)
    assert summary.return_mae_pct_points == pytest.approx(1.0)
