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
READINESS_TOOL = TOOLS_DIR / "check_phase2_isolated_timer_readiness.py"
READINESS_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_timer_readiness.py"

_MAX_TOOL_BYTES = 4 * 1024 * 1024
_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_UNIT_BYTES = 1024 * 1024

ISOLATED_UNIT_FILES = (
    "pio-phase2-isolated-prestate-stream@.service",
    "pio-phase2-isolated-add-detector.service",
    "pio-phase2-isolated-evidence-cycle.service",
    "pio-phase2-isolated-evidence-cycle.timer",
    "pio-phase2-isolated-rate-limit-pause.service",
)


@dataclass(frozen=True)
class _CapturedFile:
    path: Path
    encoded: bytes
    opened: os.stat_result


@dataclass(frozen=True)
class _ActivationInputs:
    env_file: _CapturedFile
    unit_files: tuple[_CapturedFile, ...]


def _assert_regular_path_stable(
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
        or current.st_mode != opened.st_mode
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
) -> _CapturedFile:
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
        if before.st_size < 0 or before.st_size > max_bytes:
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
            or after.st_mode != before.st_mode
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
    _assert_regular_path_stable(resolved, before, label=label)
    return _CapturedFile(path=resolved, encoded=encoded, opened=before)


def _load_captured_readiness(
    path: Path,
    name: str,
) -> tuple[Any, _CapturedFile]:
    captured = _capture_regular_file(
        path,
        label="reviewed timer readiness checker",
        max_bytes=_MAX_TOOL_BYTES,
    )
    spec = importlib.util.spec_from_file_location(name, captured.path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {captured.path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        code = compile(captured.encoded, str(captured.path), "exec")
        exec(code, module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    _assert_regular_path_stable(
        captured.path,
        captured.opened,
        label="reviewed timer readiness checker",
    )
    return module, captured


READINESS, _READINESS_CAPTURE = _load_captured_readiness(
    READINESS_TOOL,
    "phase2_isolated_timer_activation_readiness",
)
_READINESS_SHA256 = hashlib.sha256(_READINESS_CAPTURE.encoded).hexdigest()

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]

TIMER_UNIT = "pio-phase2-isolated-evidence-cycle.timer"


@dataclass(frozen=True)
class Phase2TimerActivationReport:
    timer_ready: bool
    apply_requested: bool
    applied: bool
    daemon_reload_performed: bool
    timer_enabled: bool
    timer_active: bool
    failure_step: str | None
    rollback_performed: bool
    rollback_succeeded: bool
    detector_services_untouched: bool
    legacy_services_untouched: bool
    direct_rpc_called: bool
    timer_may_trigger_rpc_cycles: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _repo_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = completed.stdout.strip()
    if len(commit) not in {40, 64}:
        raise ValueError("reviewed source commit is invalid")
    return commit


def _readiness_source_identity() -> tuple[str, str]:
    _assert_regular_path_stable(
        _READINESS_CAPTURE.path,
        _READINESS_CAPTURE.opened,
        label="reviewed timer readiness checker",
    )
    commit = _repo_head()
    historical = subprocess.run(
        ["git", "show", f"{commit}:{READINESS_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed timer readiness checker is not present at reviewed source commit"
        )
    if historical.stdout != _READINESS_CAPTURE.encoded:
        raise ValueError(
            "reviewed timer readiness checker bytes do not match reviewed source commit"
        )
    _assert_regular_path_stable(
        _READINESS_CAPTURE.path,
        _READINESS_CAPTURE.opened,
        label="reviewed timer readiness checker",
    )
    return commit, _READINESS_SHA256


def _capture_activation_inputs(
    *,
    unit_destination: str | Path,
    env_file: str | Path,
) -> _ActivationInputs:
    env_capture = _capture_regular_file(
        Path(env_file),
        label="Phase-2 environment file",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    destination = Path(unit_destination).expanduser()
    if destination.is_symlink():
        raise ValueError("systemd unit destination must not be a symlink")
    try:
        resolved_destination = destination.resolve(strict=True)
    except OSError as exc:
        raise ValueError("systemd unit destination is missing") from exc
    if not resolved_destination.is_dir():
        raise ValueError("systemd unit destination must be a directory")

    units = tuple(
        _capture_regular_file(
            resolved_destination / name,
            label=f"installed unit {name}",
            max_bytes=_MAX_UNIT_BYTES,
        )
        for name in ISOLATED_UNIT_FILES
    )
    return _ActivationInputs(
        env_file=env_capture,
        unit_files=units,
    )


def _assert_activation_inputs_stable(
    inputs: _ActivationInputs,
) -> None:
    _assert_regular_path_stable(
        inputs.env_file.path,
        inputs.env_file.opened,
        label="Phase-2 environment file",
    )
    for captured in inputs.unit_files:
        _assert_regular_path_stable(
            captured.path,
            captured.opened,
            label=f"installed unit {captured.path.name}",
        )


def _systemctl(
    runner: SystemctlRunner,
    *args: str,
) -> bool:
    completed = runner(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(completed.returncode) == 0


def _state(
    runner: SystemctlRunner,
    action: str,
) -> bool:
    completed = runner(
        ["systemctl", action, TIMER_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    expected = "active" if action == "is-active" else "enabled"
    return (
        int(completed.returncode) == 0
        and (completed.stdout or "").strip() == expected
    )


def _rollback(runner: SystemctlRunner) -> bool:
    return _systemctl(runner, "disable", "--now", TIMER_UNIT)


def _not_ready_report(
    *,
    apply: bool,
    failure_step: str,
) -> Phase2TimerActivationReport:
    return Phase2TimerActivationReport(
        timer_ready=False,
        apply_requested=apply,
        applied=False,
        daemon_reload_performed=False,
        timer_enabled=False,
        timer_active=False,
        failure_step=failure_step,
        rollback_performed=False,
        rollback_succeeded=True,
        detector_services_untouched=True,
        legacy_services_untouched=True,
        direct_rpc_called=False,
        timer_may_trigger_rpc_cycles=False,
        service_control_performed=False,
    )


def _rollback_started_timer(
    runner: SystemctlRunner,
    *,
    failure_step: str,
    timer_ready: bool,
) -> Phase2TimerActivationReport:
    rollback_succeeded = _rollback(runner)
    if rollback_succeeded:
        enabled = False
        active = False
    else:
        enabled = _state(runner, "is-enabled")
        active = _state(runner, "is-active")
    return Phase2TimerActivationReport(
        timer_ready=timer_ready,
        apply_requested=True,
        applied=False,
        daemon_reload_performed=True,
        timer_enabled=enabled,
        timer_active=active,
        failure_step=failure_step,
        rollback_performed=True,
        rollback_succeeded=rollback_succeeded,
        detector_services_untouched=True,
        legacy_services_untouched=True,
        direct_rpc_called=False,
        # enable --now already crossed the service-control boundary; even a
        # successful rollback cannot prove that no timer-triggered work began.
        timer_may_trigger_rpc_cycles=True,
        service_control_performed=True,
    )


def _assert_activation_still_valid(
    *,
    readiness_snapshot: Any,
    inputs: _ActivationInputs,
    readiness_source: tuple[str, str],
) -> None:
    READINESS.assert_timer_readiness_snapshot_stable(readiness_snapshot)
    _assert_activation_inputs_stable(inputs)
    if _readiness_source_identity() != readiness_source:
        raise ValueError(
            "reviewed timer readiness checker changed after timer enable"
        )


def activate_timer(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    max_receipt_age_seconds: int = 1800,
    apply: bool = False,
    runner: SystemctlRunner = subprocess.run,
) -> Phase2TimerActivationReport:
    readiness_source = _readiness_source_identity()
    readiness, readiness_snapshot = READINESS.capture_timer_readiness(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
    )
    if _readiness_source_identity() != readiness_source:
        raise ValueError(
            "reviewed timer readiness checker changed during activation preflight"
        )

    if not readiness.timer_ready:
        return _not_ready_report(
            apply=apply,
            failure_step="PREFLIGHT_NOT_READY",
        )

    if not apply:
        return Phase2TimerActivationReport(
            timer_ready=True,
            apply_requested=False,
            applied=False,
            daemon_reload_performed=False,
            timer_enabled=False,
            timer_active=False,
            failure_step=None,
            rollback_performed=False,
            rollback_succeeded=True,
            detector_services_untouched=True,
            legacy_services_untouched=True,
            direct_rpc_called=False,
            timer_may_trigger_rpc_cycles=False,
            service_control_performed=False,
        )

    inputs = _capture_activation_inputs(
        unit_destination=unit_destination,
        env_file=env_file,
    )
    READINESS.assert_timer_readiness_snapshot_stable(readiness_snapshot)
    _assert_activation_inputs_stable(inputs)

    # Re-run the full read-only readiness gate while the exact env/unit
    # identities captured above are held stable. This binds the mutation to
    # the files systemd is about to consume rather than to an earlier pathname
    # observation.
    revalidated, revalidated_snapshot = READINESS.capture_timer_readiness(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
    )
    if not revalidated.timer_ready:
        return _not_ready_report(
            apply=True,
            failure_step="PREFLIGHT_CHANGED_BEFORE_APPLY",
        )

    READINESS.assert_timer_readiness_snapshot_stable(readiness_snapshot)
    READINESS.assert_timer_readiness_snapshot_stable(revalidated_snapshot)
    _assert_activation_inputs_stable(inputs)
    if _readiness_source_identity() != readiness_source:
        raise ValueError(
            "reviewed timer readiness checker changed before systemd mutation"
        )

    # A concurrently enabled/started timer invalidates the reviewed inactive
    # precondition. Abort before daemon-reload instead of treating enable --now
    # as an idempotent success.
    enabled_before = _state(runner, "is-enabled")
    active_before = _state(runner, "is-active")
    if enabled_before or active_before:
        return _not_ready_report(
            apply=True,
            failure_step="TIMER_STATE_CHANGED_BEFORE_APPLY",
        )

    READINESS.assert_timer_readiness_snapshot_stable(revalidated_snapshot)
    _assert_activation_inputs_stable(inputs)
    if _readiness_source_identity() != readiness_source:
        raise ValueError(
            "reviewed timer readiness checker changed immediately before daemon-reload"
        )

    if not _systemctl(runner, "daemon-reload"):
        return Phase2TimerActivationReport(
            timer_ready=True,
            apply_requested=True,
            applied=False,
            daemon_reload_performed=False,
            timer_enabled=False,
            timer_active=False,
            failure_step="DAEMON_RELOAD",
            rollback_performed=False,
            rollback_succeeded=True,
            detector_services_untouched=True,
            legacy_services_untouched=True,
            direct_rpc_called=False,
            timer_may_trigger_rpc_cycles=False,
            service_control_performed=False,
        )

    # daemon-reload has consumed the captured unit bytes. Keep the env,
    # runtime, receipt/database and readiness-tool identities stable through
    # the enable/start boundary as well.
    READINESS.assert_timer_readiness_snapshot_stable(revalidated_snapshot)
    _assert_activation_inputs_stable(inputs)
    if _readiness_source_identity() != readiness_source:
        raise ValueError(
            "reviewed timer readiness checker changed before timer enable"
        )

    failure: str | None = None
    if not _systemctl(runner, "enable", "--now", TIMER_UNIT):
        failure = "ENABLE_NOW"

    if failure is None:
        try:
            _assert_activation_still_valid(
                readiness_snapshot=revalidated_snapshot,
                inputs=inputs,
                readiness_source=readiness_source,
            )
        except ValueError:
            return _rollback_started_timer(
                runner,
                failure_step="POST_ENABLE_INPUT_DRIFT",
                timer_ready=False,
            )

    enabled = _state(runner, "is-enabled") if failure is None else False
    if failure is None and not enabled:
        failure = "VERIFY_ENABLED"
    active = _state(runner, "is-active") if failure is None else False
    if failure is None and not active:
        failure = "VERIFY_ACTIVE"

    if failure is None:
        try:
            _assert_activation_still_valid(
                readiness_snapshot=revalidated_snapshot,
                inputs=inputs,
                readiness_source=readiness_source,
            )
        except ValueError:
            return _rollback_started_timer(
                runner,
                failure_step="POST_VERIFY_INPUT_DRIFT",
                timer_ready=False,
            )

        return Phase2TimerActivationReport(
            timer_ready=True,
            apply_requested=True,
            applied=True,
            daemon_reload_performed=True,
            timer_enabled=True,
            timer_active=True,
            failure_step=None,
            rollback_performed=False,
            rollback_succeeded=True,
            detector_services_untouched=True,
            legacy_services_untouched=True,
            direct_rpc_called=False,
            timer_may_trigger_rpc_cycles=True,
            service_control_performed=True,
        )

    return _rollback_started_timer(
        runner,
        failure_step=failure,
        timer_ready=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Enable/start the isolated bounded Phase-2 evidence timer only "
            "after a fresh receipt-backed readiness gate. This tool does not "
            "touch detector or legacy services."
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
        "--receipt",
        default="/opt/pio/data/phase2-isolated-smoke-receipt.json",
    )
    parser.add_argument(
        "--max-receipt-age-seconds",
        type=int,
        default=1800,
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = activate_timer(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))
    if args.apply and not report.applied:
        raise SystemExit(2)
    if not args.apply and not report.timer_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
