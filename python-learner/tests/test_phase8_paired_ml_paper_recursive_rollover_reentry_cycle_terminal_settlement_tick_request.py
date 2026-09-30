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
    / "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _readiness(*, status: str = "READY") -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "source_terminal_evaluation_sha256": "b" * 64,
        "source_terminal_post_audit_sha256": "c" * 64,
        "source_recursive_rollover_reentry_cycle_post_audit_sha256": "d" * 64,
        "recursive_rollover_reentry_cycle_entry_request_sha256": "e" * 64,
        "recursive_rollover_reentry_cycle_input_verification_sha256": "1" * 64,
        "source_final_evaluation_sha256": "2" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "f" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-1",
        "pair_id": "pair-2",
        "pool_address": "pool-2",
        "entry_observed_at": "2026-09-30T09:20:00+00:00",
        "terminal_tick_observed_at": "2026-09-30T09:30:00+00:00",
        "incumbent_position_id": "p8-pair-2-incumbent",
        "challenger_position_id": "p8-pair-2-challenger",
        "open_position_id": "p8-pair-2-challenger",
        "closed_position_id": "p8-pair-2-incumbent",
        "open_position_policy_source": "ML_CHALLENGER",
        "open_position_model_id": "challenger-1",
        "evaluation_as_of": "2026-09-30T09:36:00+00:00",
        "chain_max_age_seconds": 300,
        "quote_max_age_seconds": 300,
        "latest_chain_observed_at": "2026-09-30T09:35:00+00:00",
        "required_quote_mints": ["y-mint"],
        "quote_statuses": [
            {
                "token_mint": "y-mint",
                "available": True,
                "fresh": True,
                "quote_per_atomic": 0.01,
                "source": "fixture",
                "observed_at": "2026-09-30T09:35:00+00:00",
                "age_seconds": 60,
                "reason": None,
            }
        ],
        "fresh_quote_map": {"y-mint": 0.01},
        "readiness_status": status,
        "settlement_tick_request_ready": status == "READY",
        "chain_newer_than_terminal_tick": True,
        "chain_fresh": True,
        "quotes_ready": True,
        "open_position_still_open": True,
        "closed_position_still_closed": True,
        "open_counterfactual_binding_present": True,
        "cycle_model_binding_valid": True,
    }


class _FakeReadiness:
    STATUS_READY = "READY"

    @staticmethod
    def validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_readiness(value):
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


def test_reviewed_settlement_readiness_is_exactly_pinned():
    path = ROOT / MODULE.READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.READINESS_TOOL
    ]


def test_request_binds_exact_single_open_leg(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
                source_tree=ROOT,
                settlement_readiness_path=path,
            )
        )
    finally:
        temp.cleanup()

    assert request["requested_position_ids"] == ["p8-pair-2-challenger"]
    assert request["previous_pair_id"] == "pair-1"
    assert request["pair_id"] == "pair-2"
    assert request["source_recursive_rollover_reentry_cycle_post_audit_sha256"] == "d" * 64
    assert request["recursive_rollover_reentry_cycle_entry_request_sha256"] == "e" * 64
    assert request["recursive_rollover_reentry_cycle_input_verification_sha256"] == "1" * 64
    assert request["source_final_evaluation_sha256"] == "2" * 64
    assert request["entry_observed_at"] == "2026-09-30T09:20:00+00:00"
    assert request["open_position_policy_source"] == "ML_CHALLENGER"
    assert request["open_position_model_id"] == "challenger-1"
    assert request["target_chain_observed_at"] == (
        "2026-09-30T09:35:00+00:00"
    )
    assert request["settlement_cycle_id"].startswith("phase8-recursive-reentry-cycle-settle:pair-2:")
    assert request["pool_safety_config"] == MODULE.SAFETY_CONFIG
    assert request["position_management_config"] == MODULE.MANAGEMENT_CONFIG
    assert request["emergency_exit"] is False
    assert request["retry_failed"] is False
    assert request["paper_settlement_tick_authorization_present"] is False
    assert request["paper_settlement_tick_authorized"] is False
    assert request["paper_settlement_tick_executed"] is False
    assert request["paper_trading_authorized"] is False
    assert request["live_submit_authorized"] is False


def test_non_ready_settlement_cannot_request_tick(monkeypatch):
    temp, path = _build(
        monkeypatch,
        readiness=_readiness(status="WAITING_NEW_CHAIN"),
    )
    try:
        with pytest.raises(ValueError, match="not READY"):
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
                source_tree=ROOT,
                settlement_readiness_path=path,
            )
    finally:
        temp.cleanup()


def test_reentry_pair_id_must_be_advanced(monkeypatch):
    readiness = _readiness()
    readiness["pair_id"] = readiness["previous_pair_id"]
    temp, path = _build(monkeypatch, readiness=readiness)
    try:
        with pytest.raises(ValueError, match="pair id was not advanced"):
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
                source_tree=ROOT,
                settlement_readiness_path=path,
            )
    finally:
        temp.cleanup()


def test_resealed_request_cannot_expand_position_scope(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
                source_tree=ROOT,
                settlement_readiness_path=path,
            )
        )
    finally:
        temp.cleanup()

    request["requested_position_ids"] = [
        "p8-pair-2-incumbent",
        "p8-pair-2-challenger",
    ]
    _reseal(request)
    with pytest.raises(ValueError, match="position scope mismatch"):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
            request
        )


def test_resealed_request_cannot_weaken_management_config(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
                source_tree=ROOT,
                settlement_readiness_path=path,
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
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
            request
        )


def test_resealed_request_cannot_self_authorize_tick(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
                source_tree=ROOT,
                settlement_readiness_path=path,
            )
        )
    finally:
        temp.cleanup()

    request["paper_settlement_tick_authorized"] = True
    _reseal(request)
    with pytest.raises(
        ValueError,
        match="paper_settlement_tick_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
            request
        )


def test_resealed_request_cannot_authorize_promotion(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
                source_tree=ROOT,
                settlement_readiness_path=path,
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
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_terminal_settlement_tick_request(
            request
        )


def test_settlement_request_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "apply_paper_chain_valuation(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "send_transaction" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_settlement_tick_authorized": False' in source
    assert '"paper_settlement_tick_executed": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
