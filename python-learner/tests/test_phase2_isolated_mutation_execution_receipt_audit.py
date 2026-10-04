from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_mutation_execution_receipt.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_mutation_execution_receipt",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_private(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def preview_payload():
    return {
        "format_version": 2,
        "fingerprint_schema": "PHASE2_MUTATION_PREVIEW_V2",
        "reviewed_source_commit": "1" * 40,
        "deploy_surface_sha256": "2" * 64,
        "deploy_surface_files": 50,
        "mutation_fingerprint": "3" * 64,
        "mutation_tool_sha256": "4" * 64,
        "preflight_succeeded": True,
        "mutation_rendered": True,
        "mutation_executed": False,
        "read_only": True,
        "state": "SOURCE_BOOTSTRAP_REQUIRED",
        "next_action": "BOOTSTRAP_PINNED_SOURCE",
        "next_tool": "bootstrap_phase2_isolated_source.py",
        "mutation_argv": [
            sys.executable,
            str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
            "--apply",
        ],
    }


def completed_receipt(preview_path: Path, *, succeeded=True, exit_code=0):
    preview_bytes = preview_path.read_bytes()
    preview = json.loads(preview_bytes)
    return {
        "format_version": 1,
        "receipt_type": "PHASE2_REVIEWED_MUTATION_EXECUTION_V1",
        "status": "COMPLETED",
        "started_at": "2026-10-03T11:00:00+00:00",
        "completed_at": "2026-10-03T11:00:01+00:00",
        "preview_path": str(preview_path),
        "preview_sha256": MODULE._sha256_bytes(preview_bytes),
        "reviewed_source_commit": preview["reviewed_source_commit"],
        "deploy_surface_sha256": preview["deploy_surface_sha256"],
        "deploy_surface_files": preview["deploy_surface_files"],
        "mutation_fingerprint": preview["mutation_fingerprint"],
        "mutation_tool_sha256": preview["mutation_tool_sha256"],
        "mutation_argv_sha256": MODULE._canonical_sha256(
            preview["mutation_argv"]
        ),
        "mutation_launched": True,
        "mutation_completed": True,
        "mutation_succeeded": succeeded,
        "outcome_known": True,
        "exit_code": exit_code,
        "failure_category": (
            None if succeeded else "MUTATION_NONZERO_EXIT"
        ),
        "execution_report_sha256": "5" * 64,
        "result_json_valid": True,
        "result_secret_safe": True,
        "execution_failure_category": (
            None if succeeded else "MUTATION_NONZERO_EXIT"
        ),
        "raw_stderr_persisted": False,
    }


def lifecycle(
    state="SOURCE_PREPARATION_REQUIRED",
    *,
    boundary_ok=True,
    next_parameters=None,
    next_mutation_flag="--prepare",
):
    return SimpleNamespace(
        state=state,
        next_action="PREPARE_PINNED_RUNTIME",
        next_tool="prepare_phase2_isolated_runtime.py",
        next_parameters=(
            {"source_tree": "/tmp/pio-phase2-build/pinned"}
            if next_parameters is None
            else next_parameters
        ),
        next_mutation_flag=next_mutation_flag,
        attention_required=True,
        blockers=("PINNED_SOURCE_NOT_PREPARED",),
        read_only=boundary_ok,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def install_lifecycle(monkeypatch, report):
    monkeypatch.setattr(
        MODULE.LIFECYCLE,
        "inspect_lifecycle_handoff",
        lambda **kwargs: report,
    )


def artifacts(tmp_path: Path, receipt_payload=None):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    receipt = tmp_path / "execution.json"
    payload = (
        receipt_payload(preview)
        if callable(receipt_payload)
        else receipt_payload
    )
    if payload is None:
        payload = completed_receipt(preview)
    write_private(receipt, payload)
    return preview, receipt


def test_audit_verifies_successful_mutation_and_observed_progress(
    tmp_path,
    monkeypatch,
):
    preview, receipt = artifacts(tmp_path)
    install_lifecycle(monkeypatch, lifecycle())

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.receipt_status == "COMPLETED"
    assert report.receipt_terminal is True
    assert report.preview_sha256_matches is True
    assert report.preview_identity_matches is True
    assert report.mutation_argv_sha256_matches is True
    assert report.audit_integrity_valid is True
    assert report.mutation_succeeded is True
    assert report.state_changed is True
    assert report.expected_post_states == ("SOURCE_PREPARATION_REQUIRED",)
    assert report.post_state_expected is True
    assert report.post_mutation_progress_observed is True
    assert report.post_mutation_verified is True
    assert report.current_state == "SOURCE_PREPARATION_REQUIRED"
    assert report.current_next_parameters == {
        "source_tree": "/tmp/pio-phase2-build/pinned"
    }
    assert report.current_next_mutation_flag == "--prepare"
    assert report.rpc_called is False
    assert report.mutation_executed is False


def test_audit_surfaces_successful_mutation_outcome_summary(
    tmp_path,
    monkeypatch,
):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    payload = completed_receipt(preview)
    payload["mutation_outcome_summary"] = {
        "applied": True,
        "ready": True,
        "files_updated": 2,
        "service_control_performed": False,
        "rpc_called": False,
    }
    receipt = tmp_path / "execution.json"
    write_private(receipt, payload)
    install_lifecycle(monkeypatch, lifecycle())

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.audit_integrity_valid is True
    assert report.mutation_succeeded is True
    assert report.execution_failure_evidence is None
    assert report.mutation_outcome_summary == payload[
        "mutation_outcome_summary"
    ]
    assert report.post_mutation_verified is True


def test_audit_rejects_success_summary_on_failed_mutation(
    tmp_path,
    monkeypatch,
):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    payload = completed_receipt(preview, succeeded=False, exit_code=2)
    payload["mutation_outcome_summary"] = {
        "applied": False,
        "ready": True,
    }
    receipt = tmp_path / "execution.json"
    write_private(receipt, payload)
    install_lifecycle(
        monkeypatch,
        lifecycle(state="SOURCE_BOOTSTRAP_REQUIRED"),
    )

    with pytest.raises(
        ValueError,
        match="mutation outcome summary is inconsistent",
    ):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )


def test_audit_rejects_unreviewed_success_summary_field(
    tmp_path,
    monkeypatch,
):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    payload = completed_receipt(preview)
    payload["mutation_outcome_summary"] = {
        "applied": True,
        "private_path": "/private/source",
    }
    receipt = tmp_path / "execution.json"
    write_private(receipt, payload)
    install_lifecycle(monkeypatch, lifecycle())

    with pytest.raises(ValueError, match="unreviewed fields"):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )


def test_audit_keeps_known_failed_mutation_distinct_from_integrity(
    tmp_path,
    monkeypatch,
):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    receipt = tmp_path / "execution.json"
    write_private(
        receipt,
        completed_receipt(preview, succeeded=False, exit_code=2),
    )
    install_lifecycle(
        monkeypatch,
        lifecycle(state="SOURCE_BOOTSTRAP_REQUIRED"),
    )

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.audit_integrity_valid is True
    assert report.mutation_completed is True
    assert report.mutation_succeeded is False
    assert report.execution_failure_category == "MUTATION_NONZERO_EXIT"
    assert report.post_mutation_verified is False


def test_audit_surfaces_reviewed_structured_failure_evidence(
    tmp_path,
    monkeypatch,
):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    payload = completed_receipt(preview, succeeded=False, exit_code=2)
    payload["execution_failure_evidence"] = {
        "applied": False,
        "failure_step": (
            "UPDATE:pio-phase2-isolated-evidence-cycle.service"
        ),
        "rollback_performed": True,
        "rollback_succeeded": False,
        "files_updated": 1,
        "ready": True,
        "upgrade_needed": True,
        "installer_needed": False,
    }
    receipt = tmp_path / "execution.json"
    write_private(receipt, payload)
    install_lifecycle(
        monkeypatch,
        lifecycle(state="SOURCE_BOOTSTRAP_REQUIRED"),
    )

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.audit_integrity_valid is True
    assert report.mutation_succeeded is False
    assert report.execution_failure_evidence == payload[
        "execution_failure_evidence"
    ]
    assert report.post_mutation_verified is False


def test_audit_rejects_failure_evidence_on_success(
    tmp_path,
    monkeypatch,
):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    payload = completed_receipt(preview)
    payload["execution_failure_evidence"] = {
        "rollback_performed": True,
        "rollback_succeeded": True,
    }
    receipt = tmp_path / "execution.json"
    write_private(receipt, payload)
    install_lifecycle(monkeypatch, lifecycle())

    with pytest.raises(ValueError, match="failure evidence is inconsistent"):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )


@pytest.mark.parametrize(
    "failure_step",
    (
        "UPDATE:/private/source",
        "UPDATE:https://example.invalid/path",
        "A" * 257,
    ),
)
def test_audit_rejects_non_categorical_failure_step(
    tmp_path,
    monkeypatch,
    failure_step,
):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    payload = completed_receipt(preview, succeeded=False, exit_code=2)
    payload["execution_failure_evidence"] = {
        "failure_step": failure_step,
        "rollback_performed": True,
    }
    receipt = tmp_path / "execution.json"
    write_private(receipt, payload)
    install_lifecycle(
        monkeypatch,
        lifecycle(state="SOURCE_BOOTSTRAP_REQUIRED"),
    )

    with pytest.raises(ValueError, match="failure_step is invalid"):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )


def test_audit_rejects_unreviewed_failure_evidence_field(
    tmp_path,
    monkeypatch,
):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    payload = completed_receipt(preview, succeeded=False, exit_code=2)
    payload["execution_failure_evidence"] = {
        "rollback_performed": True,
        "diagnostic": "unreviewed",
    }
    receipt = tmp_path / "execution.json"
    write_private(receipt, payload)
    install_lifecycle(
        monkeypatch,
        lifecycle(state="SOURCE_BOOTSTRAP_REQUIRED"),
    )

    with pytest.raises(ValueError, match="unreviewed fields"):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )


@pytest.mark.parametrize(
    ("status", "launched", "known", "category"),
    (
        (
            "ABORTED_BEFORE_LAUNCH",
            False,
            True,
            "EXECUTION_GUARD_FAILED_BEFORE_LAUNCH",
        ),
        (
            "OUTCOME_UNKNOWN_AFTER_LAUNCH",
            True,
            False,
            "MUTATION_RUNNER_FAILED_OUTCOME_UNKNOWN",
        ),
    ),
)
def test_audit_preserves_noncompleted_terminal_outcomes(
    tmp_path,
    monkeypatch,
    status,
    launched,
    known,
    category,
):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    receipt = tmp_path / "execution.json"
    base = completed_receipt(preview)
    base.update(
        {
            "status": status,
            "completed_at": "2026-10-03T11:00:01+00:00",
            "mutation_launched": launched,
            "mutation_completed": False,
            "mutation_succeeded": False,
            "outcome_known": known,
            "exit_code": None,
            "failure_category": category,
            "execution_report_sha256": None,
            "execution_failure_category": None,
        }
    )
    write_private(receipt, base)
    install_lifecycle(
        monkeypatch,
        lifecycle(state="SOURCE_BOOTSTRAP_REQUIRED"),
    )

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.receipt_terminal is True
    assert report.receipt_status == status
    assert report.audit_integrity_valid is True
    assert report.post_mutation_verified is False


def test_audit_surfaces_stranded_pending_receipt(tmp_path, monkeypatch):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    receipt = tmp_path / "execution.json"
    base = completed_receipt(preview)
    base.update(
        {
            "status": "PENDING",
            "completed_at": None,
            "mutation_launched": False,
            "mutation_completed": False,
            "mutation_succeeded": False,
            "outcome_known": False,
            "exit_code": None,
            "failure_category": None,
            "execution_report_sha256": None,
            "execution_failure_category": None,
        }
    )
    write_private(receipt, base)
    install_lifecycle(
        monkeypatch,
        lifecycle(state="SOURCE_BOOTSTRAP_REQUIRED"),
    )

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.receipt_status == "PENDING"
    assert report.receipt_terminal is False
    assert report.audit_integrity_valid is True
    assert report.post_mutation_verified is False


def test_audit_detects_preview_tampering_without_mutation(
    tmp_path,
    monkeypatch,
):
    preview, receipt = artifacts(tmp_path)
    payload = json.loads(preview.read_text(encoding="utf-8"))
    payload["next_action"] = "TAMPERED"
    write_private(preview, payload)
    install_lifecycle(monkeypatch, lifecycle())

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.preview_sha256_matches is False
    assert report.audit_integrity_valid is False
    assert report.post_mutation_verified is False


def test_audit_rejects_sensitive_receipt_field(tmp_path, monkeypatch):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    payload = completed_receipt(preview)
    payload["rpc_url"] = "https://rpc.invalid/?api-key=secret"
    receipt = tmp_path / "execution.json"
    write_private(receipt, payload)
    install_lifecycle(monkeypatch, lifecycle())

    with pytest.raises(ValueError, match="sensitive field"):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )


def test_audit_rejects_nonprivate_receipt(tmp_path):
    preview = tmp_path / "preview.json"
    write_private(preview, preview_payload())
    receipt = tmp_path / "execution.json"
    write_private(receipt, completed_receipt(preview))
    receipt.chmod(0o644)

    with pytest.raises(ValueError, match="permissions must be 0600"):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )


def test_audit_rejects_lifecycle_boundary_crossing(
    tmp_path,
    monkeypatch,
):
    preview, receipt = artifacts(tmp_path)
    install_lifecycle(
        monkeypatch,
        lifecycle(boundary_ok=False),
    )

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )



def test_audit_rejects_unexpected_changed_post_state(
    tmp_path,
    monkeypatch,
):
    preview, receipt = artifacts(tmp_path)
    install_lifecycle(
        monkeypatch,
        lifecycle(state="RUNNING_HEALTHY"),
    )

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.audit_integrity_valid is True
    assert report.mutation_succeeded is True
    assert report.state_changed is True
    assert report.expected_post_states == ("SOURCE_PREPARATION_REQUIRED",)
    assert report.post_state_expected is False
    assert report.post_mutation_progress_observed is False
    assert report.post_mutation_verified is False


def test_audit_rejects_successful_receipt_when_lifecycle_did_not_advance(
    tmp_path,
    monkeypatch,
):
    preview, receipt = artifacts(tmp_path)
    install_lifecycle(
        monkeypatch,
        lifecycle(state="SOURCE_BOOTSTRAP_REQUIRED"),
    )

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.state_changed is False
    assert report.post_state_expected is False
    assert report.post_mutation_verified is False


@pytest.mark.parametrize(
    ("prior_state", "allowed"),
    tuple(MODULE._EXPECTED_POST_STATES.items()),
)
def test_postcondition_contract_never_allows_same_state(prior_state, allowed):
    assert allowed
    assert prior_state not in allowed
    assert len(allowed) == len(set(allowed))



def test_audit_rejects_sensitive_lifecycle_handoff_parameter(
    tmp_path,
    monkeypatch,
):
    preview, receipt = artifacts(tmp_path)
    install_lifecycle(
        monkeypatch,
        lifecycle(
            next_parameters={
                "rpc_url": "https://rpc.invalid/?api-key=secret",
            }
        ),
    )

    with pytest.raises(ValueError, match="sensitive field"):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )


def test_audit_rejects_unreviewed_next_mutation_flag(
    tmp_path,
    monkeypatch,
):
    preview, receipt = artifacts(tmp_path)
    install_lifecycle(
        monkeypatch,
        lifecycle(next_mutation_flag="--force"),
    )

    with pytest.raises(ValueError, match="next mutation flag is invalid"):
        MODULE.audit_mutation_execution_receipt(
            execution_receipt_path=receipt,
        )


def test_audit_allows_monitoring_handoff_without_mutation_flag(
    tmp_path,
    monkeypatch,
):
    preview, receipt = artifacts(tmp_path)
    install_lifecycle(
        monkeypatch,
        lifecycle(
            state="SOURCE_PREPARATION_REQUIRED",
            next_parameters={"source_tree": "/tmp/pinned"},
            next_mutation_flag=None,
        ),
    )

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.current_next_parameters == {
        "source_tree": "/tmp/pinned",
    }
    assert report.current_next_mutation_flag is None



def test_audit_uses_single_byte_snapshot_for_receipt_and_preview(
    tmp_path,
    monkeypatch,
):
    preview, receipt = artifacts(tmp_path)
    install_lifecycle(monkeypatch, lifecycle())

    assert not hasattr(MODULE, "_load_json_object")

    def forbidden_read_bytes(_self):
        raise AssertionError("artifact path must not be reopened for hashing")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    report = MODULE.audit_mutation_execution_receipt(
        execution_receipt_path=receipt,
    )

    assert report.receipt_integrity_valid is True
    assert report.preview_sha256_matches is True
    assert report.preview_identity_matches is True
    assert report.mutation_argv_sha256_matches is True
    assert report.audit_integrity_valid is True



def test_unit_upgrade_postcondition_contract_routes_sequentially():
    assert "SYSTEMD_UNIT_UPGRADE_READY" in (
        MODULE._EXPECTED_POST_STATES["RUNTIME_STAGING_READY"]
    )
    assert MODULE._EXPECTED_POST_STATES["SYSTEMD_UNIT_UPGRADE_READY"] == (
        "SYSTEMD_UNITS_NOT_READY",
        "DETECTOR_ACTIVATION_READY",
        "ACTIVE_TOPOLOGY_NOT_READY",
    )
