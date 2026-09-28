from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_preserved_human_authorization_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_preserved_human_authorization_request",
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


def _request() -> dict:
    operations = [
        _operation(),
        _operation(
            index=1,
            operation="CREATE_FILE",
            path="python-learner/src/meteora_learner/runtime_overlay.py",
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
        "authorization_review_sha256": "5" * 64,
        "production_repository": "/opt/pio",
        "backup_dir": "/var/tmp/pio-preserved-backups.test",
        "authorization_scope": MODULE.AUTHORIZATION_SCOPE,
        "excluded_scopes": list(MODULE.EXCLUDED_SCOPES),
        "operations": operations,
        "operation_count": len(operations),
        "authorization_request_ready": True,
        "approval_artifact_present": False,
        "authorization_granted": False,
        "explicit_human_authorization_required": True,
        "fresh_execution_recheck_required": True,
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
        "request_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_terminal_authorization_tool_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_request_is_explicitly_non_authorizing():
    request = _request()

    MODULE.validate_authorization_request(
        json.loads(json.dumps(request))
    )

    assert request["authorization_request_ready"] is True
    assert request["approval_artifact_present"] is False
    assert request["authorization_granted"] is False
    assert request["mutation_authorized"] is False
    assert request["fresh_execution_recheck_required"] is True
    assert "LIVE_CAPITAL" in request["excluded_scopes"]
    assert "BROAD_GIT_OPERATIONS" in request["excluded_scopes"]


def test_rehashed_request_cannot_grant_authorization():
    request = _request()
    request["authorization_granted"] = True
    _reseal(request)

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "must not grant authorization" in str(exc)
    else:
        raise AssertionError("rehashed authorization grant must fail closed")


def test_rehashed_request_cannot_authorize_mutation():
    request = _request()
    request["mutation_authorized"] = True
    _reseal(request)

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed mutation authorization must fail closed")


def test_rehashed_request_cannot_redirect_operation_path():
    request = _request()
    request["operations"][0]["path"] = "../escape"
    _reseal(request)

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "path is unsafe" in str(exc)
    else:
        raise AssertionError("rehashed path traversal must fail closed")


def test_rehashed_request_cannot_change_review_binding_to_invalid_digest():
    request = _request()
    request["authorization_review_sha256"] = "not-a-digest"
    _reseal(request)

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "authorization_review_sha256 is invalid" in str(exc)
    else:
        raise AssertionError("invalid terminal review binding must fail closed")


def test_non_backup_operation_must_be_create():
    request = _request()
    request["operations"][1]["operation"] = "UPDATE_FILE"
    _reseal(request)

    try:
        MODULE.validate_authorization_request(request)
    except ValueError as exc:
        assert "non-backup operation mismatch" in str(exc)
    else:
        raise AssertionError("non-backup update must fail closed")


def test_builder_binds_exact_terminal_review_digest(monkeypatch):
    class FakeReviewModule:
        @staticmethod
        def validate_authorization_review(review: dict) -> None:
            assert review["authorization_review_ready"] is True

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_authorization_module",
        lambda source: FakeReviewModule,
    )

    review_scope = [
        {
            **_operation(),
            "backup_observed_state": "BLOB_READ",
            "backup_observed_blob": "1" * 40,
            "backup_observed_sha256": "4" * 64,
            "backup_observed_size": 123,
            "backup_observed_mode": 0o644,
            "backup_integrity_matches": True,
            "operation_ready": True,
        }
    ]
    review = {
        "authorization_review_ready": True,
        "authorization_review_sha256": "a" * 64,
        "production_repository": "/opt/pio",
        "backup_dir": "/var/tmp/pio-preserved-backups.test",
        "operation_scope": review_scope,
        "authorization_granted": False,
        "requires_explicit_human_authorization": True,
        "requires_fresh_execution_recheck": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }

    with tempfile.TemporaryDirectory() as tmp:
        review_path = Path(tmp) / "authorization-review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        request = MODULE.build_authorization_request(
            source_tree=ROOT,
            authorization_review_path=review_path,
        )

    MODULE.validate_authorization_request(request)
    assert request["authorization_review_sha256"] == "a" * 64
    assert request["operations"][0]["target_blob"] == "3" * 40
    assert request["approval_artifact_present"] is False
    assert request["authorization_granted"] is False


def test_authorization_request_tool_has_no_production_or_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert '"approval_artifact_present": False' in source
    assert '"authorization_granted": False' in source
    assert '"mutation_authorized": False' in source
