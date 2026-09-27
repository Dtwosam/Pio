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
    / "build_manual_market_paper_preserved_source_bundle.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_preserved_source_bundle",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _entry(kind: str) -> dict:
    if kind == "RESEARCH_STORE":
        path = "python-learner/src/meteora_learner/research_store.py"
        base = "c9b9de5838d95a86bddffa6166b4d7a62e91cf16"
        candidate = "31bd88e3d74490f5d0b617ff4b36383e7e12e18f"
        sha = "1" * 64
        size = 32000
        patch_report = "2" * 64
        validation = "3" * 64
        patch_sha = "4" * 64
    else:
        path = "rust-executor/src/state_reader.rs"
        base = "30d1435af1329bca07f73d6639539b43503e84e9"
        candidate = "f54a1021cf8f89d285bde957d1f72d81857ec2fa"
        sha = "5" * 64
        size = 31806
        patch_report = "6" * 64
        validation = "7" * 64
        patch_sha = "8" * 64

    return {
        "kind": kind,
        "path": path,
        "base_blob": base,
        "preserved_candidate_blob": candidate,
        "candidate_sha256": sha,
        "candidate_size": size,
        "portable_patch_report_sha256": patch_report,
        "portable_validation_report_sha256": validation,
        "patch_sha256": patch_sha,
        "patch_size": 4096,
        "base_target_matches_reviewed_target": True,
        "patch_report_matches_preservation": True,
        "patch_bytes_match_report": True,
        "patch_apply_check_passed": True,
        "patch_applied": True,
        "observed_candidate_blob": candidate,
        "observed_candidate_sha256": sha,
        "observed_candidate_size": size,
        "candidate_reconstruction_matches": True,
        "entry_ready": True,
    }


def _report() -> dict:
    entries = [_entry("RESEARCH_STORE"), _entry("STATE_READER")]
    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "reviewed_tool_blobs": {
            str(path): blob
            for path, blob in sorted(
                MODULE.REVIEWED_TOOL_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "preservation_review_sha256": "9" * 64,
        "base_source_head": "a" * 40,
        "validation_source_head": "a" * 40,
        "base_source_matches_validation_head": True,
        "bundle_dir": "/var/tmp/pio-preserved-source.unit",
        "bundle_under_var_tmp": True,
        "entries": entries,
        "entry_count": 2,
        "all_patch_reports_match_preservation": True,
        "all_base_targets_match_reviewed_targets": True,
        "all_candidate_reconstructions_match": True,
        "bundle_ready": True,
        "candidate_content_in_report": False,
        "candidate_content_materialized_in_private_bundle": True,
        "production_file_modified": False,
        "production_repository_accessed": False,
        "requires_preserved_readiness_tooling": True,
        "requires_new_readiness_cycle": True,
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
        "bundle_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_reviewed_bundle_tool_lineage_is_exactly_pinned():
    MODULE._verify_tool_lineage()

    for relative, expected in MODULE.REVIEWED_TOOL_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_bundle_report_accepts_ready_private_bundle():
    report = _report()

    MODULE.validate_bundle_report(
        json.loads(json.dumps(report))
    )

    assert report["bundle_ready"] is True
    assert report["candidate_content_in_report"] is False
    assert report["candidate_content_materialized_in_private_bundle"] is True
    assert report["production_repository_accessed"] is False
    assert report["mutation_authorized"] is False


def test_bundle_report_rejects_source_head_drift_even_if_resealed():
    report = _report()
    report["base_source_head"] = "b" * 40
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["bundle_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_bundle_report(report)
    except ValueError as exc:
        assert "source-head match flag mismatch" in str(exc)
    else:
        raise AssertionError("source-head drift must fail closed")


def test_bundle_report_rejects_candidate_reconstruction_drift_even_if_resealed():
    report = _report()
    entry = report["entries"][1]
    entry["observed_candidate_blob"] = "b" * 40
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["bundle_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_bundle_report(report)
    except ValueError as exc:
        assert "observed candidate blob mismatch" in str(exc)
    else:
        raise AssertionError("candidate reconstruction drift must fail closed")


def test_rehashed_bundle_report_cannot_authorize_mutation():
    report = _report()
    report["mutation_authorized"] = True
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["bundle_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_bundle_report(report)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_safe_bundle_output_is_limited_to_new_var_tmp_directory():
    path = Path("/var/tmp/pio-preserved-source-unit-test-never-create")
    if path.exists():
        __import__("shutil").rmtree(path)

    assert MODULE._safe_bundle_dir(path) == path

    for invalid in (
        "relative/source",
        "/tmp/source",
        "/opt/pio/source",
        "/var/tmp",
    ):
        try:
            MODULE._safe_bundle_dir(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe bundle path must fail: {invalid}")


def test_apply_patch_changes_only_temporary_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "rust-executor/src/state_reader.rs"
    target.parent.mkdir(parents=True)
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    patch = tmp_path / "candidate.patch"
    patch.write_text(
        """--- a/rust-executor/src/state_reader.rs
+++ b/rust-executor/src/state_reader.rs
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
""",
        encoding="utf-8",
    )

    check_ok, applied = MODULE._apply_patch(
        workspace=workspace,
        patch_file=patch,
    )

    assert check_ok is True
    assert applied is True
    assert target.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_failed_patch_check_does_not_change_temporary_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "rust-executor/src/state_reader.rs"
    target.parent.mkdir(parents=True)
    target.write_text("different\n", encoding="utf-8")

    patch = tmp_path / "candidate.patch"
    patch.write_text(
        """--- a/rust-executor/src/state_reader.rs
+++ b/rust-executor/src/state_reader.rs
@@ -1 +1 @@
-expected
+candidate
""",
        encoding="utf-8",
    )

    check_ok, applied = MODULE._apply_patch(
        workspace=workspace,
        patch_file=patch,
    )

    assert check_ok is False
    assert applied is False
    assert target.read_text(encoding="utf-8") == "different\n"


def test_bundle_tool_has_no_production_repository_path_or_service_mutation():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "/opt/pio" not in source
    assert "systemctl" not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "pip install" not in source
    assert "cargo install" not in source
    assert "candidate_content_in_report" in source
    assert '"production_repository_accessed": False' in source
