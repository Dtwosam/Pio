from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/execute_phase2_isolated_mutation_preview.py"
SPEC = importlib.util.spec_from_file_location(
    "execute_phase2_isolated_mutation_preview",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def private_preview(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "preview.json"
    path.write_text('{"preview":"test"}\n', encoding="utf-8")
    path.chmod(0o600)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


def freshness_for(tool: Path):
    source_commit, deploy_sha, deploy_files = (
        MODULE.FRESH.RENDER._deploy_surface_identity()
    )
    tool_sha = hashlib.sha256(tool.read_bytes()).hexdigest()
    mutation_fp = "a" * 64
    argv = (sys.executable, str(tool), "--apply")
    return SimpleNamespace(
        preview_current=True,
        status="CURRENT",
        current_reviewed_source_commit=source_commit,
        current_deploy_surface_sha256=deploy_sha,
        current_deploy_surface_files=deploy_files,
        current_mutation_tool_sha256=tool_sha,
        current_mutation_fingerprint=mutation_fp,
        current_preview={
            "mutation_argv": list(argv),
        },
    )


def reviewed_tool() -> Path:
    return ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"


def test_executor_requires_private_preview_permissions(tmp_path):
    path = tmp_path / "preview.json"
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o644)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="permissions must be 0600"):
        MODULE.execute_fresh_mutation_preview(
            preview_path=path,
            expected_preview_sha256=digest,
        )


def test_executor_rejects_explicit_preview_hash_mismatch(tmp_path):
    path, _ = private_preview(tmp_path)

    with pytest.raises(ValueError, match="does not match explicit expectation"):
        MODULE.execute_fresh_mutation_preview(
            preview_path=path,
            expected_preview_sha256="0" * 64,
        )


def test_executor_preflight_mode_never_runs_mutation(
    tmp_path,
    monkeypatch,
):
    path, digest = private_preview(tmp_path)
    tool = reviewed_tool()
    freshness = freshness_for(tool)
    monkeypatch.setattr(
        MODULE.FRESH,
        "check_mutation_preview_freshness",
        lambda **kwargs: freshness,
    )

    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("mutation runner must not run")

    report = MODULE.execute_fresh_mutation_preview(
        preview_path=path,
        expected_preview_sha256=digest,
        runner=runner,
    )

    assert called is False
    assert report.execution_requested is False
    assert report.mutation_executed is False
    assert report.preview_current is True
    assert report.preview_sha256_matches is True
    assert report.exit_code is None
    assert report.shell_used is False
    assert report.raw_stderr_exposed is False


def test_executor_rejects_stale_preview(tmp_path, monkeypatch):
    path, digest = private_preview(tmp_path)
    freshness = SimpleNamespace(
        preview_current=False,
        status="STALE",
    )
    monkeypatch.setattr(
        MODULE.FRESH,
        "check_mutation_preview_freshness",
        lambda **kwargs: freshness,
    )

    with pytest.raises(ValueError, match="not current: STALE"):
        MODULE.execute_fresh_mutation_preview(
            preview_path=path,
            expected_preview_sha256=digest,
        )


def test_executor_uses_exact_reviewed_argv_without_shell(
    tmp_path,
    monkeypatch,
):
    path, digest = private_preview(tmp_path)
    tool = reviewed_tool()
    freshness = freshness_for(tool)
    monkeypatch.setattr(
        MODULE.FRESH,
        "check_mutation_preview_freshness",
        lambda **kwargs: freshness,
    )

    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        assert "SOLANA_RPC_URL" not in kwargs["env"]
        assert "HELIUS_API_KEY" not in kwargs["env"]

        inherited = kwargs["pass_fds"]
        assert len(inherited) == 1
        tool_fd = inherited[0]
        captured = os.pread(tool_fd, tool.stat().st_size, 0)
        assert captured == tool.read_bytes()

        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "applied": True,
                    "token_mint": "safe-mint",
                }
            ),
            stderr="https://secret.invalid/?api-key=must-not-leak",
        )

    monkeypatch.setenv("SOLANA_RPC_URL", "https://secret.invalid/rpc")
    monkeypatch.setenv("HELIUS_API_KEY", "secret")

    report = MODULE.execute_fresh_mutation_preview(
        preview_path=path,
        expected_preview_sha256=digest,
        execute=True,
        runner=runner,
    )

    reviewed_argv = list(freshness.current_preview["mutation_argv"])
    command = seen["command"]
    tool_fd = seen["kwargs"]["pass_fds"][0]
    assert command[:3] == [
        sys.executable,
        "-c",
        MODULE._EXACT_TOOL_BOOTSTRAP,
    ]
    assert command[3] == str(tool_fd)
    assert command[4] == str(tool.resolve())
    assert command[5:] == reviewed_argv[2:]
    assert "shell" not in seen["kwargs"]
    assert report.execution_requested is True
    assert report.mutation_executed is True
    assert report.exit_code == 0
    assert report.result_json_valid is True
    assert report.result_secret_safe is True
    assert report.result == {
        "applied": True,
        "token_mint": "safe-mint",
    }
    assert report.failure_category is None
    assert report.raw_stderr_exposed is False
    assert "must-not-leak" not in json.dumps(report.to_record())


def test_executor_redacts_sensitive_structured_result(
    tmp_path,
    monkeypatch,
):
    path, digest = private_preview(tmp_path)
    tool = reviewed_tool()
    freshness = freshness_for(tool)
    monkeypatch.setattr(
        MODULE.FRESH,
        "check_mutation_preview_freshness",
        lambda **kwargs: freshness,
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "api_key": "secret-key",
                    "endpoint": "https://rpc.invalid/?api-key=secret",
                    "token_mint": "not-sensitive",
                }
            ),
            stderr="",
        )

    report = MODULE.execute_fresh_mutation_preview(
        preview_path=path,
        expected_preview_sha256=digest,
        execute=True,
        runner=runner,
    )

    assert report.result_secret_safe is False
    assert report.result["api_key"] == "<redacted>"
    assert report.result["endpoint"] == "<redacted>"
    assert report.result["token_mint"] == "not-sensitive"
    encoded = json.dumps(report.to_record())
    assert "secret-key" not in encoded
    assert "api-key=secret" not in encoded


def test_executor_reports_nonzero_without_returning_stderr(
    tmp_path,
    monkeypatch,
):
    path, digest = private_preview(tmp_path)
    tool = reviewed_tool()
    freshness = freshness_for(tool)
    monkeypatch.setattr(
        MODULE.FRESH,
        "check_mutation_preview_freshness",
        lambda **kwargs: freshness,
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            2,
            stdout=json.dumps({"status": "blocked"}),
            stderr="private diagnostic https://rpc.invalid/?api-key=secret",
        )

    report = MODULE.execute_fresh_mutation_preview(
        preview_path=path,
        expected_preview_sha256=digest,
        execute=True,
        runner=runner,
    )

    assert report.exit_code == 2
    assert report.result_json_valid is True
    assert report.failure_category == "MUTATION_NONZERO_EXIT"
    encoded = json.dumps(report.to_record())
    assert "private diagnostic" not in encoded
    assert "api-key=secret" not in encoded


def test_executor_rejects_tool_digest_drift_after_freshness(
    tmp_path,
    monkeypatch,
):
    path, digest = private_preview(tmp_path)
    tool = reviewed_tool()
    freshness = freshness_for(tool)
    freshness.current_mutation_tool_sha256 = "0" * 64
    monkeypatch.setattr(
        MODULE.FRESH,
        "check_mutation_preview_freshness",
        lambda **kwargs: freshness,
    )

    with pytest.raises(ValueError, match="tool bytes changed"):
        MODULE.execute_fresh_mutation_preview(
            preview_path=path,
            expected_preview_sha256=digest,
        )


def test_executor_rejects_preview_change_during_freshness(
    tmp_path,
    monkeypatch,
):
    path, digest = private_preview(tmp_path)
    tool = reviewed_tool()
    freshness = freshness_for(tool)

    def check(**kwargs):
        path.write_text('{"preview":"changed"}\n', encoding="utf-8")
        path.chmod(0o600)
        return freshness

    monkeypatch.setattr(
        MODULE.FRESH,
        "check_mutation_preview_freshness",
        check,
    )

    with pytest.raises(ValueError, match="changed during freshness review"):
        MODULE.execute_fresh_mutation_preview(
            preview_path=path,
            expected_preview_sha256=digest,
        )



def test_capture_keeps_reviewed_inode_bytes_after_path_replacement(
    tmp_path,
    monkeypatch,
):
    tool = tmp_path / "reviewed-tool.py"
    original = b"print('captured')\n"
    replacement = b"print('replacement')\n"
    tool.write_bytes(original)

    monkeypatch.setattr(MODULE, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(
        MODULE.FRESH.RENDER.RUNNER,
        "REVIEWED_PREFLIGHT_TOOLS",
        frozenset({tool.name}),
    )

    argv = (sys.executable, str(tool), "--apply")
    path, encoded, opened, fd = MODULE._capture_mutation_tool(argv)
    try:
        assert encoded == original
        tool.unlink()
        tool.write_bytes(replacement)

        assert os.pread(fd, len(original), 0) == original
        with pytest.raises(ValueError, match="path changed after capture"):
            MODULE._assert_mutation_tool_path_stable(path, opened)
    finally:
        os.close(fd)


def test_captured_bootstrap_executes_descriptor_bytes_not_replaced_path(
    tmp_path,
    monkeypatch,
):
    tool = tmp_path / "reviewed-tool.py"
    tool.write_text(
        "import json,sys\n"
        "print(json.dumps({'version':'captured','file':__file__,"
        "'args':sys.argv[1:]}))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(MODULE, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(
        MODULE.FRESH.RENDER.RUNNER,
        "REVIEWED_PREFLIGHT_TOOLS",
        frozenset({tool.name}),
    )

    argv = (sys.executable, str(tool), "--value", "7", "--apply")
    path, _encoded, _opened, fd = MODULE._capture_mutation_tool(argv)
    try:
        command = MODULE._captured_mutation_command(
            argv=argv,
            tool_path=path,
            tool_fd=fd,
        )

        tool.unlink()
        tool.write_text(
            "import json\n"
            "print(json.dumps({'version':'replacement'}))\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            pass_fds=(fd,),
        )
    finally:
        os.close(fd)

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["version"] == "captured"
    assert payload["file"] == str(path)
    assert payload["args"] == ["--value", "7", "--apply"]


def test_executor_never_launches_mutation_tool_path_directly():
    source = TOOL.read_text(encoding="utf-8")

    assert "pass_fds=(mutation_tool_fd,)" in source
    assert "_EXACT_TOOL_BOOTSTRAP" in source
    assert "runner(\n        list(argv)," not in source
    assert "Path(argv[1]).resolve().read_bytes()" not in source
