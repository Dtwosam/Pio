from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/run_phase2_isolated_smoke.py"
SPEC = importlib.util.spec_from_file_location(
    "run_phase2_isolated_smoke",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def install_readiness(monkeypatch, *, ready=True):
    monkeypatch.setattr(
        MODULE.READINESS,
        "inspect_smoke_readiness",
        lambda **kwargs: SimpleNamespace(smoke_ready=ready),
    )


def runtime_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "runtime"
    release = root / "releases" / "pin"
    executor = release / "rust-executor/target/release/meteora-executor"
    executor.parent.mkdir(parents=True)
    executor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executor.chmod(executor.stat().st_mode | stat.S_IXUSR)
    (release / "python-learner/src").mkdir(parents=True)
    current = root / "current"
    current.symlink_to(Path("releases") / "pin")
    return root, release


def python_executable(tmp_path: Path) -> Path:
    path = tmp_path / "python"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "pio.env"
    path.write_text(
        "SOLANA_RPC_URL=https://rpc.invalid/?api-key=secret\n"
        "PIO_PHASE2_POSITION_POOL=pool\n",
        encoding="utf-8",
    )
    return path


def payload(*, failed=False, skipped=False, rate_limited=False):
    stages = [
        {
            "name": "RESEARCH_QUOTES",
            "status": "SUCCESS",
            "failure_category": None,
        },
        {
            "name": "POSITION_OBSERVATIONS",
            "status": (
                "FAILED"
                if failed or rate_limited
                else "PARTIAL"
            ),
            "failure_category": (
                "RPC_RATE_LIMITED"
                if rate_limited
                else ("POSITION_STAGE_FAILED" if failed else None)
            ),
        },
        {
            "name": "TRANSACTION_REINSPECTION",
            "status": "SKIPPED" if skipped else "SUCCESS",
            "failure_category": "RPC_CIRCUIT_OPEN" if skipped else None,
        },
        {
            "name": "PRESTATE_VERIFICATION",
            "status": "SUCCESS",
            "failure_category": None,
        },
        {
            "name": "RECONCILIATION_CORPUS",
            "status": "PARTIAL",
            "failure_category": None,
        },
        {
            "name": "CALIBRATION_EVIDENCE",
            "status": "PARTIAL",
            "failure_category": None,
        },
        {
            "name": "CALIBRATION_WORK_QUEUE",
            "status": "PARTIAL",
            "failure_category": None,
        },
    ]
    return {
        "finished_at": "2026-10-02T19:00:00+00:00",
        "stages": stages,
        "progress_evidence_id": 123,
    }


def test_smoke_dry_run_is_non_mutating(tmp_path, monkeypatch):
    install_readiness(monkeypatch, ready=True)
    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        raise AssertionError("runner must not execute")

    report = MODULE.run_smoke(
        apply=False,
        receipt_path=tmp_path / "receipt.json",
        runner=runner,
    )

    assert report.smoke_ready is True
    assert report.executed is False
    assert report.receipt_written is False
    assert report.evidence_cycle_may_call_rpc is False
    assert calls == []


def test_smoke_accepts_partial_evidence_and_writes_receipt(
    tmp_path,
    monkeypatch,
):
    install_readiness(monkeypatch, ready=True)
    runtime_root, release = runtime_tree(tmp_path)
    python = python_executable(tmp_path)
    env = env_file(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "pio.db").write_text("db", encoding="utf-8")
    receipt = data / "receipt.json"
    seen_env = {}

    def runner(command, **kwargs):
        seen_env.update(kwargs["env"])
        return subprocess.CompletedProcess(
            command,
            2,
            stdout=json.dumps(payload()),
            stderr="private stderr https://rpc.invalid/?api-key=secret",
        )

    report = MODULE.run_smoke(
        runtime_root=runtime_root,
        env_file=env,
        data_root=data,
        python_executable=python,
        receipt_path=receipt,
        apply=True,
        runner=runner,
    )

    assert report.executed is True
    assert report.exit_code == 2
    assert report.smoke_passed is True
    assert report.failed_stages == 0
    assert report.skipped_stages == 0
    assert report.progress_evidence_id == 123
    assert report.receipt_written is True
    assert report.direct_rpc_called is False
    assert report.evidence_cycle_may_call_rpc is True
    assert seen_env["SOLANA_RPC_URL"].endswith("api-key=secret")
    encoded = json.dumps(report.to_record())
    assert "api-key=secret" not in encoded
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["runtime_target"] == str(release)
    assert saved["progress_evidence_id"] == 123
    assert saved["smoke_passed"] is True


def test_smoke_rejects_rpc_rate_limit_without_receipt(
    tmp_path,
    monkeypatch,
):
    install_readiness(monkeypatch, ready=True)
    runtime_root, _ = runtime_tree(tmp_path)
    python = python_executable(tmp_path)
    env = env_file(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "pio.db").write_text("db", encoding="utf-8")
    receipt = data / "receipt.json"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            2,
            stdout=json.dumps(payload(rate_limited=True, skipped=True)),
            stderr="",
        )

    report = MODULE.run_smoke(
        runtime_root=runtime_root,
        env_file=env,
        data_root=data,
        python_executable=python,
        receipt_path=receipt,
        apply=True,
        runner=runner,
    )

    assert report.smoke_passed is False
    assert report.rpc_rate_limited is True
    assert report.receipt_written is False
    assert receipt.exists() is False


def test_smoke_rejects_malformed_output(tmp_path, monkeypatch):
    install_readiness(monkeypatch, ready=True)
    runtime_root, _ = runtime_tree(tmp_path)
    python = python_executable(tmp_path)
    env = env_file(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "pio.db").write_text("db", encoding="utf-8")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, stdout="not json", stderr="secret"
        )

    report = MODULE.run_smoke(
        runtime_root=runtime_root,
        env_file=env,
        data_root=data,
        python_executable=python,
        receipt_path=data / "receipt.json",
        apply=True,
        runner=runner,
    )

    assert report.output_valid is False
    assert report.smoke_passed is False
    assert report.receipt_written is False


def test_smoke_requires_readiness_before_execution(tmp_path, monkeypatch):
    install_readiness(monkeypatch, ready=False)
    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        raise AssertionError("runner must not execute")

    report = MODULE.run_smoke(
        apply=True,
        receipt_path=tmp_path / "receipt.json",
        runner=runner,
    )

    assert report.smoke_ready is False
    assert report.executed is False
    assert report.database_write_may_have_occurred is False
    assert calls == []


def test_smoke_refuses_symlinked_receipt_on_success(
    tmp_path,
    monkeypatch,
):
    install_readiness(monkeypatch, ready=True)
    runtime_root, _ = runtime_tree(tmp_path)
    python = python_executable(tmp_path)
    env = env_file(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "pio.db").write_text("db", encoding="utf-8")
    target = data / "target.json"
    target.write_text("do not replace", encoding="utf-8")
    receipt = data / "receipt.json"
    receipt.symlink_to(target)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            2,
            stdout=json.dumps(payload()),
            stderr="",
        )

    try:
        MODULE.run_smoke(
            runtime_root=runtime_root,
            env_file=env,
            data_root=data,
            python_executable=python,
            receipt_path=receipt,
            apply=True,
            runner=runner,
        )
    except ValueError as exc:
        assert "must not be a symlink" in str(exc)
    else:
        raise AssertionError("expected symlink receipt rejection")
    assert target.read_text(encoding="utf-8") == "do not replace"



def test_smoke_runner_executes_captured_readiness_bytes():
    source = TOOL.read_text(encoding="utf-8")

    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "compile(encoded" in source
    assert "exec(code, module.__dict__)" in source
    assert "spec.loader.exec_module(module)" not in source
    assert "def _read_env(" not in source
    assert "def _parse_env_bytes(" in source


def test_smoke_aborts_if_readiness_identity_changes_before_launch(
    tmp_path,
    monkeypatch,
):
    install_readiness(monkeypatch, ready=True)
    runtime_root, _ = runtime_tree(tmp_path)
    python = python_executable(tmp_path)
    env = env_file(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "pio.db").write_text("db", encoding="utf-8")
    calls = []

    identities = iter(
        (
            ("a" * 40, "1" * 64),
            ("a" * 40, "1" * 64),
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_readiness_source_identity",
        lambda: next(identities),
    )

    def runner(*args, **kwargs):
        calls.append(args)
        raise AssertionError("runner must not execute")

    with pytest.raises(
        ValueError,
        match="readiness source changed before launch",
    ):
        MODULE.run_smoke(
            runtime_root=runtime_root,
            env_file=env,
            data_root=data,
            python_executable=python,
            receipt_path=data / "receipt.json",
            apply=True,
            runner=runner,
        )

    assert calls == []


def test_smoke_aborts_if_env_path_is_replaced_after_capture(
    tmp_path,
    monkeypatch,
):
    install_readiness(monkeypatch, ready=True)
    runtime_root, _ = runtime_tree(tmp_path)
    python = python_executable(tmp_path)
    env = env_file(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "pio.db").write_text("db", encoding="utf-8")
    calls = []

    real_parse = MODULE._parse_env_bytes

    def replace_after_parse(encoded):
        parsed = real_parse(encoded)
        replacement = env.with_name("replacement.env")
        replacement.write_text(
            "SOLANA_RPC_URL=https://different.invalid\n"
            "PIO_PHASE2_POSITION_POOL=pool\n",
            encoding="utf-8",
        )
        replacement.replace(env)
        return parsed

    monkeypatch.setattr(MODULE, "_parse_env_bytes", replace_after_parse)

    def runner(*args, **kwargs):
        calls.append(args)
        raise AssertionError("runner must not execute")

    with pytest.raises(
        ValueError,
        match="environment file path changed after capture",
    ):
        MODULE.run_smoke(
            runtime_root=runtime_root,
            env_file=env,
            data_root=data,
            python_executable=python,
            receipt_path=data / "receipt.json",
            apply=True,
            runner=runner,
        )

    assert calls == []


def test_smoke_aborts_if_runtime_current_changes_before_launch(
    tmp_path,
    monkeypatch,
):
    install_readiness(monkeypatch, ready=True)
    runtime_root, _ = runtime_tree(tmp_path)
    python = python_executable(tmp_path)
    env = env_file(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "pio.db").write_text("db", encoding="utf-8")
    calls = []

    other = runtime_root / "releases" / "other"
    other_executor = other / "rust-executor/target/release/meteora-executor"
    other_executor.parent.mkdir(parents=True)
    other_executor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    other_executor.chmod(
        other_executor.stat().st_mode | stat.S_IXUSR
    )
    (other / "python-learner/src").mkdir(parents=True)

    real_parse = MODULE._parse_env_bytes

    def switch_runtime_after_parse(encoded):
        parsed = real_parse(encoded)
        current = runtime_root / "current"
        current.unlink()
        current.symlink_to(Path("releases") / "other")
        return parsed

    monkeypatch.setattr(
        MODULE,
        "_parse_env_bytes",
        switch_runtime_after_parse,
    )

    def runner(*args, **kwargs):
        calls.append(args)
        raise AssertionError("runner must not execute")

    with pytest.raises(
        ValueError,
        match="runtime current target changed before smoke launch",
    ):
        MODULE.run_smoke(
            runtime_root=runtime_root,
            env_file=env,
            data_root=data,
            python_executable=python,
            receipt_path=data / "receipt.json",
            apply=True,
            runner=runner,
        )

    assert calls == []
