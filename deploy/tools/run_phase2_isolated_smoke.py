#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
READINESS_TOOL = TOOLS_DIR / "check_phase2_isolated_smoke_readiness.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


READINESS = _load(
    READINESS_TOOL,
    "phase2_isolated_smoke_runner_readiness",
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class SmokeStage:
    name: str
    status: str
    failure_category: str | None


@dataclass(frozen=True)
class Phase2IsolatedSmokeReport:
    smoke_ready: bool
    apply_requested: bool
    executed: bool
    exit_code: int | None
    output_valid: bool
    stages: tuple[SmokeStage, ...]
    failed_stages: int
    skipped_stages: int
    rpc_rate_limited: bool
    progress_evidence_id: int | None
    receipt_path: str
    receipt_written: bool
    smoke_passed: bool
    direct_rpc_called: bool
    evidence_cycle_may_call_rpc: bool
    database_write_may_have_occurred: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _read_env(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _runtime_current(runtime_root: str | Path) -> Path:
    root = Path(runtime_root).expanduser()
    if root.is_symlink():
        raise ValueError("runtime root must not be a symlink")
    current = root / "current"
    if not current.is_symlink():
        raise ValueError("isolated runtime current path must be a symlink")
    resolved = current.resolve(strict=True)
    releases = (root / "releases").resolve()
    try:
        resolved.relative_to(releases)
    except ValueError as exc:
        raise ValueError("runtime current target is outside releases") from exc
    return resolved


def _parse_output(stdout: str) -> tuple[
    bool,
    tuple[SmokeStage, ...],
    int | None,
    str | None,
]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False, (), None, None
    if not isinstance(payload, dict):
        return False, (), None, None

    raw_stages = payload.get("stages")
    if not isinstance(raw_stages, list):
        return False, (), None, None

    stages: list[SmokeStage] = []
    for item in raw_stages:
        if not isinstance(item, dict):
            return False, (), None, None
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or not isinstance(status, str):
            return False, (), None, None
        failure = item.get("failure_category")
        if failure is not None and not isinstance(failure, str):
            return False, (), None, None
        stages.append(
            SmokeStage(
                name=name,
                status=status,
                failure_category=failure,
            )
        )

    evidence_id = payload.get("progress_evidence_id")
    if not isinstance(evidence_id, int) or evidence_id <= 0:
        evidence_id = None

    finished_at = payload.get("finished_at")
    if not isinstance(finished_at, str) or not finished_at:
        finished_at = None

    return True, tuple(stages), evidence_id, finished_at


def _write_receipt(
    path: Path,
    *,
    runtime_target: Path,
    progress_evidence_id: int,
    finished_at: str,
    stages: tuple[SmokeStage, ...],
) -> None:
    if path.exists() and path.is_symlink():
        raise ValueError("smoke receipt path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": 1,
        "runtime_target": str(runtime_target),
        "progress_evidence_id": progress_evidence_id,
        "finished_at": finished_at,
        "stage_statuses": [
            {
                "name": item.name,
                "status": item.status,
                "failure_category": item.failure_category,
            }
            for item in stages
        ],
        "smoke_passed": True,
        "eligible_for_timer_enable_preflight": True,
    }
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def run_smoke(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    python_executable: str | Path = "/opt/pio/python-learner/.venv/bin/python",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    timeout_seconds: int = 900,
    apply: bool = False,
    runner: Runner = subprocess.run,
) -> Phase2IsolatedSmokeReport:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    readiness = READINESS.inspect_smoke_readiness(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
    )
    receipt = Path(receipt_path).expanduser()

    if not readiness.smoke_ready:
        return Phase2IsolatedSmokeReport(
            smoke_ready=False,
            apply_requested=apply,
            executed=False,
            exit_code=None,
            output_valid=False,
            stages=(),
            failed_stages=0,
            skipped_stages=0,
            rpc_rate_limited=False,
            progress_evidence_id=None,
            receipt_path=str(receipt),
            receipt_written=False,
            smoke_passed=False,
            direct_rpc_called=False,
            evidence_cycle_may_call_rpc=False,
            database_write_may_have_occurred=False,
            service_control_performed=False,
        )

    if not apply:
        return Phase2IsolatedSmokeReport(
            smoke_ready=True,
            apply_requested=False,
            executed=False,
            exit_code=None,
            output_valid=False,
            stages=(),
            failed_stages=0,
            skipped_stages=0,
            rpc_rate_limited=False,
            progress_evidence_id=None,
            receipt_path=str(receipt),
            receipt_written=False,
            smoke_passed=False,
            direct_rpc_called=False,
            evidence_cycle_may_call_rpc=False,
            database_write_may_have_occurred=False,
            service_control_performed=False,
        )

    runtime = _runtime_current(runtime_root)
    executor = runtime / "rust-executor/target/release/meteora-executor"
    if not executor.is_file() or executor.is_symlink():
        raise ValueError("reviewed isolated executor is missing")

    python_path = Path(python_executable)
    if not python_path.is_file() or python_path.is_symlink():
        raise ValueError("Python executable is missing")

    env_path = Path(env_file).expanduser()
    loaded_env = _read_env(env_path)
    env = os.environ.copy()
    env.update(loaded_env)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(runtime / "python-learner/src")

    command = [
        str(python_path),
        "-m",
        "meteora_learner.phase2_evidence_cycle",
        "--database",
        str(Path(data_root) / "pio.db"),
        "--executor",
        str(executor),
        "--persist-progress",
    ]

    completed = runner(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
        env=env,
        cwd=str(runtime),
    )

    output_valid, stages, evidence_id, finished_at = _parse_output(
        completed.stdout or ""
    )
    failed = sum(item.status == "FAILED" for item in stages)
    skipped = sum(item.status == "SKIPPED" for item in stages)
    rate_limited = any(
        item.failure_category in {"RPC_RATE_LIMITED", "RPC_CIRCUIT_OPEN"}
        for item in stages
    )
    accepted_exit = int(completed.returncode) in {0, 2}
    passed = bool(
        accepted_exit
        and output_valid
        and stages
        and failed == 0
        and skipped == 0
        and not rate_limited
        and evidence_id is not None
        and finished_at is not None
    )

    receipt_written = False
    if passed:
        _write_receipt(
            receipt,
            runtime_target=runtime,
            progress_evidence_id=evidence_id,
            finished_at=finished_at,
            stages=stages,
        )
        receipt_written = True

    return Phase2IsolatedSmokeReport(
        smoke_ready=True,
        apply_requested=True,
        executed=True,
        exit_code=int(completed.returncode),
        output_valid=output_valid,
        stages=stages,
        failed_stages=failed,
        skipped_stages=skipped,
        rpc_rate_limited=rate_limited,
        progress_evidence_id=evidence_id,
        receipt_path=str(receipt),
        receipt_written=receipt_written,
        smoke_passed=passed,
        direct_rpc_called=False,
        evidence_cycle_may_call_rpc=True,
        database_write_may_have_occurred=True,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run one manual bounded isolated Phase-2 evidence smoke cycle "
            "after the read-only smoke-readiness gate. This may perform RPC "
            "reads and persist evidence when --apply is supplied."
        )
    )
    parser.add_argument(
        "--runtime-root",
        default="/opt/pio-phase2-runtime",
    )
    parser.add_argument(
        "--unit-destination",
        default="/etc/systemd/system",
    )
    parser.add_argument(
        "--env-file",
        default="/etc/pio/pio.env",
    )
    parser.add_argument(
        "--data-root",
        default="/opt/pio/data",
    )
    parser.add_argument(
        "--python",
        default="/opt/pio/python-learner/.venv/bin/python",
    )
    parser.add_argument(
        "--receipt",
        default="/opt/pio/data/phase2-isolated-smoke-receipt.json",
    )
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = run_smoke(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        python_executable=args.python,
        receipt_path=args.receipt,
        timeout_seconds=args.timeout_seconds,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))

    if args.apply and not report.smoke_passed:
        raise SystemExit(2)
    if not args.apply and not report.smoke_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
