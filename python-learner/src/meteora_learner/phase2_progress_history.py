from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import sqlite3
from typing import Any

from .phase2_evidence_cycle import PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE
from .storage import Storage


@dataclass(frozen=True)
class Phase2ProgressSnapshot:
    evidence_id: int
    created_at: str
    as_of: str | None
    status: str
    stages_successful: int
    stages_partial: int
    stages_failed: int
    reconciliation: dict[str, Any] | None
    calibration: dict[str, Any] | None
    work_queue_items: int
    work_queue_task_counts: tuple[tuple[str, int], ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase2ProgressDelta:
    metric: str
    previous: float
    latest: float
    delta: float


@dataclass(frozen=True)
class Phase2ProgressHistoryReport:
    pool_address: str
    snapshots_returned: int
    latest: Phase2ProgressSnapshot | None
    previous: Phase2ProgressSnapshot | None
    numeric_deltas: tuple[Phase2ProgressDelta, ...]
    read_only: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _snapshot(row: Any) -> Phase2ProgressSnapshot:
    if int(row["qualified"]) != 0:
        raise ValueError(
            "Phase-2 progress history contains qualified evidence"
        )
    try:
        evidence = json.loads(str(row["evidence_json"]))
    except json.JSONDecodeError as exc:
        raise ValueError("Phase-2 progress evidence JSON is invalid") from exc
    if not isinstance(evidence, dict):
        raise ValueError("Phase-2 progress evidence must be an object")
    if evidence.get("promotion_gate_evaluated") is not False:
        raise ValueError(
            "Phase-2 progress evidence crossed promotion boundary"
        )
    if evidence.get("phase_promotion_performed") is not False:
        raise ValueError(
            "Phase-2 progress evidence crossed promotion boundary"
        )
    if evidence.get("actionable") is not False:
        raise ValueError(
            "Phase-2 progress evidence crossed action boundary"
        )
    if evidence.get("live_authorized") is not False:
        raise ValueError(
            "Phase-2 progress evidence crossed live boundary"
        )

    task_counts = evidence.get("work_queue_task_counts") or []
    parsed_counts: list[tuple[str, int]] = []
    if not isinstance(task_counts, list):
        raise ValueError("work_queue_task_counts must be a list")
    for item in task_counts:
        if not isinstance(item, dict):
            raise ValueError("work_queue_task_counts entry must be an object")
        parsed_counts.append(
            (str(item["task_type"]), int(item["count"]))
        )

    return Phase2ProgressSnapshot(
        evidence_id=int(row["id"]),
        created_at=str(row["created_at"]),
        as_of=str(row["as_of"]) if row["as_of"] is not None else None,
        status=str(row["status"]),
        stages_successful=int(evidence.get("stages_successful", 0)),
        stages_partial=int(evidence.get("stages_partial", 0)),
        stages_failed=int(evidence.get("stages_failed", 0)),
        reconciliation=(
            dict(evidence["reconciliation"])
            if isinstance(evidence.get("reconciliation"), dict)
            else None
        ),
        calibration=(
            dict(evidence["calibration"])
            if isinstance(evidence.get("calibration"), dict)
            else None
        ),
        work_queue_items=int(evidence.get("work_queue_items", 0)),
        work_queue_task_counts=tuple(parsed_counts),
    )


def _flatten_numeric(
    prefix: str,
    value: Any,
    out: dict[str, float],
) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out[prefix] = float(value)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            _flatten_numeric(child_prefix, child, out)


def _numeric_metrics(snapshot: Phase2ProgressSnapshot) -> dict[str, float]:
    metrics: dict[str, float] = {
        "stages_successful": float(snapshot.stages_successful),
        "stages_partial": float(snapshot.stages_partial),
        "stages_failed": float(snapshot.stages_failed),
        "work_queue_items": float(snapshot.work_queue_items),
    }
    _flatten_numeric(
        "reconciliation",
        snapshot.reconciliation,
        metrics,
    )
    _flatten_numeric(
        "calibration",
        snapshot.calibration,
        metrics,
    )
    for task_type, count in snapshot.work_queue_task_counts:
        metrics[f"work_queue.{task_type}"] = float(count)
    return metrics


def build_phase2_progress_history(
    storage: Storage,
    *,
    pool_address: str,
    limit: int = 10,
) -> Phase2ProgressHistoryReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if limit <= 0:
        raise ValueError("limit must be positive")

    with storage.connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, created_at, as_of, status, qualified, evidence_json
            FROM advanced_edge_evidence
            WHERE edge_type = ? AND pool_address = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE,
                pool_address,
                limit,
            ),
        ).fetchall()

    snapshots = tuple(_snapshot(row) for row in rows)
    latest = snapshots[0] if snapshots else None
    previous = snapshots[1] if len(snapshots) > 1 else None

    deltas: list[Phase2ProgressDelta] = []
    if latest is not None and previous is not None:
        latest_metrics = _numeric_metrics(latest)
        previous_metrics = _numeric_metrics(previous)
        for metric in sorted(
            set(latest_metrics) & set(previous_metrics)
        ):
            before = previous_metrics[metric]
            after = latest_metrics[metric]
            deltas.append(
                Phase2ProgressDelta(
                    metric=metric,
                    previous=before,
                    latest=after,
                    delta=after - before,
                )
            )

    return Phase2ProgressHistoryReport(
        pool_address=pool_address,
        snapshots_returned=len(snapshots),
        latest=latest,
        previous=previous,
        numeric_deltas=tuple(deltas),
        read_only=True,
    )
