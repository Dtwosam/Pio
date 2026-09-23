from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable

from .contextual_bandit import CONTEXTUAL_BANDIT_EVIDENCE_TYPE
from .contextual_bandit_cycle import (
    evaluate_cycle_contextual_bandit,
    persist_cycle_contextual_bandit,
)
from .mint_risk import (
    MINT_RISK_EVIDENCE_TYPE,
    MintRiskCriteria,
    persist_pool_mint_risk,
    research_pool_mint_risk,
)
from .phase8_validation import audit_persisted_phase8_promotion
from .phase9_history_plan import build_phase9_history_plan
from .phase9_pool_cohort import (
    evaluate_phase9_pool_cohort,
    select_phase9_cohort_source_pools,
)
from .phase9_explicit_inputs import (
    audit_phase9_explicit_inputs,
    load_phase9_explicit_inputs,
    run_phase9_explicit_research,
)
from .phase9_bandit_dataset import (
    build_phase9_bandit_dataset,
    evaluate_phase9_contextual_bandit_from_dataset,
    persist_phase9_bandit_dataset,
    persist_phase9_contextual_bandit_from_dataset,
)
from .portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
)
from .static_hedge import STATIC_HEDGE_EVIDENCE_TYPE
from .phase9_mint_capture import (
    Phase9MintCaptureCriteria,
    build_phase9_mint_capture_plan,
)
from .phase9_replay_audit import evaluate_phase9_replay_audit
from .phase9_storage_integrity import evaluate_phase9_storage_integrity
from .phase9_source_freshness import (
    evaluate_phase9_source_freshness,
)
from .phase9_research import (
    PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
    evaluate_phase9_research,
    persist_phase9_research,
)
from .phase9_validation import (
    PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
    Phase9ResearchBundleCriteria,
    evaluate_phase9_research_bundle,
    persist_phase9_research_bundle,
    phase9_research_bundle_sha256,
)
from .phase9_wallet_flow_capture import wallet_flow_source_state
from .storage import Storage, utc_now_iso
from .wallet_flow import (
    WALLET_FLOW_EVIDENCE_TYPE,
    WalletFlowCriteria,
    persist_wallet_flow_research,
    research_wallet_flow,
)


@dataclass(frozen=True)
class Phase9ResearchRefreshItem:
    family: str
    scope: str
    status: str
    research_qualified: bool | None
    persisted_evidence_id: int | None
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9ResearchRefreshReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    storage_integrity_verified: bool
    phase8_current: bool
    automatic_families_ready: bool
    bundle_ready_after: bool
    bundle_persisted_evidence_id: int | None
    items: tuple[Phase9ResearchRefreshItem, ...]
    bundle_reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _normalized(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True))


def _latest_evidence_id(
    storage: Storage,
    *,
    edge_type: str,
    pool_address: str,
) -> int | None:
    latest = storage.latest_advanced_edge_evidence(
        edge_type=edge_type,
        pool_address=pool_address,
    )
    return int(latest["id"]) if latest is not None else None


def _persist_if_changed(
    storage: Storage,
    *,
    edge_type: str,
    pool_address: str,
    evidence: dict[str, Any],
    persist: Callable[[], int],
) -> tuple[str, int | None]:
    latest = storage.latest_advanced_edge_evidence(
        edge_type=edge_type,
        pool_address=pool_address,
    )
    if (
        latest is not None
        and _normalized(latest.get("evidence"))
        == _normalized(evidence)
    ):
        return "UNCHANGED", int(latest["id"])
    return "PERSISTED", int(persist())


def _ranked_source_pools(
    storage: Storage,
    *,
    cohort,
    limit: int,
) -> tuple[str, ...]:
    if not getattr(cohort, "sampling_pools", ()):
        return _top_chain_pools(storage, limit=limit)
    return select_phase9_cohort_source_pools(
        storage,
        cohort=cohort,
        limit=limit,
    )


def _top_chain_pools(
    storage: Storage,
    *,
    limit: int,
) -> tuple[str, ...]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT pool_address, COUNT(*) AS observations
            FROM chain_pool_snapshots
            WHERE pool_address IS NOT NULL
              AND TRIM(pool_address) != ''
            GROUP BY pool_address
            ORDER BY observations DESC, pool_address ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


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
    try:
        payload = json.loads(str(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    dataset = payload.get("dataset")
    cycle_id = str(payload.get("cycle_id", "")).strip()
    target_version = str(
        payload.get("target_dataset_version", "")
    ).strip()
    output_file = str(payload.get("output_file", "")).strip()
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


def _source_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Phase 9 source timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _mint_evaluation_as_of(
    storage: Storage,
    *,
    selected_pools: tuple[str, ...],
    mint_plan,
) -> str:
    source_times = [
        str(item.latest_snapshot_at)
        for item in mint_plan.candidates
        if item.latest_snapshot_at is not None
    ]
    with storage.connect() as conn:
        for pool in selected_pools:
            row = conn.execute(
                """
                SELECT observed_at
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                ORDER BY julianday(observed_at) DESC, id DESC
                LIMIT 1
                """,
                (pool,),
            ).fetchone()
            if row is not None:
                source_times.append(str(row[0]))
    if not source_times:
        raise ValueError("mint research has no source timestamps")
    return max(source_times, key=_source_time)


def _replay_statuses(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria,
) -> dict[str, bool]:
    audit = evaluate_phase9_replay_audit(
        storage,
        criteria=criteria,
    )
    return {
        item.family: bool(item.replay_verified)
        for item in audit.families
    }


def run_phase9_research_refresh(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
    mint_criteria: MintRiskCriteria = MintRiskCriteria(),
    wallet_criteria: WalletFlowCriteria = WalletFlowCriteria(),
    persist_bundle_when_ready: bool = True,
) -> Phase9ResearchRefreshReport:
    storage_integrity = evaluate_phase9_storage_integrity(storage)
    items: list[Phase9ResearchRefreshItem] = []
    if not storage_integrity.verified:
        blocked_reasons = tuple(
            f"storage integrity: {reason}"
            for reason in storage_integrity.reasons
        )
        items.append(
            Phase9ResearchRefreshItem(
                family="storage_integrity",
                scope="PHASE9_STORAGE",
                status="BLOCKED",
                research_qualified=None,
                persisted_evidence_id=None,
                reason=(
                    "Phase 9 storage integrity is not verified: "
                    + "; ".join(storage_integrity.reasons)
                ),
            )
        )
        return Phase9ResearchRefreshReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            storage_integrity_verified=False,
            phase8_current=False,
            automatic_families_ready=False,
            bundle_ready_after=False,
            bundle_persisted_evidence_id=None,
            items=tuple(items),
            bundle_reasons=blocked_reasons,
        )

    phase8 = audit_persisted_phase8_promotion(storage)

    if not phase8.current:
        bundle = evaluate_phase9_research_bundle(
            storage,
            criteria=criteria,
        )
        items.append(
            Phase9ResearchRefreshItem(
                family="phase8",
                scope="PHASE8_PROMOTION",
                status="BLOCKED",
                research_qualified=None,
                persisted_evidence_id=None,
                reason=(
                    "Phase 8 promotion is not current; automatic Phase 9 "
                    "research refresh is skipped"
                ),
            )
        )
        return Phase9ResearchRefreshReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            storage_integrity_verified=True,
            phase8_current=False,
            automatic_families_ready=False,
            bundle_ready_after=bundle.research_ready,
            bundle_persisted_evidence_id=None,
            items=tuple(items),
            bundle_reasons=bundle.reasons,
        )

    replay = _replay_statuses(
        storage,
        criteria=criteria,
    )
    refresh_as_of = utc_now_iso()

    source_freshness_report = evaluate_phase9_source_freshness(
        storage,
        as_of=refresh_as_of,
        required_mint_pools=criteria.min_mint_risk_pools,
        required_wallet_pools=criteria.min_wallet_flow_pools,
    )
    ranked_cohort = evaluate_phase9_pool_cohort(
        storage,
        as_of=refresh_as_of,
    )
    source_freshness = source_freshness_report.by_family()
    source_freshness_reasons = {
        item.family: item.reason
        for item in source_freshness_report.families
    }

    def family_current(family: str) -> bool:
        return bool(
            replay.get(family, False)
            and source_freshness.get(family, False)
        )

    # Adaptive/regime multi-pool research.
    if family_current("adaptive_regime"):
        items.append(
            Phase9ResearchRefreshItem(
                family="adaptive_regime",
                scope="__MULTI_POOL__",
                status="UNCHANGED",
                research_qualified=True,
                persisted_evidence_id=None,
                reason=(
                    "latest qualified evidence is replay-verified and "
                    "uses current persisted sources"
                ),
            )
        )
    else:
        try:
            cohort = ranked_cohort
            if not cohort.research_ready:
                detail = "; ".join(cohort.reasons) or (
                    "ranked Phase 9 research cohort is below the exact "
                    "chain-history requirement"
                )
                items.append(
                    Phase9ResearchRefreshItem(
                        family="adaptive_regime",
                        scope="__MULTI_POOL__",
                        status="SKIPPED_SOURCE_NOT_READY",
                        research_qualified=None,
                        persisted_evidence_id=None,
                        reason=detail,
                    )
                )
            else:
                pools = cohort.research_pools
                report = evaluate_phase9_research(
                    storage,
                    pool_addresses=pools,
                )
                status, evidence_id = _persist_if_changed(
                    storage,
                    edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
                    pool_address="__MULTI_POOL__",
                    evidence=report.to_record(),
                    persist=lambda: persist_phase9_research(
                        storage,
                        report=report,
                    ),
                )
                items.append(
                    Phase9ResearchRefreshItem(
                        family="adaptive_regime",
                        scope="__MULTI_POOL__",
                        status=status,
                        research_qualified=report.research_qualified,
                        persisted_evidence_id=evidence_id,
                        reason=(
                            "recomputed from the ranked history-ready "
                            "Phase 9 pool cohort using persisted chain-history "
                            "sources"
                            + (
                                "; source freshness: "
                                + source_freshness_reasons.get(
                                    "adaptive_regime", ""
                                )
                                if replay.get("adaptive_regime", False)
                                and not source_freshness.get(
                                    "adaptive_regime", False
                                )
                                else ""
                            )
                        ),
                    )
                )
        except Exception as exc:
            items.append(
                Phase9ResearchRefreshItem(
                    family="adaptive_regime",
                    scope="__MULTI_POOL__",
                    status="FAILED",
                    research_qualified=None,
                    persisted_evidence_id=None,
                    reason=f"{type(exc).__name__}: {str(exc)[:1000]}",
                )
            )

    # Mint risk for the exact currently selected mint-source pools.
    if family_current("mint_risk"):
        items.append(
            Phase9ResearchRefreshItem(
                family="mint_risk",
                scope="AUTO",
                status="UNCHANGED",
                research_qualified=True,
                persisted_evidence_id=None,
                reason=(
                    "required qualified mint-risk evidence is replay-verified "
                    "and uses current persisted sources"
                ),
            )
        )
    else:
        try:
            mint_pools = _ranked_source_pools(
                storage,
                cohort=ranked_cohort,
                limit=criteria.min_mint_risk_pools,
            )
            mint_plan = build_phase9_mint_capture_plan(
                storage,
                criteria=Phase9MintCaptureCriteria(
                    target_pools=criteria.min_mint_risk_pools,
                    max_snapshot_age_seconds=(
                        mint_criteria.max_snapshot_age_seconds
                    ),
                    include_reward_mints=(
                        mint_criteria.include_reward_mints
                    ),
                ),
                pool_addresses=mint_pools,
            )
            if not mint_plan.inputs_ready:
                detail = "; ".join(mint_plan.reasons) or (
                    f"{mint_plan.captures_required} mint capture(s) remain"
                )
                items.append(
                    Phase9ResearchRefreshItem(
                        family="mint_risk",
                        scope="AUTO",
                        status="SKIPPED_SOURCE_NOT_READY",
                        research_qualified=None,
                        persisted_evidence_id=None,
                        reason=detail,
                    )
                )
            else:
                mint_as_of = _mint_evaluation_as_of(
                    storage,
                    selected_pools=mint_plan.selected_pools,
                    mint_plan=mint_plan,
                )
                for pool in mint_plan.selected_pools:
                    report = research_pool_mint_risk(
                        storage,
                        pool_address=pool,
                        criteria=mint_criteria,
                        as_of=mint_as_of,
                    )
                    status, evidence_id = _persist_if_changed(
                        storage,
                        edge_type=MINT_RISK_EVIDENCE_TYPE,
                        pool_address=pool,
                        evidence=report.to_record(),
                        persist=lambda report=report: (
                            persist_pool_mint_risk(
                                storage,
                                report=report,
                            )
                        ),
                    )
                    items.append(
                        Phase9ResearchRefreshItem(
                            family="mint_risk",
                            scope=pool,
                            status=status,
                            research_qualified=report.research_qualified,
                            persisted_evidence_id=evidence_id,
                            reason=(
                                "recomputed from authoritative persisted "
                                "pool/mint snapshots"
                                + (
                                    "; source freshness: "
                                    + source_freshness_reasons.get(
                                        "mint_risk", ""
                                    )
                                    if replay.get("mint_risk", False)
                                    and not source_freshness.get(
                                        "mint_risk", False
                                    )
                                    else ""
                                )
                            ),
                        )
                    )
        except Exception as exc:
            items.append(
                Phase9ResearchRefreshItem(
                    family="mint_risk",
                    scope="AUTO",
                    status="FAILED",
                    research_qualified=None,
                    persisted_evidence_id=None,
                    reason=f"{type(exc).__name__}: {str(exc)[:1000]}",
                )
            )

    # Descriptive wallet-flow research over the same bounded source window.
    if family_current("wallet_flow"):
        items.append(
            Phase9ResearchRefreshItem(
                family="wallet_flow",
                scope="AUTO",
                status="UNCHANGED",
                research_qualified=True,
                persisted_evidence_id=None,
                reason=(
                    "required qualified wallet-flow evidence is replay-verified "
                    "and uses current persisted sources"
                ),
            )
        )
    else:
        wallet_pools = _ranked_source_pools(
            storage,
            cohort=ranked_cohort,
            limit=criteria.min_wallet_flow_pools,
        )
        if len(wallet_pools) < criteria.min_wallet_flow_pools:
            items.append(
                Phase9ResearchRefreshItem(
                    family="wallet_flow",
                    scope="AUTO",
                    status="SKIPPED_SOURCE_NOT_READY",
                    research_qualified=None,
                    persisted_evidence_id=None,
                    reason=(
                        f"chain-observed wallet-flow pools "
                        f"{len(wallet_pools)} are below "
                        f"{criteria.min_wallet_flow_pools}"
                    ),
                )
            )
        else:
            for pool in wallet_pools:
                try:
                    source = wallet_flow_source_state(
                        storage,
                        pool_address=pool,
                        criteria=wallet_criteria,
                    )
                    if not source.ready:
                        items.append(
                            Phase9ResearchRefreshItem(
                                family="wallet_flow",
                                scope=pool,
                                status="SKIPPED_SOURCE_NOT_READY",
                                research_qualified=None,
                                persisted_evidence_id=None,
                                reason=(
                                    f"events {source.events}/"
                                    f"{wallet_criteria.min_events}, unique "
                                    f"users {source.unique_users}/"
                                    f"{wallet_criteria.min_unique_users}"
                                ),
                            )
                        )
                        continue
                    report = research_wallet_flow(
                        storage,
                        pool_address=pool,
                        criteria=wallet_criteria,
                    )
                    status, evidence_id = _persist_if_changed(
                        storage,
                        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
                        pool_address=pool,
                        evidence=report.to_record(),
                        persist=lambda report=report: (
                            persist_wallet_flow_research(
                                storage,
                                report=report,
                            )
                        ),
                    )
                    items.append(
                        Phase9ResearchRefreshItem(
                            family="wallet_flow",
                            scope=pool,
                            status=status,
                            research_qualified=report.research_qualified,
                            persisted_evidence_id=evidence_id,
                            reason=(
                                "recomputed from immutable persisted "
                                "position-event history"
                                + (
                                    "; source freshness: "
                                    + source_freshness_reasons.get(
                                        "wallet_flow", ""
                                    )
                                    if replay.get("wallet_flow", False)
                                    and not source_freshness.get(
                                        "wallet_flow", False
                                    )
                                    else ""
                                )
                            ),
                        )
                    )
                except Exception as exc:
                    items.append(
                        Phase9ResearchRefreshItem(
                            family="wallet_flow",
                            scope=pool,
                            status="FAILED",
                            research_qualified=None,
                            persisted_evidence_id=None,
                            reason=(
                                f"{type(exc).__name__}: "
                                f"{str(exc)[:1000]}"
                            ),
                        )
                    )

    # Artifact-backed static hedge and portfolio research. These families
    # may refresh automatically only after an operator has persisted a valid,
    # checksum-bound explicit input artifact.
    static_required = criteria.min_static_hedge_pools > 0
    allocation_required = criteria.require_portfolio_allocation
    static_needs_refresh = (
        static_required
        and not family_current("static_hedge")
    )
    allocation_needs_refresh = (
        allocation_required
        and not family_current("portfolio_allocation")
    )

    if static_required and not static_needs_refresh:
        items.append(
            Phase9ResearchRefreshItem(
                family="static_hedge",
                scope="EXPLICIT_INPUT_ARTIFACT",
                status="UNCHANGED",
                research_qualified=True,
                persisted_evidence_id=None,
                reason=(
                    "required qualified static-hedge evidence is replay-verified "
                    "and uses current persisted sources"
                ),
            )
        )
    if allocation_required and not allocation_needs_refresh:
        items.append(
            Phase9ResearchRefreshItem(
                family="portfolio_allocation",
                scope="__PORTFOLIO__",
                status="UNCHANGED",
                research_qualified=True,
                persisted_evidence_id=None,
                reason=(
                    "qualified portfolio-allocation evidence is replay-verified "
                    "and uses current persisted sources"
                ),
            )
        )

    if static_needs_refresh or allocation_needs_refresh:
        explicit_audit = audit_phase9_explicit_inputs(storage)
        if (
            not explicit_audit.valid
            or explicit_audit.evidence_id is None
        ):
            detail = "; ".join(explicit_audit.reasons) or (
                "valid checksum-bound explicit input artifact is unavailable"
            )
            if static_needs_refresh:
                items.append(
                    Phase9ResearchRefreshItem(
                        family="static_hedge",
                        scope="EXPLICIT_INPUT_ARTIFACT",
                        status="SKIPPED_SOURCE_NOT_READY",
                        research_qualified=None,
                        persisted_evidence_id=None,
                        reason=detail,
                    )
                )
            if allocation_needs_refresh:
                items.append(
                    Phase9ResearchRefreshItem(
                        family="portfolio_allocation",
                        scope="__PORTFOLIO__",
                        status="SKIPPED_SOURCE_NOT_READY",
                        research_qualified=None,
                        persisted_evidence_id=None,
                        reason=detail,
                    )
                )
        else:
            try:
                artifact = load_phase9_explicit_inputs(
                    storage,
                    evidence_id=explicit_audit.evidence_id,
                )
                if artifact is None:
                    raise ValueError(
                        "validated explicit input artifact disappeared"
                    )

                static_before = {
                    spec.pool_address: _latest_evidence_id(
                        storage,
                        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
                        pool_address=spec.pool_address,
                    )
                    for spec in artifact.inputs.static_hedges
                }
                allocation_before = _latest_evidence_id(
                    storage,
                    edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
                    pool_address="__PORTFOLIO__",
                )

                explicit_run = run_phase9_explicit_research(
                    storage,
                    artifact=artifact,
                    persist=True,
                    deduplicate_persistence=True,
                    include_static_hedge=static_needs_refresh,
                    include_portfolio=allocation_needs_refresh,
                    persist_static_hedge=static_needs_refresh,
                    persist_portfolio=allocation_needs_refresh,
                )

                if static_needs_refresh:
                    for report, evidence_id in zip(
                        explicit_run.static_hedge_reports,
                        explicit_run.static_hedge_evidence_ids,
                    ):
                        pool = str(report.get("pool_address", ""))
                        previous = static_before.get(pool)
                        items.append(
                            Phase9ResearchRefreshItem(
                                family="static_hedge",
                                scope=pool or "UNKNOWN_POOL",
                                status=(
                                    "UNCHANGED"
                                    if previous == evidence_id
                                    else "PERSISTED"
                                ),
                                research_qualified=bool(
                                    report.get("research_qualified")
                                ),
                                persisted_evidence_id=evidence_id,
                                reason=(
                                    "recomputed from immutable explicit "
                                    f"input artifact {artifact.evidence_id}"
                                ),
                            )
                        )

                if allocation_needs_refresh:
                    allocation_record = (
                        explicit_run.portfolio_allocation or {}
                    )
                    allocation_id = (
                        explicit_run.portfolio_allocation_evidence_id
                    )
                    items.append(
                        Phase9ResearchRefreshItem(
                            family="portfolio_allocation",
                            scope="__PORTFOLIO__",
                            status=(
                                "UNCHANGED"
                                if allocation_before == allocation_id
                                and allocation_id is not None
                                else "PERSISTED"
                            ),
                            research_qualified=bool(
                                allocation_record.get(
                                    "research_qualified"
                                )
                            ),
                            persisted_evidence_id=allocation_id,
                            reason=(
                                "recomputed from immutable explicit input "
                                f"artifact {artifact.evidence_id}"
                            ),
                        )
                    )
            except Exception as exc:
                detail = (
                    f"{type(exc).__name__}: {str(exc)[:1000]}"
                )
                if static_needs_refresh:
                    items.append(
                        Phase9ResearchRefreshItem(
                            family="static_hedge",
                            scope="EXPLICIT_INPUT_ARTIFACT",
                            status="FAILED",
                            research_qualified=None,
                            persisted_evidence_id=None,
                            reason=detail,
                        )
                    )
                if allocation_needs_refresh:
                    items.append(
                        Phase9ResearchRefreshItem(
                            family="portfolio_allocation",
                            scope="__PORTFOLIO__",
                            status="FAILED",
                            research_qualified=None,
                            persisted_evidence_id=None,
                            reason=detail,
                        )
                    )

    # Contextual bandit prefers an existing checksum-bound retraining-cycle
    # dataset. If none exists, Phase 9 may derive a research-only action
    # dataset from a validated explicit-input artifact plus persisted chain
    # history. No labels or economic assumptions are synthesized.
    if family_current("contextual_bandit"):
        items.append(
            Phase9ResearchRefreshItem(
                family="contextual_bandit",
                scope="__CONTEXTUAL_BANDIT__",
                status="UNCHANGED",
                research_qualified=True,
                persisted_evidence_id=None,
                reason=(
                    "latest qualified bandit evidence is replay-verified and "
                    "uses the latest available dataset source"
                ),
            )
        )
    else:
        cycle_id = _latest_retraining_dataset_cycle(storage)
        if cycle_id is not None:
            try:
                result = evaluate_cycle_contextual_bandit(
                    storage,
                    cycle_id=cycle_id,
                )
                evidence = result.report.to_record()
                evidence["dataset_lineage"] = asdict(result.lineage)
                status, evidence_id = _persist_if_changed(
                    storage,
                    edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
                    pool_address="__CONTEXTUAL_BANDIT__",
                    evidence=evidence,
                    persist=lambda: persist_cycle_contextual_bandit(
                        storage,
                        result=result,
                    ),
                )
                items.append(
                    Phase9ResearchRefreshItem(
                        family="contextual_bandit",
                        scope=cycle_id,
                        status=status,
                        research_qualified=(
                            result.report.research_qualified
                        ),
                        persisted_evidence_id=evidence_id,
                        reason=(
                            "recomputed from checksum-bound retraining "
                            "dataset lineage"
                        ),
                    )
                )
            except Exception as exc:
                items.append(
                    Phase9ResearchRefreshItem(
                        family="contextual_bandit",
                        scope=cycle_id,
                        status="FAILED",
                        research_qualified=None,
                        persisted_evidence_id=None,
                        reason=f"{type(exc).__name__}: {str(exc)[:1000]}",
                    )
                )
        else:
            explicit_audit = audit_phase9_explicit_inputs(storage)
            if (
                not explicit_audit.valid
                or explicit_audit.evidence_id is None
            ):
                items.append(
                    Phase9ResearchRefreshItem(
                        family="contextual_bandit",
                        scope="EXPLICIT_INPUT_ARTIFACT",
                        status="SKIPPED_SOURCE_NOT_READY",
                        research_qualified=None,
                        persisted_evidence_id=None,
                        reason=(
                            "no retraining dataset exists and a valid "
                            "explicit-input artifact is unavailable"
                            + (
                                ": " + "; ".join(
                                    explicit_audit.reasons
                                )
                                if explicit_audit.reasons
                                else ""
                            )
                        ),
                    )
                )
            else:
                try:
                    artifact = load_phase9_explicit_inputs(
                        storage,
                        evidence_id=explicit_audit.evidence_id,
                    )
                    if artifact is None:
                        raise ValueError(
                            "validated explicit input artifact disappeared"
                        )
                    dataset_payload, dataset_bytes = (
                        build_phase9_bandit_dataset(
                            storage,
                            artifact=artifact,
                        )
                    )
                    dataset_artifact = persist_phase9_bandit_dataset(
                        storage,
                        payload=dataset_payload,
                        raw_dataset=dataset_bytes,
                    )
                    result = (
                        evaluate_phase9_contextual_bandit_from_dataset(
                            storage,
                            dataset_evidence_id=(
                                dataset_artifact.evidence_id
                            ),
                        )
                    )
                    evidence = result.report.to_record()
                    evidence["dataset_lineage"] = asdict(result.lineage)
                    status, evidence_id = _persist_if_changed(
                        storage,
                        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
                        pool_address="__CONTEXTUAL_BANDIT__",
                        evidence=evidence,
                        persist=lambda: (
                            persist_phase9_contextual_bandit_from_dataset(
                                storage,
                                result=result,
                            )
                        ),
                    )
                    items.append(
                        Phase9ResearchRefreshItem(
                            family="contextual_bandit",
                            scope=str(dataset_artifact.evidence_id),
                            status=status,
                            research_qualified=(
                                result.report.research_qualified
                            ),
                            persisted_evidence_id=evidence_id,
                            reason=(
                                "recomputed from a checksum-bound Phase 9 "
                                "counterfactual dataset derived from "
                                f"explicit input artifact {artifact.evidence_id}"
                            ),
                        )
                    )
                except ValueError as exc:
                    items.append(
                        Phase9ResearchRefreshItem(
                            family="contextual_bandit",
                            scope="PHASE9_BANDIT_DATASET",
                            status="SKIPPED_SOURCE_NOT_READY",
                            research_qualified=None,
                            persisted_evidence_id=None,
                            reason=str(exc)[:1000],
                        )
                    )
                except Exception as exc:
                    items.append(
                        Phase9ResearchRefreshItem(
                            family="contextual_bandit",
                            scope="PHASE9_BANDIT_DATASET",
                            status="FAILED",
                            research_qualified=None,
                            persisted_evidence_id=None,
                            reason=(
                                f"{type(exc).__name__}: "
                                f"{str(exc)[:1000]}"
                            ),
                        )
                    )

    bundle = evaluate_phase9_research_bundle(
        storage,
        criteria=criteria,
    )
    bundle_id = None
    if bundle.research_ready and persist_bundle_when_ready:
        payload = bundle.to_record()
        evidence = {
            **payload,
            "bundle_sha256": phase9_research_bundle_sha256(payload),
        }
        _, bundle_id = _persist_if_changed(
            storage,
            edge_type=PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
            pool_address="__PHASE9_RESEARCH__",
            evidence=evidence,
            persist=lambda: persist_phase9_research_bundle(
                storage,
                report=bundle,
            ),
        )

    automatic_ready = (
        bundle.adaptive_multi_pool.qualified_records >= 1
        and bundle.mint_risk.qualified_records
            >= criteria.min_mint_risk_pools
        and bundle.wallet_flow.qualified_records
            >= criteria.min_wallet_flow_pools
        and bundle.contextual_bandit.qualified_records >= 1
    )

    return Phase9ResearchRefreshReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        storage_integrity_verified=True,
        phase8_current=True,
        automatic_families_ready=automatic_ready,
        bundle_ready_after=bundle.research_ready,
        bundle_persisted_evidence_id=bundle_id,
        items=tuple(items),
        bundle_reasons=bundle.reasons,
    )
