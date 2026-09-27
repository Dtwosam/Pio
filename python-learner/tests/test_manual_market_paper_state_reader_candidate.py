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
    / "build_manual_market_paper_state_reader_candidate.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_state_reader_candidate",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reviewed_candidate_dependency_is_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def _conflict(index, target_lines):
    expected = MODULE.EXPECTED_CONFLICTS[index - 1]
    return {
        "index": index,
        "conflict_sha256": expected["conflict_sha256"],
        "production_local": {
            "sha256": expected["production_local_sha256"],
            "lines": ["local"],
        },
        "reviewed_base": {
            "sha256": expected["reviewed_base_sha256"],
            "lines": ["base"],
        },
        "reviewed_target": {
            "sha256": expected["reviewed_target_sha256"],
            "lines": target_lines,
        },
    }


def test_resolver_chooses_reviewed_target_for_both_conflicts():
    conflicts = [
        _conflict(1, []),
        _conflict(2, ["target-two-a", "target-two-b"]),
    ]
    merged = b"""before
<<<<<<< production-local
local-one
||||||| reviewed-base
base-one
=======
>>>>>>> reviewed-target
middle
<<<<<<< production-local
local-two
||||||| reviewed-base
base-two
=======
target-two-a
target-two-b
>>>>>>> reviewed-target
after
"""

    resolved = MODULE._resolve_diff3_to_reviewed_target(
        merged,
        conflicts,
    )

    assert resolved == b"""before
middle
target-two-a
target-two-b
after
"""
    assert b"<<<<<<<" not in resolved
    assert b"|||||||" not in resolved
    assert b">>>>>>>" not in resolved
    assert b"local-one" not in resolved
    assert b"local-two" not in resolved


def test_resolver_fails_if_target_side_drifts():
    conflicts = [
        _conflict(1, []),
    ]
    merged = b"""<<<<<<< production-local
local
||||||| reviewed-base
base
=======
unexpected-target
>>>>>>> reviewed-target
"""

    try:
        MODULE._resolve_diff3_to_reviewed_target(
            merged,
            conflicts,
        )
    except ValueError as exc:
        assert "target side drifted" in str(exc)
    else:
        raise AssertionError("target-side drift must fail closed")


def test_resolver_fails_on_unexpected_extra_conflict():
    conflicts = [
        _conflict(1, []),
    ]
    merged = b"""<<<<<<< production-local
local
||||||| reviewed-base
base
=======
>>>>>>> reviewed-target
<<<<<<< production-local
local-two
||||||| reviewed-base
base-two
=======
target-two
>>>>>>> reviewed-target
"""

    try:
        MODULE._resolve_diff3_to_reviewed_target(
            merged,
            conflicts,
        )
    except ValueError as exc:
        assert "unexpected extra conflict" in str(exc)
    else:
        raise AssertionError("extra conflict must fail closed")


def test_live_binding_requires_exact_sealed_conflict_identity():
    evidence = {
        "evidence_sha256": MODULE.EXPECTED_HUNK_EVIDENCE_SHA256,
        "production_head": MODULE.EXPECTED_PRODUCTION_HEAD,
        "current_blob": MODULE.EXPECTED_CURRENT_BLOB,
        "target_blob": MODULE.EXPECTED_TARGET_BLOB,
        "conflicts": [],
    }
    for expected in MODULE.EXPECTED_CONFLICTS:
        evidence["conflicts"].append(
            {
                "index": expected["index"],
                "conflict_sha256": expected["conflict_sha256"],
                "production_local": {
                    "sha256": expected["production_local_sha256"],
                },
                "reviewed_base": {
                    "sha256": expected["reviewed_base_sha256"],
                },
                "reviewed_target": {
                    "sha256": expected["reviewed_target_sha256"],
                },
            }
        )

    MODULE._validate_live_conflict_binding(evidence)

    tampered = copy.deepcopy(evidence)
    tampered["conflicts"][1]["reviewed_target"]["sha256"] = "0" * 64
    try:
        MODULE._validate_live_conflict_binding(tampered)
    except ValueError as exc:
        assert "reviewed-target side changed" in str(exc)
    else:
        raise AssertionError("changed target-side evidence must fail closed")


def test_patch_stats_are_content_free_and_deterministic():
    before = b"one\ntwo\nthree\n"
    after = b"one\nTWO\nthree\nfour\n"

    first = MODULE._unified_patch_stats(before, after)
    second = MODULE._unified_patch_stats(before, after)

    assert first == second
    assert first["hunks"] == 1
    assert first["added_lines"] == 2
    assert first["removed_lines"] == 1
    assert set(first) == {
        "sha256",
        "size",
        "hunks",
        "added_lines",
        "removed_lines",
    }
    assert "TWO" not in json.dumps(first)


def test_candidate_output_must_be_new_file_under_var_tmp(tmp_path):
    valid = "/var/tmp/pio-candidate-unit-test.rs"
    path = Path(valid)
    if path.exists():
        path.unlink()
    try:
        assert MODULE._safe_output_path(valid) == path
    finally:
        if path.exists():
            path.unlink()

    for invalid in (
        "relative.rs",
        "/tmp/candidate.rs",
        "/opt/pio/candidate.rs",
        "/var/tmp",
    ):
        try:
            MODULE._safe_output_path(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe output path must fail: {invalid}")


def _synthetic_report():
    current = b"current\n"
    target = b"target\n"
    candidate = b"candidate\n"

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
        "repository": "/opt/pio",
        "production_head": MODULE.EXPECTED_PRODUCTION_HEAD,
        "source_hunk_evidence_sha256": MODULE.EXPECTED_HUNK_EVIDENCE_SHA256,
        "path": "rust-executor/src/state_reader.rs",
        "current_blob": MODULE.EXPECTED_CURRENT_BLOB,
        "target_blob": MODULE.EXPECTED_TARGET_BLOB,
        "resolutions": [
            {
                "index": item["index"],
                "conflict_sha256": item["conflict_sha256"],
                "resolution": "REVIEWED_TARGET",
            }
            for item in MODULE.EXPECTED_CONFLICTS
        ],
        "candidate_git_blob": MODULE._git_blob_sha_bytes(candidate),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "candidate_size": len(candidate),
        "candidate_diff_from_current": MODULE._unified_patch_stats(
            current,
            candidate,
        ),
        "candidate_diff_from_target": MODULE._unified_patch_stats(
            target,
            candidate,
        ),
        "output_path": "/var/tmp/pio-state-reader-candidate.rs",
        "output_under_var_tmp": True,
        "production_file_modified": False,
        "candidate_requires_isolated_validation": True,
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
        "report_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_candidate_report_validation_accepts_sealed_non_authorizing_report():
    report = _synthetic_report()

    MODULE.validate_candidate_report(
        json.loads(json.dumps(report))
    )

    assert report["production_file_modified"] is False
    assert report["candidate_requires_isolated_validation"] is True
    assert report["mutation_authorized"] is False


def test_tampered_candidate_report_fails_digest_validation():
    report = _synthetic_report()
    report["candidate_size"] += 1

    try:
        MODULE.validate_candidate_report(report)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered candidate report must fail closed")


def test_rehashed_candidate_report_cannot_authorize_mutation():
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
        MODULE.validate_candidate_report(report)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_candidate_tool_has_only_explicit_var_tmp_output_write():
    source = TOOL.read_text(encoding="utf-8")

    assert source.count(".write_bytes(") == 1
    assert "output.write_bytes(candidate)" in source
    assert "/var/tmp" in source
    assert "systemctl" not in source
    assert "shutil" not in source
    assert "copy2(" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
