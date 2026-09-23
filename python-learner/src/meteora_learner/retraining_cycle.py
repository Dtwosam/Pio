from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any
from uuid import uuid4

from .continuous_learning import (
    ContinuousLearningCriteria,
    ContinuousLearningPlan,
    build_continuous_learning_plan,
    RETRAIN_PLAN_EVIDENCE_TYPE,
)
from .ml_registry import OFFLINE_CANDIDATE
from .storage import Storage, utc_now_iso


ACTIVE_KEY = "ACTIVE"
CYCLE_LINK_EVIDENCE_TYPE = "CONTINUOUS_RETRAIN_CYCLE_LINK_V1"
ACTIVE_STATUSES = (
    "PLANNED",
    "CHALLENGER_REGISTERED",
    "OFFLINE_QUALIFIED",
    "PAPER_CHALLENGER",
)


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("retraining-cycle timestamps require timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class RetrainingCycle:
    cycle_id: str
    created_at: str
    updated_at: str
    status: str
    champion_model_id: str
    champion_dataset_version: str
    champion_evidence_watermark: str
    plan_evidence_id: int
    plan_as_of: str
    target_dataset_version: str
    challenger_model_id: str | None
    plan: dict[str, Any]
    notes: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _row_to_cycle(row: Any) -> RetrainingCycle:
    return RetrainingCycle(
        cycle_id=str(row[0]),
        created_at=str(row[1]),
        updated_at=str(row[2]),
        status=str(row[3]),
        champion_model_id=str(row[4]),
        champion_dataset_version=str(row[5]),
        champion_evidence_watermark=str(row[6]),
        plan_evidence_id=int(row[7]),
        plan_as_of=str(row[8]),
        target_dataset_version=str(row[9]),
        challenger_model_id=(
            str(row[10]) if row[10] is not None else None
        ),
        plan=json.loads(str(row[11])),
        notes=str(row[12]) if row[12] is not None else None,
    )


def retraining_cycle(
    storage: Storage,
    *,
    cycle_id: str,
) -> RetrainingCycle:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT cycle_id, created_at, updated_at, status,
                   champion_model_id, champion_dataset_version,
                   champion_evidence_watermark, plan_evidence_id,
                   plan_as_of, target_dataset_version,
                   challenger_model_id, plan_json, notes
            FROM continuous_learning_cycles
            WHERE cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown retraining cycle: {cycle_id}")
    return _row_to_cycle(row)


def active_retraining_cycle(
    storage: Storage,
) -> RetrainingCycle | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT cycle_id, created_at, updated_at, status,
                   champion_model_id, champion_dataset_version,
                   champion_evidence_watermark, plan_evidence_id,
                   plan_as_of, target_dataset_version,
                   challenger_model_id, plan_json, notes
            FROM continuous_learning_cycles
            WHERE active_key = 'ACTIVE'
            LIMIT 1
            """
        ).fetchone()
    return _row_to_cycle(row) if row is not None else None


def start_retraining_cycle(
    storage: Storage,
    *,
    target_dataset_version: str,
    criteria: ContinuousLearningCriteria = ContinuousLearningCriteria(),
    as_of: str | None = None,
    cycle_id: str | None = None,
    notes: str | None = None,
) -> RetrainingCycle:
    if not target_dataset_version.strip():
        raise ValueError("target_dataset_version is required")
    cutoff = (
        _time(as_of).isoformat()
        if as_of is not None
        else datetime.now(timezone.utc).isoformat()
    )
    plan = build_continuous_learning_plan(
        storage,
        criteria=criteria,
        as_of=cutoff,
    )
    if not plan.retrain_due:
        raise ValueError(
            f"continuous retraining is not due: {plan.status}"
        )
    if (
        plan.champion_model_id is None
        or plan.champion_dataset_version is None
        or plan.champion_evidence_watermark is None
    ):
        raise ValueError(
            "due retraining plan is missing champion lineage"
        )

    selected_cycle_id = (
        cycle_id.strip()
        if cycle_id is not None and cycle_id.strip()
        else f"retrain-{uuid4()}"
    )
    now = utc_now_iso()
    plan_record = plan.to_record()
    plan_json = json.dumps(plan_record, separators=(",", ":"))

    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            """
            SELECT cycle_id
            FROM continuous_learning_cycles
            WHERE active_key = 'ACTIVE'
            LIMIT 1
            """
        ).fetchone()
        if active is not None:
            raise ValueError(
                f"active retraining cycle already exists: {active[0]}"
            )

        champion = conn.execute(
            """
            SELECT model_id, dataset_version
            FROM model_registry
            WHERE status = 'CHAMPION'
            LIMIT 1
            """
        ).fetchone()
        if champion is None:
            raise ValueError(
                "champion disappeared while starting retraining cycle"
            )
        if str(champion[0]) != plan.champion_model_id:
            raise ValueError(
                "champion changed while starting retraining cycle"
            )
        if str(champion[1]) != plan.champion_dataset_version:
            raise ValueError(
                "champion dataset changed while starting retraining cycle"
            )

        evidence = conn.execute(
            """
            INSERT INTO model_live_evidence(
                model_id, created_at, evidence_type,
                status, evidence_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                plan.champion_model_id,
                now,
                RETRAIN_PLAN_EVIDENCE_TYPE,
                plan.status,
                plan_json,
            ),
        )
        plan_evidence_id = int(evidence.lastrowid)

        try:
            conn.execute(
                """
                INSERT INTO continuous_learning_cycles(
                    cycle_id, created_at, updated_at, status,
                    active_key, champion_model_id,
                    champion_dataset_version,
                    champion_evidence_watermark,
                    plan_evidence_id, plan_as_of,
                    target_dataset_version,
                    challenger_model_id, plan_json, notes
                ) VALUES (
                    ?, ?, ?, 'PLANNED', 'ACTIVE',
                    ?, ?, ?, ?, ?, ?, NULL, ?, ?
                )
                """,
                (
                    selected_cycle_id,
                    now,
                    now,
                    plan.champion_model_id,
                    plan.champion_dataset_version,
                    plan.champion_evidence_watermark,
                    plan_evidence_id,
                    cutoff,
                    target_dataset_version.strip(),
                    plan_json,
                    notes,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "retraining cycle conflicts with existing active or named cycle"
            ) from exc

    return retraining_cycle(
        storage,
        cycle_id=selected_cycle_id,
    )


def attach_retraining_challenger(
    storage: Storage,
    *,
    cycle_id: str,
    model_id: str,
) -> RetrainingCycle:
    if not model_id.strip():
        raise ValueError("model_id is required")
    now = utc_now_iso()

    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT status, active_key, champion_model_id,
                   target_dataset_version, plan_as_of,
                   challenger_model_id, created_at
            FROM continuous_learning_cycles
            WHERE cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown retraining cycle: {cycle_id}")
        (
            cycle_status,
            active_key,
            champion_model_id,
            target_dataset_version,
            plan_as_of,
            existing_challenger,
            cycle_created_at,
        ) = row

        if existing_challenger is not None:
            if str(existing_challenger) == model_id:
                return retraining_cycle(
                    storage,
                    cycle_id=cycle_id,
                )
            raise ValueError(
                "retraining cycle already has a different challenger"
            )
        if str(cycle_status) != "PLANNED" or active_key != ACTIVE_KEY:
            raise ValueError(
                "challenger can only attach to an active PLANNED cycle"
            )

        champion = conn.execute(
            """
            SELECT model_id
            FROM model_registry
            WHERE status = 'CHAMPION'
            LIMIT 1
            """
        ).fetchone()
        if champion is None or str(champion[0]) != str(champion_model_id):
            raise ValueError(
                "champion changed before challenger attachment"
            )

        model = conn.execute(
            """
            SELECT created_at, dataset_version, status,
                   train_start, train_end,
                   validation_start, validation_end
            FROM model_registry
            WHERE model_id = ?
            """,
            (model_id,),
        ).fetchone()
        if model is None:
            raise ValueError(f"unknown model_id: {model_id}")
        if str(model[2]) != OFFLINE_CANDIDATE:
            raise ValueError(
                "retraining challenger must be OFFLINE_CANDIDATE"
            )
        if str(model[1]) != str(target_dataset_version):
            raise ValueError(
                "challenger dataset_version does not match retraining cycle"
            )
        if _time(str(model[0])) < _time(str(cycle_created_at)):
            raise ValueError(
                "challenger was registered before retraining cycle started"
            )
        if any(value is None for value in model[3:7]):
            raise ValueError(
                "challenger is missing training/validation window metadata"
            )
        if _time(str(model[6])) > _time(str(plan_as_of)):
            raise ValueError(
                "challenger validation window extends beyond cycle cutoff"
            )

        link = {
            "cycle_id": cycle_id,
            "model_id": model_id,
            "champion_model_id": str(champion_model_id),
            "target_dataset_version": str(target_dataset_version),
            "plan_as_of": str(plan_as_of),
            "train_start": str(model[3]),
            "train_end": str(model[4]),
            "validation_start": str(model[5]),
            "validation_end": str(model[6]),
        }
        conn.execute(
            """
            UPDATE continuous_learning_cycles
            SET status = 'CHALLENGER_REGISTERED',
                challenger_model_id = ?,
                updated_at = ?
            WHERE cycle_id = ?
              AND status = 'PLANNED'
              AND active_key = 'ACTIVE'
              AND challenger_model_id IS NULL
            """,
            (model_id, now, cycle_id),
        )
        conn.execute(
            """
            INSERT INTO model_live_evidence(
                model_id, created_at, evidence_type,
                status, evidence_json
            ) VALUES (?, ?, ?, 'CHALLENGER_REGISTERED', ?)
            """,
            (
                model_id,
                now,
                CYCLE_LINK_EVIDENCE_TYPE,
                json.dumps(link, separators=(",", ":")),
            ),
        )

    return retraining_cycle(storage, cycle_id=cycle_id)
