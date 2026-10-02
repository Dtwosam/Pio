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
HEALTH_TOOL = TOOLS_DIR / "check_phase2_isolated_post_activation.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HEALTH = _load(
    HEALTH_TOOL,
    "phase2_evidence_timer_post_activation_health",
)

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]

EVIDENCE_TIMER = "pio-phase2-isolated-evidence-cycle.timer"


@dataclass(frozen=True)
class Phase2EvidenceTimerActivationReport:
    health_ready: bool
    apply_requested: bool
    applied: bool
    timer_enabled: bool
    timer_active: bool
    failure_step: str | None
    rollback_performed: bool
    rollback_succeeded: bool
    detector_untouched: bool
    streams_untouched: bool
    legacy_units_untouched: bool
    rpc_called_directly: bool
    timer_may_trigger_rpc: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _systemctl(
    runner: SystemctlRunner,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return runner(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _state(
    runner: SystemctlRunner,
    action: str,
    unit: str,
) -> bool:
    completed = _systemctl(runner, action, unit)
    value = (completed.stdout or "").strip()
    if action == "is-active":
        return int(completed.returncode) == 0 and value == "active"
    if action == "is-enabled":
        return int(completed.returncode) == 0 and value == "enabled"
    raise ValueError(f"unsupported state action: {action}")


def _rollback(runner: SystemctlRunner) -> bool:
    stopped = _systemctl(runner, "stop", EVIDENCE_TIMER)
    disabled = _systemctl(runner, "disable", EVIDENCE_TIMER)
    return (
        int(stopped.returncode) == 0
        and int(disabled.returncode) == 0
    )


def activate_evidence_timer(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    data_root: str | Path = "/opt/pio/data",
    apply: bool = False,
    runner: SystemctlRunner = subprocess.run,
) -> Phase2EvidenceTimerActivationReport:
    health = HEALTH.inspect_post_activation(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        data_root=data_root,
        runner=runner,
    )

    if not health.health_ready_for_evidence_timer:
        return Phase2EvidenceTimerActivationReport(
            health_ready=False,
            apply_requested=apply,
            applied=False,
            timer_enabled=False,
            timer_active=False,
            failure_step="POST_ACTIVATION_HEALTH_NOT_READY",
            rollback_performed=False,
            rollback_succeeded=True,
            detector_untouched=True,
            streams_untouched=True,
            legacy_units_untouched=True,
            rpc_called_directly=False,
            timer_may_trigger_rpc=False,
        )

    if not apply:
        return Phase2EvidenceTimerActivationReport(
            health_ready=True,
            apply_requested=False,
            applied=False,
            timer_enabled=False,
            timer_active=False,
            failure_step=None,
            rollback_performed=False,
            rollback_succeeded=True,
            detector_untouched=True,
            streams_untouched=True,
            legacy_units_untouched=True,
            rpc_called_directly=False,
            timer_may_trigger_rpc=False,
        )

    completed = _systemctl(
        runner,
        "enable",
        "--now",
        EVIDENCE_TIMER,
    )
    if int(completed.returncode) != 0:
        return Phase2EvidenceTimerActivationReport(
            health_ready=True,
            apply_requested=True,
            applied=False,
            timer_enabled=False,
            timer_active=False,
            failure_step="ENABLE_NOW_TIMER",
            rollback_performed=False,
            rollback_succeeded=True,
            detector_untouched=True,
            streams_untouched=True,
            legacy_units_untouched=True,
            rpc_called_directly=False,
            timer_may_trigger_rpc=False,
        )

    enabled = _state(
        runner,
        "is-enabled",
        EVIDENCE_TIMER,
    )
    active = _state(
        runner,
        "is-active",
        EVIDENCE_TIMER,
    )
    if enabled and active:
        return Phase2EvidenceTimerActivationReport(
            health_ready=True,
            apply_requested=True,
            applied=True,
            timer_enabled=True,
            timer_active=True,
            failure_step=None,
            rollback_performed=False,
            rollback_succeeded=True,
            detector_untouched=True,
            streams_untouched=True,
            legacy_units_untouched=True,
            rpc_called_directly=False,
            timer_may_trigger_rpc=True,
        )

    rollback_ok = _rollback(runner)
    return Phase2EvidenceTimerActivationReport(
        health_ready=True,
        apply_requested=True,
        applied=False,
        timer_enabled=enabled,
        timer_active=active,
        failure_step="VERIFY_TIMER",
        rollback_performed=True,
        rollback_succeeded=rollback_ok,
        detector_untouched=True,
        streams_untouched=True,
        legacy_units_untouched=True,
        rpc_called_directly=False,
        timer_may_trigger_rpc=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Enable the isolated Phase-2 bounded evidence timer only after "
            "the reviewed post-activation health gate passes."
        )
    )
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = activate_evidence_timer(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        data_root=args.data_root,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))
    if args.apply and not report.applied:
        raise SystemExit(2)
    if not args.apply and not report.health_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
