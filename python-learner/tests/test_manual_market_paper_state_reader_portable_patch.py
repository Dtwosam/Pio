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
    / "export_manual_market_paper_state_reader_candidate_patch.py"
)

SPEC = importlib.util.spec_from_file_location(
    "export_manual_market_paper_state_reader_candidate_patch",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reviewed_portable_patch_dependency_is_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_build_patch_is_small_unified_diff_with_reviewed_paths():
    target = b"one\ntwo\nthree\n"
    candidate = b"one\nTWO\nthree\nfour\n"

    patch = MODULE._build_patch(target, candidate)
    stats = MODULE._patch_stats(patch)

    text = patch.decode("utf-8")
    assert text.startswith(
        "--- a/rust-executor/src/state_reader.rs\n"
        "+++ b/rust-executor/src/state_reader.rs\n"
    )
    assert "+TWO" in text
    assert "-two" in text
    assert "+four" in text
    assert stats["sha256"] == hashlib.sha256(patch).hexdigest()
    assert stats["hunks"] == 1
    assert stats["added_lines"] == 2
    assert stats["removed_lines"] == 1


def test_empty_patch_fails_closed():
    payload = b"same\n"

    try:
        MODULE._build_patch(payload, payload)
    except ValueError as exc:
        assert "unexpectedly empty" in str(exc)
    else:
        raise AssertionError("empty candidate patch must fail closed")


def test_patch_output_must_be_new_file_under_var_tmp():
    path = Path("/var/tmp/pio-portable-patch-unit-test.patch")
    if path.exists():
        path.unlink()

    try:
        assert MODULE._safe_output_path(str(path)) == path
    finally:
        if path.exists():
            path.unlink()

    for invalid in (
        "relative.patch",
        "/tmp/candidate.patch",
        "/opt/pio/candidate.patch",
        "/var/tmp",
    ):
        try:
            MODULE._safe_output_path(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe patch path must fail: {invalid}")


def _synthetic_report():
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
        "reviewed_target_path": str(MODULE.STATE_READER_PATH),
        "reviewed_target_blob": MODULE.EXPECTED_TARGET_BLOB,
        "candidate_report_sha256": MODULE.EXPECTED_CANDIDATE_REPORT_SHA256,
        "candidate_git_blob": MODULE.EXPECTED_CANDIDATE_GIT_BLOB,
        "candidate_sha256": MODULE.EXPECTED_CANDIDATE_SHA256,
        "candidate_size": MODULE.EXPECTED_CANDIDATE_SIZE,
        "patch_sha256": "1" * 64,
        "patch_size": 1024,
        "patch_hunks": 7,
        "patch_added_lines": 14,
        "patch_removed_lines": 28,
        "patch_output_path": "/var/tmp/pio-state-reader-candidate.patch",
        "patch_output_under_var_tmp": True,
        "production_file_modified": False,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    return {
        **identity,
        "report_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_portable_patch_report_validation_accepts_sealed_non_authorizing_report():
    report = _synthetic_report()

    MODULE.validate_portable_patch_report(
        json.loads(json.dumps(report))
    )

    assert report["patch_hunks"] == 7
    assert report["production_file_modified"] is False
    assert report["mutation_authorized"] is False


def test_portable_patch_report_rejects_oversized_patch():
    report = _synthetic_report()
    report["patch_size"] = MODULE.MAX_PATCH_BYTES + 1
    identity = {
        field: report[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    report["report_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_portable_patch_report(report)
    except ValueError as exc:
        assert "exceeds byte bound" in str(exc)
    else:
        raise AssertionError("oversized patch must fail closed")


def test_tampered_portable_patch_report_fails_digest_validation():
    report = _synthetic_report()
    report["patch_hunks"] += 1

    try:
        MODULE.validate_portable_patch_report(report)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered patch report must fail closed")


def test_rehashed_portable_patch_report_cannot_authorize_mutation():
    report = _synthetic_report()
    report["mutation_authorized"] = True
    identity = {
        field: report[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    report["report_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_portable_patch_report(report)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_exporter_is_production_blind_and_writes_only_patch_output():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "systemctl" not in source
    assert "CARGO_BUILD_JOBS" not in source
    assert source.count(".write_bytes(") == 1
    assert "output.write_bytes(patch)" in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
