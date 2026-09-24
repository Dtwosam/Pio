from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .phase9_evidence_step import (
    Phase9EvidenceStepReport,
    run_phase9_evidence_step,
)
from .phase9_pool_cohort import Phase9PoolCohortCriteria
from .phase9_validation import Phase9ResearchBundleCriteria
from .settings import Settings
from .storage import Storage
from .wallet_flow import WalletFlowCriteria


@dataclass(frozen=True)
class Phase9EvidenceRunReport:
    research_only: bool
    read_only_external: bool
    policy_actionable: bool
    execution_wired: bool
    status: str
    max_steps: int
    steps_attempted: int
    steps_progressed: int
    terminal_debt_type: str | None
    terminal_scope: str | None
    research_bundle_ready: bool
    steps: tuple[Phase9EvidenceStepReport, ...]
    reasons: tuple[str, ...]
    next_retry_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def run_phase9_evidence_until_blocked(
    storage: Storage,
    *,
    settings: Settings | None = None,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
    cohort_criteria: Phase9PoolCohortCriteria = (
        Phase9PoolCohortCriteria()
    ),
    wallet_criteria: WalletFlowCriteria = WalletFlowCriteria(),
    max_steps: int = 8,
    mint_max_snapshot_age_seconds: int = 3600,
    history_interval_seconds: int = 3600,
    chain_max_candidates: int = 8,
    bin_array_radius: int = 1,
    wallet_discovery_limit: int = 250,
    wallet_max_positions_per_run: int = 50,
    wallet_historical_signature_limit: int = 25,
    wallet_owner_expansion_limit: int = 25,
    wallet_owner_position_max_pages: int = 3,
    rust_manifest_path: str | None = None,
    rust_binary_path: str | None = None,
    timeout_seconds: int = 120,
) -> Phase9EvidenceRunReport:
    if max_steps < 1:
        raise ValueError("max_steps must be positive")

    steps: list[Phase9EvidenceStepReport] = []
    reasons: list[str] = []
    terminal_status = "MAX_STEPS"
    terminal_debt_type: str | None = None
    terminal_scope: str | None = None

    for _ in range(max_steps):
        step = run_phase9_evidence_step(
            storage,
            settings=settings,
            criteria=criteria,
            cohort_criteria=cohort_criteria,
            wallet_criteria=wallet_criteria,
            mint_max_snapshot_age_seconds=mint_max_snapshot_age_seconds,
            history_interval_seconds=history_interval_seconds,
            chain_max_candidates=chain_max_candidates,
            bin_array_radius=bin_array_radius,
            wallet_discovery_limit=wallet_discovery_limit,
            wallet_max_positions_per_run=wallet_max_positions_per_run,
            wallet_historical_signature_limit=(
                wallet_historical_signature_limit
            ),
            wallet_owner_expansion_limit=wallet_owner_expansion_limit,
            wallet_owner_position_max_pages=(
                wallet_owner_position_max_pages
            ),
            rust_manifest_path=rust_manifest_path,
            rust_binary_path=rust_binary_path,
            timeout_seconds=timeout_seconds,
        )
        steps.append(step)
        terminal_debt_type = step.debt_type
        terminal_scope = step.scope

        if step.status == "READY":
            terminal_status = "READY"
            break
        if step.status in {
            "MANUAL_REQUIRED",
            "WAITING_INTERVAL",
            "WAITING_SOURCE_ACTIVITY",
            "FAILED",
        }:
            terminal_status = step.status
            if step.error:
                reasons.append(step.error)
            break
        if step.status != "COMPLETE":
            terminal_status = step.status
            reasons.append(
                f"unexpected evidence-step status: {step.status}"
            )
            break
        if not step.progressed:
            terminal_status = "NO_PROGRESS"
            reasons.append(
                "planner-selected evidence step completed without reducing "
                "the current evidence debt"
            )
            break
    else:
        terminal_status = "MAX_STEPS"
        reasons.append(
            f"bounded evidence run reached max_steps={max_steps}"
        )

    final_plan = steps[-1].plan_after if steps else None
    if final_plan is not None:
        reasons.extend(final_plan.reasons)

    return Phase9EvidenceRunReport(
        research_only=True,
        read_only_external=True,
        policy_actionable=False,
        execution_wired=False,
        status=terminal_status,
        max_steps=max_steps,
        steps_attempted=len(steps),
        steps_progressed=sum(int(step.progressed) for step in steps),
        terminal_debt_type=terminal_debt_type,
        terminal_scope=terminal_scope,
        research_bundle_ready=bool(
            final_plan is not None
            and final_plan.research_bundle_ready
        ),
        steps=tuple(steps),
        reasons=tuple(dict.fromkeys(reasons)),
        next_retry_at=(
            final_plan.history_next_eligible_at
            if (
                final_plan is not None
                and terminal_status == "WAITING_INTERVAL"
            )
            else None
        ),
    )
