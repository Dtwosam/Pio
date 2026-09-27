from __future__ import annotations

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
    / "build_manual_market_paper_preserved_mutation_review.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_preserved_mutation_review",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _blob(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _operation_check(
    *,
    index: int,
    operation: str,
    path: str,
    source_origin: str,
    expected_current_blob: str | None,
    target_blob: str,
    bundle_sha: str | None,
    backup_required: bool,
):
    if operation == "CREATE_FILE":
        rollback_operation = "DELETE_CREATED_FILE"
        rollback_blob = None
    else:
        rollback_operation = "RESTORE_EXPECTED_BLOB"
        rollback_blob = expected_current_blob

    operation_payload = {
        "layer": (
            "STATE_READER"
            if path == "rust-executor/src/state_reader.rs"
            else "MARKET_PAPER_RUNTIME"
        ),
        "operation": operation,
        "path": path,
        "expected_current_blob": expected_current_blob,
        "target_blob": target_blob,
        "source_blob": target_blob,
        "backup_required": backup_required,
    }
    if operation == "UPDATE_PRESERVED_FILE":
        operation_payload["private_bundle_sha256"] = bundle_sha

    return {
        "index": index,
        "layer": operation_payload["layer"],
        "operation": operation,
        "path": path,
        "plan_operation_sha256": hashlib.sha256(
            MODULE._canonical_bytes(operation_payload)
        ).hexdigest(),
        "source_origin": source_origin,
        "expected_current_blob": expected_current_blob,
        "observed_current_blob": expected_current_blob,
        "current_state": (
            "ABSENT_AS_EXPECTED"
            if expected_current_blob is None
            else "BLOB_MATCH"
        ),
        "current_matches_expected": True,
        "target_blob": target_blob,
        "observed_source_blob": target_blob,
        "source_state": "BLOB_MATCH",
        "source_matches_target": True,
        "backup_required": backup_required,
        "rollback_operation": rollback_operation,
        "rollback_blob": rollback_blob,
        "rollback_recipe_complete": True,
        "private_bundle_sha256": bundle_sha,
        "operation_ready": True,
    }


def _sealed_review():
    bundle_sha = "a" * 64
    gate_sha = "b" * 64
    checks = [
        _operation_check(
            index=0,
            operation="UPDATE_PRESERVED_FILE",
            path="rust-executor/src/state_reader.rs",
            source_origin="PRIVATE_BUNDLE",
            expected_current_blob="1" * 40,
            target_blob="2" * 40,
            bundle_sha=bundle_sha,
            backup_required=True,
        ),
        _operation_check(
            index=1,
            operation="CREATE_FILE",
            path="python-learner/src/meteora_learner/example.py",
            source_origin="REVIEWED_SOURCE",
            expected_current_blob=None,
            target_blob="3" * 40,
            bundle_sha=None,
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
        "production_repository": "/opt/pio",
        "handoff_state_sha256": "4" * 64,
        "plan_sha256": "5" * 64,
        "private_bundle_sha256": bundle_sha,
        "saved_gate_sha256": gate_sha,
        "fresh_gate_sha256": gate_sha,
        "fresh_gate_matches_saved": True,
        "fresh_gate_ready": True,
        "operation_checks": checks,
        "operation_count": len(checks),
        "deployment_needed": True,
        "source_targets_match_plan": True,
        "current_files_match_plan": True,
        "rollback_recipe_complete": True,
        "backup_capture_required": True,
        "backup_material_captured": False,
        "review_ready": True,
        "candidate_content_included": False,
        "requires_fresh_execution_recheck": True,
        "requires_backup_capture_before_mutation": True,
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
        "review_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_reviewed_preserved_mutation_dependencies_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_preserved_operation_reads_private_bundle_and_builds_rollback(tmp_path):
    production = tmp_path / "production"
    reviewed = tmp_path / "reviewed"
    private = tmp_path / "private"
    relative = Path("rust-executor/src/state_reader.rs")
    for root in (production, reviewed, private):
        (root / relative.parent).mkdir(parents=True)

    current = b"production-local\n"
    candidate = b"preserved-candidate\n"
    (production / relative).write_bytes(current)
    (private / relative).write_bytes(candidate)
    (reviewed / relative).write_bytes(b"public-reviewed-target\n")

    bundle_sha = "a" * 64
    operation = {
        "layer": "STATE_READER",
        "operation": "UPDATE_PRESERVED_FILE",
        "path": str(relative),
        "expected_current_blob": _blob(current),
        "target_blob": _blob(candidate),
        "source_blob": _blob(candidate),
        "backup_required": True,
        "private_bundle_sha256": bundle_sha,
    }

    check = MODULE._operation_check(
        index=0,
        operation=operation,
        reviewed_source=reviewed,
        private_bundle=private,
        production=production,
        private_bundle_sha256=bundle_sha,
    )

    assert check["source_origin"] == "PRIVATE_BUNDLE"
    assert check["observed_source_blob"] == _blob(candidate)
    assert check["current_matches_expected"] is True
    assert check["source_matches_target"] is True
    assert check["rollback_operation"] == "RESTORE_EXPECTED_BLOB"
    assert check["rollback_blob"] == _blob(current)
    assert check["operation_ready"] is True


def test_standard_create_reads_reviewed_source_not_private_bundle(tmp_path):
    production = tmp_path / "production"
    reviewed = tmp_path / "reviewed"
    private = tmp_path / "private"
    relative = Path("python-learner/src/meteora_learner/example.py")
    production.mkdir()
    for root in (reviewed, private):
        (root / relative.parent).mkdir(parents=True)

    target = b"reviewed-standard\n"
    (reviewed / relative).write_bytes(target)
    (private / relative).write_bytes(b"different-private-content\n")

    operation = {
        "layer": "MARKET_PAPER_RUNTIME",
        "operation": "CREATE_FILE",
        "path": str(relative),
        "expected_current_blob": None,
        "target_blob": _blob(target),
        "source_blob": _blob(target),
        "backup_required": False,
    }

    check = MODULE._operation_check(
        index=0,
        operation=operation,
        reviewed_source=reviewed,
        private_bundle=private,
        production=production,
        private_bundle_sha256="a" * 64,
    )

    assert check["source_origin"] == "REVIEWED_SOURCE"
    assert check["observed_source_blob"] == _blob(target)
    assert check["current_state"] == "ABSENT_AS_EXPECTED"
    assert check["rollback_operation"] == "DELETE_CREATED_FILE"
    assert check["rollback_blob"] is None
    assert check["operation_ready"] is True


def test_sealed_preserved_mutation_review_validates():
    review = _sealed_review()

    MODULE.validate_preserved_mutation_review(
        json.loads(json.dumps(review))
    )

    assert review["review_ready"] is True
    assert review["backup_capture_required"] is True
    assert review["backup_material_captured"] is False
    assert review["mutation_authorized"] is False


def test_rehashed_gate_match_flag_cannot_disagree_with_gate_digests():
    review = _sealed_review()
    review["fresh_gate_sha256"] = "c" * 64
    identity = {
        field: review[field]
        for field in MODULE.REVIEW_IDENTITY_FIELDS
    }
    review["review_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preserved_mutation_review(review)
    except ValueError as exc:
        assert "fresh-gate match flag mismatch" in str(exc)
    else:
        raise AssertionError("gate-match contradiction must fail closed")


def test_rehashed_review_cannot_authorize_mutation():
    review = _sealed_review()
    review["mutation_authorized"] = True
    identity = {
        field: review[field]
        for field in MODULE.REVIEW_IDENTITY_FIELDS
    }
    review["review_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preserved_mutation_review(review)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("authorization flip must fail closed")


def test_rehashed_preserved_operation_cannot_claim_reviewed_source_origin():
    review = _sealed_review()
    review["operation_checks"][0]["source_origin"] = "REVIEWED_SOURCE"
    identity = {
        field: review[field]
        for field in MODULE.REVIEW_IDENTITY_FIELDS
    }
    review["review_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preserved_mutation_review(review)
    except ValueError as exc:
        assert "must source from private bundle" in str(exc)
    else:
        raise AssertionError("preserved source-origin drift must fail closed")


def test_rehashed_standard_operation_cannot_bind_private_bundle():
    review = _sealed_review()
    review["operation_checks"][1]["private_bundle_sha256"] = "a" * 64
    identity = {
        field: review[field]
        for field in MODULE.REVIEW_IDENTITY_FIELDS
    }
    review["review_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preserved_mutation_review(review)
    except ValueError as exc:
        assert "private-bundle digest must be null" in str(exc)
    else:
        raise AssertionError("standard private-bundle binding must fail closed")


def test_preserved_mutation_review_has_no_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "copy2(" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert "systemctl" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
