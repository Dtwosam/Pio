from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/run_phase2_isolated_next_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "run_phase2_isolated_next_preflight",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Preview(SimpleNamespace):
    pass


def preview(
    *,
    tool="check_phase2_isolated_operator_status.py",
    argv=None,
    mutation_flag=None,
    rpc_called=False,
):
    tool_path = ROOT / "deploy" / "tools" / tool if tool else None
    if argv is None and tool_path is not None:
        argv = (
            sys.executable,
            str(tool_path),
            "--runtime-root",
            "/tmp/runtime",
        )
    return Preview(
        state="RUNNING_HEALTHY",
        next_action="MONITOR_ZERO_RPC_STATUS",
        next_tool=tool,
        preflight_argv=argv,
        preflight_command=None,
        mutation_flag=mutation_flag,
        mutation_flag_appended=False,
        lifecycle={"state": "RUNNING_HEALTHY"},
        read_only=True,
        rpc_called=rpc_called,
        database_write_performed=False,
        service_control_performed=False,
    )


def install(monkeypatch, value):
    monkeypatch.setattr(
        MODULE.RENDER,
        "render_lifecycle_command",
        lambda **kwargs: value,
    )


def completed(command, code=0, payload=None, stderr=""):
    return subprocess.CompletedProcess(
        command,
        code,
        stdout=json.dumps(
            {
                "read_only": True,
                "rpc_called": False,
                "database_write_performed": False,
                "service_control_performed": False,
            }
            if payload is None
            else payload
        ),
        stderr=stderr,
    )


def test_runner_executes_allowlisted_preflight_without_mutation_flag(
    monkeypatch,
):
    install(
        monkeypatch,
        preview(mutation_flag="--apply"),
    )
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return completed(command)

    report = MODULE.run_next_read_only_preflight(runner=runner)

    assert report.command_executed is True
    assert report.exit_code == 0
    assert report.result_json_valid is True
    assert report.failure_category is None
    assert "--apply" not in seen["command"]
    assert "--prepare" not in seen["command"]
    assert seen["kwargs"]["check"] is False
    assert "shell" not in seen["kwargs"]
    assert report.mutation_flag == "--apply"
    assert report.mutation_flag_appended is False


def test_runner_uses_current_python_interpreter(monkeypatch):
    value = preview()
    install(monkeypatch, value)

    def runner(command, **kwargs):
        assert command[0] == sys.executable
        return completed(command)

    report = MODULE.run_next_read_only_preflight(runner=runner)
    assert report.command_executed is True


def test_runner_rejects_mutation_flag_in_rendered_argv(monkeypatch):
    tool = ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"
    install(
        monkeypatch,
        preview(
            tool=tool.name,
            argv=(sys.executable, str(tool), "--apply"),
            mutation_flag="--apply",
        ),
    )

    with pytest.raises(ValueError, match="contains a mutation flag"):
        MODULE.run_next_read_only_preflight()


def test_runner_rejects_unreviewed_tool(monkeypatch, tmp_path):
    tool = tmp_path / "unreviewed.py"
    tool.write_text("print('{}')\n", encoding="utf-8")
    install(
        monkeypatch,
        preview(
            tool=tool.name,
            argv=(sys.executable, str(tool)),
        ),
    )

    with pytest.raises(ValueError, match="not allowlisted"):
        MODULE.run_next_read_only_preflight()


def test_runner_never_exposes_child_stderr(monkeypatch):
    install(monkeypatch, preview())
    secret = "https://rpc.invalid/?api-key=super-secret"

    def runner(command, **kwargs):
        return completed(command, code=2, stderr=f"failed at {secret}")

    report = MODULE.run_next_read_only_preflight(runner=runner)

    encoded = json.dumps(report.to_record())
    assert report.failure_category == "PREFLIGHT_NONZERO_EXIT"
    assert secret not in encoded
    assert "failed at" not in encoded


def test_runner_captures_structured_nonzero_preflight(monkeypatch):
    install(monkeypatch, preview())

    def runner(command, **kwargs):
        return completed(
            command,
            code=2,
            payload={
                "status": "NOT_READY",
                "read_only": True,
                "rpc_called": False,
                "service_control_performed": False,
            },
        )

    report = MODULE.run_next_read_only_preflight(runner=runner)

    assert report.exit_code == 2
    assert report.result_json_valid is True
    assert report.result["status"] == "NOT_READY"
    assert report.failure_category == "PREFLIGHT_NONZERO_EXIT"


def test_runner_rejects_boundary_crossing_result(monkeypatch):
    install(monkeypatch, preview())

    def runner(command, **kwargs):
        return completed(
            command,
            payload={
                "read_only": True,
                "rpc_called": True,
            },
        )

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.run_next_read_only_preflight(runner=runner)


def test_runner_returns_without_execution_when_no_tool(monkeypatch):
    install(
        monkeypatch,
        preview(tool=None, argv=None, mutation_flag=None),
    )
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("runner must not execute")

    report = MODULE.run_next_read_only_preflight(runner=runner)

    assert called is False
    assert report.command_rendered is False
    assert report.command_executed is False
    assert report.preflight_argv is None
    assert report.exit_code is None


def test_runner_scrubs_rpc_credentials_from_child_environment(
    monkeypatch,
):
    install(monkeypatch, preview())
    monkeypatch.setenv("SOLANA_RPC_URL", "https://secret")
    monkeypatch.setenv("SOLANA_WS_URL", "wss://secret")
    monkeypatch.setenv("JUPITER_API_KEY", "secret")
    monkeypatch.setenv("HELIUS_API_KEY", "secret")

    def runner(command, **kwargs):
        child = kwargs["env"]
        for key in MODULE._SENSITIVE_ENV_KEYS:
            assert key not in child
        assert child["PATH"] == os.environ["PATH"]
        return completed(command)

    report = MODULE.run_next_read_only_preflight(runner=runner)
    assert report.command_executed is True


def test_runner_rejects_invalid_timeout(monkeypatch):
    install(monkeypatch, preview())

    with pytest.raises(ValueError, match="between 1 and 300"):
        MODULE.run_next_read_only_preflight(timeout_seconds=0)
