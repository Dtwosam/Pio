from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_research_store_candidate.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_research_store_candidate",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reviewed_research_store_candidate_dependencies_are_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_patch_stats_are_deterministic():
    before = b"one\ntwo\nthree\n"
    after = b"one\nTWO\nthree\nfour\n"

    first = MODULE._unified_patch_stats(before, after)
    second = MODULE._unified_patch_stats(before, after)

    assert first == second
    assert first["hunks"] == 1
    assert first["added_lines"] == 2
    assert first["removed_lines"] == 1
    assert len(first["sha256"]) == 64


def test_output_must_be_new_file_under_var_tmp():
    path = Path("/var/tmp/pio-research-store-candidate-unit-test.py")
    if path.exists():
        path.unlink()

    try:
        assert MODULE._safe_output_path(str(path)) == path
    finally:
        if path.exists():
            path.unlink()

    for invalid in (
        "relative.py",
        "/tmp/candidate.py",
        "/opt/pio/candidate.py",
        "/var/tmp",
    ):
        try:
            MODULE._safe_output_path(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe candidate path must fail: {invalid}")


def _synthetic_report():
    candidate = b"candidate\n"
    current = b"current\n"
    target = b"target\n"

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
        "path": str(MODULE.RESEARCH_STORE_PATH),
        "base_blob": MODULE.EXPECTED_BASE_BLOB,
        "current_blob": MODULE.EXPECTED_CURRENT_BLOB,
        "target_blob": MODULE.EXPECTED_TARGET_BLOB,
        "candidate_git_blob": MODULE.EXPECTED_CANDIDATE_BLOB,
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
        "output_path": "/var/tmp/pio-research-store-candidate.py",
        "output_under_var_tmp": True,
        "merge_status": "CLEAN",
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


def test_candidate_report_accepts_sealed_non_authorizing_result():
    report = _synthetic_report()

    MODULE.validate_candidate_report(
        json.loads(json.dumps(report))
    )

    assert report["candidate_git_blob"] == MODULE.EXPECTED_CANDIDATE_BLOB
    assert report["merge_status"] == "CLEAN"
    assert report["mutation_authorized"] is False


def test_rehashed_candidate_report_cannot_redirect_path():
    report = _synthetic_report()
    report["path"] = "python-learner/src/meteora_learner/storage.py"
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
        assert "path scope mismatch" in str(exc)
    else:
        raise AssertionError("rehashed path redirect must fail closed")


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


def test_production_head_read_uses_per_command_safe_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seen = []

    def runner(args, **kwargs):
        seen.append(tuple(args))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=MODULE.EXPECTED_PRODUCTION_HEAD + "\n",
            stderr="",
        )

    head = MODULE._production_head(repo, runner=runner)

    assert head == MODULE.EXPECTED_PRODUCTION_HEAD
    assert seen == [
        (
            "git",
            "-c",
            f"safe.directory={repo}",
            "rev-parse",
            "HEAD",
        )
    ]


def test_candidate_tool_only_writes_explicit_var_tmp_output():
    source = TOOL.read_text(encoding="utf-8")

    assert source.count(".write_bytes(") == 1
    assert "output.write_bytes(candidate)" in source
    assert "systemctl" not in source
    assert "shutil" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
