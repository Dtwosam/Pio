from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_manual_market_paper_preserved_execution_precheck.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_preserved_execution_precheck",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _operation(
    *,
    index: int = 0,
    operation: str = "UPDATE_PRESERVED_FILE",
    path: str = "rust-executor/src/state_reader.rs",
    backup_required: bool = True,
) -> dict:
    expected = "1" * 40 if backup_required else None
    return {
        "index": index,
        "layer": "STATE_READER",
        "operation": operation,
        "path": path,
        "plan_operation_sha256": "2" * 64,
        "expected_current_blob": expected,
        "target_blob": "3" * 40,
        "rollback_operation": (
            "RESTORE_EXPECTED_BLOB"
            if backup_required
            else "DELETE_CREATED_FILE"
        ),
        "rollback_blob": expected if backup_required else None,
        "backup_required": backup_required,
        "backup_relative_path": (
            f"files/{path}" if backup_required else None
        ),
        "backup_git_blob": expected if backup_required else None,
        "backup_sha256": "4" * 64 if backup_required else None,
        "backup_size": 123 if backup_required else None,
        "backup_mode": 0o644 if backup_required else None,
    }


def _precheck_operation() -> dict:
    return {
        **_operation(),
        "authorization_review_operation_ready": True,
        "backup_integrity_matches": True,
        "production_expected_state_revalidated": True,
        "target_source_revalidated": True,
        "operation_precheck_ready": True,
    }


def _report() -> dict:
    operations = [_precheck_operation()]
    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                MODULE.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "saved_authorization_review_sha256": "5" * 64,
        "fresh_authorization_review_sha256": "5" * 64,
        "authorization_request_sha256": "6" * 64,
        "saved_signed_authorization_verification_sha256": "7" * 64,
        "fresh_signed_authorization_verification_sha256": "7" * 64,
        "production_repository": "/opt/pio",
        "backup_dir": "/var/tmp/pio-preserved-backups.test",
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "approval_expires_at": "2026-09-28T10:05:00Z",
        "authorization_scope": MODULE.AUTHORIZATION_SCOPE,
        "operation_prechecks": operations,
        "operation_count": len(operations),
        "fresh_authorization_review_matches_saved": True,
        "request_binds_authorization_review": True,
        "request_scope_matches_review": True,
        "fresh_signed_authorization_matches_saved": True,
        "signed_authorization_binds_request": True,
        "human_authorization_verified": True,
        "approval_not_expired": True,
        "backup_integrity_ready": True,
        "all_operations_prechecked": True,
        "preserved_file_mutation_authorization_present": True,
        "execution_precheck_ready": True,
        "requires_immediate_per_operation_recheck": True,
        "execution_ready": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    return {
        **identity,
        "execution_precheck_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["execution_precheck_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _saved_artifacts(production: Path) -> tuple[dict, dict, dict]:
    operation = _operation()
    review_entry = {
        **operation,
        "backup_observed_state": "BLOB_READ",
        "backup_observed_blob": operation["backup_git_blob"],
        "backup_observed_sha256": operation["backup_sha256"],
        "backup_observed_size": operation["backup_size"],
        "backup_observed_mode": operation["backup_mode"],
        "backup_integrity_matches": True,
        "operation_ready": True,
    }
    review = {
        "authorization_review_ready": True,
        "authorization_review_sha256": "5" * 64,
        "production_repository": str(production),
        "backup_dir": "/var/tmp/pio-preserved-backups.test",
        "backup_integrity_ready": True,
        "operation_scope": [review_entry],
    }
    request = {
        "authorization_request_ready": True,
        "authorization_review_sha256": review[
            "authorization_review_sha256"
        ],
        "production_repository": str(production),
        "operations": [operation],
        "operation_count": 1,
        "request_sha256": "6" * 64,
    }
    signed = {
        "human_authorization_verified": True,
        "request_sha256": request["request_sha256"],
        "authorization_review_sha256": review[
            "authorization_review_sha256"
        ],
        "authorization_scope": MODULE.AUTHORIZATION_SCOPE,
        "execution_ready": False,
        "mutation_authorized": False,
        "verification_sha256": "7" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "expires_at": "2026-09-28T10:05:00Z",
        "approval_not_expired": True,
    }
    return review, request, signed


def _write_json(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build_with_fakes(
    monkeypatch,
    *,
    mutate_review=None,
    mutate_request=None,
    mutate_signed=None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    production.mkdir()

    review, request, signed = _saved_artifacts(production)
    if mutate_review is not None:
        mutate_review(review)
    if mutate_request is not None:
        mutate_request(request)
    if mutate_signed is not None:
        mutate_signed(signed)

    class FakeAuthorizationModule:
        @staticmethod
        def validate_authorization_review(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_authorization_review(**kwargs):
            return copy.deepcopy(review)

    class FakeRequestModule:
        @staticmethod
        def validate_authorization_request(value):
            assert isinstance(value, dict)

    class FakeSignedModule:
        @staticmethod
        def _validate_verification(value):
            assert isinstance(value, dict)

        @staticmethod
        def verify_authorization(**kwargs):
            return copy.deepcopy(signed)

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_modules",
        lambda source: (
            FakeAuthorizationModule,
            FakeRequestModule,
            FakeSignedModule,
        ),
    )

    review_path = _write_json(root, "review.json", review)
    request_path = _write_json(root, "request.json", request)
    signed_path = _write_json(root, "signed.json", signed)

    def run():
        return MODULE.build_execution_precheck(
            repository=production,
            source_tree=ROOT,
            private_bundle_report_path=root / "private-bundle.json",
            handoff_path=root / "handoff.json",
            plan_path=root / "plan.json",
            gate_path=root / "gate.json",
            mutation_review_path=root / "mutation-review.json",
            evidence_package_path=root / "evidence.json",
            backup_capture_path=root / "backup.json",
            authorization_review_path=review_path,
            authorization_request_path=request_path,
            signed_authorization_verification_path=signed_path,
            signed_payload_path=root / "payload.json",
            signature_path=root / "payload.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="8" * 64,
        )

    return temp, review, request, signed, run


def test_reviewed_execution_precheck_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_execution_precheck_remains_non_executable():
    report = _report()
    MODULE.validate_execution_precheck(copy.deepcopy(report))

    assert report["execution_precheck_ready"] is True
    assert report["preserved_file_mutation_authorization_present"] is True
    assert report["requires_immediate_per_operation_recheck"] is True
    assert report["execution_ready"] is False
    assert report["mutation_authorized"] is False


def test_resealed_precheck_cannot_become_execution_ready():
    report = _report()
    report["execution_ready"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="remain non-executable"):
        MODULE.validate_execution_precheck(report)


def test_resealed_precheck_cannot_authorize_mutation():
    report = _report()
    report["mutation_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="mutation_authorized=false"):
        MODULE.validate_execution_precheck(report)


def test_resealed_precheck_cannot_redirect_backup_identity():
    report = _report()
    report["operation_prechecks"][0]["backup_sha256"] = "not-a-digest"
    _reseal(report)

    with pytest.raises(ValueError, match="backup SHA-256 is invalid"):
        MODULE.validate_execution_precheck(report)


def test_resealed_precheck_cannot_change_rollback_semantics():
    report = _report()
    report["operation_prechecks"][0]["rollback_operation"] = "DELETE_CREATED_FILE"
    _reseal(report)

    with pytest.raises(ValueError, match="rollback action mismatch"):
        MODULE.validate_execution_precheck(report)


def test_builder_rechecks_live_review_and_signed_authorization(monkeypatch):
    temp, _, _, _, run = _build_with_fakes(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    MODULE.validate_execution_precheck(report)
    assert report["fresh_authorization_review_matches_saved"] is True
    assert report["fresh_signed_authorization_matches_saved"] is True
    assert report["human_authorization_verified"] is True
    assert report["backup_integrity_ready"] is True
    assert report["execution_precheck_ready"] is True
    assert report["execution_ready"] is False


def test_builder_fails_on_request_scope_drift(monkeypatch):
    def mutate_request(request):
        request["operations"][0]["target_blob"] = "9" * 40

    temp, _, _, _, run = _build_with_fakes(
        monkeypatch,
        mutate_request=mutate_request,
    )
    try:
        with pytest.raises(ValueError, match="operation scope mismatch"):
            run()
    finally:
        temp.cleanup()


def test_builder_fails_on_signed_request_binding_drift(monkeypatch):
    def mutate_signed(signed):
        signed["request_sha256"] = "9" * 64

    temp, _, _, _, run = _build_with_fakes(
        monkeypatch,
        mutate_signed=mutate_signed,
    )
    try:
        with pytest.raises(ValueError, match="signed request binding mismatch"):
            run()
    finally:
        temp.cleanup()


def test_tool_has_no_production_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "shutil.copy" not in source
    assert "os.replace" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert "systemctl" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert '"execution_ready": False' in source
    assert '"mutation_authorized": False' in source
    assert '"requires_immediate_per_operation_recheck": True' in source
