from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface.py"
)
RUNBOOK = (
    ROOT
    / "deploy"
    / "phase8-recursive-reentry-checkpoint-v25-operator-archive-runbook.md"
)
SPEC = importlib.util.spec_from_file_location(
    "check_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _manifest():
    value, _ = MODULE._load_archive_manifest(ROOT)
    return value


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["archive_surface_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_archive_manifest_and_bound_sources_are_exactly_pinned():
    manifest, manifest_sha = MODULE._load_archive_manifest(ROOT)
    assert len(manifest_sha) == 64

    base_manifest, base_checker, base_digest = MODULE._verify_base_surface(
        ROOT,
        manifest,
    )
    assert base_manifest == manifest["base_operator_surface"][
        "manifest_git_blob"
    ]
    assert base_checker == manifest["base_operator_surface"][
        "checker_git_blob"
    ]
    assert len(base_digest) == 64

    entries = MODULE._verify_archive_entries(ROOT, manifest)
    assert [item["role"] for item in entries] == list(
        MODULE.EXPECTED_ARCHIVE_ROLES
    )
    assert all(item["source_verified"] is True for item in entries)
    assert all(item["read_only"] is True for item in entries)
    assert all(item["historical_only"] is True for item in entries)
    assert all(item["authorizes_next_action"] is False for item in entries)


def test_builds_non_authorizing_archive_surface_integrity():
    report = (
        MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
            source_tree=ROOT
        )
    )

    assert report["base_operator_surface_verified"] is True
    assert report["archive_surface_verified"] is True
    assert report["source_tree_stable_during_check"] is True
    assert report["archive_layer_read_only"] is True
    assert report["archive_layer_historical_only"] is True
    assert report["archive_layer_is_not_execution_sequence"] is True
    assert report["archive_layer_authorizes_no_next_action"] is True
    assert report["raw_signing_materials_must_bind_to_sealed_session"] is True
    assert report["real_v25_evidence_required"] is True
    assert report["authorization_currently_reusable"] is False
    assert report["next_action_authorized"] is False
    assert report["future_checkpoint_refresh_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["phase8_promotion_authorized"] is False
    MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
        report
    )


def test_archive_manifest_rejects_next_action_authority():
    manifest = json.loads(json.dumps(_manifest()))
    manifest["archive_entries"][0]["authorizes_next_action"] = True

    with pytest.raises(ValueError, match="cannot authorize action"):
        MODULE._validate_archive_manifest(manifest)


def test_archive_manifest_rejects_reusable_authorization():
    manifest = json.loads(json.dumps(_manifest()))
    manifest["safety_boundary"]["authorization_currently_reusable"] = True

    with pytest.raises(ValueError, match="archive safety boundary mismatch"):
        MODULE._validate_archive_manifest(manifest)


def test_archive_manifest_rejects_execution_sequence_claim():
    manifest = json.loads(json.dumps(_manifest()))
    manifest["invariants"]["archive_layer_is_not_execution_sequence"] = False

    with pytest.raises(ValueError, match="archive invariants mismatch"):
        MODULE._validate_archive_manifest(manifest)


def test_bound_archive_source_drift_fails_closed():
    manifest = _manifest()
    entry = manifest["archive_entries"][0]
    relative = Path(entry["path"])

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes() + b"\n# drift\n")

        with pytest.raises(ValueError, match="blob mismatch"):
            MODULE._verify_bound_file(
                root,
                raw_path=entry["path"],
                expected_blob=entry["git_blob"],
                label="checkpoint v25 archive test entry",
            )


def test_symlinked_archive_source_is_rejected():
    manifest = _manifest()
    entry = manifest["archive_entries"][0]
    relative = Path(entry["path"])

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        actual = root / "actual.py"
        actual.write_bytes((ROOT / relative).read_bytes())
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(actual)

        with pytest.raises(ValueError, match="must not be a symlink"):
            MODULE._verify_bound_file(
                root,
                raw_path=entry["path"],
                expected_blob=entry["git_blob"],
                label="checkpoint v25 archive test entry",
            )


def test_resealed_archive_surface_cannot_authorize_future_checkpoint():
    report = (
        MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
            source_tree=ROOT
        )
    )
    report["future_checkpoint_refresh_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="future_checkpoint_refresh_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
            report
        )


def test_resealed_archive_surface_cannot_make_authorization_reusable():
    report = (
        MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
            source_tree=ROOT
        )
    )
    report["authorization_currently_reusable"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="authorization_currently_reusable=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
            report
        )


def test_archive_runbook_keeps_terminal_boundary_explicit():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert (
        "check_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface.py"
        in text
    )
    assert (
        "build_phase8_recursive_reentry_checkpoint_v25_operator_session_archive.py"
        in text
    )
    assert (
        "build_phase8_recursive_reentry_checkpoint_v25_signing_material_archive.py"
        in text
    )
    assert "archive_surface_verified=true" in text
    assert "session_lineage_verified=true" in text
    assert "signing_material_archive_ready=true" in text
    assert "input_files_stable_during_archive=true" in text
    assert "authorization_currently_reusable=false" in text
    assert "does not authorize another tick" in text
    assert "future checkpoint refresh" in text
    assert "live submission" in text
    assert "promotion" in text


def test_archive_surface_checker_has_no_execution_or_database_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "sqlite3.connect" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert "subprocess.run" not in source
    assert "os.system" not in source
    assert '"authorization_currently_reusable": False' in source
    assert '"future_checkpoint_refresh_authorized": False' in source
    assert '"next_action_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
