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
    / "build_phase8_paired_ml_paper_evidence_tick_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paired_ml_paper_evidence_tick_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _readiness() -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "source_post_audit_sha256": "b" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "c" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-1",
        "pool_address": "pool-1",
        "entry_decision_observed_at": "2026-09-30T00:40:00+00:00",
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "requested_position_ids": [
            "pair-1-incumbent",
            "pair-1-challenger",
        ],
        "evaluation_as_of": "2026-09-30T00:45:00+00:00",
        "chain_max_age_seconds": 300,
        "quote_max_age_seconds": 300,
        "latest_chain_observed_at": "2026-09-30T00:44:00+00:00",
        "latest_chain_age_seconds": 60,
        "chain_newer_than_entry": True,
        "chain_fresh": True,
        "required_quote_mints": ["y-mint"],
        "quote_statuses": [
            {
                "token_mint": "y-mint",
                "available": True,
                "fresh": True,
                "quote_per_atomic": 0.0001,
                "source": "fixture",
                "observed_at": "2026-09-30T00:44:30+00:00",
                "age_seconds": 30,
                "reason": None,
            }
        ],
        "fresh_quote_map": {"y-mint": 0.0001},
        "quotes_ready": True,
        "pair_positions_open": True,
        "pair_counterfactual_bindings_present": True,
        "readiness_status": "READY",
        "one_pair_evidence_tick_request_ready": True,
    }


class _FakeReadiness:
    STATUS_READY = "READY"

    @staticmethod
    def validate_phase8_paired_ml_paper_supervision_readiness(value):
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
    path = _write(
        Path(temp.name) / "readiness.json",
        copy.deepcopy(readiness if readiness is not None else _readiness()),
    )
    result = MODULE.build_phase8_paired_ml_paper_evidence_tick_request(
        source_tree=ROOT,
        supervision_readiness_path=path,
    )
    return temp, result


def _reseal(request: dict) -> None:
    identity = {
        field: request[field]
        for field in MODULE.REQUEST_FIELDS
    }
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_readiness_is_exactly_pinned():
    path = ROOT / MODULE.READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.READINESS_TOOL
    ]


def test_request_binds_exact_pair_snapshot_quotes_and_policy(monkeypatch):
    temp, request = _build(monkeypatch)
    try:
        assert request["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
        assert request["decision"] == MODULE.DECISION
        assert request["active_cycle_id"] == "cycle-1"
        assert request["requested_position_ids"] == [
            "pair-1-incumbent",
            "pair-1-challenger",
        ]
        assert request["target_chain_observed_at"] == (
            "2026-09-30T00:44:00+00:00"
        )
        assert request["fresh_quote_map"] == {"y-mint": 0.0001}
        assert request["pool_safety_config"] == MODULE.SAFETY_CONFIG
        assert request["position_management_config"] == (
            MODULE.MANAGEMENT_CONFIG
        )
        assert request["retry_failed"] is False
        assert request["emergency_exit"] is False
        assert request["estimated_exit_cost_quote"] == 0.0
        assert request["rebalance_cost_quote"] is None
        assert request["request_ready"] is True
        assert request["explicit_human_authorization_required"] is True
        assert request["fresh_supervision_readiness_recheck_required"] is True
        assert request["exact_chain_snapshot_required"] is True
        assert request["exact_quote_map_required"] is True
        assert request["explicit_position_scope_required"] is True
        assert request["derived_pool_safety_required"] is True
        assert request["idempotent_pair_run_required"] is True
        assert request["post_tick_audit_required"] is True
        assert request["paper_supervisor_tick_authorized"] is False
        assert request["paper_supervisor_tick_executed"] is False
        assert request["paper_evidence_collection_authorized"] is False
        assert request["paper_trading_authorized"] is False
        assert request["live_submit_authorized"] is False
        assert request["phase8_promotion_authorized"] is False
    finally:
        temp.cleanup()


def test_non_ready_supervision_cannot_build_request(monkeypatch):
    readiness = _readiness()
    readiness["readiness_status"] = "WAITING_QUOTES"
    readiness["one_pair_evidence_tick_request_ready"] = False

    monkeypatch.setattr(
        MODULE,
        "_load_readiness",
        lambda source: _FakeReadiness,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "readiness.json", readiness)
        with pytest.raises(ValueError, match="not READY"):
            MODULE.build_phase8_paired_ml_paper_evidence_tick_request(
                source_tree=ROOT,
                supervision_readiness_path=path,
            )


def test_resealed_request_cannot_expand_position_scope(monkeypatch):
    temp, request = _build(monkeypatch)
    try:
        request["requested_position_ids"] = [
            "pair-1-incumbent",
            "pair-1-challenger",
            "other-position",
        ]
        _reseal(request)
        with pytest.raises(ValueError, match="position scope mismatch"):
            MODULE.validate_phase8_paired_ml_paper_evidence_tick_request(
                request
            )
    finally:
        temp.cleanup()


def test_resealed_request_cannot_drop_quote_coverage(monkeypatch):
    readiness = _readiness()
    readiness["required_quote_mints"] = ["reward-mint", "y-mint"]
    readiness["fresh_quote_map"] = {
        "reward-mint": 0.0002,
        "y-mint": 0.0001,
    }
    readiness["quote_statuses"].append(
        {
            "token_mint": "reward-mint",
            "available": True,
            "fresh": True,
            "quote_per_atomic": 0.0002,
            "source": "fixture",
            "observed_at": "2026-09-30T00:44:30+00:00",
            "age_seconds": 30,
            "reason": None,
        }
    )
    temp, request = _build(monkeypatch, readiness=readiness)
    try:
        request["fresh_quote_map"] = {"y-mint": 0.0001}
        _reseal(request)
        with pytest.raises(ValueError, match="quote coverage mismatch"):
            MODULE.validate_phase8_paired_ml_paper_evidence_tick_request(
                request
            )
    finally:
        temp.cleanup()


def test_resealed_request_cannot_weaken_management_policy(monkeypatch):
    temp, request = _build(monkeypatch)
    try:
        policy = dict(request["position_management_config"])
        policy["stop_loss_bps"] = 5000
        request["position_management_config"] = policy
        _reseal(request)
        with pytest.raises(ValueError, match="management config mismatch"):
            MODULE.validate_phase8_paired_ml_paper_evidence_tick_request(
                request
            )
    finally:
        temp.cleanup()


def test_resealed_request_cannot_self_authorize_tick(monkeypatch):
    temp, request = _build(monkeypatch)
    try:
        request["paper_supervisor_tick_authorized"] = True
        _reseal(request)
        with pytest.raises(
            ValueError,
            match="paper_supervisor_tick_authorized=false",
        ):
            MODULE.validate_phase8_paired_ml_paper_evidence_tick_request(
                request
            )
    finally:
        temp.cleanup()


def test_resealed_request_cannot_authorize_live_submit(monkeypatch):
    temp, request = _build(monkeypatch)
    try:
        request["live_submit_authorized"] = True
        _reseal(request)
        with pytest.raises(
            ValueError,
            match="live_submit_authorized=false",
        ):
            MODULE.validate_phase8_paired_ml_paper_evidence_tick_request(
                request
            )
    finally:
        temp.cleanup()


def test_request_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_supervisor_tick_executed": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
