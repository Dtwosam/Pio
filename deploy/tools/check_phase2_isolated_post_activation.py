#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
INSTALL_TOOL = TOOLS_DIR / "install_phase2_isolated_systemd_units.py"
ACTIVATION_TOOL = TOOLS_DIR / "check_phase2_isolated_activation.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


INSTALL = _load(INSTALL_TOOL, "phase2_post_activation_install")
ACTIVATION = _load(ACTIVATION_TOOL, "phase2_post_activation_helpers")

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]

DETECTOR_UNIT = "pio-phase2-isolated-add-detector.service"
EVIDENCE_SERVICE = "pio-phase2-isolated-evidence-cycle.service"
EVIDENCE_TIMER = "pio-phase2-isolated-evidence-cycle.timer"
LEGACY_EXCLUSIVE_UNITS = (
    "pio-phase2-add-detector.service",
    "pio-phase2-prestate-watch.service",
    "pio-phase2-position-observer.service",
    "pio-phase2-position-observer.timer",
    "pio-phase2-evidence-cycle.service",
    "pio-phase2-evidence-cycle.timer",
)


@dataclass(frozen=True)
class PostActivationUnitState:
    name: str
    active_state: str
    enabled_state: str
    active: bool
    enabled: bool


@dataclass(frozen=True)
class PostActivationPrestate:
    pool_address: str
    row_present: bool
    observed_at: str | None
    capture_slot: int | None
    single_context: bool


@dataclass(frozen=True)
class Phase2PostActivationReport:
    runtime_current: str | None
    runtime_ready: bool
    installed_units_exact: bool
    detector_pools: tuple[str, ...]
    detector_state_valid: bool
    detector_cursors_complete: bool
    detector_pending_count: int
    prestate_cache_regular: bool
    prestates: tuple[PostActivationPrestate, ...]
    units: tuple[PostActivationUnitState, ...]
    detector_active_enabled: bool
    streams_active: bool
    evidence_timer_inactive_disabled: bool
    legacy_exclusive_units_inactive_disabled: bool
    health_ready_for_evidence_timer: bool
    read_only: bool
    rpc_called: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _systemctl_value(
    action: str,
    unit: str,
    *,
    runner: SystemctlRunner,
) -> str:
    completed = runner(
        ["systemctl", action, unit],
        capture_output=True,
        text=True,
        check=False,
    )
    value = (completed.stdout or "").strip()
    if value:
        return value
    return "unknown"


def _unit_state(
    unit: str,
    *,
    runner: SystemctlRunner,
) -> PostActivationUnitState:
    active_state = _systemctl_value("is-active", unit, runner=runner)
    enabled_state = _systemctl_value("is-enabled", unit, runner=runner)
    return PostActivationUnitState(
        name=unit,
        active_state=active_state,
        enabled_state=enabled_state,
        active=active_state == "active",
        enabled=enabled_state == "enabled",
    )


def _detector_state(
    path: Path,
    *,
    detector_pools: set[str],
) -> tuple[bool, bool, int]:
    valid, _cursor_count, complete = ACTIVATION._detector_state_status(
        path,
        detector_pools=detector_pools,
    )
    pending_count = 0
    if valid:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pending = payload.get("pending")
            if isinstance(pending, dict):
                pending_count = len(pending)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            valid = False
            complete = False
    return valid, complete, pending_count


def _latest_prestates(
    cache_path: Path,
    *,
    pools: tuple[str, ...],
) -> tuple[PostActivationPrestate, ...]:
    if cache_path.is_symlink() or not cache_path.is_file():
        return tuple(
            PostActivationPrestate(
                pool_address=pool,
                row_present=False,
                observed_at=None,
                capture_slot=None,
                single_context=False,
            )
            for pool in pools
        )

    uri = f"file:{cache_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        return tuple(
            PostActivationPrestate(
                pool_address=pool,
                row_present=False,
                observed_at=None,
                capture_slot=None,
                single_context=False,
            )
            for pool in pools
        )

    try:
        rows = []
        for pool in pools:
            try:
                row = conn.execute(
                    """
                    SELECT observed_at, capture_slot_start, capture_slot_end
                    FROM prestate_snapshots
                    WHERE pool_address = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (pool,),
                ).fetchone()
            except sqlite3.Error:
                row = None
            if row is None:
                rows.append(
                    PostActivationPrestate(
                        pool_address=pool,
                        row_present=False,
                        observed_at=None,
                        capture_slot=None,
                        single_context=False,
                    )
                )
                continue
            start = int(row[1])
            end = int(row[2])
            rows.append(
                PostActivationPrestate(
                    pool_address=pool,
                    row_present=True,
                    observed_at=str(row[0]),
                    capture_slot=end,
                    single_context=(
                        start >= 0
                        and end >= 0
                        and start == end
                    ),
                )
            )
        return tuple(rows)
    finally:
        conn.close()


def inspect_post_activation(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    data_root: str | Path = "/opt/pio/data",
    runner: SystemctlRunner = subprocess.run,
) -> Phase2PostActivationReport:
    runtime_current: str | None = None
    runtime_ready = False
    installed_units_exact = False

    try:
        current = INSTALL._validate_runtime(runtime_root)
        runtime_current = str(current)
        runtime_ready = True
    except (OSError, RuntimeError, ValueError):
        current = None

    destination = Path(unit_destination).expanduser()
    installed_statuses = []
    if (
        runtime_ready
        and destination.is_dir()
        and not destination.is_symlink()
    ):
        for name, expected in INSTALL.UNIT_CONTRACT.items():
            installed_statuses.append(
                INSTALL.git_blob_sha(destination / name) == expected
            )
    installed_units_exact = bool(
        installed_statuses
        and all(installed_statuses)
    )

    detector_unit = destination / DETECTOR_UNIT
    detector_pool_set = (
        ACTIVATION._detector_pools(detector_unit)
        if installed_units_exact
        else set()
    )
    detector_pools = tuple(sorted(detector_pool_set))

    data = Path(data_root).expanduser()
    state_path = data / "phase2-add-detector-state.json"
    (
        detector_state_valid,
        detector_cursors_complete,
        pending_count,
    ) = _detector_state(
        state_path,
        detector_pools=detector_pool_set,
    )

    cache_path = data / "phase2-prestate-cache.db"
    cache_regular = cache_path.is_file() and not cache_path.is_symlink()
    prestates = _latest_prestates(
        cache_path,
        pools=detector_pools,
    )

    stream_units = tuple(
        f"pio-phase2-isolated-prestate-stream@{pool}.service"
        for pool in detector_pools
    )
    units_to_read = (
        DETECTOR_UNIT,
        *stream_units,
        EVIDENCE_SERVICE,
        EVIDENCE_TIMER,
        *LEGACY_EXCLUSIVE_UNITS,
    )
    unit_states = tuple(
        _unit_state(name, runner=runner)
        for name in units_to_read
    )
    by_name = {item.name: item for item in unit_states}

    detector = by_name.get(DETECTOR_UNIT)
    detector_active_enabled = bool(
        detector
        and detector.active
        and detector.enabled
    )
    streams_active = bool(
        stream_units
        and all(
            by_name[name].active
            for name in stream_units
        )
    )
    evidence_timer = by_name.get(EVIDENCE_TIMER)
    evidence_service = by_name.get(EVIDENCE_SERVICE)
    evidence_timer_inactive_disabled = bool(
        evidence_timer
        and not evidence_timer.active
        and not evidence_timer.enabled
        and evidence_service
        and not evidence_service.active
    )
    legacy_clear = all(
        not by_name[name].active
        and not by_name[name].enabled
        for name in LEGACY_EXCLUSIVE_UNITS
    )
    prestates_ready = bool(
        prestates
        and all(
            item.row_present and item.single_context
            for item in prestates
        )
    )

    ready = bool(
        runtime_ready
        and installed_units_exact
        and detector_pools
        and detector_state_valid
        and detector_cursors_complete
        and cache_regular
        and prestates_ready
        and detector_active_enabled
        and streams_active
        and evidence_timer_inactive_disabled
        and legacy_clear
    )

    return Phase2PostActivationReport(
        runtime_current=runtime_current,
        runtime_ready=runtime_ready,
        installed_units_exact=installed_units_exact,
        detector_pools=detector_pools,
        detector_state_valid=detector_state_valid,
        detector_cursors_complete=detector_cursors_complete,
        detector_pending_count=pending_count,
        prestate_cache_regular=cache_regular,
        prestates=prestates,
        units=unit_states,
        detector_active_enabled=detector_active_enabled,
        streams_active=streams_active,
        evidence_timer_inactive_disabled=evidence_timer_inactive_disabled,
        legacy_exclusive_units_inactive_disabled=legacy_clear,
        health_ready_for_evidence_timer=ready,
        read_only=True,
        rpc_called=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only post-activation health gate for isolated Phase-2. "
            "Requires live detector/streams and cached prestates for every "
            "detector pool before recurring evidence collection is allowed."
        )
    )
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--data-root", default="/opt/pio/data")
    args = parser.parse_args()

    report = inspect_post_activation(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        data_root=args.data_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.health_ready_for_evidence_timer:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
