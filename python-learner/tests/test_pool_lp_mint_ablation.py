from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.pool_lp_learning import (
    ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    ENRICHED_LP_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
    build_mint_enriched_lp_training_frame,
    evaluate_mint_feature_ablation,
)
from meteora_learner.pool_lp_training import (
    ENRICHED_LP_MODEL_TARGET_COLUMNS,
)
from meteora_learner.pool_token_mint_context import (
    TOKEN_MINT_CONTEXT_FEATURE_COLUMNS,
)
from meteora_learner.storage import Storage


def _chain_pool_payload() -> dict:
    return {
        "pool_address": "POOL",
        "active_bin_id": 0,
        "bin_step": 25,
        "token_x_mint": "MINT_X",
        "token_y_mint": "MINT_Y",
        "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
        "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
        "base_fee_rate": "0",
        "variable_fee_rate": "0",
        "total_fee_rate": "0",
        "deposit_total_fee_rate": "0",
        "protocol_share_bps": 0,
        "collect_fee_mode": 0,
        "supports_limit_order": True,
        "bin_arrays": [],
    }


def _mint_payload(
    mint: str,
    *,
    authority: str | None = None,
) -> dict:
    return {
        "mint_address": mint,
        "token_program": STANDARD_SPL_TOKEN_PROGRAM,
        "capture_slot_start": 100,
        "capture_slot_end": 101,
        "supply": "1000000000000",
        "decimals": 6,
        "is_initialized": True,
        "mint_authority": authority,
        "freeze_authority": None,
        "data_len": 82,
        "token_2022_extension_data_len": 0,
        "has_token_2022_extension_data": False,
    }


def _market_enriched_lp_row(
    decision: str,
    forward_end: str,
) -> dict:
    row = {
        "pool_address": "POOL",
        "decision_observed_at": decision,
        "forward_end_observed_at": forward_end,
        "target_net_return_bps": 100.0,
        "target_excess_vs_hold_bps": 40.0,
        "target_range_survival_ratio": 0.8,
    }
    for index, column in enumerate(
        ENRICHED_LP_FEATURE_COLUMNS
    ):
        row[column] = 1.0 + index * 0.01
    return row


def test_build_mint_enriched_frame_uses_complete_as_of_context(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_pool_snapshot(
        _chain_pool_payload(),
        observed_at="2026-01-01T04:00:00+00:00",
    )
    storage.save_token_mint_snapshot(
        _mint_payload("MINT_X"),
        observed_at="2026-01-01T04:15:00+00:00",
    )
    storage.save_token_mint_snapshot(
        _mint_payload("MINT_Y", authority="AUTH"),
        observed_at="2026-01-01T04:30:00+00:00",
    )

    source = pd.DataFrame(
        [
            _market_enriched_lp_row(
                "2026-01-01T05:00:00Z",
                "2026-01-01T07:00:00Z",
            )
        ]
    )

    frame, report = build_mint_enriched_lp_training_frame(
        str(storage.path),
        source,
    )

    assert report.rows_seen == 1
    assert report.rows_ready == 1
    assert report.rows_with_chain_pool_state == 1
    assert report.rows_with_both_mints == 1
    assert set(TOKEN_MINT_CONTEXT_FEATURE_COLUMNS) <= set(
        frame.columns
    )
    assert frame.iloc[0][
        "mint_x_mint_authority_active"
    ] == 0.0
    assert frame.iloc[0][
        "mint_y_mint_authority_active"
    ] == 1.0


def test_build_mint_enriched_frame_drops_missing_mint_evidence(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    storage.save_chain_pool_snapshot(
        _chain_pool_payload(),
        observed_at="2026-01-01T04:00:00+00:00",
    )
    storage.save_token_mint_snapshot(
        _mint_payload("MINT_X"),
        observed_at="2026-01-01T04:15:00+00:00",
    )

    source = pd.DataFrame(
        [
            _market_enriched_lp_row(
                "2026-01-01T05:00:00Z",
                "2026-01-01T07:00:00Z",
            )
        ]
    )

    frame, report = build_mint_enriched_lp_training_frame(
        str(storage.path),
        source,
    )

    assert frame.empty
    assert report.rows_ready == 0
    assert report.rows_dropped_missing_mint_context == 1


def _synthetic_ablation_frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")

    for pool_index, pool in enumerate(("A", "B", "C")):
        for index in range(60):
            signal = float((index + pool_index) % 2)
            row = {
                "pool_address": pool,
                "decision_observed_at": (
                    start + pd.Timedelta(hours=index)
                ),
                "forward_end_observed_at": (
                    start + pd.Timedelta(hours=index + 4)
                ),
            }

            for column in ENRICHED_LP_FEATURE_COLUMNS:
                row[column] = 1.0

            for column in TOKEN_MINT_CONTEXT_FEATURE_COLUMNS:
                row[column] = 0.0

            row["mint_x_mint_authority_active"] = signal
            row["mint_y_mint_authority_active"] = signal
            row["mint_x_initialized"] = 1.0
            row["mint_y_initialized"] = 1.0
            row["mint_x_program_standard_spl"] = 1.0
            row["mint_y_program_standard_spl"] = 1.0
            row["mint_x_program_matches_pool"] = 1.0
            row["mint_y_program_matches_pool"] = 1.0

            signed = 1.0 if signal else -1.0
            row["target_net_return_bps"] = 500.0 * signed
            row["target_excess_vs_hold_bps"] = 300.0 * signed
            row["target_range_survival_ratio"] = (
                0.9 if signal else 0.1
            )
            rows.append(row)

    return pd.DataFrame(rows)



@pytest.fixture(scope="module")
def mint_ablation_report():
    return evaluate_mint_feature_ablation(
        _synthetic_ablation_frame(),
        min_train_decision_times=25,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )


def test_mint_ablation_uses_same_purged_rows_and_detects_signal(
    mint_ablation_report,
) -> None:
    report = mint_ablation_report

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.base_feature_count == len(
        ENRICHED_LP_FEATURE_COLUMNS
    )
    assert report.mint_feature_count == len(
        TOKEN_MINT_CONTEXT_FEATURE_COLUMNS
    )
    assert report.combined_feature_count == len(
        MINT_ENRICHED_LP_FEATURE_COLUMNS
    )
    assert all(fold.purged_rows > 0 for fold in report.folds)

    net = next(
        item for item in report.aggregates
        if item.target == "target_net_return_bps"
    )
    assert net.mean_mint_mae_improvement > 0
    assert net.mint_better_folds > 0


def test_mint_ablation_reports_all_continuous_lp_targets(
    mint_ablation_report,
) -> None:
    report = mint_ablation_report

    assert tuple(
        item.target for item in report.aggregates
    ) == ENRICHED_LP_MODEL_TARGET_COLUMNS

    for item in report.aggregates:
        assert np.isfinite(item.mean_base_model_mae)
        assert np.isfinite(item.mean_mint_model_mae)
        assert np.isfinite(item.mean_mint_mae_improvement)
        assert 0 <= item.mint_better_folds <= item.folds


def test_mint_ablation_emits_no_policy_verdict(
    mint_ablation_report,
) -> None:
    record = mint_ablation_report.to_record()
    assert record["policy_actionable"] is False
    assert record["execution_wired"] is False
    assert "qualified" not in record
    assert "approved" not in record
    assert "allocation" not in record
    assert "recommended_action" not in record
