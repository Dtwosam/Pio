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
    / "check_phase8_paper_challenger_transition_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paper_challenger_transition_execution_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


MODEL_ID = "challenger-1"
CYCLE_ID = "cycle-1"
COMMAND = f"pio ml-start-paper --model-id {MODEL_ID}"
REASON = "offline-qualified challenger is ready for PAPER transition"


def _artifacts() -> list[dict]:
    return [
        {
            "path": "phase8_ml_artifacts/cycle-1/model.json",
            "size_bytes": 10,
            "sha256": "1" * 64,
        }
    ]


def _operator(*, command: str = COMMAND) -> dict:
    return {
        "as_of": "2026-09-29T21:45:00Z",
        "status": "MANUAL_REQUIRED",
        "automatic_action_available": False,
        "operator_action_required": True,
        "manual_input_required": False,
        "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
        "scope": MODEL_ID,
        "reason": REASON,
        "suggested_command": command,
        "research_only": True,
        "read_only": True,
        "policy_actionable": False,
        "execution_wired": False,
    }


def _status(
    *,
    cycle_status: str = "OFFLINE_QUALIFIED",
    model_status: str = "OFFLINE_QUALIFIED",
) -> dict:
    return {
        "as_of": "2026-09-29T21:45:00Z",
        "phase7_promoted": True,
        "active_cycle_id": CYCLE_ID,
        "active_cycle_status": cycle_status,
        "active_cycle_challenger_model_id": MODEL_ID,
        "active_cycle_challenger_status": model_status,
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
    }


def _plan() -> dict:
    return {
        "as_of": "2026-09-29T21:45:00Z",
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "promotion_ready": False,
        "persisted_phase8_current": False,
        "next_action": {
            "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
            "scope": MODEL_ID,
        },
        "items": [],
        "reasons": [],
    }


def _audit() -> dict:
    artifacts = _artifacts()
    return {
        "post_audit_sha256": "a" * 64,
        "execution_receipt_sha256": "b" * 64,
        "source_post_audit_sha256": "c" * 64,
        "source_execution_receipt_sha256": "d" * 64,
        "source_offline_step_request_sha256": "e" * 64,
        "continuation_request_sha256": "f" * 64,
        "signed_authorization_verification_sha256": "2" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "receipt_database_sha256_after": "3" * 64,
        "receipt_wal_sha256_after": None,
        "receipt_shm_sha256_after": None,
        "receipt_research_artifacts_after": artifacts,
        "audit_research_artifacts_before": artifacts,
        "audit_research_artifacts_after": artifacts,
        "production_research_artifacts_match_receipt": True,
        "production_research_artifacts_unchanged_by_audit": True,
        "execution_research_artifact_change_count": 0,
        "audit_database_sha256_before": "3" * 64,
        "audit_database_sha256_after": "3" * 64,
        "audit_wal_sha256_before": None,
        "audit_wal_sha256_after": None,
        "audit_shm_sha256_before": None,
        "audit_shm_sha256_after": None,
        "production_database_matches_receipt": True,
        "production_database_unchanged_by_audit": True,
        "executed_debt_type": "RETRAIN_OFFLINE_VALIDATION_READY",
        "executed_scope": CYCLE_ID,
        "execution_status": "COMPLETE",
        "execution_completed": True,
        "execution_progressed": True,
        "fresh_phase8_evidence_status": _status(),
        "fresh_phase8_evidence_status_sha256": "4" * 64,
        "fresh_phase8_evidence_plan": _plan(),
        "fresh_phase8_evidence_plan_sha256": "5" * 64,
        "receipt_planner_after_stable_sha256": "6" * 64,
        "fresh_planner_stable_sha256": "6" * 64,
        "receipt_planner_after_matches_fresh": True,
        "fresh_phase8_operator_handoff": _operator(),
        "fresh_phase8_operator_handoff_sha256": "7" * 64,
        "next_debt_type": "PAPER_CHALLENGER_START_REQUIRED",
        "next_scope": MODEL_ID,
        "continuation_route": "PHASE8_PAPER_CHALLENGER_REVIEW",
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
        "next_offline_step_reauthorization_required": False,
        "paper_challenger_review_required": True,
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
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified_by_audit": False,
    }


def _request() -> dict:
    return {
        "request_sha256": "8" * 64,
        "source_post_audit_sha256": "a" * 64,
        "source_execution_receipt_sha256": "b" * 64,
        "source_continuation_request_sha256": "f" * 64,
        "source_signed_authorization_verification_sha256": "2" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "3" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "research_artifacts_sha256": hashlib.sha256(
            MODULE._canonical_bytes(_artifacts())
        ).hexdigest(),
        "research_artifact_count": 1,
        "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
        "scope": MODEL_ID,
        "active_cycle_id": CYCLE_ID,
        "model_id": MODEL_ID,
        "active_cycle_status": "OFFLINE_QUALIFIED",
        "challenger_model_status": "OFFLINE_QUALIFIED",
        "reason": REASON,
        "suggested_command": COMMAND,
        "cycle_sync_required_after_model_transition": True,
    }


def _verification(request: dict) -> dict:
    value = {
        **{
            field: request[field]
            for field in (
                "request_sha256",
                "source_post_audit_sha256",
                "source_execution_receipt_sha256",
                "source_continuation_request_sha256",
                "source_signed_authorization_verification_sha256",
                "pio_database_sha256",
                "pio_wal_sha256",
                "pio_shm_sha256",
                "research_artifacts_sha256",
                "research_artifact_count",
                "debt_type",
                "scope",
                "active_cycle_id",
                "model_id",
                "active_cycle_status",
                "challenger_model_status",
                "reason",
                "suggested_command",
            )
        },
        "verification_sha256": "9" * 64,
        "approval_payload_sha256": "a" * 64,
        "approval_signature_sha256": "b" * 64,
        "allowed_signers_sha256": "c" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "expires_at": "2026-09-29T21:50:00Z",
        "human_paper_challenger_transition_authorization_verified": True,
        "approval_not_expired": True,
    }
    return value


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh_audit_mutator=None,
    fresh_request_mutator=None,
    fresh_verification_mutator=None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    production.mkdir()

    saved_audit = _audit()
    saved_audit["production_repository"] = str(production)
    saved_audit["pio_database_path"] = str(
        production / "data" / "pio.db"
    )
    request = _request()
    request["production_repository"] = str(production)
    request["pio_database_path"] = saved_audit["pio_database_path"]
    verification = _verification(request)

    fresh_audit = copy.deepcopy(saved_audit)
    fresh_audit["post_audit_sha256"] = "d" * 64
    fresh_audit["fresh_phase8_evidence_status"]["as_of"] = (
        "2026-09-29T21:46:00Z"
    )
    fresh_audit["fresh_phase8_evidence_plan"]["as_of"] = (
        "2026-09-29T21:46:00Z"
    )
    fresh_audit["fresh_phase8_operator_handoff"]["as_of"] = (
        "2026-09-29T21:46:00Z"
    )
    fresh_audit["fresh_phase8_evidence_status_sha256"] = "e" * 64
    fresh_audit["fresh_phase8_evidence_plan_sha256"] = "f" * 64
    fresh_audit["fresh_phase8_operator_handoff_sha256"] = "1" * 64
    if fresh_audit_mutator:
        fresh_audit_mutator(fresh_audit)

    fresh_request = copy.deepcopy(request)
    if fresh_request_mutator:
        fresh_request_mutator(fresh_request)
    fresh_verification = copy.deepcopy(verification)
    if fresh_verification_mutator:
        fresh_verification_mutator(fresh_verification)

    class FakeAudit:
        ROUTE_PAPER_REVIEW = "PHASE8_PAPER_CHALLENGER_REVIEW"

        @staticmethod
        def validate_phase8_offline_continuation_post_audit(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_offline_continuation_post_audit(**kwargs):
            return copy.deepcopy(fresh_audit)

    class FakeRequest:
        @staticmethod
        def validate_phase8_paper_challenger_transition_request(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paper_challenger_transition_request(**kwargs):
            return copy.deepcopy(fresh_request)

    class FakeSigner:
        BINDING_FIELDS = (
            "request_sha256",
            "source_post_audit_sha256",
            "source_execution_receipt_sha256",
            "source_continuation_request_sha256",
            "source_signed_authorization_verification_sha256",
            "pio_database_sha256",
            "pio_wal_sha256",
            "pio_shm_sha256",
            "research_artifacts_sha256",
            "research_artifact_count",
            "debt_type",
            "scope",
            "active_cycle_id",
            "model_id",
            "active_cycle_status",
            "challenger_model_status",
            "reason",
            "suggested_command",
        )

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
    verification_path = _write(root / "verification.json", verification)
    for name in ("receipt.json", "payload.json"):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    def run():
        return MODULE.build_phase8_paper_challenger_transition_execution_readiness(
            repository=production,
            source_tree=ROOT,
            saved_post_audit_path=audit_path,
            execution_receipt_path=root / "receipt.json",
            transition_request_path=request_path,
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="c" * 64,
            now="2026-09-29T21:47:00Z",
        )

    return temp, run


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


def test_readiness_allows_only_ephemeral_audit_timestamp_drift(
    monkeypatch,
):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["saved_post_audit_sha256"] == "a" * 64
    assert report["fresh_post_audit_sha256"] == "d" * 64
    assert (
        report["saved_post_audit_state_sha256"]
        == report["fresh_post_audit_state_sha256"]
    )
    assert report["fresh_post_audit_state_matches_saved"] is True
    assert report["fresh_transition_request_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True
    assert report[
        "human_paper_challenger_transition_authorization_verified"
    ] is True
    assert report["exact_cycle_model_binding_verified"] is True
    assert report["cycle_sync_required_after_model_transition"] is True
    assert report["paper_transition_execution_readiness_ready"] is True
    assert report["readiness_only"] is True
    assert report[
        "requires_immediate_one_shot_paper_transition_executor"
    ] is True
    assert report["paper_challenger_transition_authorized"] is False
    assert report["paper_challenger_transition_executed"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_operator_command_drift_fails_closed(monkeypatch):
    def mutate(audit):
        audit["fresh_phase8_operator_handoff"][
            "suggested_command"
        ] = "pio changed-command"

    temp, run = _build(
        monkeypatch,
        fresh_audit_mutator=mutate,
    )
    try:
        with pytest.raises(ValueError, match="operator action differs"):
            run()
    finally:
        temp.cleanup()


def test_cycle_status_drift_fails_closed(monkeypatch):
    def mutate(audit):
        audit["fresh_phase8_evidence_status"][
            "active_cycle_status"
        ] = "PAPER_CHALLENGER"

    temp, run = _build(
        monkeypatch,
        fresh_audit_mutator=mutate,
    )
    try:
        with pytest.raises(ValueError, match="cycle/model state differs"):
            run()
    finally:
        temp.cleanup()


def test_model_identity_drift_fails_closed(monkeypatch):
    def mutate(audit):
        audit["fresh_phase8_evidence_status"][
            "active_cycle_challenger_model_id"
        ] = "challenger-2"

    temp, run = _build(
        monkeypatch,
        fresh_audit_mutator=mutate,
    )
    try:
        with pytest.raises(ValueError, match="cycle/model state differs"):
            run()
    finally:
        temp.cleanup()


def test_fresh_request_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        fresh_request_mutator=lambda value: value.update(
            research_artifact_count=2
        ),
    )
    try:
        with pytest.raises(ValueError, match="request differs"):
            run()
    finally:
        temp.cleanup()


def test_fresh_authorization_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        fresh_verification_mutator=lambda value: value.update(
            verification_sha256="0" * 64
        ),
    )
    try:
        with pytest.raises(ValueError, match="authorization differs"):
            run()
    finally:
        temp.cleanup()


def test_substantive_post_audit_drift_fails_closed(monkeypatch):
    def mutate(audit):
        audit["next_scope"] = "challenger-2"

    temp, run = _build(
        monkeypatch,
        fresh_audit_mutator=mutate,
    )
    try:
        with pytest.raises(
            ValueError,
            match="decisive state differs|scope differs",
        ):
            run()
    finally:
        temp.cleanup()


def test_resealed_readiness_cannot_claim_transition_executed(
    monkeypatch,
):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_challenger_transition_executed"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_challenger_transition_executed=false",
    ):
        MODULE.validate_phase8_paper_challenger_transition_execution_readiness(
            report
        )


def test_resealed_readiness_cannot_authorize_paper_trading(
    monkeypatch,
):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_trading_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_paper_challenger_transition_execution_readiness(
            report
        )


def test_readiness_has_no_transition_or_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "start_paper_challenger(" not in source
    assert "start_model_paper_challenger(" not in source
    assert "sync_retraining_cycle(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_challenger_transition_executed": False' in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
