from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .ml_challenger import MLChallengerCriteria
from .ml_inference import MLInferenceConfig
from .ml_walk_forward import MLWalkForwardCriteria
from .ml_workflow import (
    evaluate_registered_offline_challenger,
    qualify_registered_offline_challenger,
)
from .retraining_cycle import (
    RetrainingCycle,
    active_retraining_cycle,
    retraining_cycle,
    sync_retraining_cycle,
)
from .retraining_workflow import (
    RETRAIN_DATASET_EVIDENCE_TYPE,
    RetrainingTrainResult,
    RetrainingWalkForwardResult,
    evaluate_retraining_cycle_walk_forward,
    train_retraining_cycle_challenger,
)
from .storage import Storage


@dataclass(frozen=True)
class Phase8RetrainDatasetLineage:
    evidence_id: int
    cycle_id: str
    champion_model_id: str
    cutoff: str
    dataset_version: str
    dataset_sha256: str
    output_file: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase8OfflineTrainRun:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    lineage: Phase8RetrainDatasetLineage
    result: RetrainingTrainResult

    def to_record(self) -> dict[str, Any]:
        return {
            "research_only": self.research_only,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
            "lineage": self.lineage.to_record(),
            "result": self.result.to_record(),
        }


@dataclass(frozen=True)
class Phase8OfflineValidationRun:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    lineage: Phase8RetrainDatasetLineage
    cycle_id: str
    model_id: str
    walk_forward: dict[str, Any]
    walk_forward_qualified: bool
    offline_validation: dict[str, Any] | None
    offline_qualified: bool
    model_status_after: str
    cycle_status_after: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _selected_cycle(
    storage: Storage,
    *,
    cycle_id: str | None,
) -> RetrainingCycle:
    if cycle_id is not None:
        return retraining_cycle(storage, cycle_id=cycle_id)
    cycle = active_retraining_cycle(storage)
    if cycle is None:
        raise ValueError("no active Phase 8 retraining cycle exists")
    return cycle


def phase8_cycle_dataset_lineage(
    storage: Storage,
    *,
    cycle_id: str | None = None,
) -> Phase8RetrainDatasetLineage:
    cycle = _selected_cycle(storage, cycle_id=cycle_id)
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, status, evidence_json
            FROM model_live_evidence
            WHERE model_id = ?
              AND evidence_type = ?
            ORDER BY id DESC
            """,
            (
                cycle.champion_model_id,
                RETRAIN_DATASET_EVIDENCE_TYPE,
            ),
        ).fetchall()

    selected = None
    for row in rows:
        try:
            evidence = json.loads(str(row[2]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if str(evidence.get("cycle_id", "")) == cycle.cycle_id:
            selected = (int(row[0]), str(row[1]), evidence)
            break
    if selected is None:
        raise ValueError(
            "active retraining cycle lacks persisted dataset evidence"
        )
    evidence_id, status, evidence = selected
    if status != "BUILT":
        raise ValueError("retraining dataset evidence status is not BUILT")
    if str(evidence.get("cutoff", "")) != cycle.plan_as_of:
        raise ValueError(
            "retraining dataset cutoff does not match cycle plan cutoff"
        )
    if (
        str(evidence.get("target_dataset_version", ""))
        != cycle.target_dataset_version
    ):
        raise ValueError(
            "retraining dataset target version does not match cycle"
        )
    dataset = evidence.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("retraining dataset evidence is malformed")
    dataset_sha = str(dataset.get("dataset_sha256", "")).strip()
    dataset_version = str(dataset.get("dataset_version", "")).strip()
    if not dataset_sha or dataset_version != cycle.target_dataset_version:
        raise ValueError(
            "retraining dataset report version/checksum is invalid"
        )

    path = Path(str(evidence.get("output_file", "")))
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError(
            "retraining dataset file must be an absolute regular file"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != dataset_sha:
        raise ValueError("retraining dataset file checksum mismatch")
    if dataset_version != f"ML_ACTION_DATASET_V1:{digest[:16]}":
        raise ValueError("retraining dataset version does not match bytes")

    return Phase8RetrainDatasetLineage(
        evidence_id=evidence_id,
        cycle_id=cycle.cycle_id,
        champion_model_id=cycle.champion_model_id,
        cutoff=cycle.plan_as_of,
        dataset_version=dataset_version,
        dataset_sha256=digest,
        output_file=str(path),
    )


def train_phase8_cycle_challenger(
    storage: Storage,
    *,
    cycle_id: str | None = None,
    model_id: str | None = None,
    artifact_directory: str | Path | None = None,
    split_fraction: float = 0.8,
    min_rows: int = 50,
) -> Phase8OfflineTrainRun:
    cycle = _selected_cycle(storage, cycle_id=cycle_id)
    if cycle.status != "PLANNED" or cycle.challenger_model_id is not None:
        raise ValueError(
            "offline training requires an unattached PLANNED retraining cycle"
        )
    lineage = phase8_cycle_dataset_lineage(
        storage,
        cycle_id=cycle.cycle_id,
    )
    selected_model_id = (
        model_id.strip()
        if model_id is not None and model_id.strip()
        else f"phase8-challenger-{cycle.cycle_id}"
    )
    selected_artifact_directory = (
        Path(artifact_directory).expanduser().resolve()
        if artifact_directory is not None
        else (
            Path(storage.path).expanduser().resolve().parent
            / "phase8_ml_artifacts"
            / cycle.cycle_id
        )
    )
    result = train_retraining_cycle_challenger(
        storage,
        cycle_id=cycle.cycle_id,
        dataset_file=lineage.output_file,
        model_id=selected_model_id,
        artifact_directory=selected_artifact_directory,
        split_fraction=split_fraction,
        min_rows=min_rows,
    )
    return Phase8OfflineTrainRun(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        lineage=lineage,
        result=result,
    )


def validate_phase8_cycle_challenger_offline(
    storage: Storage,
    *,
    cycle_id: str | None = None,
    walk_forward_criteria: MLWalkForwardCriteria = (
        MLWalkForwardCriteria()
    ),
    offline_criteria: MLChallengerCriteria = MLChallengerCriteria(),
    inference_config: MLInferenceConfig = MLInferenceConfig(),
) -> Phase8OfflineValidationRun:
    cycle = _selected_cycle(storage, cycle_id=cycle_id)
    if cycle.challenger_model_id is None:
        raise ValueError(
            "offline validation requires an attached cycle challenger"
        )
    model = storage.model_registry_entry(cycle.challenger_model_id)
    if model is None:
        raise ValueError("cycle challenger model is missing")
    if str(model["status"]) != "OFFLINE_CANDIDATE":
        raise ValueError(
            "offline validation requires OFFLINE_CANDIDATE status"
        )

    lineage = phase8_cycle_dataset_lineage(
        storage,
        cycle_id=cycle.cycle_id,
    )
    walk = evaluate_retraining_cycle_walk_forward(
        storage,
        cycle_id=cycle.cycle_id,
        dataset_file=lineage.output_file,
        criteria=walk_forward_criteria,
        inference_config=inference_config,
    )
    offline_record: dict[str, Any] | None = None
    offline_qualified = False

    if walk.report.walk_forward_qualified:
        frame = pd.read_csv(lineage.output_file)
        preview = evaluate_registered_offline_challenger(
            storage,
            frame,
            model_id=cycle.challenger_model_id,
            inference_config=inference_config,
            criteria=offline_criteria,
        )
        offline_record = preview.to_record()
        if preview.offline_qualified:
            validation, _ = qualify_registered_offline_challenger(
                storage,
                frame,
                model_id=cycle.challenger_model_id,
                inference_config=inference_config,
                criteria=offline_criteria,
                notes=(
                    f"qualified from continuous retraining cycle "
                    f"{cycle.cycle_id}"
                ),
            )
            offline_record = validation.to_record()
            offline_qualified = True

    synced = sync_retraining_cycle(
        storage,
        cycle_id=cycle.cycle_id,
    )
    updated = storage.model_registry_entry(cycle.challenger_model_id)
    if updated is None:
        raise RuntimeError("cycle challenger disappeared after validation")

    return Phase8OfflineValidationRun(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        lineage=lineage,
        cycle_id=cycle.cycle_id,
        model_id=cycle.challenger_model_id,
        walk_forward=walk.to_record(),
        walk_forward_qualified=walk.report.walk_forward_qualified,
        offline_validation=offline_record,
        offline_qualified=offline_qualified,
        model_status_after=str(updated["status"]),
        cycle_status_after=synced.status,
    )
