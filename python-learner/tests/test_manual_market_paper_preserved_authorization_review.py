from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_manual_market_paper_preserved_authorization_review.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_preserved_authorization_review",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _load_backup_module():
    spec = importlib.util.spec_from_file_location(
        "authorization_review_test_backup_capture",
        ROOT / MODULE.BACKUP_CAPTURE_TOOL,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BACKUP = _load_backup_module()


def _operation(*, index=0, operation="UPDATE_FILE", path="x.py", blob=None):
    blob = blob or ("1" * 40)
    return {
        "index": index,
        "layer": "PHASE2_SHARED_PREREQUISITES",
        "operation": operation,
        "path": path,
        "plan_operation_sha256": "2" * 64,
        "source_origin": "PRIVATE_BUNDLE",
        "expected_current_blob": None if operation == "CREATE_FILE" else blob,
        "observed_current_blob": None if operation == "CREATE_FILE" else blob,
        "current_state": (
            "ABSENT_AS_EXPECTED" if operation == "CREATE_FILE" else "BLOB_READ"
        ),
        "current_matches_expected": True,
        "target_blob": "3" * 40,
        "observed_source_blob": "3" * 40,
        "source_state": "BLOB_READ",
        "source_matches_target": True,
        "backup_required": operation != "CREATE_FILE",
        "rollback_operation": (
            "DELETE_CREATED_FILE"
            if operation == "CREATE_FILE"
            else "RESTORE_EXPECTED_BLOB"
        ),
        "rollback_blob": None if operation == "CREATE_FILE" else blob,
        "rollback_recipe_complete": True,
        "private_bundle_sha256": "4" * 64,
        "operation_ready": True,
    }


def _backup_entry(
    *,
    index=0,
    operation="UPDATE_FILE",
    path="x.py",
    blob=None,
    relative_path="files/x.py",
    sha=None,
    size=None,
    mode=0o600,
):
    blob = blob or ("1" * 40)
    required = operation != "CREATE_FILE"
    return {
        "index": index,
        "layer": "PHASE2_SHARED_PREREQUISITES",
        "operation": operation,
        "path": path,
        "plan_operation_sha256": "2" * 64,
        "backup_required": required,
        "expected_current_blob": None if not required else blob,
        "rollback_operation": (
            "DELETE_CREATED_FILE"
            if operation == "CREATE_FILE"
            else "RESTORE_EXPECTED_BLOB"
        ),
        "rollback_blob": None if not required else blob,
        "production_state": (
            "ABSENT_AS_EXPECTED" if not required else "BLOB_READ"
        ),
        "production_observed_blob": None if not required else blob,
        "post_capture_observed_blob": None if not required else blob,
        "production_matches_expected": True,
        "post_capture_matches_expected": True,
        "backup_materialized": required,
        "backup_relative_path": relative_path if required else None,
        "backup_git_blob": blob if required else None,
        "backup_sha256": sha if required else None,
        "backup_size": size if required else None,
        "backup_mode": mode if required else None,
        "backup_matches_expected": True,
        "entry_ready": True,
    }


def _ready_scope_entry(index=0):
    return {
        "index": index,
        "layer": "PHASE2_SHARED_PREREQUISITES",
        "operation": "UPDATE_FILE",
        "path": f"file-{index}.py",
        "plan_operation_sha256": "2" * 64,
        "expected_current_blob": "1" * 40,
        "target_blob": "3" * 40,
        "rollback_operation": "RESTORE_EXPECTED_BLOB",
        "rollback_blob": "1" * 40,
        "backup_required": True,
        "backup_relative_path": f"files/file-{index}.py",
        "backup_git_blob": "1" * 40,
        "backup_sha256": "5" * 64,
        "backup_size": 10,
        "backup_mode": 0o600,
        "backup_observed_state": "BLOB_READ",
        "backup_observed_blob": "1" * 40,
        "backup_observed_sha256": "5" * 64,
        "backup_observed_size": 10,
        "backup_observed_mode": 0o600,
        "backup_integrity_matches": True,
        "operation_ready": True,
    }


def _report(*, ready=True, backup_private=True, fresh_matches=True):
    scope = [_ready_scope_entry()]
    all_scope = all(x["operation_ready"] for x in scope)
    backup_integrity = backup_private and all(
        x["backup_integrity_matches"] for x in scope
    )
    expected_ready = bool(
        fresh_matches
        and True
        and True
        and all_scope
        and backup_integrity
    )
    assert expected_ready is ready

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
        "evidence_package_sha256": "6" * 64,
        "saved_mutation_review_sha256": "7" * 64,
        "fresh_mutation_review_sha256": "7" * 64,
        "backup_capture_sha256": "8" * 64,
        "plan_sha256": "9" * 64,
        "private_bundle_sha256": "a" * 64,
        "production_repository": "/srv/pio",
        "backup_dir": "/var/tmp/pio-backups.unit",
        "backup_dir_private": backup_private,
        "fresh_review_matches_saved": fresh_matches,
        "fresh_review_ready": True,
        "backup_capture_ready": True,
        "operation_scope": scope,
        "operation_count": len(scope),
        "all_scope_operations_ready": all_scope,
        "backup_integrity_ready": backup_integrity,
        "authorization_review_ready": ready,
        "authorization_granted": False,
        "requires_explicit_human_authorization": True,
        "requires_fresh_execution_recheck": True,
        "requires_separate_mutation_authorization": True,
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
        "authorization_review_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_reviewed_authorization_artifacts_are_exactly_pinned():
    source = ROOT
    _, _, _ = MODULE._verify_reviewed_source(source)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(source / relative) == expected


def test_materialized_backup_is_re_read_and_verified(tmp_path):
    payload = b"rollback-bytes\n"
    blob = MODULE._git_blob_sha_bytes(payload)
    sha = MODULE._sha256_bytes(payload)

    backup_root = tmp_path / "backup"
    target = backup_root / "files" / "x.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    os.chmod(target, 0o600)

    entry = MODULE._scope_entry(
        review_operation=_operation(blob=blob),
        backup_entry=_backup_entry(
            blob=blob,
            sha=sha,
            size=len(payload),
        ),
        backup_root=backup_root,
        backup_module=BACKUP,
    )

    assert entry["backup_observed_state"] == "BLOB_READ"
    assert entry["backup_observed_blob"] == blob
    assert entry["backup_observed_sha256"] == sha
    assert entry["backup_observed_size"] == len(payload)
    assert entry["backup_observed_mode"] == 0o600
    assert entry["backup_integrity_matches"] is True
    assert entry["operation_ready"] is True


def test_tampered_backup_bytes_fail_integrity_without_becoming_ready(tmp_path):
    original = b"rollback-bytes\n"
    tampered = b"tampered\n"
    blob = MODULE._git_blob_sha_bytes(original)
    sha = MODULE._sha256_bytes(original)

    backup_root = tmp_path / "backup"
    target = backup_root / "files" / "x.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(tampered)
    os.chmod(target, 0o600)

    entry = MODULE._scope_entry(
        review_operation=_operation(blob=blob),
        backup_entry=_backup_entry(
            blob=blob,
            sha=sha,
            size=len(original),
        ),
        backup_root=backup_root,
        backup_module=BACKUP,
    )

    assert entry["backup_integrity_matches"] is False
    assert entry["operation_ready"] is False


def test_create_operation_requires_no_backup_material(tmp_path):
    entry = MODULE._scope_entry(
        review_operation=_operation(operation="CREATE_FILE"),
        backup_entry=_backup_entry(operation="CREATE_FILE"),
        backup_root=tmp_path,
        backup_module=BACKUP,
    )

    assert entry["backup_required"] is False
    assert entry["backup_observed_state"] == "NOT_REQUIRED"
    assert entry["backup_integrity_matches"] is True
    assert entry["operation_ready"] is True


def test_ready_authorization_review_validates_but_never_grants_authorization():
    report = _report()

    MODULE.validate_authorization_review(
        json.loads(json.dumps(report))
    )

    assert report["authorization_review_ready"] is True
    assert report["authorization_granted"] is False
    assert report["requires_explicit_human_authorization"] is True
    assert report["mutation_authorized"] is False


def test_nonprivate_backup_directory_blocks_review_readiness():
    report = _report(
        ready=False,
        backup_private=False,
        fresh_matches=True,
    )

    MODULE.validate_authorization_review(report)

    assert report["backup_integrity_ready"] is False
    assert report["authorization_review_ready"] is False


def test_fresh_review_mismatch_blocks_authorization_review():
    report = _report(
        ready=False,
        backup_private=True,
        fresh_matches=False,
    )

    MODULE.validate_authorization_review(report)

    assert report["fresh_review_matches_saved"] is False
    assert report["authorization_review_ready"] is False


def test_rehashed_report_cannot_grant_authorization():
    report = _report()
    report["authorization_granted"] = True
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["authorization_review_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_authorization_review(report)
    except ValueError as exc:
        assert "must not grant authorization" in str(exc)
    else:
        raise AssertionError("rehashed authorization grant must fail closed")


def test_rehashed_report_cannot_enable_mutation():
    report = _report()
    report["mutation_authorized"] = True
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["authorization_review_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_authorization_review(report)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed mutation authorization must fail closed")


def test_authorization_review_has_no_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert ".write_text(" not in source
    assert ".write_bytes(" not in source
    assert "shutil" not in source
    assert "os.replace" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert "systemctl" not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply_guarded_patch" not in source
