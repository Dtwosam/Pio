from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_preserved_mutation_auth_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_preserved_mutation_auth_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BACKUP_SPEC = importlib.util.spec_from_file_location(
    "preserved_auth_request_test_backup_module",
    ROOT / MODULE.BACKUP_CAPTURE_TOOL,
)
assert BACKUP_SPEC is not None and BACKUP_SPEC.loader is not None
BACKUP = importlib.util.module_from_spec(BACKUP_SPEC)
sys.modules[BACKUP_SPEC.name] = BACKUP
BACKUP_SPEC.loader.exec_module(BACKUP)


def _operation(
    *,
    index: int,
    operation: str,
    path: str,
    backup_required: bool,
) -> dict:
    expected = ("1" * 40) if backup_required else None
    if operation == "UPDATE_PRESERVED_FILE":
        source_origin = "PRIVATE_BUNDLE"
        private_bundle = "2" * 64
    else:
        source_origin = "REVIEWED_SOURCE"
        private_bundle = None

    return {
        "index": index,
        "layer": (
            "STATE_READER"
            if operation == "UPDATE_PRESERVED_FILE"
            else "MARKET_PAPER_RUNTIME"
        ),
        "operation": operation,
        "path": path,
        "plan_operation_sha256": "3" * 64,
        "source_origin": source_origin,
        "expected_current_blob": expected,
        "target_blob": "4" * 40,
        "private_bundle_sha256": private_bundle,
        "backup_required": backup_required,
        "backup_relative_path": (
            f"files/{path}" if backup_required else None
        ),
        "backup_git_blob": expected if backup_required else None,
        "backup_sha256": "5" * 64 if backup_required else None,
        "backup_size": 123 if backup_required else None,
        "rollback_operation": (
            "RESTORE_EXPECTED_BLOB"
            if backup_required
            else "DELETE_CREATED_FILE"
        ),
        "rollback_blob": expected if backup_required else None,
    }


def _request() -> dict:
    operations = [
        _operation(
            index=0,
            operation="UPDATE_PRESERVED_FILE",
            path="rust-executor/src/state_reader.rs",
            backup_required=True,
        ),
        _operation(
            index=1,
            operation="CREATE_FILE",
            path="python-learner/src/meteora_learner/new_runtime.py",
            backup_required=False,
        ),
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
        "evidence_package_sha256": "6" * 64,
        "mutation_review_sha256": "7" * 64,
        "backup_capture_sha256": "8" * 64,
        "plan_sha256": "9" * 64,
        "private_bundle_sha256": "2" * 64,
        "production_repository": "/srv/pio",
        "authorization_scope": MODULE.AUTHORIZATION_SCOPE,
        "excluded_scopes": list(MODULE.EXCLUDED_SCOPES),
        "operations": operations,
        "operation_count": len(operations),
        "all_lineage_bound": True,
        "all_backup_material_reverified": True,
        "authorization_request_ready": True,
        "explicit_human_approval_required": True,
        "approval_artifact_present": False,
        "fresh_execution_recheck_required": True,
        "backup_material_must_remain_available": True,
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
        "request_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_reviewed_authorization_request_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_authorization_request_is_explicitly_non_authorizing():
    request = _request()

    MODULE.validate_authorization_request(
        json.loads(json.dumps(request))
    )

    assert request["authorization_request_ready"] is True
    assert request["explicit_human_approval_required"] is True
    assert request["approval_artifact_present"] is False
    assert request["mutation_authorized"] is False
    assert "SERVICE_RESTART" in request["excluded_scopes"]
    assert "LIVE_CAPITAL" in request["excluded_scopes"]


def test_rehashed_request_cannot_redirect_operation_path():
    request = _request()
    request["operations"][0]["path"] = "../escape"
    identity = {
        field: request[field]
        for field in MODULE.REQUEST_FIELDS
    }
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "path is unsafe" in str(exc)
    else:
        raise AssertionError("rehashed operation path traversal must fail closed")


def test_rehashed_request_cannot_redirect_backup_path():
    request = _request()
    request["operations"][0]["backup_relative_path"] = "files/../../escape"
    identity = {
        field: request[field]
        for field in MODULE.REQUEST_FIELDS
    }
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "path is unsafe" in str(exc)
    else:
        raise AssertionError("rehashed backup path traversal must fail closed")


def test_rehashed_request_cannot_change_preserved_source_origin():
    request = _request()
    request["operations"][0]["source_origin"] = "REVIEWED_SOURCE"
    identity = {
        field: request[field]
        for field in MODULE.REQUEST_FIELDS
    }
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "preserved source origin mismatch" in str(exc)
    else:
        raise AssertionError("rehashed source-origin change must fail closed")


def test_rehashed_request_cannot_authorize_mutation():
    request = _request()
    request["mutation_authorized"] = True
    identity = {
        field: request[field]
        for field in MODULE.REQUEST_FIELDS
    }
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_empty_request_is_rejected_even_if_resealed():
    request = _request()
    request["operations"] = []
    request["operation_count"] = 0
    identity = {
        field: request[field]
        for field in MODULE.REQUEST_FIELDS
    }
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "non-empty list" in str(exc)
    else:
        raise AssertionError("empty authorization request must fail closed")


def test_physical_backup_material_is_reverified():
    with tempfile.TemporaryDirectory(
        prefix="pio-auth-request-backup-test-",
        dir="/var/tmp",
    ) as tmp:
        backup_root = Path(tmp)
        backup_file = (
            backup_root
            / "files"
            / "rust-executor"
            / "src"
            / "state_reader.rs"
        )
        backup_file.parent.mkdir(parents=True)
        payload = b"rollback bytes\n"
        backup_file.write_bytes(payload)

        blob = MODULE._git_blob_sha_bytes(payload)
        sha = MODULE._sha256_bytes(payload)

        report = {
            "backup_dir": str(backup_root),
            "operation_backups": [
                {
                    "index": 0,
                    "path": "rust-executor/src/state_reader.rs",
                    "backup_required": True,
                    "backup_relative_path": (
                        "files/rust-executor/src/state_reader.rs"
                    ),
                    "backup_git_blob": blob,
                    "backup_sha256": sha,
                    "backup_size": len(payload),
                    "expected_current_blob": blob,
                    "rollback_blob": blob,
                },
                {
                    "index": 1,
                    "path": "python-learner/new.py",
                    "backup_required": False,
                    "backup_relative_path": None,
                    "backup_git_blob": None,
                    "backup_sha256": None,
                    "backup_size": None,
                    "expected_current_blob": None,
                    "rollback_blob": None,
                },
            ],
        }

        entries, verified = MODULE._verify_backup_material(
            backup_module=BACKUP,
            backup_report=report,
        )

        assert verified is True
        assert set(entries) == {0, 1}


def test_physical_backup_tampering_fails_closed():
    with tempfile.TemporaryDirectory(
        prefix="pio-auth-request-tamper-test-",
        dir="/var/tmp",
    ) as tmp:
        backup_root = Path(tmp)
        backup_file = backup_root / "files" / "x.py"
        backup_file.parent.mkdir(parents=True)
        backup_file.write_bytes(b"changed\n")

        expected_payload = b"expected\n"
        expected_blob = MODULE._git_blob_sha_bytes(expected_payload)
        report = {
            "backup_dir": str(backup_root),
            "operation_backups": [
                {
                    "index": 0,
                    "path": "x.py",
                    "backup_required": True,
                    "backup_relative_path": "files/x.py",
                    "backup_git_blob": expected_blob,
                    "backup_sha256": MODULE._sha256_bytes(expected_payload),
                    "backup_size": len(expected_payload),
                    "expected_current_blob": expected_blob,
                    "rollback_blob": expected_blob,
                }
            ],
        }

        try:
            MODULE._verify_backup_material(
                backup_module=BACKUP,
                backup_report=report,
            )
        except ValueError as exc:
            assert "backup material mismatch" in str(exc)
        else:
            raise AssertionError("tampered physical backup must fail closed")


def test_authorization_request_tool_has_no_production_or_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "/opt/pio" not in source
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert '"approval_artifact_present": False' in source
    assert '"mutation_authorized": False' in source
