from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .phase9_policy_readiness import evaluate_phase9_policy_readiness
from .storage import Storage


PHASE9_POLICY_ROLLOUT_SIMULATION_EVIDENCE_TYPE = (
    "PHASE9_POLICY_ROLLOUT_SIMULATION_V1"
)
PHASE9_POLICY_ROLLOUT_SIMULATION_SCOPE = (
    "__PHASE9_POLICY_ROLLOUT_SIMULATION__"
)


@dataclass(frozen=True)
class Phase9PolicyRolloutEnvelope:
    enabled: bool
    allowed_pool_addresses: tuple[str, ...]
    max_open_positions: int
    max_rebalances_per_position: int
    max_capital_quote_per_entry: float
    max_daily_entry_capital_quote: float
    max_daily_entry_submissions: int
    max_daily_realized_loss_quote: float
    max_daily_drawdown_pct: float
    allow_rebalance: bool
    allow_exit: bool

    def validate(self) -> None:
        if not self.allowed_pool_addresses:
            raise ValueError("allowed_pool_addresses cannot be empty")
        if any(not value.strip() for value in self.allowed_pool_addresses):
            raise ValueError("allowed_pool_addresses cannot contain blanks")
        for name, value in (
            ("max_open_positions", self.max_open_positions),
            (
                "max_rebalances_per_position",
                self.max_rebalances_per_position,
            ),
            (
                "max_daily_entry_submissions",
                self.max_daily_entry_submissions,
            ),
        ):
            if value < 1:
                raise ValueError(f"{name} must be positive")
        for name, value in (
            (
                "max_capital_quote_per_entry",
                self.max_capital_quote_per_entry,
            ),
            (
                "max_daily_entry_capital_quote",
                self.max_daily_entry_capital_quote,
            ),
            (
                "max_daily_realized_loss_quote",
                self.max_daily_realized_loss_quote,
            ),
        ):
            if not float("-inf") < value < float("inf") or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        if (
            self.max_daily_entry_capital_quote
            < self.max_capital_quote_per_entry
        ):
            raise ValueError(
                "max_daily_entry_capital_quote cannot be below "
                "max_capital_quote_per_entry"
            )
        if (
            not float("-inf")
            < self.max_daily_drawdown_pct
            < float("inf")
            or self.max_daily_drawdown_pct < 0.0
        ):
            raise ValueError(
                "max_daily_drawdown_pct must be finite and non-negative"
            )

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyRolloutSimulationCriteria:
    require_strictly_narrower: bool = True
    require_exit_enabled: bool = True
    require_rebalance_disabled: bool = True

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyRolloutSimulationReport:
    research_only: bool
    simulation_only: bool
    policy_actionable: bool
    execution_wired: bool
    policy_readiness_ready: bool
    proposal_disabled: bool
    allowed_pools_subset: bool
    numeric_caps_within_current: bool
    action_permissions_compatible: bool
    strictly_narrower: bool
    rollout_simulation_ready: bool
    current: Phase9PolicyRolloutEnvelope
    proposed: Phase9PolicyRolloutEnvelope
    criteria: Phase9PolicyRolloutSimulationCriteria
    policy_readiness: dict[str, Any]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyRolloutSimulationAudit:
    exists: bool
    qualified: bool
    boundary_valid: bool
    criteria_valid: bool
    current_rollout_simulation_ready: bool
    persisted_matches_current: bool
    current: bool
    evidence_id: int | None
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _normalized_pools(values: tuple[str, ...]) -> set[str]:
    return {value.strip() for value in values if value.strip()}


def evaluate_phase9_policy_rollout_simulation(
    storage: Storage,
    *,
    current: Phase9PolicyRolloutEnvelope,
    proposed: Phase9PolicyRolloutEnvelope,
    criteria: Phase9PolicyRolloutSimulationCriteria = (
        Phase9PolicyRolloutSimulationCriteria()
    ),
) -> Phase9PolicyRolloutSimulationReport:
    current.validate()
    proposed.validate()

    readiness = evaluate_phase9_policy_readiness(storage)
    reasons: list[str] = []
    if not readiness.ready:
        reasons.extend(
            f"policy readiness: {reason}"
            for reason in readiness.reasons
        )

    proposal_disabled = not proposed.enabled
    if not proposal_disabled:
        reasons.append(
            "proposed Phase 9 rollout envelope must remain disabled in "
            "simulation evidence"
        )

    current_pools = _normalized_pools(current.allowed_pool_addresses)
    proposed_pools = _normalized_pools(proposed.allowed_pool_addresses)
    allowed_pools_subset = proposed_pools.issubset(current_pools)
    if not allowed_pools_subset:
        reasons.append(
            "proposed allowed pools are not a subset of the current "
            "controlled-live allowlist"
        )

    numeric_pairs = (
        (
            "max_open_positions",
            proposed.max_open_positions,
            current.max_open_positions,
        ),
        (
            "max_rebalances_per_position",
            proposed.max_rebalances_per_position,
            current.max_rebalances_per_position,
        ),
        (
            "max_capital_quote_per_entry",
            proposed.max_capital_quote_per_entry,
            current.max_capital_quote_per_entry,
        ),
        (
            "max_daily_entry_capital_quote",
            proposed.max_daily_entry_capital_quote,
            current.max_daily_entry_capital_quote,
        ),
        (
            "max_daily_entry_submissions",
            proposed.max_daily_entry_submissions,
            current.max_daily_entry_submissions,
        ),
        (
            "max_daily_realized_loss_quote",
            proposed.max_daily_realized_loss_quote,
            current.max_daily_realized_loss_quote,
        ),
        (
            "max_daily_drawdown_pct",
            proposed.max_daily_drawdown_pct,
            current.max_daily_drawdown_pct,
        ),
    )
    cap_violations = tuple(
        name
        for name, proposed_value, current_value in numeric_pairs
        if proposed_value > current_value
    )
    numeric_caps_within_current = not cap_violations
    if cap_violations:
        reasons.append(
            "proposed caps exceed current controlled-live limits: "
            + ", ".join(cap_violations)
        )

    action_permissions_compatible = True
    if proposed.allow_rebalance and not current.allow_rebalance:
        action_permissions_compatible = False
        reasons.append(
            "proposed rollout enables rebalance while current "
            "controlled-live policy disables it"
        )
    if proposed.allow_exit and not current.allow_exit:
        action_permissions_compatible = False
        reasons.append(
            "proposed rollout enables exit while current controlled-live "
            "policy disables it"
        )
    if criteria.require_exit_enabled and not proposed.allow_exit:
        action_permissions_compatible = False
        reasons.append(
            "proposed rollout must preserve EXIT for risk reduction"
        )
    if criteria.require_rebalance_disabled and proposed.allow_rebalance:
        action_permissions_compatible = False
        reasons.append(
            "proposed rollout must keep REBALANCE disabled during the "
            "simulation canary"
        )

    strictly_narrower = (
        len(proposed_pools) < len(current_pools)
        or any(
            proposed_value < current_value
            for _, proposed_value, current_value in numeric_pairs
        )
        or (
            current.allow_rebalance
            and not proposed.allow_rebalance
        )
    )
    if criteria.require_strictly_narrower and not strictly_narrower:
        reasons.append(
            "proposed rollout envelope is not strictly narrower than the "
            "current controlled-live envelope"
        )

    rollout_ready = (
        readiness.ready
        and proposal_disabled
        and allowed_pools_subset
        and numeric_caps_within_current
        and action_permissions_compatible
        and (
            strictly_narrower
            or not criteria.require_strictly_narrower
        )
        and not reasons
    )

    return Phase9PolicyRolloutSimulationReport(
        research_only=True,
        simulation_only=True,
        policy_actionable=False,
        execution_wired=False,
        policy_readiness_ready=readiness.ready,
        proposal_disabled=proposal_disabled,
        allowed_pools_subset=allowed_pools_subset,
        numeric_caps_within_current=numeric_caps_within_current,
        action_permissions_compatible=action_permissions_compatible,
        strictly_narrower=strictly_narrower,
        rollout_simulation_ready=rollout_ready,
        current=current,
        proposed=proposed,
        criteria=criteria,
        policy_readiness=readiness.to_record(),
        reasons=tuple(reasons),
    )


def persist_phase9_policy_rollout_simulation(
    storage: Storage,
    *,
    report: Phase9PolicyRolloutSimulationReport,
) -> int:
    if report.policy_actionable:
        raise ValueError(
            "Phase 9 rollout simulation must not grant LIVE policy authority"
        )
    if report.execution_wired:
        raise ValueError(
            "Phase 9 rollout simulation must not be wired to execution"
        )
    if not report.simulation_only:
        raise ValueError(
            "Phase 9 rollout evidence must remain simulation-only"
        )
    if not report.research_only:
        raise ValueError(
            "Phase 9 rollout evidence must remain research-only"
        )
    if report.proposed.enabled:
        raise ValueError(
            "Phase 9 rollout simulation cannot persist an enabled proposal"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_ROLLOUT_SIMULATION_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_ROLLOUT_SIMULATION_SCOPE,
        as_of=None,
        status=(
            "ROLLOUT_SIMULATION_READY"
            if report.rollout_simulation_ready
            else "ROLLOUT_SIMULATION_NOT_READY"
        ),
        qualified=report.rollout_simulation_ready,
        evidence=report.to_record(),
    )


def audit_persisted_phase9_policy_rollout_simulation(
    storage: Storage,
) -> Phase9PolicyRolloutSimulationAudit:
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_ROLLOUT_SIMULATION_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_ROLLOUT_SIMULATION_SCOPE,
    )
    if latest is None:
        return Phase9PolicyRolloutSimulationAudit(
            exists=False,
            qualified=False,
            boundary_valid=False,
            criteria_valid=False,
            current_rollout_simulation_ready=False,
            persisted_matches_current=False,
            current=False,
            evidence_id=None,
            reasons=("persisted Phase 9 rollout simulation is missing",),
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
            "persisted rollout evidence violates the simulation-only boundary"
        )
    if not qualified:
        reasons.append(
            "latest persisted rollout simulation is not qualified"
        )

    current = None
    proposed = None
    criteria = None
    if isinstance(evidence, dict):
        current_raw = evidence.get("current")
        proposed_raw = evidence.get("proposed")
        criteria_raw = evidence.get("criteria")
        try:
            if isinstance(current_raw, dict):
                current = Phase9PolicyRolloutEnvelope(
                    allowed_pool_addresses=tuple(
                        current_raw.get("allowed_pool_addresses", ())
                    ),
                    **{
                        key: value
                        for key, value in current_raw.items()
                        if key != "allowed_pool_addresses"
                    },
                )
                current.validate()
            if isinstance(proposed_raw, dict):
                proposed = Phase9PolicyRolloutEnvelope(
                    allowed_pool_addresses=tuple(
                        proposed_raw.get("allowed_pool_addresses", ())
                    ),
                    **{
                        key: value
                        for key, value in proposed_raw.items()
                        if key != "allowed_pool_addresses"
                    },
                )
                proposed.validate()
            if isinstance(criteria_raw, dict):
                criteria = Phase9PolicyRolloutSimulationCriteria(
                    **criteria_raw
                )
        except (TypeError, ValueError):
            current = None
            proposed = None
            criteria = None

    criteria_valid = (
        current is not None
        and proposed is not None
        and criteria is not None
    )
    if not criteria_valid:
        reasons.append(
            "persisted rollout envelope or criteria are invalid"
        )

    current_report = (
        evaluate_phase9_policy_rollout_simulation(
            storage,
            current=current,
            proposed=proposed,
            criteria=criteria,
        )
        if criteria_valid
        else None
    )
    current_ready = bool(
        current_report is not None
        and current_report.rollout_simulation_ready
    )
    if not current_ready:
        reasons.append(
            "current Phase 9 rollout simulation no longer passes"
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
            "persisted rollout simulation is stale versus current readiness"
        )

    return Phase9PolicyRolloutSimulationAudit(
        exists=True,
        qualified=qualified,
        boundary_valid=boundary_valid,
        criteria_valid=criteria_valid,
        current_rollout_simulation_ready=current_ready,
        persisted_matches_current=persisted_matches_current,
        current=not reasons,
        evidence_id=int(latest["id"]),
        reasons=tuple(reasons),
    )
