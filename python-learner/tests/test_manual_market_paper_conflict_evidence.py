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
    / "collect_manual_market_paper_conflict_evidence.py"
)

SPEC = importlib.util.spec_from_file_location(
    "collect_manual_market_paper_conflict_evidence",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _completed(args, *, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=args,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_reviewed_conflict_evidence_source_artifacts_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_git_probe_recovers_read_only_from_safe_directory_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seen = []

    def runner(args, **kwargs):
        seen.append(tuple(args))
        if args[:2] == ["git", "rev-parse"]:
            return _completed(
                args,
                stderr="fatal: detected dubious ownership in repository\n",
                returncode=128,
            )
        if args[:3] == ["git", "status", "--porcelain=v1"]:
            return _completed(
                args,
                stderr="fatal: detected dubious ownership in repository\n",
                returncode=128,
            )
        if args[:2] == ["git", "-c"]:
            if "rev-parse" in args:
                return _completed(args, stdout="abc123\n")
            if "status" in args:
                return _completed(args, stdout=" M one.py\n M two.py\n")
        raise AssertionError(f"unexpected command: {args}")

    metadata = MODULE._git_metadata(repo, runner=runner)

    assert metadata["production_head"] == "abc123"
    assert metadata["tracked_changes"] == 2
    assert metadata["metadata_readable"] is True
    assert metadata["safe_directory_override_recovered"] is True
    assert metadata["head_read"]["default_error_category"] == "GIT_SAFE_DIRECTORY"
    assert metadata["status_read"]["default_error_category"] == "GIT_SAFE_DIRECTORY"
    assert metadata["head_read"]["override_ok"] is True
    assert metadata["status_read"]["override_ok"] is True
    assert any(
        command[:2] == ("git", "-c")
        and command[2] == f"safe.directory={repo}"
        for command in seen
    )


def test_git_probe_does_not_use_override_when_default_read_succeeds(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seen = []

    def runner(args, **kwargs):
        seen.append(tuple(args))
        if args[:2] == ["git", "rev-parse"]:
            return _completed(args, stdout="def456\n")
        if args[:3] == ["git", "status", "--porcelain=v1"]:
            return _completed(args, stdout="")
        raise AssertionError(f"unexpected command: {args}")

    metadata = MODULE._git_metadata(repo, runner=runner)

    assert metadata["production_head"] == "def456"
    assert metadata["tracked_changes"] == 0
    assert metadata["metadata_readable"] is True
    assert metadata["safe_directory_override_recovered"] is False
    assert all(command[:2] != ("git", "-c") for command in seen)


def test_git_error_classification_does_not_expose_raw_stderr():
    proc = _completed(
        ["git"],
        stderr=(
            "fatal: detected dubious ownership in repository at '/secret/path'\n"
            "To add an exception for this directory, call safe.directory\n"
        ),
        returncode=128,
    )

    assert MODULE._classify_git_error(proc) == "GIT_SAFE_DIRECTORY"


def test_collection_evidence_records_current_hashes_without_file_contents(tmp_path):
    collection = MODULE._load_module(
        ROOT / MODULE.COLLECTION_TOOL,
        "test_conflict_collection",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    manifest = json.loads(
        (ROOT / MODULE.PHASE2_MANIFEST).read_text(encoding="utf-8")
    )
    relative = manifest["deployment_files"][0]
    target = repo / relative
    target.parent.mkdir(parents=True)
    target.write_text("local production fix\n", encoding="utf-8")

    evidence = MODULE._collection_evidence(
        collection,
        repository=repo,
        source=ROOT,
        manifest=MODULE.PHASE2_MANIFEST,
    )

    item = next(entry for entry in evidence["files"] if entry["path"] == relative)
    assert item["status"] == "CONFLICT_MODIFIED"
    assert item["current_blob"] == MODULE._git_blob_sha(target)
    assert item["current"]["sha256"] == MODULE._sha256(target)
    assert item["current"]["size"] == len(target.read_bytes())
    serialized = json.dumps(item, sort_keys=True)
    assert "local production fix" not in serialized


def test_state_reader_conflict_evidence_reports_exact_current_digest(tmp_path):
    state_module = MODULE._load_module(
        ROOT / MODULE.STATE_READER_TOOL,
        "test_conflict_state_reader",
    )
    repo = tmp_path / "repo"
    target = repo / state_module.TARGET_PATH
    target.parent.mkdir(parents=True)
    target.write_text("production local state reader fix\n", encoding="utf-8")

    evidence = MODULE._state_reader_evidence(
        state_module,
        repository=repo,
        source=ROOT,
    )

    assert evidence["status"] == "CONFLICT_MODIFIED"
    assert evidence["preflight_ready"] is False
    assert evidence["current"]["git_blob"] == MODULE._git_blob_sha(target)
    assert evidence["current"]["sha256"] == MODULE._sha256(target)
    assert evidence["expected_base_blob"] == state_module.STACK_BASE_BLOB_SHA
    assert evidence["target_blob"] == state_module.STACK_TARGET_BLOB_SHA
    assert evidence["patch_scope"] == [state_module.TARGET_PATH]


def test_conflict_path_summary_includes_only_nonready_paths():
    phase2 = {
        "files": [
            {"path": "a.py", "status": "CONFLICT_MODIFIED"},
            {"path": "b.py", "status": "READY_UPDATE"},
        ]
    }
    state_reader = {
        "path": "state_reader.rs",
        "status": "CONFLICT_MODIFIED",
    }
    runtime = {
        "files": [
            {"path": "c.py", "status": "READY_CREATE"},
            {"path": "d.py", "status": "CONFLICT_SYMLINK"},
        ]
    }

    assert MODULE._conflict_paths(phase2, state_reader, runtime) == [
        {
            "layer": "PHASE2_SHARED_PREREQUISITES",
            "path": "a.py",
            "status": "CONFLICT_MODIFIED",
        },
        {
            "layer": "MARKET_PAPER_RUNTIME",
            "path": "d.py",
            "status": "CONFLICT_SYMLINK",
        },
        {
            "layer": "STATE_READER",
            "path": "state_reader.rs",
            "status": "CONFLICT_MODIFIED",
        },
    ]


def test_build_conflict_evidence_is_sealed_and_non_authorizing(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    research = repo / "python-learner/src/meteora_learner/research_store.py"
    research.parent.mkdir(parents=True)
    research.write_text("local research store fix\n", encoding="utf-8")

    state = repo / "rust-executor/src/state_reader.rs"
    state.parent.mkdir(parents=True)
    state.write_text("local state reader fix\n", encoding="utf-8")

    def runner(args, **kwargs):
        if args[:2] == ["git", "rev-parse"]:
            return _completed(
                args,
                stderr="fatal: detected dubious ownership\n",
                returncode=128,
            )
        if args[:3] == ["git", "status", "--porcelain=v1"]:
            return _completed(
                args,
                stderr="fatal: detected dubious ownership\n",
                returncode=128,
            )
        if args[:2] == ["git", "-c"]:
            if "rev-parse" in args:
                return _completed(args, stdout="abc123\n")
            if "status" in args:
                return _completed(args, stdout=" M research_store.py\n")
        if args[:2] == ["systemctl", "is-active"]:
            return _completed(args, stdout="active\n")
        raise AssertionError(f"unexpected command: {args}")

    evidence = MODULE.build_conflict_evidence(
        repository=repo,
        source_tree=ROOT,
        runner=runner,
    )

    assert evidence["evidence_complete"] is True
    assert evidence["git_metadata"]["production_head"] == "abc123"
    assert evidence["git_metadata"]["safe_directory_override_recovered"] is True
    assert evidence["detector_service"] == "active"
    assert evidence["watcher_service"] == "active"
    assert evidence["mutation_authorized"] is False
    assert evidence["service_restart_authorized"] is False
    assert evidence["detector_cursor_movement_authorized"] is False
    assert evidence["paper_timer_enable_authorized"] is False
    assert evidence["live_capital_authorized"] is False
    assert any(
        item["path"].endswith("research_store.py")
        and item["status"] == "CONFLICT_MODIFIED"
        for item in evidence["conflict_paths"]
    )
    assert any(
        item["layer"] == "STATE_READER"
        and item["status"] == "CONFLICT_MODIFIED"
        for item in evidence["conflict_paths"]
    )
    MODULE.validate_conflict_evidence(json.loads(json.dumps(evidence)))


def test_tampered_conflict_evidence_digest_is_rejected(tmp_path):
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
        "target_pool": MODULE.TARGET_POOL,
        "target_pool_cursor": "cursor",
        "detector_service": "active",
        "watcher_service": "active",
        "git_metadata": {
            "production_head": "abc",
            "tracked_changes": 1,
            "head_read": {},
            "status_read": {},
            "metadata_readable": True,
            "safe_directory_override_recovered": False,
        },
        "phase2": {
            "files": [],
        },
        "state_reader": {
            "expected_base_blob": "0" * 40,
            "target_blob": "1" * 40,
            "patch_sha256": "2" * 64,
        },
        "market_paper": {
            "files": [],
        },
        "conflict_paths": [],
        "evidence_complete": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    evidence = {
        **identity,
        "evidence_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }

    MODULE.validate_conflict_evidence(evidence)
    tampered = copy.deepcopy(evidence)
    tampered["target_pool_cursor"] = "different"

    try:
        MODULE.validate_conflict_evidence(tampered)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered conflict evidence must fail closed")


def test_rehashed_conflict_evidence_cannot_authorize_mutation():
    source_blobs = {
        str(path): blob
        for path, blob in sorted(
            MODULE.REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "reviewed_source_blobs": source_blobs,
        "repository": "/opt/pio",
        "target_pool": MODULE.TARGET_POOL,
        "target_pool_cursor": None,
        "detector_service": "active",
        "watcher_service": "active",
        "git_metadata": {
            "production_head": "abc",
            "tracked_changes": 0,
            "head_read": {},
            "status_read": {},
            "metadata_readable": True,
            "safe_directory_override_recovered": False,
        },
        "phase2": {"files": []},
        "state_reader": {
            "expected_base_blob": "0" * 40,
            "target_blob": "1" * 40,
            "patch_sha256": None,
        },
        "market_paper": {"files": []},
        "conflict_paths": [],
        "evidence_complete": True,
        "production_deployment_authorized": False,
        "mutation_authorized": True,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    evidence = {
        **identity,
        "evidence_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }

    try:
        MODULE.validate_conflict_evidence(evidence)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_conflict_evidence_tool_contains_no_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "copy2(" not in source
    assert "shutil" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert 'systemctl", "restart' not in source
    assert 'systemctl", "start' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
