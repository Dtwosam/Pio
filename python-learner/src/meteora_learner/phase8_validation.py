from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .continuous_promotion import CONTINUOUS_PROMOTION_EVIDENCE_TYPE
from .live_champion_monitor import (
    LiveChampionCriteria,
    LiveChampionReport,
    evaluate_live_champion,
)
from .phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
)
from .storage import Storage


@dataclass(frozen=True)
class Phase8PromotionCriteria:
    min_completed_cycles: int = 1
    min_live_labels: int = 10
    min_live_pools: int = 2
    max_realized_drawdown_bps: int = 2_000
    max_single_loss_bps: int = 1_500
    min_win_rate: float = 0.30
    min_mean_return_bps: float = -100.0
    max_mean_abs_prediction_error_bps: float = 1_500.0

    def validate(self) -> None:
        if self.min_completed_cycles < 1:
            raise ValueError("min_completed_cycles must be positive")
        LiveChampionCriteria(
            min_live_labels=self.min_live_labels,
            min_live_pools=self.min_live_pools,
            max_realized_drawdown_bps=self.max_realized_drawdown_bps,
            max_single_loss_bps=self.max_single_loss_bps,
            min_win_rate=self.min_win_rate,
            min_mean_return_bps=self.min_mean_return_bps,
            max_mean_abs_prediction_error_bps=(
                self.max_mean_abs_prediction_error_bps
            ),
        )

    def live_criteria(self) -> LiveChampionCriteria:
        self.validate()
        return LiveChampionCriteria(
            min_live_labels=self.min_live_labels,
            min_live_pools=self.min_live_pools,
            max_realized_drawdown_bps=self.max_realized_drawdown_bps,
            max_single_loss_bps=self.max_single_loss_bps,
            min_win_rate=self.min_win_rate,
            min_mean_return_bps=self.min_mean_return_bps,
            max_mean_abs_prediction_error_bps=(
                self.max_mean_abs_prediction_error_bps
            ),
        )


@dataclass(frozen=True)
class Phase8PromotionReport:
    phase7_promoted: bool
    champion_model_id: str | None
    completed_cycles: int
    champion_cycle_id: str | None
    continuous_promotion_evidence_id: int | None
    live_champion: LiveChampionReport | None
    criteria: Phase8PromotionCriteria
    promotion_ready: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase8PersistedPromotionAudit:
    exists: bool
    history_exists: bool
    qualified: bool
    evidence_type_valid: bool
    current_row_matches_latest_history: bool
    current_promotion_ready: bool
    champion_lineage_matches: bool
    current: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

def evaluate_phase8_promotion(
    storage: Storage,
    *,
    criteria: Phase8PromotionCriteria = Phase8PromotionCriteria(),
) -> Phase8PromotionReport:
    criteria.validate()
    phase7_promoted = storage.phase_is_promoted(
        PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
    )
    champion = storage.current_model_champion()
    champion_model_id = (
        str(champion["model_id"])
        if champion is not None
        else None
    )

    completed_cycles = 0
    champion_cycle_id = None
    promotion_evidence_id = None
    live_report = None

    if champion_model_id is not None:
        with storage.connect() as conn:
            cycle_rows = conn.execute(
                """
                SELECT cycle_id, challenger_model_id
                FROM continuous_learning_cycles
                WHERE status = 'COMPLETED'
                ORDER BY updated_at ASC, cycle_id ASC
                """
            ).fetchall()
        completed_cycles = len(cycle_rows)
        matching_cycles = [
            str(row[0])
            for row in cycle_rows
            if row[1] is not None
            and str(row[1]) == champion_model_id
        ]
        if matching_cycles:
            champion_cycle_id = matching_cycles[-1]

        evidence = storage.latest_model_live_evidence(
            champion_model_id,
            evidence_type=CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
        )
        if evidence is not None:
            payload = evidence["evidence"]
            if (
                evidence["status"] == "CHAMPION"
                and payload.get("cycle_id") == champion_cycle_id
                and payload.get("validation", {}).get("qualified") is True
            ):
                promotion_evidence_id = int(evidence["id"])

        live_report = evaluate_live_champion(
            storage,
            model_id=champion_model_id,
            criteria=criteria.live_criteria(),
        )

    reasons: list[str] = []
    if not phase7_promoted:
        reasons.append(
            "Phase 7 must be persistently promoted before Phase 8"
        )
    if champion_model_id is None:
        reasons.append("Phase 8 requires a current champion")
    if completed_cycles < criteria.min_completed_cycles:
        reasons.append(
            f"completed continuous retraining cycles {completed_cycles} "
            f"are below {criteria.min_completed_cycles}"
        )
    if champion_model_id is not None and champion_cycle_id is None:
        reasons.append(
            "current champion was not produced by a completed continuous cycle"
        )
    if champion_model_id is not None and promotion_evidence_id is None:
        reasons.append(
            "current champion lacks matching continuous-promotion evidence"
        )
    if live_report is None:
        reasons.append("live champion health evidence is unavailable")
    elif live_report.status != "HEALTHY":
        reasons.append(
            f"live champion status {live_report.status} is not HEALTHY"
        )

    return Phase8PromotionReport(
        phase7_promoted=phase7_promoted,
        champion_model_id=champion_model_id,
        completed_cycles=completed_cycles,
        champion_cycle_id=champion_cycle_id,
        continuous_promotion_evidence_id=promotion_evidence_id,
        live_champion=live_report,
        criteria=criteria,
        promotion_ready=not reasons,
        reasons=tuple(reasons),
    )


def audit_persisted_phase8_promotion(
    storage: Storage,
) -> Phase8PersistedPromotionAudit:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT promoted_at, evidence_type, qualified, evidence_json
            FROM phase_promotion_evidence
            WHERE phase_name = ?
            LIMIT 1
            """,
            (PHASE8,),
        ).fetchone()
        history_row = conn.execute(
            """
            SELECT promoted_at, evidence_type, qualified, evidence_json
            FROM phase_promotion_evidence_history
            WHERE phase_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (PHASE8,),
        ).fetchone()

    if row is None:
        return Phase8PersistedPromotionAudit(
            exists=False,
            history_exists=history_row is not None,
            qualified=False,
            evidence_type_valid=False,
            current_row_matches_latest_history=False,
            current_promotion_ready=False,
            champion_lineage_matches=False,
            current=False,
            reasons=("persisted Phase 8 promotion evidence is missing",),
        )

    reasons: list[str] = []
    history_exists = history_row is not None
    current_row_matches_latest_history = (
        history_row is not None and tuple(row) == tuple(history_row)
    )
    evidence_type_valid = str(row[1]) == PHASE8_EVIDENCE_TYPE
    qualified = bool(row[2])

    try:
        persisted = json.loads(str(row[3]))
    except (TypeError, ValueError, json.JSONDecodeError):
        persisted = None

    criteria = None
    if isinstance(persisted, dict):
        criteria_raw = persisted.get("criteria")
        if isinstance(criteria_raw, dict):
            try:
                criteria = Phase8PromotionCriteria(**criteria_raw)
            except (TypeError, ValueError):
                criteria = None

    current_report = (
        evaluate_phase8_promotion(storage, criteria=criteria)
        if criteria is not None
        else None
    )
    current_promotion_ready = bool(
        current_report is not None and current_report.promotion_ready
    )

    champion_lineage_matches = False
    if isinstance(persisted, dict) and current_report is not None:
        champion_lineage_matches = (
            persisted.get("champion_model_id")
            == current_report.champion_model_id
            and persisted.get("champion_cycle_id")
            == current_report.champion_cycle_id
            and persisted.get("continuous_promotion_evidence_id")
            == current_report.continuous_promotion_evidence_id
        )

    if not history_exists:
        reasons.append("immutable Phase 8 promotion history is missing")
    elif not current_row_matches_latest_history:
        reasons.append(
            "current Phase 8 promotion row differs from immutable history"
        )
    if not evidence_type_valid:
        reasons.append("persisted Phase 8 promotion evidence type is invalid")
    if not qualified:
        reasons.append("persisted Phase 8 promotion evidence is not qualified")
    if criteria is None:
        reasons.append("persisted Phase 8 promotion criteria are invalid")
    if not current_promotion_ready:
        reasons.append("current Phase 8 promotion gate no longer passes")
    if not champion_lineage_matches:
        reasons.append(
            "current Phase 8 champion lineage differs from persisted promotion"
        )

    return Phase8PersistedPromotionAudit(
        exists=True,
        history_exists=history_exists,
        qualified=qualified,
        evidence_type_valid=evidence_type_valid,
        current_row_matches_latest_history=current_row_matches_latest_history,
        current_promotion_ready=current_promotion_ready,
        champion_lineage_matches=champion_lineage_matches,
        current=not reasons,
        reasons=tuple(reasons),
    )
