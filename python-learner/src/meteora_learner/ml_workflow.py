from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .ml_artifact import (
    SavedMLArtifact,
    load_ml_v1_artifact,
    save_ml_v1_artifact,
)
from .ml_challenger import (
    MLChallengerCriteria,
    MLChallengerReport,
    evaluate_ml_challenger,
)
from .ml_inference import MLInferenceConfig
from .ml_registry import (
    ModelRegistryRecord,
    qualify_offline_challenger,
    register_ml_v1_bundle,
)
from .ml_training import MLV1Bundle, train_ml_v1_frame
from .phase_promotion import PHASE3, PHASE3_EVIDENCE_TYPE
from .storage import Storage


@dataclass(frozen=True)
class TrainRegisterResult:
    registry: ModelRegistryRecord
    artifact: SavedMLArtifact


def train_save_register_ml_v1(
    storage: Storage,
    frame: pd.DataFrame,
    *,
    model_id: str,
    dataset_version: str,
    artifact_directory: str | Path,
    split_fraction: float = 0.8,
    min_rows: int = 50,
    notes: str | None = None,
) -> TrainRegisterResult:
    bundle = train_ml_v1_frame(
        frame,
        split_fraction=split_fraction,
        min_rows=min_rows,
    )
    saved = save_ml_v1_artifact(
        bundle,
        directory=artifact_directory,
        model_id=model_id,
    )
    registry = register_ml_v1_bundle(
        storage,
        model_id=model_id,
        bundle=bundle,
        dataset_version=dataset_version,
        artifact_uri=str(saved.artifact_path),
        notes=notes,
    )
    return TrainRegisterResult(
        registry=registry,
        artifact=saved,
    )


def load_registered_ml_v1(
    storage: Storage,
    *,
    model_id: str,
    require_runtime_match: bool = True,
) -> MLV1Bundle:
    raw = storage.model_registry_entry(model_id)
    if raw is None:
        raise ValueError(f"unknown model_id: {model_id}")
    artifact_uri = raw.get("artifact_uri")
    if not artifact_uri:
        raise ValueError("registered model has no artifact_uri")

    artifact_path = Path(str(artifact_uri))
    metadata_path = artifact_path.with_suffix(".json")
    return load_ml_v1_artifact(
        artifact_path=artifact_path,
        metadata_path=metadata_path,
        require_runtime_match=require_runtime_match,
    )


def evaluate_registered_offline_challenger(
    storage: Storage,
    frame: pd.DataFrame,
    *,
    model_id: str,
    inference_config: MLInferenceConfig = MLInferenceConfig(),
    criteria: MLChallengerCriteria = MLChallengerCriteria(),
) -> MLChallengerReport:
    bundle = load_registered_ml_v1(storage, model_id=model_id)
    phase3_ready = storage.phase_is_promoted(
        PHASE3,
        evidence_type=PHASE3_EVIDENCE_TYPE,
    )
    return evaluate_ml_challenger(
        bundle,
        frame,
        phase3_ready=phase3_ready,
        inference_config=inference_config,
        criteria=criteria,
    )


def qualify_registered_offline_challenger(
    storage: Storage,
    frame: pd.DataFrame,
    *,
    model_id: str,
    inference_config: MLInferenceConfig = MLInferenceConfig(),
    criteria: MLChallengerCriteria = MLChallengerCriteria(),
    notes: str | None = None,
) -> tuple[MLChallengerReport, ModelRegistryRecord]:
    validation = evaluate_registered_offline_challenger(
        storage,
        frame,
        model_id=model_id,
        inference_config=inference_config,
        criteria=criteria,
    )
    qualified = qualify_offline_challenger(
        storage,
        model_id=model_id,
        validation=validation,
        notes=notes,
    )
    return validation, qualified
