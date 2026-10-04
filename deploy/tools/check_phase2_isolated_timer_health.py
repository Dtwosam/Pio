#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
ACTIVATION_TOOL = TOOLS_DIR / "check_phase2_isolated_activation.py"
ACTIVATION_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_activation.py"

_MAX_TOOL_BYTES = 4 * 1024 * 1024
_MAX_ENV_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _CapturedFile:
    path: Path
    encoded: bytes
    opened: os.stat_result


@dataclass(frozen=True)
class _DatabaseCompanionSnapshot:
    path: Path
    exists: bool
    opened: os.stat_result | None


@dataclass(frozen=True)
class _DatabasePathSnapshot:
    path: Path
    opened: os.stat_result
    companions: tuple[_DatabaseCompanionSnapshot, ...]


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
    optional: bool = False,
) -> _CapturedFile | None:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        if optional:
            return None
        raise ValueError(f"{label} must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError:
        if optional:
            return None
        raise ValueError(f"{label} is missing")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except OSError:
        if optional:
            return None
        raise ValueError(f"{label} cannot be opened safely")

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            if optional:
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


def _load_captured_activation(
    path: Path,
    name: str,
) -> tuple[Any, _CapturedFile]:
    captured = _capture_regular_file(
        path,
        label="reviewed activation checker",
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
        label="reviewed activation checker",
    )
    return module, captured


ACTIVATION, _ACTIVATION_CAPTURE = _load_captured_activation(
    ACTIVATION_TOOL,
    "phase2_timer_health_activation_helpers",
)
_ACTIVATION_SHA256 = hashlib.sha256(
    _ACTIVATION_CAPTURE.encoded
).hexdigest()


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


def _activation_source_identity() -> tuple[str, str]:
    _assert_regular_path_stable(
        _ACTIVATION_CAPTURE.path,
        _ACTIVATION_CAPTURE.opened,
        label="reviewed activation checker",
    )
    commit = _repo_head()
    historical = subprocess.run(
        ["git", "show", f"{commit}:{ACTIVATION_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed activation checker is not present at reviewed source commit"
        )
    if historical.stdout != _ACTIVATION_CAPTURE.encoded:
        raise ValueError(
            "reviewed activation checker bytes do not match reviewed source commit"
        )
    _assert_regular_path_stable(
        _ACTIVATION_CAPTURE.path,
        _ACTIVATION_CAPTURE.opened,
        label="reviewed activation checker",
    )
    return commit, _ACTIVATION_SHA256

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]
Now = Callable[[], datetime]

PROGRESS_EDGE_TYPE = "PHASE2_EVIDENCE_CYCLE_PROGRESS_V1"
TIMER_UNIT = "pio-phase2-isolated-evidence-cycle.timer"
EVIDENCE_SERVICE = "pio-phase2-isolated-evidence-cycle.service"
DETECTOR_UNIT = "pio-phase2-isolated-add-detector.service"


@dataclass(frozen=True)
class TimerHealthCycle:
    evidence_id: int
    as_of: str
    status: str
    rpc_rate_limited: bool
    rpc_circuit_open: bool
    stages_failed: int
    stages_skipped: int


@dataclass(frozen=True)
class TimerHealthUnit:
    name: str
    active: bool
    enabled: bool


@dataclass(frozen=True)
class Phase2TimerHealthReport:
    pool_address: str | None
    runtime_ready: bool
    installed_units_exact: bool
    env_ready: bool
    data_ready: bool
    detector_state_ready: bool
    detector_active_enabled: bool
    streams_active: bool
    timer_active: bool
    timer_enabled: bool
    timer_active_enabled: bool
    legacy_collectors_quiescent: bool
    evidence_service_active: bool
    cycles: tuple[TimerHealthCycle, ...]
    latest_cycle_age_seconds: float | None
    latest_cycle_recent: bool
    latest_cycle_failed: bool
    consecutive_rpc_rate_limited: int
    rate_limit_streak_threshold: int
    pause_recommended: bool
    collection_healthy: bool
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _systemctl_state(
    unit: str,
    *,
    runner: SystemctlRunner,
) -> TimerHealthUnit:
    def query(action: str, expected: str) -> bool:
        completed = runner(
            ["systemctl", action, unit],
            capture_output=True,
            text=True,
            check=False,
        )
        return (
            int(completed.returncode) == 0
            and (completed.stdout or "").strip() == expected
        )

    return TimerHealthUnit(
        name=unit,
        active=query("is-active", "active"),
        enabled=query("is-enabled", "enabled"),
    )


def _database_companion_snapshot(
    path: Path,
) -> _DatabaseCompanionSnapshot:
    try:
        opened = os.lstat(path)
    except FileNotFoundError:
        return _DatabaseCompanionSnapshot(
            path=path,
            exists=False,
            opened=None,
        )
    except OSError as exc:
        raise ValueError(
            "Phase-2 database companion cannot be inspected safely"
        ) from exc
    if stat.S_ISLNK(opened.st_mode) or not stat.S_ISREG(opened.st_mode):
        raise ValueError(
            "Phase-2 database companion must be a regular non-symlink file"
        )
    return _DatabaseCompanionSnapshot(
        path=path,
        exists=True,
        opened=opened,
    )


def _database_path_snapshot(
    database_path: Path,
) -> _DatabasePathSnapshot | None:
    raw = Path(database_path).expanduser()
    try:
        opened = os.lstat(raw)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("Phase-2 database cannot be inspected safely") from exc
    if stat.S_ISLNK(opened.st_mode) or not stat.S_ISREG(opened.st_mode):
        return None
    try:
        resolved = raw.resolve(strict=True)
    except OSError:
        return None

    companions = tuple(
        _database_companion_snapshot(
            resolved.with_name(resolved.name + suffix)
        )
        for suffix in ("-wal", "-shm")
    )
    return _DatabasePathSnapshot(
        path=resolved,
        opened=opened,
        companions=companions,
    )


def _assert_database_companion_stable(
    snapshot: _DatabaseCompanionSnapshot,
) -> None:
    try:
        current = os.lstat(snapshot.path)
    except FileNotFoundError:
        if snapshot.exists:
            raise ValueError(
                "Phase-2 database companion path changed during health check"
            )
        return
    except OSError as exc:
        raise ValueError(
            "Phase-2 database companion path changed during health check"
        ) from exc

    if not snapshot.exists:
        raise ValueError(
            "Phase-2 database companion path changed during health check"
        )
    before = snapshot.opened
    assert before is not None
    if (
        not stat.S_ISREG(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
        or current.st_dev != before.st_dev
        or current.st_ino != before.st_ino
        or current.st_mode != before.st_mode
    ):
        raise ValueError(
            "Phase-2 database companion path changed during health check"
        )


def _assert_database_path_stable(
    snapshot: _DatabasePathSnapshot,
) -> None:
    try:
        current = os.stat(snapshot.path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("Phase-2 database path changed during health check") from exc
    before = snapshot.opened
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != before.st_dev
        or current.st_ino != before.st_ino
        or current.st_mode != before.st_mode
    ):
        raise ValueError("Phase-2 database path changed during health check")
    for companion in snapshot.companions:
        _assert_database_companion_stable(companion)


def _open_database_descriptor(
    snapshot: _DatabasePathSnapshot,
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(snapshot.path, flags)
    except OSError as exc:
        raise ValueError(
            "Phase-2 database cannot be opened through a stable descriptor"
        ) from exc

    opened = os.fstat(fd)
    before = snapshot.opened
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_dev != before.st_dev
        or opened.st_ino != before.st_ino
        or opened.st_mode != before.st_mode
    ):
        os.close(fd)
        raise ValueError(
            "Phase-2 database path changed before descriptor capture"
        )
    return fd


def _assert_database_descriptor_stable(
    fd: int,
    snapshot: _DatabasePathSnapshot,
) -> None:
    try:
        current = os.fstat(fd)
    except OSError as exc:
        raise ValueError(
            "Phase-2 database descriptor changed during health check"
        ) from exc
    before = snapshot.opened
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != before.st_dev
        or current.st_ino != before.st_ino
        or current.st_mode != before.st_mode
    ):
        raise ValueError(
            "Phase-2 database descriptor changed during health check"
        )


def _read_recent_cycles_snapshot(
    database_path: Path,
    *,
    pool_address: str,
    limit: int,
) -> tuple[tuple[TimerHealthCycle, ...], _DatabasePathSnapshot | None]:
    if limit <= 0:
        return (), None
    snapshot = _database_path_snapshot(database_path)
    if snapshot is None:
        return (), None

    fd = _open_database_descriptor(snapshot)
    try:
        proc_path = Path("/proc/self/fd") / str(fd)
        if not proc_path.exists():
            raise ValueError(
                "descriptor-bound SQLite path is unavailable on this platform"
            )
        uri = f"file:{proc_path}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                # Establish one SQLite read snapshot before reading health
                # evidence. Companion identities are checked on both sides of
                # the transaction while allowing normal WAL append growth.
                conn.execute(
                    "SELECT 1 FROM sqlite_master LIMIT 1"
                ).fetchone()
                _assert_database_descriptor_stable(fd, snapshot)
                _assert_database_path_stable(snapshot)
                rows = conn.execute(
                    """
                    SELECT id, as_of, status, evidence_json
                    FROM advanced_edge_evidence
                    WHERE edge_type = ?
                      AND pool_address = ?
                    ORDER BY as_of DESC, id DESC
                    LIMIT ?
                    """,
                    (PROGRESS_EDGE_TYPE, pool_address, limit),
                ).fetchall()
                _assert_database_descriptor_stable(fd, snapshot)
                _assert_database_path_stable(snapshot)
                conn.rollback()
        except sqlite3.Error:
            rows = []
        _assert_database_descriptor_stable(fd, snapshot)
        _assert_database_path_stable(snapshot)
    finally:
        os.close(fd)

    cycles = []
    for evidence_id, as_of, status, evidence_json in rows:
        try:
            payload = json.loads(str(evidence_json))
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            cycles.append(
                TimerHealthCycle(
                    evidence_id=int(evidence_id),
                    as_of=str(as_of),
                    status=str(status),
                    rpc_rate_limited=False,
                    rpc_circuit_open=False,
                    stages_failed=0,
                    stages_skipped=0,
                )
            )
            continue

        rpc_rate_limited = payload.get("rpc_rate_limited")
        rpc_circuit_open = payload.get("rpc_circuit_open")
        if not isinstance(rpc_rate_limited, bool):
            rpc_rate_limited = False
            outcomes = payload.get("stage_outcomes")
            if isinstance(outcomes, list):
                rpc_rate_limited = any(
                    isinstance(item, list)
                    and len(item) == 3
                    and item[2] == "RPC_RATE_LIMITED"
                    for item in outcomes
                )
        if not isinstance(rpc_circuit_open, bool):
            rpc_circuit_open = False
            outcomes = payload.get("stage_outcomes")
            if isinstance(outcomes, list):
                rpc_circuit_open = any(
                    isinstance(item, list)
                    and len(item) == 3
                    and item[2] == "RPC_CIRCUIT_OPEN"
                    for item in outcomes
                )

        cycles.append(
            TimerHealthCycle(
                evidence_id=int(evidence_id),
                as_of=str(as_of),
                status=str(status),
                rpc_rate_limited=rpc_rate_limited,
                rpc_circuit_open=rpc_circuit_open,
                stages_failed=int(payload.get("stages_failed", 0) or 0),
                stages_skipped=int(payload.get("stages_skipped", 0) or 0),
            )
        )
    _assert_database_path_stable(snapshot)
    return tuple(cycles), snapshot


def _read_recent_cycles(
    database_path: Path,
    *,
    pool_address: str,
    limit: int,
) -> tuple[TimerHealthCycle, ...]:
    cycles, _snapshot = _read_recent_cycles_snapshot(
        database_path,
        pool_address=pool_address,
        limit=limit,
    )
    return cycles


def _consecutive_rate_limits(
    cycles: tuple[TimerHealthCycle, ...],
    *,
    max_gap_seconds: int,
) -> int:
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")

    count = 0
    previous_time: datetime | None = None
    for cycle in cycles:
        if not cycle.rpc_rate_limited:
            break
        try:
            cycle_time = _parse_time(cycle.as_of)
        except (TypeError, ValueError):
            break
        if previous_time is not None:
            gap = (previous_time - cycle_time).total_seconds()
            if gap < 0 or gap > max_gap_seconds:
                break
        count += 1
        previous_time = cycle_time
    return count


def inspect_timer_health(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    history_limit: int = 8,
    max_cycle_age_seconds: int = 2700,
    max_rate_limit_gap_seconds: int | None = None,
    rate_limit_streak_threshold: int = 2,
    now: Now = lambda: datetime.now(timezone.utc),
    runner: SystemctlRunner = subprocess.run,
) -> Phase2TimerHealthReport:
    if history_limit <= 0:
        raise ValueError("history_limit must be positive")
    if max_cycle_age_seconds <= 0:
        raise ValueError("max_cycle_age_seconds must be positive")
    rate_limit_gap_seconds = (
        max_cycle_age_seconds
        if max_rate_limit_gap_seconds is None
        else max_rate_limit_gap_seconds
    )
    if rate_limit_gap_seconds <= 0:
        raise ValueError("max_rate_limit_gap_seconds must be positive")
    if rate_limit_streak_threshold <= 0:
        raise ValueError("rate_limit_streak_threshold must be positive")

    activation_source = _activation_source_identity()
    env_capture = _capture_regular_file(
        Path(env_file),
        label="Phase-2 environment file",
        max_bytes=_MAX_ENV_BYTES,
        optional=True,
    )
    if env_capture is not None:
        _assert_regular_path_stable(
            env_capture.path,
            env_capture.opened,
            label="Phase-2 environment file",
        )

    base = ACTIVATION.inspect_activation(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        runner=runner,
    )
    if _activation_source_identity() != activation_source:
        raise ValueError(
            "reviewed activation checker changed during timer-health inspection"
        )
    if env_capture is not None:
        _assert_regular_path_stable(
            env_capture.path,
            env_capture.opened,
            label="Phase-2 environment file",
        )

    env_ready = bool(
        base.env_file_regular
        and all(item.configured for item in base.env_keys)
        and base.position_pool_matches_detector_topology
    )
    data_ready = bool(
        base.data_files
        and all(
            item.exists
            and item.regular_file
            and not item.symlink
            for item in base.data_files
        )
    )
    detector_state_ready = bool(
        base.detector_state_valid
        and base.detector_cursors_complete
    )

    base_states = {
        item.name: item
        for item in base.unit_states
    }
    detector = base_states.get(DETECTOR_UNIT)
    detector_active_enabled = bool(
        detector and detector.active and detector.enabled
    )

    stream_names = tuple(
        name
        for name in ACTIVATION.NEW_INACTIVE_UNITS
        if name.startswith("pio-phase2-isolated-prestate-stream@")
    )
    streams_active = bool(
        stream_names
        and all(
            name in base_states
            and base_states[name].active
            for name in stream_names
        )
    )

    timer = base_states.get(TIMER_UNIT)
    timer_active = bool(timer and timer.active)
    timer_enabled = bool(timer and timer.enabled)
    timer_active_enabled = timer_active and timer_enabled
    evidence_service = base_states.get(EVIDENCE_SERVICE)
    evidence_service_active = bool(
        evidence_service and evidence_service.active
    )

    # Activation owns the complete set of legacy Solana-RPC collectors that
    # overlap this isolated topology. Reuse those already-read states rather
    # than issuing duplicate local systemctl queries here.
    legacy_clear = all(
        name in base_states
        and not base_states[name].active
        and not base_states[name].enabled
        for name in ACTIVATION.LEGACY_UNITS
    )

    env_values = (
        ACTIVATION._read_env_bytes(env_capture.encoded)
        if env_capture is not None
        else {}
    )
    pool_address = env_values.get(
        "PIO_PHASE2_POSITION_POOL",
        "",
    ).strip()
    database_snapshot = None
    if pool_address:
        cycles, database_snapshot = _read_recent_cycles_snapshot(
            Path(data_root).expanduser() / "pio.db",
            pool_address=pool_address,
            limit=history_limit,
        )
    else:
        cycles = ()

    latest_age: float | None = None
    latest_recent = False
    if cycles:
        try:
            latest_age = (
                now().astimezone(timezone.utc)
                - _parse_time(cycles[0].as_of)
            ).total_seconds()
        except (TypeError, ValueError):
            latest_age = None
        latest_recent = bool(
            latest_age is not None
            and 0 <= latest_age <= max_cycle_age_seconds
        )

    rate_limit_streak = _consecutive_rate_limits(
        cycles,
        max_gap_seconds=rate_limit_gap_seconds,
    )
    latest_failed = bool(
        cycles and cycles[0].status == "COLLECTION_FAILED"
    )

    topology_ready = bool(
        base.runtime_ready
        and base.installed_units_exact
        and env_ready
        and data_ready
        and detector_state_ready
        and detector_active_enabled
        and streams_active
        and timer_active_enabled
        and legacy_clear
    )
    # Pausing the recurring evidence timer is a narrower safety action
    # than declaring the entire collection topology healthy. If sibling
    # collectors are degraded during an RPC-provider incident, they must not
    # prevent us from stopping additional rejected timer cycles.
    pause_recommended = bool(
        timer_active_enabled
        and latest_recent
        and rate_limit_streak >= rate_limit_streak_threshold
    )
    healthy = bool(
        topology_ready
        and latest_recent
        and not latest_failed
        and rate_limit_streak == 0
    )

    if _activation_source_identity() != activation_source:
        raise ValueError(
            "reviewed activation checker changed during timer-health inspection"
        )
    if env_capture is not None:
        _assert_regular_path_stable(
            env_capture.path,
            env_capture.opened,
            label="Phase-2 environment file",
        )
    if database_snapshot is not None:
        _assert_database_path_stable(database_snapshot)

    return Phase2TimerHealthReport(
        pool_address=pool_address or None,
        runtime_ready=bool(base.runtime_ready),
        installed_units_exact=bool(base.installed_units_exact),
        env_ready=env_ready,
        data_ready=data_ready,
        detector_state_ready=detector_state_ready,
        detector_active_enabled=detector_active_enabled,
        streams_active=streams_active,
        timer_active=timer_active,
        timer_enabled=timer_enabled,
        timer_active_enabled=timer_active_enabled,
        legacy_collectors_quiescent=legacy_clear,
        evidence_service_active=evidence_service_active,
        cycles=cycles,
        latest_cycle_age_seconds=latest_age,
        latest_cycle_recent=latest_recent,
        latest_cycle_failed=latest_failed,
        consecutive_rpc_rate_limited=rate_limit_streak,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        pause_recommended=pause_recommended,
        collection_healthy=healthy,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only health check for the running isolated Phase-2 evidence "
            "timer. Repeated provider rate limits are surfaced separately from "
            "ordinary evidence gaps; healthy collection is never throttled."
        )
    )
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument("--history-limit", type=int, default=8)
    parser.add_argument("--max-cycle-age-seconds", type=int, default=2700)
    parser.add_argument("--max-rate-limit-gap-seconds", type=int)
    parser.add_argument("--rate-limit-streak-threshold", type=int, default=2)
    args = parser.parse_args()

    report = inspect_timer_health(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        history_limit=args.history_limit,
        max_cycle_age_seconds=args.max_cycle_age_seconds,
        max_rate_limit_gap_seconds=args.max_rate_limit_gap_seconds,
        rate_limit_streak_threshold=args.rate_limit_streak_threshold,
    )
    print(json.dumps(report.to_record(), indent=2))

    if not report.collection_healthy:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
