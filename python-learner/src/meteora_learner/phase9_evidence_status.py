from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .phase8_validation import audit_persisted_phase8_promotion
from .phase9_explicit_inputs import audit_phase9_explicit_inputs
from .phase9_mint_capture import (
    Phase9MintCaptureCriteria,
    build_phase9_mint_capture_plan,
)
from .phase9_pool_cohort import (
    Phase9PoolCohortCriteria,
    evaluate_phase9_pool_cohort,
)
from .phase9_source_freshness import evaluate_phase9_source_freshness
from .phase9_validation import (
    Phase9ResearchBundleCriteria,
    evaluate_phase9_research_bundle,
)
from .phase9_wallet_flow_capture import wallet_flow_source_state
from .storage import Storage, utc_now_iso
from .wallet_flow import WalletFlowCriteria


@dataclass(frozen=True)
class Phase9PoolEvidenceStatus:
    pool_address: str
    rank: int
    chain_observations: int
    required_chain_observations: int
    chain_observations_remaining: int
    history_ready: bool
    mint_target: bool
    mint_inputs_ready: bool | None
    mint_captures_required: int | None
    wallet_target: bool
    wallet_events: int | None
    wallet_unique_users: int | None
    wallet_source_ready: bool | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9EvidenceFamilyStatus:
    family: str
    qualified_records: int
    required_records: int
    qualified_pools: tuple[str, ...]
    ready: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9EvidenceStatus:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    as_of: str
    phase8_current: bool
    fresh_api_pools: int
    stale_api_pools_excluded: int
    desired_pools: tuple[str, ...]
    research_pools: tuple[str, ...]
    sampling_pools: tuple[str, ...]
    missing_chain_pools: tuple[str, ...]
    chain_history_ready: bool
    max_history_samples_remaining: int
    mint_ready_pools: int
    mint_required_pools: int
    wallet_ready_pools: int
    wallet_required_pools: int
    explicit_inputs_valid: bool
    explicit_input_evidence_id: int | None
    research_sources_current: bool
    research_bundle_ready: bool
    pools: tuple[Phase9PoolEvidenceStatus, ...]
    families: tuple[Phase9EvidenceFamilyStatus, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _family(
    *,
    family: str,
    summary,
    required: int,
) -> Phase9EvidenceFamilyStatus:
    return Phase9EvidenceFamilyStatus(
        family=family,
        qualified_records=summary.qualified_records,
        required_records=required,
        qualified_pools=summary.qualified_pools,
        ready=summary.qualified_records >= required,
    )


def evaluate_phase9_evidence_status(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
    cohort_criteria: Phase9PoolCohortCriteria = (
        Phase9PoolCohortCriteria()
    ),
    mint_max_snapshot_age_seconds: int = 3600,
    wallet_criteria: WalletFlowCriteria = WalletFlowCriteria(),
    as_of: str | None = None,
) -> Phase9EvidenceStatus:
    evaluation_time = as_of or utc_now_iso()

    phase8 = audit_persisted_phase8_promotion(storage)
    cohort = evaluate_phase9_pool_cohort(
        storage,
        criteria=cohort_criteria,
        as_of=evaluation_time,
    )
    freshness = evaluate_phase9_source_freshness(
        storage,
        as_of=evaluation_time,
        required_mint_pools=criteria.min_mint_risk_pools,
        required_wallet_pools=criteria.min_wallet_flow_pools,
    )
    explicit = audit_phase9_explicit_inputs(storage)
    bundle = evaluate_phase9_research_bundle(
        storage,
        criteria=criteria,
    )

    mint_targets = tuple(
        cohort.sampling_pools[: criteria.min_mint_risk_pools]
    )
    wallet_targets = tuple(
        cohort.sampling_pools[: criteria.min_wallet_flow_pools]
    )

    pool_items: list[Phase9PoolEvidenceStatus] = []
    mint_ready = 0
    wallet_ready = 0

    for item in cohort.items:
        pool = item.pool_address
        mint_target = pool in mint_targets
        wallet_target = pool in wallet_targets

        mint_inputs_ready: bool | None = None
        mint_captures_required: int | None = None
        if mint_target:
            mint_plan = build_phase9_mint_capture_plan(
                storage,
                criteria=Phase9MintCaptureCriteria(
                    target_pools=1,
                    max_snapshot_age_seconds=(
                        mint_max_snapshot_age_seconds
                    ),
                    include_reward_mints=True,
                ),
                pool_addresses=(pool,),
                as_of=evaluation_time,
            )
            mint_inputs_ready = mint_plan.inputs_ready
            mint_captures_required = mint_plan.captures_required
            if mint_inputs_ready:
                mint_ready += 1

        wallet_events: int | None = None
        wallet_unique_users: int | None = None
        wallet_source_ready: bool | None = None
        if wallet_target:
            wallet = wallet_flow_source_state(
                storage,
                pool_address=pool,
                criteria=wallet_criteria,
                as_of=evaluation_time,
            )
            wallet_events = wallet.events
            wallet_unique_users = wallet.unique_users
            wallet_source_ready = wallet.ready
            if wallet_source_ready:
                wallet_ready += 1

        pool_items.append(
            Phase9PoolEvidenceStatus(
                pool_address=pool,
                rank=item.rank,
                chain_observations=item.chain_observations,
                required_chain_observations=(
                    cohort.required_observations
                ),
                chain_observations_remaining=max(
                    0,
                    cohort.required_observations
                    - item.chain_observations,
                ),
                history_ready=item.history_ready,
                mint_target=mint_target,
                mint_inputs_ready=mint_inputs_ready,
                mint_captures_required=mint_captures_required,
                wallet_target=wallet_target,
                wallet_events=wallet_events,
                wallet_unique_users=wallet_unique_users,
                wallet_source_ready=wallet_source_ready,
            )
        )

    families = (
        _family(
            family="adaptive_regime",
            summary=bundle.adaptive_multi_pool,
            required=1,
        ),
        _family(
            family="mint_risk",
            summary=bundle.mint_risk,
            required=criteria.min_mint_risk_pools,
        ),
        _family(
            family="wallet_flow",
            summary=bundle.wallet_flow,
            required=criteria.min_wallet_flow_pools,
        ),
        _family(
            family="portfolio_allocation",
            summary=bundle.portfolio_allocation,
            required=1 if criteria.require_portfolio_allocation else 0,
        ),
        _family(
            family="static_hedge",
            summary=bundle.static_hedge,
            required=criteria.min_static_hedge_pools,
        ),
        _family(
            family="contextual_bandit",
            summary=bundle.contextual_bandit,
            required=1 if criteria.require_contextual_bandit else 0,
        ),
    )

    sampling = set(cohort.sampling_pools)
    max_remaining = max(
        (
            item.chain_observations_remaining
            for item in pool_items
            if item.pool_address in sampling
        ),
        default=0,
    )

    reasons: list[str] = []
    if not phase8.current:
        reasons.append("Phase 8 promotion is not current")
    if cohort.api_pools_seen < cohort.criteria.min_research_pools:
        reasons.append(
            "fresh API-ranked pool coverage is below cohort minimum: "
            f"{cohort.api_pools_seen}/"
            f"{cohort.criteria.min_research_pools}"
        )
    if not cohort.research_ready:
        reasons.append(
            "ranked chain-history cohort is not research-ready"
        )
    if mint_ready < criteria.min_mint_risk_pools:
        reasons.append(
            f"ranked mint inputs ready {mint_ready}/"
            f"{criteria.min_mint_risk_pools}"
        )
    if wallet_ready < criteria.min_wallet_flow_pools:
        reasons.append(
            f"ranked wallet sources ready {wallet_ready}/"
            f"{criteria.min_wallet_flow_pools}"
        )
    if not explicit.valid:
        reasons.append(
            "checksum-bound explicit research inputs are not valid"
        )
    if not freshness.current:
        stale = ", ".join(
            item.family
            for item in freshness.families
            if not item.current
        )
        reasons.append(
            "research source refresh is pending"
            + (f": {stale}" if stale else "")
        )
    reasons.extend(bundle.reasons)

    return Phase9EvidenceStatus(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of=evaluation_time,
        phase8_current=phase8.current,
        fresh_api_pools=cohort.api_pools_seen,
        stale_api_pools_excluded=cohort.stale_api_pools_excluded,
        desired_pools=cohort.desired_pools,
        research_pools=cohort.research_pools,
        sampling_pools=cohort.sampling_pools,
        missing_chain_pools=cohort.missing_chain_pools,
        chain_history_ready=cohort.research_ready,
        max_history_samples_remaining=max_remaining,
        mint_ready_pools=mint_ready,
        mint_required_pools=criteria.min_mint_risk_pools,
        wallet_ready_pools=wallet_ready,
        wallet_required_pools=criteria.min_wallet_flow_pools,
        explicit_inputs_valid=explicit.valid,
        explicit_input_evidence_id=explicit.evidence_id,
        research_sources_current=freshness.current,
        research_bundle_ready=bundle.research_ready,
        pools=tuple(pool_items),
        families=families,
        reasons=tuple(dict.fromkeys(reasons)),
    )
