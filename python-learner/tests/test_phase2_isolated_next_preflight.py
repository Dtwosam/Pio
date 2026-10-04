from __future__ import annotations

import hashlib
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
        tool = Path(preview(mutation_flag="--apply").preflight_argv[1])
        inherited = kwargs["pass_fds"]
        assert len(inherited) == 1
        assert os.pread(
            inherited[0],
            tool.stat().st_size,
            0,
        ) == tool.read_bytes()
        return completed(command)

    report = MODULE.run_next_read_only_preflight(runner=runner)

    assert report.command_executed is True
    assert report.exit_code == 0
    assert report.result_json_valid is True
    assert report.failure_category is None
    assert "--apply" not in seen["command"]
    assert "--prepare" not in seen["command"]
    assert seen["command"][:3] == [
        sys.executable,
        "-c",
        MODULE._EXACT_PREFLIGHT_BOOTSTRAP,
    ]
    assert seen["kwargs"]["pass_fds"]
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



def test_runner_rejects_nested_rpc_boundary_crossing(monkeypatch):
    install(monkeypatch, preview())

    def runner(command, **kwargs):
        return completed(
            command,
            payload={
                "read_only": True,
                "details": {
                    "activation": {
                        "rpc_called": True,
                    },
                },
            },
        )

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.run_next_read_only_preflight(runner=runner)


def test_runner_rejects_nested_read_only_false(monkeypatch):
    install(monkeypatch, preview())

    def runner(command, **kwargs):
        return completed(
            command,
            payload={
                "read_only": True,
                "details": {
                    "smoke": {
                        "read_only": False,
                    },
                },
            },
        )

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.run_next_read_only_preflight(runner=runner)


def test_runner_allows_historical_nested_applied_state(monkeypatch):
    install(monkeypatch, preview())

    def runner(command, **kwargs):
        return completed(
            command,
            payload={
                "read_only": True,
                "rpc_called": False,
                "history": {
                    "previous_release": {
                        "applied": True,
                    },
                },
            },
        )

    report = MODULE.run_next_read_only_preflight(runner=runner)

    assert report.exit_code == 0
    assert report.result_json_valid is True
    assert report.failure_category is None


def test_runner_rejects_top_level_current_applied_state(monkeypatch):
    install(monkeypatch, preview())

    def runner(command, **kwargs):
        return completed(
            command,
            payload={
                "read_only": True,
                "rpc_called": False,
                "applied": True,
            },
        )

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.run_next_read_only_preflight(runner=runner)


def test_nested_boundary_helper_scans_lists_and_tuples():
    assert MODULE._nested_boundary_ok(
        {
            "items": [
                {"read_only": True, "rpc_called": False},
                {"nested": ({"service_control_performed": False},)},
            ]
        }
    )
    assert not MODULE._nested_boundary_ok(
        {
            "items": [
                {"read_only": True},
                {"nested": [{"daemon_reload_performed": True}]},
            ]
        }
    )



def test_runner_renderer_source_identity_matches_exact_executed_bytes():
    commit, renderer_sha = MODULE._renderer_source_identity()

    assert len(commit) >= 40
    assert renderer_sha == MODULE._RENDER_TOOL_SHA256_AT_LOAD
    assert renderer_sha == hashlib.sha256(
        MODULE._RENDER_TOOL_BYTES_AT_LOAD
    ).hexdigest()


def test_runner_renderer_is_descriptor_captured_and_executed():
    source = TOOL.read_text(encoding="utf-8")

    assert "def _capture_regular_file(" in source
    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "compile(encoded" in source
    assert "exec(code, module.__dict__)" in source
    assert "_RENDER_TOOL_BYTES_AT_LOAD" in source
    assert "spec.loader.exec_module(module)" not in source


def test_runner_rejects_renderer_change_during_render(monkeypatch):
    value = preview()
    install(monkeypatch, value)
    identities = iter(
        (
            ("a" * 40, "1" * 64),
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_renderer_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(ValueError, match="renderer changed during render"):
        MODULE.run_next_read_only_preflight()


def test_preflight_bootstrap_executes_descriptor_bytes_not_replaced_path(
    tmp_path,
):
    tool = tmp_path / "preflight.py"
    tool.write_text(
        "import json,sys\n"
        "print(json.dumps({'version':'captured','file':__file__,"
        "'args':sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    fd = os.open(tool, os.O_RDONLY)
    try:
        command = MODULE._captured_preflight_command(
            argv=(sys.executable, str(tool), "--value", "7"),
            tool_path=tool.resolve(),
            tool_fd=fd,
        )
        tool.unlink()
        tool.write_text(
            "import json\n"
            "print(json.dumps({'version':'replacement'}))\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            pass_fds=(fd,),
        )
    finally:
        os.close(fd)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "captured"
    assert payload["file"] == str(tool.resolve())
    assert payload["args"] == ["--value", "7"]


def test_runner_reports_original_reviewed_preflight_argv(monkeypatch):
    value = preview()
    install(monkeypatch, value)

    report = MODULE.run_next_read_only_preflight(
        runner=lambda command, **kwargs: completed(command),
    )

    assert report.preflight_argv == value.preflight_argv
    assert report.preflight_argv[0] == sys.executable
    assert report.preflight_argv[1].endswith(
        "check_phase2_isolated_operator_status.py"
    )


def test_runner_never_launches_preflight_path_directly():
    source = TOOL.read_text(encoding="utf-8")

    assert "pass_fds=(preflight_tool_fd,)" in source
    assert "_EXACT_PREFLIGHT_BOOTSTRAP" in source
    assert "runner(\n        list(argv)," not in source



def test_preflight_runner_allows_unit_upgrade_tool(monkeypatch):
    rendered = Preview(
        state="SYSTEMD_UNIT_UPGRADE_READY",
        next_action="UPGRADE_REVIEWED_UNITS",
        next_tool="upgrade_phase2_isolated_systemd_units.py",
        preflight_argv=(
            sys.executable,
            str(
                ROOT
                / "deploy/tools/upgrade_phase2_isolated_systemd_units.py"
            ),
            "--source-tree",
            "/opt/pio-phase2-runtime/current",
            "--destination",
            "/etc/systemd/system",
        ),
        mutation_flag="--apply",
        mutation_flag_appended=False,
        lifecycle={},
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )
    monkeypatch.setattr(
        MODULE.RENDER,
        "render_lifecycle_command",
        lambda **kwargs: rendered,
    )

    def runner(command, **kwargs):
        payload = {
            "ready": True,
            "upgrade_needed": True,
            "installer_needed": False,
            "applied": False,
            "files_updated": 0,
            "backup_root": None,
            "daemon_reload_performed": False,
            "service_control_performed": False,
            "rpc_called": False,
            "units": [],
        }
        return subprocess.CompletedProcess(
            command, 0, json.dumps(payload), ""
        )

    report = MODULE.run_next_read_only_preflight(runner=runner)

    assert report.command_executed is True
    assert report.result_json_valid is True
    assert report.exit_code == 0
    assert report.failure_category is None
    assert report.next_tool == "upgrade_phase2_isolated_systemd_units.py"
    assert report.mutation_flag == "--apply"
    assert report.mutation_flag_appended is False
