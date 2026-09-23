from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .ml_registry import CHAMPION, PAPER_CHALLENGER, ModelRegistryRecord, _to_record
from .paper_performance import PaperPerformanceReport, build_paper_performance
from .storage import Storage


@dataclass(frozen=True)
class PaperChallengerCriteria:
    min_closed_trades: int = 20
    min_realized_return_bps: int = 0
    min_win_rate: float = 0.50
    max_realized_drawdown_bps: int = 1_500
    min_return_uplift_vs_baseline_bps: int = 0

    def __post_init__(self) -> None:
        if self.min_closed_trades <= 0:
            raise ValueError("min_closed_trades must be positive")
        if not 0.0 <= self.min_win_rate <= 1.0:
            raise ValueError("min_win_rate must be between 0 and 1")
        if self.max_realized_drawdown_bps < 0:
            raise ValueError("max_realized_drawdown_bps cannot be negative")


@dataclass(frozen=True)
class PaperChallengerValidation:
    account_id: str
    model_id: str
    phase3_ready: bool
    model_status: str
    challenger: PaperPerformanceReport
    baseline: PaperPerformanceReport
    return_uplift_vs_baseline_bps: int | None
    criteria: PaperChallengerCriteria
    paper_qualified: bool
    policy_actionable: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_paper_challenger(
    storage: Storage,
    *,
    account_id: str,
    model_id: str,
    phase3_ready: bool,
    criteria: PaperChallengerCriteria = PaperChallengerCriteria(),
) -> PaperChallengerValidation:
    raw = storage.model_registry_entry(model_id)
    if raw is None:
        raise ValueError(f"unknown model_id: {model_id}")
    status = str(raw["status"])

    challenger = build_paper_performance(
        storage,
        account_id=account_id,
        policy_source="ML_CHALLENGER",
        model_id=model_id,
    )
    baseline = build_paper_performance(
        storage,
        account_id=account_id,
        policy_source="DETERMINISTIC",
        model_id=None,
    )

    uplift = None
    if (
        challenger.realized_return_bps is not None
        and baseline.realized_return_bps is not None
    ):
        uplift = (
            challenger.realized_return_bps
            - baseline.realized_return_bps
        )

    reasons: list[str] = []
    checks = [
        (
            phase3_ready,
            "Phase 3 deterministic policy is not promoted",
        ),
        (
            status == PAPER_CHALLENGER,
            f"model status {status} is not PAPER_CHALLENGER",
        ),
        (
            challenger.closed_trades >= criteria.min_closed_trades,
            f"challenger closed_trades {challenger.closed_trades} < required "
            f"{criteria.min_closed_trades}",
        ),
        (
            baseline.closed_trades >= criteria.min_closed_trades,
            f"baseline closed_trades {baseline.closed_trades} < required "
            f"{criteria.min_closed_trades}",
        ),
        (
            challenger.realized_return_bps is not None
            and challenger.realized_return_bps >= criteria.min_realized_return_bps,
            f"challenger realized_return_bps "
            f"{challenger.realized_return_bps if challenger.realized_return_bps is not None else 'unavailable'} "
            f"< required {criteria.min_realized_return_bps}",
        ),
        (
            challenger.win_rate is not None
            and challenger.win_rate >= criteria.min_win_rate,
            f"challenger win_rate "
            f"{challenger.win_rate if challenger.win_rate is not None else 'unavailable'} "
            f"< required {criteria.min_win_rate:.6f}",
        ),
        (
            challenger.realized_max_drawdown_bps is not None
            and challenger.realized_max_drawdown_bps
            <= criteria.max_realized_drawdown_bps,
            f"challenger realized_max_drawdown_bps "
            f"{challenger.realized_max_drawdown_bps if challenger.realized_max_drawdown_bps is not None else 'unavailable'} "
            f"> allowed {criteria.max_realized_drawdown_bps}",
        ),
        (
            uplift is not None
            and uplift >= criteria.min_return_uplift_vs_baseline_bps,
            f"return_uplift_vs_baseline_bps "
            f"{uplift if uplift is not None else 'unavailable'} "
            f"< required {criteria.min_return_uplift_vs_baseline_bps}",
        ),
    ]
    reasons.extend(message for passed, message in checks if not passed)

    return PaperChallengerValidation(
        account_id=account_id,
        model_id=model_id,
        phase3_ready=phase3_ready,
        model_status=status,
        challenger=challenger,
        baseline=baseline,
        return_uplift_vs_baseline_bps=uplift,
        criteria=criteria,
        paper_qualified=not reasons,
        policy_actionable=False,
        reasons=tuple(reasons),
    )


def promote_paper_challenger(
    storage: Storage,
    *,
    validation: PaperChallengerValidation,
    notes: str | None = None,
) -> ModelRegistryRecord:
    if not validation.paper_qualified:
        raise ValueError("paper challenger has not passed validation")

    raw = storage.model_registry_entry(validation.model_id)
    if raw is None:
        raise ValueError(f"unknown model_id: {validation.model_id}")
    if str(raw["status"]) != PAPER_CHALLENGER:
        raise ValueError("model is no longer in PAPER_CHALLENGER status")

    champion = storage.current_model_champion()
    if champion is not None and champion["model_id"] != validation.model_id:
        raise ValueError(
            f"champion already exists: {champion['model_id']}; "
            "roll it back before promoting another model"
        )

    storage.save_model_promotion_evidence(
        model_id=validation.model_id,
        evidence_type="PAPER_VALIDATION_V1",
        qualified=validation.paper_qualified,
        evidence=validation.to_record(),
    )
    storage.promote_model_to_champion(
        validation.model_id,
        notes=notes,
    )
    updated = storage.model_registry_entry(validation.model_id)
    if updated is None:
        raise RuntimeError("promoted model disappeared")
    return _to_record(updated)
