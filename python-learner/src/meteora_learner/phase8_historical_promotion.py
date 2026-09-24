from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
import json
from typing import Any

from .continuous_promotion import CONTINUOUS_PROMOTION_EVIDENCE_TYPE
from .live_champion_monitor import LiveChampionReport, _max_drawdown_bps
from .phase8_historical_state import (
    Phase8HistoricalStateSnapshot,
    build_phase8_historical_state_snapshot,
)
from .phase8_validation import Phase8PromotionCriteria
from .storage import Storage


@dataclass(frozen=True)
class Phase8HistoricalPromotionReport:
    as_of: str
    journal_started_at: str
    state_consistent: bool
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


def _champion_cycle_id(
    snapshot: Phase8HistoricalStateSnapshot,
) -> str | None:
    champion = snapshot.champion_model_id
    if champion is None:
        return None

    matches = [
        cycle
        for cycle in snapshot.cycles
        if (
            cycle.status == "COMPLETED"
            and cycle.challenger_model_id == champion
        )
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: (item.changed_at, item.cycle_id))
    return matches[-1].cycle_id


def _historical_promotion_evidence_id(
    storage: Storage,
    *,
    model_id: str,
    cycle_id: str | None,
    as_of: str,
) -> int | None:
    if cycle_id is None:
        return None

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT id, status, evidence_json
            FROM model_live_evidence
            WHERE model_id = ?
              AND evidence_type = ?
              AND julianday(created_at) <= julianday(?)
            ORDER BY julianday(created_at) DESC, id DESC
            LIMIT 1
            """,
            (
                model_id,
                CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
                as_of,
            ),
        ).fetchone()

    if row is None:
        return None
    try:
        payload = json.loads(str(row[2]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        str(row[1]) != "CHAMPION"
        or payload.get("cycle_id") != cycle_id
        or payload.get("validation", {}).get("qualified") is not True
    ):
        return None
    return int(row[0])


def _historical_live_champion(
    storage: Storage,
    *,
    snapshot: Phase8HistoricalStateSnapshot,
    model_id: str,
    criteria,
) -> LiveChampionReport:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                pool_address,
                realized_return_bps,
                prediction_error_bps,
                target_positive_return
            FROM live_learning_labels
            WHERE model_version = ?
              AND julianday(created_at) <= julianday(?)
            ORDER BY julianday(created_at) ASC, position_address ASC
            """,
            (model_id, snapshot.as_of),
        ).fetchall()

    returns = [int(row[1]) for row in rows]
    errors = [int(row[2]) for row in rows]
    label_count = len(rows)
    distinct_pools = len({str(row[0]) for row in rows})
    mean_return = float(fmean(returns)) if returns else None
    win_rate = (
        sum(int(row[3]) == 1 for row in rows) / label_count
        if label_count
        else None
    )
    max_single_loss = (
        max((max(0, -value) for value in returns), default=0)
        if returns
        else None
    )
    max_drawdown = _max_drawdown_bps(returns) if returns else None
    mean_error = float(fmean(errors)) if errors else None
    mean_abs_error = (
        float(fmean(abs(value) for value in errors))
        if errors
        else None
    )

    reasons: list[str] = []
    rollback_recommended = False
    if not snapshot.phase7_promoted:
        status = "BLOCKED_PHASE7"
        reasons.append(
            "Phase 7 was not persistently promoted at the historical cutoff"
        )
    elif label_count < criteria.min_live_labels:
        status = "INSUFFICIENT_EVIDENCE"
        reasons.append(
            f"live labels {label_count} are below {criteria.min_live_labels}"
        )
    elif distinct_pools < criteria.min_live_pools:
        status = "INSUFFICIENT_EVIDENCE"
        reasons.append(
            f"distinct live pools {distinct_pools} are below "
            f"{criteria.min_live_pools}"
        )
    else:
        checks = (
            (
                max_drawdown is not None
                and max_drawdown <= criteria.max_realized_drawdown_bps,
                f"realized max drawdown {max_drawdown} bps exceeds "
                f"{criteria.max_realized_drawdown_bps} bps",
            ),
            (
                max_single_loss is not None
                and max_single_loss <= criteria.max_single_loss_bps,
                f"max single loss {max_single_loss} bps exceeds "
                f"{criteria.max_single_loss_bps} bps",
            ),
            (
                win_rate is not None and win_rate >= criteria.min_win_rate,
                f"live win rate {win_rate:.6f} is below "
                f"{criteria.min_win_rate:.6f}",
            ),
            (
                mean_return is not None
                and mean_return >= criteria.min_mean_return_bps,
                f"mean live return {mean_return:.6f} bps is below "
                f"{criteria.min_mean_return_bps:.6f} bps",
            ),
            (
                mean_abs_error is not None
                and mean_abs_error
                <= criteria.max_mean_abs_prediction_error_bps,
                f"mean absolute prediction error "
                f"{mean_abs_error:.6f} bps exceeds "
                f"{criteria.max_mean_abs_prediction_error_bps:.6f} bps",
            ),
        )
        reasons.extend(
            message for passed, message in checks if not passed
        )
        if reasons:
            status = "BREACH"
            rollback_recommended = True
        else:
            status = "HEALTHY"

    return LiveChampionReport(
        model_id=model_id,
        model_status="CHAMPION",
        phase7_promoted=snapshot.phase7_promoted,
        label_count=label_count,
        distinct_pools=distinct_pools,
        mean_return_bps=mean_return,
        win_rate=win_rate,
        max_single_loss_bps=max_single_loss,
        realized_max_drawdown_bps=max_drawdown,
        mean_prediction_error_bps=mean_error,
        mean_abs_prediction_error_bps=mean_abs_error,
        criteria=criteria,
        status=status,
        rollback_recommended=rollback_recommended,
        reasons=tuple(reasons),
    )


def evaluate_phase8_historical_promotion(
    storage: Storage,
    *,
    as_of: str,
    criteria: Phase8PromotionCriteria = Phase8PromotionCriteria(),
) -> Phase8HistoricalPromotionReport:
    criteria.validate()
    snapshot = build_phase8_historical_state_snapshot(
        storage,
        as_of=as_of,
    )
    champion_model_id = snapshot.champion_model_id
    completed_cycles = len(snapshot.completed_cycle_ids)
    champion_cycle_id = _champion_cycle_id(snapshot)
    promotion_evidence_id = None
    live_report = None

    if champion_model_id is not None:
        promotion_evidence_id = _historical_promotion_evidence_id(
            storage,
            model_id=champion_model_id,
            cycle_id=champion_cycle_id,
            as_of=snapshot.as_of,
        )
        live_report = _historical_live_champion(
            storage,
            snapshot=snapshot,
            model_id=champion_model_id,
            criteria=criteria.live_criteria(),
        )

    reasons: list[str] = list(snapshot.reasons)
    if not snapshot.consistent:
        reasons.append(
            "historical Phase 8 model/cycle state is internally inconsistent"
        )
    if not snapshot.phase7_promoted:
        reasons.append(
            "Phase 7 was not persistently promoted at the historical cutoff"
        )
    if champion_model_id is None:
        reasons.append(
            "Phase 8 requires exactly one historical CHAMPION at the cutoff"
        )
    if completed_cycles < criteria.min_completed_cycles:
        reasons.append(
            f"completed continuous retraining cycles {completed_cycles} "
            f"are below {criteria.min_completed_cycles}"
        )
    if champion_model_id is not None and champion_cycle_id is None:
        reasons.append(
            "historical champion was not produced by a completed "
            "continuous cycle"
        )
    if champion_model_id is not None and promotion_evidence_id is None:
        reasons.append(
            "historical champion lacks matching continuous-promotion "
            "evidence at the cutoff"
        )
    if live_report is None:
        reasons.append(
            "historical live champion health evidence is unavailable"
        )
    elif live_report.status != "HEALTHY":
        reasons.append(
            f"historical live champion status {live_report.status} "
            "is not HEALTHY"
        )

    return Phase8HistoricalPromotionReport(
        as_of=snapshot.as_of,
        journal_started_at=snapshot.journal_started_at,
        state_consistent=snapshot.consistent,
        phase7_promoted=snapshot.phase7_promoted,
        champion_model_id=champion_model_id,
        completed_cycles=completed_cycles,
        champion_cycle_id=champion_cycle_id,
        continuous_promotion_evidence_id=promotion_evidence_id,
        live_champion=live_report,
        criteria=criteria,
        promotion_ready=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
    )
