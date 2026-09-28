from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
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
    / "apply_manual_market_paper_preserved_file_mutations.py"
)

SPEC = importlib.util.spec_from_file_location(
    "apply_manual_market_paper_preserved_file_mutations",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BACKUP_SPEC = importlib.util.spec_from_file_location(
    "preserved_file_writer_test_backup_module",
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


def _operation(
    *,
    index: int,
    operation: str,
    path: str,
    expected_payload: bytes | None,
    target_payload: bytes,
    backup_relative_path: str | None,
    mode: int | None,
) -> dict:
    expected_blob = _blob(expected_payload) if expected_payload is not None else None
    backup_required = expected_payload is not None
    return {
        "index": index,
        "layer": (
            "STATE_READER"
            if operation == "UPDATE_PRESERVED_FILE"
            else "MARKET_PAPER_RUNTIME"
        ),
        "operation": operation,
        "path": path,
        "plan_operation_sha256": "a" * 64,
        "expected_current_blob": expected_blob,
        "target_blob": _blob(target_payload),
        "rollback_operation": (
            "RESTORE_EXPECTED_BLOB"
            if backup_required
            else "DELETE_CREATED_FILE"
        ),
        "rollback_blob": expected_blob,
        "backup_required": backup_required,
        "backup_relative_path": backup_relative_path,
        "backup_git_blob": expected_blob if backup_required else None,
        "backup_sha256": _sha(expected_payload) if backup_required else None,
        "backup_size": len(expected_payload) if backup_required else None,
        "backup_mode": mode if backup_required else None,
        "authorization_review_operation_ready": True,
        "backup_integrity_matches": True,
        "production_expected_state_revalidated": True,
        "target_source_revalidated": True,
        "operation_precheck_ready": True,
    }


def _future_expiry() -> str:
    value = datetime.now(timezone.utc) + timedelta(minutes=10)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _saved_precheck(
    *,
    production: Path,
    backup_root: Path,
    operations: list[dict],
) -> dict:
    return {
        "execution_precheck_ready": True,
        "execution_ready": False,
        "requires_immediate_per_operation_recheck": True,
        "execution_precheck_sha256": "b" * 64,
        "production_repository": str(production),
        "backup_dir": str(backup_root),
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "approval_expires_at": _future_expiry(),
        "operation_prechecks": operations,
    }


def _write(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.chmod(path, mode)


def _build_fixture():
    temp = tempfile.TemporaryDirectory(prefix="pio-writer-test-", dir="/var/tmp")
    root = Path(temp.name)
    source = root / "source"
    production = root / "production"
    bundle = root / "bundle"
    backup_root = root / "backups"
    for directory in (source, production, bundle, backup_root):
        directory.mkdir()

    update_path = "rust-executor/src/state_reader.rs"
    create_path = "python-learner/src/meteora_learner/new_runtime.py"
    old_payload = b"old production bytes\n"
    update_target = b"new preserved bytes\n"
    create_target = b"new runtime file\n"
    update_mode = 0o640

    _write(production / update_path, old_payload, update_mode)
    (production / Path(create_path).parent).mkdir(parents=True, exist_ok=True)
    _write(bundle / update_path, update_target, 0o644)
    _write(source / create_path, create_target, MODULE.CREATE_FILE_MODE)

    backup_relative = f"files/{update_path}"
    _write(backup_root / backup_relative, old_payload, update_mode)

    operations = [
        _operation(
            index=0,
            operation="UPDATE_PRESERVED_FILE",
            path=update_path,
            expected_payload=old_payload,
            target_payload=update_target,
            backup_relative_path=backup_relative,
            mode=update_mode,
        ),
        _operation(
            index=1,
            operation="CREATE_FILE",
            path=create_path,
            expected_payload=None,
            target_payload=create_target,
            backup_relative_path=None,
            mode=None,
        ),
    ]
    precheck = _saved_precheck(
        production=production,
        backup_root=backup_root,
        operations=operations,
    )
    mutation_review = {
        "review_ready": True,
        "private_bundle_sha256": "c" * 64,
    }
    bundle_report = {"bundle_sha256": "c" * 64}

    paths = {}
    for name, value in (
        ("precheck.json", precheck),
        ("mutation-review.json", mutation_review),
        ("bundle-report.json", bundle_report),
    ):
        path = root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path

    for name in (
        "handoff.json",
        "plan.json",
        "gate.json",
        "evidence.json",
        "backup-capture.json",
        "authorization-review.json",
        "authorization-request.json",
        "signed-verification.json",
        "signed-payload.json",
        "signature.sig",
        "allowed-signers",
    ):
        path = root / name
        if name.endswith(".json"):
            path.write_text("{}", encoding="utf-8")
        else:
            path.write_bytes(b"x")
        paths[name] = path

    return {
        "temp": temp,
        "root": root,
        "source": source,
        "production": production,
        "bundle": bundle,
        "backup_root": backup_root,
        "old_payload": old_payload,
        "update_target": update_target,
        "create_target": create_target,
        "update_path": update_path,
        "create_path": create_path,
        "operations": operations,
        "precheck": precheck,
        "mutation_review": mutation_review,
        "paths": paths,
    }


def _install_fake_modules(monkeypatch, fixture):
    precheck = fixture["precheck"]
    bundle = fixture["bundle"]

    class FakePrecheckModule:
        @staticmethod
        def validate_execution_precheck(value):
            assert value["execution_precheck_ready"] is True

        @staticmethod
        def build_execution_precheck(**kwargs):
            return copy.deepcopy(precheck)

    class FakePlanModule:
        pass

    class FakeMutationReviewModule:
        @staticmethod
        def validate_preserved_mutation_review(value):
            assert value["review_ready"] is True

        @staticmethod
        def _load_reviewed_modules(source):
            return object(), FakePlanModule, object()

        @staticmethod
        def _private_bundle_dir(**kwargs):
            return bundle

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_modules",
        lambda source: (FakePrecheckModule, FakeMutationReviewModule, BACKUP),
    )


def _run_apply(fixture):
    paths = fixture["paths"]
    return MODULE.apply_preserved_file_mutations(
        repository=fixture["production"],
        source_tree=fixture["source"],
        private_bundle_report_path=paths["bundle-report.json"],
        handoff_path=paths["handoff.json"],
        plan_path=paths["plan.json"],
        gate_path=paths["gate.json"],
        mutation_review_path=paths["mutation-review.json"],
        evidence_package_path=paths["evidence.json"],
        backup_capture_path=paths["backup-capture.json"],
        authorization_review_path=paths["authorization-review.json"],
        authorization_request_path=paths["authorization-request.json"],
        signed_authorization_verification_path=paths["signed-verification.json"],
        signed_payload_path=paths["signed-payload.json"],
        signature_path=paths["signature.sig"],
        allowed_signers_path=paths["allowed-signers"],
        expected_allowed_signers_sha256="d" * 64,
        execution_precheck_path=paths["precheck.json"],
        expected_execution_precheck_sha256="b" * 64,
    )


def test_reviewed_writer_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_update_install_and_rollback_restore_exact_blob_and_mode():
    fixture = _build_fixture()
    try:
        operation = fixture["operations"][0]
        prepared = MODULE._prepare_operation(
            operation=operation,
            source=fixture["source"],
            private_bundle=fixture["bundle"],
            backup_root=fixture["backup_root"],
            backup_module=BACKUP,
        )

        result = MODULE._install_operation(
            prepared=prepared,
            production=fixture["production"],
            backup_module=BACKUP,
        )
        target = fixture["production"] / fixture["update_path"]
        assert target.read_bytes() == fixture["update_target"]
        assert stat_mode(target) == 0o640
        assert result["immediate_expected_state_recheck_passed"] is True

        MODULE._rollback_operation(
            prepared=prepared,
            production=fixture["production"],
            backup_module=BACKUP,
        )
        assert target.read_bytes() == fixture["old_payload"]
        assert stat_mode(target) == 0o640
    finally:
        fixture["temp"].cleanup()


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o7777


def test_create_install_is_no_overwrite_and_rollback_deletes_only_written_blob():
    fixture = _build_fixture()
    try:
        operation = fixture["operations"][1]
        prepared = MODULE._prepare_operation(
            operation=operation,
            source=fixture["source"],
            private_bundle=fixture["bundle"],
            backup_root=fixture["backup_root"],
            backup_module=BACKUP,
        )

        result = MODULE._install_operation(
            prepared=prepared,
            production=fixture["production"],
            backup_module=BACKUP,
        )
        target = fixture["production"] / fixture["create_path"]
        assert target.read_bytes() == fixture["create_target"]
        assert stat_mode(target) == MODULE.CREATE_FILE_MODE
        assert result["before_blob"] is None

        MODULE._rollback_operation(
            prepared=prepared,
            production=fixture["production"],
            backup_module=BACKUP,
        )
        assert not target.exists()
    finally:
        fixture["temp"].cleanup()


def test_writer_applies_exact_batch_and_emits_non_activation_receipt(monkeypatch):
    fixture = _build_fixture()
    _install_fake_modules(monkeypatch, fixture)
    try:
        receipt = _run_apply(fixture)
        MODULE.validate_mutation_receipt(receipt)

        assert (
            fixture["production"] / fixture["update_path"]
        ).read_bytes() == fixture["update_target"]
        assert (
            fixture["production"] / fixture["create_path"]
        ).read_bytes() == fixture["create_target"]
        assert receipt["file_mutation_completed"] is True
        assert receipt["rollback_performed"] is False
        assert receipt["service_restart_performed"] is False
        assert receipt["paper_timer_enabled"] is False
        assert receipt["transaction_submitted"] is False
        assert receipt["live_capital_deployed"] is False
    finally:
        fixture["temp"].cleanup()


def test_post_write_failure_self_rolls_back_current_operation(monkeypatch):
    fixture = _build_fixture()
    original_fsync = MODULE._fsync_directory
    calls = {"count": 0}

    def fail_first_fsync(parent):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("forced post-write fsync failure")
        return original_fsync(parent)

    monkeypatch.setattr(MODULE, "_fsync_directory", fail_first_fsync)

    try:
        prepared = MODULE._prepare_operation(
            operation=fixture["operations"][0],
            source=fixture["source"],
            private_bundle=fixture["bundle"],
            backup_root=fixture["backup_root"],
            backup_module=BACKUP,
        )
        target = fixture["production"] / fixture["update_path"]

        with pytest.raises(OSError, match="forced post-write fsync failure"):
            MODULE._install_operation(
                prepared=prepared,
                production=fixture["production"],
                backup_module=BACKUP,
            )

        assert target.read_bytes() == fixture["old_payload"]
        assert stat_mode(target) == 0o640
        assert calls["count"] >= 2
    finally:
        fixture["temp"].cleanup()


def test_later_operation_failure_rolls_back_earlier_update(monkeypatch):
    fixture = _build_fixture()
    _install_fake_modules(monkeypatch, fixture)
    original_install = MODULE._install_operation

    def fail_second(*, prepared, production, backup_module):
        if prepared["operation"]["index"] == 1:
            raise ValueError("forced second operation failure")
        return original_install(
            prepared=prepared,
            production=production,
            backup_module=backup_module,
        )

    monkeypatch.setattr(MODULE, "_install_operation", fail_second)

    try:
        with pytest.raises(RuntimeError, match="were rolled back"):
            _run_apply(fixture)

        update_target = fixture["production"] / fixture["update_path"]
        create_target = fixture["production"] / fixture["create_path"]
        assert update_target.read_bytes() == fixture["old_payload"]
        assert stat_mode(update_target) == 0o640
        assert not create_target.exists()
    finally:
        fixture["temp"].cleanup()


def test_writer_rejects_wrong_execution_precheck_digest_before_write(monkeypatch):
    fixture = _build_fixture()
    _install_fake_modules(monkeypatch, fixture)
    paths = fixture["paths"]
    try:
        with pytest.raises(ValueError, match="digest pin mismatch"):
            MODULE.apply_preserved_file_mutations(
                repository=fixture["production"],
                source_tree=fixture["source"],
                private_bundle_report_path=paths["bundle-report.json"],
                handoff_path=paths["handoff.json"],
                plan_path=paths["plan.json"],
                gate_path=paths["gate.json"],
                mutation_review_path=paths["mutation-review.json"],
                evidence_package_path=paths["evidence.json"],
                backup_capture_path=paths["backup-capture.json"],
                authorization_review_path=paths["authorization-review.json"],
                authorization_request_path=paths["authorization-request.json"],
                signed_authorization_verification_path=paths["signed-verification.json"],
                signed_payload_path=paths["signed-payload.json"],
                signature_path=paths["signature.sig"],
                allowed_signers_path=paths["allowed-signers"],
                expected_allowed_signers_sha256="d" * 64,
                execution_precheck_path=paths["precheck.json"],
                expected_execution_precheck_sha256="e" * 64,
            )

        assert (
            fixture["production"] / fixture["update_path"]
        ).read_bytes() == fixture["old_payload"]
        assert not (
            fixture["production"] / fixture["create_path"]
        ).exists()
    finally:
        fixture["temp"].cleanup()


def test_immediate_recheck_rejects_drifted_update():
    fixture = _build_fixture()
    try:
        operation = fixture["operations"][0]
        prepared = MODULE._prepare_operation(
            operation=operation,
            source=fixture["source"],
            private_bundle=fixture["bundle"],
            backup_root=fixture["backup_root"],
            backup_module=BACKUP,
        )
        target = fixture["production"] / fixture["update_path"]
        target.write_bytes(b"drifted after precheck\n")

        with pytest.raises(ValueError, match="expected-current blob changed"):
            MODULE._install_operation(
                prepared=prepared,
                production=fixture["production"],
                backup_module=BACKUP,
            )
        assert target.read_bytes() == b"drifted after precheck\n"
    finally:
        fixture["temp"].cleanup()


def test_create_source_mode_must_be_explicitly_supported():
    fixture = _build_fixture()
    try:
        source = fixture["source"] / fixture["create_path"]
        os.chmod(source, 0o755)
        with pytest.raises(ValueError, match="create source mode is not 0644"):
            MODULE._prepare_operation(
                operation=fixture["operations"][1],
                source=fixture["source"],
                private_bundle=fixture["bundle"],
                backup_root=fixture["backup_root"],
                backup_module=BACKUP,
            )
    finally:
        fixture["temp"].cleanup()


def test_rehashed_receipt_cannot_claim_activation_side_effect():
    fixture = _build_fixture()
    try:
        result = {
            "index": 0,
            "layer": "STATE_READER",
            "operation": "UPDATE_PRESERVED_FILE",
            "path": fixture["update_path"],
            "expected_current_blob": _blob(fixture["old_payload"]),
            "before_blob": _blob(fixture["old_payload"]),
            "target_blob": _blob(fixture["update_target"]),
            "after_blob": _blob(fixture["update_target"]),
            "target_mode": 0o640,
            "immediate_expected_state_recheck_passed": True,
            "source_bytes_reverified": True,
            "rollback_material_reverified": True,
            "write_applied": True,
            "post_write_verified": True,
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
            "execution_precheck_sha256": "b" * 64,
            "production_repository": str(fixture["production"]),
            "approver_principal": "wyck@example.com",
            "approval_id": "01234567-89ab-4def-8123-456789abcdef",
            "approval_expires_at": _future_expiry(),
            "operation_results": [result],
            "operation_count": 1,
            "all_operations_applied": True,
            "preserved_file_mutation_authorization_present": True,
            "file_mutation_completed": True,
            "requires_post_mutation_validation": True,
            "rollback_performed": False,
            "production_repository_git_mutated": False,
            "service_restart_performed": True,
            "detector_cursor_moved": False,
            "paper_timer_enabled": False,
            "transaction_signed": False,
            "transaction_submitted": False,
            "live_capital_deployed": False,
        }
        receipt = {
            **identity,
            "receipt_sha256": hashlib.sha256(
                MODULE._canonical_bytes(identity)
            ).hexdigest(),
        }
        with pytest.raises(ValueError, match="service_restart_performed=false"):
            MODULE.validate_mutation_receipt(receipt)
    finally:
        fixture["temp"].cleanup()


def test_rehashed_update_receipt_cannot_drop_rollback_reverification():
    fixture = _build_fixture()
    try:
        result = {
            "index": 0,
            "layer": "STATE_READER",
            "operation": "UPDATE_PRESERVED_FILE",
            "path": fixture["update_path"],
            "expected_current_blob": _blob(fixture["old_payload"]),
            "before_blob": _blob(fixture["old_payload"]),
            "target_blob": _blob(fixture["update_target"]),
            "after_blob": _blob(fixture["update_target"]),
            "target_mode": 0o640,
            "immediate_expected_state_recheck_passed": True,
            "source_bytes_reverified": True,
            "rollback_material_reverified": False,
            "write_applied": True,
            "post_write_verified": True,
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
            "execution_precheck_sha256": "b" * 64,
            "production_repository": str(fixture["production"]),
            "approver_principal": "wyck@example.com",
            "approval_id": "01234567-89ab-4def-8123-456789abcdef",
            "approval_expires_at": _future_expiry(),
            "operation_results": [result],
            "operation_count": 1,
            "all_operations_applied": True,
            "preserved_file_mutation_authorization_present": True,
            "file_mutation_completed": True,
            "requires_post_mutation_validation": True,
            "rollback_performed": False,
            "production_repository_git_mutated": False,
            "service_restart_performed": False,
            "detector_cursor_moved": False,
            "paper_timer_enabled": False,
            "transaction_signed": False,
            "transaction_submitted": False,
            "live_capital_deployed": False,
        }
        receipt = {
            **identity,
            "receipt_sha256": hashlib.sha256(
                MODULE._canonical_bytes(identity)
            ).hexdigest(),
        }
        with pytest.raises(ValueError, match="rollback-material semantics mismatch"):
            MODULE.validate_mutation_receipt(receipt)
    finally:
        fixture["temp"].cleanup()


def test_writer_has_no_git_service_or_transaction_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "systemctl" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "solana" not in source.lower()
    assert "transaction_signed\": True" not in source
    assert "transaction_submitted\": True" not in source
    assert "live_capital_deployed\": True" not in source
    assert '"service_restart_performed": False' in source
    assert '"paper_timer_enabled": False' in source
