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
    expected_tool_sha = MODULE._AUDIT_TOOL_SHA256_AT_LOAD
    assert expected_tool_sha == hashlib.sha256(
        MODULE._AUDIT_TOOL_BYTES_AT_LOAD
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



def test_saver_publish_race_never_overwrites_post_audit_destination(
    tmp_path,
    monkeypatch,
):
    execution_receipt = receipt(tmp_path)
    install(monkeypatch, verified_audit(execution_receipt))
    output = tmp_path / "audit.json"
    competitor = b"preexisting concurrent post-audit evidence\n"
    real_link = MODULE.os.link
    raced = False

    def competing_publish(source, destination, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            destination = Path(destination)
            destination.write_bytes(competitor)
            destination.chmod(0o600)
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(MODULE.os, "link", competing_publish)

    with pytest.raises(
        ValueError,
        match="output appeared before publish",
    ):
        MODULE.save_verified_mutation_post_audit(
            execution_receipt_path=execution_receipt,
            output_path=output,
        )

    assert output.read_bytes() == competitor
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".audit.json.*.tmp"))



def test_saver_digest_comes_from_exact_published_payload_not_path_reread(
    tmp_path,
    monkeypatch,
):
    execution_receipt = receipt(tmp_path)
    audit = verified_audit(execution_receipt)
    install(monkeypatch, audit)
    output = tmp_path / "audit.json"

    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(path):
        if path == output:
            raise AssertionError(
                "published destination must not be reopened for digesting"
            )
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    saved = MODULE.save_verified_mutation_post_audit(
        execution_receipt_path=execution_receipt,
        output_path=output,
    )

    payload = output.read_text(encoding="utf-8").encode("utf-8")
    assert saved.artifact_sha256 == MODULE.hashlib.sha256(payload).hexdigest()
    assert saved.bytes_written == len(payload)



def test_saver_source_identity_matches_exact_executed_audit_bytes():
    (
        commit,
        audit_sha,
        deploy_surface_sha,
        deploy_surface_files,
    ) = MODULE._source_identity()

    assert len(commit) >= 40
    assert audit_sha == MODULE._AUDIT_TOOL_SHA256_AT_LOAD
    assert audit_sha == hashlib.sha256(
        MODULE._AUDIT_TOOL_BYTES_AT_LOAD
    ).hexdigest()
    assert len(deploy_surface_sha) == 64
    assert deploy_surface_files > 0


def test_saver_never_rereads_loaded_audit_or_renderer_tool_bytes(monkeypatch):
    real_read_bytes = Path.read_bytes

    def reject_tool_reread(path):
        resolved = path.resolve()
        if resolved in {
            MODULE._AUDIT_TOOL_PATH_AT_LOAD,
            MODULE._RENDER_TOOL_PATH_AT_LOAD,
        }:
            raise AssertionError(
                "executed saver dependency path must not be reread for identity"
            )
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_tool_reread)

    identity = MODULE._source_identity()

    assert identity[1] == MODULE._AUDIT_TOOL_SHA256_AT_LOAD


def test_saver_dependencies_are_executed_from_descriptor_captured_bytes():
    source = TOOL.read_text(encoding="utf-8")

    assert "def _capture_tool(" in source
    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "compile(encoded" in source
    assert "exec(code, module.__dict__)" in source
    assert "_AUDIT_TOOL_BYTES_AT_LOAD" in source
    assert "_RENDER_TOOL_BYTES_AT_LOAD" in source
    assert "AUDIT_TOOL.read_bytes()" not in source
    assert "RENDER_TOOL.read_bytes()" not in source


def test_saver_rejects_source_identity_change_during_audit(
    tmp_path,
    monkeypatch,
):
    execution_receipt = receipt(tmp_path)
    install(monkeypatch, verified_audit(execution_receipt))
    identities = iter(
        (
            ("a" * 40, "1" * 64, "2" * 64, 10),
            ("b" * 40, "3" * 64, "4" * 64, 10),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(ValueError, match="source changed during audit"):
        MODULE.save_verified_mutation_post_audit(
            execution_receipt_path=execution_receipt,
        )

    assert not (tmp_path / "execution.json.post-audit.json").exists()
