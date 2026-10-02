#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
READINESS_TOOL = TOOLS_DIR / "check_phase2_isolated_timer_readiness.py"


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
    "phase2_isolated_timer_activation_readiness",
)

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
    readiness = READINESS.inspect_timer_readiness(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
    )

    if not readiness.timer_ready:
        return Phase2TimerActivationReport(
            timer_ready=False,
            apply_requested=apply,
            applied=False,
            daemon_reload_performed=False,
            timer_enabled=False,
            timer_active=False,
            failure_step="PREFLIGHT_NOT_READY",
            rollback_performed=False,
            rollback_succeeded=True,
            detector_services_untouched=True,
            legacy_services_untouched=True,
            direct_rpc_called=False,
            timer_may_trigger_rpc_cycles=False,
            service_control_performed=False,
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

    failure: str | None = None
    if not _systemctl(runner, "enable", "--now", TIMER_UNIT):
        failure = "ENABLE_NOW"
    enabled = _state(runner, "is-enabled") if failure is None else False
    if failure is None and not enabled:
        failure = "VERIFY_ENABLED"
    active = _state(runner, "is-active") if failure is None else False
    if failure is None and not active:
        failure = "VERIFY_ACTIVE"

    if failure is None:
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

    rollback_succeeded = _rollback(runner)
    return Phase2TimerActivationReport(
        timer_ready=True,
        apply_requested=True,
        applied=False,
        daemon_reload_performed=True,
        timer_enabled=False if rollback_succeeded else enabled,
        timer_active=False if rollback_succeeded else active,
        failure_step=failure,
        rollback_performed=True,
        rollback_succeeded=rollback_succeeded,
        detector_services_untouched=True,
        legacy_services_untouched=True,
        direct_rpc_called=False,
        # enable --now may have partially started the timer even when
        # systemd reports failure, so fail closed in the activity report.
        timer_may_trigger_rpc_cycles=True,
        service_control_performed=True,
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
