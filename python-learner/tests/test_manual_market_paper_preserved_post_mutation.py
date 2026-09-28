from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_manual_market_paper_preserved_post_mutation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_preserved_post_mutation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BACKUP_SPEC = importlib.util.spec_from_file_location(
    "post_mutation_test_backup_module",
    ROOT / MODULE.BACKUP_CAPTURE_TOOL,
)
assert BACKUP_SPEC is not None and BACKUP_SPEC.loader is not None
BACKUP = importlib.util.module_from_spec(BACKUP_SPEC)
sys.modules[BACKUP_SPEC.name] = BACKUP
BACKUP_SPEC.loader.exec_module(BACKUP)


def _blob(payload: bytes) -> str:
    return MODULE._git_blob_sha_bytes(payload)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.chmod(path, mode)


def _fixture():
    temp = tempfile.TemporaryDirectory(
        prefix="pio-post-mutation-test-",
        dir="/var/tmp",
    )
    root = Path(temp.name)
    source = root / "source"
    production = root / "production"
    backup_root = root / "backups"
    source.mkdir()
    production.mkdir()
    backup_root.mkdir()

    update_path = "rust-executor/src/state_reader.rs"
    create_path = "python-learner/src/meteora_learner/new_runtime.py"
    old_payload = b"old bytes\n"
    update_target = b"deployed preserved bytes\n"
    create_target = b"deployed runtime bytes\n"
    update_mode = 0o640
    create_mode = 0o644

    _write(production / update_path, update_target, update_mode)
    _write(production / create_path, create_target, create_mode)

    backup_relative = f"files/{update_path}"
    _write(backup_root / backup_relative, old_payload, update_mode)

    update_precheck = {
        "index": 0,
        "layer": "STATE_READER",
        "operation": "UPDATE_PRESERVED_FILE",
        "path": update_path,
        "plan_operation_sha256": "1" * 64,
        "expected_current_blob": _blob(old_payload),
        "target_blob": _blob(update_target),
        "rollback_operation": "RESTORE_EXPECTED_BLOB",
        "rollback_blob": _blob(old_payload),
        "backup_required": True,
        "backup_relative_path": backup_relative,
        "backup_git_blob": _blob(old_payload),
        "backup_sha256": _sha(old_payload),
        "backup_size": len(old_payload),
        "backup_mode": update_mode,
        "authorization_review_operation_ready": True,
        "backup_integrity_matches": True,
        "production_expected_state_revalidated": True,
        "target_source_revalidated": True,
        "operation_precheck_ready": True,
    }
    create_precheck = {
        "index": 1,
        "layer": "MARKET_PAPER_RUNTIME",
        "operation": "CREATE_FILE",
        "path": create_path,
        "plan_operation_sha256": "2" * 64,
        "expected_current_blob": None,
        "target_blob": _blob(create_target),
        "rollback_operation": "DELETE_CREATED_FILE",
        "rollback_blob": None,
        "backup_required": False,
        "backup_relative_path": None,
        "backup_git_blob": None,
        "backup_sha256": None,
        "backup_size": None,
        "backup_mode": None,
        "authorization_review_operation_ready": True,
        "backup_integrity_matches": True,
        "production_expected_state_revalidated": True,
        "target_source_revalidated": True,
        "operation_precheck_ready": True,
    }
    precheck = {
        "execution_precheck_sha256": "3" * 64,
        "production_repository": str(production),
        "backup_dir": str(backup_root),
        "operation_prechecks": [update_precheck, create_precheck],
    }

    update_result = {
        "index": 0,
        "layer": "STATE_READER",
        "operation": "UPDATE_PRESERVED_FILE",
        "path": update_path,
        "expected_current_blob": _blob(old_payload),
        "before_blob": _blob(old_payload),
        "target_blob": _blob(update_target),
        "after_blob": _blob(update_target),
        "target_mode": update_mode,
        "immediate_expected_state_recheck_passed": True,
        "source_bytes_reverified": True,
        "rollback_material_reverified": True,
        "write_applied": True,
        "post_write_verified": True,
    }
    create_result = {
        "index": 1,
        "layer": "MARKET_PAPER_RUNTIME",
        "operation": "CREATE_FILE",
        "path": create_path,
        "expected_current_blob": None,
        "before_blob": None,
        "target_blob": _blob(create_target),
        "after_blob": _blob(create_target),
        "target_mode": create_mode,
        "immediate_expected_state_recheck_passed": True,
        "source_bytes_reverified": True,
        "rollback_material_reverified": False,
        "write_applied": True,
        "post_write_verified": True,
    }
    receipt = {
        "receipt_sha256": "4" * 64,
        "execution_precheck_sha256": precheck["execution_precheck_sha256"],
        "production_repository": str(production),
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "operation_results": [update_result, create_result],
        "file_mutation_completed": True,
    }

    prior_state = {
        "production_head": "5" * 40,
        "detector_service": "active",
        "watcher_service": "active",
        "paper_account": "paper",
        "paper_service": "inactive",
        "paper_timer": "inactive",
        "target_pool": "pool-address",
        "target_pool_cursor": "cursor-before",
    }
    handoff = {
        "handoff_ready": True,
        "production_state_sha256": "6" * 64,
        "state": prior_state,
    }
    readiness = {
        "readiness_sha256": "7" * 64,
        "production_head": prior_state["production_head"],
        "detector_service": prior_state["detector_service"],
        "watcher_service": prior_state["watcher_service"],
        "paper_account": prior_state["paper_account"],
        "paper_service": prior_state["paper_service"],
        "paper_timer": prior_state["paper_timer"],
        "target_pool": prior_state["target_pool"],
        "target_pool_cursor": prior_state["target_pool_cursor"],
        "runtime_files_deployed": True,
        "manual_mode_safe": True,
        "operational_services_healthy": True,
        "manual_paper_runtime_ready": True,
    }

    paths = {}
    for name, value in (
        ("precheck.json", precheck),
        ("receipt.json", receipt),
        ("handoff.json", handoff),
        ("bundle.json", {"bundle_sha256": "8" * 64}),
    ):
        path = root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path

    return {
        "temp": temp,
        "root": root,
        "source": source,
        "production": production,
        "backup_root": backup_root,
        "update_path": update_path,
        "create_path": create_path,
        "old_payload": old_payload,
        "update_target": update_target,
        "create_target": create_target,
        "precheck": precheck,
        "receipt": receipt,
        "handoff": handoff,
        "readiness": readiness,
        "paths": paths,
    }


def _install_fakes(monkeypatch, fixture):
    readiness = fixture["readiness"]

    class FakeWriterModule:
        @staticmethod
        def validate_mutation_receipt(value):
            assert value["file_mutation_completed"] is True

    class FakePrecheckModule:
        @staticmethod
        def validate_execution_precheck(value):
            assert "operation_prechecks" in value

    class FakeHandoffModule:
        @staticmethod
        def validate_handoff_snapshot(value):
            assert value["handoff_ready"] is True

    class FakeReadinessModule:
        @staticmethod
        def build_preserved_readiness(**kwargs):
            return copy.deepcopy(readiness)

        @staticmethod
        def validate_preserved_readiness(value):
            assert value["runtime_files_deployed"] is True

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_modules",
        lambda source: (
            FakeWriterModule,
            FakePrecheckModule,
            FakeHandoffModule,
            FakeReadinessModule,
            BACKUP,
        ),
    )


def _run(fixture):
    return MODULE.build_post_mutation_audit(
        repository=fixture["production"],
        source_tree=fixture["source"],
        private_bundle_report_path=fixture["paths"]["bundle.json"],
        pre_mutation_handoff_path=fixture["paths"]["handoff.json"],
        execution_precheck_path=fixture["paths"]["precheck.json"],
        mutation_receipt_path=fixture["paths"]["receipt.json"],
    )


def test_reviewed_post_mutation_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_post_mutation_audit_verifies_files_backups_and_no_activation(
    monkeypatch,
):
    fixture = _fixture()
    _install_fakes(monkeypatch, fixture)
    try:
        report = _run(fixture)
        MODULE.validate_post_mutation_audit(report)

        assert report["all_targets_verified"] is True
        assert report["all_rollback_material_verified"] is True
        assert report["activation_state_unchanged"] is True
        assert report["activation_observed"] is False
        assert report["runtime_files_deployed"] is True
        assert report["manual_paper_runtime_ready"] is True
        assert report["post_mutation_audit_ready"] is True
    finally:
        fixture["temp"].cleanup()


def test_receipt_mode_drift_from_precheck_fails_closed(monkeypatch):
    fixture = _fixture()
    fixture["receipt"]["operation_results"][0]["target_mode"] = 0o644
    fixture["paths"]["receipt.json"].write_text(
        json.dumps(fixture["receipt"]),
        encoding="utf-8",
    )
    _install_fakes(monkeypatch, fixture)
    try:
        with pytest.raises(ValueError, match="target-mode binding mismatch"):
            _run(fixture)
    finally:
        fixture["temp"].cleanup()


def test_target_drift_after_writer_fails_closed(monkeypatch):
    fixture = _fixture()
    _install_fakes(monkeypatch, fixture)
    try:
        target = fixture["production"] / fixture["update_path"]
        target.write_bytes(b"drifted after writer\n")

        with pytest.raises(ValueError, match="target blob mismatch"):
            _run(fixture)
    finally:
        fixture["temp"].cleanup()


def test_rollback_backup_tampering_after_writer_fails_closed(monkeypatch):
    fixture = _fixture()
    _install_fakes(monkeypatch, fixture)
    try:
        backup = (
            fixture["backup_root"]
            / "files"
            / fixture["update_path"]
        )
        backup.write_bytes(b"tampered rollback\n")

        with pytest.raises(ValueError, match="rollback material mismatch"):
            _run(fixture)
    finally:
        fixture["temp"].cleanup()


def test_service_or_timer_change_is_detected_as_activation_drift(monkeypatch):
    fixture = _fixture()
    fixture["readiness"]["paper_timer"] = "active"
    fixture["readiness"]["manual_mode_safe"] = False
    fixture["readiness"]["manual_paper_runtime_ready"] = False
    _install_fakes(monkeypatch, fixture)
    try:
        with pytest.raises(ValueError, match="failed closed"):
            _run(fixture)
    finally:
        fixture["temp"].cleanup()


def test_cursor_change_is_detected_as_activation_drift(monkeypatch):
    fixture = _fixture()
    fixture["readiness"]["target_pool_cursor"] = "cursor-after"
    _install_fakes(monkeypatch, fixture)
    try:
        with pytest.raises(ValueError, match="failed closed"):
            _run(fixture)
    finally:
        fixture["temp"].cleanup()


def test_rehashed_audit_cannot_claim_activation_observed():
    fixture = _fixture()
    try:
        target = {
            "index": 0,
            "layer": "STATE_READER",
            "operation": "UPDATE_PRESERVED_FILE",
            "path": fixture["update_path"],
            "target_blob": _blob(fixture["update_target"]),
            "observed_blob": _blob(fixture["update_target"]),
            "target_mode": 0o640,
            "observed_mode": 0o640,
            "target_matches_receipt": True,
            "mode_matches_receipt": True,
            "rollback_required": True,
            "rollback_material_verified": True,
            "target_audit_ready": True,
        }
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
            "mutation_receipt_sha256": "4" * 64,
            "execution_precheck_sha256": "3" * 64,
            "pre_mutation_handoff_state_sha256": "6" * 64,
            "post_mutation_readiness_sha256": "7" * 64,
            "production_repository": str(fixture["production"]),
            "approver_principal": "wyck@example.com",
            "approval_id": "01234567-89ab-4def-8123-456789abcdef",
            "target_audits": [target],
            "operation_count": 1,
            "all_targets_verified": True,
            "all_rollback_material_verified": True,
            "production_head_unchanged": True,
            "detector_service_unchanged": True,
            "watcher_service_unchanged": True,
            "paper_service_unchanged": True,
            "paper_timer_unchanged": True,
            "target_pool_unchanged": True,
            "target_pool_cursor_unchanged": True,
            "runtime_files_deployed": True,
            "manual_mode_safe": True,
            "operational_services_healthy": True,
            "manual_paper_runtime_ready": True,
            "activation_state_unchanged": True,
            "activation_observed": True,
            "post_mutation_audit_ready": True,
            "production_file_modified": False,
            "production_repository_git_mutated": False,
            "service_restart_authorized": False,
            "detector_cursor_movement_authorized": False,
            "paper_timer_enable_authorized": False,
            "live_capital_authorized": False,
        }
        report = {
            **identity,
            "post_mutation_audit_sha256": hashlib.sha256(
                MODULE._canonical_bytes(identity)
            ).hexdigest(),
        }

        with pytest.raises(ValueError, match="must not observe activation"):
            MODULE.validate_post_mutation_audit(report)
    finally:
        fixture["temp"].cleanup()


def test_post_mutation_audit_has_no_write_or_activation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "os.replace" not in source
    assert ".unlink(" not in source
    assert "systemctl" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"activation_observed": False' not in source
    assert '"production_file_modified": False' in source
    assert '"service_restart_authorized": False' in source
