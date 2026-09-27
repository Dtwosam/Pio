from __future__ import annotations

import copy
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
    / "capture_manual_market_paper_preserved_backups.py"
)

SPEC = importlib.util.spec_from_file_location(
    "capture_manual_market_paper_preserved_backups",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _blob(payload: bytes) -> str:
    return MODULE._git_blob_sha_bytes(payload)


def _update_operation(path: str, payload: bytes, index: int = 0) -> dict:
    blob = _blob(payload)
    return {
        "index": index,
        "layer": "PHASE2_SHARED_PREREQUISITES",
        "operation": "UPDATE_FILE",
        "path": path,
        "plan_operation_sha256": "1" * 64,
        "source_origin": "REVIEWED_SOURCE",
        "expected_current_blob": blob,
        "observed_current_blob": blob,
        "current_state": "BLOB_MATCH",
        "current_matches_expected": True,
        "target_blob": "2" * 40,
        "observed_source_blob": "2" * 40,
        "source_state": "BLOB_MATCH",
        "source_matches_target": True,
        "backup_required": True,
        "rollback_operation": "RESTORE_EXPECTED_BLOB",
        "rollback_blob": blob,
        "rollback_recipe_complete": True,
        "private_bundle_sha256": None,
        "operation_ready": True,
    }


def _create_operation(path: str, index: int = 0) -> dict:
    return {
        "index": index,
        "layer": "MARKET_PAPER_RUNTIME",
        "operation": "CREATE_FILE",
        "path": path,
        "plan_operation_sha256": "3" * 64,
        "source_origin": "REVIEWED_SOURCE",
        "expected_current_blob": None,
        "observed_current_blob": None,
        "current_state": "ABSENT_AS_EXPECTED",
        "current_matches_expected": True,
        "target_blob": "4" * 40,
        "observed_source_blob": "4" * 40,
        "source_state": "BLOB_MATCH",
        "source_matches_target": True,
        "backup_required": False,
        "rollback_operation": "DELETE_CREATED_FILE",
        "rollback_blob": None,
        "rollback_recipe_complete": True,
        "private_bundle_sha256": None,
        "operation_ready": True,
    }


def _synthetic_entry(*, backup_required: bool, index: int) -> dict:
    if backup_required:
        expected = "5" * 40
        return {
            "index": index,
            "layer": "PHASE2_SHARED_PREREQUISITES",
            "operation": "UPDATE_FILE",
            "path": f"python-learner/file-{index}.py",
            "plan_operation_sha256": "6" * 64,
            "backup_required": True,
            "expected_current_blob": expected,
            "rollback_operation": "RESTORE_EXPECTED_BLOB",
            "rollback_blob": expected,
            "production_state": "BLOB_READ",
            "production_observed_blob": expected,
            "post_capture_observed_blob": expected,
            "production_matches_expected": True,
            "post_capture_matches_expected": True,
            "backup_materialized": True,
            "backup_relative_path": f"files/python-learner/file-{index}.py",
            "backup_git_blob": expected,
            "backup_sha256": "7" * 64,
            "backup_size": 123,
            "backup_mode": 0o644,
            "backup_matches_expected": True,
            "entry_ready": True,
        }

    return {
        "index": index,
        "layer": "MARKET_PAPER_RUNTIME",
        "operation": "CREATE_FILE",
        "path": f"python-learner/new-{index}.py",
        "plan_operation_sha256": "8" * 64,
        "backup_required": False,
        "expected_current_blob": None,
        "rollback_operation": "DELETE_CREATED_FILE",
        "rollback_blob": None,
        "production_state": "ABSENT_AS_EXPECTED",
        "production_observed_blob": None,
        "post_capture_observed_blob": None,
        "production_matches_expected": True,
        "post_capture_matches_expected": True,
        "backup_materialized": False,
        "backup_relative_path": None,
        "backup_git_blob": None,
        "backup_sha256": None,
        "backup_size": None,
        "backup_mode": None,
        "backup_matches_expected": True,
        "entry_ready": True,
    }


def _synthetic_report() -> dict:
    entries = [
        _synthetic_entry(backup_required=True, index=0),
        _synthetic_entry(backup_required=False, index=1),
    ]
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
        "evidence_package_sha256": "9" * 64,
        "mutation_review_sha256": "a" * 64,
        "plan_sha256": "b" * 64,
        "private_bundle_sha256": "c" * 64,
        "production_repository": "/srv/pio",
        "backup_dir": "/var/tmp/pio-backups.unit",
        "backup_dir_under_var_tmp": True,
        "operation_backups": entries,
        "operation_count": len(entries),
        "backup_required_count": 1,
        "backup_materialized_count": 1,
        "all_current_files_match_expected": True,
        "all_required_backups_materialized": True,
        "all_backup_blobs_match_expected": True,
        "rollback_material_complete": True,
        "backup_capture_ready": True,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "requires_fresh_execution_recheck": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    return {
        **identity,
        "backup_capture_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_reviewed_backup_capture_dependencies_are_exactly_pinned():
    source = ROOT
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_update_capture_copies_exact_bytes_without_mutating_source(tmp_path):
    production = tmp_path / "production"
    backup = tmp_path / "backup"
    production.mkdir()
    backup.mkdir()

    relative = Path("python-learner/src/example.py")
    target = production / relative
    target.parent.mkdir(parents=True)
    payload = b"important production-local bytes\n"
    target.write_bytes(payload)
    target.chmod(0o640)

    operation = _update_operation(relative.as_posix(), payload)
    before = target.read_bytes()

    entry = MODULE._capture_entry(
        operation=operation,
        production=production,
        backup_root=backup,
    )

    assert entry["entry_ready"] is True
    assert entry["backup_materialized"] is True
    assert entry["backup_git_blob"] == _blob(payload)
    assert entry["production_observed_blob"] == _blob(payload)
    assert entry["post_capture_observed_blob"] == _blob(payload)
    assert entry["backup_size"] == len(payload)
    assert entry["backup_mode"] == 0o640
    assert (backup / entry["backup_relative_path"]).read_bytes() == payload
    assert target.read_bytes() == before


def test_create_capture_requires_absence_and_materializes_no_backup(tmp_path):
    production = tmp_path / "production"
    backup = tmp_path / "backup"
    (production / "python-learner").mkdir(parents=True)
    backup.mkdir()

    entry = MODULE._capture_entry(
        operation=_create_operation("python-learner/new.py"),
        production=production,
        backup_root=backup,
    )

    assert entry["entry_ready"] is True
    assert entry["production_state"] == "ABSENT_AS_EXPECTED"
    assert entry["backup_materialized"] is False
    assert entry["backup_relative_path"] is None
    assert list(backup.rglob("*")) == []


def test_create_capture_fails_when_target_appears(tmp_path):
    production = tmp_path / "production"
    backup = tmp_path / "backup"
    target = production / "python-learner/new.py"
    target.parent.mkdir(parents=True)
    target.write_text("unexpected\n", encoding="utf-8")
    backup.mkdir()

    entry = MODULE._capture_entry(
        operation=_create_operation("python-learner/new.py"),
        production=production,
        backup_root=backup,
    )

    assert entry["entry_ready"] is False
    assert entry["production_state"] == "UNEXPECTED_PRESENT"


def test_update_capture_rejects_symlink_target(tmp_path):
    production = tmp_path / "production"
    backup = tmp_path / "backup"
    real = production / "real.py"
    relative = Path("python-learner/src/example.py")
    target = production / relative
    target.parent.mkdir(parents=True)
    real.write_text("real\n", encoding="utf-8")
    target.symlink_to(real)
    backup.mkdir()

    operation = _update_operation(relative.as_posix(), b"real\n")
    entry = MODULE._capture_entry(
        operation=operation,
        production=production,
        backup_root=backup,
    )

    assert entry["entry_ready"] is False
    assert entry["production_state"] == "TARGET_SYMLINK"
    assert entry["backup_materialized"] is False


def test_update_capture_rejects_symlink_parent(tmp_path):
    production = tmp_path / "production"
    backup = tmp_path / "backup"
    outside = tmp_path / "outside"
    outside.mkdir()
    production.mkdir()
    (production / "linked").symlink_to(outside, target_is_directory=True)
    backup.mkdir()

    operation = _update_operation("linked/example.py", b"x\n")
    entry = MODULE._capture_entry(
        operation=operation,
        production=production,
        backup_root=backup,
    )

    assert entry["entry_ready"] is False
    assert entry["production_state"] == "PARENT_SYMLINK"
    assert entry["backup_materialized"] is False


def test_backup_report_validates_ready_non_authorizing_capture():
    report = _synthetic_report()

    MODULE.validate_backup_capture(json.loads(json.dumps(report)))

    assert report["backup_capture_ready"] is True
    assert report["backup_required_count"] == 1
    assert report["backup_materialized_count"] == 1
    assert report["mutation_authorized"] is False


def test_tampered_backup_report_fails_digest_validation():
    report = _synthetic_report()
    report["operation_backups"][0]["backup_size"] += 1

    try:
        MODULE.validate_backup_capture(report)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered backup report must fail closed")


def test_rehashed_backup_report_cannot_authorize_mutation():
    report = _synthetic_report()
    report["mutation_authorized"] = True
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["backup_capture_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_backup_capture(report)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_safe_backup_directory_is_limited_to_new_var_tmp_path():
    candidate = Path("/var/tmp/pio-preserved-backups-unit-test-never-create")
    if candidate.exists():
        __import__("shutil").rmtree(candidate)

    assert MODULE._safe_backup_dir(candidate) == candidate

    for invalid in (
        "relative/backups",
        "/tmp/backups",
        "/srv/backups",
        "/var/tmp",
    ):
        try:
            MODULE._safe_backup_dir(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe backup path must fail: {invalid}")


def test_backup_capture_has_no_production_mutation_or_service_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "systemctl" not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
    assert "O_RDONLY" in source
    assert "O_NOFOLLOW" in source
    assert '"production_file_modified": False' in source
    assert '"production_repository_git_mutated": False' in source
    assert '"mutation_authorized": False' in source
