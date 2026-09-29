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
    / "build_phase8_offline_step_continuation_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_offline_step_continuation_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POST_AUDIT_SHA = "a" * 64
DEBT_TYPE = "RETRAIN_OFFLINE_VALIDATION_READY"
SCOPE = "cycle-1"
REASON = "offline validation is ready"
COMMAND = "pio phase8-retrain-offline-validate-run --cycle-id cycle-1"


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
        "offline_step_request_sha256": "c" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "audit_database_sha256_after": _sha(database.read_bytes()),
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_after": None,
        "audit_research_artifacts_after": _artifacts(),
        "continuation_route": (
            "PHASE8_NEXT_OFFLINE_STEP_REAUTHORIZATION"
        ),
        "post_step_audit_ready": True,
        "requires_separate_phase8_action": True,
        "requires_new_human_authorization": True,
        "next_offline_step_reauthorization_required": True,
        "phase7_dependency_satisfied": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "next_debt_type": DEBT_TYPE,
        "next_scope": SCOPE,
        "fresh_phase8_operator_handoff": {
            "status": "AUTOMATIC_ACTION",
            "automatic_action_available": True,
            "operator_action_required": False,
            "manual_input_required": False,
            "debt_type": DEBT_TYPE,
            "scope": SCOPE,
            "reason": REASON,
            "suggested_command": COMMAND,
        },
        "paper_challenger_transition_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "new_live_capital_authorized": False,
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
        database.write_bytes(b"phase8-current")

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
            ROUTE_NEXT_OFFLINE = (
                "PHASE8_NEXT_OFFLINE_STEP_REAUTHORIZATION"
            )

            @staticmethod
            def validate_phase8_offline_step_post_audit(value):
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

        return MODULE.build_phase8_offline_continuation_request(
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


def test_clean_audit_builds_new_one_step_request(monkeypatch):
    request = _build(monkeypatch)

    assert request["source_post_audit_sha256"] == POST_AUDIT_SHA
    assert request["source_execution_receipt_sha256"] == "b" * 64
    assert request["source_offline_step_request_sha256"] == "c" * 64
    assert request["debt_type"] == DEBT_TYPE
    assert request["scope"] == SCOPE
    assert request["reason"] == REASON
    assert request["suggested_command"] == COMMAND
    assert request["research_artifact_count"] == 1
    assert request["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert request["one_step_only"] is True
    assert request["request_ready"] is True
    assert request["explicit_human_authorization_required"] is True
    assert request["fresh_post_audit_recheck_required"] is True
    assert request["offline_step_authorization_present"] is False
    assert request["offline_step_execution_authorized"] is False
    assert request["offline_step_executed"] is False
    assert request["paper_challenger_transition_authorized"] is False
    assert request["paper_trading_authorized"] is False
    assert request["live_submit_authorized"] is False
    assert request["new_live_capital_authorized"] is False
    assert request["phase8_execution_authorized"] is False
    assert request["phase8_promotion_authorized"] is False
    assert request["production_pio_database_modified"] is False
    assert request["production_research_artifacts_modified"] is False


def test_non_offline_continuation_route_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="not routed"):
        _build(
            monkeypatch,
            audit_override={
                "continuation_route": "PHASE8_PAPER_CHALLENGER_REVIEW"
            },
        )


def test_non_allowed_debt_type_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="debt type is not allowed"):
        _build(
            monkeypatch,
            audit_override={
                "next_debt_type": "PAPER_CHALLENGER_START_REQUIRED",
                "fresh_phase8_operator_handoff": {
                    "status": "AUTOMATIC_ACTION",
                    "automatic_action_available": True,
                    "operator_action_required": False,
                    "manual_input_required": False,
                    "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
                    "scope": SCOPE,
                    "reason": REASON,
                    "suggested_command": COMMAND,
                },
            },
        )


def test_operator_action_binding_must_match_audit(monkeypatch):
    bad_operator = {
        "status": "AUTOMATIC_ACTION",
        "automatic_action_available": True,
        "operator_action_required": False,
        "manual_input_required": False,
        "debt_type": DEBT_TYPE,
        "scope": "cycle-2",
        "reason": REASON,
        "suggested_command": COMMAND,
    }

    with pytest.raises(ValueError, match="action mismatch"):
        _build(
            monkeypatch,
            audit_override={
                "fresh_phase8_operator_handoff": bad_operator
            },
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


def test_post_audit_digest_pin_must_match(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        database = data / "pio.db"
        database.write_bytes(b"phase8-current")
        audit = _audit(production, database)
        path = _write(root / "audit.json", audit)

        class FakeAudit:
            @staticmethod
            def validate_phase8_offline_step_post_audit(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_audit_module",
            lambda source: FakeAudit,
        )
        with pytest.raises(ValueError, match="digest mismatch"):
            MODULE.build_phase8_offline_continuation_request(
                repository=production,
                source_tree=ROOT,
                post_audit_path=path,
                expected_post_audit_sha256="f" * 64,
            )


def test_resealed_request_cannot_authorize_paper(monkeypatch):
    request = _build(monkeypatch)
    request["paper_trading_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_offline_continuation_request(request)


def test_resealed_request_cannot_authorize_phase8_execution(monkeypatch):
    request = _build(monkeypatch)
    request["phase8_execution_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="phase8_execution_authorized=false",
    ):
        MODULE.validate_phase8_offline_continuation_request(request)


def test_continuation_request_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_phase8_evidence_step(" not in source
    assert "train_phase8_cycle_challenger(" not in source
    assert "start_model_paper_challenger(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"offline_step_execution_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_execution_authorized": False' in source
