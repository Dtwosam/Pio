from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .phase9_policy_rollout_simulation import (
    audit_persisted_phase9_policy_rollout_simulation,
)
from .storage import Storage


PHASE9_POLICY_ROLLBACK_SIMULATION_EVIDENCE_TYPE = (
    "PHASE9_POLICY_ROLLBACK_SIMULATION_V1"
)
PHASE9_POLICY_ROLLBACK_SIMULATION_SCOPE = (
    "__PHASE9_POLICY_ROLLBACK_SIMULATION__"
)


@dataclass(frozen=True)
class Phase9PolicyRollbackMetrics:
    observations: int
    closed_positions: int
    distinct_pools: int
    realized_pnl_quote: float
    drawdown_pct: float
    win_rate_pct: float | None
    mean_return_bps: float | None
    reconciliation_failures: int
    unvalued_closed_positions: int
    stale_decisions: int
    policy_errors: int

    def validate(self) -> None:
        for name, value in (
            ("observations", self.observations),
            ("closed_positions", self.closed_positions),
            ("distinct_pools", self.distinct_pools),
            (
                "reconciliation_failures",
                self.reconciliation_failures,
            ),
            (
                "unvalued_closed_positions",
                self.unvalued_closed_positions,
            ),
            ("stale_decisions", self.stale_decisions),
            ("policy_errors", self.policy_errors),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        for name, value in (
            ("realized_pnl_quote", self.realized_pnl_quote),
            ("drawdown_pct", self.drawdown_pct),
        ):
            if not float("-inf") < value < float("inf"):
                raise ValueError(f"{name} must be finite")
        if self.drawdown_pct < 0.0:
            raise ValueError("drawdown_pct cannot be negative")
        if self.win_rate_pct is not None:
            if (
                not float("-inf")
                < self.win_rate_pct
                < float("inf")
                or not 0.0 <= self.win_rate_pct <= 100.0
            ):
                raise ValueError(
                    "win_rate_pct must be between 0 and 100"
                )
        if self.mean_return_bps is not None and not (
            float("-inf")
            < self.mean_return_bps
            < float("inf")
        ):
            raise ValueError("mean_return_bps must be finite")

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyRollbackCriteria:
    min_observations: int
    min_closed_positions: int
    min_distinct_pools: int
    max_realized_loss_quote: float
    max_drawdown_pct: float
    min_win_rate_pct: float | None = None
    min_mean_return_bps: float | None = None
    max_reconciliation_failures: int = 0
    max_unvalued_closed_positions: int = 0
    max_stale_decisions: int = 0
    max_policy_errors: int = 0

    def validate(self) -> None:
        for name, value in (
            ("min_observations", self.min_observations),
            ("min_closed_positions", self.min_closed_positions),
            ("min_distinct_pools", self.min_distinct_pools),
        ):
            if value < 1:
                raise ValueError(f"{name} must be positive")
        for name, value in (
            (
                "max_reconciliation_failures",
                self.max_reconciliation_failures,
            ),
            (
                "max_unvalued_closed_positions",
                self.max_unvalued_closed_positions,
            ),
            ("max_stale_decisions", self.max_stale_decisions),
            ("max_policy_errors", self.max_policy_errors),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        for name, value in (
            (
                "max_realized_loss_quote",
                self.max_realized_loss_quote,
            ),
            ("max_drawdown_pct", self.max_drawdown_pct),
        ):
            if (
                not float("-inf")
                < value
                < float("inf")
                or value < 0.0
            ):
                raise ValueError(
                    f"{name} must be finite and non-negative"
                )
        if self.min_win_rate_pct is not None:
            if not 0.0 <= self.min_win_rate_pct <= 100.0:
                raise ValueError(
                    "min_win_rate_pct must be between 0 and 100"
                )
        if self.min_mean_return_bps is not None and not (
            float("-inf")
            < self.min_mean_return_bps
            < float("inf")
        ):
            raise ValueError(
                "min_mean_return_bps must be finite"
            )

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyRollbackSimulationReport:
    research_only: bool
    simulation_only: bool
    policy_actionable: bool
    execution_wired: bool
    rollout_simulation_current: bool
    sample_sufficient: bool
    hard_breach: bool
    performance_breach: bool
    rollback_required: bool
    observation_pending: bool
    status: str
    metrics: Phase9PolicyRollbackMetrics
    criteria: Phase9PolicyRollbackCriteria
    rollout_audit: dict[str, Any]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyRollbackSimulationAudit:
    exists: bool
    qualified: bool
    boundary_valid: bool
    payload_valid: bool
    persisted_matches_current: bool
    current: bool
    evidence_id: int | None
    rollback_required: bool | None
    status: str | None
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase9_policy_rollback_simulation(
    storage: Storage,
    *,
    metrics: Phase9PolicyRollbackMetrics,
    criteria: Phase9PolicyRollbackCriteria,
) -> Phase9PolicyRollbackSimulationReport:
    metrics.validate()
    criteria.validate()

    rollout_audit = audit_persisted_phase9_policy_rollout_simulation(
        storage,
    )
    reasons: list[str] = []
    if not rollout_audit.current:
        reasons.extend(
            f"rollout simulation: {reason}"
            for reason in rollout_audit.reasons
        )

    sample_sufficient = (
        metrics.observations >= criteria.min_observations
        and metrics.closed_positions >= criteria.min_closed_positions
        and metrics.distinct_pools >= criteria.min_distinct_pools
    )

    hard_reasons: list[str] = []
    realized_loss_quote = max(0.0, -metrics.realized_pnl_quote)
    if realized_loss_quote > criteria.max_realized_loss_quote:
        hard_reasons.append(
            f"realized loss {realized_loss_quote} exceeds "
            f"{criteria.max_realized_loss_quote}"
        )
    if metrics.drawdown_pct > criteria.max_drawdown_pct:
        hard_reasons.append(
            f"drawdown {metrics.drawdown_pct}% exceeds "
            f"{criteria.max_drawdown_pct}%"
        )
    if (
        metrics.reconciliation_failures
        > criteria.max_reconciliation_failures
    ):
        hard_reasons.append(
            "reconciliation failures exceed configured maximum"
        )
    if (
        metrics.unvalued_closed_positions
        > criteria.max_unvalued_closed_positions
    ):
        hard_reasons.append(
            "unvalued closed positions exceed configured maximum"
        )
    if metrics.stale_decisions > criteria.max_stale_decisions:
        hard_reasons.append(
            "stale decisions exceed configured maximum"
        )
    if metrics.policy_errors > criteria.max_policy_errors:
        hard_reasons.append(
            "policy errors exceed configured maximum"
        )

    performance_reasons: list[str] = []
    if sample_sufficient:
        if criteria.min_win_rate_pct is not None:
            if metrics.win_rate_pct is None:
                performance_reasons.append(
                    "win rate is missing after minimum sample depth"
                )
            elif metrics.win_rate_pct < criteria.min_win_rate_pct:
                performance_reasons.append(
                    f"win rate {metrics.win_rate_pct}% is below "
                    f"{criteria.min_win_rate_pct}%"
                )
        if criteria.min_mean_return_bps is not None:
            if metrics.mean_return_bps is None:
                performance_reasons.append(
                    "mean return is missing after minimum sample depth"
                )
            elif (
                metrics.mean_return_bps
                < criteria.min_mean_return_bps
            ):
                performance_reasons.append(
                    f"mean return {metrics.mean_return_bps} bps is below "
                    f"{criteria.min_mean_return_bps} bps"
                )

    hard_breach = bool(hard_reasons)
    performance_breach = bool(performance_reasons)
    rollback_required = (
        not rollout_audit.current
        or hard_breach
        or performance_breach
    )
    observation_pending = (
        rollout_audit.current
        and not rollback_required
        and not sample_sufficient
    )

    if hard_reasons:
        reasons.extend(
            f"hard rollback trigger: {reason}"
            for reason in hard_reasons
        )
    if performance_reasons:
        reasons.extend(
            f"performance rollback trigger: {reason}"
            for reason in performance_reasons
        )
    if observation_pending:
        reasons.append(
            "minimum observation depth has not been reached"
        )

    if rollback_required:
        status = "ROLLBACK_REQUIRED"
    elif observation_pending:
        status = "OBSERVATION_PENDING"
    else:
        status = "NO_ROLLBACK_TRIGGER"

    return Phase9PolicyRollbackSimulationReport(
        research_only=True,
        simulation_only=True,
        policy_actionable=False,
        execution_wired=False,
        rollout_simulation_current=rollout_audit.current,
        sample_sufficient=sample_sufficient,
        hard_breach=hard_breach,
        performance_breach=performance_breach,
        rollback_required=rollback_required,
        observation_pending=observation_pending,
        status=status,
        metrics=metrics,
        criteria=criteria,
        rollout_audit=rollout_audit.to_record(),
        reasons=tuple(reasons),
    )


def persist_phase9_policy_rollback_simulation(
    storage: Storage,
    *,
    report: Phase9PolicyRollbackSimulationReport,
) -> int:
    if report.policy_actionable:
        raise ValueError(
            "Phase 9 rollback simulation must not grant LIVE policy authority"
        )
    if report.execution_wired:
        raise ValueError(
            "Phase 9 rollback simulation must not be wired to execution"
        )
    if not report.simulation_only:
        raise ValueError(
            "Phase 9 rollback evidence must remain simulation-only"
        )
    if not report.research_only:
        raise ValueError(
            "Phase 9 rollback evidence must remain research-only"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_ROLLBACK_SIMULATION_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_ROLLBACK_SIMULATION_SCOPE,
        as_of=None,
        status=report.status,
        qualified=report.rollout_simulation_current,
        evidence=report.to_record(),
    )


def audit_persisted_phase9_policy_rollback_simulation(
    storage: Storage,
) -> Phase9PolicyRollbackSimulationAudit:
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_ROLLBACK_SIMULATION_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_ROLLBACK_SIMULATION_SCOPE,
    )
    if latest is None:
        return Phase9PolicyRollbackSimulationAudit(
            exists=False,
            qualified=False,
            boundary_valid=False,
            payload_valid=False,
            persisted_matches_current=False,
            current=False,
            evidence_id=None,
            rollback_required=None,
            status=None,
            reasons=("persisted Phase 9 rollback simulation is missing",),
        )

    evidence = latest["evidence"]
    reasons: list[str] = []
    qualified = bool(latest["qualified"])
    boundary_valid = (
        isinstance(evidence, dict)
        and evidence.get("research_only") is True
        and evidence.get("simulation_only") is True
        and evidence.get("policy_actionable") is False
        and evidence.get("execution_wired") is False
    )
    if not boundary_valid:
        reasons.append(
            "persisted rollback evidence violates the simulation-only boundary"
        )
    if not qualified:
        reasons.append(
            "persisted rollback simulation was not based on a current "
            "rollout simulation"
        )

    metrics = None
    criteria = None
    if isinstance(evidence, dict):
        metrics_raw = evidence.get("metrics")
        criteria_raw = evidence.get("criteria")
        try:
            if isinstance(metrics_raw, dict):
                metrics = Phase9PolicyRollbackMetrics(**metrics_raw)
                metrics.validate()
            if isinstance(criteria_raw, dict):
                criteria = Phase9PolicyRollbackCriteria(**criteria_raw)
                criteria.validate()
        except (TypeError, ValueError):
            metrics = None
            criteria = None

    payload_valid = metrics is not None and criteria is not None
    if not payload_valid:
        reasons.append(
            "persisted rollback metrics or criteria are invalid"
        )

    current_report = (
        evaluate_phase9_policy_rollback_simulation(
            storage,
            metrics=metrics,
            criteria=criteria,
        )
        if payload_valid
        else None
    )
    persisted_matches_current = False
    if isinstance(evidence, dict) and current_report is not None:
        persisted_normalized = json.loads(
            json.dumps(evidence, sort_keys=True)
        )
        current_normalized = json.loads(
            json.dumps(current_report.to_record(), sort_keys=True)
        )
        persisted_matches_current = (
            persisted_normalized == current_normalized
        )
    if not persisted_matches_current:
        reasons.append(
            "persisted rollback simulation is stale versus current rollout "
            "evidence"
        )

    rollback_required = (
        bool(current_report.rollback_required)
        if current_report is not None
        else None
    )
    status = (
        current_report.status
        if current_report is not None
        else None
    )

    return Phase9PolicyRollbackSimulationAudit(
        exists=True,
        qualified=qualified,
        boundary_valid=boundary_valid,
        payload_valid=payload_valid,
        persisted_matches_current=persisted_matches_current,
        current=not reasons,
        evidence_id=int(latest["id"]),
        rollback_required=rollback_required,
        status=status,
        reasons=tuple(reasons),
    )
