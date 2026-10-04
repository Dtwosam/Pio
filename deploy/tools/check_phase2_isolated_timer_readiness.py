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
SMOKE_RUNNER_TOOL = TOOLS_DIR / "run_phase2_isolated_smoke.py"
SMOKE_RUNNER_TOOL_RELATIVE = "deploy/tools/run_phase2_isolated_smoke.py"

_MAX_TOOL_BYTES = 4 * 1024 * 1024
_MAX_RECEIPT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _CapturedFile:
    path: Path
    encoded: bytes
    opened: os.stat_result


@dataclass(frozen=True)
class _DatabasePathSnapshot:
    path: Path
    opened: os.stat_result


@dataclass(frozen=True)
class _RuntimeCurrentSnapshot:
    current_path: Path
    opened: os.stat_result
    link_target: str
    resolved_target: Path


@dataclass(frozen=True)
class Phase2TimerReadinessSnapshot:
    smoke_source_commit: str
    smoke_source_sha256: str
    receipt: _CapturedFile | None
    database: _DatabasePathSnapshot | None
    runtime_current: _RuntimeCurrentSnapshot | None


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
    try:
        opened_path = os.lstat(raw)
    except FileNotFoundError:
        if optional:
            return None
        raise ValueError(f"{label} is missing")
    except OSError as exc:
        raise ValueError(f"{label} cannot be inspected safely") from exc

    if stat.S_ISLNK(opened_path.st_mode) or not stat.S_ISREG(opened_path.st_mode):
        if optional:
            return None
        raise ValueError(f"{label} must be a regular file")

    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        if optional:
            return None
        raise ValueError(f"{label} is missing") from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        if optional:
            return None
        raise ValueError(f"{label} cannot be opened safely") from exc

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


def _load_captured_smoke_runner(
    path: Path,
    name: str,
) -> tuple[Any, _CapturedFile]:
    captured = _capture_regular_file(
        path,
        label="reviewed smoke runner",
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
        label="reviewed smoke runner",
    )
    return module, captured


SMOKE, _SMOKE_CAPTURE = _load_captured_smoke_runner(
    SMOKE_RUNNER_TOOL,
    "phase2_isolated_timer_readiness_smoke",
)
_SMOKE_SHA256 = hashlib.sha256(_SMOKE_CAPTURE.encoded).hexdigest()

Now = Callable[[], datetime]

PROGRESS_EDGE_TYPE = "PHASE2_EVIDENCE_CYCLE_PROGRESS_V1"


@dataclass(frozen=True)
class Phase2TimerReadinessReport:
    smoke_readiness_current: bool
    receipt_regular: bool
    receipt_valid: bool
    receipt_runtime_matches: bool
    receipt_age_seconds: float | None
    receipt_fresh: bool
    receipt_stage_statuses_valid: bool
    progress_evidence_id: int | None
    evidence_row_present: bool
    evidence_row_matches_receipt: bool
    latest_progress_evidence_id: int | None
    receipt_is_latest_for_pool: bool
    evidence_row_non_qualified: bool
    evidence_row_no_promotion: bool
    timer_ready: bool
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
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


def _smoke_source_identity() -> tuple[str, str]:
    _assert_regular_path_stable(
        _SMOKE_CAPTURE.path,
        _SMOKE_CAPTURE.opened,
        label="reviewed smoke runner",
    )
    commit = _repo_head()
    historical = subprocess.run(
        ["git", "show", f"{commit}:{SMOKE_RUNNER_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed smoke runner is not present at reviewed source commit"
        )
    if historical.stdout != _SMOKE_CAPTURE.encoded:
        raise ValueError(
            "reviewed smoke runner bytes do not match reviewed source commit"
        )
    _assert_regular_path_stable(
        _SMOKE_CAPTURE.path,
        _SMOKE_CAPTURE.opened,
        label="reviewed smoke runner",
    )
    return commit, _SMOKE_SHA256


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_receipt_bytes(encoded: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _receipt_stages_valid(payload: dict[str, Any]) -> bool:
    stages = payload.get("stage_statuses")
    if not isinstance(stages, list) or not stages:
        return False
    for item in stages:
        if not isinstance(item, dict):
            return False
        status = item.get("status")
        failure = item.get("failure_category")
        if status in {"FAILED", "SKIPPED"}:
            return False
        if failure in {"RPC_RATE_LIMITED", "RPC_CIRCUIT_OPEN"}:
            return False
    return True


def _capture_runtime_current(
    runtime_root: str | Path,
) -> _RuntimeCurrentSnapshot:
    root = Path(runtime_root).expanduser()
    current = root / "current"
    try:
        opened = os.lstat(current)
    except OSError as exc:
        raise ValueError("isolated runtime current path is missing") from exc
    if not stat.S_ISLNK(opened.st_mode):
        raise ValueError("isolated runtime current path must be a symlink")
    try:
        link_target = os.readlink(current)
        resolved = SMOKE._runtime_current(runtime_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("isolated runtime current path is invalid") from exc

    after = os.lstat(current)
    if (
        after.st_dev != opened.st_dev
        or after.st_ino != opened.st_ino
        or after.st_mode != opened.st_mode
        or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns
        or after.st_ctime_ns != opened.st_ctime_ns
        or os.readlink(current) != link_target
    ):
        raise ValueError("isolated runtime current changed while reading")
    return _RuntimeCurrentSnapshot(
        current_path=current,
        opened=opened,
        link_target=link_target,
        resolved_target=resolved,
    )


def _assert_runtime_current_stable(
    snapshot: _RuntimeCurrentSnapshot,
) -> None:
    try:
        current = os.lstat(snapshot.current_path)
        target = os.readlink(snapshot.current_path)
    except OSError as exc:
        raise ValueError(
            "isolated runtime current changed after readiness capture"
        ) from exc
    before = snapshot.opened
    if (
        not stat.S_ISLNK(current.st_mode)
        or current.st_dev != before.st_dev
        or current.st_ino != before.st_ino
        or current.st_mode != before.st_mode
        or current.st_size != before.st_size
        or current.st_mtime_ns != before.st_mtime_ns
        or current.st_ctime_ns != before.st_ctime_ns
        or target != snapshot.link_target
    ):
        raise ValueError(
            "isolated runtime current changed after readiness capture"
        )
    resolved = snapshot.current_path.resolve(strict=True)
    if resolved != snapshot.resolved_target:
        raise ValueError(
            "isolated runtime current target changed after readiness capture"
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
    return _DatabasePathSnapshot(path=resolved, opened=opened)


def _assert_database_path_stable(
    snapshot: _DatabasePathSnapshot,
) -> None:
    try:
        current = os.stat(snapshot.path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("Phase-2 database path changed during readiness") from exc
    before = snapshot.opened
    # Size/mtime/ctime may change because detector streams can legitimately
    # append evidence while this read-only check runs. Device/inode/mode bind
    # the SQLite transaction to the same database object without rejecting
    # legitimate concurrent writes.
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != before.st_dev
        or current.st_ino != before.st_ino
        or current.st_mode != before.st_mode
    ):
        raise ValueError("Phase-2 database path changed during readiness")


def _read_progress_snapshot(
    database_path: Path,
    evidence_id: int,
) -> tuple[
    dict[str, Any] | None,
    int | None,
    _DatabasePathSnapshot | None,
]:
    snapshot = _database_path_snapshot(database_path)
    if snapshot is None:
        return None, None, None

    uri = f"file:{snapshot.path}?mode=ro"
    row = None
    latest_id: int | None = None
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            conn.execute("BEGIN")
            raw = conn.execute(
                """
                SELECT id, edge_type, pool_address, as_of, status,
                       qualified, evidence_json
                FROM advanced_edge_evidence
                WHERE id = ?
                """,
                (evidence_id,),
            ).fetchone()

            if raw is not None:
                try:
                    evidence = json.loads(str(raw[6]))
                except json.JSONDecodeError:
                    evidence = None
                if isinstance(evidence, dict):
                    row = {
                        "id": int(raw[0]),
                        "edge_type": str(raw[1]),
                        "pool_address": str(raw[2]),
                        "as_of": (
                            str(raw[3]) if raw[3] is not None else None
                        ),
                        "status": str(raw[4]),
                        "qualified": bool(raw[5]),
                        "evidence": evidence,
                    }
                    latest = conn.execute(
                        """
                        SELECT id
                        FROM advanced_edge_evidence
                        WHERE edge_type = ?
                          AND pool_address = ?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (PROGRESS_EDGE_TYPE, row["pool_address"]),
                    ).fetchone()
                    latest_id = int(latest[0]) if latest is not None else None
            conn.rollback()
    except sqlite3.Error:
        row = None
        latest_id = None

    _assert_database_path_stable(snapshot)
    return row, latest_id, snapshot


def assert_timer_readiness_snapshot_stable(
    snapshot: Phase2TimerReadinessSnapshot,
) -> None:
    if _smoke_source_identity() != (
        snapshot.smoke_source_commit,
        snapshot.smoke_source_sha256,
    ):
        raise ValueError(
            "reviewed smoke runner changed after timer-readiness capture"
        )
    if snapshot.receipt is not None:
        _assert_regular_path_stable(
            snapshot.receipt.path,
            snapshot.receipt.opened,
            label="smoke receipt",
        )
    if snapshot.database is not None:
        _assert_database_path_stable(snapshot.database)
    if snapshot.runtime_current is not None:
        _assert_runtime_current_stable(snapshot.runtime_current)


def capture_timer_readiness(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    max_receipt_age_seconds: int = 1800,
    now: Now = lambda: datetime.now(timezone.utc),
) -> tuple[Phase2TimerReadinessReport, Phase2TimerReadinessSnapshot]:
    if max_receipt_age_seconds <= 0:
        raise ValueError("max_receipt_age_seconds must be positive")

    smoke_source_before = _smoke_source_identity()
    smoke_readiness = SMOKE.READINESS.inspect_smoke_readiness(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
    )
    if _smoke_source_identity() != smoke_source_before:
        raise ValueError(
            "reviewed smoke runner changed during timer-readiness inspection"
        )
    smoke_current = bool(smoke_readiness.smoke_ready)

    receipt_capture = _capture_regular_file(
        Path(receipt_path),
        label="smoke receipt",
        max_bytes=_MAX_RECEIPT_BYTES,
        optional=True,
    )
    receipt_regular = receipt_capture is not None
    payload = (
        _parse_receipt_bytes(receipt_capture.encoded)
        if receipt_capture is not None
        else None
    )

    receipt_valid = bool(
        payload
        and payload.get("format_version") == 1
        and payload.get("smoke_passed") is True
        and payload.get("eligible_for_timer_enable_preflight") is True
        and isinstance(payload.get("runtime_target"), str)
        and isinstance(payload.get("progress_evidence_id"), int)
        and int(payload["progress_evidence_id"]) > 0
        and isinstance(payload.get("finished_at"), str)
    )

    runtime_snapshot: _RuntimeCurrentSnapshot | None = None
    runtime_matches = False
    age_seconds: float | None = None
    fresh = False
    stages_valid = False
    evidence_id: int | None = None

    if receipt_valid and payload is not None:
        try:
            runtime_snapshot = _capture_runtime_current(runtime_root)
            runtime_matches = (
                str(runtime_snapshot.resolved_target)
                == str(payload["runtime_target"])
            )
        except (OSError, RuntimeError, ValueError):
            runtime_snapshot = None
            runtime_matches = False

        try:
            age_seconds = (
                now().astimezone(timezone.utc)
                - _parse_time(str(payload["finished_at"]))
            ).total_seconds()
        except (TypeError, ValueError):
            age_seconds = None
        fresh = bool(
            age_seconds is not None
            and 0 <= age_seconds <= max_receipt_age_seconds
        )
        stages_valid = _receipt_stages_valid(payload)
        evidence_id = int(payload["progress_evidence_id"])

    database_path = Path(data_root) / "pio.db"
    if evidence_id is not None:
        row, latest_progress_id, database_snapshot = _read_progress_snapshot(
            database_path,
            evidence_id,
        )
    else:
        row = None
        latest_progress_id = None
        database_snapshot = _database_path_snapshot(database_path)

    row_present = row is not None
    row_matches = False
    receipt_is_latest = False
    row_non_qualified = False
    row_no_promotion = False

    if row is not None and payload is not None:
        receipt_is_latest = (
            latest_progress_id is not None
            and latest_progress_id == evidence_id
        )
        row_matches = bool(
            row["id"] == evidence_id
            and row["edge_type"] == PROGRESS_EDGE_TYPE
            and row["as_of"] == payload.get("finished_at")
            and row["status"] in {
                "COLLECTION_SUCCESS",
                "COLLECTION_PARTIAL",
            }
        )
        row_non_qualified = bool(
            row["qualified"] is False
            and row["evidence"].get("qualified") is False
        )
        row_no_promotion = bool(
            row["evidence"].get("promotion_gate_evaluated") is False
            and row["evidence"].get("phase_promotion_performed") is False
            and row["evidence"].get("live_authorized") is False
            and row["evidence"].get("actionable") is False
        )

    ready = bool(
        smoke_current
        and receipt_regular
        and receipt_valid
        and runtime_matches
        and fresh
        and stages_valid
        and row_present
        and row_matches
        and receipt_is_latest
        and row_non_qualified
        and row_no_promotion
    )

    report = Phase2TimerReadinessReport(
        smoke_readiness_current=smoke_current,
        receipt_regular=receipt_regular,
        receipt_valid=receipt_valid,
        receipt_runtime_matches=runtime_matches,
        receipt_age_seconds=age_seconds,
        receipt_fresh=fresh,
        receipt_stage_statuses_valid=stages_valid,
        progress_evidence_id=evidence_id,
        evidence_row_present=row_present,
        evidence_row_matches_receipt=row_matches,
        latest_progress_evidence_id=latest_progress_id,
        receipt_is_latest_for_pool=receipt_is_latest,
        evidence_row_non_qualified=row_non_qualified,
        evidence_row_no_promotion=row_no_promotion,
        timer_ready=ready,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )
    snapshot = Phase2TimerReadinessSnapshot(
        smoke_source_commit=smoke_source_before[0],
        smoke_source_sha256=smoke_source_before[1],
        receipt=receipt_capture,
        database=database_snapshot,
        runtime_current=runtime_snapshot,
    )
    assert_timer_readiness_snapshot_stable(snapshot)
    return report, snapshot


def inspect_timer_readiness(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    max_receipt_age_seconds: int = 1800,
    now: Now = lambda: datetime.now(timezone.utc),
) -> Phase2TimerReadinessReport:
    report, _snapshot = capture_timer_readiness(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
        now=now,
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only gate for enabling the isolated bounded Phase-2 "
            "evidence timer after a successful manual smoke receipt."
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
    args = parser.parse_args()

    report = inspect_timer_readiness(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.timer_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
