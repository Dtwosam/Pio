from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .continuous_promotion import CONTINUOUS_PROMOTION_EVIDENCE_TYPE
from .live_champion_monitor import (
    LiveChampionCriteria,
    LiveChampionReport,
    evaluate_live_champion,
)
from .phase_promotion import PHASE7, PHASE7_EVIDENCE_TYPE
from .storage import Storage


@dataclass(frozen=True)
class Phase8PromotionCriteria:
    min_completed_cycles: int = 1
    min_live_labels: int = 10
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
