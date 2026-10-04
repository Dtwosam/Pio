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
_MAX_TOOL_BYTES = 4 * 1024 * 1024
_DEPENDENCY_DOMAIN = b"PIO_PHASE2_LIFECYCLE_DEPENDENCIES_V1\0"
ACTIVATION_TOOL = TOOLS_DIR / "check_phase2_isolated_activation.py"
SMOKE_TOOL = TOOLS_DIR / "check_phase2_isolated_smoke_readiness.py"
TIMER_TOOL = TOOLS_DIR / "check_phase2_isolated_timer_readiness.py"
OPERATOR_TOOL = TOOLS_DIR / "check_phase2_isolated_operator_status.py"
BOOTSTRAP_TOOL = TOOLS_DIR / "bootstrap_phase2_isolated_source.py"
RUNTIME_CHECK_TOOL = TOOLS_DIR / "check_phase2_isolated_runtime.py"
UNIT_UPGRADE_TOOL = TOOLS_DIR / "upgrade_phase2_isolated_systemd_units.py"


def _assert_dependency_path_stable(
    path: Path,
    opened: os.stat_result,
    *,
    label: str,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} path changed after load") from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} path changed after load")


def _capture_dependency(
    path: Path,
    *,
    label: str,
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
        if before.st_size <= 0 or before.st_size > _MAX_TOOL_BYTES:
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
    _assert_dependency_path_stable(resolved, before, label=label)
    return resolved, encoded, before


def _load_captured_dependency(
    path: Path,
    name: str,
    *,
    label: str,
) -> tuple[Any, Path, bytes, os.stat_result]:
    resolved, encoded, opened = _capture_dependency(path, label=label)
    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {resolved}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        code = compile(encoded, str(resolved), "exec")
        exec(code, module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    _assert_dependency_path_stable(resolved, opened, label=label)
    return module, resolved, encoded, opened


(
    ACTIVATION,
    _ACTIVATION_PATH,
    _ACTIVATION_BYTES,
    _ACTIVATION_STAT,
) = _load_captured_dependency(
    ACTIVATION_TOOL,
    "phase2_lifecycle_activation",
    label="reviewed activation checker",
)
(
    SMOKE,
    _SMOKE_PATH,
    _SMOKE_BYTES,
    _SMOKE_STAT,
) = _load_captured_dependency(
    SMOKE_TOOL,
    "phase2_lifecycle_smoke",
    label="reviewed smoke checker",
)
(
    TIMER,
    _TIMER_PATH,
    _TIMER_BYTES,
    _TIMER_STAT,
) = _load_captured_dependency(
    TIMER_TOOL,
    "phase2_lifecycle_timer",
    label="reviewed timer checker",
)
(
    OPERATOR,
    _OPERATOR_PATH,
    _OPERATOR_BYTES,
    _OPERATOR_STAT,
) = _load_captured_dependency(
    OPERATOR_TOOL,
    "phase2_lifecycle_operator",
    label="reviewed operator checker",
)
(
    BOOTSTRAP,
    _BOOTSTRAP_PATH,
    _BOOTSTRAP_BYTES,
    _BOOTSTRAP_STAT,
) = _load_captured_dependency(
    BOOTSTRAP_TOOL,
    "phase2_lifecycle_source_bootstrap",
    label="reviewed source bootstrap checker",
)
(
    RUNTIME_CHECK,
    _RUNTIME_CHECK_PATH,
    _RUNTIME_CHECK_BYTES,
    _RUNTIME_CHECK_STAT,
) = _load_captured_dependency(
    RUNTIME_CHECK_TOOL,
    "phase2_lifecycle_runtime_check",
    label="reviewed runtime checker",
)
(
    UNIT_UPGRADE,
    _UNIT_UPGRADE_PATH,
    _UNIT_UPGRADE_BYTES,
    _UNIT_UPGRADE_STAT,
) = _load_captured_dependency(
    UNIT_UPGRADE_TOOL,
    "phase2_lifecycle_unit_upgrade",
    label="reviewed systemd unit upgrader",
)

_DEPENDENCY_SNAPSHOTS = (
    (
        ACTIVATION_TOOL,
        "deploy/tools/check_phase2_isolated_activation.py",
        _ACTIVATION_PATH,
        _ACTIVATION_BYTES,
        _ACTIVATION_STAT,
        "reviewed activation checker",
    ),
    (
        SMOKE_TOOL,
        "deploy/tools/check_phase2_isolated_smoke_readiness.py",
        _SMOKE_PATH,
        _SMOKE_BYTES,
        _SMOKE_STAT,
        "reviewed smoke checker",
    ),
    (
        TIMER_TOOL,
        "deploy/tools/check_phase2_isolated_timer_readiness.py",
        _TIMER_PATH,
        _TIMER_BYTES,
        _TIMER_STAT,
        "reviewed timer checker",
    ),
    (
        OPERATOR_TOOL,
        "deploy/tools/check_phase2_isolated_operator_status.py",
        _OPERATOR_PATH,
        _OPERATOR_BYTES,
        _OPERATOR_STAT,
        "reviewed operator checker",
    ),
    (
        BOOTSTRAP_TOOL,
        "deploy/tools/bootstrap_phase2_isolated_source.py",
        _BOOTSTRAP_PATH,
        _BOOTSTRAP_BYTES,
        _BOOTSTRAP_STAT,
        "reviewed source bootstrap checker",
    ),
    (
        RUNTIME_CHECK_TOOL,
        "deploy/tools/check_phase2_isolated_runtime.py",
        _RUNTIME_CHECK_PATH,
        _RUNTIME_CHECK_BYTES,
        _RUNTIME_CHECK_STAT,
        "reviewed runtime checker",
    ),
    (
        UNIT_UPGRADE_TOOL,
        "deploy/tools/upgrade_phase2_isolated_systemd_units.py",
        _UNIT_UPGRADE_PATH,
        _UNIT_UPGRADE_BYTES,
        _UNIT_UPGRADE_STAT,
        "reviewed systemd unit upgrader",
    ),
)

def _dependency_source_identity() -> tuple[str, str]:
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

    digest = hashlib.sha256()
    digest.update(_DEPENDENCY_DOMAIN)

    for current_path, relative, loaded_path, encoded, opened, label in (
        _DEPENDENCY_SNAPSHOTS
    ):
        raw = Path(current_path).expanduser()
        if raw.is_symlink():
            raise ValueError(f"{label} is missing or symlinked")
        try:
            resolved = raw.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"{label} is missing or symlinked") from exc
        if resolved != loaded_path:
            raise ValueError(f"{label} path changed after module load")
        _assert_dependency_path_stable(
            loaded_path,
            opened,
            label=label,
        )

        historical = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=False,
        )
        if historical.returncode != 0:
            raise ValueError(f"{label} is not present at reviewed source commit")
        if historical.stdout != encoded:
            raise ValueError(f"{label} bytes do not match reviewed source commit")

        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    return commit, digest.hexdigest()


SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Phase2LifecycleHandoffReport:
    state: str
    next_action: str
    next_tool: str | None
    next_parameters: dict[str, Any]
    next_mutation_flag: str | None
    attention_required: bool
    runtime_ready: bool
    installed_units_exact: bool
    activation_ready: bool
    smoke_ready: bool
    timer_ready: bool
    collection_running: bool
    provider_rate_limit_incident: bool
    provider_rate_limit_paused: bool
    source_tree: str
    source_status: str
    source_runtime_ready: bool
    blockers: tuple[str, ...]
    source_bootstrap: dict[str, Any] | None
    source_runtime: dict[str, Any] | None
    systemd_unit_upgrade: dict[str, Any] | None
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


def _runtime_boundary_ok(report: Any) -> bool:
    return bool(
        not getattr(report, "production_tree_modified", True)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "service_control_performed", True)
    )


def _unit_upgrade_boundary_ok(report: Any) -> bool:
    return bool(
        not getattr(report, "applied", True)
        and int(getattr(report, "files_updated", -1)) == 0
        and getattr(report, "backup_root", None) is None
        and not getattr(report, "daemon_reload_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "rpc_called", True)
    )


def _unit_upgrade_blockers(report: Any) -> tuple[str, ...]:
    allowed = {"READY_UPDATE", "ALREADY_TARGET", "NOT_INSTALLED"}
    blockers = tuple(
        f"{item.name}:{item.status}"
        for item in report.units
        if str(item.status) not in allowed
    )
    return blockers or ("SYSTEMD_UNIT_STATE_INCONSISTENT",)


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


def _runtime_release_path(
    activation: Any,
    *,
    runtime_root: str | Path,
) -> str:
    raw = getattr(activation, "runtime_current", None)
    current = (
        Path(str(raw)).expanduser()
        if raw
        else Path(runtime_root).expanduser() / "current"
    )
    return str(current.resolve(strict=False))


def _deployment_source_path() -> str:
    return str(REPO_ROOT.resolve(strict=False))


def _operator_parameters(
    *,
    runtime_root: str | Path,
    unit_destination: str | Path,
    env_file: str | Path,
    data_root: str | Path,
) -> dict[str, Any]:
    return {
        "runtime_root": str(Path(runtime_root).expanduser()),
        "unit_destination": str(Path(unit_destination).expanduser()),
        "env_file": str(Path(env_file).expanduser()),
        "data_root": str(Path(data_root).expanduser()),
    }


def _next_step_contract(
    *,
    state: str,
    activation: Any,
    source_path: Path,
    source_bootstrap: Any | None,
    runtime_root: str | Path,
    unit_destination: str | Path,
    env_file: str | Path,
    data_root: str | Path,
    receipt_path: str | Path,
    max_receipt_age_seconds: int,
) -> tuple[dict[str, Any], str | None]:
    operator = _operator_parameters(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
    )

    if state == "SOURCE_BOOTSTRAP_REQUIRED":
        repository_url = (
            str(source_bootstrap.repository_url)
            if source_bootstrap is not None
            else BOOTSTRAP.DEFAULT_REPOSITORY_URL
        )
        return {
            "destination": str(source_path),
            "repository_url": repository_url,
        }, "--apply"

    if state == "SOURCE_PREPARATION_REQUIRED":
        return {"source_tree": str(source_path)}, "--prepare"

    if state == "RUNTIME_STAGING_READY":
        return {
            "source_tree": str(source_path),
            "destination_root": str(Path(runtime_root).expanduser()),
        }, "--apply"

    if state == "SOURCE_CONFLICT_REVIEW_REQUIRED":
        repository_url = (
            str(source_bootstrap.repository_url)
            if source_bootstrap is not None
            else BOOTSTRAP.DEFAULT_REPOSITORY_URL
        )
        return {
            "destination": str(source_path),
            "repository_url": repository_url,
        }, None

    if state == "SYSTEMD_UNIT_UPGRADE_READY":
        return {
            "source_tree": _deployment_source_path(),
            "destination": str(Path(unit_destination).expanduser()),
        }, "--apply"

    if state == "SYSTEMD_UNIT_CONFLICT_REVIEW_REQUIRED":
        return {
            "source_tree": _deployment_source_path(),
            "destination": str(Path(unit_destination).expanduser()),
        }, None

    if state == "SYSTEMD_UNITS_NOT_READY":
        return {
            "source_tree": _deployment_source_path(),
            "runtime_root": str(Path(runtime_root).expanduser()),
            "destination": str(Path(unit_destination).expanduser()),
        }, "--apply"

    if state == "DETECTOR_ACTIVATION_READY":
        return operator, "--apply"

    if state == "SMOKE_REQUIRED":
        return {
            **operator,
            "receipt": str(Path(receipt_path).expanduser()),
        }, "--apply"

    if state == "TIMER_ACTIVATION_READY":
        return {
            **operator,
            "receipt": str(Path(receipt_path).expanduser()),
            "max_receipt_age_seconds": max_receipt_age_seconds,
        }, "--apply"

    if state == "RATE_LIMIT_PAUSE_REQUIRED":
        return {
            "database": str(
                Path(data_root).expanduser() / "pio.db"
            ),
        }, "--apply"

    if state in {
        "RATE_LIMIT_PAUSED",
        "RUNNING_HEALTHY",
        "RUNNING_ATTENTION_REQUIRED",
    }:
        return operator, None

    if state == "ACTIVE_TOPOLOGY_NOT_READY":
        return operator, None

    return operator, None


def inspect_lifecycle_handoff(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    max_receipt_age_seconds: int = 1800,
    source_tree: str | Path | None = None,
    repository_url: str = BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    runner: SystemctlRunner = subprocess.run,
) -> Phase2LifecycleHandoffReport:
    dependency_source_before = _dependency_source_identity()

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
    source_bootstrap = None
    source_runtime = None
    unit_upgrade = None
    source_runtime_ready = False
    source_status = "NOT_REQUIRED"
    source_path = Path(
        source_tree
        if source_tree is not None
        else (
            "/opt/pio-phase2-build/"
            + RUNTIME_CHECK.PINNED_SOURCE_HEAD
        )
    ).expanduser()

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
        try:
            source_runtime = RUNTIME_CHECK.inspect_runtime(source_path)
        except (OSError, RuntimeError, ValueError):
            source_runtime = None
        else:
            if not _runtime_boundary_ok(source_runtime):
                raise ValueError(
                    "Phase-2 source runtime check crossed the read-only boundary"
                )
            source_runtime_ready = bool(source_runtime.runtime_ready)

        if source_runtime_ready:
            source_status = "PREPARED_RUNTIME_READY"
            state = "RUNTIME_STAGING_READY"
            next_action = "STAGE_REVIEWED_RUNTIME"
            next_tool = "stage_phase2_isolated_runtime.py"
            blockers = ()
        else:
            source_bootstrap = BOOTSTRAP.inspect_pinned_source(
                destination=source_path,
                repository_url=repository_url,
            )
            if not _boundary_ok(source_bootstrap):
                raise ValueError(
                    "Phase-2 source bootstrap check crossed the read-only boundary"
                )
            source_status = str(source_bootstrap.status)

            if source_bootstrap.status == "READY_CREATE":
                state = "SOURCE_BOOTSTRAP_REQUIRED"
                next_action = "BOOTSTRAP_PINNED_SOURCE"
                next_tool = "bootstrap_phase2_isolated_source.py"
                blockers = ("PINNED_SOURCE_MISSING",)
            elif source_bootstrap.status == "ALREADY_PINNED":
                state = "SOURCE_PREPARATION_REQUIRED"
                next_action = "PREPARE_PINNED_RUNTIME"
                next_tool = "prepare_phase2_isolated_runtime.py"
                blockers = ("PINNED_SOURCE_NOT_PREPARED",)
            else:
                state = "SOURCE_CONFLICT_REVIEW_REQUIRED"
                next_action = "REVIEW_PINNED_SOURCE_CONFLICT"
                next_tool = "bootstrap_phase2_isolated_source.py"
                blockers = (str(source_bootstrap.status),)
    elif not bool(activation.installed_units_exact):
        unit_upgrade = UNIT_UPGRADE.inspect_upgrade(
            source_tree=_deployment_source_path(),
            destination=unit_destination,
        )
        if not _unit_upgrade_boundary_ok(unit_upgrade):
            raise ValueError(
                "Phase-2 unit upgrade inspection crossed the read-only boundary"
            )

        if not bool(unit_upgrade.ready):
            state = "SYSTEMD_UNIT_CONFLICT_REVIEW_REQUIRED"
            next_action = "REVIEW_SYSTEMD_UNIT_CONFLICT"
            next_tool = "upgrade_phase2_isolated_systemd_units.py"
            blockers = _unit_upgrade_blockers(unit_upgrade)
        elif bool(unit_upgrade.upgrade_needed):
            state = "SYSTEMD_UNIT_UPGRADE_READY"
            next_action = "UPGRADE_REVIEWED_UNITS"
            next_tool = "upgrade_phase2_isolated_systemd_units.py"
            blockers = ()
        elif bool(unit_upgrade.installer_needed):
            state = "SYSTEMD_UNITS_NOT_READY"
            next_action = "INSTALL_REVIEWED_UNITS"
            next_tool = "install_phase2_isolated_systemd_units.py"
            blockers = ("SYSTEMD_UNITS_MISSING",)
        else:
            raise ValueError(
                "activation/unit-upgrade reports disagree on installed unit bytes"
            )
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

    next_parameters, next_mutation_flag = _next_step_contract(
        state=state,
        activation=activation,
        source_path=source_path,
        source_bootstrap=source_bootstrap,
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
    )

    dependency_source_after = _dependency_source_identity()
    if dependency_source_after != dependency_source_before:
        raise ValueError(
            "reviewed lifecycle dependencies changed during inspection"
        )

    return Phase2LifecycleHandoffReport(
        state=state,
        next_action=next_action,
        next_tool=next_tool,
        next_parameters=next_parameters,
        next_mutation_flag=next_mutation_flag,
        attention_required=state != "RUNNING_HEALTHY",
        runtime_ready=bool(activation.runtime_ready),
        installed_units_exact=bool(activation.installed_units_exact),
        activation_ready=activation_ready,
        smoke_ready=smoke_ready,
        timer_ready=timer_ready,
        collection_running=collection_running,
        provider_rate_limit_incident=rate_limit_incident,
        provider_rate_limit_paused=rate_limit_paused,
        source_tree=str(source_path),
        source_status=source_status,
        source_runtime_ready=source_runtime_ready,
        blockers=blockers,
        source_bootstrap=(
            source_bootstrap.to_record()
            if source_bootstrap is not None
            else None
        ),
        source_runtime=(
            source_runtime.to_record()
            if source_runtime is not None
            else None
        ),
        systemd_unit_upgrade=(
            unit_upgrade.to_record()
            if unit_upgrade is not None
            else None
        ),
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
    parser.add_argument(
        "--source-tree",
        help=(
            "Pinned Phase-2 build source tree; defaults to "
            "/opt/pio-phase2-build/<PINNED_SOURCE_HEAD>"
        ),
    )
    parser.add_argument(
        "--repository-url",
        default=BOOTSTRAP.DEFAULT_REPOSITORY_URL,
        help="Public Git source used only for bootstrap-state validation",
    )
    args = parser.parse_args()

    report = inspect_lifecycle_handoff(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt_path,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
        source_tree=args.source_tree,
        repository_url=args.repository_url,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.attention_required:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
