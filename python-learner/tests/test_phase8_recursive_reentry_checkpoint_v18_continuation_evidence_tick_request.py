from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase8_recursive_reentry_checkpoint_v18_continuation_evidence_tick_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_recursive_reentry_checkpoint_v18_continuation_evidence_tick_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _readiness() -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "source_checkpoint_sha256": "b" * 64,
        "source_post_audit_sha256": "c" * 64,
        "source_execution_receipt_sha256": "d" * 64,
        "source_pair_entry_post_audit_sha256": "e" * 64,
        "pair_entry_request_sha256": "f" * 64,
        "pair_entry_input_verification_sha256": "2" * 64,
        "pair_lineage_sha256": "3" * 64,
        "latest_previous_tick_post_audit_sha256": "4" * 64,
        "source_final_evaluation_sha256": "5" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "1" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-3",
        "pair_id": "pair-4",
        "pool_address": "pool-4",
        "entry_observed_at": "2026-09-30T09:20:00+00:00",
        "previous_tick_observed_at": "2026-09-30T09:25:00+00:00",
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
        "requested_position_ids": [
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ],
        "evaluation_as_of": "2026-09-30T09:31:00+00:00",
        "chain_max_age_seconds": 300,
        "quote_max_age_seconds": 300,
        "latest_chain_observed_at": "2026-09-30T09:30:00+00:00",
        "chain_newer_than_previous_tick": True,
        "chain_fresh": True,
        "required_quote_mints": ["y-mint"],
        "quote_statuses": [
            {
                "token_mint": "y-mint",
                "fresh": True,
                "quote_per_atomic": 0.000001,
            }
        ],
        "fresh_quote_map": {"y-mint": 0.000001},
        "quotes_ready": True,
        "pair_positions_open": True,
        "pair_counterfactual_bindings_present": True,
        "cycle_model_binding_valid": True,
        "readiness_status": "READY",
        "continuation_tick_request_ready": True,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class _FakeReadiness:
    STATUS_READY = "READY"

    @staticmethod
    def validate_phase8_recursive_reentry_checkpoint_v18_continuation_supervision_readiness(
        value,
    ):
        assert isinstance(value, dict)


def _build(monkeypatch, *, mutator=None):
    value = _readiness()
    if mutator:
        mutator(value)
    temp = tempfile.TemporaryDirectory()
    path = _write(Path(temp.name) / "readiness.json", value)
    monkeypatch.setattr(
        MODULE,
        "_load_readiness",
        lambda source: _FakeReadiness,
    )

    def run():
        return MODULE.build_phase8_recursive_reentry_checkpoint_v18_continuation_evidence_tick_request(
            source_tree=ROOT,
            continuation_readiness_path=path,
        )

    return temp, run


def _reseal(request: dict) -> None:
    identity = {
        field: request[field]
        for field in MODULE.REQUEST_FIELDS
    }
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_checkpoint_v18_continuation_readiness_is_exactly_pinned():
    path = ROOT / MODULE.READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.READINESS_TOOL
    ]


def test_request_binds_new_target_and_fixed_policy(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        request = run()
    finally:
        temp.cleanup()

    assert request["source_continuation_supervision_readiness_sha256"] == (
        "a" * 64
    )
    assert request["source_checkpoint_sha256"] == "b" * 64
    assert request["source_post_audit_sha256"] == "c" * 64
    assert request["source_execution_receipt_sha256"] == "d" * 64
    assert request["source_pair_entry_post_audit_sha256"] == "e" * 64
    assert request["pair_entry_request_sha256"] == "f" * 64
    assert request["pair_entry_input_verification_sha256"] == "2" * 64
    assert request["pair_lineage_sha256"] == "3" * 64
    assert request["latest_previous_tick_post_audit_sha256"] == "4" * 64
    assert request["source_final_evaluation_sha256"] == "5" * 64
    assert request["previous_pair_id"] == "pair-3"
    assert request["pair_id"] == "pair-4"
    assert request["previous_tick_observed_at"] == (
        "2026-09-30T09:25:00+00:00"
    )
    assert request["target_chain_observed_at"] == (
        "2026-09-30T09:30:00+00:00"
    )
    assert request["requested_position_ids"] == [
        "p8-pair-4-incumbent",
        "p8-pair-4-challenger",
    ]
    assert request["pool_safety_config"] == MODULE.SAFETY_CONFIG
    assert request["position_management_config"] == MODULE.MANAGEMENT_CONFIG
    assert request["retry_failed"] is False
    assert request["emergency_exit"] is False
    assert request["estimated_exit_cost_quote"] == 0.0
    assert request["rebalance_cost_quote"] is None
    assert request["request_ready"] is True
    assert request["explicit_human_authorization_required"] is True
    assert request["paper_supervisor_tick_authorized"] is False
    assert request["paper_supervisor_tick_executed"] is False
    assert request["paper_evidence_collection_authorized"] is False
    assert request["paper_trading_authorized"] is False
    assert request["live_submit_authorized"] is False
    assert request["continuous_promotion_authorized"] is False
    assert request["phase8_promotion_authorized"] is False
    expected_suffix = hashlib.sha256(
        request["target_chain_observed_at"].encode("utf-8")
    ).hexdigest()[:12]
    assert request["evidence_cycle_id"] == (
        f"phase8-recursive-reentry-checkpoint-v15-continuation:pair-4:{expected_suffix}"
    )


def test_checkpoint_pair_id_cannot_regress(monkeypatch):
    temp, run = _build(
        monkeypatch,
        mutator=lambda value: value.update(pair_id="pair-3"),
    )
    try:
        with pytest.raises(ValueError, match="pair id was not advanced"):
            run()
    finally:
        temp.cleanup()


def test_non_ready_continuation_cannot_request_tick(monkeypatch):
    temp, run = _build(
        monkeypatch,
        mutator=lambda value: value.update(
            readiness_status="WAITING_NEW_CHAIN",
            continuation_tick_request_ready=False,
        ),
    )
    try:
        with pytest.raises(ValueError, match="not READY"):
            run()
    finally:
        temp.cleanup()


def test_stale_quote_gate_cannot_request_tick(monkeypatch):
    temp, run = _build(
        monkeypatch,
        mutator=lambda value: value.update(quotes_ready=False),
    )
    try:
        with pytest.raises(
            ValueError,
            match="requires quotes_ready=true",
        ):
            run()
    finally:
        temp.cleanup()


def test_resealed_request_cannot_authorize_tick(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        request = run()
    finally:
        temp.cleanup()

    request["paper_supervisor_tick_authorized"] = True
    _reseal(request)
    with pytest.raises(
        ValueError,
        match="paper_supervisor_tick_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v18_continuation_evidence_tick_request(
            request
        )


def test_resealed_request_cannot_authorize_promotion(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        request = run()
    finally:
        temp.cleanup()

    request["continuous_promotion_authorized"] = True
    _reseal(request)
    with pytest.raises(
        ValueError,
        match="continuous_promotion_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v18_continuation_evidence_tick_request(
            request
        )


def test_resealed_request_cannot_weaken_safety_config(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        request = run()
    finally:
        temp.cleanup()

    request["pool_safety_config"]["min_tvl_usd"] = 0.0
    _reseal(request)
    with pytest.raises(ValueError, match="safety config changed"):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v18_continuation_evidence_tick_request(
            request
        )


def test_request_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
