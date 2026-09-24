from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .storage import Storage


MODEL_HISTORY_TRIGGERS = (
    "phase8_model_status_history_no_update",
    "phase8_model_status_history_no_delete",
    "phase8_model_status_history_on_insert",
    "phase8_model_status_history_on_status_update",
)

CYCLE_HISTORY_TRIGGERS = (
    "phase8_cycle_status_history_no_update",
    "phase8_cycle_status_history_no_delete",
    "phase8_cycle_status_history_on_insert",
    "phase8_cycle_status_history_on_transition",
)


@dataclass(frozen=True)
class Phase8ModelStatusTransition:
    event_id: int
    model_id: str
    changed_at: str
    old_status: str | None
    new_status: str
    model_family: str
    feature_version: str
    dataset_version: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase8CycleStatusTransition:
    event_id: int
    cycle_id: str
    changed_at: str
    old_status: str | None
    new_status: str
    champion_model_id: str
    target_dataset_version: str
    old_challenger_model_id: str | None
    new_challenger_model_id: str | None
    old_active_key: str | None
    new_active_key: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase8TransitionHistoryAudit:
    journal_ready: bool
    started_at: str | None
    model_history_rows: int
    cycle_history_rows: int
    model_registry_rows: int
    covered_model_rows: int
    cycle_rows: int
    covered_cycle_rows: int
    required_triggers: tuple[str, ...]
    present_triggers: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _limit(value: int) -> int:
    if value < 1 or value > 500:
        raise ValueError("limit must be between 1 and 500")
    return value


def list_phase8_model_status_history(
    storage: Storage,
    *,
    model_id: str | None = None,
    limit: int = 50,
) -> tuple[Phase8ModelStatusTransition, ...]:
    limit = _limit(limit)
    query = """
        SELECT
            id,
            model_id,
            changed_at,
            old_status,
            new_status,
            model_family,
            feature_version,
            dataset_version
        FROM phase8_model_status_history
    """
    params: list[object] = []
    if model_id is not None:
        normalized = model_id.strip()
        if not normalized:
            raise ValueError("model_id cannot be blank")
        query += " WHERE model_id = ?"
        params.append(normalized)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with storage.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    return tuple(
        Phase8ModelStatusTransition(
            event_id=int(row[0]),
            model_id=str(row[1]),
            changed_at=str(row[2]),
            old_status=(str(row[3]) if row[3] is not None else None),
            new_status=str(row[4]),
            model_family=str(row[5]),
            feature_version=str(row[6]),
            dataset_version=str(row[7]),
        )
        for row in rows
    )


def list_phase8_cycle_status_history(
    storage: Storage,
    *,
    cycle_id: str | None = None,
    limit: int = 50,
) -> tuple[Phase8CycleStatusTransition, ...]:
    limit = _limit(limit)
    query = """
        SELECT
            id,
            cycle_id,
            changed_at,
            old_status,
            new_status,
            champion_model_id,
            target_dataset_version,
            old_challenger_model_id,
            new_challenger_model_id,
            old_active_key,
            new_active_key
        FROM phase8_cycle_status_history
    """
    params: list[object] = []
    if cycle_id is not None:
        normalized = cycle_id.strip()
        if not normalized:
            raise ValueError("cycle_id cannot be blank")
        query += " WHERE cycle_id = ?"
        params.append(normalized)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with storage.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    return tuple(
        Phase8CycleStatusTransition(
            event_id=int(row[0]),
            cycle_id=str(row[1]),
            changed_at=str(row[2]),
            old_status=(str(row[3]) if row[3] is not None else None),
            new_status=str(row[4]),
            champion_model_id=str(row[5]),
            target_dataset_version=str(row[6]),
            old_challenger_model_id=(
                str(row[7]) if row[7] is not None else None
            ),
            new_challenger_model_id=(
                str(row[8]) if row[8] is not None else None
            ),
            old_active_key=(
                str(row[9]) if row[9] is not None else None
            ),
            new_active_key=(
                str(row[10]) if row[10] is not None else None
            ),
        )
        for row in rows
    )


def audit_phase8_transition_history(
    storage: Storage,
) -> Phase8TransitionHistoryAudit:
    required = MODEL_HISTORY_TRIGGERS + CYCLE_HISTORY_TRIGGERS
    with storage.connect() as conn:
        meta = conn.execute(
            """
            SELECT started_at
            FROM phase8_transition_history_meta
            WHERE singleton = 1
            """
        ).fetchone()
        model_history_rows = int(
            conn.execute(
                "SELECT COUNT(*) FROM phase8_model_status_history"
            ).fetchone()[0]
        )
        cycle_history_rows = int(
            conn.execute(
                "SELECT COUNT(*) FROM phase8_cycle_status_history"
            ).fetchone()[0]
        )
        model_registry_rows = int(
            conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
        )
        covered_model_rows = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM model_registry AS model
                WHERE EXISTS (
                    SELECT 1
                    FROM phase8_model_status_history AS history
                    WHERE history.model_id = model.model_id
                )
                """
            ).fetchone()[0]
        )
        cycle_rows = int(
            conn.execute(
                "SELECT COUNT(*) FROM continuous_learning_cycles"
            ).fetchone()[0]
        )
        covered_cycle_rows = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM continuous_learning_cycles AS cycle
                WHERE EXISTS (
                    SELECT 1
                    FROM phase8_cycle_status_history AS history
                    WHERE history.cycle_id = cycle.cycle_id
                )
                """
            ).fetchone()[0]
        )
        trigger_rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name IN (
                ?, ?, ?, ?, ?, ?, ?, ?
              )
            ORDER BY name ASC
            """,
            required,
        ).fetchall()

    present = tuple(str(row[0]) for row in trigger_rows)
    reasons: list[str] = []
    if meta is None:
        reasons.append("Phase 8 transition-history start watermark is missing")
    missing_triggers = tuple(
        name for name in required if name not in present
    )
    if missing_triggers:
        reasons.append(
            "Phase 8 transition-history triggers are missing: "
            + ", ".join(missing_triggers)
        )
    if covered_model_rows != model_registry_rows:
        reasons.append(
            "not every current model registry row has a transition-history baseline"
        )
    if covered_cycle_rows != cycle_rows:
        reasons.append(
            "not every current retraining cycle has a transition-history baseline"
        )

    return Phase8TransitionHistoryAudit(
        journal_ready=not reasons,
        started_at=(str(meta[0]) if meta is not None else None),
        model_history_rows=model_history_rows,
        cycle_history_rows=cycle_history_rows,
        model_registry_rows=model_registry_rows,
        covered_model_rows=covered_model_rows,
        cycle_rows=cycle_rows,
        covered_cycle_rows=covered_cycle_rows,
        required_triggers=required,
        present_triggers=present,
        reasons=tuple(reasons),
    )
