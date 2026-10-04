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
INSTALL_TOOL = TOOLS_DIR / "install_phase2_isolated_systemd_units.py"
INSTALL_TOOL_RELATIVE = "deploy/tools/install_phase2_isolated_systemd_units.py"
_MAX_TOOL_BYTES = 4 * 1024 * 1024
_MAX_CONFIG_BYTES = 4 * 1024 * 1024
_MAX_STATE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class _CapturedFile:
    path: Path
    encoded: bytes
    opened: os.stat_result


@dataclass(frozen=True)
class _PathSnapshot:
    path: Path
    kind: str
    opened: os.stat_result | None
    symlink_target: str | None


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
    allow_missing: bool = False,
) -> _CapturedFile | None:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        if allow_missing:
            return None
        raise ValueError(f"{label} must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ValueError(f"{label} is missing")
    except OSError as exc:
        raise ValueError(f"{label} cannot be resolved") from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ValueError(f"{label} is missing")
    except OSError as exc:
        raise ValueError(f"{label} cannot be opened safely") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            if allow_missing:
                return None
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


def _path_snapshot(path: Path) -> _PathSnapshot:
    raw = Path(path).expanduser()
    try:
        opened = os.lstat(raw)
    except FileNotFoundError:
        return _PathSnapshot(raw, "ABSENT", None, None)
    except OSError as exc:
        raise ValueError(f"cannot inspect path: {raw}") from exc

    if stat.S_ISLNK(opened.st_mode):
        try:
            target = os.readlink(raw)
        except OSError as exc:
            raise ValueError(f"cannot inspect symlink: {raw}") from exc
        return _PathSnapshot(raw, "SYMLINK", opened, target)
    if stat.S_ISREG(opened.st_mode):
        return _PathSnapshot(raw, "REGULAR", opened, None)
    return _PathSnapshot(raw, "OTHER", opened, None)


def _assert_path_snapshot_stable(
    snapshot: _PathSnapshot,
    *,
    label: str,
) -> None:
    current = _path_snapshot(snapshot.path)
    if current.kind != snapshot.kind:
        raise ValueError(f"{label} path changed during activation inspection")
    if snapshot.kind == "ABSENT":
        return
    before = snapshot.opened
    after = current.opened
    assert before is not None and after is not None
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_mode != after.st_mode
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or snapshot.symlink_target != current.symlink_target
    ):
        raise ValueError(f"{label} path changed during activation inspection")


def _load_captured_tool(path: Path, name: str) -> tuple[Any, _CapturedFile]:
    captured = _capture_regular_file(
        path,
        label="reviewed systemd installer",
        max_bytes=_MAX_TOOL_BYTES,
    )
    assert captured is not None
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
        label="reviewed systemd installer",
    )
    return module, captured


INSTALL, _INSTALL_CAPTURE = _load_captured_tool(
    INSTALL_TOOL,
    "phase2_isolated_activation_install_check",
)
_INSTALL_SHA256 = hashlib.sha256(_INSTALL_CAPTURE.encoded).hexdigest()


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


def _install_source_identity() -> tuple[str, str]:
    _assert_regular_path_stable(
        _INSTALL_CAPTURE.path,
        _INSTALL_CAPTURE.opened,
        label="reviewed systemd installer",
    )
    commit = _repo_head()
    historical = subprocess.run(
        ["git", "show", f"{commit}:{INSTALL_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed systemd installer is not present at reviewed source commit"
        )
    if historical.stdout != _INSTALL_CAPTURE.encoded:
        raise ValueError(
            "reviewed systemd installer bytes do not match reviewed source commit"
        )
    _assert_regular_path_stable(
        _INSTALL_CAPTURE.path,
        _INSTALL_CAPTURE.opened,
        label="reviewed systemd installer",
    )
    return commit, _INSTALL_SHA256

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

EVIDENCE_SERVICE_UNIT = "pio-phase2-isolated-evidence-cycle.service"


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


def _read_env_bytes(encoded: bytes) -> dict[str, str]:
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError:
        return {}
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


def _detector_pools_from_bytes(encoded: bytes) -> set[str]:
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError:
        return set()
    prefix = "Environment=PIO_PHASE2_DETECTOR_POOLS="
    for raw in text.splitlines():
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


def _detector_state_status_from_bytes(
    encoded: bytes,
    *,
    detector_pools: set[str],
) -> tuple[bool, int, bool]:
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
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


def _all_unit_states(
    *,
    runner: SystemctlRunner,
) -> tuple[ActivationUnitState, ...]:
    return tuple(
        _unit_state(unit, runner=runner)
        for unit in (*LEGACY_UNITS, *NEW_INACTIVE_UNITS)
    )


def _capture_from_snapshot(
    snapshot: _PathSnapshot,
    *,
    label: str,
    max_bytes: int,
) -> _CapturedFile | None:
    if snapshot.kind != "REGULAR":
        return None
    captured = _capture_regular_file(
        snapshot.path,
        label=label,
        max_bytes=max_bytes,
    )
    assert captured is not None
    opened = snapshot.opened
    assert opened is not None
    if (
        captured.opened.st_dev != opened.st_dev
        or captured.opened.st_ino != opened.st_ino
        or captured.opened.st_size != opened.st_size
        or captured.opened.st_mtime_ns != opened.st_mtime_ns
        or captured.opened.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} changed before capture")
    return captured


def _read_env(path: Path) -> dict[str, str]:
    """
    Compatibility helper for read-only consumers such as timer-health.

    Read and parse one descriptor-bound env snapshot, then recheck the path
    identity before returning. New activation logic uses the already-captured
    bytes directly.
    """
    snapshot = _path_snapshot(Path(path).expanduser())
    captured = _capture_from_snapshot(
        snapshot,
        label="Phase-2 environment file",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    if captured is None:
        return {}
    values = _read_env_bytes(captured.encoded)
    _assert_path_snapshot_stable(
        snapshot,
        label="Phase-2 environment file",
    )
    _assert_regular_path_stable(
        captured.path,
        captured.opened,
        label="Phase-2 environment file",
    )
    return values


def inspect_activation(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    runner: SystemctlRunner = subprocess.run,
    allow_transient_evidence_service: bool = False,
) -> Phase2IsolatedActivationReport:
    install_source_before = _install_source_identity()

    env_path = Path(env_file).expanduser()
    env_snapshot = _path_snapshot(env_path)
    env_capture = _capture_from_snapshot(
        env_snapshot,
        label="Phase-2 environment file",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    env_regular = env_capture is not None
    env_values = (
        _read_env_bytes(env_capture.encoded)
        if env_capture is not None
        else {}
    )

    destination_path = Path(unit_destination).expanduser()
    detector_unit = (
        destination_path
        / "pio-phase2-isolated-add-detector.service"
    )
    detector_unit_snapshot = _path_snapshot(detector_unit)
    detector_unit_capture = _capture_from_snapshot(
        detector_unit_snapshot,
        label="installed Phase-2 detector unit",
        max_bytes=_MAX_CONFIG_BYTES,
    )

    data_path = Path(data_root).expanduser()
    data_snapshots = {
        name: _path_snapshot(data_path / name)
        for name in REQUIRED_DATA_FILES
    }
    detector_state_snapshot = data_snapshots[
        "phase2-add-detector-state.json"
    ]
    detector_state_capture = _capture_from_snapshot(
        detector_state_snapshot,
        label="Phase-2 detector state",
        max_bytes=_MAX_STATE_BYTES,
    )

    runtime_current: str | None = None
    runtime_ready = False
    installed_units_exact = False
    installed_statuses: tuple[tuple[str, str], ...] = ()
    current_path: Path | None = None
    current_snapshot: _PathSnapshot | None = None
    runtime_source: Path | None = None

    try:
        current = INSTALL._validate_runtime(runtime_root)
        current_path = Path(current)
        current_snapshot = _path_snapshot(current_path)
        runtime_current = str(current_path)
        runtime_source = current_path.resolve(strict=True)
        install = INSTALL.inspect_install(
            source_tree=runtime_source,
            runtime_root=runtime_root,
            destination=unit_destination,
        )
        if current_path.resolve(strict=True) != runtime_source:
            raise ValueError(
                "isolated runtime current target changed during activation inspection"
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
        runtime_ready = False
        installed_units_exact = False

    env_keys = tuple(
        ActivationEnvKey(
            name=name,
            configured=bool(env_values.get(name, "").strip()),
        )
        for name in REQUIRED_ENV_KEYS
    )

    detector_pools = (
        _detector_pools_from_bytes(detector_unit_capture.encoded)
        if (
            installed_units_exact
            and detector_unit_capture is not None
        )
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

    data_files = tuple(
        ActivationDataFile(
            name=name,
            exists=snapshot.kind != "ABSENT",
            regular_file=snapshot.kind == "REGULAR",
            symlink=snapshot.kind == "SYMLINK",
        )
        for name, snapshot in (
            (name, data_snapshots[name])
            for name in REQUIRED_DATA_FILES
        )
    )
    if detector_state_capture is None:
        detector_state_valid = False
        detector_cursor_pools = 0
        detector_cursors_complete = False
    else:
        (
            detector_state_valid,
            detector_cursor_pools,
            detector_cursors_complete,
        ) = _detector_state_status_from_bytes(
            detector_state_capture.encoded,
            detector_pools=detector_pools,
        )

    def assert_inputs_stable() -> None:
        if _install_source_identity() != install_source_before:
            raise ValueError(
                "reviewed systemd installer changed during activation inspection"
            )
        _assert_path_snapshot_stable(
            env_snapshot,
            label="Phase-2 environment file",
        )
        if env_capture is not None:
            _assert_regular_path_stable(
                env_capture.path,
                env_capture.opened,
                label="Phase-2 environment file",
            )
        _assert_path_snapshot_stable(
            detector_unit_snapshot,
            label="installed Phase-2 detector unit",
        )
        if detector_unit_capture is not None:
            _assert_regular_path_stable(
                detector_unit_capture.path,
                detector_unit_capture.opened,
                label="installed Phase-2 detector unit",
            )
        for name, snapshot in data_snapshots.items():
            _assert_path_snapshot_stable(
                snapshot,
                label=f"Phase-2 data file {name}",
            )
        if detector_state_capture is not None:
            _assert_regular_path_stable(
                detector_state_capture.path,
                detector_state_capture.opened,
                label="Phase-2 detector state",
            )
        if current_path is not None and current_snapshot is not None:
            _assert_path_snapshot_stable(
                current_snapshot,
                label="isolated runtime current",
            )
            if (
                runtime_source is not None
                and current_path.resolve(strict=True) != runtime_source
            ):
                raise ValueError(
                    "isolated runtime current target changed during activation inspection"
                )

    units_before = _all_unit_states(runner=runner)
    assert_inputs_stable()
    units_after = _all_unit_states(runner=runner)
    assert_inputs_stable()

    def stable_unit_projection(
        rows: tuple[ActivationUnitState, ...],
    ) -> tuple[ActivationUnitState, ...]:
        if not allow_transient_evidence_service:
            return rows
        return tuple(
            item
            for item in rows
            if item.name != EVIDENCE_SERVICE_UNIT
        )

    if stable_unit_projection(units_after) != stable_unit_projection(
        units_before
    ):
        raise ValueError(
            "systemd unit state changed during activation inspection"
        )
    units = units_after

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
