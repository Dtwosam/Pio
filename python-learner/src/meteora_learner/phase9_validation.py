from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .contextual_bandit import CONTEXTUAL_BANDIT_EVIDENCE_TYPE
from .mint_risk import MINT_RISK_EVIDENCE_TYPE
from .phase9_research import PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE
from .phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from .portfolio_allocation import PORTFOLIO_ALLOCATION_EVIDENCE_TYPE
from .static_hedge import STATIC_HEDGE_EVIDENCE_TYPE
from .storage import Storage
from .wallet_flow import WALLET_FLOW_EVIDENCE_TYPE


PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE = "PHASE9_RESEARCH_BUNDLE_V1"


@dataclass(frozen=True)
class Phase9ResearchBundleCriteria:
    min_mint_risk_pools: int = 2
    min_wallet_flow_pools: int = 2
    min_static_hedge_pools: int = 1
    require_adaptive_multi_pool: bool = True
    require_portfolio_allocation: bool = True
    require_contextual_bandit: bool = True

    def __post_init__(self) -> None:
        if self.min_mint_risk_pools < 1:
            raise ValueError("min_mint_risk_pools must be positive")
        if self.min_wallet_flow_pools < 1:
            raise ValueError("min_wallet_flow_pools must be positive")
        if self.min_static_hedge_pools < 1:
            raise ValueError("min_static_hedge_pools must be positive")


@dataclass(frozen=True)
class Phase9EvidenceSummary:
    edge_type: str
    latest_records: int
    qualified_records: int
    qualified_pools: tuple[str, ...]
    latest_evidence_ids: tuple[int, ...]
    boundary_valid: bool


@dataclass(frozen=True)
class Phase9ResearchBundleReport:
    phase8_promoted: bool
    research_only: bool
    policy_actionable: bool
    status: str
    criteria: Phase9ResearchBundleCriteria
    adaptive_multi_pool: Phase9EvidenceSummary
    mint_risk: Phase9EvidenceSummary
    wallet_flow: Phase9EvidenceSummary
    portfolio_allocation: Phase9EvidenceSummary
    static_hedge: Phase9EvidenceSummary
    contextual_bandit: Phase9EvidenceSummary
    research_ready: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _latest_by_pool(
    storage: Storage,
    *,
    edge_type: str,
) -> list[dict[str, Any]]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.created_at, e.edge_type, e.pool_address,
                   e.as_of, e.status, e.qualified, e.evidence_json
            FROM advanced_edge_evidence e
            JOIN (
                SELECT pool_address, MAX(id) AS max_id
                FROM advanced_edge_evidence
                WHERE edge_type = ?
                GROUP BY pool_address
            ) latest
              ON latest.max_id = e.id
            WHERE e.edge_type = ?
            ORDER BY e.pool_address ASC
            """,
            (edge_type, edge_type),
        ).fetchall()

    import json

    return [
        {
            "id": int(row[0]),
            "created_at": str(row[1]),
            "edge_type": str(row[2]),
            "pool_address": str(row[3]),
            "as_of": str(row[4]) if row[4] is not None else None,
            "status": str(row[5]),
            "qualified": bool(row[6]),
            "evidence": json.loads(str(row[7])),
        }
        for row in rows
    ]


def _summary(
    storage: Storage,
    *,
    edge_type: str,
) -> Phase9EvidenceSummary:
    rows = _latest_by_pool(storage, edge_type=edge_type)
    boundary_valid = all(
        row["evidence"].get("research_only") is True
        and row["evidence"].get("policy_actionable") is False
        for row in rows
    )
    qualified = [
        row
        for row in rows
        if row["qualified"]
        and row["evidence"].get("research_qualified") is True
        and row["evidence"].get("research_only") is True
        and row["evidence"].get("policy_actionable") is False
    ]
    return Phase9EvidenceSummary(
        edge_type=edge_type,
        latest_records=len(rows),
        qualified_records=len(qualified),
        qualified_pools=tuple(
            sorted(str(row["pool_address"]) for row in qualified)
        ),
        latest_evidence_ids=tuple(int(row["id"]) for row in rows),
        boundary_valid=boundary_valid,
    )


def evaluate_phase9_research_bundle(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
) -> Phase9ResearchBundleReport:
    phase8_promoted = storage.phase_is_promoted(
        PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
    )

    adaptive = _summary(
        storage,
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
    )
    mint = _summary(storage, edge_type=MINT_RISK_EVIDENCE_TYPE)
    wallet = _summary(storage, edge_type=WALLET_FLOW_EVIDENCE_TYPE)
    allocation = _summary(
        storage,
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    )
    hedge = _summary(storage, edge_type=STATIC_HEDGE_EVIDENCE_TYPE)
    bandit = _summary(
        storage,
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
    )

    reasons: list[str] = []
    if not phase8_promoted:
        reasons.append(
            "Phase 8 must be persistently promoted before Phase 9 research can be ready"
        )

    for name, summary in (
        ("adaptive multi-pool", adaptive),
        ("mint risk", mint),
        ("wallet flow", wallet),
        ("portfolio allocation", allocation),
        ("static hedge", hedge),
        ("contextual bandit", bandit),
    ):
        if summary.latest_records and not summary.boundary_valid:
            reasons.append(
                f"{name} evidence violates the research-only boundary"
            )

    if (
        criteria.require_adaptive_multi_pool
        and adaptive.qualified_records < 1
    ):
        reasons.append(
            "qualified adaptive multi-pool evidence is required"
        )
    if mint.qualified_records < criteria.min_mint_risk_pools:
        reasons.append(
            f"qualified mint-risk pools {mint.qualified_records} are below "
            f"{criteria.min_mint_risk_pools}"
        )
    if wallet.qualified_records < criteria.min_wallet_flow_pools:
        reasons.append(
            f"qualified wallet-flow pools {wallet.qualified_records} are below "
            f"{criteria.min_wallet_flow_pools}"
        )
    if (
        criteria.require_portfolio_allocation
        and allocation.qualified_records < 1
    ):
        reasons.append(
            "qualified portfolio-allocation evidence is required"
        )
    if hedge.qualified_records < criteria.min_static_hedge_pools:
        reasons.append(
            f"qualified static-hedge pools {hedge.qualified_records} are below "
            f"{criteria.min_static_hedge_pools}"
        )
    if (
        criteria.require_contextual_bandit
        and bandit.qualified_records < 1
    ):
        reasons.append(
            "qualified contextual-bandit evidence is required"
        )

    ready = not reasons
    if not phase8_promoted:
        status = "RESEARCH_ONLY_PHASE8_BLOCKED"
    elif ready:
        status = "RESEARCH_BUNDLE_READY"
    else:
        status = "RESEARCH_BUNDLE_INCOMPLETE"

    return Phase9ResearchBundleReport(
        phase8_promoted=phase8_promoted,
        research_only=True,
        policy_actionable=False,
        status=status,
        criteria=criteria,
        adaptive_multi_pool=adaptive,
        mint_risk=mint,
        wallet_flow=wallet,
        portfolio_allocation=allocation,
        static_hedge=hedge,
        contextual_bandit=bandit,
        research_ready=ready,
        reasons=tuple(reasons),
    )


def persist_phase9_research_bundle(
    storage: Storage,
    *,
    report: Phase9ResearchBundleReport,
) -> int:
    if report.policy_actionable or not report.research_only:
        raise ValueError(
            "Phase 9 research bundle must remain non-actionable"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
        pool_address="__PHASE9_RESEARCH__",
        as_of=None,
        status=report.status,
        qualified=report.research_ready,
        evidence=report.to_record(),
    )
