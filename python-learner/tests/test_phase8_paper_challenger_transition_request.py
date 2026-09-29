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
    / "build_phase8_paper_challenger_transition_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paper_challenger_transition_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POST_AUDIT_SHA = "a" * 64
MODEL_ID = "challenger-1"
CYCLE_ID = "cycle-1"
REASON = (
    "offline-qualified cycle challenger must be moved to "
    "PAPER_CHALLENGER before continuous promotion validation"
)
COMMAND = f"pio ml-start-paper --model-id {MODEL_ID}"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _artifacts() -> list[dict]:
    return [
        {
            "path": "phase8_ml_artifacts/cycle-1/model.json",
            "size_bytes": 20,
            "sha256": "1" * 64,
        }
    ]


def _audit(production: Path, database: Path) -> dict:
    return {
        "post_audit_sha256": POST_AUDIT_SHA,
        "execution_receipt_sha256": "b" * 64,
        "continuation_request_sha256": "c" * 64,
        "signed_authorization_verification_sha256": "d" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "audit_database_sha256_after": _sha(database.read_bytes()),
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_after": None,
        "audit_research_artifacts_after": _artifacts(),
        "continuation_route": "PHASE8_PAPER_CHALLENGER_REVIEW",
        "post_step_audit_ready": True,
        "requires_separate_phase8_action": True,
        "requires_new_human_authorization": True,
        "paper_challenger_review_required": True,
        "phase7_dependency_satisfied": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "next_debt_type": "PAPER_CHALLENGER_START_REQUIRED",
        "next_scope": MODEL_ID,
        "fresh_phase8_evidence_status": {
            "phase7_promoted": True,
            "active_cycle_id": CYCLE_ID,
            "active_cycle_status": "OFFLINE_QUALIFIED",
            "active_cycle_challenger_model_id": MODEL_ID,
            "active_cycle_challenger_status": "OFFLINE_QUALIFIED",
        },
        "fresh_phase8_operator_handoff": {
            "status": "MANUAL_REQUIRED",
            "automatic_action_available": False,
            "operator_action_required": True,
            "manual_input_required": False,
            "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
            "scope": MODEL_ID,
            "reason": REASON,
            "suggested_command": COMMAND,
        },
        "paper_challenger_transition_authorized": False,
        "paper_trading_authorized": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_policy_action_authorized": False,
        "phase8_execution_authorized": False,
        "phase8_promotion_authorized": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    audit_override: dict | None = None,
    current_state_override: dict | None = None,
    artifacts_override: list[dict] | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        database = data / "pio.db"
        database.write_bytes(b"phase8-paper-ready")

        audit = _audit(production, database)
        if audit_override:
            audit.update(audit_override)
        path = _write(root / "audit.json", audit)

        current_state = {
            "database": _sha(database.read_bytes()),
            "wal": None,
            "shm": None,
        }
        if current_state_override:
            current_state.update(current_state_override)

        class FakeExecutor:
            @staticmethod
            def _research_artifacts(data_root):
                return copy.deepcopy(
                    artifacts_override
                    if artifacts_override is not None
                    else _artifacts()
                )

        class FakeAudit:
            ROUTE_PAPER_REVIEW = "PHASE8_PAPER_CHALLENGER_REVIEW"

            @staticmethod
            def validate_phase8_offline_continuation_post_audit(value):
                assert isinstance(value, dict)

            @staticmethod
            def _database_state(database_path):
                return copy.deepcopy(current_state)

            @staticmethod
            def _load_reviewed(source):
                return FakeExecutor, {}

        monkeypatch.setattr(
            MODULE,
            "_load_audit_module",
            lambda source: FakeAudit,
        )

        return MODULE.build_phase8_paper_challenger_transition_request(
            repository=production,
            source_tree=ROOT,
            post_audit_path=path,
            expected_post_audit_sha256=POST_AUDIT_SHA,
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_post_audit_tool_is_exactly_pinned():
    path = ROOT / MODULE.POST_AUDIT_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.POST_AUDIT_TOOL
    ]


def test_paper_review_builds_non_authorizing_transition_request(
    monkeypatch,
):
    request = _build(monkeypatch)

    assert request["source_post_audit_sha256"] == POST_AUDIT_SHA
    assert request["source_execution_receipt_sha256"] == "b" * 64
    assert request["source_continuation_request_sha256"] == "c" * 64
    assert (
        request["source_signed_authorization_verification_sha256"]
        == "d" * 64
    )
    assert request["active_cycle_id"] == CYCLE_ID
    assert request["model_id"] == MODEL_ID
    assert request["scope"] == MODEL_ID
    assert request["active_cycle_status"] == "OFFLINE_QUALIFIED"
    assert request["challenger_model_status"] == "OFFLINE_QUALIFIED"
    assert request["reason"] == REASON
    assert request["suggested_command"] == COMMAND
    assert request["research_artifact_count"] == 1
    assert request["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert request["request_ready"] is True
    assert request["explicit_human_authorization_required"] is True
    assert request["fresh_post_audit_recheck_required"] is True
    assert request["exact_cycle_model_binding_required"] is True
    assert request["cycle_sync_required_after_model_transition"] is True
    assert (
        request["paper_challenger_transition_authorization_present"]
        is False
    )
    assert request["paper_challenger_transition_authorized"] is False
    assert request["paper_challenger_transition_executed"] is False
    assert request["paper_evidence_collection_authorized"] is False
    assert request["paper_trading_authorized"] is False
    assert request["live_submit_authorized"] is False
    assert request["new_live_capital_authorized"] is False
    assert request["phase8_execution_authorized"] is False
    assert request["phase8_promotion_authorized"] is False


def test_non_paper_continuation_route_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="not routed"):
        _build(
            monkeypatch,
            audit_override={
                "continuation_route": (
                    "PHASE8_NEXT_OFFLINE_STEP_REAUTHORIZATION"
                )
            },
        )


def test_wrong_debt_type_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="debt type mismatch"):
        _build(
            monkeypatch,
            audit_override={
                "next_debt_type": "RETRAIN_OFFLINE_VALIDATION_READY"
            },
        )


def test_cycle_model_binding_must_match(monkeypatch):
    status = copy.deepcopy(
        _audit(Path("/tmp/prod"), Path("/tmp/prod/data/pio.db"))[
            "fresh_phase8_evidence_status"
        ]
    )
    status["active_cycle_challenger_model_id"] = "challenger-2"
    with pytest.raises(ValueError, match="cycle/model mismatch"):
        _build(
            monkeypatch,
            audit_override={"fresh_phase8_evidence_status": status},
        )


def test_cycle_must_still_be_offline_qualified(monkeypatch):
    status = {
        "phase7_promoted": True,
        "active_cycle_id": CYCLE_ID,
        "active_cycle_status": "PAPER_CHALLENGER",
        "active_cycle_challenger_model_id": MODEL_ID,
        "active_cycle_challenger_status": "OFFLINE_QUALIFIED",
    }
    with pytest.raises(ValueError, match="cycle is not offline-qualified"):
        _build(
            monkeypatch,
            audit_override={"fresh_phase8_evidence_status": status},
        )


def test_model_must_still_be_offline_qualified(monkeypatch):
    status = {
        "phase7_promoted": True,
        "active_cycle_id": CYCLE_ID,
        "active_cycle_status": "OFFLINE_QUALIFIED",
        "active_cycle_challenger_model_id": MODEL_ID,
        "active_cycle_challenger_status": "PAPER_CHALLENGER",
    }
    with pytest.raises(ValueError, match="model is not offline-qualified"):
        _build(
            monkeypatch,
            audit_override={"fresh_phase8_evidence_status": status},
        )


def test_operator_command_must_be_exact(monkeypatch):
    operator = {
        "status": "MANUAL_REQUIRED",
        "automatic_action_available": False,
        "operator_action_required": True,
        "manual_input_required": False,
        "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
        "scope": MODEL_ID,
        "reason": REASON,
        "suggested_command": "pio ml-start-paper --model-id challenger-2",
    }
    with pytest.raises(ValueError, match="suggested command mismatch"):
        _build(
            monkeypatch,
            audit_override={"fresh_phase8_operator_handoff": operator},
        )


def test_database_drift_after_post_audit_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="database changed"):
        _build(
            monkeypatch,
            current_state_override={"database": "f" * 64},
        )


def test_research_artifact_drift_after_post_audit_fails_closed(
    monkeypatch,
):
    with pytest.raises(ValueError, match="artifacts changed"):
        _build(
            monkeypatch,
            artifacts_override=[
                {
                    "path": "phase8_ml_artifacts/cycle-1/model.json",
                    "size_bytes": 21,
                    "sha256": "2" * 64,
                }
            ],
        )


def test_resealed_request_cannot_authorize_transition(monkeypatch):
    request = _build(monkeypatch)
    request["paper_challenger_transition_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="paper_challenger_transition_authorized=false",
    ):
        MODULE.validate_phase8_paper_challenger_transition_request(
            request
        )


def test_resealed_request_cannot_authorize_paper_trading(monkeypatch):
    request = _build(monkeypatch)
    request["paper_trading_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_paper_challenger_transition_request(
            request
        )


def test_resealed_request_cannot_authorize_live_submit(monkeypatch):
    request = _build(monkeypatch)
    request["live_submit_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_phase8_paper_challenger_transition_request(
            request
        )


def test_paper_transition_request_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "start_paper_challenger(" not in source
    assert "start_model_paper_challenger(" not in source
    assert "sync_retraining_cycle(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_challenger_transition_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
