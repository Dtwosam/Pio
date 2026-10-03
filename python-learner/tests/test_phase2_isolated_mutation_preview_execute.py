from __future__ import annotations

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

    assert seen["command"] == list(freshness.current_preview["mutation_argv"])
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
