from types import SimpleNamespace

import pandas as pd
import pytest

import meteora_learner.phase9_bandit_dataset as bandit_module
from meteora_learner.ml_retraining_dataset import (
    MultiPoolMLDataset,
    MultiPoolMLDatasetReport,
)
from meteora_learner.phase9_bandit_dataset import (
    PHASE9_BANDIT_DATASET_EVIDENCE_TYPE,
    build_phase9_bandit_dataset,
    evaluate_phase9_contextual_bandit_from_dataset,
    load_phase9_bandit_dataset,
    persist_phase9_bandit_dataset,
)
from meteora_learner.phase9_explicit_inputs import (
    parse_phase9_explicit_inputs,
    persist_phase9_explicit_inputs,
)
from meteora_learner.storage import Storage


def explicit_payload():
    return {
        "static_hedges": [
            {
                "pool_address": "pool-a",
                "amount_x": 100,
                "amount_y": 100,
                "instrument": {
                    "instrument_id": "SOL-PERP",
                    "venue": "TEST",
                    "available_liquidity_y_atomic": 1_000_000,
                    "max_liquidity_share_bps": 1000,
                    "max_leverage": 1.0,
                    "funding_bps_per_holding_window": 0.0,
                },
                "criteria": {
                    "observation_limit": 96,
                    "holding_observations": 6,
                    "hedge_fraction": 1.0,
                    "hedge_round_trip_cost_bps": 10.0,
                    "min_windows": 20,
                    "min_mean_abs_return_reduction_bps": 0.0,
                    "min_worst_loss_improvement_bps": 0.0,
                    "max_mean_return_drag_bps": 100.0,
                },
                "as_of": None,
            }
        ],
        "pool_inputs": [
            {
                "pool_address": pool,
                "amount_x": 100,
                "amount_y": 100,
                "requested_quote": 100.0,
                "network_cost_y_atomic": 1000,
            }
            for pool in ("pool-a", "pool-b", "pool-c")
        ],
        "portfolio": {
            "account_equity_quote": 1000.0,
            "cash_quote": 1000.0,
            "current_deployed_quote": 0.0,
            "portfolio_drawdown_bps": 0,
            "observation_limit": 12,
            "half_widths": [0, 1, 2],
            "center_offsets": [0],
            "strategies": ["SPOT", "CURVE", "BID_ASK"],
            "max_share_bps": 500,
            "favor_x_in_active_bin": False,
            "budget_quote": 200.0,
            "allocation_criteria": {
                "max_positions": 3,
                "min_positions": 2,
                "max_pool_allocation_bps": 5000,
                "min_range_survival_ratio": 0.75,
                "min_excess_vs_hold_bps": 0,
                "min_position_quote": 10.0,
                "min_budget_utilization_rate": 0.75,
            },
        },
    }


def save_pool(storage, pool, observed_at):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": pool,
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": f"{pool}-x",
            "token_y_mint": f"{pool}-y",
            "bin_arrays": [],
        },
        observed_at=observed_at,
    )


def fake_dataset():
    frame = pd.DataFrame(
        [
            {
                "pool_address": "pool-a",
                "decision_observed_at": "2026-09-23T12:00:00+00:00",
                "forward_end_observed_at": "2026-09-23T13:00:00+00:00",
                "strategy": "SPOT",
                "value": 1,
            }
        ]
    )
    raw = frame.to_csv(
        index=False,
        lineterminator="\n",
        float_format="%.12g",
    ).encode("utf-8")
    import hashlib

    digest = hashlib.sha256(raw).hexdigest()
    return MultiPoolMLDataset(
        report=MultiPoolMLDatasetReport(
            pools_requested=3,
            pools_built=3,
            decision_points=30,
            candidates_seen=90,
            examples_built=1,
            candidates_dropped=89,
            dataset_sha256=digest,
            dataset_version=f"ML_ACTION_DATASET_V1:{digest[:16]}",
            pool_summaries=(),
        ),
        frame=frame,
    )


def test_bandit_dataset_uses_common_pool_cutoff_and_deduplicates(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    artifact = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(explicit_payload()),
    )
    save_pool(storage, "pool-a", "2026-09-23T15:00:00+00:00")
    save_pool(storage, "pool-b", "2026-09-23T14:00:00+00:00")
    save_pool(storage, "pool-c", "2026-09-23T16:00:00+00:00")
    dataset = fake_dataset()
    seen = []

    monkeypatch.setattr(
        bandit_module,
        "_build_dataset",
        lambda storage, *, artifact, cutoff, build_parameters: (
            seen.append(cutoff) or dataset
        ),
    )

    payload, raw = build_phase9_bandit_dataset(
        storage,
        artifact=artifact,
    )
    first = persist_phase9_bandit_dataset(
        storage,
        payload=payload,
        raw_dataset=raw,
    )
    second = persist_phase9_bandit_dataset(
        storage,
        payload=payload,
        raw_dataset=raw,
    )

    assert seen == ["2026-09-23T14:00:00+00:00"]
    assert first.evidence_id == second.evidence_id
    assert first.explicit_input_evidence_id == artifact.evidence_id
    assert (
        first.explicit_input_artifact_sha256
        == artifact.artifact_sha256
    )
    assert first.output_file.startswith(str(tmp_path.resolve()))
    assert first.dataset_version.startswith("ML_ACTION_DATASET_V1:")
    assert first.dataset_sha256 == dataset.report.dataset_sha256


def test_bandit_dataset_load_rebuilds_and_rejects_file_tamper(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    explicit = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(explicit_payload()),
    )
    dataset = fake_dataset()

    monkeypatch.setattr(
        bandit_module,
        "_build_dataset",
        lambda *args, **kwargs: dataset,
    )

    payload, raw = build_phase9_bandit_dataset(
        storage,
        artifact=explicit,
        cutoff="2026-09-23T14:00:00+00:00",
    )
    artifact = persist_phase9_bandit_dataset(
        storage,
        payload=payload,
        raw_dataset=raw,
    )

    loaded, rebuilt = load_phase9_bandit_dataset(
        storage,
        evidence_id=artifact.evidence_id,
    )
    assert loaded.evidence_id == artifact.evidence_id
    assert rebuilt.report.to_record() == dataset.report.to_record()

    from pathlib import Path

    Path(artifact.output_file).write_text(
        "tampered\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="checksum/version mismatch"):
        load_phase9_bandit_dataset(
            storage,
            evidence_id=artifact.evidence_id,
        )


def test_bandit_dataset_research_emits_phase9_lineage(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    explicit = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(explicit_payload()),
    )
    dataset = fake_dataset()
    monkeypatch.setattr(
        bandit_module,
        "_build_dataset",
        lambda *args, **kwargs: dataset,
    )
    payload, raw = build_phase9_bandit_dataset(
        storage,
        artifact=explicit,
        cutoff="2026-09-23T14:00:00+00:00",
    )
    artifact = persist_phase9_bandit_dataset(
        storage,
        payload=payload,
        raw_dataset=raw,
    )

    monkeypatch.setattr(
        bandit_module,
        "bandit_examples_from_records",
        lambda records: ("example",),
    )
    report = SimpleNamespace(
        research_qualified=True,
        to_record=lambda: {
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
        },
    )
    monkeypatch.setattr(
        bandit_module,
        "evaluate_contextual_bandit",
        lambda *args, **kwargs: report,
    )

    result = evaluate_phase9_contextual_bandit_from_dataset(
        storage,
        dataset_evidence_id=artifact.evidence_id,
    )

    assert result.lineage.source_type == (
        PHASE9_BANDIT_DATASET_EVIDENCE_TYPE
    )
    assert result.lineage.dataset_evidence_id == artifact.evidence_id
    assert (
        result.lineage.explicit_input_evidence_id
        == explicit.evidence_id
    )
    assert result.report is report


def test_bandit_dataset_requires_three_explicit_pools(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    payload = explicit_payload()
    payload["pool_inputs"] = payload["pool_inputs"][:2]
    artifact = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(payload),
    )

    with pytest.raises(ValueError, match="at least three"):
        build_phase9_bandit_dataset(
            storage,
            artifact=artifact,
            cutoff="2026-09-23T14:00:00+00:00",
        )
