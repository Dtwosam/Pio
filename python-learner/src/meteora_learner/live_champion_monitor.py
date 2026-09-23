from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from statistics import fmean
from typing import Any

from .ml_registry import CHAMPION, ROLLED_BACK, ModelRegistryRecord, _to_record
from .phase_promotion import PHASE7, PHASE7_EVIDENCE_TYPE
from .storage import Storage, utc_now_iso


LIVE_MONITOR_EVIDENCE_TYPE = "LIVE_CHAMPION_MONITOR_V1"
LIVE_ROLLBACK_EVIDENCE_TYPE = "LIVE_CHAMPION_ROLLBACK_V1"


@dataclass(frozen=True)
class LiveChampionCriteria:
    min_live_labels: int = 10
    max_realized_drawdown_bps: int = 2_000
    max_single_loss_bps: int = 1_500
    min_win_rate: float = 0.30
    min_mean_return_bps: float = -100.0
    max_mean_abs_prediction_error_bps: float = 1_500.0

    def __post_init__(self) -> None:
        if self.min_live_labels <= 0:
            raise ValueError("min_live_labels must be positive")
        if self.max_realized_drawdown_bps < 0:
            raise ValueError(
                "max_realized_drawdown_bps cannot be negative"
            )
        if self.max_single_loss_bps < 0:
            raise ValueError("max_single_loss_bps cannot be negative")
        if not 0.0 <= self.min_win_rate <= 1.0:
            raise ValueError("min_win_rate must be between 0 and 1")
        if not math.isfinite(self.min_mean_return_bps):
            raise ValueError("min_mean_return_bps must be finite")
        if (
            not math.isfinite(
                self.max_mean_abs_prediction_error_bps
            )
            or self.max_mean_abs_prediction_error_bps < 0
        ):
            raise ValueError(
                "max_mean_abs_prediction_error_bps must be finite "
                "and non-negative"
            )


@dataclass(frozen=True)
class LiveChampionReport:
    model_id: str
    model_status: str
    phase7_promoted: bool
    label_count: int
    distinct_pools: int
    mean_return_bps: float | None
    win_rate: float | None
    max_single_loss_bps: int | None
    realized_max_drawdown_bps: int | None
    mean_prediction_error_bps: float | None
    mean_abs_prediction_error_bps: float | None
    criteria: LiveChampionCriteria
    status: str
    rollback_recommended: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _max_drawdown_bps(returns_bps: list[int]) -> int:
    equity = 1.0
    peak = 1.0
    maximum = 0.0
    for return_bps in returns_bps:
        if return_bps < -10_000:
            raise ValueError(
                "live realized_return_bps cannot be below -10000"
            )
        equity *= 1.0 + return_bps / 10_000.0
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(
                maximum,
                (peak - equity) / peak * 10_000.0,
            )
    return int(round(maximum))


def evaluate_live_champion(
    storage: Storage,
    *,
    model_id: str | None = None,
    criteria: LiveChampionCriteria = LiveChampionCriteria(),
) -> LiveChampionReport:
    phase7_promoted = storage.phase_is_promoted(
        PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
    )

    raw = (
        storage.model_registry_entry(model_id)
        if model_id is not None
        else storage.current_model_champion()
    )
    if raw is None:
        raise ValueError(
            "no model selected and no current champion exists"
        )
    selected_model_id = str(raw["model_id"])
    model_status = str(raw["status"])

    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT pool_address, realized_return_bps,
                   prediction_error_bps, target_positive_return
            FROM live_learning_labels
            WHERE model_version = ?
            ORDER BY created_at ASC, position_address ASC
            """,
            (selected_model_id,),
        ).fetchall()

    returns = [int(row[1]) for row in rows]
    errors = [int(row[2]) for row in rows]
    label_count = len(rows)
    distinct_pools = len({str(row[0]) for row in rows})

    mean_return = (
        float(fmean(returns)) if returns else None
    )
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
    max_drawdown = (
        _max_drawdown_bps(returns) if returns else None
    )
    mean_error = (
        float(fmean(errors)) if errors else None
    )
    mean_abs_error = (
        float(fmean(abs(value) for value in errors))
        if errors
        else None
    )

    reasons: list[str] = []
    rollback_recommended = False
    if not phase7_promoted:
        status = "BLOCKED_PHASE7"
        reasons.append(
            "Phase 7 must be persistently promoted before live champion monitoring"
        )
    elif model_status != CHAMPION:
        status = "NOT_CHAMPION"
        reasons.append(
            f"model status {model_status} is not CHAMPION"
        )
    elif label_count < criteria.min_live_labels:
        status = "INSUFFICIENT_EVIDENCE"
        reasons.append(
            f"live labels {label_count} are below "
            f"{criteria.min_live_labels}"
        )
    else:
        checks = (
            (
                max_drawdown is not None
                and max_drawdown
                <= criteria.max_realized_drawdown_bps,
                f"realized max drawdown {max_drawdown} bps exceeds "
                f"{criteria.max_realized_drawdown_bps} bps",
            ),
            (
                max_single_loss is not None
                and max_single_loss
                <= criteria.max_single_loss_bps,
                f"max single loss {max_single_loss} bps exceeds "
                f"{criteria.max_single_loss_bps} bps",
            ),
            (
                win_rate is not None
                and win_rate >= criteria.min_win_rate,
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
        model_id=selected_model_id,
        model_status=model_status,
        phase7_promoted=phase7_promoted,
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


def persist_live_champion_report(
    storage: Storage,
    *,
    report: LiveChampionReport,
) -> int:
    return storage.save_model_live_evidence(
        model_id=report.model_id,
        evidence_type=LIVE_MONITOR_EVIDENCE_TYPE,
        status=report.status,
        evidence=report.to_record(),
    )


def rollback_live_champion(
    storage: Storage,
    *,
    model_id: str,
    notes: str | None = None,
) -> ModelRegistryRecord:
    if not model_id.strip():
        raise ValueError("model_id is required")

    with storage.connect() as conn:
        current = conn.execute(
            """
            SELECT model_id, status
            FROM model_registry
            WHERE model_id = ?
            """,
            (model_id,),
        ).fetchone()
        if current is None:
            raise ValueError(f"unknown model_id: {model_id}")
        if str(current[1]) != CHAMPION:
            raise ValueError("model is not the current CHAMPION")

        evidence_row = conn.execute(
            """
            SELECT evidence_json
            FROM model_live_evidence
            WHERE model_id = ?
              AND evidence_type = ?
              AND status = 'BREACH'
            ORDER BY id DESC
            LIMIT 1
            """,
            (model_id, LIVE_MONITOR_EVIDENCE_TYPE),
        ).fetchone()
        if evidence_row is None:
            raise ValueError(
                "persisted BREACH live-monitor evidence is required"
            )
        evidence = json.loads(str(evidence_row[0]))
        if evidence.get("rollback_recommended") is not True:
            raise ValueError(
                "persisted live-monitor evidence does not recommend rollback"
            )
        if evidence.get("model_id") != model_id:
            raise ValueError(
                "live-monitor evidence model_id does not match champion"
            )
        if evidence.get("phase7_promoted") is not True:
            raise ValueError(
                "live-monitor evidence was not evaluated with Phase 7 promoted"
            )

        now = utc_now_iso()
        cursor = conn.execute(
            """
            UPDATE model_registry
            SET status = ?, updated_at = ?,
                notes = COALESCE(?, notes)
            WHERE model_id = ? AND status = ?
            """,
            (ROLLED_BACK, now, notes, model_id, CHAMPION),
        )
        if cursor.rowcount != 1:
            raise ValueError(
                "champion changed while rollback was being applied"
            )
        conn.execute(
            """
            INSERT INTO model_live_evidence(
                model_id, created_at, evidence_type,
                status, evidence_json
            ) VALUES (?, ?, ?, 'ROLLED_BACK', ?)
            """,
            (
                model_id,
                now,
                LIVE_ROLLBACK_EVIDENCE_TYPE,
                json.dumps(
                    {
                        "model_id": model_id,
                        "source_evidence": evidence,
                        "fallback_policy": "DETERMINISTIC",
                    },
                    separators=(",", ":"),
                ),
            ),
        )

    updated = storage.model_registry_entry(model_id)
    if updated is None:
        raise RuntimeError("rolled-back model disappeared")
    return _to_record(updated)
