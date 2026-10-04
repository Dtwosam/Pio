#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
READINESS_TOOL = TOOLS_DIR / "check_phase2_isolated_smoke_readiness.py"
READINESS_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_smoke_readiness.py"
_MAX_TOOL_BYTES = 4 * 1024 * 1024
_MAX_ENV_BYTES = 1024 * 1024


def _assert_file_path_stable(
    path: Path,
    opened: os.stat_result,
    *,
    label: str,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} path changed after capture") from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} path changed after capture")


def _capture_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> tuple[Path, bytes, os.stat_result]:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} is missing") from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        raise ValueError(f"{label} cannot be opened safely") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise ValueError(f"{label} size is invalid")

        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)

        after = os.fstat(fd)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or not stat.S_ISREG(after.st_mode)
        ):
            raise ValueError(f"{label} changed while reading")
    finally:
        os.close(fd)

    if len(encoded) != before.st_size:
        raise ValueError(f"{label} changed while reading")
    _assert_file_path_stable(resolved, before, label=label)
    return resolved, encoded, before


def _load_captured_readiness(
    path: Path,
    name: str,
) -> tuple[Any, Path, bytes, os.stat_result]:
    label = "reviewed smoke readiness checker"
    resolved, encoded, opened = _capture_regular_file(
        path,
        label=label,
        max_bytes=_MAX_TOOL_BYTES,
    )
    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {resolved}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        code = compile(encoded, str(resolved), "exec")
        exec(code, module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    _assert_file_path_stable(resolved, opened, label=label)
    return module, resolved, encoded, opened


(
    READINESS,
    _READINESS_PATH_AT_LOAD,
    _READINESS_BYTES_AT_LOAD,
    _READINESS_STAT_AT_LOAD,
) = _load_captured_readiness(
    READINESS_TOOL,
    "phase2_isolated_smoke_runner_readiness",
)
_READINESS_SHA256_AT_LOAD = hashlib.sha256(
    _READINESS_BYTES_AT_LOAD
).hexdigest()

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


def _readiness_source_identity() -> tuple[str, str]:
    raw = Path(READINESS_TOOL).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed smoke readiness checker is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            "reviewed smoke readiness checker is missing or symlinked"
        ) from exc
    if resolved != _READINESS_PATH_AT_LOAD:
        raise ValueError("reviewed smoke readiness checker path changed after load")
    _assert_file_path_stable(
        _READINESS_PATH_AT_LOAD,
        _READINESS_STAT_AT_LOAD,
        label="reviewed smoke readiness checker",
    )

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if head.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = head.stdout.strip()
    if len(commit) not in {40, 64}:
        raise ValueError("reviewed source commit is invalid")

    historical = subprocess.run(
        ["git", "show", f"{commit}:{READINESS_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed smoke readiness checker is not present at reviewed source commit"
        )
    if historical.stdout != _READINESS_BYTES_AT_LOAD:
        raise ValueError(
            "reviewed smoke readiness checker bytes do not match reviewed source commit"
        )
    _assert_file_path_stable(
        _READINESS_PATH_AT_LOAD,
        _READINESS_STAT_AT_LOAD,
        label="reviewed smoke readiness checker",
    )
    return commit, _READINESS_SHA256_AT_LOAD


def _parse_env_bytes(encoded: bytes) -> dict[str, str]:
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("environment file is not valid UTF-8") from exc

    values: dict[str, str] = {}
    for raw in text.splitlines():
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


def _regular_file_identity(
    path: Path,
    *,
    label: str,
) -> tuple[Path, os.stat_result]:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} is missing") from exc
    current = os.stat(resolved, follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved, current


def _assert_runtime_target_stable(
    runtime_root: str | Path,
    expected: Path,
) -> None:
    current = _runtime_current(runtime_root)
    if current != expected:
        raise ValueError("isolated runtime current target changed before smoke launch")


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

    readiness_source_before = _readiness_source_identity()
    readiness = READINESS.inspect_smoke_readiness(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
    )
    readiness_source_after = _readiness_source_identity()
    if readiness_source_after != readiness_source_before:
        raise ValueError("reviewed smoke readiness source changed during inspection")
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
    executor, executor_stat = _regular_file_identity(
        runtime / "rust-executor/target/release/meteora-executor",
        label="reviewed isolated executor",
    )
    python_path, python_stat = _regular_file_identity(
        Path(python_executable),
        label="Python executable",
    )

    env_path, env_bytes, env_stat = _capture_regular_file(
        Path(env_file),
        label="environment file",
        max_bytes=_MAX_ENV_BYTES,
    )
    loaded_env = _parse_env_bytes(env_bytes)
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

    readiness_source_before_launch = _readiness_source_identity()
    if readiness_source_before_launch != readiness_source_before:
        raise ValueError("reviewed smoke readiness source changed before launch")
    _assert_file_path_stable(
        env_path,
        env_stat,
        label="environment file",
    )
    _assert_runtime_target_stable(runtime_root, runtime)
    _assert_file_path_stable(
        executor,
        executor_stat,
        label="reviewed isolated executor",
    )
    _assert_file_path_stable(
        python_path,
        python_stat,
        label="Python executable",
    )

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
