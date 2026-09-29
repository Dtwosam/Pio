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
    / "check_phase8_offline_step_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_offline_step_execution_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


APPROVAL_ID = "11111111-2222-4333-8444-555555555555"
DEBT_TYPE = "RETRAIN_OFFLINE_TRAIN_READY"
SCOPE = "cycle-1"
REASON = "private offline training step is ready"
COMMAND = "pio phase8-evidence-run --max-steps 4"


def _db_sha(database: Path) -> str:
    return hashlib.sha256(database.read_bytes()).hexdigest()


def _handoff(database: Path) -> dict:
    return {
        "handoff_sha256": "a" * 64,
        "saved_phase7_post_promotion_audit_sha256": "b" * 64,
        "production_repository": str(database.parent.parent),
        "pio_database_path": str(database),
        "pio_database_sha256_after": _db_sha(database),
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "phase8_operator_handoff": {
            "status": "AUTOMATIC_ACTION",
            "debt_type": DEBT_TYPE,
            "scope": SCOPE,
            "reason": REASON,
            "suggested_command": COMMAND,
        },
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
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


def _request(database: Path) -> dict:
    return {
        "request_sha256": "c" * 64,
        "saved_phase8_handoff_sha256": "a" * 64,
        "production_repository": str(database.parent.parent),
        "pio_database_path": str(database),
        "pio_database_sha256": _db_sha(database),
        "phase8_status": "AUTOMATIC_ACTION",
        "debt_type": DEBT_TYPE,
        "scope": SCOPE,
        "reason": REASON,
        "suggested_command": COMMAND,
        "automatic_action_available": True,
        "allowed_offline_debt_type": True,
        "one_step_only": True,
        "request_ready": True,
        "fresh_phase8_handoff_recheck_required": True,
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
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


def _verification(database: Path) -> dict:
    return {
        "verification_sha256": "d" * 64,
        "request_sha256": "c" * 64,
        "saved_phase8_handoff_sha256": "a" * 64,
        "pio_database_sha256": _db_sha(database),
        "debt_type": DEBT_TYPE,
        "scope": SCOPE,
        "approval_payload_sha256": "e" * 64,
        "approval_signature_sha256": "f" * 64,
        "allowed_signers_sha256": "1" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": APPROVAL_ID,
        "expires_at": "2026-09-29T21:40:00Z",
        "approval_not_expired": True,
        "human_offline_step_authorization_verified": True,
        "offline_step_executed": False,
        "paper_challenger_transition_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    handoff_override: dict | None = None,
    request_override: dict | None = None,
    verification_override: dict | None = None,
    fresh_handoff_override: dict | None = None,
    fresh_verification_override: dict | None = None,
    mutate_database_after_artifacts: bool = False,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        database = data / "pio.db"
        database.write_bytes(b"phase8-db")

        handoff = _handoff(database)
        request = _request(database)
        verification = _verification(database)
        if handoff_override:
            handoff.update(handoff_override)
        if request_override:
            request.update(request_override)
        if verification_override:
            verification.update(verification_override)

        fresh_handoff = copy.deepcopy(handoff)
        if fresh_handoff_override:
            fresh_handoff.update(fresh_handoff_override)
        fresh_verification = copy.deepcopy(verification)
        if fresh_verification_override:
            fresh_verification.update(fresh_verification_override)

        class FakeHandoff:
            @staticmethod
            def validate_phase8_post_phase7_handoff(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_phase8_post_phase7_handoff(**kwargs):
                return copy.deepcopy(fresh_handoff)

        class FakeRequest:
            ALLOWED_DEBT_TYPES = {
                "RETRAIN_DATASET_BUILD_READY",
                "RETRAIN_OFFLINE_TRAIN_READY",
                "RETRAIN_OFFLINE_VALIDATION_READY",
            }

            @staticmethod
            def validate_phase8_offline_step_request(value):
                assert isinstance(value, dict)

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
            lambda source: (FakeHandoff, FakeRequest, FakeSigner),
        )

        handoff_path = _write(root / "handoff.json", handoff)
        request_path = _write(root / "request.json", request)
        verification_path = _write(
            root / "verification.json",
            verification,
        )
        phase7_path = _write(
            root / "phase7-post.json",
            {"unused": True},
        )
        payload_path = _write(
            root / "payload.json",
            {"unused": True},
        )
        signature = root / "signature"
        signature.write_bytes(b"sig")
        allowed = root / "allowed"
        allowed.write_bytes(b"allowed")

        if mutate_database_after_artifacts:
            database.write_bytes(b"phase8-db-mutated")

        return MODULE.build_phase8_offline_step_execution_readiness(
            repository=production,
            source_tree=ROOT,
            saved_phase8_handoff_path=handoff_path,
            phase7_post_promotion_audit_path=phase7_path,
            offline_step_request_path=request_path,
            saved_signed_authorization_verification_path=(
                verification_path
            ),
            signed_payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="1" * 64,
            now="2026-09-29T21:35:00Z",
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_report_freshly_rechecks_handoff_and_authorization(
    monkeypatch,
):
    report = _build(monkeypatch)

    assert report["fresh_handoff_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True
    assert report["human_offline_step_authorization_verified"] is True
    assert report["approval_not_expired"] is True
    assert report["allowed_offline_debt_type"] is True
    assert report["fresh_phase8_handoff_recheck_completed"] is True
    assert report["offline_step_execution_readiness_ready"] is True
    assert report["readiness_only"] is True
    assert report["requires_immediate_one_shot_offline_executor"] is True
    assert report["requires_post_step_audit"] is True
    assert report["offline_step_execution_authorized"] is False
    assert report["offline_step_executed"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["new_live_capital_authorized"] is False


def test_fresh_handoff_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="fresh Phase 8 handoff differs",
    ):
        _build(
            monkeypatch,
            fresh_handoff_override={"handoff_sha256": "9" * 64},
        )


def test_fresh_authorization_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="authorization differs from saved",
    ):
        _build(
            monkeypatch,
            fresh_verification_override={
                "verification_sha256": "9" * 64
            },
        )


def test_database_drift_after_request_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="database changed"):
        _build(monkeypatch, mutate_database_after_artifacts=True)


def test_non_offline_debt_type_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="debt type is not allowed"):
        _build(
            monkeypatch,
            request_override={
                "debt_type": "PAPER_CHALLENGER_START_REQUIRED"
            },
            verification_override={
                "debt_type": "PAPER_CHALLENGER_START_REQUIRED"
            },
            handoff_override={
                "phase8_operator_handoff": {
                    "status": "AUTOMATIC_ACTION",
                    "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
                    "scope": SCOPE,
                    "reason": REASON,
                    "suggested_command": COMMAND,
                }
            },
        )


def test_resealed_readiness_cannot_claim_offline_step_executed(
    monkeypatch,
):
    report = _build(monkeypatch)
    report["offline_step_executed"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="offline_step_executed=false",
    ):
        MODULE.validate_phase8_offline_step_execution_readiness(report)


def test_resealed_readiness_cannot_authorize_paper(monkeypatch):
    report = _build(monkeypatch)
    report["paper_trading_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_offline_step_execution_readiness(report)


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
        MODULE.validate_phase8_offline_step_execution_readiness(report)


def test_readiness_tool_has_no_step_or_live_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_phase8_evidence_step(" not in source
    assert "run_phase8_evidence_until_blocked(" not in source
    assert "start_model_paper_challenger(" not in source
    assert "subprocess" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert '"offline_step_executed": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
