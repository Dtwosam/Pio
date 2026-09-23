from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any

from .contextual_bandit import CONTEXTUAL_BANDIT_EVIDENCE_TYPE
from .mint_risk import MINT_RISK_EVIDENCE_TYPE
from .phase9_bandit_dataset import PHASE9_BANDIT_DATASET_EVIDENCE_TYPE
from .phase9_explicit_inputs import load_phase9_explicit_inputs
from .phase9_research import PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE
from .portfolio_allocation import (
    PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    PORTFOLIO_CANDIDATE_EVIDENCE_TYPE,
)
from .static_hedge import STATIC_HEDGE_EVIDENCE_TYPE
from .storage import Storage
from .wallet_flow import WALLET_FLOW_EVIDENCE_TYPE


@dataclass(frozen=True)
class Phase9SourceFreshnessItem:
    family: str
    current: bool
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9SourceFreshnessReport:
    current: bool
    families: tuple[Phase9SourceFreshnessItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    def by_family(self) -> dict[str, bool]:
        return {item.family: item.current for item in self.families}


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Phase 9 source freshness timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _latest_rows(
    storage: Storage,
    *,
    edge_type: str,
) -> list[dict[str, Any]]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.created_at, e.pool_address,
                   e.qualified, e.evidence_json
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
    output: list[dict[str, Any]] = []
    for row in rows:
        try:
            evidence = json.loads(str(row[4]))
        except (TypeError, ValueError, json.JSONDecodeError):
            evidence = None
        output.append(
            {
                "id": int(row[0]),
                "created_at": str(row[1]),
                "pool_address": str(row[2]),
                "qualified": bool(row[3]),
                "evidence": evidence,
            }
        )
    return output


def _latest_pool_snapshot_id(
    storage: Storage,
    *,
    pool_address: str,
) -> int | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (pool_address,),
        ).fetchone()
    return int(row[0]) if row is not None else None


def _latest_pool_observed_at(
    storage: Storage,
    *,
    pool_address: str,
) -> str | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT observed_at
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (pool_address,),
        ).fetchone()
    return str(row[0]) if row is not None else None


def _latest_mint_snapshot_id(
    storage: Storage,
    *,
    mint_address: str,
) -> int | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM token_mint_snapshots
            WHERE mint_address = ?
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (mint_address,),
        ).fetchone()
    return int(row[0]) if row is not None else None


def _latest_wallet_event_id(
    storage: Storage,
    *,
    pool_address: str,
) -> int | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM position_event_history
            WHERE pool_address = ?
            ORDER BY julianday(created_at) DESC, id DESC
            LIMIT 1
            """,
            (pool_address,),
        ).fetchone()
    return int(row[0]) if row is not None else None


def _adaptive_current(storage: Storage) -> Phase9SourceFreshnessItem:
    rows = _latest_rows(
        storage,
        edge_type=PHASE9_ADAPTIVE_MULTI_POOL_EVIDENCE_TYPE,
    )
    if not rows:
        return Phase9SourceFreshnessItem(
            family="adaptive_regime",
            current=False,
            reason="adaptive/regime evidence is missing",
        )
    evidence = rows[0]["evidence"]
    pools = evidence.get("pools") if isinstance(evidence, dict) else None
    if not isinstance(pools, list) or not pools:
        return Phase9SourceFreshnessItem(
            family="adaptive_regime",
            current=False,
            reason="adaptive/regime evidence has no source pools",
        )
    for item in pools:
        if not isinstance(item, dict):
            return Phase9SourceFreshnessItem(
                family="adaptive_regime",
                current=False,
                reason="adaptive/regime source pool metadata is malformed",
            )
        pool = str(item.get("pool_address", "")).strip()
        adaptive = item.get("adaptive")
        ids = (
            adaptive.get("source_snapshot_ids")
            if isinstance(adaptive, dict)
            else None
        )
        if not pool or not isinstance(ids, list) or not ids:
            return Phase9SourceFreshnessItem(
                family="adaptive_regime",
                current=False,
                reason="adaptive/regime source snapshot lineage is incomplete",
            )
        try:
            used_latest = max(int(value) for value in ids)
        except (TypeError, ValueError):
            return Phase9SourceFreshnessItem(
                family="adaptive_regime",
                current=False,
                reason="adaptive/regime source snapshot IDs are invalid",
            )
        current_latest = _latest_pool_snapshot_id(
            storage,
            pool_address=pool,
        )
        if current_latest is None or current_latest != used_latest:
            return Phase9SourceFreshnessItem(
                family="adaptive_regime",
                current=False,
                reason=(
                    f"pool {pool} chain history advanced from snapshot "
                    f"{used_latest} to {current_latest}"
                ),
            )
    return Phase9SourceFreshnessItem(
        family="adaptive_regime",
        current=True,
        reason="adaptive/regime evidence uses the latest persisted pool snapshots",
    )


def _mint_current(storage: Storage) -> Phase9SourceFreshnessItem:
    rows = [
        row
        for row in _latest_rows(storage, edge_type=MINT_RISK_EVIDENCE_TYPE)
        if row["qualified"]
    ]
    if not rows:
        return Phase9SourceFreshnessItem(
            family="mint_risk",
            current=False,
            reason="qualified mint-risk evidence is missing",
        )
    for row in rows:
        evidence = row["evidence"]
        if not isinstance(evidence, dict):
            return Phase9SourceFreshnessItem(
                family="mint_risk",
                current=False,
                reason="mint-risk evidence is malformed",
            )
        pool = str(row["pool_address"])
        try:
            used_pool_id = int(evidence["pool_snapshot_id"])
        except (KeyError, TypeError, ValueError):
            return Phase9SourceFreshnessItem(
                family="mint_risk",
                current=False,
                reason="mint-risk pool snapshot lineage is incomplete",
            )
        latest_pool_id = _latest_pool_snapshot_id(
            storage,
            pool_address=pool,
        )
        if latest_pool_id is None or latest_pool_id != used_pool_id:
            return Phase9SourceFreshnessItem(
                family="mint_risk",
                current=False,
                reason=(
                    f"pool {pool} source snapshot advanced from "
                    f"{used_pool_id} to {latest_pool_id}"
                ),
            )

        assessments = evidence.get("assessments")
        if not isinstance(assessments, list) or not assessments:
            return Phase9SourceFreshnessItem(
                family="mint_risk",
                current=False,
                reason="mint-risk mint snapshot lineage is incomplete",
            )
        for assessment in assessments:
            if not isinstance(assessment, dict):
                return Phase9SourceFreshnessItem(
                    family="mint_risk",
                    current=False,
                    reason="mint-risk assessment metadata is malformed",
                )
            mint = str(assessment.get("mint_address", "")).strip()
            try:
                used_mint_id = int(assessment["mint_snapshot_id"])
            except (KeyError, TypeError, ValueError):
                return Phase9SourceFreshnessItem(
                    family="mint_risk",
                    current=False,
                    reason=f"mint-risk snapshot lineage is incomplete for {mint}",
                )
            latest_mint_id = _latest_mint_snapshot_id(
                storage,
                mint_address=mint,
            )
            if latest_mint_id is None or latest_mint_id != used_mint_id:
                return Phase9SourceFreshnessItem(
                    family="mint_risk",
                    current=False,
                    reason=(
                        f"mint {mint} source snapshot advanced from "
                        f"{used_mint_id} to {latest_mint_id}"
                    ),
                )
    return Phase9SourceFreshnessItem(
        family="mint_risk",
        current=True,
        reason="mint-risk evidence uses the latest persisted pool/mint snapshots",
    )


def _wallet_current(storage: Storage) -> Phase9SourceFreshnessItem:
    rows = [
        row
        for row in _latest_rows(storage, edge_type=WALLET_FLOW_EVIDENCE_TYPE)
        if row["qualified"]
    ]
    if not rows:
        return Phase9SourceFreshnessItem(
            family="wallet_flow",
            current=False,
            reason="qualified wallet-flow evidence is missing",
        )
    for row in rows:
        evidence = row["evidence"]
        ids = (
            evidence.get("source_event_ids")
            if isinstance(evidence, dict)
            else None
        )
        if not isinstance(ids, list) or not ids:
            return Phase9SourceFreshnessItem(
                family="wallet_flow",
                current=False,
                reason="wallet-flow source event lineage is incomplete",
            )
        try:
            used_latest = max(int(value) for value in ids)
        except (TypeError, ValueError):
            return Phase9SourceFreshnessItem(
                family="wallet_flow",
                current=False,
                reason="wallet-flow source event IDs are invalid",
            )
        pool = str(row["pool_address"])
        latest_event = _latest_wallet_event_id(
            storage,
            pool_address=pool,
        )
        if latest_event is None or latest_event != used_latest:
            return Phase9SourceFreshnessItem(
                family="wallet_flow",
                current=False,
                reason=(
                    f"pool {pool} wallet-flow history advanced from event "
                    f"{used_latest} to {latest_event}"
                ),
            )
    return Phase9SourceFreshnessItem(
        family="wallet_flow",
        current=True,
        reason="wallet-flow evidence uses the latest persisted pool events",
    )


def _static_hedge_current(storage: Storage) -> Phase9SourceFreshnessItem:
    rows = [
        row
        for row in _latest_rows(storage, edge_type=STATIC_HEDGE_EVIDENCE_TYPE)
        if row["qualified"]
    ]
    if not rows:
        return Phase9SourceFreshnessItem(
            family="static_hedge",
            current=False,
            reason="qualified static-hedge evidence is missing",
        )
    for row in rows:
        evidence = row["evidence"]
        observations = (
            evidence.get("source_observations")
            if isinstance(evidence, dict)
            else None
        )
        if not isinstance(observations, list) or not observations:
            return Phase9SourceFreshnessItem(
                family="static_hedge",
                current=False,
                reason="static-hedge source path lineage is incomplete",
            )
        try:
            used_latest = max(
                int(item["pool_snapshot_id"])
                for item in observations
                if isinstance(item, dict)
            )
        except (KeyError, TypeError, ValueError):
            return Phase9SourceFreshnessItem(
                family="static_hedge",
                current=False,
                reason="static-hedge source snapshot IDs are invalid",
            )
        pool = str(row["pool_address"])
        latest_pool = _latest_pool_snapshot_id(
            storage,
            pool_address=pool,
        )
        if latest_pool is None or latest_pool != used_latest:
            return Phase9SourceFreshnessItem(
                family="static_hedge",
                current=False,
                reason=(
                    f"pool {pool} hedge price path advanced from snapshot "
                    f"{used_latest} to {latest_pool}"
                ),
            )
    return Phase9SourceFreshnessItem(
        family="static_hedge",
        current=True,
        reason="static-hedge evidence uses the latest persisted price path",
    )


def _portfolio_current(storage: Storage) -> Phase9SourceFreshnessItem:
    allocation_rows = _latest_rows(
        storage,
        edge_type=PORTFOLIO_ALLOCATION_EVIDENCE_TYPE,
    )
    if not allocation_rows:
        return Phase9SourceFreshnessItem(
            family="portfolio_allocation",
            current=False,
            reason="portfolio-allocation evidence is missing",
        )
    allocation = allocation_rows[0]["evidence"]
    lineage = (
        allocation.get("candidate_lineage")
        if isinstance(allocation, dict)
        else None
    )
    if not isinstance(lineage, dict):
        return Phase9SourceFreshnessItem(
            family="portfolio_allocation",
            current=False,
            reason="portfolio candidate lineage is missing",
        )
    try:
        candidate_id = int(lineage["candidate_evidence_id"])
    except (KeyError, TypeError, ValueError):
        return Phase9SourceFreshnessItem(
            family="portfolio_allocation",
            current=False,
            reason="portfolio candidate evidence ID is invalid",
        )

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT created_at, evidence_json
            FROM advanced_edge_evidence
            WHERE id = ?
              AND edge_type = ?
              AND pool_address = '__PORTFOLIO_CANDIDATES__'
            LIMIT 1
            """,
            (candidate_id, PORTFOLIO_CANDIDATE_EVIDENCE_TYPE),
        ).fetchone()
    if row is None:
        return Phase9SourceFreshnessItem(
            family="portfolio_allocation",
            current=False,
            reason="portfolio candidate artifact is missing",
        )
    created_at = str(row[0])
    try:
        candidate = json.loads(str(row[1]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return Phase9SourceFreshnessItem(
            family="portfolio_allocation",
            current=False,
            reason="portfolio candidate artifact is malformed",
        )
    source_inputs = candidate.get("source_inputs")
    if not isinstance(source_inputs, list) or not source_inputs:
        return Phase9SourceFreshnessItem(
            family="portfolio_allocation",
            current=False,
            reason="portfolio candidate source inputs are missing",
        )
    try:
        created = _time(created_at)
    except ValueError:
        return Phase9SourceFreshnessItem(
            family="portfolio_allocation",
            current=False,
            reason="portfolio candidate creation time is invalid",
        )
    for item in source_inputs:
        if not isinstance(item, dict):
            return Phase9SourceFreshnessItem(
                family="portfolio_allocation",
                current=False,
                reason="portfolio source input metadata is malformed",
            )
        pool = str(item.get("pool_address", "")).strip()
        latest = _latest_pool_observed_at(
            storage,
            pool_address=pool,
        )
        if not pool or latest is None:
            return Phase9SourceFreshnessItem(
                family="portfolio_allocation",
                current=False,
                reason=f"portfolio source pool {pool or '<missing>'} has no chain snapshot",
            )
        try:
            advanced = _time(latest) > created
        except ValueError:
            return Phase9SourceFreshnessItem(
                family="portfolio_allocation",
                current=False,
                reason=f"portfolio source pool {pool} has an invalid timestamp",
            )
        if advanced:
            return Phase9SourceFreshnessItem(
                family="portfolio_allocation",
                current=False,
                reason=(
                    f"pool {pool} chain history advanced after candidate "
                    f"artifact {candidate_id}"
                ),
            )
    return Phase9SourceFreshnessItem(
        family="portfolio_allocation",
        current=True,
        reason="portfolio candidate artifact is newer than its source pool snapshots",
    )


def _latest_retraining_dataset_cycle(storage: Storage) -> str | None:
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
    cycle_id = str(payload.get("cycle_id", "")).strip()
    return cycle_id or None


def _common_pool_cutoff(
    storage: Storage,
    *,
    pools: tuple[str, ...],
) -> str | None:
    latest: list[datetime] = []
    for pool in pools:
        value = _latest_pool_observed_at(
            storage,
            pool_address=pool,
        )
        if value is None:
            return None
        try:
            latest.append(_time(value))
        except ValueError:
            return None
    return min(latest).isoformat() if latest else None


def _bandit_current(storage: Storage) -> Phase9SourceFreshnessItem:
    rows = _latest_rows(
        storage,
        edge_type=CONTEXTUAL_BANDIT_EVIDENCE_TYPE,
    )
    if not rows or not rows[0]["qualified"]:
        return Phase9SourceFreshnessItem(
            family="contextual_bandit",
            current=False,
            reason="qualified contextual-bandit evidence is missing",
        )
    evidence = rows[0]["evidence"]
    lineage = (
        evidence.get("dataset_lineage")
        if isinstance(evidence, dict)
        else None
    )
    if not isinstance(lineage, dict):
        return Phase9SourceFreshnessItem(
            family="contextual_bandit",
            current=False,
            reason="contextual-bandit dataset lineage is missing",
        )

    if str(lineage.get("source_type", "")) == PHASE9_BANDIT_DATASET_EVIDENCE_TYPE:
        try:
            explicit_id = int(lineage["explicit_input_evidence_id"])
            stored_cutoff = _time(str(lineage["cutoff"]))
        except (KeyError, TypeError, ValueError):
            return Phase9SourceFreshnessItem(
                family="contextual_bandit",
                current=False,
                reason="Phase 9 bandit dataset lineage is incomplete",
            )
        try:
            artifact = load_phase9_explicit_inputs(
                storage,
                evidence_id=explicit_id,
            )
        except ValueError:
            artifact = None
        if artifact is None:
            return Phase9SourceFreshnessItem(
                family="contextual_bandit",
                current=False,
                reason="Phase 9 bandit explicit input artifact is unavailable",
            )
        pools = tuple(
            item.pool_address for item in artifact.inputs.pool_inputs
        )
        cutoff = _common_pool_cutoff(storage, pools=pools)
        if cutoff is None:
            return Phase9SourceFreshnessItem(
                family="contextual_bandit",
                current=False,
                reason="Phase 9 bandit source pools are incomplete",
            )
        if _time(cutoff) > stored_cutoff:
            return Phase9SourceFreshnessItem(
                family="contextual_bandit",
                current=False,
                reason=(
                    "Phase 9 bandit common source cutoff advanced from "
                    f"{stored_cutoff.isoformat()} to {cutoff}"
                ),
            )
        return Phase9SourceFreshnessItem(
            family="contextual_bandit",
            current=True,
            reason="Phase 9 bandit dataset uses the latest common source cutoff",
        )

    cycle_id = str(lineage.get("cycle_id", "")).strip()
    if not cycle_id:
        return Phase9SourceFreshnessItem(
            family="contextual_bandit",
            current=False,
            reason="contextual-bandit retraining-cycle lineage is incomplete",
        )
    latest_cycle = _latest_retraining_dataset_cycle(storage)
    if latest_cycle is not None and latest_cycle != cycle_id:
        return Phase9SourceFreshnessItem(
            family="contextual_bandit",
            current=False,
            reason=(
                f"a newer retraining dataset cycle {latest_cycle} is available "
                f"after bandit cycle {cycle_id}"
            ),
        )
    return Phase9SourceFreshnessItem(
        family="contextual_bandit",
        current=True,
        reason="contextual-bandit evidence uses the latest available dataset lineage",
    )


def evaluate_phase9_source_freshness(
    storage: Storage,
) -> Phase9SourceFreshnessReport:
    families = (
        _adaptive_current(storage),
        _mint_current(storage),
        _wallet_current(storage),
        _portfolio_current(storage),
        _static_hedge_current(storage),
        _bandit_current(storage),
    )
    return Phase9SourceFreshnessReport(
        current=all(item.current for item in families),
        families=families,
    )
