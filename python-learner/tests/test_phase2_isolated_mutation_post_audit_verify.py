from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_mutation_post_audit.py"
RENDER_TOOL = ROOT / "deploy/tools/render_phase2_isolated_mutation_command.py"

SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_mutation_post_audit",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

RENDER_SPEC = importlib.util.spec_from_file_location(
    "phase2_post_audit_test_renderer",
    RENDER_TOOL,
)
assert RENDER_SPEC is not None and RENDER_SPEC.loader is not None
RENDER = importlib.util.module_from_spec(RENDER_SPEC)
sys.modules[RENDER_SPEC.name] = RENDER
RENDER_SPEC.loader.exec_module(RENDER)


def write_private(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def head_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def install_static_receipt_validation(monkeypatch):
    monkeypatch.setattr(
        MODULE.AUDIT,
        "_validate_receipt",
        lambda receipt: ("COMPLETED", True),
    )
    monkeypatch.setattr(
        MODULE.AUDIT,
        "_validate_preview_chain",
        lambda **kwargs: ({}, True, True, True),
    )


def build_artifacts(
    tmp_path: Path,
    *,
    embedded_receipt_path: str | None = None,
    embedded_preview_path: str | None = None,
):
    preview = tmp_path / "preview.json"
    write_private(preview, {"preview": "fixture"})

    receipt = tmp_path / "execution.json"
    receipt_payload = {
        "preview_path": (
            embedded_preview_path
            if embedded_preview_path is not None
            else str(preview)
        ),
        "mutation_launched": True,
        "mutation_completed": True,
        "mutation_succeeded": True,
        "outcome_known": True,
        "exit_code": 0,
        "execution_failure_category": None,
    }
    write_private(receipt, receipt_payload)
    receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()

    audit = {
        "execution_receipt_path": (
            embedded_receipt_path
            if embedded_receipt_path is not None
            else str(receipt)
        ),
        "execution_receipt_sha256": receipt_sha,
        "receipt_status": "COMPLETED",
        "receipt_terminal": True,
        "receipt_integrity_valid": True,
        "preview_path": (
            embedded_preview_path
            if embedded_preview_path is not None
            else str(preview)
        ),
        "preview_sha256_matches": True,
        "preview_identity_matches": True,
        "mutation_argv_sha256_matches": True,
        "prior_state": "SOURCE_BOOTSTRAP_REQUIRED",
        "prior_next_action": "BOOTSTRAP_PINNED_SOURCE",
        "prior_next_tool": "bootstrap_phase2_isolated_source.py",
        "current_state": "SOURCE_PREPARATION_REQUIRED",
        "current_next_action": "PREPARE_PINNED_RUNTIME",
        "current_next_tool": "prepare_phase2_isolated_runtime.py",
        "current_next_parameters": {
            "source_tree": "/tmp/pio-phase2-build/pinned",
        },
        "current_next_mutation_flag": "--prepare",
        "lifecycle_attention_required": True,
        "lifecycle_blockers": ["PINNED_SOURCE_NOT_PREPARED"],
        "state_changed": True,
        "expected_post_states": ["SOURCE_PREPARATION_REQUIRED"],
        "post_state_expected": True,
        "mutation_launched": True,
        "mutation_completed": True,
        "mutation_succeeded": True,
        "outcome_known": True,
        "exit_code": 0,
        "execution_failure_category": None,
        "audit_integrity_valid": True,
        "post_mutation_progress_observed": True,
        "post_mutation_verified": True,
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
    }

    commit = head_sha()
    resolved, deploy_sha, deploy_files = (
        MODULE._deploy_surface_identity_at_commit(ROOT, commit)
    )
    tool_sha = MODULE._historical_file_sha256(
        ROOT,
        commit=resolved,
        relative_path=MODULE._AUDIT_TOOL_RELATIVE,
    )
    artifact = {
        "format_version": 1,
        "artifact_type": "PHASE2_MUTATION_POST_AUDIT_V1",
        "execution_receipt_sha256": receipt_sha,
        "audit_payload_sha256": MODULE._canonical_sha256(audit),
        "audit_tool_sha256": tool_sha,
        "audit_source_commit": resolved,
        "audit_deploy_surface_sha256": deploy_sha,
        "audit_deploy_surface_files": deploy_files,
        "audit": audit,
    }
    artifact_path = tmp_path / "post-audit.json"
    write_private(artifact_path, artifact)
    return preview, receipt, artifact_path, artifact


def test_historical_deploy_surface_matches_current_renderer():
    commit, expected_sha, expected_files = RENDER._deploy_surface_identity()
    observed_commit, observed_sha, observed_files = (
        MODULE._deploy_surface_identity_at_commit(ROOT, commit)
    )

    assert observed_commit == commit
    assert observed_sha == expected_sha
    assert observed_files == expected_files


def test_verifier_accepts_intact_saved_post_audit(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    preview, receipt, artifact_path, _artifact = build_artifacts(tmp_path)

    report = MODULE.verify_saved_mutation_post_audit(
        artifact_path=artifact_path,
        repository_root=ROOT,
    )

    assert report.artifact_format_valid is True
    assert report.audit_payload_sha256 == _artifact["audit_payload_sha256"]
    assert report.audit_payload_sha256_matches is True
    assert report.audit_source_commit_present is True
    assert report.audit_source_is_ancestor_of_current_head is True
    assert report.audit_deploy_surface_sha256_matches is True
    assert report.audit_deploy_surface_files_matches is True
    assert report.audit_tool_sha256_matches is True
    assert report.execution_receipt_path == str(receipt)
    assert report.execution_receipt_sha256 == _artifact[
        "execution_receipt_sha256"
    ]
    assert report.execution_receipt_sha256_matches is True
    assert report.execution_receipt_valid is True
    assert report.preview_path == str(preview)
    assert report.preview_sha256_matches is True
    assert report.preview_identity_matches is True
    assert report.mutation_argv_sha256_matches is True
    assert report.prior_state == "SOURCE_BOOTSTRAP_REQUIRED"
    assert report.current_state == "SOURCE_PREPARATION_REQUIRED"
    assert report.current_next_action == "PREPARE_PINNED_RUNTIME"
    assert report.current_next_tool == "prepare_phase2_isolated_runtime.py"
    assert report.current_next_parameters == {
        "source_tree": "/tmp/pio-phase2-build/pinned",
    }
    assert report.current_next_mutation_flag == "--prepare"
    assert report.postcondition_record_valid is True
    assert report.static_audit_verified is True
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False


def test_verifier_supports_archive_path_overrides(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    preview, receipt, artifact_path, _artifact = build_artifacts(
        tmp_path,
        embedded_receipt_path="/archive/original/execution.json",
        embedded_preview_path="/archive/original/preview.json",
    )

    report = MODULE.verify_saved_mutation_post_audit(
        artifact_path=artifact_path,
        execution_receipt_path=receipt,
        preview_path=preview,
        repository_root=ROOT,
    )

    assert report.execution_receipt_override_used is True
    assert report.preview_override_used is True
    assert report.static_audit_verified is True


def test_verifier_detects_audit_payload_tampering(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    _preview, _receipt, artifact_path, artifact = build_artifacts(tmp_path)
    artifact["audit"]["current_next_action"] = "TAMPERED"
    write_private(artifact_path, artifact)

    report = MODULE.verify_saved_mutation_post_audit(
        artifact_path=artifact_path,
        repository_root=ROOT,
    )

    assert report.audit_payload_sha256_matches is False
    assert report.static_audit_verified is False


def test_verifier_detects_deploy_surface_identity_tampering(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    _preview, _receipt, artifact_path, artifact = build_artifacts(tmp_path)
    artifact["audit_deploy_surface_sha256"] = "0" * 64
    write_private(artifact_path, artifact)

    report = MODULE.verify_saved_mutation_post_audit(
        artifact_path=artifact_path,
        repository_root=ROOT,
    )

    assert report.audit_deploy_surface_sha256_matches is False
    assert report.static_audit_verified is False


def test_verifier_detects_historical_audit_tool_tampering(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    _preview, _receipt, artifact_path, artifact = build_artifacts(tmp_path)
    artifact["audit_tool_sha256"] = "0" * 64
    write_private(artifact_path, artifact)

    report = MODULE.verify_saved_mutation_post_audit(
        artifact_path=artifact_path,
        repository_root=ROOT,
    )

    assert report.audit_tool_sha256_matches is False
    assert report.static_audit_verified is False


def test_verifier_detects_receipt_hash_tampering(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    _preview, receipt, artifact_path, _artifact = build_artifacts(tmp_path)
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    receipt_payload["tampered"] = True
    write_private(receipt, receipt_payload)

    report = MODULE.verify_saved_mutation_post_audit(
        artifact_path=artifact_path,
        repository_root=ROOT,
    )

    assert report.execution_receipt_sha256_matches is False
    assert report.static_audit_verified is False


def test_verifier_rejects_noncontract_postcondition_record(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    _preview, _receipt, artifact_path, artifact = build_artifacts(tmp_path)
    artifact["audit"]["expected_post_states"] = ["RUNNING_HEALTHY"]
    artifact["audit"]["current_state"] = "RUNNING_HEALTHY"
    artifact["audit_payload_sha256"] = MODULE._canonical_sha256(
        artifact["audit"]
    )
    write_private(artifact_path, artifact)

    report = MODULE.verify_saved_mutation_post_audit(
        artifact_path=artifact_path,
        repository_root=ROOT,
    )

    assert report.audit_payload_sha256_matches is True
    assert report.postcondition_record_valid is False
    assert report.static_audit_verified is False


def test_verifier_rejects_nonancestor_audit_source(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    _preview, _receipt, artifact_path, _artifact = build_artifacts(tmp_path)
    monkeypatch.setattr(
        MODULE,
        "_is_ancestor_of_head",
        lambda repository_root, commit: False,
    )

    report = MODULE.verify_saved_mutation_post_audit(
        artifact_path=artifact_path,
        repository_root=ROOT,
    )

    assert report.audit_source_commit_present is True
    assert report.audit_source_is_ancestor_of_current_head is False
    assert report.static_audit_verified is False


def test_verifier_rejects_sensitive_saved_artifact(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    _preview, _receipt, artifact_path, artifact = build_artifacts(tmp_path)
    artifact["audit"]["current_next_parameters"] = {
        "rpc_url": "https://rpc.invalid/?api-key=secret",
    }
    artifact["audit_payload_sha256"] = MODULE._canonical_sha256(
        artifact["audit"]
    )
    write_private(artifact_path, artifact)

    with pytest.raises(ValueError, match="sensitive field"):
        MODULE.verify_saved_mutation_post_audit(
            artifact_path=artifact_path,
            repository_root=ROOT,
        )


def test_verifier_requires_private_artifact(
    tmp_path,
    monkeypatch,
):
    install_static_receipt_validation(monkeypatch)
    _preview, _receipt, artifact_path, _artifact = build_artifacts(tmp_path)
    artifact_path.chmod(0o644)

    with pytest.raises(ValueError, match="permissions must be 0600"):
        MODULE.verify_saved_mutation_post_audit(
            artifact_path=artifact_path,
            repository_root=ROOT,
        )
