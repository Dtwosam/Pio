from __future__ import annotations

import pandas as pd
import pytest

from meteora_learner.pool_lp_outcome_coverage import (
    build_outcome_coverage_report,
    compare_outcome_coverage,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "pool_address": "A",
                "target_net_return_bps": -1200.0,
                "target_excess_vs_hold_bps": -800.0,
                "target_range_survival_ratio": 0.1,
            },
            {
                "pool_address": "A",
                "target_net_return_bps": -100.0,
                "target_excess_vs_hold_bps": -50.0,
                "target_range_survival_ratio": 0.5,
            },
            {
                "pool_address": "B",
                "target_net_return_bps": 200.0,
                "target_excess_vs_hold_bps": 100.0,
                "target_range_survival_ratio": 0.8,
            },
            {
                "pool_address": "B",
                "target_net_return_bps": 500.0,
                "target_excess_vs_hold_bps": 300.0,
                "target_range_survival_ratio": 0.9,
            },
        ]
    )


def test_outcome_coverage_preserves_severe_losses() -> None:
    report = build_outcome_coverage_report(
        _frame(),
        quantiles=(0.1, 0.5, 0.9),
    )

    net = next(
        item for item in report.targets
        if item.target == "target_net_return_bps"
    )
    downside = next(
        item for item in report.targets
        if item.target == "target_downside_bps"
    )

    assert net.minimum == -1200.0
    assert downside.maximum == 1200.0
    assert sum(
        item.negative_net_return_rows
        for item in report.pools
    ) == 2
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_coverage_comparison_exposes_loss_selection_bias() -> None:
    source = _frame()
    ready = source[source["target_net_return_bps"] > -500].copy()

    report = compare_outcome_coverage(
        source,
        ready,
        quantiles=(0.1, 0.5, 0.9),
    )

    assert report.rows_retained == 3
    assert report.rows_dropped == 1
    assert report.source_negative_net_return_rows == 2
    assert report.model_ready_negative_net_return_rows == 1
    assert report.negative_net_return_retention_rate == 0.5
    assert report.source_worst_net_return_bps == -1200.0
    assert report.model_ready_worst_net_return_bps == -100.0
    assert report.source_maximum_downside_bps == 1200.0
    assert report.model_ready_maximum_downside_bps == 100.0


def test_empty_model_ready_frame_is_reported_not_filled() -> None:
    source = _frame()
    empty = source.iloc[0:0].copy()

    report = compare_outcome_coverage(source, empty)

    assert report.rows_retained == 0
    assert report.retention_rate == 0.0
    assert report.model_ready_worst_net_return_bps is None
    assert report.model_ready_maximum_downside_bps is None
    assert report.model_ready_negative_net_return_rows == 0


def test_outcome_coverage_rejects_invalid_quantiles() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        build_outcome_coverage_report(
            _frame(),
            quantiles=(0.9, 0.1),
        )

    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        build_outcome_coverage_report(
            _frame(),
            quantiles=(0.5, 1.0),
        )
