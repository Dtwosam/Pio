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
    / "check_phase8_offline_continuation_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_offline_continuation_execution_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POST_AUDIT_SHA = "a" * 64
REQUEST_SHA = "b" * 64
VERIFICATION_SHA = "c" * 64
RECEIPT_SHA = "d" * 64
PRIOR_REQUEST_SHA = "e" * 64
DEBT_TYPE = "RETRAIN_OFFLINE_VALIDATION_READY"
SCOPE = "cycle-1"


def _operator() -> dict:
    return {
        "status": "AUTOMATIC_ACTION",
        "automatic_action_available": True,
        "operator_action_required": False,
        "manual_input_required": False,
        "debt_type": DEBT_TYPE,
        "scope": SCOPE,
        "reason": "offline validation is ready",
        "suggested_command": (
            "pio phase8-retrain-offline-validate-run "
            "--cycle-id cycle-1"
        ),
        "research_only": True,
        "read_only": True,
        "policy_actionable": False,
        "execution_wired": False,
    }


def _audit(*, post_sha: str = POST_AUDIT_SHA) -> dict:
    return {
        "post_audit_sha256": post_sha,
        "execution_receipt_sha256": RECEIPT_SHA,
        "offline_step_request_sha256": PRIOR_REQUEST_SHA,
        "signed_authorization_verification_sha256": "f" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "receipt_database_sha256_after": "1" * 64,
        "receipt_wal_sha256_after": None,
        "receipt_shm_sha256_after": None,
        "receipt_research_artifacts_after": [],
        "audit_research_artifacts_after": [],
        "execution_research_artifact_change_count": 0,
        "audit_database_sha256_after": "1" * 64,
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_after": None,
        "executed_debt_type": "RETRAIN_OFFLINE_TRAIN_READY",
        "executed_scope": SCOPE,
        "execution_status": "COMPLETE",
        "execution_completed": True,
        "execution_progressed": True,
        "next_debt_type": DEBT_TYPE,
        "next_scope": SCOPE,
        "continuation_route": (
            "PHASE8_NEXT_OFFLINE_STEP_REAUTHORIZATION"
        ),
        "phase7_dependency_satisfied": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "phase8_promotion_ready": False,
        "phase8_persisted_current": False,
        "post_step_audit_ready": True,
        "requires_separate_phase8_action": True,
        "requires_new_human_authorization": True,
        "next_offline_step_reauthorization_required": True,
        "paper_challenger_review_required": False,
        "phase8_promotion_review_required": False,
        "manual_or_evidence_review_required": False,
        "failure_review_required": False,
        "phase8_current": False,
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
        "fresh_phase8_operator_handoff": _operator(),
    }


def _request() -> dict:
    return {
        "request_sha256": REQUEST_SHA,
        "source_post_audit_sha256": POST_AUDIT_SHA,
        "source_execution_receipt_sha256": RECEIPT_SHA,
        "source_offline_step_request_sha256": PRIOR_REQUEST_SHA,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "1" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "research_artifacts_sha256": "2" * 64,
        "research_artifact_count": 0,
        "debt_type": DEBT_TYPE,
        "scope": SCOPE,
        "reason": _operator()["reason"],
        "suggested_command": _operator()["suggested_command"],
    }


def _verification() -> dict:
    return {
        "verification_sha256": VERIFICATION_SHA,
        "request_sha256": REQUEST_SHA,
        "source_post_audit_sha256": POST_AUDIT_SHA,
        "source_execution_receipt_sha256": RECEIPT_SHA,
        "source_offline_step_request_sha256": PRIOR_REQUEST_SHA,
        "pio_database_sha256": "1" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "research_artifacts_sha256": "2" * 64,
        "research_artifact_count": 0,
        "debt_type": DEBT_TYPE,
        "scope": SCOPE,
        "approval_payload_sha256": "3" * 64,
        "approval_signature_sha256": "4" * 64,
        "allowed_signers_sha256": "5" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "expires_at": "2026-09-29T21:30:00Z",
        "approval_not_expired": True,
        "human_continuation_offline_step_authorization_verified": True,
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
    fresh_audit_override: dict | None = None,
    fresh_request_override: dict | None = None,
    fresh_verification_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        (production / "data" / "pio.db").write_bytes(b"db")

        saved_audit = _audit()
        saved_audit["production_repository"] = str(production)
        saved_audit["pio_database_path"] = str(
            production / "data" / "pio.db"
        )
        request = _request()
        request["production_repository"] = str(production)
        request["pio_database_path"] = str(
            production / "data" / "pio.db"
        )
        verification = _verification()

        fresh_audit = copy.deepcopy(saved_audit)
        fresh_audit["post_audit_sha256"] = "9" * 64
        if fresh_audit_override:
            fresh_audit.update(fresh_audit_override)

        fresh_request = copy.deepcopy(request)
        if fresh_request_override:
            fresh_request.update(fresh_request_override)
        fresh_verification = copy.deepcopy(verification)
        if fresh_verification_override:
            fresh_verification.update(fresh_verification_override)

        class FakeAudit:
            ROUTE_NEXT_OFFLINE = (
                "PHASE8_NEXT_OFFLINE_STEP_REAUTHORIZATION"
            )

            @staticmethod
            def validate_phase8_offline_step_post_audit(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_phase8_offline_step_post_audit(**kwargs):
                return copy.deepcopy(fresh_audit)

        class FakeRequest:
            @staticmethod
            def validate_phase8_offline_continuation_request(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_phase8_offline_continuation_request(**kwargs):
                return copy.deepcopy(fresh_request)

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                assert isinstance(value, dict)

            @staticmethod
            def verify_authorization(**kwargs):
                return copy.deepcopy(fresh_verification)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (FakeAudit, FakeRequest, FakeSigner),
        )

        audit_path = _write(root / "audit.json", saved_audit)
        request_path = _write(root / "request.json", request)
        verification_path = _write(
            root / "verification.json",
            verification,
        )
        receipt_path = _write(
            root / "receipt.json",
            {"fixture": True},
        )
        payload_path = _write(
            root / "payload.json",
            {"fixture": True},
        )
        signature = root / "signature"
        signature.write_bytes(b"sig")
        allowed = root / "allowed"
        allowed.write_bytes(b"allowed")

        return (
            MODULE.build_phase8_offline_continuation_execution_readiness(
                repository=production,
                source_tree=ROOT,
                saved_post_audit_path=audit_path,
                execution_receipt_path=receipt_path,
                continuation_request_path=request_path,
                saved_signed_authorization_verification_path=(
                    verification_path
                ),
                signed_payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256="5" * 64,
                now="2026-09-29T21:25:00Z",
            )
        )


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_readiness_rechecks_audit_request_and_signature(monkeypatch):
    report = _build(monkeypatch)

    assert report["saved_post_audit_sha256"] == POST_AUDIT_SHA
    assert report["fresh_post_audit_sha256"] == "9" * 64
    assert (
        report["saved_post_audit_state_sha256"]
        == report["fresh_post_audit_state_sha256"]
    )
    assert report["fresh_post_audit_state_matches_saved"] is True
    assert report["fresh_continuation_request_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True
    assert report[
        "human_continuation_offline_step_authorization_verified"
    ] is True
    assert report["approval_not_expired"] is True
    assert report["debt_type"] == DEBT_TYPE
    assert report["scope"] == SCOPE
    assert report["continuation_execution_readiness_ready"] is True
    assert report["readiness_only"] is True
    assert (
        report["requires_immediate_one_shot_continuation_executor"]
        is True
    )
    assert report["requires_post_step_audit"] is True
    assert report["offline_step_execution_authorized"] is False
    assert report["offline_step_executed"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["phase8_execution_authorized"] is False


def test_fresh_audit_decisive_state_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="decisive state differs"):
        _build(
            monkeypatch,
            fresh_audit_override={"next_scope": "cycle-2"},
        )


def test_fresh_request_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="request differs"):
        _build(
            monkeypatch,
            fresh_request_override={"scope": "cycle-2"},
        )


def test_fresh_authorization_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="authorization differs"):
        _build(
            monkeypatch,
            fresh_verification_override={
                "verification_sha256": "8" * 64
            },
        )


def test_fresh_operator_action_drift_fails_closed(monkeypatch):
    operator = _operator()
    operator["suggested_command"] = "pio changed-command"
    with pytest.raises(ValueError, match="operator action differs"):
        _build(
            monkeypatch,
            fresh_audit_override={
                "fresh_phase8_operator_handoff": operator
            },
        )


def test_resealed_readiness_cannot_authorize_paper(monkeypatch):
    report = _build(monkeypatch)
    report["paper_trading_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_offline_continuation_execution_readiness(
            report
        )


def test_resealed_readiness_cannot_authorize_phase8_execution(
    monkeypatch,
):
    report = _build(monkeypatch)
    report["phase8_execution_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase8_execution_authorized=false",
    ):
        MODULE.validate_phase8_offline_continuation_execution_readiness(
            report
        )


def test_continuation_readiness_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_phase8_evidence_step(" not in source
    assert "train_phase8_cycle_challenger(" not in source
    assert "start_model_paper_challenger(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"offline_step_execution_authorized": False' in source
    assert '"offline_step_executed": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"phase8_execution_authorized": False' in source
