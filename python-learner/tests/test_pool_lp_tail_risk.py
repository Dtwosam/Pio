from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_tail_risk import (
    evaluate_tail_risk_calibration,
)


def _frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")

    for pool_index, pool in enumerate(("A", "B", "C")):
        for index in range(90):
            wave = np.sin(index / 5.0 + pool_index)
            row = {
                "pool_address": pool,
                "decision_observed_at": (
                    start + pd.Timedelta(hours=index)
                ),
                "forward_end_observed_at": (
                    start + pd.Timedelta(hours=index + 4)
                ),
            }
            for feature_index, column in enumerate(
                MINT_ENRICHED_LP_FEATURE_COLUMNS
            ):
                row[column] = (
                    1.0
                    + 0.005 * feature_index
                    + 0.002 * index
                    + 0.03 * wave
                )

            shock = -700.0 if index % 17 == 0 else 0.0
            net = 120.0 * wave + 1.5 * index - 50.0 + shock
            row["target_net_return_bps"] = net
            row["target_excess_vs_hold_bps"] = net * 0.7
            row["target_range_survival_ratio"] = float(
                np.clip(0.75 + 0.1 * wave, 0.0, 1.0)
            )
            rows.append(row)

    return pd.DataFrame(rows)


def test_tail_risk_calibration_is_purged_and_research_only() -> None:
    report = evaluate_tail_risk_calibration(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert len(report.folds) >= 3
    assert all(fold.purged_rows > 0 for fold in report.folds)


def test_tail_risk_reports_return_and_downside_quantiles() -> None:
    report = evaluate_tail_risk_calibration(
        _frame(),
        return_quantiles=(0.1, 0.5, 0.9),
        downside_quantiles=(0.5, 0.9),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    keys = {
        (item.target, item.quantile)
        for item in report.aggregates
    }
    assert keys == {
        ("target_net_return_bps", 0.1),
        ("target_net_return_bps", 0.5),
        ("target_net_return_bps", 0.9),
        ("target_downside_bps", 0.5),
        ("target_downside_bps", 0.9),
    }

    for item in report.aggregates:
        assert np.isfinite(item.mean_pinball_loss)
        assert 0.0 <= item.mean_empirical_below_rate <= 1.0
        assert -1.0 <= item.mean_calibration_error <= 1.0


def test_tail_risk_reports_quantile_crossing_instead_of_hiding_it() -> None:
    report = evaluate_tail_risk_calibration(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert 0.0 <= report.mean_return_quantile_crossing_rate <= 1.0
    assert 0.0 <= report.mean_downside_quantile_crossing_rate <= 1.0


def test_tail_risk_rejects_invalid_quantile_configuration() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        evaluate_tail_risk_calibration(
            _frame(),
            return_quantiles=(0.5, 0.1),
        )

    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        evaluate_tail_risk_calibration(
            _frame(),
            downside_quantiles=(0.5, 1.0),
        )


def test_tail_risk_emits_no_policy_verdict() -> None:
    report = evaluate_tail_risk_calibration(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    record = report.to_record()
    assert record["policy_actionable"] is False
    assert record["execution_wired"] is False
    assert "approved" not in record
    assert "qualified" not in record
    assert "allocation" not in record
    assert "recommended_action" not in record
