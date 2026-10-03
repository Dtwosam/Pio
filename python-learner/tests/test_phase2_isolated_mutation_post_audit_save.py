from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/save_phase2_isolated_mutation_post_audit.py"
SPEC = importlib.util.spec_from_file_location(
    "save_phase2_isolated_mutation_post_audit",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Audit(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def verified_audit(receipt: Path, **overrides):
    values = {
        "execution_receipt_path": str(receipt),
        "execution_receipt_sha256": "1" * 64,
        "receipt_terminal": True,
        "audit_integrity_valid": True,
        "mutation_succeeded": True,
        "prior_state": "SOURCE_BOOTSTRAP_REQUIRED",
        "current_state": "SOURCE_PREPARATION_REQUIRED",
        "expected_post_states": ("SOURCE_PREPARATION_REQUIRED",),
        "post_state_expected": True,
        "post_mutation_verified": True,
        "current_next_action": "PREPARE_PINNED_RUNTIME",
        "current_next_tool": "prepare_phase2_isolated_runtime.py",
        "current_next_parameters": {
            "source_tree": "/tmp/pio-phase2-build/pinned",
        },
        "current_next_mutation_flag": "--prepare",
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
    }
    values.update(overrides)
    return Audit(**values)


def install(monkeypatch, report):
    monkeypatch.setattr(
        MODULE.AUDIT,
        "audit_mutation_execution_receipt",
        lambda **kwargs: report,
    )


def receipt(tmp_path: Path) -> Path:
    path = tmp_path / "execution.json"
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_saver_writes_private_verified_audit_atomically(
    tmp_path,
    monkeypatch,
):
    execution_receipt = receipt(tmp_path)
    audit = verified_audit(execution_receipt)
    install(monkeypatch, audit)

    report = MODULE.save_verified_mutation_post_audit(
        execution_receipt_path=execution_receipt,
    )

    output = Path(report.output_path)
    assert output == tmp_path / "execution.json.post-audit.json"
    assert output.is_file()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["format_version"] == 1
    assert payload["artifact_type"] == "PHASE2_MUTATION_POST_AUDIT_V1"
    assert payload["execution_receipt_sha256"] == "1" * 64
    assert payload["audit"]["post_mutation_verified"] is True
    assert payload["audit"]["post_state_expected"] is True
    assert payload["audit"]["current_state"] == (
        "SOURCE_PREPARATION_REQUIRED"
    )
    assert payload["audit_payload_sha256"] == report.audit_payload_sha256
    expected_tool_sha = hashlib.sha256(
        MODULE.AUDIT_TOOL.read_bytes()
    ).hexdigest()
    assert payload["audit_tool_sha256"] == expected_tool_sha
    assert report.audit_tool_sha256 == expected_tool_sha
    assert payload["audit_source_commit"] == report.audit_source_commit
    assert len(report.audit_source_commit) in {40, 64}
    assert payload["audit_deploy_surface_sha256"] == (
        report.audit_deploy_surface_sha256
    )
    assert len(report.audit_deploy_surface_sha256) == 64
    assert payload["audit_deploy_surface_files"] == (
        report.audit_deploy_surface_files
    )
    assert report.audit_deploy_surface_files > 0
    assert report.artifact_write_performed is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("audit_integrity_valid", False),
        ("receipt_terminal", False),
        ("mutation_succeeded", False),
        ("post_state_expected", False),
        ("post_mutation_verified", False),
        ("read_only", False),
        ("rpc_called", True),
        ("database_write_performed", True),
        ("service_control_performed", True),
        ("mutation_executed", True),
    ),
)
def test_saver_refuses_unverified_or_boundary_crossing_audit(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    execution_receipt = receipt(tmp_path)
    audit = verified_audit(execution_receipt, **{field: value})
    install(monkeypatch, audit)

    with pytest.raises(ValueError, match="not fully verified"):
        MODULE.save_verified_mutation_post_audit(
            execution_receipt_path=execution_receipt,
        )

    assert not (tmp_path / "execution.json.post-audit.json").exists()


def test_saver_refuses_existing_output(tmp_path, monkeypatch):
    execution_receipt = receipt(tmp_path)
    install(monkeypatch, verified_audit(execution_receipt))
    output = tmp_path / "audit.json"
    output.write_text("existing\n", encoding="utf-8")
    output.chmod(0o600)

    with pytest.raises(ValueError, match="already exists"):
        MODULE.save_verified_mutation_post_audit(
            execution_receipt_path=execution_receipt,
            output_path=output,
        )

    assert output.read_text(encoding="utf-8") == "existing\n"


def test_saver_refuses_symlink_output(tmp_path, monkeypatch):
    execution_receipt = receipt(tmp_path)
    install(monkeypatch, verified_audit(execution_receipt))
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "audit.json"
    output.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        MODULE.save_verified_mutation_post_audit(
            execution_receipt_path=execution_receipt,
            output_path=output,
        )


def test_saver_rechecks_credential_minimality(tmp_path, monkeypatch):
    execution_receipt = receipt(tmp_path)
    install(
        monkeypatch,
        verified_audit(
            execution_receipt,
            current_next_parameters={
                "rpc_url": "https://rpc.invalid/?api-key=secret",
            },
        ),
    )

    with pytest.raises(ValueError, match="sensitive field"):
        MODULE.save_verified_mutation_post_audit(
            execution_receipt_path=execution_receipt,
        )


def test_saver_refuses_protected_production_output(tmp_path, monkeypatch):
    execution_receipt = receipt(tmp_path)
    install(monkeypatch, verified_audit(execution_receipt))

    with pytest.raises(ValueError, match="protected production path"):
        MODULE.save_verified_mutation_post_audit(
            execution_receipt_path=execution_receipt,
            output_path="/opt/pio/data/post-audit.json",
        )


def test_saver_output_hash_is_stable_for_same_audit_record(
    tmp_path,
    monkeypatch,
):
    first_receipt = receipt(tmp_path)
    audit = verified_audit(first_receipt)
    install(monkeypatch, audit)

    first = MODULE.save_verified_mutation_post_audit(
        execution_receipt_path=first_receipt,
        output_path=tmp_path / "first.json",
    )
    second = MODULE.save_verified_mutation_post_audit(
        execution_receipt_path=first_receipt,
        output_path=tmp_path / "second.json",
    )

    assert first.audit_payload_sha256 == second.audit_payload_sha256
    assert Path(first.output_path).read_bytes() == Path(second.output_path).read_bytes()
    assert first.artifact_sha256 == second.artifact_sha256



def test_saver_rejects_symlinked_audit_tool(
    tmp_path,
    monkeypatch,
):
    execution_receipt = receipt(tmp_path)
    install(monkeypatch, verified_audit(execution_receipt))
    real_tool = tmp_path / "audit-tool.py"
    real_tool.write_text("# reviewed\n", encoding="utf-8")
    link = tmp_path / "audit-tool-link.py"
    link.symlink_to(real_tool)
    monkeypatch.setattr(MODULE, "AUDIT_TOOL", link)

    with pytest.raises(ValueError, match="missing or symlinked"):
        MODULE.save_verified_mutation_post_audit(
            execution_receipt_path=execution_receipt,
        )

    assert not (tmp_path / "execution.json.post-audit.json").exists()
