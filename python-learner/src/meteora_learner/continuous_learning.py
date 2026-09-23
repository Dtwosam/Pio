from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .phase_promotion import PHASE7, PHASE7_EVIDENCE_TYPE
from .storage import Storage


ACTIVE_CHALLENGER_STATUSES = (
    "OFFLINE_CANDIDATE",
    "OFFLINE_QUALIFIED",
    "PAPER_CHALLENGER",
)
RETRAIN_PLAN_EVIDENCE_TYPE = "CONTINUOUS_RETRAIN_PLAN_V1"


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("continuous-learning timestamps require timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ContinuousLearningCriteria:
    min_new_chain_observations: int = 500
    min_new_chain_pools: int = 3
    min_new_live_labels: int = 5
    max_champion_age_days: float = 14.0

    def __post_init__(self) -> None:
        if self.min_new_chain_observations < 1:
            raise ValueError(
                "min_new_chain_observations must be positive"
            )
        if self.min_new_chain_pools < 1:
            raise ValueError("min_new_chain_pools must be positive")
        if self.min_new_live_labels < 0:
            raise ValueError("min_new_live_labels cannot be negative")
        if self.max_champion_age_days <= 0:
            raise ValueError("max_champion_age_days must be positive")


@dataclass(frozen=True)
class ContinuousLearningPlan:
    phase7_promoted: bool
    champion_model_id: str | None
    champion_dataset_version: str | None
    champion_evidence_watermark: str | None
    champion_age_days: float | None
    active_challenger_model_ids: tuple[str, ...]
    new_chain_observations: int
    new_chain_pools: int
    new_live_labels: int
    chain_evidence_ready: bool
    live_label_trigger: bool
    age_trigger: bool
    retrain_due: bool
    status: str
    criteria: ContinuousLearningCriteria
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_continuous_learning_plan(
    storage: Storage,
    *,
    criteria: ContinuousLearningCriteria = ContinuousLearningCriteria(),
    as_of: str | None = None,
) -> ContinuousLearningPlan:
    now = _time(as_of) if as_of is not None else datetime.now(timezone.utc)
    phase7_promoted = storage.phase_is_promoted(
        PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
    )

    with storage.connect() as conn:
        champion = conn.execute(
            """
            SELECT model_id, dataset_version, updated_at,
                   validation_end, train_end
            FROM model_registry
            WHERE status = 'CHAMPION'
            LIMIT 1
            """
        ).fetchone()
        active_rows = conn.execute(
            """
            SELECT model_id
            FROM model_registry
            WHERE status IN (
                'OFFLINE_CANDIDATE',
                'OFFLINE_QUALIFIED',
                'PAPER_CHALLENGER'
            )
            ORDER BY created_at ASC, model_id ASC
            """
        ).fetchall()

        if champion is None:
            watermark = None
            chain_count = 0
            chain_pools = 0
            live_labels = 0
            champion_age_days = None
            champion_model_id = None
            dataset_version = None
        else:
            champion_model_id = str(champion[0])
            dataset_version = str(champion[1])
            champion_updated_at = str(champion[2])
            watermark = str(
                champion[3]
                if champion[3] is not None
                else (
                    champion[4]
                    if champion[4] is not None
                    else champion_updated_at
                )
            )
            row = conn.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT pool_address)
                FROM chain_pool_snapshots
                WHERE observed_at > ?
                  AND julianday(observed_at) <= julianday(?)
                """,
                (watermark, now.isoformat()),
            ).fetchone()
            chain_count = int(row[0] or 0)
            chain_pools = int(row[1] or 0)
            live_labels = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM live_learning_labels
                    WHERE model_version = ?
                      AND created_at > ?
                      AND julianday(created_at) <= julianday(?)
                    """,
                    (
                        champion_model_id,
                        champion_updated_at,
                        now.isoformat(),
                    ),
                ).fetchone()[0]
            )
            champion_age_days = max(
                0.0,
                (now - _time(champion_updated_at)).total_seconds()
                / 86400.0,
            )

    active_challengers = tuple(str(row[0]) for row in active_rows)
    chain_ready = (
        chain_count >= criteria.min_new_chain_observations
        and chain_pools >= criteria.min_new_chain_pools
    )
    live_trigger = live_labels >= criteria.min_new_live_labels
    age_trigger = (
        champion_age_days is not None
        and champion_age_days >= criteria.max_champion_age_days
    )

    reasons: list[str] = []
    if not phase7_promoted:
        status = "BLOCKED_PHASE7"
        reasons.append(
            "Phase 7 must be persistently promoted before continuous retraining"
        )
    elif champion is None:
        status = "NO_CHAMPION"
        reasons.append(
            "continuous retraining requires an existing CHAMPION; "
            "use the staged initial challenger workflow first"
        )
    elif active_challengers:
        status = "CHALLENGER_IN_PROGRESS"
        reasons.append(
            "an offline/paper challenger cycle is already active"
        )
    elif not chain_ready:
        status = "WAITING_CHAIN_EVIDENCE"
        reasons.append(
            f"new chain observations/pools {chain_count}/{chain_pools} "
            f"are below {criteria.min_new_chain_observations}/"
            f"{criteria.min_new_chain_pools}"
        )
    elif not (live_trigger or age_trigger):
        status = "WAITING_TRIGGER"
        reasons.append(
            f"new live labels {live_labels} are below "
            f"{criteria.min_new_live_labels} and champion age "
            f"{champion_age_days:.2f}d is below "
            f"{criteria.max_champion_age_days:.2f}d"
        )
    else:
        status = "RETRAIN_DUE"

    return ContinuousLearningPlan(
        phase7_promoted=phase7_promoted,
        champion_model_id=champion_model_id,
        champion_dataset_version=dataset_version,
        champion_evidence_watermark=watermark,
        champion_age_days=champion_age_days,
        active_challenger_model_ids=active_challengers,
        new_chain_observations=chain_count,
        new_chain_pools=chain_pools,
        new_live_labels=live_labels,
        chain_evidence_ready=chain_ready,
        live_label_trigger=live_trigger,
        age_trigger=age_trigger,
        retrain_due=status == "RETRAIN_DUE",
        status=status,
        criteria=criteria,
        reasons=tuple(reasons),
    )


def persist_continuous_learning_plan(
    storage: Storage,
    *,
    plan: ContinuousLearningPlan,
) -> int:
    if plan.champion_model_id is None:
        raise ValueError(
            "cannot persist continuous retrain plan without a champion"
        )
    return storage.save_model_live_evidence(
        model_id=plan.champion_model_id,
        evidence_type=RETRAIN_PLAN_EVIDENCE_TYPE,
        status=plan.status,
        evidence=plan.to_record(),
    )
