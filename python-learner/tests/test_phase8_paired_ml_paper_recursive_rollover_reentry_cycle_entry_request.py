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
    / "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _readiness(*, status: str = "READY") -> dict:
    ready = status == "READY"
    return {
        "readiness_sha256": "a" * 64,
        "recursive_rollover_reentry_cycle_input_verification_sha256": "b" * 64,
        "source_final_evaluation_sha256": "c" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "current_pio_database_sha256": "d" * 64,
        "current_pio_wal_sha256": None,
        "current_pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-4",
        "pair_id": "pair-5",
        "pool_address": "pool-5",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "incumbent_position_id": "p8-pair-5-incumbent",
        "challenger_position_id": "p8-pair-5-challenger",
        "incumbent_event_key": "p8-pair-5:incumbent",
        "challenger_event_key": "p8-pair-5:challenger",
        "readiness_status": status,
        "account_ready": ready,
        "reentry_cycle_preconditions_ready": ready,
        "previous_pair_positions_closed": True,
        "zero_open_positions_verified": True,
        "paper_cash_sufficient_for_pair": True,
        "pair_position_ids_available": True,
        "pair_event_keys_available": True,
        "cycle_model_binding_valid": True,
        "paper_pair_entry_authorized": False,
        "paper_pair_entry_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
    }


class _FakeReadiness:
    STATUS_READY = "READY"

    @staticmethod
    def validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_account_readiness(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, readiness: dict | None = None):
    monkeypatch.setattr(
        MODULE,
        "_load_readiness_module",
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


def test_reviewed_reentry_cycle_readiness_is_exactly_pinned():
    path = ROOT / MODULE.ACCOUNT_READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.ACCOUNT_READINESS_TOOL
    ]


def test_request_binds_fresh_reentry_cycle_pair_state(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(
            source_tree=ROOT,
            account_readiness_path=path,
        )
    finally:
        temp.cleanup()

    assert request["source_recursive_rollover_reentry_cycle_account_readiness_sha256"] == "a" * 64
    assert request["recursive_rollover_reentry_cycle_input_verification_sha256"] == "b" * 64
    assert request["source_final_evaluation_sha256"] == "c" * 64
    assert request["authorization_scope"] == (
        "OPEN_ONE_PHASE8_RECURSIVE_REENTRY_CYCLE_PAIRED_ML_PAPER_ENTRY_ONLY"
    )
    assert request["decision"] == (
        "AUTHORIZE_ONE_PHASE8_RECURSIVE_REENTRY_CYCLE_PAIRED_ML_PAPER_ENTRY"
    )
    assert request["pio_database_sha256"] == "d" * 64
    assert request["previous_pair_id"] == "pair-4"
    assert request["pair_id"] == "pair-5"
    assert request["required_pair_cash_quote"] == 2010.0
    assert request["fresh_model_inference_readiness_required"] is True
    assert request["same_candidate_frame_required"] is True
    assert request["same_decision_snapshot_required"] is True
    assert request["equal_capital_required"] is True
    assert request["atomic_pair_open_required"] is True
    assert request["zero_open_positions_required"] is True
    assert request["previous_pair_closed_required"] is True
    assert request["paper_pair_entry_authorization_present"] is False
    assert request["paper_pair_entry_authorized"] is False
    assert request["paper_pair_entry_executed"] is False
    assert request["live_submit_authorized"] is False


def test_request_requires_ready_reentry_cycle_account(monkeypatch):
    temp, path = _build(
        monkeypatch,
        readiness=_readiness(status="OPEN_POSITIONS_PRESENT"),
    )
    try:
        with pytest.raises(ValueError, match="requires READY account state"):
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(
                source_tree=ROOT,
                account_readiness_path=path,
            )
    finally:
        temp.cleanup()


def test_request_refuses_previous_pair_not_closed(monkeypatch):
    readiness = _readiness()
    readiness["previous_pair_positions_closed"] = False
    temp, path = _build(monkeypatch, readiness=readiness)
    try:
        with pytest.raises(
            ValueError,
            match="previous_pair_positions_closed=true",
        ):
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(
                source_tree=ROOT,
                account_readiness_path=path,
            )
    finally:
        temp.cleanup()


def test_resealed_request_cannot_reuse_previous_pair_id(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(
            source_tree=ROOT,
            account_readiness_path=path,
        )
    finally:
        temp.cleanup()

    request["pair_id"] = request["previous_pair_id"]
    _reseal(request)
    with pytest.raises(ValueError, match="pair id was not advanced"):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(request)


def test_resealed_request_cannot_authorize_pair_open(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(
            source_tree=ROOT,
            account_readiness_path=path,
        )
    finally:
        temp.cleanup()

    request["paper_pair_entry_authorized"] = True
    _reseal(request)
    with pytest.raises(
        ValueError,
        match="paper_pair_entry_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(request)


def test_resealed_request_cannot_authorize_promotion(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(
                source_tree=ROOT,
                account_readiness_path=path,
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
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(
            request
        )


def test_resealed_request_rejects_cash_binding_change(monkeypatch):
    temp, path = _build(monkeypatch)
    try:
        request = MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(
            source_tree=ROOT,
            account_readiness_path=path,
        )
    finally:
        temp.cleanup()

    request["required_pair_cash_quote"] = 2000.0
    _reseal(request)
    with pytest.raises(ValueError, match="cash binding mismatch"):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_request(request)


def test_reentry_cycle_request_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paired_ml_paper_entries(" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "promote_continuous_challenger(" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_pair_entry_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
