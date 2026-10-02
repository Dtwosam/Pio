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
INSTALL_TOOL = TOOLS_DIR / "install_phase2_isolated_systemd_units.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


INSTALL = _load(
    INSTALL_TOOL,
    "phase2_isolated_activation_install_check",
)

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]

REQUIRED_ENV_KEYS = (
    "SOLANA_RPC_URL",
    "PIO_PHASE2_POSITION_POOL",
)

REQUIRED_DATA_FILES = (
    "pio.db",
    "phase2-prestate-cache.db",
    "phase2-add-detector-state.json",
)

LEGACY_UNITS = (
    "pio-phase2-add-detector.service",
    "pio-phase2-prestate-watch.service",
    # These standalone collectors overlap the isolated bounded evidence cycle
    # and would duplicate Solana RPC work if left active or enabled.
    "pio-phase2-position-observer.service",
    "pio-phase2-position-observer.timer",
    "pio-phase2-evidence-cycle.service",
    "pio-phase2-evidence-cycle.timer",
)

NEW_INACTIVE_UNITS = (
    "pio-phase2-isolated-add-detector.service",
    "pio-phase2-isolated-prestate-stream@54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ.service",
    "pio-phase2-isolated-prestate-stream@DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf.service",
    "pio-phase2-isolated-evidence-cycle.service",
    "pio-phase2-isolated-evidence-cycle.timer",
    "pio-phase2-isolated-rate-limit-pause.service",
)

MUST_BE_DISABLED = (
    *LEGACY_UNITS,
    "pio-phase2-isolated-add-detector.service",
    "pio-phase2-isolated-evidence-cycle.timer",
)


@dataclass(frozen=True)
class ActivationEnvKey:
    name: str
    configured: bool


@dataclass(frozen=True)
class ActivationDataFile:
    name: str
    exists: bool
    regular_file: bool
    symlink: bool


@dataclass(frozen=True)
class ActivationUnitState:
    name: str
    active_state: str
    enabled_state: str
    active: bool
    enabled: bool
    must_be_disabled: bool
    ready: bool


@dataclass(frozen=True)
class Phase2IsolatedActivationReport:
    runtime_current: str | None
    runtime_ready: bool
    installed_units_exact: bool
    installed_unit_statuses: tuple[tuple[str, str], ...]
    env_file: str
    env_file_regular: bool
    env_keys: tuple[ActivationEnvKey, ...]
    position_pool_matches_detector_topology: bool
    detector_pool_count: int
    detector_state_valid: bool
    detector_cursor_pools: int
    detector_cursors_complete: bool
    data_root: str
    data_files: tuple[ActivationDataFile, ...]
    unit_states: tuple[ActivationUnitState, ...]
    activation_ready: bool
    read_only: bool
    rpc_called: bool
    daemon_reload_performed: bool
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
        if not key:
            continue
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value.strip()
    return values


def _detector_pools(unit_path: Path) -> set[str]:
    if unit_path.is_symlink() or not unit_path.is_file():
        return set()
    prefix = "Environment=PIO_PHASE2_DETECTOR_POOLS="
    for raw in unit_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith(prefix):
            continue
        raw_value = line[len(prefix):].strip().strip('"').strip("'")
        pools = set()
        for entry in raw_value.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            address, _seconds = entry.rsplit(":", 1)
            address = address.strip()
            if address:
                pools.add(address)
        return pools
    return set()


def _detector_state_status(
    path: Path,
    *,
    detector_pools: set[str],
) -> tuple[bool, int, bool]:
    if path.is_symlink() or not path.is_file():
        return False, 0, False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False, 0, False
    if not isinstance(payload, dict):
        return False, 0, False
    cursors = payload.get("cursors")
    processed = payload.get("processed")
    pending = payload.get("pending")
    if (
        not isinstance(cursors, dict)
        or not isinstance(processed, list)
        or not isinstance(pending, dict)
    ):
        return False, 0, False

    cursor_count = sum(
        1
        for pool in detector_pools
        if isinstance(cursors.get(pool), str)
        and bool(cursors[pool].strip())
    )
    complete = bool(
        detector_pools
        and cursor_count == len(detector_pools)
    )
    return True, cursor_count, complete


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
    if completed.returncode == 0:
        return "yes"
    return "unknown"


def _unit_state(
    unit: str,
    *,
    runner: SystemctlRunner,
) -> ActivationUnitState:
    active_state = _systemctl_value(
        "is-active",
        unit,
        runner=runner,
    )
    enabled_state = _systemctl_value(
        "is-enabled",
        unit,
        runner=runner,
    )
    active = active_state == "active"
    enabled = enabled_state == "enabled"
    must_disable = unit in MUST_BE_DISABLED
    ready = (not active) and (not must_disable or not enabled)
    return ActivationUnitState(
        name=unit,
        active_state=active_state,
        enabled_state=enabled_state,
        active=active,
        enabled=enabled,
        must_be_disabled=must_disable,
        ready=ready,
    )


def inspect_activation(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    runner: SystemctlRunner = subprocess.run,
) -> Phase2IsolatedActivationReport:
    runtime_current: str | None = None
    runtime_ready = False
    installed_units_exact = False
    installed_statuses: tuple[tuple[str, str], ...] = ()

    try:
        current = INSTALL._validate_runtime(runtime_root)
        runtime_current = str(current)
        runtime_source = current.resolve(strict=True)
        install = INSTALL.inspect_install(
            source_tree=runtime_source,
            runtime_root=runtime_root,
            destination=unit_destination,
        )
        runtime_ready = bool(install.runtime_ready)
        installed_statuses = tuple(
            (row.name, row.status)
            for row in install.units
        )
        installed_units_exact = bool(
            install.units
            and all(
                row.status == "ALREADY_TARGET"
                for row in install.units
            )
        )
    except (OSError, RuntimeError, ValueError):
        pass

    env_path = Path(env_file).expanduser()
    env_regular = env_path.is_file() and not env_path.is_symlink()
    env_values = _read_env(env_path) if env_regular else {}
    env_keys = tuple(
        ActivationEnvKey(
            name=name,
            configured=bool(env_values.get(name, "").strip()),
        )
        for name in REQUIRED_ENV_KEYS
    )

    detector_unit = (
        Path(unit_destination)
        / "pio-phase2-isolated-add-detector.service"
    )
    detector_pools = (
        _detector_pools(detector_unit)
        if installed_units_exact
        else set()
    )
    position_pool = env_values.get(
        "PIO_PHASE2_POSITION_POOL",
        "",
    ).strip()
    pool_matches = bool(
        position_pool
        and detector_pools
        and position_pool in detector_pools
    )

    data_path = Path(data_root).expanduser()
    data_files = tuple(
        ActivationDataFile(
            name=name,
            exists=(data_path / name).exists(),
            regular_file=(data_path / name).is_file(),
            symlink=(data_path / name).is_symlink(),
        )
        for name in REQUIRED_DATA_FILES
    )
    (
        detector_state_valid,
        detector_cursor_pools,
        detector_cursors_complete,
    ) = _detector_state_status(
        data_path / "phase2-add-detector-state.json",
        detector_pools=detector_pools,
    )

    units = tuple(
        _unit_state(unit, runner=runner)
        for unit in (*LEGACY_UNITS, *NEW_INACTIVE_UNITS)
    )

    ready = bool(
        runtime_ready
        and installed_units_exact
        and env_regular
        and all(item.configured for item in env_keys)
        and pool_matches
        and all(
            item.exists
            and item.regular_file
            and not item.symlink
            for item in data_files
        )
        and detector_state_valid
        and detector_cursors_complete
        and all(item.ready for item in units)
    )

    return Phase2IsolatedActivationReport(
        runtime_current=runtime_current,
        runtime_ready=runtime_ready,
        installed_units_exact=installed_units_exact,
        installed_unit_statuses=installed_statuses,
        env_file=str(env_path),
        env_file_regular=env_regular,
        env_keys=env_keys,
        position_pool_matches_detector_topology=pool_matches,
        detector_pool_count=len(detector_pools),
        detector_state_valid=detector_state_valid,
        detector_cursor_pools=detector_cursor_pools,
        detector_cursors_complete=detector_cursors_complete,
        data_root=str(data_path),
        data_files=data_files,
        unit_states=units,
        activation_ready=ready,
        read_only=True,
        rpc_called=False,
        daemon_reload_performed=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only activation preflight for the isolated Phase-2 runtime. "
            "The checker never daemon-reloads, enables, starts, stops, or "
            "restarts services and never makes RPC calls."
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
    args = parser.parse_args()

    report = inspect_activation(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.activation_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
