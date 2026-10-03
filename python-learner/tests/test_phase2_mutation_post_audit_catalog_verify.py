from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_mutation_post_audit_catalog.py"
SAVE_TOOL = ROOT / "deploy/tools/save_phase2_mutation_post_audit_catalog.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load(TOOL, "check_phase2_mutation_post_audit_catalog")
SAVER = load(SAVE_TOOL, "save_phase2_mutation_post_audit_catalog_for_verify_test")


def catalog_record():
    return {
        "artifact_directory": "/archive",
        "pattern": "*.post-audit.json",
        "artifacts_seen": 2,
        "artifacts_verified": 2,
        "artifacts_failed": 0,
        "duplicate_artifact_hashes": 0,
        "duplicate_receipt_hashes": 0,
        "entries": [
            {
                "artifact_path": "/archive/a.post-audit.json",
                "artifact_sha256": "1" * 64,
                "verification_status": "VERIFIED",
                "failure_category": None,
                "audit_source_commit": "a" * 40,
                "execution_receipt_sha256": "2" * 64,
                "prior_state": "SMOKE_REQUIRED",
                "current_state": "TIMER_ACTIVATION_READY",
                "current_next_action": "ACTIVATE_EVIDENCE_TIMER",
                "current_next_tool": "activate_phase2_isolated_timer.py",
                "current_next_mutation_flag": "--apply",
            },
            {
                "artifact_path": "/archive/b.post-audit.json",
                "artifact_sha256": "3" * 64,
                "verification_status": "VERIFIED",
                "failure_category": None,
                "audit_source_commit": "b" * 40,
                "execution_receipt_sha256": "4" * 64,
                "prior_state": "TIMER_ACTIVATION_READY",
                "current_state": "RUNNING_HEALTHY",
                "current_next_action": "MONITOR_ZERO_RPC_STATUS",
                "current_next_tool": "check_phase2_isolated_operator_status.py",
                "current_next_mutation_flag": None,
            },
        ],
        "catalog_stable_during_scan": True,
        "all_verified": True,
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
    }


def write_snapshot(path: Path, *, mutate=None) -> dict:
    commit, tool_sha = SAVER._catalog_source_identity()
    catalog = catalog_record()
    payload = {
        "format_version": 1,
        "artifact_type": "PHASE2_MUTATION_POST_AUDIT_CATALOG_SNAPSHOT_V1",
        "catalog_payload_sha256": MODULE._canonical_sha256(catalog),
        "catalog_source_commit": commit,
        "catalog_tool_sha256": tool_sha,
        "catalog": catalog,
    }
    if mutate is not None:
        mutate(payload)
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return payload


def test_verify_saved_catalog_snapshot_static_lineage(tmp_path):
    snapshot = tmp_path / "catalog.json"
    write_snapshot(snapshot)

    report = MODULE.verify_phase2_post_audit_catalog_snapshot(
        snapshot_path=snapshot,
    )

    assert report.snapshot_verified is True
    assert report.catalog_payload_sha256_matches is True
    assert report.catalog_source_commit_present is True
    assert report.catalog_source_is_ancestor_of_current_head is True
    assert report.catalog_tool_sha256_matches is True
    assert report.catalog_schema_valid is True
    assert report.artifacts_seen == 2
    assert report.artifacts_verified == 2
    assert report.unique_artifact_identities is True
    assert report.unique_receipt_identities is True
    assert report.artifacts_reverified is False
    assert report.historical_snapshot_only is True
    assert report.authorizes_next_action is False
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False


def test_verify_snapshot_detects_catalog_payload_tamper(tmp_path):
    snapshot = tmp_path / "catalog.json"

    def mutate(payload):
        payload["catalog"]["artifact_directory"] = "/archive/moved"

    write_snapshot(snapshot, mutate=mutate)

    report = MODULE.verify_phase2_post_audit_catalog_snapshot(
        snapshot_path=snapshot,
    )

    assert report.catalog_payload_sha256_matches is False
    assert report.snapshot_verified is False


def test_verify_snapshot_detects_catalog_tool_lineage_tamper(tmp_path):
    snapshot = tmp_path / "catalog.json"

    def mutate(payload):
        payload["catalog_tool_sha256"] = "f" * 64

    write_snapshot(snapshot, mutate=mutate)

    report = MODULE.verify_phase2_post_audit_catalog_snapshot(
        snapshot_path=snapshot,
    )

    assert report.catalog_tool_sha256_matches is False
    assert report.snapshot_verified is False


def test_verify_snapshot_rejects_duplicate_recorded_identity(tmp_path):
    snapshot = tmp_path / "catalog.json"

    def mutate(payload):
        payload["catalog"]["entries"][1]["artifact_sha256"] = "1" * 64
        payload["catalog"]["catalog_payload_sha256"] = "unused"

    payload = write_snapshot(snapshot)
    payload["catalog"]["entries"][1]["artifact_sha256"] = "1" * 64
    payload["catalog_payload_sha256"] = MODULE._canonical_sha256(payload["catalog"])
    snapshot.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    snapshot.chmod(0o600)

    with pytest.raises(ValueError, match="identity uniqueness"):
        MODULE.verify_phase2_post_audit_catalog_snapshot(
            snapshot_path=snapshot,
        )


def test_verify_snapshot_rejects_non_private_file(tmp_path):
    snapshot = tmp_path / "catalog.json"
    write_snapshot(snapshot)
    snapshot.chmod(0o644)

    with pytest.raises(ValueError, match="permissions must be 0600"):
        MODULE.verify_phase2_post_audit_catalog_snapshot(
            snapshot_path=snapshot,
        )


def test_verify_snapshot_rejects_sensitive_text(tmp_path):
    snapshot = tmp_path / "catalog.json"

    def mutate(payload):
        payload["catalog"]["artifact_directory"] = "/tmp/api-key=secret"
        payload["catalog_payload_sha256"] = MODULE._canonical_sha256(
            payload["catalog"]
        )

    write_snapshot(snapshot, mutate=mutate)

    with pytest.raises(ValueError, match="sensitive text"):
        MODULE.verify_phase2_post_audit_catalog_snapshot(
            snapshot_path=snapshot,
        )


def test_verify_snapshot_reports_missing_source_commit_without_action(tmp_path):
    snapshot = tmp_path / "catalog.json"

    def mutate(payload):
        payload["catalog_source_commit"] = "0" * 40

    write_snapshot(snapshot, mutate=mutate)

    report = MODULE.verify_phase2_post_audit_catalog_snapshot(
        snapshot_path=snapshot,
    )

    assert report.catalog_source_commit_present is False
    assert report.catalog_source_is_ancestor_of_current_head is False
    assert report.catalog_tool_sha256_matches is False
    assert report.snapshot_verified is False
    assert report.authorizes_next_action is False
