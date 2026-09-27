from __future__ import annotations

import copy
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
    / "analyze_manual_market_paper_conflict_reconciliation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "analyze_manual_market_paper_conflict_reconciliation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reviewed_reconciliation_source_artifacts_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_nonoverlapping_local_and_reviewed_changes_merge_cleanly():
    base = b"alpha\nbeta\ngamma\n"
    current = b"alpha-local\nbeta\ngamma\n"
    target = b"alpha\nbeta\ngamma-reviewed\n"

    result = MODULE._analyze_file(
        path="example.txt",
        base=base,
        current=current,
        target=target,
        expected_base_blob=MODULE._git_blob_sha_bytes(base),
        expected_target_blob=MODULE._git_blob_sha_bytes(target),
    )

    assert result["merge_status"] == "CLEAN"
    assert result["merge_conflict_markers"] == 0
    assert result["candidate_git_blob"] is not None
    assert result["candidate_sha256"] is not None
    assert result["candidate_size"] is not None
    assert result["candidate_equals_current"] is False
    assert result["candidate_equals_target"] is False
    assert result["candidate_patch_from_current"] is not None
    assert result["candidate_content_emitted"] is False
    assert result["local_patch"]["hunks"] == 1
    assert result["reviewed_patch"]["hunks"] == 1


def test_overlapping_local_and_reviewed_changes_report_conflict():
    base = b"alpha\nbeta\ngamma\n"
    current = b"alpha\nbeta-local\ngamma\n"
    target = b"alpha\nbeta-reviewed\ngamma\n"

    result = MODULE._analyze_file(
        path="example.txt",
        base=base,
        current=current,
        target=target,
        expected_base_blob=MODULE._git_blob_sha_bytes(base),
        expected_target_blob=MODULE._git_blob_sha_bytes(target),
    )

    assert result["merge_status"] == "CONFLICT"
    assert result["merge_conflict_markers"] >= 1
    assert result["candidate_git_blob"] is None
    assert result["candidate_sha256"] is None
    assert result["candidate_size"] is None
    assert result["candidate_patch_from_current"] is None
    assert result["candidate_content_emitted"] is False


def test_merge_file_multiple_conflicts_are_valid_conflict_result():
    merged = (
        b"<<<<<<< current\nlocal-a\n||||||| base\nbase-a\n=======\n"
        b"target-a\n>>>>>>> target\n"
        b"middle\n"
        b"<<<<<<< current\nlocal-b\n||||||| base\nbase-b\n=======\n"
        b"target-b\n>>>>>>> target\n"
    )

    def runner(args, **kwargs):
        assert args[:3] == ["git", "merge-file", "-p"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=2,
            stdout=merged,
            stderr=b"",
        )

    result = MODULE._three_way_merge(
        current=b"local\n",
        base=b"base\n",
        target=b"target\n",
        runner=runner,
    )

    assert result["status"] == "CONFLICT"
    assert result["conflict_markers"] == 2
    assert result["candidate_git_blob"] is None
    assert result["candidate_sha256"] is None
    assert result["candidate_size"] is None


def test_merge_file_execution_failure_still_fails_closed():
    def runner(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=128,
            stdout=b"",
            stderr=b"fatal: merge-file failed",
        )

    try:
        MODULE._three_way_merge(
            current=b"local\n",
            base=b"base\n",
            target=b"target\n",
            runner=runner,
        )
    except ValueError as exc:
        assert "return code 128" in str(exc)
    else:
        raise AssertionError("merge-file execution error must fail closed")


def test_base_or_target_blob_mismatch_fails_closed():
    base = b"base\n"
    current = b"current\n"
    target = b"target\n"

    try:
        MODULE._analyze_file(
            path="example.txt",
            base=base,
            current=current,
            target=target,
            expected_base_blob="0" * 40,
            expected_target_blob=MODULE._git_blob_sha_bytes(target),
        )
    except ValueError as exc:
        assert "production-base blob mismatch" in str(exc)
    else:
        raise AssertionError("unexpected production base must fail closed")

    try:
        MODULE._analyze_file(
            path="example.txt",
            base=base,
            current=current,
            target=target,
            expected_base_blob=MODULE._git_blob_sha_bytes(base),
            expected_target_blob="1" * 40,
        )
    except ValueError as exc:
        assert "reviewed target blob mismatch" in str(exc)
    else:
        raise AssertionError("unexpected reviewed target must fail closed")


def test_patch_metadata_is_deterministic_and_content_free():
    before = b"one\ntwo\nthree\n"
    after = b"one\nTWO\nthree\nfour\n"

    patch_one = MODULE._unified_patch(
        from_name="base/x",
        to_name="current/x",
        before=before,
        after=after,
    )
    patch_two = MODULE._unified_patch(
        from_name="base/x",
        to_name="current/x",
        before=before,
        after=after,
    )

    assert patch_one == patch_two
    stats = MODULE._patch_stats(patch_one)
    assert stats["sha256"] == hashlib.sha256(patch_one).hexdigest()
    assert stats["size"] == len(patch_one)
    assert stats["hunks"] == 1
    assert stats["added_lines"] == 2
    assert stats["removed_lines"] == 1
    assert set(stats) == {
        "sha256",
        "size",
        "hunks",
        "added_lines",
        "removed_lines",
    }


def test_git_reads_use_only_per_command_safe_directory_override(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seen = []

    def runner(args, **kwargs):
        seen.append((tuple(args), kwargs))
        if "rev-parse" in args:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=MODULE.EXPECTED_PRODUCTION_HEAD + "\n",
                stderr="",
            )
        if "show" in args:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=b"bytes\n",
                stderr=b"",
            )
        raise AssertionError(f"unexpected command: {args}")

    head = MODULE._production_head(repo, runner=runner)
    payload = MODULE._git_show_bytes(
        repo,
        head,
        "path/file.py",
        runner=runner,
    )

    assert head == MODULE.EXPECTED_PRODUCTION_HEAD
    assert payload == b"bytes\n"
    for args, kwargs in seen:
        assert args[:2] == ("git", "-c")
        assert args[2] == f"safe.directory={repo}"
        assert "config" not in args
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False


def _synthetic_evidence(*, clean=True):
    base_research = b"a\nb\nc\n"
    current_research = b"a-local\nb\nc\n"
    target_research = b"a\nb\nc-target\n"

    base_state = b"x\ny\nz\n"
    current_state = b"x-local\ny\nz\n"
    target_state = (
        b"x\ny\nz-target\n"
        if clean
        else b"x-target\ny\nz\n"
    )

    research = MODULE._analyze_file(
        path=MODULE.RESEARCH_STORE,
        base=base_research,
        current=current_research,
        target=target_research,
        expected_base_blob=MODULE._git_blob_sha_bytes(base_research),
        expected_target_blob=MODULE._git_blob_sha_bytes(target_research),
    )
    state = MODULE._analyze_file(
        path=MODULE.STATE_READER,
        base=base_state,
        current=current_state,
        target=target_state,
        expected_base_blob=MODULE._git_blob_sha_bytes(base_state),
        expected_target_blob=MODULE._git_blob_sha_bytes(target_state),
    )

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
        "expected_production_head": MODULE.EXPECTED_PRODUCTION_HEAD,
        "files": [research, state],
        "all_merge_clean": all(
            item["merge_status"] == "CLEAN"
            for item in (research, state)
        ),
        "candidate_content_emitted": False,
        "requires_manual_candidate_review": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    return {
        **identity,
        "evidence_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_reconciliation_evidence_validation_accepts_sealed_clean_report():
    evidence = _synthetic_evidence(clean=True)

    MODULE.validate_reconciliation_evidence(
        json.loads(json.dumps(evidence))
    )
    assert evidence["all_merge_clean"] is True
    assert evidence["mutation_authorized"] is False
    assert evidence["candidate_content_emitted"] is False
    assert evidence["requires_manual_candidate_review"] is True


def test_reconciliation_evidence_validation_accepts_conflict_report():
    evidence = _synthetic_evidence(clean=False)

    MODULE.validate_reconciliation_evidence(
        json.loads(json.dumps(evidence))
    )
    assert evidence["all_merge_clean"] is False
    assert any(
        item["merge_status"] == "CONFLICT"
        for item in evidence["files"]
    )


def test_tampered_reconciliation_evidence_is_rejected():
    evidence = _synthetic_evidence(clean=True)
    tampered = copy.deepcopy(evidence)
    tampered["files"][0]["current_size"] += 1

    try:
        MODULE.validate_reconciliation_evidence(tampered)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered reconciliation evidence must fail closed")


def test_rehashed_reconciliation_cannot_authorize_mutation():
    evidence = _synthetic_evidence(clean=True)
    evidence["mutation_authorized"] = True
    identity = {
        field: evidence[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    evidence["evidence_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_reconciliation_evidence(evidence)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_reconciliation_tool_has_no_production_mutation_or_service_path():
    source = TOOL.read_text(encoding="utf-8")

    assert "TemporaryDirectory" in source
    assert "git", "merge-file" in source
    assert "systemctl" not in source
    assert "shutil" not in source
    assert "copy2(" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
    assert "candidate_content_emitted" in source
