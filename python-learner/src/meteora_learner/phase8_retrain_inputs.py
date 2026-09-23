from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .continuous_learning import (
    ContinuousLearningCriteria,
    build_continuous_learning_plan,
)
from .ml_retraining_dataset import MLRetrainPoolSpec
from .retraining_workflow import (
    RetrainingDatasetCycleResult,
    start_retraining_cycle_with_dataset,
)
from .storage import Storage


PHASE8_RETRAIN_INPUTS_EVIDENCE_TYPE = "PHASE8_RETRAIN_INPUTS_V1"


@dataclass(frozen=True)
class Phase8RetrainPoolInput:
    pool_address: str
    amount_x: int
    amount_y: int
    network_cost_y_atomic: int


@dataclass(frozen=True)
class Phase8RetrainInputs:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    champion_model_id: str
    champion_dataset_version: str
    pools: tuple[Phase8RetrainPoolInput, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase8RetrainInputsArtifact:
    evidence_id: int
    artifact_sha256: str
    inputs: Phase8RetrainInputs

    def to_record(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "artifact_sha256": self.artifact_sha256,
            "inputs": self.inputs.to_record(),
        }


@dataclass(frozen=True)
class Phase8RetrainInputsAudit:
    exists: bool
    valid: bool
    evidence_id: int | None
    artifact_sha256: str | None
    champion_matches: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase8RetrainBuildRun:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    input_evidence_id: int
    input_artifact_sha256: str
    result: RetrainingDatasetCycleResult

    def to_record(self) -> dict[str, Any]:
        return {
            "research_only": self.research_only,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
            "input_evidence_id": self.input_evidence_id,
            "input_artifact_sha256": self.input_artifact_sha256,
            "result": self.result.to_record(),
        }


def _normalized(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True))


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _candidate_pools(
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


def build_phase8_retrain_input_template(
    storage: Storage,
    *,
    pool_addresses: tuple[str, ...] | None = None,
    min_pools: int = 3,
) -> dict[str, Any]:
    if min_pools < 1:
        raise ValueError("min_pools must be positive")
    champion = storage.current_model_champion()
    champion_id = (
        str(champion["model_id"])
        if champion is not None
        else None
    )
    dataset_version = (
        str(champion["dataset_version"])
        if champion is not None
        else None
    )
    selected = tuple(
        dict.fromkeys(
            value.strip()
            for value in (
                pool_addresses
                if pool_addresses is not None
                else _candidate_pools(storage, limit=min_pools)
            )
            if value.strip()
        )
    )
    pool_rows: list[dict[str, Any]] = [
        {
            "pool_address": pool,
            "amount_x": None,
            "amount_y": None,
            "network_cost_y_atomic": None,
        }
        for pool in selected
    ]
    while len(pool_rows) < min_pools:
        pool_rows.append(
            {
                "pool_address": None,
                "amount_x": None,
                "amount_y": None,
                "network_cost_y_atomic": None,
            }
        )
    return {
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "champion_model_id": champion_id,
        "champion_dataset_version": dataset_version,
        "pools": pool_rows,
    }


def parse_phase8_retrain_inputs(
    payload: Any,
    *,
    min_pools: int = 3,
) -> Phase8RetrainInputs:
    if min_pools < 1:
        raise ValueError("min_pools must be positive")
    if not isinstance(payload, dict):
        raise ValueError("Phase 8 retrain inputs must be a JSON object")
    if payload.get("research_only") is not True:
        raise ValueError("research_only must be true")
    if payload.get("policy_actionable") is not False:
        raise ValueError("policy_actionable must be false")
    if payload.get("execution_wired") is not False:
        raise ValueError("execution_wired must be false")

    champion_model_id = str(
        payload.get("champion_model_id") or ""
    ).strip()
    champion_dataset_version = str(
        payload.get("champion_dataset_version") or ""
    ).strip()
    if not champion_model_id:
        raise ValueError("champion_model_id is required")
    if not champion_dataset_version:
        raise ValueError("champion_dataset_version is required")

    raw_pools = payload.get("pools")
    if not isinstance(raw_pools, list) or len(raw_pools) < min_pools:
        raise ValueError(
            f"pools must contain at least {min_pools} entries"
        )

    pools: list[Phase8RetrainPoolInput] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_pools):
        path = f"pools[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must be an object")
        pool = str(raw.get("pool_address") or "").strip()
        if not pool:
            raise ValueError(f"{path}.pool_address is required")
        if pool in seen:
            raise ValueError("pool addresses must be unique")
        seen.add(pool)
        for key in ("amount_x", "amount_y", "network_cost_y_atomic"):
            if raw.get(key) is None:
                raise ValueError(f"{path}.{key} is required")
        amount_x = int(raw["amount_x"])
        amount_y = int(raw["amount_y"])
        network_cost = int(raw["network_cost_y_atomic"])
        if amount_x < 0 or amount_y < 0:
            raise ValueError(f"{path} token amounts cannot be negative")
        if amount_x == 0 and amount_y == 0:
            raise ValueError(
                f"{path} requires at least one positive token amount"
            )
        if network_cost < 0:
            raise ValueError(
                f"{path}.network_cost_y_atomic cannot be negative"
            )
        pools.append(
            Phase8RetrainPoolInput(
                pool_address=pool,
                amount_x=amount_x,
                amount_y=amount_y,
                network_cost_y_atomic=network_cost,
            )
        )

    return Phase8RetrainInputs(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        champion_model_id=champion_model_id,
        champion_dataset_version=champion_dataset_version,
        pools=tuple(pools),
    )


def persist_phase8_retrain_inputs(
    storage: Storage,
    *,
    inputs: Phase8RetrainInputs,
) -> Phase8RetrainInputsArtifact:
    champion = storage.current_model_champion()
    if champion is None:
        raise ValueError(
            "a current champion is required before persisting retrain inputs"
        )
    if str(champion["model_id"]) != inputs.champion_model_id:
        raise ValueError(
            "retrain input champion_model_id does not match current champion"
        )
    if (
        str(champion["dataset_version"])
        != inputs.champion_dataset_version
    ):
        raise ValueError(
            "retrain input champion_dataset_version does not match current champion"
        )

    record = inputs.to_record()
    digest = _canonical_sha256(record)
    evidence = {
        "artifact_sha256": digest,
        "inputs": record,
    }
    latest = storage.latest_model_live_evidence(
        inputs.champion_model_id,
        evidence_type=PHASE8_RETRAIN_INPUTS_EVIDENCE_TYPE,
    )
    if (
        latest is not None
        and str(latest["status"]) == "INPUTS_VALIDATED"
        and _normalized(latest.get("evidence")) == _normalized(evidence)
    ):
        evidence_id = int(latest["id"])
    else:
        evidence_id = storage.save_model_live_evidence(
            model_id=inputs.champion_model_id,
            evidence_type=PHASE8_RETRAIN_INPUTS_EVIDENCE_TYPE,
            status="INPUTS_VALIDATED",
            evidence=evidence,
        )
    return Phase8RetrainInputsArtifact(
        evidence_id=evidence_id,
        artifact_sha256=digest,
        inputs=inputs,
    )


def audit_phase8_retrain_inputs(
    storage: Storage,
) -> Phase8RetrainInputsAudit:
    champion = storage.current_model_champion()
    if champion is None:
        return Phase8RetrainInputsAudit(
            exists=False,
            valid=False,
            evidence_id=None,
            artifact_sha256=None,
            champion_matches=False,
            reasons=("current champion is missing",),
        )
    champion_id = str(champion["model_id"])
    champion_dataset = str(champion["dataset_version"])
    latest = storage.latest_model_live_evidence(
        champion_id,
        evidence_type=PHASE8_RETRAIN_INPUTS_EVIDENCE_TYPE,
    )
    if latest is None:
        return Phase8RetrainInputsAudit(
            exists=False,
            valid=False,
            evidence_id=None,
            artifact_sha256=None,
            champion_matches=False,
            reasons=("persisted Phase 8 retrain inputs are missing",),
        )

    reasons: list[str] = []
    evidence = latest.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
        reasons.append("persisted retrain input evidence is malformed")
    raw_inputs = evidence.get("inputs")
    digest = None
    parsed = None
    try:
        parsed = parse_phase8_retrain_inputs(raw_inputs)
        digest = _canonical_sha256(parsed.to_record())
    except (TypeError, ValueError) as exc:
        reasons.append(f"retrain input validation failed: {exc}")

    stored_digest = str(evidence.get("artifact_sha256") or "").strip()
    if digest is not None:
        if not stored_digest:
            reasons.append("retrain input artifact SHA-256 is missing")
        elif stored_digest != digest:
            reasons.append(
                "retrain input artifact SHA-256 does not match normalized inputs"
            )

    champion_matches = bool(
        parsed is not None
        and parsed.champion_model_id == champion_id
        and parsed.champion_dataset_version == champion_dataset
    )
    if not champion_matches:
        reasons.append(
            "retrain inputs do not match current champion lineage"
        )
    if str(latest["status"]) != "INPUTS_VALIDATED":
        reasons.append(
            "persisted retrain input status is not INPUTS_VALIDATED"
        )

    return Phase8RetrainInputsAudit(
        exists=True,
        valid=not reasons,
        evidence_id=int(latest["id"]),
        artifact_sha256=digest,
        champion_matches=champion_matches,
        reasons=tuple(reasons),
    )


def load_phase8_retrain_inputs(
    storage: Storage,
    *,
    evidence_id: int | None = None,
) -> Phase8RetrainInputsArtifact | None:
    champion = storage.current_model_champion()
    if champion is None:
        return None
    champion_id = str(champion["model_id"])

    if evidence_id is None:
        latest = storage.latest_model_live_evidence(
            champion_id,
            evidence_type=PHASE8_RETRAIN_INPUTS_EVIDENCE_TYPE,
        )
    else:
        with storage.connect() as conn:
            row = conn.execute(
                """
                SELECT id, model_id, status, evidence_json
                FROM model_live_evidence
                WHERE id = ?
                  AND evidence_type = ?
                LIMIT 1
                """,
                (
                    evidence_id,
                    PHASE8_RETRAIN_INPUTS_EVIDENCE_TYPE,
                ),
            ).fetchone()
        latest = (
            {
                "id": int(row[0]),
                "model_id": str(row[1]),
                "status": str(row[2]),
                "evidence": json.loads(str(row[3])),
            }
            if row is not None
            else None
        )
    if latest is None:
        return None
    if str(latest["model_id"]) != champion_id:
        raise ValueError(
            "retrain input artifact belongs to a different champion"
        )
    evidence = latest.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("persisted retrain input evidence is malformed")
    inputs = parse_phase8_retrain_inputs(evidence.get("inputs"))
    digest = _canonical_sha256(inputs.to_record())
    if str(evidence.get("artifact_sha256") or "") != digest:
        raise ValueError(
            "persisted retrain input artifact SHA-256 does not match inputs"
        )
    if str(latest["status"]) != "INPUTS_VALIDATED":
        raise ValueError("persisted retrain input status is invalid")
    current_dataset = str(champion["dataset_version"])
    if (
        inputs.champion_model_id != champion_id
        or inputs.champion_dataset_version != current_dataset
    ):
        raise ValueError(
            "persisted retrain inputs do not match current champion lineage"
        )
    return Phase8RetrainInputsArtifact(
        evidence_id=int(latest["id"]),
        artifact_sha256=digest,
        inputs=inputs,
    )


def run_phase8_retrain_build_from_inputs(
    storage: Storage,
    *,
    artifact: Phase8RetrainInputsArtifact,
    criteria: ContinuousLearningCriteria = ContinuousLearningCriteria(),
    as_of: str | None = None,
    output_directory: str | Path | None = None,
) -> Phase8RetrainBuildRun:
    plan = build_continuous_learning_plan(
        storage,
        criteria=criteria,
        as_of=as_of,
    )
    if not plan.retrain_due:
        raise ValueError(
            f"continuous retraining is not due: {plan.status}"
        )
    if plan.champion_model_id != artifact.inputs.champion_model_id:
        raise ValueError(
            "retrain input artifact no longer matches retraining champion"
        )
    if (
        plan.champion_dataset_version
        != artifact.inputs.champion_dataset_version
    ):
        raise ValueError(
            "retrain input artifact dataset lineage is stale"
        )

    cutoff = (
        datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if as_of is not None
        else datetime.now(timezone.utc)
    )
    if cutoff.tzinfo is None:
        raise ValueError("retraining cutoff must be timezone-aware")
    cutoff_text = cutoff.astimezone(timezone.utc).isoformat()
    root = (
        Path(output_directory).expanduser().resolve()
        if output_directory is not None
        else (
            Path(storage.path).expanduser().resolve().parent
            / "phase8_retraining_datasets"
        )
    )
    safe_time = (
        cutoff_text.replace(":", "").replace("+", "_").replace("-", "")
    )
    output_file = root / (
        f"phase8-retrain-{artifact.artifact_sha256[:12]}-"
        f"{safe_time}.csv"
    )
    result = start_retraining_cycle_with_dataset(
        storage,
        pools=tuple(
            MLRetrainPoolSpec(
                pool_address=item.pool_address,
                amount_x=item.amount_x,
                amount_y=item.amount_y,
                network_cost_y_atomic=item.network_cost_y_atomic,
            )
            for item in artifact.inputs.pools
        ),
        output_file=output_file,
        criteria=criteria,
        as_of=cutoff_text,
    )
    return Phase8RetrainBuildRun(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        input_evidence_id=artifact.evidence_id,
        input_artifact_sha256=artifact.artifact_sha256,
        result=result,
    )
