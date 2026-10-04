from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/run_phase2_isolated_mutation_with_receipt.py"
SPEC = importlib.util.spec_from_file_location(
    "run_phase2_isolated_mutation_with_receipt",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@dataclass
class Execution:
    mutation_executed: bool = True
    result_json_valid: bool = True
    result_secret_safe: bool = True
    exit_code: int | None = 0
    failure_category: str | None = None
    result: object | None = None

    def to_record(self):
        return asdict(self)


def preview(tmp_path: Path) -> Path:
    path = tmp_path / "mutation-preview.json"
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def ready(path: Path):
    return SimpleNamespace(
        preview_path=str(path),
        preview_sha256="1" * 64,
        reviewed_source_commit="2" * 40,
        deploy_surface_sha256="3" * 64,
        deploy_surface_files=42,
        mutation_fingerprint="4" * 64,
        mutation_tool_sha256="5" * 64,
        mutation_argv=(
            sys.executable,
            str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
            "--repository-url",
            "https://user:secret@example.invalid/repo.git",
            "--apply",
        ),
        execution_requested=False,
        mutation_executed=False,
        preview_current=True,
    )


def install_executor(monkeypatch, path: Path, execution=None):
    readiness = ready(path)
    final = execution or Execution()

    def fake_execute(*, execute=False, runner=subprocess.run, **kwargs):
        if not execute:
            return readiness
        runner(
            list(readiness.mutation_argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env={},
        )
        return final

    monkeypatch.setattr(
        MODULE.EXEC,
        "execute_fresh_mutation_preview",
        fake_execute,
    )
    return readiness, final


def times():
    values = iter(
        (
            "2026-10-03T11:00:00+00:00",
            "2026-10-03T11:00:01+00:00",
        )
    )
    return lambda: next(values)


def test_receipt_wrapper_preflight_writes_nothing(tmp_path, monkeypatch):
    path = preview(tmp_path)
    install_executor(monkeypatch, path)
    receipt = tmp_path / "receipt.json"

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=False,
    )

    assert report.preview_ready is True
    assert report.execution_requested is False
    assert report.pending_receipt_written is False
    assert report.final_receipt_written is False
    assert not receipt.exists()


def test_successful_mutation_finalizes_private_receipt(tmp_path, monkeypatch):
    path = preview(tmp_path)
    readiness, execution = install_executor(monkeypatch, path)
    receipt = tmp_path / "receipt.json"
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command, 0, stdout="{}", stderr="private stderr"
        )

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        runner=runner,
        now=times(),
    )

    assert seen["command"] == list(readiness.mutation_argv)
    assert report.receipt_status == "COMPLETED"
    assert report.mutation_launched is True
    assert report.mutation_completed is True
    assert report.mutation_succeeded is True
    assert report.exit_code == 0
    assert report.pending_receipt_written is True
    assert report.final_receipt_written is True
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "COMPLETED"
    assert payload["mutation_launched"] is True
    assert payload["mutation_completed"] is True
    assert payload["mutation_succeeded"] is True
    assert payload["outcome_known"] is True
    assert payload["execution_report_sha256"]
    assert payload["mutation_argv_sha256"]
    encoded = receipt.read_text(encoding="utf-8")
    assert "mutation_argv" not in payload
    assert "user:secret" not in encoded
    assert "private stderr" not in encoded


def test_successful_mutation_persists_only_allowlisted_outcome_summary(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    secret = "https://rpc.invalid/?api-key=secret"
    execution = Execution(
        result={
            "applied": True,
            "ready": True,
            "upgrade_needed": False,
            "installer_needed": False,
            "files_updated": 2,
            "daemon_reload_performed": False,
            "service_control_performed": False,
            "rpc_called": False,
            "rollback_performed": False,
            "failure_step": "SHOULD_NOT_COPY",
            "source_tree": "/private/source",
            "diagnostic": secret,
        },
    )
    install_executor(monkeypatch, path, execution)
    receipt = tmp_path / "receipt.json"

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="{}", stderr=secret
        ),
        now=times(),
    )

    expected = {
        "applied": True,
        "ready": True,
        "upgrade_needed": False,
        "installer_needed": False,
        "files_updated": 2,
        "daemon_reload_performed": False,
        "service_control_performed": False,
        "rpc_called": False,
    }
    assert report.mutation_outcome_summary == expected
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["mutation_outcome_summary"] == expected
    assert payload["execution_failure_evidence"] is None
    encoded = receipt.read_text(encoding="utf-8")
    assert "rollback_performed" not in encoded
    assert "failure_step" not in encoded
    assert "/private/source" not in encoded
    assert secret not in encoded


def test_successful_summary_drops_wrong_typed_values(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    execution = Execution(
        result={
            "applied": "yes",
            "ready": False,
            "files_updated": True,
            "rpc_called": 0,
        },
    )
    install_executor(monkeypatch, path, execution)
    receipt = tmp_path / "receipt.json"

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="{}", stderr=""
        ),
        now=times(),
    )

    assert report.mutation_outcome_summary == {"ready": False}
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["mutation_outcome_summary"] == {"ready": False}


def test_nonzero_mutation_is_still_finalized_as_known_outcome(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    execution = Execution(
        exit_code=2,
        failure_category="MUTATION_NONZERO_EXIT",
    )
    install_executor(monkeypatch, path, execution)
    receipt = tmp_path / "receipt.json"

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 2, stdout="{}", stderr="failed"
        ),
        now=times(),
    )

    assert report.receipt_status == "COMPLETED"
    assert report.mutation_completed is True
    assert report.mutation_succeeded is False
    assert report.exit_code == 2
    assert report.failure_category == "MUTATION_NONZERO_EXIT"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["outcome_known"] is True
    assert payload["execution_failure_category"] == "MUTATION_NONZERO_EXIT"
    assert payload["mutation_outcome_summary"] is None


def test_failed_mutation_preserves_reviewed_structured_failure_evidence(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    execution = Execution(
        exit_code=2,
        failure_category="MUTATION_NONZERO_EXIT",
        result={
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
            "unreviewed_detail": "must not persist",
        },
    )
    install_executor(monkeypatch, path, execution)
    receipt = tmp_path / "receipt.json"

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 2, stdout="{}", stderr="private failure"
        ),
        now=times(),
    )

    assert report.mutation_succeeded is False
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["execution_failure_evidence"] == {
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
    assert "unreviewed_detail" not in json.dumps(
        payload["execution_failure_evidence"]
    )
    assert "private failure" not in receipt.read_text(encoding="utf-8")


def test_secret_unsafe_failure_result_is_not_persisted(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    secret = "https://rpc.invalid/?api-key=secret"
    execution = Execution(
        result_secret_safe=False,
        exit_code=2,
        failure_category="MUTATION_NONZERO_EXIT",
        result={
            "failure_step": secret,
            "rollback_performed": True,
            "rollback_succeeded": False,
        },
    )
    install_executor(monkeypatch, path, execution)
    receipt = tmp_path / "receipt.json"

    MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 2, stdout="{}", stderr=""
        ),
        now=times(),
    )

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["execution_failure_evidence"] is None
    assert secret not in receipt.read_text(encoding="utf-8")


def test_guard_failure_after_pending_finalizes_aborted_before_launch(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    readiness = ready(path)

    def fake_execute(*, execute=False, **kwargs):
        if not execute:
            return readiness
        raise ValueError("freshness changed")

    monkeypatch.setattr(
        MODULE.EXEC,
        "execute_fresh_mutation_preview",
        fake_execute,
    )
    receipt = tmp_path / "receipt.json"

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        now=times(),
    )

    assert report.receipt_status == "ABORTED_BEFORE_LAUNCH"
    assert report.mutation_launched is False
    assert report.mutation_completed is False
    assert report.failure_category == "EXECUTION_GUARD_FAILED_BEFORE_LAUNCH"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["outcome_known"] is True
    assert "freshness changed" not in json.dumps(payload)


def test_runner_exception_after_launch_records_unknown_outcome(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    readiness = ready(path)

    def fake_execute(*, execute=False, runner=subprocess.run, **kwargs):
        if not execute:
            return readiness
        runner(
            list(readiness.mutation_argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env={},
        )
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        MODULE.EXEC,
        "execute_fresh_mutation_preview",
        fake_execute,
    )

    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout=10)

    receipt = tmp_path / "receipt.json"
    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        runner=runner,
        now=times(),
    )

    assert report.receipt_status == "OUTCOME_UNKNOWN_AFTER_LAUNCH"
    assert report.mutation_launched is True
    assert report.mutation_completed is False
    assert report.mutation_succeeded is False
    assert report.failure_category == "MUTATION_RUNNER_FAILED_OUTCOME_UNKNOWN"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["outcome_known"] is False


def test_existing_receipt_blocks_execution_before_pending_write(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    readiness = ready(path)
    execute_calls = 0

    def fake_execute(*, execute=False, **kwargs):
        nonlocal execute_calls
        if execute:
            execute_calls += 1
        return readiness

    monkeypatch.setattr(
        MODULE.EXEC,
        "execute_fresh_mutation_preview",
        fake_execute,
    )
    receipt = tmp_path / "receipt.json"
    receipt.write_text("existing", encoding="utf-8")
    receipt.chmod(0o600)

    with pytest.raises(ValueError, match="already exists"):
        MODULE.run_mutation_with_receipt(
            preview_path=path,
            expected_preview_sha256="1" * 64,
            execution_receipt_path=receipt,
            execute=True,
        )

    assert execute_calls == 0
    assert receipt.read_text(encoding="utf-8") == "existing"


def test_broken_symlink_receipt_is_rejected(tmp_path, monkeypatch):
    path = preview(tmp_path)
    install_executor(monkeypatch, path)
    receipt = tmp_path / "receipt.json"
    receipt.symlink_to(tmp_path / "missing-target")

    with pytest.raises(ValueError, match="must not be a symlink"):
        MODULE.run_mutation_with_receipt(
            preview_path=path,
            expected_preview_sha256="1" * 64,
            execution_receipt_path=receipt,
            execute=True,
        )


def test_receipt_path_defaults_next_to_preview(tmp_path, monkeypatch):
    path = preview(tmp_path)
    install_executor(monkeypatch, path)

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execute=False,
    )

    assert report.receipt_path == str(
        path.with_name(f"{path.name}.execution.json")
    )



def test_pending_receipt_publish_does_not_clobber_racing_peer(
    tmp_path,
    monkeypatch,
):
    receipt = tmp_path / "receipt.json"
    real_link = MODULE.os.link

    def racing_link(source, destination, **kwargs):
        Path(destination).write_text("peer\n", encoding="utf-8")
        Path(destination).chmod(0o600)
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(MODULE.os, "link", racing_link)

    with pytest.raises(ValueError, match="appeared before publish"):
        MODULE._atomic_write_new_or_replace(
            receipt,
            {"status": "PENDING"},
            allow_replace=False,
        )

    assert receipt.read_text(encoding="utf-8") == "peer\n"


def test_receipt_digest_is_bound_to_exact_payload_without_destination_reread(
    tmp_path,
    monkeypatch,
):
    receipt = tmp_path / "receipt.json"
    payload = {"status": "PENDING", "value": 7}
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    expected = hashlib.sha256(encoded).hexdigest()

    def forbidden_read_bytes(_self):
        raise AssertionError("published receipt must not be reread for digest")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    digest = MODULE._atomic_write_new_or_replace(
        receipt,
        payload,
        allow_replace=False,
    )

    assert digest == expected
    assert receipt.read_text(encoding="utf-8") == encoded.decode("utf-8")
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600


def test_terminal_receipt_replacement_remains_explicit_and_verified(tmp_path):
    receipt = tmp_path / "receipt.json"
    pending = {"status": "PENDING"}
    final = {"status": "COMPLETED"}

    MODULE._atomic_write_new_or_replace(
        receipt,
        pending,
        allow_replace=False,
    )
    pending_inode = receipt.stat().st_ino

    digest = MODULE._atomic_write_new_or_replace(
        receipt,
        final,
        allow_replace=True,
    )

    encoded = (
        json.dumps(
            final,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    assert digest == hashlib.sha256(encoded).hexdigest()
    assert receipt.read_bytes() == encoded
    assert receipt.stat().st_ino != pending_inode
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600



def test_receipt_runner_source_identity_matches_exact_executed_bytes():
    commit, executor_sha = MODULE._executor_source_identity()

    assert len(commit) >= 40
    assert executor_sha == MODULE._EXECUTOR_TOOL_SHA256_AT_LOAD
    assert executor_sha == hashlib.sha256(
        MODULE._EXECUTOR_TOOL_BYTES_AT_LOAD
    ).hexdigest()


def test_receipt_runner_never_rereads_loaded_executor_tool_bytes(monkeypatch):
    real_read_bytes = Path.read_bytes

    def reject_executor_reread(path):
        if path.resolve() == MODULE._EXECUTOR_TOOL_PATH_AT_LOAD:
            raise AssertionError(
                "executed mutation tool path must not be reread for identity"
            )
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_executor_reread)

    commit, executor_sha = MODULE._executor_source_identity()

    assert len(commit) >= 40
    assert executor_sha == MODULE._EXECUTOR_TOOL_SHA256_AT_LOAD


def test_receipt_runner_executor_is_descriptor_captured_and_executed():
    source = TOOL.read_text(encoding="utf-8")

    assert "def _capture_tool(" in source
    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "compile(encoded" in source
    assert "exec(code, module.__dict__)" in source
    assert "_EXECUTOR_TOOL_BYTES_AT_LOAD" in source
    assert "EXECUTOR_TOOL.read_bytes()" not in source


def test_source_change_during_readiness_writes_no_receipt(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    install_executor(monkeypatch, path)
    identities = iter(
        (
            ("a" * 40, "1" * 64),
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_executor_source_identity",
        lambda: next(identities),
    )
    receipt = tmp_path / "receipt.json"

    with pytest.raises(ValueError, match="source changed during readiness"):
        MODULE.run_mutation_with_receipt(
            preview_path=path,
            expected_preview_sha256="1" * 64,
            execution_receipt_path=receipt,
            execute=False,
        )

    assert not receipt.exists()


def test_source_change_before_launch_finalizes_abort_receipt(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    install_executor(monkeypatch, path)
    identity = ("a" * 40, "1" * 64)
    identities = iter((identity, identity, ("b" * 40, "2" * 64)))
    monkeypatch.setattr(
        MODULE,
        "_executor_source_identity",
        lambda: next(identities),
    )
    receipt = tmp_path / "receipt.json"

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        now=times(),
    )

    assert report.receipt_status == "ABORTED_BEFORE_LAUNCH"
    assert report.mutation_launched is False
    assert report.failure_category == "EXECUTION_GUARD_FAILED_BEFORE_LAUNCH"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["outcome_known"] is True


def test_source_change_after_launch_keeps_outcome_unknown(
    tmp_path,
    monkeypatch,
):
    path = preview(tmp_path)
    install_executor(monkeypatch, path)
    identity = ("a" * 40, "1" * 64)
    identities = iter(
        (
            identity,
            identity,
            identity,
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_executor_source_identity",
        lambda: next(identities),
    )
    receipt = tmp_path / "receipt.json"

    report = MODULE.run_mutation_with_receipt(
        preview_path=path,
        expected_preview_sha256="1" * 64,
        execution_receipt_path=receipt,
        execute=True,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout="{}",
            stderr="",
        ),
        now=times(),
    )

    assert report.receipt_status == "OUTCOME_UNKNOWN_AFTER_LAUNCH"
    assert report.mutation_launched is True
    assert report.mutation_completed is False
    assert report.mutation_succeeded is False
    assert report.failure_category == "MUTATION_RUNNER_FAILED_OUTCOME_UNKNOWN"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["outcome_known"] is False
