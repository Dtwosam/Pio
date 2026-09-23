from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .chain_snapshot_lineage import (
    chain_snapshot_source_record,
    chain_snapshot_source_sha256,
)
from .contextual_bandit import CONTEXTUAL_BANDIT_EVIDENCE_TYPE
from .mint_risk import MINT_RISK_EVIDENCE_TYPE
from .phase9_research import PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE
from .phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from .portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
    portfolio_candidate_artifact_sha256,
)
from .static_hedge import (
    STATIC_HEDGE_EVIDENCE_TYPE,
    StaticHedgeSourceObservation,
    static_hedge_source_sha256,
)
from .storage import Storage
from .wallet_flow import (
    WALLET_FLOW_EVIDENCE_TYPE,
    wallet_flow_source_sha256,
)


PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE = "PHASE9_RESEARCH_BUNDLE_V1"


@dataclass(frozen=True)
class Phase9ResearchBundleCriteria:
    min_mint_risk_pools: int = 2
    min_wallet_flow_pools: int = 2
    require_wallet_flow_lineage: bool = True
    require_mint_snapshot_lineage: bool = True
    min_static_hedge_pools: int = 1
    require_static_hedge_lineage: bool = True
    require_adaptive_multi_pool: bool = True
    require_adaptive_snapshot_lineage: bool = True
    require_portfolio_allocation: bool = True
    require_portfolio_allocation_lineage: bool = True
    require_contextual_bandit: bool = True
    require_contextual_bandit_lineage: bool = True

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


def _adaptive_snapshot_lineage_valid(
    storage: Storage,
) -> bool:
    rows = _latest_by_pool(
        storage,
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
    )
    qualified = [row for row in rows if row["qualified"]]
    if not qualified:
        return False

    with storage.connect() as conn:
        for row in qualified:
            pools = row["evidence"].get("pools")
            if not isinstance(pools, list) or not pools:
                return False
            for item in pools:
                if not isinstance(item, dict):
                    return False
                pool_address = str(
                    item.get("pool_address", "")
                ).strip()
                if not pool_address:
                    return False

                for family in ("adaptive", "regime"):
                    report = item.get(family)
                    if not isinstance(report, dict):
                        return False
                    raw_ids = report.get("source_snapshot_ids")
                    expected_sha = str(
                        report.get("source_snapshot_sha256", "")
                    ).strip()
                    if (
                        not isinstance(raw_ids, list)
                        or not raw_ids
                        or not expected_sha
                    ):
                        return False
                    try:
                        snapshot_ids = [
                            int(value) for value in raw_ids
                        ]
                    except (TypeError, ValueError):
                        return False
                    if len(snapshot_ids) != len(set(snapshot_ids)):
                        return False

                    records: list[dict[str, Any]] = []
                    for snapshot_id in snapshot_ids:
                        source = conn.execute(
                            """
                            SELECT id, pool_address,
                                   observed_at, active_bin_id
                            FROM chain_pool_snapshots
                            WHERE id = ?
                            """,
                            (snapshot_id,),
                        ).fetchone()
                        if source is None:
                            return False
                        if str(source[1]) != pool_address:
                            return False
                        as_of = report.get("as_of")
                        if (
                            as_of is not None
                            and conn.execute(
                                """
                                SELECT julianday(?) <= julianday(?)
                                """,
                                (
                                    str(source[2]),
                                    str(as_of),
                                ),
                            ).fetchone()[0]
                            != 1
                        ):
                            return False
                        records.append(
                            chain_snapshot_source_record(source)
                        )

                    if (
                        chain_snapshot_source_sha256(records)
                        != expected_sha
                    ):
                        return False
    return True


def _mint_lineage_valid(storage: Storage) -> bool:
    rows = _latest_by_pool(
        storage,
        edge_type=MINT_RISK_EVIDENCE_TYPE,
    )
    qualified = [row for row in rows if row["qualified"]]
    if not qualified:
        return False

    with storage.connect() as conn:
        for row in qualified:
            evidence = row["evidence"]
            try:
                pool_snapshot_id = int(evidence["pool_snapshot_id"])
            except (KeyError, TypeError, ValueError):
                return False
            pool_row = conn.execute(
                """
                SELECT pool_address
                FROM chain_pool_snapshots
                WHERE id = ?
                """,
                (pool_snapshot_id,),
            ).fetchone()
            if (
                pool_row is None
                or str(pool_row[0]) != str(row["pool_address"])
            ):
                return False

            assessments = evidence.get("assessments")
            if not isinstance(assessments, list) or not assessments:
                return False
            for item in assessments:
                if not isinstance(item, dict):
                    return False
                try:
                    snapshot_id = int(item["mint_snapshot_id"])
                except (KeyError, TypeError, ValueError):
                    return False
                mint_row = conn.execute(
                    """
                    SELECT mint_address, observed_at
                    FROM token_mint_snapshots
                    WHERE id = ?
                    """,
                    (snapshot_id,),
                ).fetchone()
                if mint_row is None:
                    return False
                if str(mint_row[0]) != str(item.get("mint_address", "")):
                    return False
                if str(mint_row[1]) != str(item.get("observed_at", "")):
                    return False
    return True


def _wallet_flow_lineage_valid(storage: Storage) -> bool:
    rows = _latest_by_pool(
        storage,
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
    )
    qualified = [row for row in rows if row["qualified"]]
    if not qualified:
        return False

    with storage.connect() as conn:
        for row in qualified:
            evidence = row["evidence"]
            raw_ids = evidence.get("source_event_ids")
            expected_sha = str(
                evidence.get("source_event_sha256", "")
            ).strip()
            if (
                not isinstance(raw_ids, list)
                or not raw_ids
                or not expected_sha
            ):
                return False
            try:
                event_ids = [int(value) for value in raw_ids]
            except (TypeError, ValueError):
                return False
            if len(event_ids) != len(set(event_ids)):
                return False

            records: list[dict[str, Any]] = []
            for event_id in event_ids:
                source = conn.execute(
                    """
                    SELECT id, created_at, user_address, event_type,
                           total_usd, signature, ix_index,
                           position_address, pool_address
                    FROM position_event_history
                    WHERE id = ?
                    """,
                    (event_id,),
                ).fetchone()
                if source is None:
                    return False
                if str(source[8]) != str(row["pool_address"]):
                    return False
                if (
                    row["as_of"] is not None
                    and conn.execute(
                        """
                        SELECT julianday(?) <= julianday(?)
                        """,
                        (str(source[1]), str(row["as_of"])),
                    ).fetchone()[0]
                    != 1
                ):
                    return False
                records.append(
                    {
                        "id": int(source[0]),
                        "created_at": str(source[1]),
                        "user_address": str(source[2]),
                        "event_type": str(source[3]),
                        "total_usd": str(source[4]),
                        "signature": str(source[5]),
                        "ix_index": int(source[6]),
                        "position_address": str(source[7]),
                    }
                )

            if wallet_flow_source_sha256(records) != expected_sha:
                return False
    return True


def _allocation_lineage_valid(storage: Storage) -> bool:
    rows = _latest_by_pool(
        storage,
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    )
    if not rows:
        return False
    lineage = rows[0]["evidence"].get("candidate_lineage")
    if not isinstance(lineage, dict):
        return False
    try:
        evidence_id = int(lineage["candidate_evidence_id"])
    except (KeyError, TypeError, ValueError):
        return False
    expected_sha = str(
        lineage.get("candidate_evidence_sha256", "")
    ).strip()
    if not expected_sha:
        return False

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT edge_type, pool_address, status, qualified,
                   evidence_json
            FROM advanced_edge_evidence
            WHERE id = ?
            """,
            (evidence_id,),
        ).fetchone()
    if row is None:
        return False
    if str(row[0]) != PORTFOLIO_CANDIDATE_EVIDENCE_TYPE:
        return False
    if str(row[1]) != "__PORTFOLIO_CANDIDATES__":
        return False
    if str(row[2]) != "BUILT" or not bool(row[3]):
        return False
    try:
        evidence = json.loads(str(row[4]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if (
        evidence.get("research_only") is not True
        or evidence.get("policy_actionable") is not False
        or not isinstance(evidence.get("comparison"), dict)
        or not isinstance(evidence.get("source_inputs"), list)
        or not isinstance(evidence.get("assumptions"), dict)
    ):
        return False
    payload = {
        "research_only": True,
        "policy_actionable": False,
        "source_inputs": evidence["source_inputs"],
        "assumptions": evidence["assumptions"],
        "comparison": evidence["comparison"],
    }
    recomputed_sha = portfolio_candidate_artifact_sha256(payload)
    return (
        str(evidence.get("artifact_sha256", "")).strip()
        == expected_sha
        == recomputed_sha
    )


def _static_hedge_lineage_valid(
    storage: Storage,
) -> bool:
    rows = _latest_by_pool(
        storage,
        edge_type=STATIC_HEDGE_EVIDENCE_TYPE,
    )
    qualified = [row for row in rows if row["qualified"]]
    if not qualified:
        return False

    with storage.connect() as conn:
        for row in qualified:
            evidence = row["evidence"]
            raw_observations = evidence.get("source_observations")
            expected_sha = str(
                evidence.get("source_path_sha256", "")
            ).strip()
            if (
                not isinstance(raw_observations, list)
                or not raw_observations
                or not expected_sha
            ):
                return False

            observations: list[StaticHedgeSourceObservation] = []
            for item in raw_observations:
                if not isinstance(item, dict):
                    return False
                try:
                    observation = StaticHedgeSourceObservation(
                        pool_snapshot_id=int(item["pool_snapshot_id"]),
                        bin_liquidity_snapshot_id=int(
                            item["bin_liquidity_snapshot_id"]
                        ),
                        pool_address=str(item["pool_address"]),
                        observed_at=str(item["observed_at"]),
                        active_bin_id=int(item["active_bin_id"]),
                        price_q64=int(item["price_q64"]),
                    )
                except (KeyError, TypeError, ValueError):
                    return False
                if observation.pool_address != str(
                    row["pool_address"]
                ):
                    return False

                pool_row = conn.execute(
                    """
                    SELECT pool_address, observed_at, active_bin_id
                    FROM chain_pool_snapshots
                    WHERE id = ?
                    """,
                    (observation.pool_snapshot_id,),
                ).fetchone()
                if pool_row is None:
                    return False
                if (
                    str(pool_row[0]) != observation.pool_address
                    or str(pool_row[1]) != observation.observed_at
                    or int(pool_row[2]) != observation.active_bin_id
                ):
                    return False

                bin_row = conn.execute(
                    """
                    SELECT pool_address, observed_at, bin_id, price
                    FROM bin_liquidity_snapshots
                    WHERE id = ?
                    """,
                    (observation.bin_liquidity_snapshot_id,),
                ).fetchone()
                if bin_row is None:
                    return False
                if (
                    str(bin_row[0]) != observation.pool_address
                    or str(bin_row[1]) != observation.observed_at
                    or int(bin_row[2]) != observation.active_bin_id
                    or int(str(bin_row[3])) != observation.price_q64
                ):
                    return False

                as_of = evidence.get("as_of")
                if (
                    as_of is not None
                    and conn.execute(
                        """
                        SELECT julianday(?) <= julianday(?)
                        """,
                        (
                            observation.observed_at,
                            str(as_of),
                        ),
                    ).fetchone()[0]
                    != 1
                ):
                    return False
                observations.append(observation)

            if static_hedge_source_sha256(observations) != expected_sha:
                return False
    return True


def _bandit_lineage_valid(storage: Storage) -> bool:
    rows = _latest_by_pool(
        storage,
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
    )
    if not rows:
        return False
    row = rows[0]
    lineage = row["evidence"].get("dataset_lineage")
    if not isinstance(lineage, dict):
        return False

    required = (
        "cycle_id",
        "champion_model_id",
        "dataset_evidence_id",
        "dataset_version",
        "dataset_sha256",
        "cutoff",
        "output_file",
    )
    if any(lineage.get(field) in (None, "") for field in required):
        return False

    try:
        evidence_id = int(lineage["dataset_evidence_id"])
    except (TypeError, ValueError):
        return False

    with storage.connect() as conn:
        evidence_row = conn.execute(
            """
            SELECT model_id, evidence_type, status, evidence_json
            FROM model_live_evidence
            WHERE id = ?
            """,
            (evidence_id,),
        ).fetchone()
        cycle_row = conn.execute(
            """
            SELECT champion_model_id, plan_as_of,
                   target_dataset_version
            FROM continuous_learning_cycles
            WHERE cycle_id = ?
            """,
            (str(lineage["cycle_id"]),),
        ).fetchone()

    if evidence_row is None or cycle_row is None:
        return False
    if str(evidence_row[0]) != str(lineage["champion_model_id"]):
        return False
    if str(evidence_row[1]) != "CONTINUOUS_RETRAIN_DATASET_V1":
        return False
    if str(evidence_row[2]) != "BUILT":
        return False

    try:
        payload = json.loads(str(evidence_row[3]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    dataset = payload.get("dataset")
    if not isinstance(dataset, dict):
        return False

    return (
        str(payload.get("cycle_id", ""))
        == str(lineage["cycle_id"])
        and str(payload.get("cutoff", ""))
        == str(lineage["cutoff"])
        and str(payload.get("target_dataset_version", ""))
        == str(lineage["dataset_version"])
        and str(payload.get("output_file", ""))
        == str(lineage["output_file"])
        and str(dataset.get("dataset_version", ""))
        == str(lineage["dataset_version"])
        and str(dataset.get("dataset_sha256", ""))
        == str(lineage["dataset_sha256"])
        and str(cycle_row[0])
        == str(lineage["champion_model_id"])
        and str(cycle_row[1]) == str(lineage["cutoff"])
        and str(cycle_row[2])
        == str(lineage["dataset_version"])
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
    if (
        criteria.require_adaptive_multi_pool
        and criteria.require_adaptive_snapshot_lineage
        and adaptive.qualified_records >= 1
        and not _adaptive_snapshot_lineage_valid(storage)
    ):
        reasons.append(
            "qualified adaptive/regime evidence must resolve to immutable "
            "chain snapshot IDs with matching source hashes"
        )
    if mint.qualified_records < criteria.min_mint_risk_pools:
        reasons.append(
            f"qualified mint-risk pools {mint.qualified_records} are below "
            f"{criteria.min_mint_risk_pools}"
        )
    if (
        criteria.require_mint_snapshot_lineage
        and mint.qualified_records >= criteria.min_mint_risk_pools
        and not _mint_lineage_valid(storage)
    ):
        reasons.append(
            "qualified mint-risk evidence must resolve to authoritative "
            "pool and mint snapshot IDs"
        )
    if wallet.qualified_records < criteria.min_wallet_flow_pools:
        reasons.append(
            f"qualified wallet-flow pools {wallet.qualified_records} are below "
            f"{criteria.min_wallet_flow_pools}"
        )
    if (
        criteria.require_wallet_flow_lineage
        and wallet.qualified_records >= criteria.min_wallet_flow_pools
        and not _wallet_flow_lineage_valid(storage)
    ):
        reasons.append(
            "qualified wallet-flow evidence must reproduce from immutable "
            "source event IDs and SHA-256"
        )
    if (
        criteria.require_wallet_flow_lineage
        and wallet.qualified_records >= criteria.min_wallet_flow_pools
        and not _wallet_flow_lineage_valid(storage)
    ):
        reasons.append(
            "qualified wallet-flow evidence must resolve to immutable "
            "position-event IDs with matching source hash"
        )
    if (
        criteria.require_portfolio_allocation
        and allocation.qualified_records < 1
    ):
        reasons.append(
            "qualified portfolio-allocation evidence is required"
        )
    if (
        criteria.require_portfolio_allocation
        and criteria.require_portfolio_allocation_lineage
        and allocation.qualified_records >= 1
        and not _allocation_lineage_valid(storage)
    ):
        reasons.append(
            "qualified portfolio-allocation evidence must be bound to "
            "immutable candidate-artifact lineage"
        )
    if hedge.qualified_records < criteria.min_static_hedge_pools:
        reasons.append(
            f"qualified static-hedge pools {hedge.qualified_records} are below "
            f"{criteria.min_static_hedge_pools}"
        )
    if (
        criteria.require_static_hedge_lineage
        and hedge.qualified_records >= criteria.min_static_hedge_pools
        and not _static_hedge_lineage_valid(storage)
    ):
        reasons.append(
            "qualified static-hedge evidence must resolve to immutable "
            "pool/bin price-path IDs with matching source hash"
        )
    if (
        criteria.require_contextual_bandit
        and bandit.qualified_records < 1
    ):
        reasons.append(
            "qualified contextual-bandit evidence is required"
        )
    if (
        criteria.require_contextual_bandit
        and criteria.require_contextual_bandit_lineage
        and bandit.qualified_records >= 1
        and not _bandit_lineage_valid(storage)
    ):
        reasons.append(
            "qualified contextual-bandit evidence must be bound to a "
            "checksum-verified retraining dataset lineage"
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


@dataclass(frozen=True)
class Phase9PromotionReport:
    phase8_promoted: bool
    research_only: bool
    policy_actionable: bool
    research_bundle: Phase9ResearchBundleReport
    research_bundle_evidence_id: int | None
    persisted_bundle_matches_current: bool
    promotion_ready: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase9_promotion(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
) -> Phase9PromotionReport:
    bundle = evaluate_phase9_research_bundle(
        storage,
        criteria=criteria,
    )
    latest_bundle = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_RESEARCH_BUNDLE_EVIDENCE_TYPE,
        pool_address="__PHASE9_RESEARCH__",
    )
    bundle_evidence_id = (
        int(latest_bundle["id"])
        if latest_bundle is not None
        else None
    )
    persisted_matches_current = False
    if latest_bundle is not None:
        persisted = latest_bundle["evidence"]
        current = json.loads(
            json.dumps(bundle.to_record(), sort_keys=True)
        )
        persisted_matches_current = all(
            persisted.get(key) == current.get(key)
            for key in (
                "phase8_promoted",
                "research_only",
                "policy_actionable",
                "status",
                "criteria",
                "adaptive_multi_pool",
                "mint_risk",
                "wallet_flow",
                "portfolio_allocation",
                "static_hedge",
                "contextual_bandit",
                "research_ready",
                "reasons",
            )
        )

    reasons: list[str] = []

    if not bundle.phase8_promoted:
        reasons.append(
            "Phase 8 must be persistently promoted before Phase 9"
        )
    if not bundle.research_ready:
        reasons.extend(
            f"research bundle: {reason}"
            for reason in bundle.reasons
        )
    if not bundle.research_only:
        reasons.append(
            "Phase 9 bundle must remain research_only"
        )
    if bundle.policy_actionable:
        reasons.append(
            "Phase 9 promotion cannot grant live-policy authority"
        )
    if latest_bundle is None:
        reasons.append(
            "persisted Phase 9 research-bundle evidence is required"
        )
    elif not bool(latest_bundle["qualified"]):
        reasons.append(
            "persisted Phase 9 research bundle is not qualified"
        )
    elif not persisted_matches_current:
        reasons.append(
            "persisted Phase 9 research bundle is stale versus current evidence"
        )

    return Phase9PromotionReport(
        phase8_promoted=bundle.phase8_promoted,
        research_only=True,
        policy_actionable=False,
        research_bundle=bundle,
        research_bundle_evidence_id=bundle_evidence_id,
        persisted_bundle_matches_current=persisted_matches_current,
        promotion_ready=not reasons,
        reasons=tuple(reasons),
    )
