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
    / "check_manual_market_paper_preserved_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_preserved_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reviewed_preserved_readiness_dependencies_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_git_reads_use_safe_directory_override(tmp_path):
    seen = []

    def runner(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="deadbeef\n",
            stderr="",
        )

    proc = MODULE._git_read(
        tmp_path,
        ["rev-parse", "HEAD"],
        runner=runner,
    )

    assert proc.returncode == 0
    assert seen[0][0] == [
        "git",
        "-c",
        f"safe.directory={tmp_path}",
        "rev-parse",
        "HEAD",
    ]
    assert seen[0][1]["cwd"] == str(tmp_path)


def _write_blob(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return MODULE._git_blob_sha_bytes(payload)


def test_preserved_file_classification_ready_update_and_target(tmp_path):
    repo = tmp_path / "repo"
    source = tmp_path / "source"
    relative = "x/file.rs"

    current = b"production-local\n"
    candidate = b"preserved-candidate\n"
    current_blob = _write_blob(repo / relative, current)
    candidate_blob = _write_blob(source / relative, candidate)

    ready = MODULE._classify_preserved_file(
        repository=repo,
        source_tree=source,
        relative=relative,
        expected_current_blob=current_blob,
        target_blob=candidate_blob,
    )
    assert ready.status == "READY_UPDATE"
    assert ready.current_blob == current_blob
    assert ready.source_blob == candidate_blob

    (repo / relative).write_bytes(candidate)
    deployed = MODULE._classify_preserved_file(
        repository=repo,
        source_tree=source,
        relative=relative,
        expected_current_blob=current_blob,
        target_blob=candidate_blob,
    )
    assert deployed.status == "ALREADY_TARGET"


def test_preserved_file_classification_rejects_symlinked_parent(tmp_path):
    repo = tmp_path / "repo"
    source = tmp_path / "source"
    repo.mkdir()
    source.mkdir()

    real_source_parent = source / "real"
    real_source_parent.mkdir()
    (real_source_parent / "file.rs").write_bytes(b"candidate\n")
    (source / "alias").symlink_to(real_source_parent, target_is_directory=True)

    candidate_blob = MODULE._git_blob_sha_bytes(b"candidate\n")
    result = MODULE._classify_preserved_file(
        repository=repo,
        source_tree=source,
        relative="alias/file.rs",
        expected_current_blob="1" * 40,
        target_blob=candidate_blob,
    )

    assert result.status == "SOURCE_SYMLINK_PARENT"


def test_preserved_file_classification_rejects_unexpected_drift(tmp_path):
    repo = tmp_path / "repo"
    source = tmp_path / "source"
    relative = "x/file.rs"

    current_blob = _write_blob(repo / relative, b"unexpected-drift\n")
    candidate_blob = _write_blob(source / relative, b"candidate\n")

    result = MODULE._classify_preserved_file(
        repository=repo,
        source_tree=source,
        relative=relative,
        expected_current_blob="1" * 40,
        target_blob=candidate_blob,
    )

    assert current_blob != "1" * 40
    assert result.status == "CONFLICT_MODIFIED"


def test_layer_summary_preserves_pending_and_nonready_evidence():
    files = [
        MODULE.FileStatus(
            path="a",
            expected_current_blob="1" * 40,
            target_blob="2" * 40,
            source_blob="2" * 40,
            current_blob="1" * 40,
            status="READY_UPDATE",
        ),
        MODULE.FileStatus(
            path="b",
            expected_current_blob=None,
            target_blob="3" * 40,
            source_blob="3" * 40,
            current_blob=None,
            status="READY_CREATE",
        ),
    ]

    summary = MODULE._layer_summary(files)

    assert summary.content_ready is True
    assert summary.deployed is False
    assert summary.files_changed == 2
    assert summary.status_counts == {
        "READY_CREATE": 1,
        "READY_UPDATE": 1,
    }
    assert summary.nonready == ()

    conflict = MODULE._layer_summary(
        files
        + [
            MODULE.FileStatus(
                path="c",
                expected_current_blob="4" * 40,
                target_blob="5" * 40,
                source_blob="5" * 40,
                current_blob="6" * 40,
                status="CONFLICT_MODIFIED",
            )
        ]
    )
    assert conflict.content_ready is False
    assert conflict.nonready == (
        {"path": "c", "status": "CONFLICT_MODIFIED"},
    )


def test_layer_summary_record_is_json_native():
    summary = MODULE._layer_summary(
        [
            MODULE.FileStatus(
                path="a",
                expected_current_blob="1" * 40,
                target_blob="2" * 40,
                source_blob="2" * 40,
                current_blob="1" * 40,
                status="READY_UPDATE",
            )
        ]
    )

    record = summary.to_record()

    assert isinstance(record["pending"], list)
    assert isinstance(record["nonready"], list)
    assert isinstance(record["files"], list)
    json.loads(json.dumps(record))


def _layer_record(contract, *, ready=True, deployed=False):
    files = []
    for path, expected_current_blob, target_blob in contract:
        if deployed:
            status = "ALREADY_TARGET"
            current_blob = target_blob
        elif ready:
            status = (
                "READY_CREATE"
                if expected_current_blob is None
                else "READY_UPDATE"
            )
            current_blob = expected_current_blob
        else:
            status = "CONFLICT_MODIFIED"
            current_blob = "f" * 40

        files.append(
            {
                "path": path,
                "expected_current_blob": expected_current_blob,
                "target_blob": target_blob,
                "source_blob": target_blob,
                "current_blob": current_blob,
                "status": status,
            }
        )

    statuses = [item["status"] for item in files]
    counts = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    pending = [
        {"path": item["path"], "status": item["status"]}
        for item in files
        if item["status"] in {"READY_CREATE", "READY_UPDATE"}
    ]
    nonready = [
        {"path": item["path"], "status": item["status"]}
        for item in files
        if item["status"] not in MODULE.READY_STATUSES
    ]
    return {
        "content_ready": not nonready,
        "deployed": all(
            item["status"] == "ALREADY_TARGET"
            for item in files
        ),
        "apply_authorized": False,
        "files_changed": sum(
            item["status"] in {"READY_CREATE", "READY_UPDATE"}
            for item in files
        ),
        "status_counts": dict(sorted(counts.items())),
        "pending": pending,
        "nonready": nonready,
        "files": files,
    }


def _state_record(*, ready=True, deployed=False):
    return {
        "content_ready": ready,
        "deployed": deployed,
        "status": (
            "ALREADY_TARGET"
            if deployed
            else ("READY_UPDATE" if ready else "CONFLICT_MODIFIED")
        ),
        "expected_current_blob": MODULE.EXPECTED_STATE_READER_CURRENT_BLOB,
        "target_blob": MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
        "source_blob": MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
        "current_blob": (
            MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB
            if deployed
            else MODULE.EXPECTED_STATE_READER_CURRENT_BLOB
        ),
    }


def _synthetic_report(
    *,
    preflight_ready=True,
    deployed=False,
    head=MODULE.EXPECTED_PRODUCTION_HEAD,
):
    phase2_contract = MODULE._manifest_contract(
        MODULE.PHASE2_MANIFEST,
        preserved_overrides={
            MODULE.RESEARCH_STORE_PATH: (
                MODULE.EXPECTED_RESEARCH_STORE_CURRENT_BLOB,
                MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
            ),
        },
    )
    runtime_contract = MODULE._manifest_contract(MODULE.RUNTIME_MANIFEST)
    phase2 = _layer_record(
        phase2_contract,
        ready=preflight_ready,
        deployed=deployed,
    )
    runtime = _layer_record(
        runtime_contract,
        ready=preflight_ready,
        deployed=deployed,
    )
    state = _state_record(ready=preflight_ready, deployed=deployed)
    head_matches = head == MODULE.EXPECTED_PRODUCTION_HEAD
    operational = True
    manual_safe = True

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
        "repository": "/production/repo",
        "reviewed_source_tree": "/reviewed/source",
        "private_bundle_dir": "/var/tmp/pio-preserved-source.synthetic",
        "private_bundle_sha256": "a" * 64,
        "private_bundle_verified": True,
        "production_head": head,
        "production_head_matches_reviewed_baseline": head_matches,
        "tracked_changes": 46,
        "detector_service": "active",
        "watcher_service": "active",
        "paper_account": "paper-test",
        "paper_service": "inactive",
        "paper_timer": "inactive",
        "manual_mode_safe": manual_safe,
        "target_pool": MODULE.TARGET_POOL,
        "target_pool_cursor": "cursor",
        "phase2": phase2,
        "state_reader": state,
        "market_paper": runtime,
        "deployment_preflight_clean": bool(
            head_matches and preflight_ready
        ),
        "runtime_files_deployed": deployed,
        "operational_services_healthy": operational,
        "manual_paper_runtime_ready": bool(
            operational and manual_safe and deployed
        ),
        "requires_fresh_handoff": True,
        "requires_preservation_aware_plan": True,
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
        "readiness_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_preserved_readiness_accepts_sealed_ready_report():
    report = _synthetic_report()

    MODULE.validate_preserved_readiness(
        json.loads(json.dumps(report))
    )

    assert report["deployment_preflight_clean"] is True
    assert report["runtime_files_deployed"] is False
    assert report["manual_paper_runtime_ready"] is False
    assert report["mutation_authorized"] is False


def test_production_head_drift_forces_preflight_not_clean():
    report = _synthetic_report(head="b" * 40)
    report["deployment_preflight_clean"] = False
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    MODULE.validate_preserved_readiness(report)

    assert report["production_head_matches_reviewed_baseline"] is False
    assert report["deployment_preflight_clean"] is False


def test_rehashed_preserved_readiness_cannot_substitute_runtime_contract():
    report = _synthetic_report()
    report["market_paper"]["files"][0]["target_blob"] = "f" * 40
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preserved_readiness(report)
    except ValueError as exc:
        assert "deployment contract mismatch" in str(exc)
    else:
        raise AssertionError("rehashed runtime contract substitution must fail closed")


def test_rehashed_preserved_readiness_cannot_flip_layer_summary():
    report = _synthetic_report()
    report["phase2"]["files"][0]["status"] = "CONFLICT_MODIFIED"
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preserved_readiness(report)
    except ValueError as exc:
        assert (
            "status counts mismatch" in str(exc)
            or "nonready evidence mismatch" in str(exc)
            or "content-ready mismatch" in str(exc)
        )
    else:
        raise AssertionError("rehashed layer-summary contradiction must fail closed")


def test_rehashed_preserved_readiness_cannot_flip_state_summary():
    report = _synthetic_report()
    report["state_reader"]["status"] = "CONFLICT_MODIFIED"
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preserved_readiness(report)
    except ValueError as exc:
        assert "content-ready mismatch" in str(exc)
    else:
        raise AssertionError("rehashed state-summary contradiction must fail closed")


def test_tampered_preserved_readiness_fails_digest_validation():
    report = _synthetic_report()
    report["tracked_changes"] += 1

    try:
        MODULE.validate_preserved_readiness(report)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered preserved readiness must fail closed")


def test_rehashed_preserved_readiness_cannot_authorize_mutation():
    report = _synthetic_report()
    report["mutation_authorized"] = True
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preserved_readiness(report)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_preserved_readiness_has_read_only_production_surface():
    source = TOOL.read_text(encoding="utf-8")

    assert '"is-active"' in source
    assert "safe.directory=" in source
    assert "SOURCE_SYMLINK_PARENT" in source
    assert "CONFLICT_SYMLINK_PARENT" in source
    assert ".write_text(" not in source
    assert ".write_bytes(" not in source
    assert "shutil" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "systemctl restart" not in source
    assert "systemctl enable" not in source
    assert "apply=True" not in source
