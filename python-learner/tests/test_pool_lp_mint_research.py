from __future__ import annotations

from pathlib import Path

import pandas as pd

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.pool_lp_mint_research import (
    run_mint_feature_research_from_dataset_file,
)
from meteora_learner.storage import Storage


def _save_market_history(
    storage: Storage,
    *,
    pools: tuple[str, ...],
    observations: int = 90,
) -> None:
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for index in range(observations):
        observed_at = (
            start + pd.Timedelta(hours=index)
        ).isoformat()
        for pool in pools:
            storage.save_pool_snapshot(
                {
                    "address": pool,
                    "name": f"{pool}-pool",
                    "tvl": 100_000.0 + 500.0 * index,
                    "volume_24h": 20_000.0 + 250.0 * index,
                    "fees_24h": 80.0 + 0.5 * index,
                    "current_price": 10.0 + 0.02 * index,
                    "bin_step": 25,
                    "active_bin_id": 0,
                    "apr": 12.0,
                    "apy": 13.0,
                    "dynamic_fee_pct": 0.2,
                    "pool_config": {
                        "base_fee_pct": 0.1,
                        "max_fee_pct": 1.0,
                        "protocol_fee_pct": 0.05,
                        "collect_fee_mode": 0,
                    },
                    "is_blacklisted": False,
                    "created_at": int(
                        pd.Timestamp(
                            "2025-12-01T00:00:00Z"
                        ).timestamp()
                    ),
                    "token_x": {"symbol": "X", "decimals": 6},
                    "token_y": {"symbol": "Y", "decimals": 6},
                },
                observed_at=observed_at,
            )


def _save_chain_and_mints(
    storage: Storage,
    *,
    pool: str,
    x_authority: str | None,
) -> None:
    x_mint = f"{pool}-X"
    y_mint = f"{pool}-Y"

    storage.save_chain_pool_snapshot(
        {
            "pool_address": pool,
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": x_mint,
            "token_y_mint": y_mint,
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
        },
        observed_at="2026-01-01T00:00:00+00:00",
    )

    for mint, authority in (
        (x_mint, x_authority),
        (y_mint, None),
    ):
        storage.save_token_mint_snapshot(
            {
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
            },
            observed_at="2026-01-01T00:05:00+00:00",
        )


def _write_canonical_dataset(path: Path) -> None:
    pools = (
        ("A", True),
        ("B", False),
        ("C", True),
    )
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    rows = []

    for index in range(10, 80):
        decision = start + pd.Timedelta(hours=index)
        forward_end = decision + pd.Timedelta(hours=4)

        for pool, authority_active in pools:
            row = {
                "pool_address": pool,
                "decision_observed_at": decision.isoformat(),
                "forward_end_observed_at": forward_end.isoformat(),
            }
            for column in ML_FEATURE_COLUMNS:
                row[column] = 1.0

            signed = 1.0 if authority_active else -1.0
            row["target_net_return_bps"] = 500.0 * signed
            row["target_excess_vs_hold_bps"] = 300.0 * signed
            row["target_range_survival_ratio"] = (
                0.9 if authority_active else 0.1
            )
            row["target_positive_excess"] = int(
                authority_active
            )
            rows.append(row)

    pd.DataFrame(rows).to_csv(path, index=False)


def _seed_complete_store(storage: Storage) -> None:
    pools = ("A", "B", "C")
    _save_market_history(storage, pools=pools)
    _save_chain_and_mints(
        storage,
        pool="A",
        x_authority="AUTH-A",
    )
    _save_chain_and_mints(
        storage,
        pool="B",
        x_authority=None,
    )
    _save_chain_and_mints(
        storage,
        pool="C",
        x_authority="AUTH-C",
    )


def test_canonical_dataset_mint_research_runs_end_to_end(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_complete_store(storage)

    dataset = tmp_path / "canonical.csv"
    _write_canonical_dataset(dataset)

    report = run_mint_feature_research_from_dataset_file(
        str(storage.path),
        dataset,
        volatility_window=4,
        drawdown_window=6,
        activity_window=4,
        min_train_decision_times=25,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert report.status == "EVALUATED"
    assert report.reason is None
    assert report.source_dataset_rows == 210
    assert len(report.source_dataset_sha256) == 64
    assert report.market_enrichment is not None
    assert report.market_enrichment.rows_ready == 210
    assert report.mint_enrichment is not None
    assert report.mint_enrichment.rows_ready == 210
    assert report.ablation is not None
    assert report.context_ablation is not None
    assert report.unseen_pool_validation is None
    assert report.unseen_pool_reason is not None
    assert "purged training rows" in report.unseen_pool_reason
    assert report.tail_risk_calibration is not None
    assert len(report.tail_risk_calibration.folds) > 0
    assert report.tail_risk_reason is None
    assert report.feature_drift is not None
    assert len(report.feature_drift.folds) > 0
    assert report.feature_drift_reason is None
    assert report.source_outcome_coverage is not None
    assert report.source_outcome_coverage.rows_seen == 210
    assert report.model_ready_outcome_coverage is not None
    assert report.model_ready_outcome_coverage.rows_retained == 210
    assert report.model_ready_outcome_coverage.rows_dropped == 0
    assert report.api_metadata_enrichment is not None
    assert report.api_metadata_enrichment.rows_ready == 210
    assert report.api_metadata_ablation is not None
    assert report.api_metadata_reason is None
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False

    net = next(
        item for item in report.ablation.aggregates
        if item.target == "target_net_return_bps"
    )
    assert net.mean_mint_mae_improvement > 0
    assert net.mint_better_folds > 0

    staged_net = next(
        item for item in report.context_ablation.aggregates
        if item.target == "target_net_return_bps"
    )
    assert staged_net.mean_mint_mae_improvement > 0
    assert staged_net.mint_better_folds > 0


def test_canonical_dataset_reports_missing_mint_context(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    pools = ("A", "B", "C")
    _save_market_history(storage, pools=pools)

    for pool in pools:
        storage.save_chain_pool_snapshot(
            {
                "pool_address": pool,
                "active_bin_id": 0,
                "bin_step": 25,
                "token_x_mint": f"{pool}-X",
                "token_y_mint": f"{pool}-Y",
                "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
                "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
                "bin_arrays": [],
            },
            observed_at="2026-01-01T00:00:00+00:00",
        )

    dataset = tmp_path / "canonical.csv"
    _write_canonical_dataset(dataset)

    report = run_mint_feature_research_from_dataset_file(
        str(storage.path),
        dataset,
        volatility_window=4,
        drawdown_window=6,
        activity_window=4,
        min_train_decision_times=25,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert report.status == "COLLECTING_CONTEXT"
    assert report.ablation is None
    assert report.context_ablation is None
    assert report.unseen_pool_validation is None
    assert report.unseen_pool_reason is None
    assert report.tail_risk_calibration is None
    assert report.tail_risk_reason is None
    assert report.feature_drift is None
    assert report.feature_drift_reason is None
    assert report.mint_enrichment is not None
    assert report.mint_enrichment.rows_ready == 0
    assert report.source_outcome_coverage is not None
    assert report.source_outcome_coverage.rows_seen == 210
    assert report.model_ready_outcome_coverage is not None
    assert report.model_ready_outcome_coverage.rows_retained == 0
    assert report.model_ready_outcome_coverage.rows_dropped == 210
    assert report.api_metadata_enrichment is None
    assert report.api_metadata_ablation is None
    assert report.api_metadata_reason is None
    assert (
        report.mint_enrichment.rows_dropped_missing_mint_context
        == 210
    )
    assert report.policy_actionable is False
