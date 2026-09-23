from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .contextual_bandit import CONTEXTUAL_BANDIT_EVIDENCE_TYPE
from .mint_risk import MINT_RISK_EVIDENCE_TYPE
from .phase9_research import PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE
from .phase9_validation import (
    Phase9ResearchBundleCriteria,
    _adaptive_snapshot_lineage_valid,
    _allocation_lineage_valid,
    _bandit_lineage_valid,
    _latest_by_pool,
    _mint_lineage_valid,
    _static_hedge_lineage_valid,
    _summary,
    _wallet_flow_lineage_valid,
)
from .portfolio_allocation import PORTFOLIO_ALLOCATION_EVIDENCE_TYPE
from .static_hedge import STATIC_HEDGE_EVIDENCE_TYPE
from .storage import Storage
from .wallet_flow import WALLET_FLOW_EVIDENCE_TYPE


@dataclass(frozen=True)
class Phase9ReplayFamilyAudit:
    family: str
    edge_type: str
    required_records: int
    latest_records: int
    qualified_records: int
    latest_evidence_ids: tuple[int, ...]
    qualified_evidence_ids: tuple[int, ...]
    boundary_valid: bool
    replay_verified: bool
    status: str
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9ReplayAuditReport:
    research_only: bool
    policy_actionable: bool
    verified: bool
    families: tuple[Phase9ReplayFamilyAudit, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _qualified_ids(
    storage: Storage,
    *,
    edge_type: str,
) -> tuple[int, ...]:
    rows = _latest_by_pool(storage, edge_type=edge_type)
    return tuple(
        int(row["id"])
        for row in rows
        if row["qualified"]
        and row["evidence"].get("research_qualified") is True
        and row["evidence"].get("research_only") is True
        and row["evidence"].get("policy_actionable") is False
    )


def _family_audit(
    storage: Storage,
    *,
    family: str,
    edge_type: str,
    required_records: int,
    replay_check,
) -> Phase9ReplayFamilyAudit:
    summary = _summary(storage, edge_type=edge_type)
    qualified_ids = _qualified_ids(storage, edge_type=edge_type)

    if summary.latest_records == 0:
        replay_verified = False
        status = "MISSING_EVIDENCE"
        reason = "no persisted evidence exists"
    elif not summary.boundary_valid:
        replay_verified = False
        status = "BOUNDARY_INVALID"
        reason = "latest evidence violates research-only boundary"
    elif summary.qualified_records < required_records:
        replay_verified = False
        status = "INSUFFICIENT_QUALIFIED_EVIDENCE"
        reason = (
            f"qualified records {summary.qualified_records} are below "
            f"required {required_records}"
        )
    else:
        try:
            replay_verified = bool(replay_check(storage))
        except Exception as exc:
            replay_verified = False
            status = "REPLAY_ERROR"
            reason = (
                "replay evaluator raised "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            if replay_verified:
                status = "REPLAY_VERIFIED"
                reason = (
                    "qualified evidence reproduces from immutable sources"
                )
            else:
                status = "REPLAY_MISMATCH"
                reason = (
                    "qualified evidence does not deterministically reproduce "
                    "from its persisted source lineage"
                )

    return Phase9ReplayFamilyAudit(
        family=family,
        edge_type=edge_type,
        required_records=required_records,
        latest_records=summary.latest_records,
        qualified_records=summary.qualified_records,
        latest_evidence_ids=summary.latest_evidence_ids,
        qualified_evidence_ids=qualified_ids,
        boundary_valid=summary.boundary_valid,
        replay_verified=replay_verified,
        status=status,
        reason=reason,
    )


def evaluate_phase9_replay_audit(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = Phase9ResearchBundleCriteria(),
) -> Phase9ReplayAuditReport:
    families = (
        _family_audit(
            storage,
            family="adaptive_regime",
            edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
            required_records=1 if criteria.require_adaptive_multi_pool else 0,
            replay_check=_adaptive_snapshot_lineage_valid,
        ),
        _family_audit(
            storage,
            family="mint_risk",
            edge_type=MINT_RISK_EVIDENCE_TYPE,
            required_records=criteria.min_mint_risk_pools,
            replay_check=_mint_lineage_valid,
        ),
        _family_audit(
            storage,
            family="wallet_flow",
            edge_type=WALLET_FLOW_EVIDENCE_TYPE,
            required_records=criteria.min_wallet_flow_pools,
            replay_check=_wallet_flow_lineage_valid,
        ),
        _family_audit(
            storage,
            family="portfolio_allocation",
            edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
            required_records=1 if criteria.require_portfolio_allocation else 0,
            replay_check=_allocation_lineage_valid,
        ),
        _family_audit(
            storage,
            family="static_hedge",
            edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
            required_records=criteria.min_static_hedge_pools,
            replay_check=_static_hedge_lineage_valid,
        ),
        _family_audit(
            storage,
            family="contextual_bandit",
            edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
            required_records=1 if criteria.require_contextual_bandit else 0,
            replay_check=_bandit_lineage_valid,
        ),
    )

    required = tuple(
        item for item in families if item.required_records > 0
    )
    verified = all(item.replay_verified for item in required)
    reasons = tuple(
        f"{item.family}: {item.reason}"
        for item in required
        if not item.replay_verified
    )

    return Phase9ReplayAuditReport(
        research_only=True,
        policy_actionable=False,
        verified=verified,
        families=families,
        reasons=reasons,
    )
