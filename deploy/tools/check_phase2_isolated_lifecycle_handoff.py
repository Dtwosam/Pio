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
ACTIVATION_TOOL = TOOLS_DIR / "check_phase2_isolated_activation.py"
SMOKE_TOOL = TOOLS_DIR / "check_phase2_isolated_smoke_readiness.py"
TIMER_TOOL = TOOLS_DIR / "check_phase2_isolated_timer_readiness.py"
OPERATOR_TOOL = TOOLS_DIR / "check_phase2_isolated_operator_status.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ACTIVATION = _load(ACTIVATION_TOOL, "phase2_lifecycle_activation")
SMOKE = _load(SMOKE_TOOL, "phase2_lifecycle_smoke")
TIMER = _load(TIMER_TOOL, "phase2_lifecycle_timer")
OPERATOR = _load(OPERATOR_TOOL, "phase2_lifecycle_operator")

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Phase2LifecycleHandoffReport:
    state: str
    next_action: str
    next_tool: str | None
    attention_required: bool
    runtime_ready: bool
    installed_units_exact: bool
    activation_ready: bool
    smoke_ready: bool
    timer_ready: bool
    collection_running: bool
    provider_rate_limit_incident: bool
    provider_rate_limit_paused: bool
    blockers: tuple[str, ...]
    activation: dict[str, Any]
    smoke_readiness: dict[str, Any] | None
    timer_readiness: dict[str, Any] | None
    operator_status: dict[str, Any] | None
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _boundary_ok(report: Any) -> bool:
    if not bool(getattr(report, "read_only", False)):
        return False
    if bool(getattr(report, "rpc_called", True)):
        return False
    if bool(getattr(report, "service_control_performed", True)):
        return False
    if bool(getattr(report, "database_write_performed", False)):
        return False
    if bool(getattr(report, "daemon_reload_performed", False)):
        return False
    return True


def _activation_blockers(report: Any) -> tuple[str, ...]:
    blockers: list[str] = []
    if not bool(report.runtime_ready):
        blockers.append("RUNTIME_NOT_READY")
    if not bool(report.installed_units_exact):
        blockers.append("SYSTEMD_UNITS_NOT_EXACT")
    if not bool(report.env_file_regular):
        blockers.append("ENV_FILE_NOT_READY")
    if any(not bool(item.configured) for item in report.env_keys):
        blockers.append("REQUIRED_ENV_KEYS_MISSING")
    if not bool(report.position_pool_matches_detector_topology):
        blockers.append("POSITION_POOL_NOT_IN_DETECTOR_TOPOLOGY")
    if any(
        not (
            bool(item.exists)
            and bool(item.regular_file)
            and not bool(item.symlink)
        )
        for item in report.data_files
    ):
        blockers.append("PHASE2_DATA_FILES_NOT_READY")
    if not bool(report.detector_state_valid):
        blockers.append("DETECTOR_STATE_INVALID")
    if not bool(report.detector_cursors_complete):
        blockers.append("DETECTOR_CURSORS_INCOMPLETE")
    if any(not bool(item.ready) for item in report.unit_states):
        blockers.append("UNIT_STATE_CONFLICT")
    return tuple(blockers)


def _smoke_blockers(report: Any) -> tuple[str, ...]:
    checks = (
        ("RUNTIME_NOT_READY", report.runtime_ready),
        ("SYSTEMD_UNITS_NOT_EXACT", report.installed_units_exact),
        ("ENV_NOT_READY", report.env_ready),
        ("DATA_NOT_READY", report.data_ready),
        ("DETECTOR_STATE_NOT_READY", report.detector_state_ready),
        ("LEGACY_COLLECTORS_NOT_QUIESCENT", report.legacy_collectors_quiescent),
        ("PRESTATE_STREAMS_NOT_ACTIVE", report.streams_active),
        ("DETECTOR_NOT_ACTIVE", report.detector_active),
        ("DETECTOR_NOT_ENABLED", report.detector_enabled),
        ("EVIDENCE_SERVICE_ACTIVE", report.evidence_service_inactive),
        ("EVIDENCE_TIMER_ACTIVE", report.evidence_timer_inactive),
        ("EVIDENCE_TIMER_ENABLED", report.evidence_timer_disabled),
    )
    return tuple(name for name, ready in checks if not bool(ready))


def _timer_blockers(report: Any) -> tuple[str, ...]:
    checks = (
        ("SMOKE_TOPOLOGY_NOT_CURRENT", report.smoke_readiness_current),
        ("SMOKE_RECEIPT_NOT_REGULAR", report.receipt_regular),
        ("SMOKE_RECEIPT_INVALID", report.receipt_valid),
        ("SMOKE_RECEIPT_RUNTIME_MISMATCH", report.receipt_runtime_matches),
        ("SMOKE_RECEIPT_STALE", report.receipt_fresh),
        ("SMOKE_RECEIPT_STAGE_FAILURE", report.receipt_stage_statuses_valid),
        ("SMOKE_PROGRESS_EVIDENCE_MISSING", report.evidence_row_present),
        ("SMOKE_PROGRESS_EVIDENCE_MISMATCH", report.evidence_row_matches_receipt),
        ("SMOKE_RECEIPT_NOT_LATEST", report.receipt_is_latest_for_pool),
        ("SMOKE_PROGRESS_QUALIFIED", report.evidence_row_non_qualified),
        ("SMOKE_PROGRESS_CROSSED_PROMOTION_BOUNDARY", report.evidence_row_no_promotion),
    )
    return tuple(name for name, ready in checks if not bool(ready))


def inspect_lifecycle_handoff(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    max_receipt_age_seconds: int = 1800,
    runner: SystemctlRunner = subprocess.run,
) -> Phase2LifecycleHandoffReport:
    activation = ACTIVATION.inspect_activation(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        runner=runner,
    )
    if not _boundary_ok(activation):
        raise ValueError("Phase-2 activation report crossed the read-only boundary")

    activation_record = activation.to_record()
    smoke = None
    timer = None
    operator = None

    state = "ACTIVATION_PREREQUISITES_BLOCKED"
    next_action = "FIX_ACTIVATION_PREREQUISITES"
    next_tool: str | None = "check_phase2_isolated_activation.py"
    blockers = _activation_blockers(activation)
    activation_ready = bool(activation.activation_ready)
    smoke_ready = False
    timer_ready = False
    collection_running = False
    rate_limit_incident = False
    rate_limit_paused = False

    if not bool(activation.runtime_ready):
        state = "RUNTIME_NOT_READY"
        next_action = "STAGE_REVIEWED_RUNTIME"
        next_tool = "stage_phase2_isolated_runtime.py"
    elif not bool(activation.installed_units_exact):
        state = "SYSTEMD_UNITS_NOT_READY"
        next_action = "INSTALL_REVIEWED_UNITS"
        next_tool = "install_phase2_isolated_systemd_units.py"
    else:
        smoke = SMOKE.inspect_smoke_readiness(
            runtime_root=runtime_root,
            unit_destination=unit_destination,
            env_file=env_file,
            data_root=data_root,
            runner=runner,
        )
        if not _boundary_ok(smoke):
            raise ValueError("Phase-2 smoke report crossed the read-only boundary")
        smoke_ready = bool(smoke.smoke_ready)

        operator = OPERATOR.inspect_operator_status(
            runtime_root=runtime_root,
            unit_destination=unit_destination,
            env_file=env_file,
            data_root=data_root,
            runner=runner,
        )
        if not _boundary_ok(operator):
            raise ValueError("Phase-2 operator report crossed the read-only boundary")
        collection_running = bool(operator.collection_running)
        rate_limit_incident = bool(operator.provider_rate_limit_incident)
        rate_limit_paused = bool(operator.provider_rate_limit_paused)

        if rate_limit_incident and rate_limit_paused:
            state = "RATE_LIMIT_PAUSED"
            next_action = "KEEP_TIMER_PAUSED_UNTIL_PROVIDER_RECOVERS"
            next_tool = "check_phase2_isolated_operator_status.py"
            blockers = ("ACTIVE_PROVIDER_RATE_LIMIT_INCIDENT",)
        elif collection_running:
            if operator.state == "HEALTHY":
                state = "RUNNING_HEALTHY"
                next_action = "MONITOR_ZERO_RPC_STATUS"
                next_tool = "check_phase2_isolated_operator_status.py"
                blockers = ()
            elif operator.state == "RATE_LIMIT_PAUSE_REQUIRED":
                state = "RATE_LIMIT_PAUSE_REQUIRED"
                next_action = "RUN_RATE_LIMIT_AUTOPAUSE"
                next_tool = "autopause_phase2_isolated_timer.py"
                blockers = ("REPEATED_PROVIDER_REJECTION",)
            else:
                state = "RUNNING_ATTENTION_REQUIRED"
                next_action = "REVIEW_OPERATOR_STATUS"
                next_tool = "check_phase2_isolated_operator_status.py"
                blockers = (str(operator.state),)
        elif smoke_ready:
            timer = TIMER.inspect_timer_readiness(
                runtime_root=runtime_root,
                unit_destination=unit_destination,
                env_file=env_file,
                data_root=data_root,
                receipt_path=receipt_path,
                max_receipt_age_seconds=max_receipt_age_seconds,
            )
            if not _boundary_ok(timer):
                raise ValueError("Phase-2 timer report crossed the read-only boundary")
            timer_ready = bool(timer.timer_ready)
            if timer_ready:
                state = "TIMER_ACTIVATION_READY"
                next_action = "ACTIVATE_EVIDENCE_TIMER"
                next_tool = "activate_phase2_isolated_timer.py"
                blockers = ()
            else:
                state = "SMOKE_REQUIRED"
                next_action = "RUN_ONE_SHOT_SMOKE"
                next_tool = "run_phase2_isolated_smoke.py"
                blockers = _timer_blockers(timer)
        elif activation_ready:
            state = "DETECTOR_ACTIVATION_READY"
            next_action = "ACTIVATE_PRESTATE_STREAMS_AND_DETECTOR"
            next_tool = "activate_phase2_isolated_detector.py"
            blockers = ()
        else:
            state = "ACTIVE_TOPOLOGY_NOT_READY"
            next_action = "REPAIR_OR_COMPLETE_ACTIVE_TOPOLOGY"
            next_tool = "check_phase2_isolated_smoke_readiness.py"
            blockers = _smoke_blockers(smoke)

    return Phase2LifecycleHandoffReport(
        state=state,
        next_action=next_action,
        next_tool=next_tool,
        attention_required=state != "RUNNING_HEALTHY",
        runtime_ready=bool(activation.runtime_ready),
        installed_units_exact=bool(activation.installed_units_exact),
        activation_ready=activation_ready,
        smoke_ready=smoke_ready,
        timer_ready=timer_ready,
        collection_running=collection_running,
        provider_rate_limit_incident=rate_limit_incident,
        provider_rate_limit_paused=rate_limit_paused,
        blockers=blockers,
        activation=activation_record,
        smoke_readiness=smoke.to_record() if smoke is not None else None,
        timer_readiness=timer.to_record() if timer is not None else None,
        operator_status=operator.to_record() if operator is not None else None,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Report the next guarded Phase-2 isolated deployment/operations "
            "step from local state. This tool makes no RPC calls and performs "
            "no service or database mutation."
        )
    )
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument(
        "--receipt-path",
        default="/opt/pio/data/phase2-isolated-smoke-receipt.json",
    )
    parser.add_argument("--max-receipt-age-seconds", type=int, default=1800)
    args = parser.parse_args()

    report = inspect_lifecycle_handoff(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt_path,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.attention_required:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
