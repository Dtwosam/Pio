from __future__ import annotations

import json

import pytest

from meteora_learner.live_outcome_evidence import (
    build_live_outcome_evidence,
)
from meteora_learner.storage import Storage


def _insert(
    storage: Storage,
    *,
    position: str,
    pool: str,
    model: str,
    expected_return_pct: str,
    expected_downside_pct: str,
    realized_return_bps: int,
    realized_pnl_quote: str,
    entry_outflow_quote: str,
    composition_cost_quote: str,
    network_cost_quote: str,
    fee_income_quote: str,
    reward_income_quote: str,
    created_at: str,
) -> None:
    expected_return_bps = int(float(expected_return_pct) * 100)
    prediction_error = realized_return_bps - expected_return_bps

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_position_valuations(
                position_address, pool_address,
                opened_decision_id, closed_decision_id,
                quote_unit, valued_execution_count,
                principal_cashflow_quote,
                composition_cost_quote,
                fee_income_quote, reward_income_quote,
                network_cost_quote, realized_pnl_quote,
                entry_outflow_quote, realized_return_bps,
                max_age_seconds, created_at,
                quote_evidence_json, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position,
                pool,
                f"{position}-OPEN",
                f"{position}-CLOSE",
                "USD",
                3,
                "0",
                composition_cost_quote,
                fee_income_quote,
                reward_income_quote,
                network_cost_quote,
                realized_pnl_quote,
                entry_outflow_quote,
                realized_return_bps,
                60,
                created_at,
                "[]",
                "{}",
            ),
        )
        label = {
            "position_address": position,
            "decision_id": f"{position}-OPEN",
            "pool_address": pool,
            "model_version": model,
            "strategy": "SPOT",
            "min_bin_id": -2,
            "max_bin_id": 2,
            "range_width_bins": 5,
            "proposed_capital_quote": entry_outflow_quote,
            "expected_net_return_pct": expected_return_pct,
            "expected_downside_pct": expected_downside_pct,
            "realized_pnl_quote": realized_pnl_quote,
            "realized_return_bps": realized_return_bps,
            "prediction_error_bps": prediction_error,
            "target_positive_return": int(
                float(realized_pnl_quote) > 0
            ),
            "quote_unit": "USD",
            "opened_signature": f"{position}-SIG",
            "closed_decision_id": f"{position}-CLOSE",
        }
        conn.execute(
            """
            INSERT INTO live_learning_labels(
                position_address, decision_id, pool_address,
                model_version, strategy,
                min_bin_id, max_bin_id, range_width_bins,
                proposed_capital_quote,
                expected_net_return_pct, expected_downside_pct,
                realized_pnl_quote, realized_return_bps,
                prediction_error_bps, target_positive_return,
                quote_unit, opened_signature, closed_decision_id,
                created_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position,
                f"{position}-OPEN",
                pool,
                model,
                "SPOT",
                -2,
                2,
                5,
                entry_outflow_quote,
                expected_return_pct,
                expected_downside_pct,
                realized_pnl_quote,
                realized_return_bps,
                prediction_error,
                int(float(realized_pnl_quote) > 0),
                "USD",
                f"{position}-SIG",
                f"{position}-CLOSE",
                created_at,
                json.dumps(label, sort_keys=True),
            ),
        )


def test_live_outcome_evidence_normalizes_quote_backed_costs(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _insert(
        storage,
        position="P1",
        pool="A",
        model="M1",
        expected_return_pct="4",
        expected_downside_pct="2",
        realized_return_bps=500,
        realized_pnl_quote="50",
        entry_outflow_quote="1000",
        composition_cost_quote="10",
        network_cost_quote="5",
        fee_income_quote="30",
        reward_income_quote="0",
        created_at="2026-01-01T01:00:00+00:00",
    )
    _insert(
        storage,
        position="P2",
        pool="B",
        model="M1",
        expected_return_pct="1",
        expected_downside_pct="2",
        realized_return_bps=-300,
        realized_pnl_quote="-60",
        entry_outflow_quote="2000",
        composition_cost_quote="20",
        network_cost_quote="10",
        fee_income_quote="20",
        reward_income_quote="0",
        created_at="2026-01-02T01:00:00+00:00",
    )

    report = build_live_outcome_evidence(str(storage.path))

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.samples_seen == 2
    assert report.pools_seen == 2
    assert report.models_seen == 1
    assert report.positive_return_rate == 0.5
    assert report.mean_prediction_error_bps == -150.0
    assert report.mean_absolute_prediction_error_bps == 250.0
    assert report.downside_exceedance_rate == 0.5
    assert report.mean_composition_cost_bps == 100.0
    assert report.mean_network_cost_bps == 50.0
    assert report.mean_fee_income_bps == 200.0

    assert len(report.model_calibration) == 1
    assert report.model_calibration[0].model_version == "M1"
    assert report.model_calibration[0].samples == 2


def test_live_outcome_evidence_empty_store_is_descriptive(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = build_live_outcome_evidence(str(storage.path))

    assert report.samples_seen == 0
    assert report.positive_return_rate is None
    assert report.model_calibration == ()
    assert report.samples == ()


def test_live_outcome_evidence_rejects_label_valuation_mismatch(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _insert(
        storage,
        position="P1",
        pool="A",
        model="M1",
        expected_return_pct="4",
        expected_downside_pct="2",
        realized_return_bps=500,
        realized_pnl_quote="50",
        entry_outflow_quote="1000",
        composition_cost_quote="10",
        network_cost_quote="5",
        fee_income_quote="30",
        reward_income_quote="0",
        created_at="2026-01-01T01:00:00+00:00",
    )
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE live_position_valuations
            SET realized_return_bps = 999
            WHERE position_address = 'P1'
            """
        )

    with pytest.raises(
        ValueError,
        match="realized return differs",
    ):
        build_live_outcome_evidence(str(storage.path))


def test_live_outcome_evidence_matches_fractional_expected_return_rounding(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _insert(
        storage,
        position="P-FRACTIONAL",
        pool="A",
        model="M1",
        expected_return_pct="1.234",
        expected_downside_pct="2.5",
        realized_return_bps=200,
        realized_pnl_quote="20",
        entry_outflow_quote="1000",
        composition_cost_quote="0",
        network_cost_quote="1",
        fee_income_quote="0",
        reward_income_quote="0",
        created_at="2026-01-01T01:00:00+00:00",
    )

    report = build_live_outcome_evidence(str(storage.path))

    assert report.samples_seen == 1
    sample = report.samples[0]
    assert sample.expected_net_return_bps == 123.0
    assert sample.prediction_error_bps == 77
