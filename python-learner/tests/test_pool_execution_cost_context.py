from __future__ import annotations

import numpy as np
import pandas as pd

from meteora_learner.pool_execution_cost_context import (
    EXECUTION_COST_ENRICHED_LP_FEATURE_COLUMNS,
    POOL_EXECUTION_COST_FEATURE_COLUMNS,
    attach_execution_cost_context_from_store,
    evaluate_execution_cost_ablation,
)
from meteora_learner.pool_lp_learning import ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS
from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_training import ENRICHED_LP_MODEL_TARGET_COLUMNS
from meteora_learner.storage import Storage


def _event(
    *,
    signature: str,
    block_time: int,
    fee: int,
    compute: int,
    event_type: str,
    amount_x: str = "0",
    amount_y: str = "0",
    requested_x: str | None = None,
    requested_y: str | None = None,
    comp_x: str = "0",
    comp_y: str = "0",
) -> dict:
    parent_ix = 2
    events = []

    if event_type == "AddLiquidity":
        events.append(
            {
                "event_index": 0,
                "parent_ix_index": parent_ix,
                "event": {
                    "event_type": "AddLiquidity",
                    "event": {
                        "lb_pair": "POOL",
                        "position": "POS",
                        "active_bin_id": 10,
                        "amount_x": amount_x,
                        "amount_y": amount_y,
                    },
                },
            }
        )
        events.append(
            {
                "event_index": 1,
                "parent_ix_index": parent_ix,
                "event": {
                    "event_type": "CompositionFee",
                    "event": {
                        "bin_id": 10,
                        "token_x_fee_amount": comp_x,
                        "token_y_fee_amount": comp_y,
                        "protocol_token_x_fee_amount": "0",
                        "protocol_token_y_fee_amount": "0",
                    },
                },
            }
        )
        add_requests = [
            {
                "instruction_index": parent_ix,
                "instruction_type": "AddLiquidityByStrategy2",
                "requested_amount_x": requested_x,
                "requested_amount_y": requested_y,
            }
        ]
        rebalance_requests = []
    else:
        events.append(
            {
                "event_index": 0,
                "parent_ix_index": parent_ix,
                "event": {
                    "event_type": "Rebalancing",
                    "event": {
                        "lb_pair": "POOL",
                        "position": "POS",
                        "owner": "OWNER",
                        "active_bin_id": 10,
                        "x_withdrawn_amount": "100",
                        "x_added_amount": "90",
                        "y_withdrawn_amount": "200",
                        "y_added_amount": "180",
                        "x_fee_amount": "5",
                        "y_fee_amount": "8",
                        "old_min_id": 5,
                        "old_max_id": 15,
                        "new_min_id": 6,
                        "new_max_id": 16,
                        "reward_one": "0",
                        "reward_two": "0",
                    },
                },
            }
        )
        add_requests = []
        rebalance_requests = []

    return {
        "signature": signature,
        "slot": block_time,
        "block_time": block_time,
        "network_fee_lamports": fee,
        "compute_units_consumed": compute,
        "succeeded": True,
        "add_requests": add_requests,
        "rebalance_requests": rebalance_requests,
        "events": events,
    }


def test_execution_cost_context_is_as_of_and_dimensionless_where_needed(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    t0 = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp())
    t1 = int(pd.Timestamp("2026-01-01T01:00:00Z").timestamp())
    future = int(pd.Timestamp("2026-01-01T04:00:00Z").timestamp())

    storage.save_chain_transaction_events(
        _event(
            signature="ADD1",
            block_time=t0,
            fee=5_000,
            compute=100_000,
            event_type="AddLiquidity",
            amount_x="900",
            amount_y="1800",
            requested_x="1000",
            requested_y="2000",
            comp_x="9",
            comp_y="18",
        )
    )
    storage.save_chain_transaction_events(
        _event(
            signature="REB1",
            block_time=t1,
            fee=7_000,
            compute=120_000,
            event_type="Rebalancing",
        )
    )
    storage.save_chain_transaction_events(
        _event(
            signature="FUTURE",
            block_time=future,
            fee=999_999,
            compute=999_999,
            event_type="AddLiquidity",
            amount_x="1",
            amount_y="1",
            requested_x="1000",
            requested_y="1000",
            comp_x="1",
            comp_y="1",
        )
    )

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T03:00:00Z",
            }
        ]
    )

    enriched, report = attach_execution_cost_context_from_store(
        str(storage.path),
        decisions,
    )
    row = enriched.iloc[0]

    assert report.rows_with_history == 1
    assert report.rows_with_complete_context == 1
    assert row["execution_history_samples"] == 2.0
    assert row["execution_history_age_seconds"] == 7200.0
    assert row["execution_median_network_fee_lamports"] == 6000.0
    assert row["execution_p95_network_fee_lamports"] == 7000.0
    assert row["execution_median_compute_units"] == 110000.0
    assert row["execution_add_fraction"] == 0.5
    assert row["execution_rebalance_fraction"] == 0.5
    assert row["execution_add_request_coverage_rate"] == 1.0
    assert row["execution_median_add_underfill_bps"] == 1000.0
    assert row["execution_median_composition_fee_bps"] == 100.0


def test_execution_cost_context_reports_missing_history(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    decisions = pd.DataFrame(
        [
            {
                "pool_address": "UNKNOWN",
                "decision_observed_at": "2026-01-01T03:00:00Z",
            }
        ]
    )

    enriched, report = attach_execution_cost_context_from_store(
        str(storage.path),
        decisions,
    )

    assert report.rows_missing_history == 1
    assert report.rows_with_complete_context == 0
    assert enriched[list(POOL_EXECUTION_COST_FEATURE_COLUMNS)].isna().all(axis=None)


def _ablation_frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for pool_index, pool in enumerate(("A", "B", "C")):
        for index in range(90):
            signal = float((index + pool_index) % 2)
            row = {
                "pool_address": pool,
                "decision_observed_at": start + pd.Timedelta(hours=index),
                "forward_end_observed_at": start + pd.Timedelta(hours=index + 4),
            }
            for column in MINT_ENRICHED_LP_FEATURE_COLUMNS:
                row[column] = 1.0
            for feature_index, column in enumerate(
                POOL_EXECUTION_COST_FEATURE_COLUMNS
            ):
                row[column] = 1.0 + 0.01 * feature_index
            row["execution_median_network_fee_lamports"] = (
                5_000.0 if signal else 50_000.0
            )

            signed = 1.0 if signal else -1.0
            row["target_net_return_bps"] = 500.0 * signed
            row["target_excess_vs_hold_bps"] = 300.0 * signed
            row["target_range_survival_ratio"] = 0.9 if signal else 0.1
            rows.append(row)
    return pd.DataFrame(rows)


def test_execution_cost_ablation_detects_incremental_signal() -> None:
    report = evaluate_execution_cost_ablation(
        _ablation_frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.combined_feature_count == len(
        EXECUTION_COST_ENRICHED_LP_FEATURE_COLUMNS
    )
    assert tuple(
        item.target for item in report.aggregates
    ) == ENRICHED_LP_MODEL_TARGET_COLUMNS

    net = next(
        item for item in report.aggregates
        if item.target == "target_net_return_bps"
    )
    assert net.mean_execution_mae_improvement > 0
    assert net.execution_better_folds > 0
