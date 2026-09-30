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
    / "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _readiness(*, status: str = "READY") -> dict:
    ready = status == "READY"
    return {
        "readiness_sha256": "a" * 64,
        "source_recursive_rollover_reentry_cycle_post_audit_sha256": "b" * 64,
        "recursive_rollover_reentry_cycle_entry_request_sha256": "c" * 64,
        "recursive_rollover_reentry_cycle_input_verification_sha256": "d" * 64,
        "source_final_evaluation_sha256": "e" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "f" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-3",
        "pair_id": "pair-4",
        "pool_address": "pool-4",
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
        "requested_position_ids": [
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ],
        "entry_observed_at": "2026-09-30T10:10:00+00:00",
        "evaluation_as_of": "2026-09-30T10:16:00+00:00",
        "chain_max_age_seconds": 300,
        "quote_max_age_seconds": 300,
        "latest_chain_observed_at": "2026-09-30T10:15:00+00:00",
        "required_quote_mints": ["y-mint"],
        "fresh_quote_map": {"y-mint": 0.01},
        "quote_statuses": [
            {
                "token_mint": "y-mint",
                "available": True,
                "fresh": True,
                "quote_per_atomic": 0.01,
                "source": "fixture",
                "observed_at": "2026-09-30T10:15:00+00:00",
                "age_seconds": 60,
                "reason": None,
            }
        ],
        "readiness_status": status,
        "recursive_rollover_reentry_cycle_evidence_tick_request_ready": ready,
        "chain_newer_than_entry": True,
        "chain_fresh": True,
        "quotes_ready": True,
        "pair_positions_open": True,
        "pair_counterfactual_bindings_present": True,
        "paper_ledger_audit_passing": True,
        "cycle_model_binding_valid": True,
    }


class _FakeReadiness:
    STATUS_READY = "READY"

    @staticmethod
    def validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_supervision_readiness(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, readiness: dict | None = None):
    monkeypatch.setattr(
        MODULE,
        "_load_readiness",
        lambda source: _FakeReadiness,
    )
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    path = _write(
        root / "readiness.json",
        copy.deepcopy(readiness if readiness is not None else _readiness()),
    )
    return temp, path


def _reseal(request: dict) -> None:
    identity = {
        field: request[field]
        for field in MODULE.REQUEST_FIELDS
    }
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_recursive_reentry_cycle_supervision_is_exactly_pinned():
    path = ROOT / MODULE.READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.READINESS_TOOL
    ]


def test_request_binds_exact_recursive_reentry_cycle_pair_scope(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
                source_tree=ROOT,
                supervision_readiness_path=path,
            )
        )
    finally:
        temp.cleanup()

    assert request["previous_pair_id"] == "pair-3"
    assert request["pair_id"] == "pair-4"
    assert request["source_recursive_rollover_reentry_cycle_post_audit_sha256"] == "b" * 64
    assert request["recursive_rollover_reentry_cycle_entry_request_sha256"] == "c" * 64
    assert request["recursive_rollover_reentry_cycle_input_verification_sha256"] == "d" * 64
    assert request["source_final_evaluation_sha256"] == "e" * 64
    assert request["requested_position_ids"] == [
        "p8-pair-4-incumbent",
        "p8-pair-4-challenger",
    ]
    assert request["target_chain_observed_at"] == (
        "2026-09-30T10:15:00+00:00"
    )
    assert request["evidence_cycle_id"].startswith(
        "phase8-recursive-reentry-cycle-evidence:pair-4:"
    )
    assert request["pool_safety_config"] == MODULE.SAFETY_CONFIG
    assert request["position_management_config"] == MODULE.MANAGEMENT_CONFIG
    assert request["retry_failed"] is False
    assert request["emergency_exit"] is False
    assert request["paper_supervisor_tick_authorization_present"] is False
    assert request["paper_supervisor_tick_authorized"] is False
    assert request["paper_supervisor_tick_executed"] is False
    assert request["paper_trading_authorized"] is False
    assert request["live_submit_authorized"] is False


def test_non_ready_supervision_cannot_request_tick(monkeypatch):
    temp, path = _build(
        monkeypatch,
        readiness=_readiness(status="WAITING_NEW_CHAIN"),
    )
    try:
        with pytest.raises(ValueError, match="requires READY supervision"):
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
                source_tree=ROOT,
                supervision_readiness_path=path,
            )
    finally:
        temp.cleanup()


def test_resealed_request_cannot_expand_position_scope(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
                source_tree=ROOT,
                supervision_readiness_path=path,
            )
        )
    finally:
        temp.cleanup()

    request["requested_position_ids"] = [
        "p8-pair-4-incumbent",
    ]
    _reseal(request)
    with pytest.raises(ValueError, match="position scope mismatch"):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
            request
        )


def test_resealed_request_cannot_weaken_management(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
                source_tree=ROOT,
                supervision_readiness_path=path,
            )
        )
    finally:
        temp.cleanup()

    request["position_management_config"] = {
        **request["position_management_config"],
        "stop_loss_bps": 10_000,
    }
    _reseal(request)
    with pytest.raises(ValueError, match="management config changed"):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
            request
        )


def test_resealed_request_cannot_self_authorize_tick(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
                source_tree=ROOT,
                supervision_readiness_path=path,
            )
        )
    finally:
        temp.cleanup()

    request["paper_supervisor_tick_authorized"] = True
    _reseal(request)
    with pytest.raises(
        ValueError,
        match="paper_supervisor_tick_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
            request
        )


def test_resealed_request_cannot_authorize_promotion(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
                source_tree=ROOT,
                supervision_readiness_path=path,
            )
        )
    finally:
        temp.cleanup()

    request["continuous_promotion_authorized"] = True
    _reseal(request)
    with pytest.raises(
        ValueError,
        match="continuous_promotion_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_evidence_tick_request(
            request
        )


def test_recursive_reentry_cycle_tick_request_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "send_transaction" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
