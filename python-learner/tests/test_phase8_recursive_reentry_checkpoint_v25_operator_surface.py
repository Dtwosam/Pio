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
    / "check_phase8_recursive_reentry_checkpoint_v25_operator_surface.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_phase8_recursive_reentry_checkpoint_v25_operator_surface",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _surface_manifest():
    value, _ = MODULE._load_surface_manifest(ROOT)
    return value


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["surface_integrity_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_outer_manifest_and_all_bound_files_are_exactly_pinned():
    manifest, manifest_sha = MODULE._load_surface_manifest(ROOT)
    assert len(manifest_sha) == 64
    core_blob, support = MODULE._verify_surface_files(ROOT, manifest)

    assert core_blob == manifest["core_execution_manifest"]["git_blob"]
    assert [item["role"] for item in support] == list(
        MODULE.EXPECTED_SUPPORT_ROLES
    )
    assert all(item["source_verified"] is True for item in support)
    assert all(item["read_only"] is True for item in support)
    assert all(item["authorizes_next_action"] is False for item in support)


def test_builds_non_authorizing_operator_surface_integrity():
    report = (
        MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
            source_tree=ROOT
        )
    )

    assert report["core_execution_manifest_verified"] is True
    assert report["core_mutation_boundary_role"] == "one-shot-paper-executor"
    assert report["support_surface_verified"] is True
    assert report["source_tree_stable_during_check"] is True
    assert report["surface_read_only"] is True
    assert report["surface_is_not_execution_sequence"] is True
    assert report["real_v25_evidence_required_for_future_checkpoint"] is True
    assert report["next_action_authorized"] is False
    assert report["future_checkpoint_refresh_authorized"] is False
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["phase8_promotion_authorized"] is False
    MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
        report
    )


def test_surface_manifest_rejects_support_authority_escalation():
    manifest = json.loads(json.dumps(_surface_manifest()))
    manifest["support_entries"][1]["authorizes_next_action"] = True

    with pytest.raises(
        ValueError,
        match="support cannot authorize next action",
    ):
        MODULE._validate_surface_manifest(manifest)


def test_surface_manifest_rejects_execution_sequence_claim():
    manifest = json.loads(json.dumps(_surface_manifest()))
    manifest["invariants"]["support_entries_are_not_an_execution_sequence"] = False

    with pytest.raises(ValueError, match="surface invariants mismatch"):
        MODULE._validate_surface_manifest(manifest)


def test_surface_manifest_rejects_future_checkpoint_authority():
    manifest = json.loads(json.dumps(_surface_manifest()))
    manifest["safety_boundary"]["future_checkpoint_refresh_authorized"] = True

    with pytest.raises(ValueError, match="surface safety boundary mismatch"):
        MODULE._validate_surface_manifest(manifest)


def test_bound_support_file_drift_fails_closed():
    manifest = _surface_manifest()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for relative in (
            Path(manifest["core_execution_manifest"]["path"]),
            MODULE.SURFACE_MANIFEST,
            *[Path(item["path"]) for item in manifest["support_entries"]],
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())

        victim = root / Path(manifest["support_entries"][0]["path"])
        victim.write_text(
            victim.read_text(encoding="utf-8") + "\n# drift\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="blob mismatch"):
            MODULE._verify_surface_files(root, manifest)


def test_symlinked_support_file_is_rejected():
    manifest = _surface_manifest()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for relative in (
            Path(manifest["core_execution_manifest"]["path"]),
            MODULE.SURFACE_MANIFEST,
            *[Path(item["path"]) for item in manifest["support_entries"]],
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())

        entry = manifest["support_entries"][0]
        target = root / Path(entry["path"])
        actual = target.with_name(target.name + ".actual")
        actual.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(actual)

        with pytest.raises(ValueError, match="must not be a symlink"):
            MODULE._verify_surface_files(root, manifest)


def test_resealed_report_cannot_authorize_future_checkpoint():
    report = (
        MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
            source_tree=ROOT
        )
    )
    report["future_checkpoint_refresh_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="future_checkpoint_refresh_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
            report
        )


def test_resealed_report_cannot_authorize_live_submit():
    report = (
        MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
            source_tree=ROOT
        )
    )
    report["live_submit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
            report
        )


def test_surface_integrity_has_no_execution_or_database_mutation_primitive():
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
    assert '"future_checkpoint_refresh_authorized": False' in source
    assert '"next_action_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
