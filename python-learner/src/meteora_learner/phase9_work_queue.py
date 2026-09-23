from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import shlex
from typing import Any

from .phase8_validation import audit_persisted_phase8_promotion
from .phase9_capture_plan import (
    Phase9ChainCaptureCriteria,
    build_phase9_chain_capture_plan,
)
from .phase9_history_plan import build_phase9_history_plan
from .phase9_pool_cohort import evaluate_phase9_pool_cohort
from .phase9_source_freshness import (
    evaluate_phase9_source_freshness,
)
from .phase9_mint_capture import (
    Phase9MintCaptureCriteria,
    build_phase9_mint_capture_plan,
)
from .phase9_explicit_inputs import (
    audit_phase9_explicit_inputs,
    load_phase9_explicit_inputs,
)
from .phase9_wallet_flow_capture import wallet_flow_source_state
from .phase9_pool_activity_scan_state import (
    phase9_pool_activity_scan_state,
)
from .wallet_flow import WalletFlowCriteria
from .phase9_policy_authorization import (
    audit_persisted_phase9_policy_authorization,
    evaluate_phase9_policy_authorization,
)
from .phase9_policy_controlled_validation import (
    PHASE9_POLICY_CONTROLLED_VALIDATION_EVIDENCE_TYPE,
    PHASE9_POLICY_CONTROLLED_VALIDATION_SCOPE,
    audit_persisted_phase9_policy_controlled_validation,
    evaluate_phase9_policy_controlled_validation,
)
from .phase9_policy_rollout_simulation import (
    audit_persisted_phase9_policy_rollout_simulation,
)
from .phase9_policy_rollback_simulation import (
    audit_persisted_phase9_policy_rollback_simulation,
)
from .phase9_policy_prewire import evaluate_phase9_policy_prewire_audit
from .phase9_policy_manifest import (
    audit_persisted_phase9_policy_prewire_manifest,
)
from .phase9_shadow import evaluate_phase9_shadow
from .phase9_validation import (
    Phase9ResearchBundleCriteria,
    audit_persisted_phase9_promotion,
    evaluate_phase9_promotion,
    evaluate_phase9_research_bundle,
)
from .storage import Storage, utc_now_iso


DEFAULT_PUBKEY = "11111111111111111111111111111111"


@dataclass(frozen=True)
class Phase9WorkItem:
    task_type: str
    scope: str
    reason: str
    shell_command: str | None


@dataclass(frozen=True)
class Phase9WorkQueue:
    phase8_promoted: bool
    research_bundle_ready: bool
    promotion_ready: bool
    candidate_pools: tuple[str, ...]
    items: tuple[Phase9WorkItem, ...]
    phase9_current: bool = False
    policy_authorization_current: bool = False
    controlled_validation_current: bool = False
    rollout_simulation_current: bool = False
    rollback_simulation_current: bool = False
    prewire_ready: bool = False
    prewire_manifest_current: bool = False
    research_sources_current: bool = False

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9WorkQueueSnapshot:
    snapshot_id: int
    created_at: str
    queue_sha256: str
    task_count: int
    phase8_promoted: bool
    research_bundle_ready: bool
    promotion_ready: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _snapshot_state(queue: Phase9WorkQueue) -> dict[str, Any]:
    return {
        "phase8_promoted": queue.phase8_promoted,
        "research_bundle_ready": queue.research_bundle_ready,
        "promotion_ready": queue.promotion_ready,
        "phase9_current": queue.phase9_current,
        "policy_authorization_current": (
            queue.policy_authorization_current
        ),
        "controlled_validation_current": (
            queue.controlled_validation_current
        ),
        "rollout_simulation_current": (
            queue.rollout_simulation_current
        ),
        "rollback_simulation_current": (
            queue.rollback_simulation_current
        ),
        "prewire_ready": queue.prewire_ready,
        "prewire_manifest_current": queue.prewire_manifest_current,
        "research_sources_current": queue.research_sources_current,
        "candidate_pools": list(queue.candidate_pools),
        "items": [
            {
                "task_type": item.task_type,
                "scope": item.scope,
                "reason": item.reason,
            }
            for item in queue.items
        ],
    }


def persist_phase9_work_queue_snapshot(
    storage: Storage,
    *,
    queue: Phase9WorkQueue,
    criteria: Phase9ResearchBundleCriteria,
    created_at: str | None = None,
) -> Phase9WorkQueueSnapshot:
    timestamp = created_at or utc_now_iso()
    criteria_record = asdict(criteria)
    state = _snapshot_state(queue)
    canonical = json.dumps(
        {
            "criteria": criteria_record,
            "state": state,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    with storage.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO phase9_work_queue_snapshots(
                created_at, queue_sha256, criteria_json, state_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                timestamp,
                digest,
                json.dumps(
                    criteria_record,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    state,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        snapshot_id = int(cursor.lastrowid)

    return Phase9WorkQueueSnapshot(
        snapshot_id=snapshot_id,
        created_at=timestamp,
        queue_sha256=digest,
        task_count=len(queue.items),
        phase8_promoted=queue.phase8_promoted,
        research_bundle_ready=queue.research_bundle_ready,
        promotion_ready=queue.promotion_ready,
    )


def _q(value: object) -> str:
    return shlex.quote(str(value))


def _candidate_pools(
    storage: Storage,
    *,
    limit: int = 8,
    as_of: str | None = None,
) -> tuple[str, ...]:
    with storage.connect() as conn:
        if as_of is None:
            rows = conn.execute(
                """
                SELECT pool_address, COUNT(*) AS observations
                FROM chain_pool_snapshots
                GROUP BY pool_address
                ORDER BY observations DESC, pool_address ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT pool_address, COUNT(*) AS observations
                FROM chain_pool_snapshots
                WHERE julianday(observed_at) <= julianday(?)
                GROUP BY pool_address
                ORDER BY observations DESC, pool_address ASC
                LIMIT ?
                """,
                (as_of, limit),
            ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _latest_pool_mints(
    storage: Storage,
    *,
    pool_address: str,
) -> tuple[str, ...]:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT token_x_mint, token_y_mint,
                   reward_mint_0, reward_mint_1
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (pool_address,),
        ).fetchone()
    if row is None:
        return ()
    values: list[str] = []
    for raw in row:
        if raw is None:
            continue
        mint = str(raw).strip()
        if not mint or mint == DEFAULT_PUBKEY:
            continue
        if mint not in values:
            values.append(mint)
    return tuple(values)


def _mint_snapshot_exists(
    storage: Storage,
    *,
    mint_address: str,
) -> bool:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM token_mint_snapshots
            WHERE mint_address = ?
            LIMIT 1
            """,
            (mint_address,),
        ).fetchone()
    return row is not None


def _latest_retraining_dataset_cycle(
    storage: Storage,
) -> str | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT evidence_json
            FROM model_live_evidence
            WHERE evidence_type = 'CONTINUOUS_RETRAIN_DATASET_V1'
              AND status = 'BUILT'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(str(row[0]))
    cycle_id = str(payload.get("cycle_id", "")).strip()
    target_version = str(
        payload.get("target_dataset_version", "")
    ).strip()
    output_file = str(payload.get("output_file", "")).strip()
    dataset = payload.get("dataset")
    if (
        not cycle_id
        or not target_version
        or not output_file
        or not isinstance(dataset, dict)
        or not str(dataset.get("dataset_sha256", "")).strip()
        or str(dataset.get("dataset_version", "")).strip()
        != target_version
    ):
        return None
    return cycle_id


def _latest_controlled_validation_cycle(
    storage: Storage,
) -> str | None:
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_CONTROLLED_VALIDATION_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_CONTROLLED_VALIDATION_SCOPE,
    )
    if latest is None:
        return None
    evidence = latest.get("evidence")
    if not isinstance(evidence, dict):
        return None
    cycle_id = str(evidence.get("cycle_id", "")).strip()
    return cycle_id or None


def build_phase9_work_queue(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
    rpc_url: str | None = None,
    as_of: str | None = None,
) -> Phase9WorkQueue:
    phase8_audit = audit_persisted_phase8_promotion(storage)
    phase8_promoted = phase8_audit.current
    bundle = evaluate_phase9_research_bundle(
        storage,
        criteria=criteria,
    )
    promotion = evaluate_phase9_promotion(
        storage,
        criteria=criteria,
        research_bundle=bundle,
    )
    promotion_audit = audit_persisted_phase9_promotion(
        storage,
        criteria=criteria,
        current_report=promotion,
    )
    source_freshness_report = evaluate_phase9_source_freshness(storage)
    source_freshness = source_freshness_report.by_family()
    required_source_ready = {
        "adaptive_regime": (
            not criteria.require_adaptive_multi_pool
            or bundle.adaptive_multi_pool.qualified_records >= 1
        ),
        "mint_risk": (
            bundle.mint_risk.qualified_records
            >= criteria.min_mint_risk_pools
        ),
        "wallet_flow": (
            bundle.wallet_flow.qualified_records
            >= criteria.min_wallet_flow_pools
        ),
        "portfolio_allocation": (
            not criteria.require_portfolio_allocation
            or bundle.portfolio_allocation.qualified_records >= 1
        ),
        "static_hedge": (
            bundle.static_hedge.qualified_records
            >= criteria.min_static_hedge_pools
        ),
        "contextual_bandit": (
            not criteria.require_contextual_bandit
            or bundle.contextual_bandit.qualified_records >= 1
        ),
    }
    research_sources_current = all(
        required_source_ready[family]
        and source_freshness.get(family, False)
        for family in required_source_ready
    )
    pools = _candidate_pools(
        storage,
        as_of=as_of,
    )
    live_as_of = as_of or utc_now_iso()
    live_cohort = (
        evaluate_phase9_pool_cohort(
            storage,
            as_of=live_as_of,
        )
        if as_of is None
        else None
    )
    items: list[Phase9WorkItem] = []
    stale_source_families = tuple(
        item.family
        for item in source_freshness_report.families
        if not item.current
        and (
            (
                item.family == "adaptive_regime"
                and bundle.adaptive_multi_pool.qualified_records > 0
            )
            or (
                item.family == "mint_risk"
                and bundle.mint_risk.qualified_records > 0
            )
            or (
                item.family == "wallet_flow"
                and bundle.wallet_flow.qualified_records > 0
            )
            or (
                item.family == "portfolio_allocation"
                and bundle.portfolio_allocation.qualified_records > 0
            )
            or (
                item.family == "static_hedge"
                and bundle.static_hedge.qualified_records > 0
            )
            or (
                item.family == "contextual_bandit"
                and bundle.contextual_bandit.qualified_records > 0
            )
        )
    )
    if stale_source_families:
        detail = "; ".join(
            item.reason
            for item in source_freshness_report.families
            if item.family in stale_source_families
        )
        items.append(
            Phase9WorkItem(
                task_type="RESEARCH_SOURCE_REFRESH",
                scope=",".join(stale_source_families),
                reason=(
                    "persisted Phase 9 research remains replay-valid but "
                    "newer source evidence is available: " + detail
                ),
                shell_command="pio phase9-research-refresh-run",
            )
        )

    phase9_current = promotion_audit.current
    policy_authorization_current = False
    controlled_validation_current = False
    rollout_simulation_current = False
    rollback_simulation_current = False
    prewire_ready = False
    prewire_manifest_current = False

    if not bundle.storage_integrity_verified:
        items.append(
            Phase9WorkItem(
                task_type="STORAGE_INTEGRITY",
                scope="PHASE9_STORAGE",
                reason=(
                    "Phase 9 immutable source/evidence storage integrity "
                    "must be restored before research can be ready"
                ),
                shell_command=(
                    "pio phase9-storage-integrity --require-verified"
                ),
            )
        )

    if not phase8_promoted:
        if phase8_audit.exists:
            items.append(
                Phase9WorkItem(
                    task_type="PHASE8_CURRENTNESS_REQUIRED",
                    scope="PHASE8",
                    reason=(
                        "persisted Phase 8 promotion is stale or invalid: "
                        + "; ".join(phase8_audit.reasons)
                    ),
                    shell_command=(
                        "pio phase8-promotion-audit --require-current"
                    ),
                )
            )
        else:
            items.append(
                Phase9WorkItem(
                    task_type="PHASE8_PROMOTION_REQUIRED",
                    scope="PHASE8",
                    reason=(
                        "Phase 8 must be promoted before "
                        "Phase 9 research can qualify"
                    ),
                    shell_command=(
                        "pio phase8-validate "
                        "--require-ready --persist-ready"
                    ),
                )
            )

    if (
        live_cohort is not None
        and live_cohort.api_pools_seen > 0
    ):
        if live_cohort.missing_chain_pools:
            items.append(
                Phase9WorkItem(
                    task_type="RANKED_POOL_ONBOARDING",
                    scope=",".join(live_cohort.missing_chain_pools),
                    reason=(
                        "the ranked Phase 9 evidence cohort still has "
                        "top-ranked pool(s) without chain snapshots: "
                        + ", ".join(live_cohort.missing_chain_pools)
                    ),
                    shell_command="pio phase9-source-capture-run",
                )
            )
        shallow_ranked = tuple(
            item.pool_address
            for item in live_cohort.items
            if (
                item.pool_address in live_cohort.desired_pools
                and item.chain_observed
                and not item.history_ready
            )
        )
        if shallow_ranked:
            items.append(
                Phase9WorkItem(
                    task_type="RANKED_POOL_HISTORY",
                    scope=",".join(shallow_ranked),
                    reason=(
                        "ranked Phase 9 pool(s) are chain-observed but still "
                        f"below the {live_cohort.required_observations}-"
                        "observation research depth: "
                        + ", ".join(shallow_ranked)
                    ),
                    shell_command="pio phase9-source-capture-run",
                )
            )

    if len(pools) < 3:
        if as_of is not None:
            items.append(
                Phase9WorkItem(
                    task_type="HISTORICAL_CHAIN_POOL_GAP",
                    scope="PHASE9_CHAIN_POOLS",
                    reason=(
                        f"fewer than three chain-observed pools existed at "
                        f"cutoff {as_of}; later chain state cannot backfill "
                        "the historical corpus"
                    ),
                    shell_command=None,
                )
            )
        else:
            capture_plan = build_phase9_chain_capture_plan(
                storage,
                criteria=Phase9ChainCaptureCriteria(
                    target_chain_pools=3,
                    max_candidates=8,
                    bin_array_radius=1,
                ),
                rpc_url=rpc_url,
                as_of=live_as_of,
            )
            if capture_plan.candidates:
                planner_command = "pio phase9-chain-capture-plan"
                if rpc_url is not None:
                    planner_command += " --rpc-url " + _q(rpc_url)
                planner_command += " --require-ready"
                items.append(
                    Phase9WorkItem(
                        task_type="CHAIN_POOL_CAPTURE_PLAN",
                        scope="PHASE9_CHAIN_POOLS",
                        reason=(
                            f"{capture_plan.additional_chain_pools_needed} "
                            "additional chain-observed pool(s) are required; "
                            "discovered API candidates are available for "
                            "read-only Rust capture"
                        ),
                        shell_command=planner_command,
                    )
                )
            else:
                items.append(
                    Phase9WorkItem(
                        task_type="API_POOL_DISCOVERY",
                        scope="METEORA_POOLS",
                        reason=(
                            "fewer than three chain-observed pools are "
                            "available and no uncaptured API-discovered "
                            "candidates exist"
                        ),
                        shell_command="pio collect-once",
                    )
                )

    if bundle.adaptive_multi_pool.qualified_records < 1:
        command = None
        adaptive_reason = (
            "at least three persisted chain-history pools are needed"
        )
        selected_research_pools = (
            live_cohort.research_pools
            if live_cohort is not None and live_cohort.research_ready
            else ()
        )
        if selected_research_pools:
            command = (
                "pio phase9-research-validate --pools "
                + _q(",".join(selected_research_pools))
                + " --persist --require-qualified"
            )
            adaptive_reason = (
                "qualified adaptive/regime multi-pool evidence is missing; "
                "the ranked history-ready research cohort is available"
            )
        elif len(pools) >= 3:
            history_plan = build_phase9_history_plan(
                storage,
                rpc_url=rpc_url,
                as_of=as_of,
            )
            if history_plan.plan_ready:
                command = (
                    "pio phase9-research-validate --pools "
                    + _q(",".join(
                        item.pool_address
                        for item in history_plan.pools
                    ))
                )
                if as_of is not None:
                    command += " --as-of " + _q(as_of)
                command += " --persist --require-qualified"
                adaptive_reason = (
                    "qualified adaptive/regime multi-pool evidence is missing"
                )
            else:
                deficits = tuple(
                    (
                        item.pool_address,
                        item.additional_observations_needed,
                    )
                    for item in history_plan.pools
                    if item.additional_observations_needed > 0
                )
                detail = ", ".join(
                    f"{pool}:{needed}"
                    for pool, needed in deficits
                )
                history_command = None
                if as_of is None:
                    history_command = "pio phase9-chain-history-plan"
                    if rpc_url is not None:
                        history_command += " --rpc-url " + _q(rpc_url)
                    history_command += " --require-ready"
                items.append(
                    Phase9WorkItem(
                        task_type="CHAIN_HISTORY_DEPTH",
                        scope="__MULTI_POOL__",
                        reason=(
                            "adaptive/regime research history is below the "
                            "exact walk-forward requirement"
                            + (
                                f"; additional snapshots by pool: {detail}"
                                if detail
                                else ""
                            )
                            + (
                                f"; cutoff {as_of} cannot be backfilled with "
                                "later chain snapshots"
                                if as_of is not None
                                else ""
                            )
                        ),
                        shell_command=history_command,
                    )
                )
                adaptive_reason = (
                    "exact chain-history depth must be satisfied before "
                    "adaptive/regime research can run"
                )
        items.append(
            Phase9WorkItem(
                task_type="ADAPTIVE_MULTI_POOL",
                scope="__MULTI_POOL__",
                reason=adaptive_reason,
                shell_command=command,
            )
        )

    qualified_mint = set(bundle.mint_risk.qualified_pools)
    mint_needed = max(
        0,
        criteria.min_mint_risk_pools
        - bundle.mint_risk.qualified_records,
    )
    mint_candidate_pools = [
        value for value in pools if value not in qualified_mint
    ][:mint_needed]
    for pool in mint_candidate_pools:
        mint_plan = build_phase9_mint_capture_plan(
            storage,
            criteria=Phase9MintCaptureCriteria(
                target_pools=1,
                max_snapshot_age_seconds=3600,
                include_reward_mints=True,
            ),
            pool_addresses=(pool,),
            as_of=as_of,
        )
        if not mint_plan.candidates:
            items.append(
                Phase9WorkItem(
                    task_type="MINT_RISK",
                    scope=pool,
                    reason=(
                        "latest chain pool snapshot does not expose token "
                        "mints needed for mint-risk research"
                    ),
                    shell_command=None,
                )
            )
            continue

        if mint_plan.captures_required:
            details = "; ".join(
                f"{candidate.mint_address}: {candidate.reason}"
                for candidate in mint_plan.candidates
                if candidate.capture_required
            )
            if as_of is not None:
                capture_command = None
                reason = (
                    f"pool {pool} lacks mint snapshots valid at historical "
                    f"cutoff {as_of}; authoritative state captured later "
                    "cannot backfill that cutoff"
                    + (f": {details}" if details else "")
                )
            else:
                prefix = (
                    "SOLANA_RPC_URL="
                    + _q(rpc_url)
                    + " "
                    if rpc_url is not None
                    else ""
                )
                capture_command = (
                    prefix
                    + "pio phase9-mint-capture-run "
                    + "--target-pools 1 --pools "
                    + _q(pool)
                    + " --require-ready"
                )
                reason = (
                    f"pool {pool} requires fresh authoritative mint "
                    "snapshots before mint-risk research can run"
                    + (f": {details}" if details else "")
                )
            items.append(
                Phase9WorkItem(
                    task_type="MINT_SNAPSHOT",
                    scope=pool,
                    reason=reason,
                    shell_command=capture_command,
                )
            )
            continue

        risk_command = (
            "pio mint-risk-research --pool "
            + _q(pool)
        )
        if as_of is not None:
            risk_command += " --as-of " + _q(as_of)
        risk_command += " --persist --require-qualified"
        items.append(
            Phase9WorkItem(
                task_type="MINT_RISK",
                scope=pool,
                reason="qualified persisted mint-risk evidence is needed",
                shell_command=risk_command,
            )
        )
    if mint_needed > len(mint_candidate_pools):
        items.append(
            Phase9WorkItem(
                task_type="MINT_RISK",
                scope="POOL_REQUIRED",
                reason=(
                    f"{mint_needed - len(mint_candidate_pools)} additional "
                    "chain-observed pool(s) are required for mint-risk evidence"
                ),
                shell_command=None,
            )
        )

    qualified_wallet = set(bundle.wallet_flow.qualified_pools)
    wallet_needed = max(
        0,
        criteria.min_wallet_flow_pools
        - bundle.wallet_flow.qualified_records,
    )
    wallet_candidate_pools = [
        value for value in pools if value not in qualified_wallet
    ][:wallet_needed]
    for pool in wallet_candidate_pools:
        source = wallet_flow_source_state(
            storage,
            pool_address=pool,
            criteria=WalletFlowCriteria(),
            as_of=as_of,
        )
        if not source.ready:
            if as_of is None:
                scan_state = phase9_pool_activity_scan_state(
                    storage,
                    pool_address=pool,
                )
                if scan_state.pages_scanned == 0:
                    scan_detail = (
                        "historical pool-signature backfill has not started"
                    )
                elif scan_state.backfill_exhausted:
                    scan_detail = (
                        "historical pool-signature backfill is exhausted "
                        f"after {scan_state.pages_scanned} page(s) and "
                        f"{scan_state.signatures_scanned} signature(s); "
                        "recent live rescans remain available"
                    )
                else:
                    scan_detail = (
                        "historical pool-signature backfill is in progress: "
                        f"{scan_state.pages_scanned} page(s), "
                        f"{scan_state.signatures_scanned} signature(s), "
                        f"{scan_state.positions_discovered} position "
                        "candidate(s) discovered"
                    )
            else:
                scan_detail = (
                    "live pool-signature backfill is disabled at a "
                    f"historical cutoff ({as_of}) to avoid lookahead"
                )
            prefix = (
                "SOLANA_RPC_URL="
                + _q(rpc_url)
                + " "
                if rpc_url is not None
                else ""
            )
            capture_command = (
                prefix
                + "pio phase9-wallet-flow-capture-run --pool "
                + _q(pool)
            )
            if as_of is not None:
                capture_command += " --as-of " + _q(as_of)
            capture_command += " --require-ready"
            items.append(
                Phase9WorkItem(
                    task_type="WALLET_FLOW_CAPTURE",
                    scope=pool,
                    reason=(
                        "wallet-flow source corpus is below the default "
                        "event/user threshold: "
                        f"events {source.events}/20, "
                        f"unique users {source.unique_users}/5; "
                        + scan_detail
                    ),
                    shell_command=capture_command,
                )
            )
            items.append(
                Phase9WorkItem(
                    task_type="WALLET_FLOW",
                    scope=pool,
                    reason=(
                        "wallet-flow research is blocked until real "
                        "position-history source thresholds are satisfied"
                    ),
                    shell_command=None,
                )
            )
            continue

        command = (
            "pio wallet-flow-research --pool "
            + _q(pool)
        )
        if as_of is not None:
            command += " --as-of " + _q(as_of)
        command += " --persist --require-qualified"
        items.append(
            Phase9WorkItem(
                task_type="WALLET_FLOW",
                scope=pool,
                reason="qualified persisted wallet-flow evidence is needed",
                shell_command=command,
            )
        )
    if wallet_needed > len(wallet_candidate_pools):
        items.append(
            Phase9WorkItem(
                task_type="WALLET_FLOW",
                scope="POOL_REQUIRED",
                reason=(
                    f"{wallet_needed - len(wallet_candidate_pools)} "
                    "additional chain-observed pool(s) are required for "
                    "wallet-flow evidence"
                ),
                shell_command=None,
            )
        )

    adaptive_lineage_invalid = any(
        "immutable chain snapshot IDs with matching source hashes"
        in reason
        for reason in bundle.reasons
    )
    if adaptive_lineage_invalid:
        if pools:
            joined = ",".join(pools)
            items.append(
                Phase9WorkItem(
                    task_type="ADAPTIVE_MULTI_POOL_REPAIR",
                    scope="__MULTI_POOL__",
                    reason=(
                        "qualified adaptive/regime evidence does not "
                        "reproduce from its persisted chain snapshots"
                    ),
                    shell_command=(
                        "pio phase9-research-validate --pools "
                        + _q(joined)
                        + " --persist"
                    ),
                )
            )
        else:
            items.append(
                Phase9WorkItem(
                    task_type="ADAPTIVE_MULTI_POOL_REPAIR",
                    scope="POOL_REQUIRED",
                    reason=(
                        "adaptive/regime lineage is invalid and no "
                        "chain-observed pool corpus is available"
                    ),
                    shell_command=None,
                )
            )

    mint_lineage_invalid = any(
        "authoritative pool and mint snapshot IDs" in reason
        for reason in bundle.reasons
    )
    if mint_lineage_invalid:
        for pool in bundle.mint_risk.qualified_pools:
            mint_plan = build_phase9_mint_capture_plan(
                storage,
                criteria=Phase9MintCaptureCriteria(
                    target_pools=1,
                    max_snapshot_age_seconds=3600,
                    include_reward_mints=True,
                ),
                pool_addresses=(pool,),
                as_of=as_of,
            )
            if mint_plan.captures_required:
                details = "; ".join(
                    f"{candidate.mint_address}: {candidate.reason}"
                    for candidate in mint_plan.candidates
                    if candidate.capture_required
                )
                if as_of is not None:
                    command = None
                    reason = (
                        "qualified mint-risk lineage cannot be repaired at "
                        f"historical cutoff {as_of} because required "
                        "authoritative mint state is missing or stale"
                        + (f": {details}" if details else "")
                    )
                else:
                    prefix = (
                        "SOLANA_RPC_URL="
                        + _q(rpc_url)
                        + " "
                        if rpc_url is not None
                        else ""
                    )
                    command = (
                        prefix
                        + "pio phase9-mint-capture-run "
                        + "--target-pools 1 --pools "
                        + _q(pool)
                        + " --require-ready"
                    )
                    reason = (
                        f"qualified mint-risk evidence for {pool} needs "
                        "fresh authoritative mint snapshots before lineage "
                        "can be rebuilt"
                        + (f": {details}" if details else "")
                    )
                items.append(
                    Phase9WorkItem(
                        task_type="MINT_SNAPSHOT_REPAIR",
                        scope=pool,
                        reason=reason,
                        shell_command=command,
                    )
                )
            else:
                command = (
                    "pio mint-risk-research --pool "
                    + _q(pool)
                )
                if as_of is not None:
                    command += " --as-of " + _q(as_of)
                command += " --persist --require-qualified"
                items.append(
                    Phase9WorkItem(
                        task_type="MINT_RISK_REPAIR",
                        scope=pool,
                        reason=(
                            "qualified mint-risk evidence is not bound to "
                            "valid authoritative snapshot IDs"
                        ),
                        shell_command=command,
                    )
                )

    wallet_lineage_invalid = any(
        "position-event IDs with matching source hash" in reason
        for reason in bundle.reasons
    )
    if wallet_lineage_invalid:
        for pool in bundle.wallet_flow.qualified_pools:
            items.append(
                Phase9WorkItem(
                    task_type="WALLET_FLOW_REPAIR",
                    scope=pool,
                    reason=(
                        "qualified wallet-flow evidence does not reproduce "
                        "from its persisted source-event window"
                    ),
                    shell_command=(
                        "pio wallet-flow-research --pool "
                        + _q(pool)
                        + " --persist --require-qualified"
                    ),
                )
            )

    explicit_static_needed = (
        bundle.static_hedge.qualified_records
        < criteria.min_static_hedge_pools
    )
    explicit_allocation_needed = (
        criteria.require_portfolio_allocation
        and bundle.portfolio_allocation.qualified_records < 1
    )
    if explicit_static_needed or explicit_allocation_needed:
        explicit_audit = audit_phase9_explicit_inputs(storage)
        explicit_artifact = (
            load_phase9_explicit_inputs(
                storage,
                evidence_id=explicit_audit.evidence_id,
            )
            if explicit_audit.valid
            and explicit_audit.evidence_id is not None
            else None
        )
        missing_families = []
        if explicit_static_needed:
            missing_families.append("static hedge")
        if explicit_allocation_needed:
            missing_families.append("portfolio allocation")

        if explicit_artifact is None:
            template_command = "pio phase9-research-input-template"
            if pools:
                template_command += " --pools " + _q(
                    ",".join(pools[:3])
                )
            template_command += " > phase9-research-inputs.json"
            items.append(
                Phase9WorkItem(
                    task_type="EXPLICIT_RESEARCH_INPUTS",
                    scope="USER_ASSUMPTIONS_REQUIRED",
                    reason=(
                        " and ".join(missing_families)
                        + " require explicit economic assumptions. "
                        + (
                            "The latest input artifact is invalid: "
                            + "; ".join(explicit_audit.reasons)
                            + ". "
                            if explicit_audit.exists
                            else ""
                        )
                        + "Generate the template, fill every null economic "
                        "field, then persist it with "
                        "pio phase9-research-inputs-ingest "
                        "--file phase9-research-inputs.json"
                    ),
                    shell_command=template_command,
                )
            )
        else:
            items.append(
                Phase9WorkItem(
                    task_type="EXPLICIT_RESEARCH_RUN",
                    scope=str(explicit_artifact.evidence_id),
                    reason=(
                        "checksum-bound explicit inputs are available for "
                        + " and ".join(missing_families)
                    ),
                    shell_command=(
                        "pio phase9-explicit-research-run "
                        "--input-evidence-id "
                        + _q(explicit_artifact.evidence_id)
                        + " --persist --require-ready"
                    ),
                )
            )

    hedge_lineage_invalid = any(
        "pool/bin price-path IDs" in reason
        for reason in bundle.reasons
    )
    allocation_lineage_invalid = any(
        "immutable candidate-artifact lineage" in reason
        for reason in bundle.reasons
    )
    if hedge_lineage_invalid or allocation_lineage_invalid:
        explicit_repair_audit = audit_phase9_explicit_inputs(storage)
        repair_families = []
        if hedge_lineage_invalid:
            repair_families.append("static hedge")
        if allocation_lineage_invalid:
            repair_families.append("portfolio allocation")

        if (
            explicit_repair_audit.valid
            and explicit_repair_audit.evidence_id is not None
        ):
            items.append(
                Phase9WorkItem(
                    task_type="EXPLICIT_RESEARCH_REPAIR",
                    scope=str(explicit_repair_audit.evidence_id),
                    reason=(
                        "qualified "
                        + " and ".join(repair_families)
                        + " evidence has invalid lineage; rerun from the "
                        "current checksum-bound explicit input artifact"
                    ),
                    shell_command=(
                        "pio phase9-explicit-research-run "
                        "--input-evidence-id "
                        + _q(explicit_repair_audit.evidence_id)
                        + " --persist --require-ready"
                    ),
                )
            )
        else:
            template_command = "pio phase9-research-input-template"
            if pools:
                template_command += " --pools " + _q(
                    ",".join(pools[:3])
                )
            template_command += " > phase9-research-inputs.json"
            items.append(
                Phase9WorkItem(
                    task_type="EXPLICIT_RESEARCH_INPUTS_REPAIR",
                    scope="USER_ASSUMPTIONS_REQUIRED",
                    reason=(
                        "qualified "
                        + " and ".join(repair_families)
                        + " evidence has invalid lineage, but no valid "
                        "checksum-bound explicit input artifact is available"
                        + (
                            ": " + "; ".join(
                                explicit_repair_audit.reasons
                            )
                            if explicit_repair_audit.reasons
                            else ""
                        )
                    ),
                    shell_command=template_command,
                )
            )

    bandit_explicit_audit = audit_phase9_explicit_inputs(storage)
    bandit_explicit_artifact = None
    if (
        bandit_explicit_audit.valid
        and bandit_explicit_audit.evidence_id is not None
    ):
        bandit_explicit_artifact = load_phase9_explicit_inputs(
            storage,
            evidence_id=bandit_explicit_audit.evidence_id,
        )
    bandit_explicit_ready = (
        bandit_explicit_artifact is not None
        and len(bandit_explicit_artifact.inputs.pool_inputs) >= 3
    )

    if (
        criteria.require_contextual_bandit
        and bundle.contextual_bandit.qualified_records < 1
    ):
        cycle_id = _latest_retraining_dataset_cycle(storage)
        if cycle_id is not None:
            items.append(
                Phase9WorkItem(
                    task_type="CONTEXTUAL_BANDIT",
                    scope=cycle_id,
                    reason=(
                        "qualified contextual-bandit evidence is missing; "
                        "a checksum-bound retraining dataset is available"
                    ),
                    shell_command=(
                        "pio contextual-bandit-cycle-research "
                        "--cycle-id "
                        + _q(cycle_id)
                        + " --persist --require-qualified"
                    ),
                )
            )
        elif bandit_explicit_ready:
            items.append(
                Phase9WorkItem(
                    task_type="CONTEXTUAL_BANDIT",
                    scope=str(bandit_explicit_artifact.evidence_id),
                    reason=(
                        "qualified contextual-bandit evidence is missing; "
                        "a valid checksum-bound explicit input artifact with "
                        "at least three pools can derive the fully labeled "
                        "counterfactual action dataset from persisted chain "
                        "history"
                    ),
                    shell_command=(
                        "pio phase9-bandit-research-run "
                        "--input-evidence-id "
                        + _q(bandit_explicit_artifact.evidence_id)
                        + " --persist --require-qualified"
                    ),
                )
            )
        else:
            detail = (
                "; ".join(bandit_explicit_audit.reasons)
                if bandit_explicit_audit.reasons
                else ""
            )
            items.append(
                Phase9WorkItem(
                    task_type="CONTEXTUAL_BANDIT",
                    scope="CHECKSUM_BOUND_DATASET_REQUIRED",
                    reason=(
                        "contextual-bandit qualification requires either a "
                        "checksum-bound retraining-cycle dataset or a valid "
                        "Phase 9 explicit input artifact containing at least "
                        "three pools so labels can be derived from persisted "
                        "no-lookahead chain replay"
                        + (": " + detail if detail else "")
                    ),
                    shell_command=None,
                )
            )

    bandit_lineage_invalid = any(
        "checksum-verified dataset lineage" in reason
        for reason in bundle.reasons
    )
    if bandit_lineage_invalid:
        cycle_id = _latest_retraining_dataset_cycle(storage)
        if cycle_id is not None:
            repair_scope = cycle_id
            repair_command = (
                "pio contextual-bandit-cycle-research "
                "--cycle-id "
                + _q(cycle_id)
                + " --persist --require-qualified"
            )
            repair_reason = (
                "qualified contextual-bandit evidence is not bound to "
                "valid checksum-verified retraining-cycle dataset lineage"
            )
        elif bandit_explicit_ready:
            repair_scope = str(bandit_explicit_artifact.evidence_id)
            repair_command = (
                "pio phase9-bandit-research-run "
                "--input-evidence-id "
                + _q(bandit_explicit_artifact.evidence_id)
                + " --persist --require-qualified"
            )
            repair_reason = (
                "qualified contextual-bandit evidence has invalid dataset "
                "lineage; rebuild it from the current checksum-bound Phase 9 "
                "explicit inputs and persisted chain history"
            )
        else:
            repair_scope = "CHECKSUM_BOUND_DATASET_REQUIRED"
            repair_command = None
            repair_reason = (
                "qualified contextual-bandit evidence has invalid dataset "
                "lineage and no valid repair source is available"
            )
        items.append(
            Phase9WorkItem(
                task_type="CONTEXTUAL_BANDIT_REPAIR",
                scope=repair_scope,
                reason=repair_reason,
                shell_command=repair_command,
            )
        )

    if bundle.research_ready and not promotion.promotion_ready:
        if promotion.research_bundle_evidence_id is None:
            items.append(
                Phase9WorkItem(
                    task_type="PERSIST_RESEARCH_BUNDLE",
                    scope="__PHASE9_RESEARCH__",
                    reason=(
                        "current research bundle is ready but has not been "
                        "persisted as immutable evidence"
                    ),
                    shell_command=(
                        "pio phase9-research-bundle "
                        "--persist --require-ready"
                    ),
                )
            )
        elif not promotion.persisted_bundle_matches_current:
            items.append(
                Phase9WorkItem(
                    task_type="REFRESH_RESEARCH_BUNDLE",
                    scope="__PHASE9_RESEARCH__",
                    reason=(
                        "persisted research bundle is stale versus the "
                        "latest component evidence"
                    ),
                    shell_command=(
                        "pio phase9-research-bundle "
                        "--persist --require-ready"
                    ),
                )
            )

    if promotion.promotion_ready and not promotion_audit.current:
        items.append(
            Phase9WorkItem(
                task_type="PERSIST_PHASE9_PROMOTION",
                scope="PHASE9",
                reason=(
                    "all non-actionable research promotion gates pass and "
                    "persisted Phase 9 promotion is missing or stale"
                ),
                shell_command=(
                    "pio phase9-validate "
                    "--persist-ready --require-ready"
                ),
            )
        )

    if phase9_current:
        authorization_audit = (
            audit_persisted_phase9_policy_authorization(storage)
        )
        policy_authorization_current = authorization_audit.current

        if not policy_authorization_current:
            authorization = evaluate_phase9_policy_authorization(storage)
            if authorization.authorization_ready:
                items.append(
                    Phase9WorkItem(
                        task_type="PERSIST_POLICY_AUTHORIZATION",
                        scope="PHASE9_POLICY_AUTHORIZATION",
                        reason=(
                            "current replay-verified shadow evidence passes "
                            "the authorization gate but persisted authorization "
                            "evidence is missing or stale"
                        ),
                        shell_command=(
                            "pio phase9-policy-authorization-gate "
                            "--persist --require-ready"
                        ),
                    )
                )
            else:
                latest_cycle = _latest_retraining_dataset_cycle(storage)
                candidate_report = None
                if latest_cycle is not None:
                    candidate_report = evaluate_phase9_shadow(
                        storage,
                        cycle_id=latest_cycle,
                    )
                if (
                    latest_cycle is not None
                    and candidate_report is not None
                    and candidate_report.shadow_ready
                ):
                    items.append(
                        Phase9WorkItem(
                            task_type="PHASE9_SHADOW_VALIDATION",
                            scope=latest_cycle,
                            reason=(
                                "authorization evidence is not ready; the "
                                "latest checksum-bound retraining cycle can "
                                "supply or refresh qualifying shadow evidence"
                            ),
                            shell_command=(
                                "pio phase9-shadow-validate --cycle-id "
                                + _q(latest_cycle)
                                + " --persist --require-ready"
                            ),
                        )
                    )
                else:
                    details = list(authorization.reasons)
                    if candidate_report is not None:
                        details.extend(candidate_report.reasons)
                    items.append(
                        Phase9WorkItem(
                            task_type="POST_PROMOTION_SHADOW_REQUIRED",
                            scope="FRESH_RETRAINING_CYCLE",
                            reason=(
                                "additional independent post-promotion shadow "
                                "evidence is required"
                                + (
                                    ": " + "; ".join(dict.fromkeys(details))
                                    if details
                                    else ""
                                )
                            ),
                            shell_command=None,
                        )
                    )

        if policy_authorization_current:
            controlled_audit = (
                audit_persisted_phase9_policy_controlled_validation(
                    storage,
                )
            )
            controlled_validation_current = controlled_audit.current
            if not controlled_validation_current:
                controlled_cycle = _latest_controlled_validation_cycle(
                    storage,
                )
                if controlled_cycle is None:
                    controlled_cycle = _latest_retraining_dataset_cycle(
                        storage,
                    )
                controlled_report = None
                if controlled_cycle is not None:
                    controlled_report = (
                        evaluate_phase9_policy_controlled_validation(
                            storage,
                            cycle_id=controlled_cycle,
                        )
                    )
                if (
                    controlled_cycle is not None
                    and controlled_report is not None
                    and controlled_report.controlled_validation_ready
                ):
                    items.append(
                        Phase9WorkItem(
                            task_type="PERSIST_CONTROLLED_VALIDATION",
                            scope=controlled_cycle,
                            reason=(
                                "a fresh independent holdout passes current "
                                "controlled validation but persisted evidence "
                                "is missing or stale"
                            ),
                            shell_command=(
                                "pio phase9-policy-controlled-validate "
                                "--cycle-id "
                                + _q(controlled_cycle)
                                + " --persist --require-ready"
                            ),
                        )
                    )
                else:
                    details = (
                        list(controlled_report.reasons)
                        if controlled_report is not None
                        else list(controlled_audit.reasons)
                    )
                    items.append(
                        Phase9WorkItem(
                            task_type="FRESH_CONTROLLED_HOLDOUT_REQUIRED",
                            scope="FRESH_RETRAINING_CYCLE",
                            reason=(
                                "controlled validation requires a fresh "
                                "checksum-bound cycle independent of the "
                                "authorization corpus"
                                + (
                                    ": " + "; ".join(dict.fromkeys(details))
                                    if details
                                    else ""
                                )
                            ),
                            shell_command=None,
                        )
                    )

        if policy_authorization_current and controlled_validation_current:
            rollout_audit = (
                audit_persisted_phase9_policy_rollout_simulation(storage)
            )
            rollout_simulation_current = rollout_audit.current
            if not rollout_simulation_current:
                items.append(
                    Phase9WorkItem(
                        task_type="BOUNDED_ROLLOUT_SIMULATION",
                        scope="ROLLOUT_ENVELOPE_JSON_REQUIRED",
                        reason=(
                            "a current disabled bounded-rollout simulation is "
                            "required after policy-readiness evidence"
                            + (
                                ": " + "; ".join(rollout_audit.reasons)
                                if rollout_audit.reasons
                                else ""
                            )
                        ),
                        shell_command=(
                            "pio phase9-policy-rollout-simulate "
                            "--file <ROLLOUT_ENVELOPE_JSON> "
                            "--persist --require-ready"
                        ),
                    )
                )

        if rollout_simulation_current:
            rollback_audit = (
                audit_persisted_phase9_policy_rollback_simulation(storage)
            )
            rollback_simulation_current = rollback_audit.current
            if not rollback_simulation_current:
                items.append(
                    Phase9WorkItem(
                        task_type="ROLLBACK_SIMULATION",
                        scope="ROLLBACK_METRICS_AND_CRITERIA_JSON_REQUIRED",
                        reason=(
                            "current explicit rollback simulation evidence is "
                            "required for the bounded rollout"
                            + (
                                ": " + "; ".join(rollback_audit.reasons)
                                if rollback_audit.reasons
                                else ""
                            )
                        ),
                        shell_command=(
                            "pio phase9-policy-rollback-simulate "
                            "--file <ROLLBACK_METRICS_AND_CRITERIA_JSON> "
                            "--persist"
                        ),
                    )
                )
            elif rollback_audit.rollback_required is True:
                items.append(
                    Phase9WorkItem(
                        task_type="ROLLBACK_REMEDIATION_REQUIRED",
                        scope="PHASE9_POLICY_ROLLOUT",
                        reason=(
                            "current rollback simulation resolves to "
                            "ROLLBACK_REQUIRED; no forward policy-wiring "
                            "task is valid until the simulated breach is "
                            "resolved and new evidence is persisted"
                        ),
                        shell_command=None,
                    )
                )
            elif rollback_audit.status == "OBSERVATION_PENDING":
                items.append(
                    Phase9WorkItem(
                        task_type="ROLLBACK_OBSERVATION_DEPTH",
                        scope="ROLLBACK_METRICS_REQUIRED",
                        reason=(
                            "rollback simulation is current but minimum "
                            "configured observation depth has not been met"
                        ),
                        shell_command=(
                            "pio phase9-policy-rollback-simulate "
                            "--file <UPDATED_ROLLBACK_METRICS_JSON> "
                            "--persist"
                        ),
                    )
                )

        if (
            policy_authorization_current
            and controlled_validation_current
            and rollout_simulation_current
            and rollback_simulation_current
        ):
            prewire = evaluate_phase9_policy_prewire_audit(storage)
            prewire_ready = prewire.ready

            if prewire_ready:
                manifest_audit = (
                    audit_persisted_phase9_policy_prewire_manifest(
                        storage,
                    )
                )
                prewire_manifest_current = manifest_audit.current
                if not prewire_manifest_current:
                    items.append(
                        Phase9WorkItem(
                            task_type="PERSIST_PREWIRE_MANIFEST",
                            scope="PHASE9_POLICY_PREWIRE_MANIFEST",
                            reason=(
                                "pre-wiring evidence is current and ready but "
                                "the immutable component manifest is missing "
                                "or stale"
                                + (
                                    ": " + "; ".join(
                                        manifest_audit.reasons
                                    )
                                    if manifest_audit.reasons
                                    else ""
                                )
                            ),
                            shell_command=(
                                "pio phase9-policy-manifest "
                                "--persist --require-ready"
                            ),
                        )
                    )

    return Phase9WorkQueue(
        phase8_promoted=phase8_promoted,
        research_bundle_ready=bundle.research_ready,
        promotion_ready=promotion.promotion_ready,
        phase9_current=phase9_current,
        policy_authorization_current=policy_authorization_current,
        controlled_validation_current=controlled_validation_current,
        rollout_simulation_current=rollout_simulation_current,
        rollback_simulation_current=rollback_simulation_current,
        prewire_ready=prewire_ready,
        prewire_manifest_current=prewire_manifest_current,
        research_sources_current=research_sources_current,
        candidate_pools=pools,
        items=tuple(items),
    )
