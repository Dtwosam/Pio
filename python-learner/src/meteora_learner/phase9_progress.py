from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class Phase9WalletActivityProgress:
    pool_address: str
    backfill_before_signature: str | None
    backfill_exhausted: bool
    pages_scanned: int
    signatures_scanned: int
    matching_transactions: int
    positions_discovered: int
    updated_at: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9ProgressReport:
    status: str
    snapshot_count: int
    integrity_verified: bool
    first_snapshot_id: int | None
    first_created_at: str | None
    latest_snapshot_id: int | None
    latest_created_at: str | None
    first_task_count: int
    latest_task_count: int
    resolved_tasks: tuple[str, ...]
    new_tasks: tuple[str, ...]
    phase8_current: bool
    research_bundle_ready: bool
    promotion_ready: bool
    phase9_current: bool
    policy_authorization_current: bool
    controlled_validation_current: bool
    rollout_simulation_current: bool
    rollback_simulation_current: bool
    prewire_ready: bool
    prewire_manifest_current: bool
    latest_queue_sha256: str | None
    wallet_activity_scans: tuple[Phase9WalletActivityProgress, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _task_keys(state: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in state.get("items", []):
        if not isinstance(item, dict):
            continue
        task_type = str(item.get("task_type", "")).strip()
        scope = str(item.get("scope", "")).strip()
        if task_type:
            result.add(f"{task_type}:{scope}")
    return result


def _snapshot_digest(
    criteria: dict[str, Any],
    state: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "criteria": criteria,
            "state": state,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _wallet_activity_scans(
    storage: Storage,
) -> tuple[Phase9WalletActivityProgress, ...]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT pool_address, backfill_before_signature,
                   backfill_exhausted, pages_scanned,
                   signatures_scanned, matching_transactions,
                   positions_discovered, updated_at
            FROM phase9_pool_activity_scan_state
            ORDER BY pool_address ASC
            """
        ).fetchall()
    return tuple(
        Phase9WalletActivityProgress(
            pool_address=str(row[0]),
            backfill_before_signature=(
                str(row[1]) if row[1] is not None else None
            ),
            backfill_exhausted=bool(row[2]),
            pages_scanned=int(row[3]),
            signatures_scanned=int(row[4]),
            matching_transactions=int(row[5]),
            positions_discovered=int(row[6]),
            updated_at=str(row[7]),
        )
        for row in rows
    )


def evaluate_phase9_progress(storage: Storage) -> Phase9ProgressReport:
    wallet_activity_scans = _wallet_activity_scans(storage)
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, queue_sha256,
                   criteria_json, state_json
            FROM phase9_work_queue_snapshots
            ORDER BY id ASC
            """
        ).fetchall()

    if not rows:
        return Phase9ProgressReport(
            status="NO_SNAPSHOTS",
            snapshot_count=0,
            integrity_verified=True,
            first_snapshot_id=None,
            first_created_at=None,
            latest_snapshot_id=None,
            latest_created_at=None,
            first_task_count=0,
            latest_task_count=0,
            resolved_tasks=(),
            new_tasks=(),
            phase8_current=False,
            research_bundle_ready=False,
            promotion_ready=False,
            phase9_current=False,
            policy_authorization_current=False,
            controlled_validation_current=False,
            rollout_simulation_current=False,
            rollback_simulation_current=False,
            prewire_ready=False,
            prewire_manifest_current=False,
            latest_queue_sha256=None,
            wallet_activity_scans=wallet_activity_scans,
            reasons=(
                "no Phase 9 work-queue progress snapshots are persisted",
            ),
        )

    decoded: list[tuple[int, str, str, dict[str, Any], dict[str, Any]]] = []
    reasons: list[str] = []
    for raw_id, created_at, digest, criteria_json, state_json in rows:
        try:
            criteria = json.loads(str(criteria_json))
            state = json.loads(str(state_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            reasons.append(
                f"snapshot {raw_id} contains invalid persisted JSON"
            )
            continue
        if not isinstance(criteria, dict) or not isinstance(state, dict):
            reasons.append(
                f"snapshot {raw_id} payload is not an object"
            )
            continue
        expected = _snapshot_digest(criteria, state)
        if expected != str(digest):
            reasons.append(
                f"snapshot {raw_id} checksum does not match persisted payload"
            )
        decoded.append(
            (
                int(raw_id),
                str(created_at),
                str(digest),
                criteria,
                state,
            )
        )

    integrity_verified = not reasons and len(decoded) == len(rows)
    if not decoded:
        return Phase9ProgressReport(
            status="INVALID_SNAPSHOTS",
            snapshot_count=len(rows),
            integrity_verified=False,
            first_snapshot_id=None,
            first_created_at=None,
            latest_snapshot_id=None,
            latest_created_at=None,
            first_task_count=0,
            latest_task_count=0,
            resolved_tasks=(),
            new_tasks=(),
            phase8_current=False,
            research_bundle_ready=False,
            promotion_ready=False,
            phase9_current=False,
            policy_authorization_current=False,
            controlled_validation_current=False,
            rollout_simulation_current=False,
            rollback_simulation_current=False,
            prewire_ready=False,
            prewire_manifest_current=False,
            latest_queue_sha256=None,
            wallet_activity_scans=wallet_activity_scans,
            reasons=tuple(reasons),
        )

    first = decoded[0]
    latest = decoded[-1]
    first_tasks = _task_keys(first[4])
    latest_tasks = _task_keys(latest[4])
    resolved = tuple(sorted(first_tasks - latest_tasks))
    new = tuple(sorted(latest_tasks - first_tasks))

    phase8_current = bool(latest[4].get("phase8_promoted"))
    research_bundle_ready = bool(
        latest[4].get("research_bundle_ready")
    )
    promotion_ready = bool(latest[4].get("promotion_ready"))
    phase9_current = bool(latest[4].get("phase9_current"))
    policy_authorization_current = bool(
        latest[4].get("policy_authorization_current")
    )
    controlled_validation_current = bool(
        latest[4].get("controlled_validation_current")
    )
    rollout_simulation_current = bool(
        latest[4].get("rollout_simulation_current")
    )
    rollback_simulation_current = bool(
        latest[4].get("rollback_simulation_current")
    )
    prewire_ready = bool(latest[4].get("prewire_ready"))
    prewire_manifest_current = bool(
        latest[4].get("prewire_manifest_current")
    )

    if not integrity_verified:
        status = "INTEGRITY_FAILED"
    elif prewire_manifest_current:
        status = "PREWIRE_MANIFEST_CURRENT"
    elif prewire_ready:
        status = "PREWIRE_READY"
    elif rollback_simulation_current:
        status = "ROLLBACK_SIMULATION_CURRENT"
    elif rollout_simulation_current:
        status = "ROLLOUT_SIMULATION_CURRENT"
    elif controlled_validation_current:
        status = "CONTROLLED_VALIDATION_CURRENT"
    elif policy_authorization_current:
        status = "POLICY_AUTHORIZATION_CURRENT"
    elif phase9_current:
        status = "PHASE9_CURRENT"
    elif promotion_ready:
        status = "PROMOTION_READY"
    elif research_bundle_ready:
        status = "RESEARCH_BUNDLE_READY"
    elif not phase8_current:
        status = "PHASE8_BLOCKED"
    elif latest_tasks:
        status = "EVIDENCE_PENDING"
    else:
        status = "NO_BLOCKERS_REPORTED"

    return Phase9ProgressReport(
        status=status,
        snapshot_count=len(rows),
        integrity_verified=integrity_verified,
        first_snapshot_id=first[0],
        first_created_at=first[1],
        latest_snapshot_id=latest[0],
        latest_created_at=latest[1],
        first_task_count=len(first_tasks),
        latest_task_count=len(latest_tasks),
        resolved_tasks=resolved,
        new_tasks=new,
        phase8_current=phase8_current,
        research_bundle_ready=research_bundle_ready,
        promotion_ready=promotion_ready,
        phase9_current=phase9_current,
        policy_authorization_current=policy_authorization_current,
        controlled_validation_current=controlled_validation_current,
        rollout_simulation_current=rollout_simulation_current,
        rollback_simulation_current=rollback_simulation_current,
        prewire_ready=prewire_ready,
        prewire_manifest_current=prewire_manifest_current,
        latest_queue_sha256=latest[2],
        wallet_activity_scans=wallet_activity_scans,
        reasons=tuple(reasons),
    )
