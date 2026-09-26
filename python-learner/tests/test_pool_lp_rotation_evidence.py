from __future__ import annotations

import numpy as np
import pandas as pd

from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_rotation_evidence import (
    evaluate_rotation_transition_evidence,
)


def _frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    pools = ("A", "B", "C")

    for index in range(70):
        leader_index = index % len(pools)
        for pool_index, pool in enumerate(pools):
            distance = abs(pool_index - leader_index)
            score = float(3 - distance)
            row = {
                "pool_address": pool,
                "decision_observed_at": (
                    start + pd.Timedelta(hours=index)
                ),
                "forward_end_observed_at": (
                    start + pd.Timedelta(hours=index + 3)
                ),
            }
            for column in MINT_ENRICHED_LP_FEATURE_COLUMNS:
                row[column] = 1.0
            row["market_volume_to_tvl"] = score
            row["mint_x_log10_supply_tokens"] = score

            net = score * 250.0 - 500.0
            row["target_net_return_bps"] = net
            row["target_excess_vs_hold_bps"] = net * 0.7
            row["target_range_survival_ratio"] = float(
                np.clip(0.4 + 0.15 * score, 0.0, 1.0)
            )
            rows.append(row)
    return pd.DataFrame(rows)


def test_rotation_evidence_is_purged_and_non_actionable() -> None:
    report = evaluate_rotation_transition_evidence(
        _frame(),
        min_train_decision_times=25,
        validation_decision_times=6,
        step_decision_times=6,
        min_train_rows=60,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.switching_transition_costs_included is False
    assert all(fold.purged_rows > 0 for fold in report.folds)


def test_rotation_evidence_records_leader_changes_and_realized_value() -> None:
    report = evaluate_rotation_transition_evidence(
        _frame(),
        min_train_decision_times=25,
        validation_decision_times=6,
        step_decision_times=6,
        min_train_rows=60,
    )

    assert report.transition_opportunities > 0
    assert report.leader_changes > 0
    assert 0.0 < report.leader_change_rate <= 1.0
    assert report.beneficial_change_rate is not None
    assert report.beneficial_change_rate > 0.5
    assert report.mean_realized_switch_advantage_bps is not None
    assert report.mean_realized_switch_advantage_bps > 0.0


def test_rotation_evidence_does_not_invent_switch_threshold() -> None:
    report = evaluate_rotation_transition_evidence(
        _frame(),
        min_train_decision_times=25,
        validation_decision_times=6,
        step_decision_times=6,
        min_train_rows=60,
    )

    record = report.to_record()
    assert record["policy_actionable"] is False
    assert record["switching_transition_costs_included"] is False
    assert "switch_threshold_bps" not in record
    assert "recommended_action" not in record
    assert "allocation" not in record
