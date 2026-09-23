from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .phase9_evidence_status import (
    Phase9EvidenceStatus,
    evaluate_phase9_evidence_status,
)
from .phase9_pool_cohort import Phase9PoolCohortCriteria
from .phase9_validation import Phase9ResearchBundleCriteria
from .storage import Storage, utc_now_iso
from .wallet_flow import WalletFlowCriteria


@dataclass(frozen=True)
class Phase9EvidenceDebtItem:
    priority: int
    debt_type: str
    scope: str
    blocking: bool
    actionable: bool
    current: int | None
    required: int | None
    remaining: int | None
    shell_command: str | None
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9EvidencePlan:
    research_only: bool
    read_only_commands: bool
    policy_actionable: bool
    execution_wired: bool
    as_of: str
    research_bundle_ready: bool
    source_ready: bool
    history_capture_cycles_remaining: int
    history_interval_seconds: int
    next_action: Phase9EvidenceDebtItem | None
    items: tuple[Phase9EvidenceDebtItem, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _q(value: object) -> str:
    text = str(value)
    if text and all(
        char.isalnum() or char in "-_.,:/"
        for char in text
    ):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _source_ready(status: Phase9EvidenceStatus) -> bool:
    return bool(
        status.chain_history_ready
        and status.mint_ready_pools >= status.mint_required_pools
        and status.wallet_ready_pools >= status.wallet_required_pools
        and status.explicit_inputs_valid
    )


def build_phase9_evidence_plan(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
    cohort_criteria: Phase9PoolCohortCriteria = (
        Phase9PoolCohortCriteria()
    ),
    wallet_criteria: WalletFlowCriteria = WalletFlowCriteria(),
    mint_max_snapshot_age_seconds: int = 3600,
    history_interval_seconds: int = 3600,
    as_of: str | None = None,
) -> Phase9EvidencePlan:
    if history_interval_seconds < 0:
        raise ValueError("history_interval_seconds cannot be negative")

    evaluation_time = as_of or utc_now_iso()
    status = evaluate_phase9_evidence_status(
        storage,
        criteria=criteria,
        cohort_criteria=cohort_criteria,
        mint_max_snapshot_age_seconds=mint_max_snapshot_age_seconds,
        wallet_criteria=wallet_criteria,
        as_of=evaluation_time,
    )

    items: list[Phase9EvidenceDebtItem] = []

    if not status.phase8_current:
        items.append(
            Phase9EvidenceDebtItem(
                priority=5,
                debt_type="PHASE8_DEPENDENCY",
                scope="PHASE8_PROMOTION",
                blocking=True,
                actionable=False,
                current=0,
                required=1,
                remaining=1,
                shell_command=None,
                reason=(
                    "Phase 8 promotion is not current. Phase 9 source "
                    "collection can continue, but research-bundle readiness "
                    "cannot complete until Phase 8 is current."
                ),
            )
        )

    fresh_api_required = cohort_criteria.min_research_pools
    if status.fresh_api_pools < fresh_api_required:
        remaining = fresh_api_required - status.fresh_api_pools
        items.append(
            Phase9EvidenceDebtItem(
                priority=10,
                debt_type="API_POOL_COVERAGE",
                scope="METEORA_POOLS",
                blocking=True,
                actionable=True,
                current=status.fresh_api_pools,
                required=fresh_api_required,
                remaining=remaining,
                shell_command="pio collect-once",
                reason=(
                    "fresh ranked API pool coverage is below the Phase 9 "
                    f"cohort minimum by {remaining} pool(s)"
                ),
            )
        )

    if status.missing_chain_pools:
        items.append(
            Phase9EvidenceDebtItem(
                priority=20,
                debt_type="CHAIN_POOL_COVERAGE",
                scope=",".join(status.missing_chain_pools),
                blocking=True,
                actionable=True,
                current=None,
                required=len(status.missing_chain_pools),
                remaining=len(status.missing_chain_pools),
                shell_command=(
                    "pio phase9-chain-capture-run "
                    "--target-chain-pools "
                    + _q(cohort_criteria.min_research_pools)
                    + " --require-target"
                ),
                reason=(
                    "ranked cohort pools are missing authoritative read-only "
                    "chain snapshots: "
                    + ", ".join(status.missing_chain_pools)
                ),
            )
        )

    history_deficits = tuple(
        item
        for item in status.pools
        if (
            item.pool_address in status.sampling_pools
            and item.chain_observations_remaining > 0
        )
    )
    if history_deficits:
        scope = ",".join(item.pool_address for item in history_deficits)
        details = ", ".join(
            f"{item.pool_address}:{item.chain_observations_remaining}"
            for item in history_deficits
        )
        items.append(
            Phase9EvidenceDebtItem(
                priority=30,
                debt_type="CHAIN_HISTORY_DEPTH",
                scope=scope,
                blocking=True,
                actionable=True,
                current=None,
                required=None,
                remaining=status.max_history_samples_remaining,
                shell_command=(
                    "pio phase9-chain-history-run "
                    "--min-observation-interval-seconds "
                    + _q(history_interval_seconds)
                ),
                reason=(
                    "ranked sampling pools remain below exact history depth; "
                    "additional observations by pool: "
                    + details
                ),
            )
        )

    mint_deficits = tuple(
        item
        for item in status.pools
        if item.mint_target and not item.mint_inputs_ready
    )
    if mint_deficits:
        pools = tuple(item.pool_address for item in mint_deficits)
        captures = sum(
            int(item.mint_captures_required or 0)
            for item in mint_deficits
        )
        target_pools = tuple(
            item.pool_address
            for item in status.pools
            if item.mint_target
        )
        items.append(
            Phase9EvidenceDebtItem(
                priority=40,
                debt_type="MINT_INPUTS",
                scope=",".join(pools),
                blocking=True,
                actionable=True,
                current=status.mint_ready_pools,
                required=status.mint_required_pools,
                remaining=max(
                    0,
                    status.mint_required_pools
                    - status.mint_ready_pools,
                ),
                shell_command=(
                    "pio phase9-mint-capture-run --target-pools "
                    + _q(status.mint_required_pools)
                    + " --pools "
                    + _q(",".join(target_pools))
                    + " --max-snapshot-age-seconds "
                    + _q(mint_max_snapshot_age_seconds)
                    + " --require-ready"
                ),
                reason=(
                    f"{captures} missing/stale authoritative mint capture(s) "
                    "remain across ranked target pools"
                ),
            )
        )

    for pool in status.pools:
        if not pool.wallet_target or pool.wallet_source_ready:
            continue
        event_deficit = max(
            0,
            wallet_criteria.min_events - int(pool.wallet_events or 0),
        )
        user_deficit = max(
            0,
            (
                wallet_criteria.min_unique_users
                - int(pool.wallet_unique_users or 0)
            ),
        )
        items.append(
            Phase9EvidenceDebtItem(
                priority=50 + pool.rank,
                debt_type="WALLET_FLOW_SOURCE",
                scope=pool.pool_address,
                blocking=True,
                actionable=True,
                current=int(pool.wallet_events or 0),
                required=wallet_criteria.min_events,
                remaining=event_deficit,
                shell_command=(
                    "pio phase9-wallet-flow-capture-run --pool "
                    + _q(pool.pool_address)
                    + " --require-ready"
                ),
                reason=(
                    f"wallet-flow source deficit for {pool.pool_address}: "
                    f"events {event_deficit} remaining, unique users "
                    f"{user_deficit} remaining"
                ),
            )
        )

    if not status.explicit_inputs_valid:
        template_pools = tuple(status.sampling_pools[:3])
        command = "pio phase9-research-input-template"
        if template_pools:
            command += " --pools " + _q(",".join(template_pools))
        command += " > phase9-research-inputs.json"
        items.append(
            Phase9EvidenceDebtItem(
                priority=70,
                debt_type="EXPLICIT_RESEARCH_INPUTS",
                scope="USER_ASSUMPTIONS_REQUIRED",
                blocking=True,
                actionable=True,
                current=0,
                required=1,
                remaining=1,
                shell_command=command,
                reason=(
                    "checksum-bound hedge/portfolio economic assumptions are "
                    "missing or invalid; generate the template, fill every "
                    "null economic field, then ingest it"
                ),
            )
        )

    missing_families = tuple(
        family.family
        for family in status.families
        if not family.ready
    )
    if (
        missing_families
        or not status.research_sources_current
    ):
        items.append(
            Phase9EvidenceDebtItem(
                priority=80,
                debt_type="RESEARCH_REFRESH",
                scope=(
                    ",".join(missing_families)
                    if missing_families
                    else "SOURCE_STALE"
                ),
                blocking=True,
                actionable=True,
                current=sum(
                    int(family.ready) for family in status.families
                ),
                required=len(status.families),
                remaining=sum(
                    int(not family.ready)
                    for family in status.families
                ),
                shell_command="pio phase9-research-refresh-run",
                reason=(
                    "persisted Phase 9 research must be recomputed from the "
                    "current source corpus"
                    + (
                        "; missing/under-qualified families: "
                        + ", ".join(missing_families)
                        if missing_families
                        else ""
                    )
                ),
            )
        )

    if (
        not status.research_bundle_ready
        and not missing_families
        and status.research_sources_current
    ):
        items.append(
            Phase9EvidenceDebtItem(
                priority=90,
                debt_type="BUNDLE_REVALIDATION",
                scope="__PHASE9_RESEARCH_BUNDLE__",
                blocking=True,
                actionable=True,
                current=0,
                required=1,
                remaining=1,
                shell_command=(
                    "pio phase9-research-refresh-run --require-bundle-ready"
                ),
                reason=(
                    "component evidence is ready/current but the consolidated "
                    "research bundle still needs revalidation"
                ),
            )
        )

    items.sort(key=lambda item: (item.priority, item.scope))
    next_action = next(
        (item for item in items if item.actionable),
        items[0] if items else None,
    )

    source_ready = _source_ready(status)
    reasons = tuple(dict.fromkeys(
        tuple(status.reasons)
        + tuple(item.reason for item in items)
    ))

    return Phase9EvidencePlan(
        research_only=True,
        read_only_commands=True,
        policy_actionable=False,
        execution_wired=False,
        as_of=evaluation_time,
        research_bundle_ready=status.research_bundle_ready,
        source_ready=source_ready,
        history_capture_cycles_remaining=(
            status.max_history_samples_remaining
        ),
        history_interval_seconds=history_interval_seconds,
        next_action=next_action,
        items=tuple(items),
        reasons=reasons,
    )
