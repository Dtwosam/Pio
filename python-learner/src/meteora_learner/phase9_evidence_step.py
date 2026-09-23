from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from .collector import collect_once
from .phase9_capture_plan import Phase9ChainCaptureCriteria
from .phase9_chain_capture import run_phase9_chain_capture_batch
from .phase9_evidence_plan import (
    Phase9EvidencePlan,
    build_phase9_evidence_plan,
)
from .phase9_evidence_status import evaluate_phase9_evidence_status
from .phase9_history_capture import run_phase9_history_capture
from .phase9_mint_capture import (
    Phase9MintCaptureCriteria,
    run_phase9_mint_capture,
)
from .phase9_pool_cohort import Phase9PoolCohortCriteria
from .phase9_research_refresh import run_phase9_research_refresh
from .phase9_validation import Phase9ResearchBundleCriteria
from .phase9_wallet_flow_capture import run_phase9_wallet_flow_capture
from .settings import Settings
from .storage import Storage
from .wallet_flow import WalletFlowCriteria


@dataclass(frozen=True)
class Phase9EvidenceStepReport:
    research_only: bool
    read_only_external: bool
    policy_actionable: bool
    execution_wired: bool
    status: str
    debt_type: str | None
    scope: str | None
    progressed: bool
    operation: dict[str, Any] | None
    error: str | None
    plan_before: Phase9EvidencePlan
    plan_after: Phase9EvidencePlan

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
        if isinstance(result, dict):
            return result
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"value": str(value)}


def _same_debt_progressed(
    before: Phase9EvidencePlan,
    after: Phase9EvidencePlan,
) -> bool:
    old = before.next_action
    new = after.next_action
    if before.research_bundle_ready != after.research_bundle_ready:
        return after.research_bundle_ready
    if old is None:
        return False
    if new is None:
        return True
    if (old.debt_type, old.scope) != (new.debt_type, new.scope):
        return True
    return (
        old.current != new.current
        or old.remaining != new.remaining
        or before.history_capture_cycles_remaining
        != after.history_capture_cycles_remaining
    )


def run_phase9_evidence_step(
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
) -> Phase9EvidenceStepReport:
    if chain_max_candidates < 1:
        raise ValueError("chain_max_candidates must be positive")
    if bin_array_radius < 0:
        raise ValueError("bin_array_radius cannot be negative")
    if history_interval_seconds < 0:
        raise ValueError("history_interval_seconds cannot be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    current_settings = settings or Settings.from_env()
    before = build_phase9_evidence_plan(
        storage,
        criteria=criteria,
        cohort_criteria=cohort_criteria,
        wallet_criteria=wallet_criteria,
        mint_max_snapshot_age_seconds=mint_max_snapshot_age_seconds,
        history_interval_seconds=history_interval_seconds,
    )
    action = before.next_action

    if action is None:
        return Phase9EvidenceStepReport(
            research_only=True,
            read_only_external=True,
            policy_actionable=False,
            execution_wired=False,
            status="READY",
            debt_type=None,
            scope=None,
            progressed=False,
            operation=None,
            error=None,
            plan_before=before,
            plan_after=before,
        )

    if action.debt_type in {
        "PHASE8_DEPENDENCY",
        "EXPLICIT_RESEARCH_INPUTS",
    }:
        return Phase9EvidenceStepReport(
            research_only=True,
            read_only_external=True,
            policy_actionable=False,
            execution_wired=False,
            status="MANUAL_REQUIRED",
            debt_type=action.debt_type,
            scope=action.scope,
            progressed=False,
            operation=None,
            error=None,
            plan_before=before,
            plan_after=before,
        )

    operation: dict[str, Any] | None = None
    error: str | None = None
    status = "COMPLETE"

    try:
        if action.debt_type == "API_POOL_COVERAGE":
            operation = _record(collect_once(current_settings))
        elif action.debt_type == "CHAIN_POOL_COVERAGE":
            preferred = tuple(
                value for value in action.scope.split(",") if value
            )
            result = run_phase9_chain_capture_batch(
                storage,
                criteria=Phase9ChainCaptureCriteria(
                    target_chain_pools=cohort_criteria.min_research_pools,
                    max_candidates=chain_max_candidates,
                    bin_array_radius=bin_array_radius,
                    max_api_snapshot_age_seconds=(
                        cohort_criteria.max_api_snapshot_age_seconds
                    ),
                ),
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
                preferred_pool_addresses=preferred,
                max_preferred_candidates=len(preferred),
            )
            operation = result.to_record()
        elif action.debt_type == "CHAIN_HISTORY_DEPTH":
            pools = tuple(
                value for value in action.scope.split(",") if value
            )
            result = run_phase9_history_capture(
                storage,
                pool_addresses=pools,
                bin_array_radius=bin_array_radius,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
                min_observation_interval_seconds=history_interval_seconds,
            )
            operation = result.to_record()
        elif action.debt_type == "MINT_INPUTS":
            evidence = evaluate_phase9_evidence_status(
                storage,
                criteria=criteria,
                cohort_criteria=cohort_criteria,
                mint_max_snapshot_age_seconds=(
                    mint_max_snapshot_age_seconds
                ),
                wallet_criteria=wallet_criteria,
            )
            mint_pools = tuple(
                item.pool_address
                for item in evidence.pools
                if item.mint_target
            )
            result = run_phase9_mint_capture(
                storage,
                criteria=Phase9MintCaptureCriteria(
                    target_pools=criteria.min_mint_risk_pools,
                    max_snapshot_age_seconds=(
                        mint_max_snapshot_age_seconds
                    ),
                    include_reward_mints=True,
                ),
                pool_addresses=mint_pools,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
            )
            operation = result.to_record()
        elif action.debt_type == "WALLET_FLOW_SOURCE":
            result = run_phase9_wallet_flow_capture(
                storage,
                pool_address=action.scope,
                criteria=wallet_criteria,
                discovery_limit=wallet_discovery_limit,
                max_positions_per_run=wallet_max_positions_per_run,
                historical_signature_limit=(
                    wallet_historical_signature_limit
                ),
                owner_expansion_limit=wallet_owner_expansion_limit,
                owner_position_max_pages=wallet_owner_position_max_pages,
                settings=current_settings,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
            )
            operation = result.to_record()
        elif action.debt_type in {
            "RESEARCH_REFRESH",
            "BUNDLE_REVALIDATION",
        }:
            result = run_phase9_research_refresh(
                storage,
                criteria=criteria,
            )
            operation = result.to_record()
        else:
            status = "MANUAL_REQUIRED"
    except Exception as exc:
        status = "FAILED"
        error = f"{type(exc).__name__}: {str(exc)[:2000]}"

    after = build_phase9_evidence_plan(
        storage,
        criteria=criteria,
        cohort_criteria=cohort_criteria,
        wallet_criteria=wallet_criteria,
        mint_max_snapshot_age_seconds=mint_max_snapshot_age_seconds,
        history_interval_seconds=history_interval_seconds,
    )
    return Phase9EvidenceStepReport(
        research_only=True,
        read_only_external=True,
        policy_actionable=False,
        execution_wired=False,
        status=status,
        debt_type=action.debt_type,
        scope=action.scope,
        progressed=(
            status == "COMPLETE"
            and _same_debt_progressed(before, after)
        ),
        operation=operation,
        error=error,
        plan_before=before,
        plan_after=after,
    )
