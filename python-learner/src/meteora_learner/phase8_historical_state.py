from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .continuous_learning import ACTIVE_CHALLENGER_STATUSES
from .phase8_transition_history import audit_phase8_transition_history
from .phase_promotion import PHASE7, PHASE7_EVIDENCE_TYPE
from .storage import Storage


@dataclass(frozen=True)
class Phase8HistoricalModelState:
    model_id: str
    changed_at: str
    status: str
    model_family: str
    feature_version: str
    dataset_version: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase8HistoricalCycleState:
    cycle_id: str
    changed_at: str
    status: str
    champion_model_id: str
    target_dataset_version: str
    challenger_model_id: str | None
    active_key: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase8HistoricalStateSnapshot:
    as_of: str
    journal_started_at: str
    consistent: bool
    phase7_promoted: bool
    phase7_promoted_at: str | None
    champion_model_id: str | None
    active_challenger_model_ids: tuple[str, ...]
    active_cycle_id: str | None
    completed_cycle_ids: tuple[str, ...]
    models: tuple[Phase8HistoricalModelState, ...]
    cycles: tuple[Phase8HistoricalCycleState, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Phase 8 historical cutoff must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def build_phase8_historical_state_snapshot(
    storage: Storage,
    *,
    as_of: str,
) -> Phase8HistoricalStateSnapshot:
    cutoff = _time(as_of)
    audit = audit_phase8_transition_history(storage)
    if not audit.journal_ready or audit.started_at is None:
        detail = (
            "; ".join(audit.reasons)
            if audit.reasons
            else "transition journal is not ready"
        )
        raise ValueError(
            "Phase 8 historical state requires a ready transition journal: "
            + detail
        )

    started = _time(audit.started_at)
    if cutoff < started:
        raise ValueError(
            "Phase 8 historical cutoff predates transition journal start "
            f"{audit.started_at}"
        )
    cutoff_text = _text(cutoff)

    with storage.connect() as conn:
        model_rows = conn.execute(
            """
            SELECT
                history.model_id,
                history.changed_at,
                history.new_status,
                history.model_family,
                history.feature_version,
                history.dataset_version
            FROM phase8_model_status_history AS history
            WHERE julianday(history.changed_at) <= julianday(?)
              AND history.id = (
                  SELECT candidate.id
                  FROM phase8_model_status_history AS candidate
                  WHERE candidate.model_id = history.model_id
                    AND julianday(candidate.changed_at) <= julianday(?)
                  ORDER BY julianday(candidate.changed_at) DESC,
                           candidate.id DESC
                  LIMIT 1
              )
            ORDER BY history.model_id ASC
            """,
            (cutoff_text, cutoff_text),
        ).fetchall()
        cycle_rows = conn.execute(
            """
            SELECT
                history.cycle_id,
                history.changed_at,
                history.new_status,
                history.champion_model_id,
                history.target_dataset_version,
                history.new_challenger_model_id,
                history.new_active_key
            FROM phase8_cycle_status_history AS history
            WHERE julianday(history.changed_at) <= julianday(?)
              AND history.id = (
                  SELECT candidate.id
                  FROM phase8_cycle_status_history AS candidate
                  WHERE candidate.cycle_id = history.cycle_id
                    AND julianday(candidate.changed_at) <= julianday(?)
                  ORDER BY julianday(candidate.changed_at) DESC,
                           candidate.id DESC
                  LIMIT 1
              )
            ORDER BY history.cycle_id ASC
            """,
            (cutoff_text, cutoff_text),
        ).fetchall()
        phase7 = conn.execute(
            """
            SELECT promoted_at, evidence_type, qualified
            FROM phase_promotion_evidence_history
            WHERE phase_name = ?
              AND julianday(promoted_at) <= julianday(?)
            ORDER BY julianday(promoted_at) DESC, id DESC
            LIMIT 1
            """,
            (PHASE7, cutoff_text),
        ).fetchone()

    models = tuple(
        Phase8HistoricalModelState(
            model_id=str(row[0]),
            changed_at=str(row[1]),
            status=str(row[2]),
            model_family=str(row[3]),
            feature_version=str(row[4]),
            dataset_version=str(row[5]),
        )
        for row in model_rows
    )
    cycles = tuple(
        Phase8HistoricalCycleState(
            cycle_id=str(row[0]),
            changed_at=str(row[1]),
            status=str(row[2]),
            champion_model_id=str(row[3]),
            target_dataset_version=str(row[4]),
            challenger_model_id=(
                str(row[5]) if row[5] is not None else None
            ),
            active_key=(str(row[6]) if row[6] is not None else None),
        )
        for row in cycle_rows
    )

    champion_models = tuple(
        item.model_id for item in models if item.status == "CHAMPION"
    )
    active_challengers = tuple(
        item.model_id
        for item in models
        if item.status in ACTIVE_CHALLENGER_STATUSES
    )
    active_cycles = tuple(
        item.cycle_id
        for item in cycles
        if item.active_key == "ACTIVE"
    )
    completed_cycles = tuple(
        item.cycle_id for item in cycles if item.status == "COMPLETED"
    )

    phase7_promoted = bool(
        phase7 is not None
        and str(phase7[1]) == PHASE7_EVIDENCE_TYPE
        and bool(phase7[2])
    )
    phase7_promoted_at = (
        str(phase7[0]) if phase7_promoted and phase7 is not None else None
    )

    reasons: list[str] = []
    if len(champion_models) > 1:
        reasons.append(
            "historical model journal contains multiple CHAMPION states "
            f"at cutoff: {', '.join(champion_models)}"
        )
    if len(active_cycles) > 1:
        reasons.append(
            "historical cycle journal contains multiple ACTIVE cycles "
            f"at cutoff: {', '.join(active_cycles)}"
        )

    return Phase8HistoricalStateSnapshot(
        as_of=cutoff_text,
        journal_started_at=audit.started_at,
        consistent=not reasons,
        phase7_promoted=phase7_promoted,
        phase7_promoted_at=phase7_promoted_at,
        champion_model_id=(
            champion_models[0] if len(champion_models) == 1 else None
        ),
        active_challenger_model_ids=active_challengers,
        active_cycle_id=(
            active_cycles[0] if len(active_cycles) == 1 else None
        ),
        completed_cycle_ids=completed_cycles,
        models=models,
        cycles=cycles,
        reasons=tuple(reasons),
    )
