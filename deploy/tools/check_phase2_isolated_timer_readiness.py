#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
SMOKE_RUNNER_TOOL = TOOLS_DIR / "run_phase2_isolated_smoke.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SMOKE = _load(
    SMOKE_RUNNER_TOOL,
    "phase2_isolated_timer_readiness_smoke",
)

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
    evidence_row_non_qualified: bool
    evidence_row_no_promotion: bool
    timer_ready: bool
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


def _read_receipt(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
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


def _read_progress_row(
    database_path: Path,
    evidence_id: int,
) -> dict[str, Any] | None:
    if database_path.is_symlink() or not database_path.is_file():
        return None
    uri = f"file:{database_path.resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            row = conn.execute(
                """
                SELECT id, edge_type, pool_address, as_of, status,
                       qualified, evidence_json
                FROM advanced_edge_evidence
                WHERE id = ?
                """,
                (evidence_id,),
            ).fetchone()
    except sqlite3.Error:
        return None

    if row is None:
        return None
    try:
        evidence = json.loads(str(row[6]))
    except json.JSONDecodeError:
        return None
    if not isinstance(evidence, dict):
        return None
    return {
        "id": int(row[0]),
        "edge_type": str(row[1]),
        "pool_address": str(row[2]),
        "as_of": str(row[3]) if row[3] is not None else None,
        "status": str(row[4]),
        "qualified": bool(row[5]),
        "evidence": evidence,
    }


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
    if max_receipt_age_seconds <= 0:
        raise ValueError("max_receipt_age_seconds must be positive")

    smoke_readiness = SMOKE.READINESS.inspect_smoke_readiness(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
    )
    smoke_current = bool(smoke_readiness.smoke_ready)

    receipt = Path(receipt_path).expanduser()
    receipt_regular = receipt.is_file() and not receipt.is_symlink()
    payload = _read_receipt(receipt) if receipt_regular else None

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

    runtime_matches = False
    age_seconds: float | None = None
    fresh = False
    stages_valid = False
    evidence_id: int | None = None

    if receipt_valid and payload is not None:
        try:
            runtime = SMOKE._runtime_current(runtime_root)
            runtime_matches = (
                str(runtime) == str(payload["runtime_target"])
            )
        except (OSError, RuntimeError, ValueError):
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

    row = (
        _read_progress_row(
            Path(data_root) / "pio.db",
            evidence_id,
        )
        if evidence_id is not None
        else None
    )
    row_present = row is not None
    row_matches = False
    row_non_qualified = False
    row_no_promotion = False

    if row is not None and payload is not None:
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
        and row_non_qualified
        and row_no_promotion
    )

    return Phase2TimerReadinessReport(
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
        evidence_row_non_qualified=row_non_qualified,
        evidence_row_no_promotion=row_no_promotion,
        timer_ready=ready,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


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
