from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .phase9_shadow import (
    PHASE9_SHADOW_EVIDENCE_TYPE,
    Phase9ShadowCriteria,
    _persisted_phase9_context,
    evaluate_phase9_shadow,
)
from .phase9_bandit_dataset import (
    PHASE9_BANDIT_DATASET_EVIDENCE_TYPE,
)
from .phase9_validation import (
    audit_persisted_phase9_promotion,
    audit_persisted_phase9_promotion_baseline,
)
from .phase9_source_freshness import (
    evaluate_phase9_source_freshness,
)
from .storage import Storage


PHASE9_POLICY_GATE_EVIDENCE_TYPE = (
    "PHASE9_LIVE_POLICY_AUTHORIZATION_GATE_V1"
)
PHASE9_POLICY_GATE_SCOPE = "__PHASE9_LIVE_POLICY_GATE__"


@dataclass(frozen=True)
class Phase9PolicyAuthorizationCriteria:
    min_shadow_runs: int = 3
    min_distinct_dataset_hashes: int = 3
    min_distinct_cutoffs: int = 3
    min_decisions_per_run: int = 50
    min_pools_per_run: int = 3
    min_selected_arms_per_run: int = 2
    min_total_decisions: int = 150
    min_mean_uplift_vs_baseline_bps: float = 0.0
    max_mean_regret_vs_oracle_bps: float = 300.0

    def validate(self) -> None:
        for name, value in (
            ("min_shadow_runs", self.min_shadow_runs),
            (
                "min_distinct_dataset_hashes",
                self.min_distinct_dataset_hashes,
            ),
            ("min_distinct_cutoffs", self.min_distinct_cutoffs),
            ("min_decisions_per_run", self.min_decisions_per_run),
            ("min_pools_per_run", self.min_pools_per_run),
            (
                "min_selected_arms_per_run",
                self.min_selected_arms_per_run,
            ),
            ("min_total_decisions", self.min_total_decisions),
        ):
            if value < 1:
                raise ValueError(f"{name} must be positive")
        if (
            self.min_distinct_dataset_hashes
            > self.min_shadow_runs
        ):
            raise ValueError(
                "min_distinct_dataset_hashes cannot exceed "
                "min_shadow_runs"
            )
        if self.min_distinct_cutoffs > self.min_shadow_runs:
            raise ValueError(
                "min_distinct_cutoffs cannot exceed min_shadow_runs"
            )
        if not (
            float("-inf")
            < self.min_mean_uplift_vs_baseline_bps
            < float("inf")
        ):
            raise ValueError(
                "min_mean_uplift_vs_baseline_bps must be finite"
            )
        if (
            not float("-inf")
            < self.max_mean_regret_vs_oracle_bps
            < float("inf")
        ):
            raise ValueError(
                "max_mean_regret_vs_oracle_bps must be finite"
            )


@dataclass(frozen=True)
class Phase9ShadowReplayEvidence:
    evidence_id: int
    cycle_id: str
    dataset_sha256: str
    dataset_cutoff: str
    decisions_evaluated: int
    pools_evaluated: int
    selected_arms: int
    mean_uplift_vs_baseline_bps: float | None
    mean_regret_vs_oracle_bps: float | None
    replay_verified: bool
    criteria_strong_enough: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyAuthorizationReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    phase9_current: bool
    shadow_records_seen: int
    unique_shadow_cycles_seen: int
    replay_verified_shadow_runs: int
    qualifying_shadow_runs: int
    distinct_dataset_hashes: int
    distinct_cutoffs: int
    total_decisions: int
    authorization_ready: bool
    criteria: Phase9PolicyAuthorizationCriteria
    phase9_audit: dict[str, Any] | None
    qualifying_evidence_ids: tuple[int, ...]
    shadow_evidence: tuple[Phase9ShadowReplayEvidence, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyAuthorizationAudit:
    exists: bool
    qualified: bool
    boundary_valid: bool
    criteria_valid: bool
    current_authorization_ready: bool
    persisted_matches_current: bool
    current: bool
    evidence_id: int | None
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

def _criteria_strong_enough(
    shadow: Phase9ShadowCriteria,
    gate: Phase9PolicyAuthorizationCriteria,
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if shadow.min_decisions < gate.min_decisions_per_run:
        reasons.append(
            f"shadow min_decisions {shadow.min_decisions} is below "
            f"{gate.min_decisions_per_run}"
        )
    if shadow.min_pools < gate.min_pools_per_run:
        reasons.append(
            f"shadow min_pools {shadow.min_pools} is below "
            f"{gate.min_pools_per_run}"
        )
    if shadow.min_selected_arms < gate.min_selected_arms_per_run:
        reasons.append(
            f"shadow min_selected_arms "
            f"{shadow.min_selected_arms} is below "
            f"{gate.min_selected_arms_per_run}"
        )
    if (
        shadow.min_mean_uplift_vs_baseline_bps
        < gate.min_mean_uplift_vs_baseline_bps
    ):
        reasons.append(
            "shadow uplift threshold is weaker than authorization gate"
        )
    if (
        shadow.max_mean_regret_vs_oracle_bps
        > gate.max_mean_regret_vs_oracle_bps
    ):
        reasons.append(
            "shadow regret threshold is weaker than authorization gate"
        )
    return not reasons, tuple(reasons)


def _latest_shadow_rows_by_cycle(
    storage: Storage,
) -> tuple[int, dict[str, tuple[int, bool, dict[str, Any]]]]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, qualified, evidence_json
            FROM advanced_edge_evidence
            WHERE edge_type = ?
              AND pool_address = ?
            ORDER BY id ASC
            """,
            (
                PHASE9_SHADOW_EVIDENCE_TYPE,
                "__PHASE9_SHADOW__",
            ),
        ).fetchall()

    latest: dict[str, tuple[int, bool, dict[str, Any]]] = {}
    for raw_id, qualified, raw_json in rows:
        try:
            evidence = json.loads(str(raw_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(evidence, dict):
            continue
        cycle_id = str(evidence.get("cycle_id", "")).strip()
        if not cycle_id:
            continue
        latest[cycle_id] = (
            int(raw_id),
            bool(qualified),
            evidence,
        )
    return len(rows), latest


def evaluate_phase9_policy_authorization(
    storage: Storage,
    *,
    criteria: Phase9PolicyAuthorizationCriteria = (
        Phase9PolicyAuthorizationCriteria()
    ),
) -> Phase9PolicyAuthorizationReport:
    criteria.validate()
    reasons: list[str] = []

    _, promotion_criteria, context_reasons = (
        _persisted_phase9_context(storage)
    )
    reasons.extend(context_reasons)
    phase9_audit = None
    phase9_current = False
    if promotion_criteria is not None:
        audit = audit_persisted_phase9_promotion_baseline(storage)
        phase9_audit = audit.to_record()
        phase9_current = audit.valid
        if not audit.valid:
            reasons.extend(
                f"Phase 9 currentness: {reason}"
                for reason in audit.reasons
            )
        if audit.valid:
            freshness = evaluate_phase9_source_freshness(
                storage,
                required_mint_pools=(
                    promotion_criteria.min_mint_risk_pools
                ),
                required_wallet_pools=(
                    promotion_criteria.min_wallet_flow_pools
                ),
            )
            required_families = {
                "adaptive_regime": (
                    promotion_criteria.require_adaptive_multi_pool
                ),
                "mint_risk": True,
                "wallet_flow": True,
                "portfolio_allocation": (
                    promotion_criteria.require_portfolio_allocation
                ),
                "static_hedge": True,
                "contextual_bandit": (
                    promotion_criteria.require_contextual_bandit
                ),
            }
            stale_required = [
                item
                for item in freshness.families
                if required_families.get(item.family, False)
                and not item.current
            ]
            if stale_required:
                phase9_current = False
                reasons.extend(
                    "Phase 9 source currentness: "
                    + item.family
                    + ": "
                    + item.reason
                    for item in stale_required
                )

    records_seen, latest = _latest_shadow_rows_by_cycle(storage)
    replay_evidence: list[Phase9ShadowReplayEvidence] = []
    qualifying_ids: list[int] = []
    qualifying_hashes: set[str] = set()
    qualifying_cutoffs: set[str] = set()
    total_decisions = 0
    replay_verified_count = 0

    for cycle_id in sorted(latest):
        evidence_id, db_qualified, persisted = latest[cycle_id]
        item_reasons: list[str] = []
        criteria_raw = persisted.get("criteria")
        shadow_criteria = None
        if isinstance(criteria_raw, dict):
            try:
                shadow_criteria = Phase9ShadowCriteria(
                    **criteria_raw
                )
                shadow_criteria.validate()
            except (TypeError, ValueError):
                shadow_criteria = None
        if shadow_criteria is None:
            item_reasons.append(
                "persisted shadow criteria are invalid"
            )

        boundary_valid = (
            persisted.get("research_only") is True
            and persisted.get("policy_actionable") is False
            and persisted.get("shadow_ready") is True
            and db_qualified
        )
        if not boundary_valid:
            item_reasons.append(
                "persisted shadow evidence is not ready and non-actionable"
            )

        replay = None
        replay_verified = False
        criteria_ok = False
        criteria_reasons: tuple[str, ...] = ()
        if shadow_criteria is not None:
            criteria_ok, criteria_reasons = _criteria_strong_enough(
                shadow_criteria,
                criteria,
            )
            item_reasons.extend(criteria_reasons)
            try:
                source_type = str(
                    persisted.get(
                        "dataset_source_type",
                        "CONTINUOUS_RETRAIN_DATASET_V1",
                    )
                )
                if source_type == PHASE9_BANDIT_DATASET_EVIDENCE_TYPE:
                    dataset_evidence_id = persisted.get(
                        "dataset_evidence_id"
                    )
                    if dataset_evidence_id is None:
                        raise ValueError(
                            "Phase 9 dataset-backed shadow is missing "
                            "dataset_evidence_id"
                        )
                    replay = evaluate_phase9_shadow(
                        storage,
                        dataset_evidence_id=int(dataset_evidence_id),
                        criteria=shadow_criteria,
                    )
                else:
                    replay = evaluate_phase9_shadow(
                        storage,
                        cycle_id=cycle_id,
                        criteria=shadow_criteria,
                    )
                persisted_normalized = json.loads(
                    json.dumps(persisted, sort_keys=True)
                )
                replay_normalized = json.loads(
                    json.dumps(replay.to_record(), sort_keys=True)
                )
                replay_verified = (
                    replay.shadow_ready
                    and persisted_normalized == replay_normalized
                )
                if not replay_verified:
                    item_reasons.append(
                        "shadow evidence does not deterministically replay"
                    )
            except (ValueError, FileNotFoundError) as exc:
                item_reasons.append(
                    f"shadow replay unavailable: {exc}"
                )

        if replay_verified:
            replay_verified_count += 1

        dataset_sha = str(
            persisted.get("dataset_sha256", "")
        ).strip()
        dataset_cutoff = str(
            persisted.get("dataset_cutoff", "")
        ).strip()
        cycle_result = persisted.get("cycle_result")
        report = (
            cycle_result.get("report")
            if isinstance(cycle_result, dict)
            else None
        )
        if not isinstance(report, dict):
            report = {}

        def _integer(name: str) -> int:
            try:
                return int(report.get(name, 0))
            except (TypeError, ValueError):
                return 0

        decisions = _integer("decisions_evaluated")
        pools = _integer("pools_evaluated")
        selected_arms = _integer("selected_arms")
        uplift_raw = report.get("mean_uplift_vs_baseline_bps")
        regret_raw = report.get("mean_regret_vs_oracle_bps")
        try:
            uplift = (
                float(uplift_raw)
                if uplift_raw is not None
                else None
            )
        except (TypeError, ValueError):
            uplift = None
        try:
            regret = (
                float(regret_raw)
                if regret_raw is not None
                else None
            )
        except (TypeError, ValueError):
            regret = None

        qualifies = (
            boundary_valid
            and replay_verified
            and criteria_ok
            and decisions >= criteria.min_decisions_per_run
            and pools >= criteria.min_pools_per_run
            and selected_arms >= criteria.min_selected_arms_per_run
            and uplift is not None
            and uplift
            >= criteria.min_mean_uplift_vs_baseline_bps
            and regret is not None
            and regret
            <= criteria.max_mean_regret_vs_oracle_bps
            and bool(dataset_sha)
            and bool(dataset_cutoff)
        )
        if qualifies:
            qualifying_ids.append(evidence_id)
            qualifying_hashes.add(dataset_sha)
            qualifying_cutoffs.add(dataset_cutoff)
            total_decisions += decisions

        replay_evidence.append(
            Phase9ShadowReplayEvidence(
                evidence_id=evidence_id,
                cycle_id=cycle_id,
                dataset_sha256=dataset_sha,
                dataset_cutoff=dataset_cutoff,
                decisions_evaluated=decisions,
                pools_evaluated=pools,
                selected_arms=selected_arms,
                mean_uplift_vs_baseline_bps=uplift,
                mean_regret_vs_oracle_bps=regret,
                replay_verified=replay_verified,
                criteria_strong_enough=criteria_ok,
                reasons=tuple(item_reasons),
            )
        )

    qualifying_runs = len(qualifying_ids)
    if qualifying_runs < criteria.min_shadow_runs:
        reasons.append(
            f"qualifying shadow runs {qualifying_runs} are below "
            f"{criteria.min_shadow_runs}"
        )
    if (
        len(qualifying_hashes)
        < criteria.min_distinct_dataset_hashes
    ):
        reasons.append(
            f"distinct shadow dataset hashes "
            f"{len(qualifying_hashes)} are below "
            f"{criteria.min_distinct_dataset_hashes}"
        )
    if len(qualifying_cutoffs) < criteria.min_distinct_cutoffs:
        reasons.append(
            f"distinct shadow cutoffs {len(qualifying_cutoffs)} are below "
            f"{criteria.min_distinct_cutoffs}"
        )
    if total_decisions < criteria.min_total_decisions:
        reasons.append(
            f"shadow decisions {total_decisions} are below "
            f"{criteria.min_total_decisions}"
        )

    authorization_ready = (
        phase9_current
        and qualifying_runs >= criteria.min_shadow_runs
        and len(qualifying_hashes)
        >= criteria.min_distinct_dataset_hashes
        and len(qualifying_cutoffs)
        >= criteria.min_distinct_cutoffs
        and total_decisions >= criteria.min_total_decisions
        and not reasons
    )

    return Phase9PolicyAuthorizationReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        phase9_current=phase9_current,
        shadow_records_seen=records_seen,
        unique_shadow_cycles_seen=len(latest),
        replay_verified_shadow_runs=replay_verified_count,
        qualifying_shadow_runs=qualifying_runs,
        distinct_dataset_hashes=len(qualifying_hashes),
        distinct_cutoffs=len(qualifying_cutoffs),
        total_decisions=total_decisions,
        authorization_ready=authorization_ready,
        criteria=criteria,
        phase9_audit=phase9_audit,
        qualifying_evidence_ids=tuple(qualifying_ids),
        shadow_evidence=tuple(replay_evidence),
        reasons=tuple(reasons),
    )


def persist_phase9_policy_authorization(
    storage: Storage,
    *,
    report: Phase9PolicyAuthorizationReport,
) -> int:
    if report.policy_actionable:
        raise ValueError(
            "Phase 9 authorization evidence must not directly grant "
            "LIVE policy authority"
        )
    if report.execution_wired:
        raise ValueError(
            "Phase 9 authorization evidence must not be wired to execution"
        )
    if not report.research_only:
        raise ValueError(
            "Phase 9 authorization evidence must remain research-only"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_GATE_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_GATE_SCOPE,
        as_of=None,
        status=(
            "AUTHORIZATION_EVIDENCE_READY"
            if report.authorization_ready
            else "AUTHORIZATION_EVIDENCE_NOT_READY"
        ),
        qualified=report.authorization_ready,
        evidence=report.to_record(),
    )


def audit_persisted_phase9_policy_authorization(
    storage: Storage,
) -> Phase9PolicyAuthorizationAudit:
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_GATE_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_GATE_SCOPE,
    )
    if latest is None:
        return Phase9PolicyAuthorizationAudit(
            exists=False,
            qualified=False,
            boundary_valid=False,
            criteria_valid=False,
            current_authorization_ready=False,
            persisted_matches_current=False,
            current=False,
            evidence_id=None,
            reasons=(
                "persisted Phase 9 policy authorization evidence is missing",
            ),
        )

    evidence = latest["evidence"]
    reasons: list[str] = []
    qualified = bool(latest["qualified"])
    boundary_valid = (
        isinstance(evidence, dict)
        and evidence.get("research_only") is True
        and evidence.get("policy_actionable") is False
        and evidence.get("execution_wired") is False
    )
    if not boundary_valid:
        reasons.append(
            "persisted authorization evidence violates the non-actionable boundary"
        )
    if not qualified:
        reasons.append(
            "latest persisted authorization evidence is not qualified"
        )

    criteria = None
    if isinstance(evidence, dict):
        criteria_raw = evidence.get("criteria")
        if isinstance(criteria_raw, dict):
            try:
                criteria = Phase9PolicyAuthorizationCriteria(
                    **criteria_raw
                )
                criteria.validate()
            except (TypeError, ValueError):
                criteria = None
    criteria_valid = criteria is not None
    if not criteria_valid:
        reasons.append(
            "persisted authorization criteria are invalid"
        )

    current_report = (
        evaluate_phase9_policy_authorization(
            storage,
            criteria=criteria,
        )
        if criteria is not None
        else None
    )
    current_ready = bool(
        current_report is not None
        and current_report.authorization_ready
    )
    if not current_ready:
        reasons.append(
            "current Phase 9 policy authorization gate no longer passes"
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
            "persisted authorization evidence is stale versus current replay"
        )

    return Phase9PolicyAuthorizationAudit(
        exists=True,
        qualified=qualified,
        boundary_valid=boundary_valid,
        criteria_valid=criteria_valid,
        current_authorization_ready=current_ready,
        persisted_matches_current=persisted_matches_current,
        current=not reasons,
        evidence_id=int(latest["id"]),
        reasons=tuple(reasons),
    )
