from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from .continuous_learning import ContinuousLearningCriteria
from .ml_workflow import train_save_register_ml_v1
from .ml_walk_forward import (
    MLWalkForwardCriteria,
    MLWalkForwardReport,
    evaluate_ml_walk_forward,
)
from .ml_challenger import MLChallengerCriteria
from .ml_inference import MLInferenceConfig
from .phase_promotion import PHASE3, PHASE3_EVIDENCE_TYPE
from .retraining_cycle import (
    RetrainingCycle,
    active_retraining_cycle,
    attach_retraining_challenger,
    retraining_cycle,
    start_retraining_cycle,
)
from .ml_retraining_dataset import (
    MLRetrainPoolSpec,
    MultiPoolMLDatasetReport,
    build_multi_pool_ml_action_dataset,
)
from .storage import Storage


RETRAIN_DATASET_EVIDENCE_TYPE = "CONTINUOUS_RETRAIN_DATASET_V1"
WALK_FORWARD_EVIDENCE_TYPE = "CONTINUOUS_WALK_FORWARD_V1"


@dataclass(frozen=True)
class RetrainingDatasetCycleResult:
    cycle: RetrainingCycle
    dataset: MultiPoolMLDatasetReport
    output_file: str

    def to_record(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle.to_record(),
            "dataset": self.dataset.to_record(),
            "output_file": self.output_file,
        }


@dataclass(frozen=True)
class RetrainingTrainResult:
    cycle: RetrainingCycle
    model_id: str
    dataset_version: str
    artifact_path: str
    metadata_path: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _dataset_version_from_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"ML_ACTION_DATASET_V1:{digest[:16]}"


def train_retraining_cycle_challenger(
    storage: Storage,
    *,
    cycle_id: str,
    dataset_file: str | Path,
    model_id: str,
    artifact_directory: str | Path,
    split_fraction: float = 0.8,
    min_rows: int = 50,
) -> RetrainingTrainResult:
    cycle = retraining_cycle(storage, cycle_id=cycle_id)
    if cycle.status != "PLANNED" or cycle.challenger_model_id is not None:
        raise ValueError(
            "cycle-aware training requires an unattached PLANNED cycle"
        )
    if storage.model_registry_entry(model_id) is not None:
        raise ValueError(f"model_id already exists: {model_id}")

    dataset_path = Path(dataset_file)
    if not dataset_path.is_file():
        raise ValueError(f"dataset file does not exist: {dataset_path}")
    observed_version = _dataset_version_from_file(dataset_path)
    if observed_version != cycle.target_dataset_version:
        raise ValueError(
            "dataset file checksum/version does not match retraining cycle"
        )

    frame = pd.read_csv(dataset_path)
    if "decision_observed_at" not in frame.columns:
        raise ValueError(
            "retraining dataset is missing decision_observed_at"
        )
    times = pd.to_datetime(
        frame["decision_observed_at"],
        utc=True,
        errors="coerce",
    )
    if times.isna().any():
        raise ValueError(
            "retraining dataset contains invalid decision timestamps"
        )
    cutoff = pd.Timestamp(cycle.plan_as_of)
    if (times > cutoff).any():
        raise ValueError(
            "retraining dataset contains decisions after cycle cutoff"
        )
    if "forward_end_observed_at" in frame.columns:
        forward_times = pd.to_datetime(
            frame["forward_end_observed_at"],
            utc=True,
            errors="coerce",
        )
        if forward_times.isna().any():
            raise ValueError(
                "retraining dataset contains invalid forward timestamps"
            )
        if (forward_times > cutoff).any():
            raise ValueError(
                "retraining dataset contains forward labels after cycle cutoff"
            )

    result = train_save_register_ml_v1(
        storage,
        frame,
        model_id=model_id,
        dataset_version=cycle.target_dataset_version,
        artifact_directory=artifact_directory,
        split_fraction=split_fraction,
        min_rows=min_rows,
        notes=f"continuous retraining cycle {cycle_id}",
    )
    attached = attach_retraining_challenger(
        storage,
        cycle_id=cycle_id,
        model_id=model_id,
    )
    return RetrainingTrainResult(
        cycle=attached,
        model_id=model_id,
        dataset_version=cycle.target_dataset_version,
        artifact_path=str(result.artifact.artifact_path),
        metadata_path=str(result.artifact.metadata_path),
    )


@dataclass(frozen=True)
class RetrainingWalkForwardResult:
    cycle_id: str
    model_id: str
    dataset_version: str
    evidence_id: int
    report: MLWalkForwardReport

    def to_record(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "model_id": self.model_id,
            "dataset_version": self.dataset_version,
            "evidence_id": self.evidence_id,
            "report": self.report.to_record(),
        }


def evaluate_retraining_cycle_walk_forward(
    storage: Storage,
    *,
    cycle_id: str,
    dataset_file: str | Path,
    criteria: MLWalkForwardCriteria = MLWalkForwardCriteria(),
    fold_criteria: MLChallengerCriteria = MLChallengerCriteria(
        min_comparable_decisions=5,
        min_choice_coverage_rate=0.60,
        min_mean_uplift_bps=0.0,
        min_win_rate=0.40,
        min_positive_excess_rate=0.40,
        max_single_decision_loss_bps=1_500,
    ),
    inference_config: MLInferenceConfig = MLInferenceConfig(),
) -> RetrainingWalkForwardResult:
    cycle = retraining_cycle(storage, cycle_id=cycle_id)
    if cycle.challenger_model_id is None:
        raise ValueError(
            "walk-forward validation requires an attached challenger"
        )
    model = storage.model_registry_entry(cycle.challenger_model_id)
    if model is None:
        raise ValueError("cycle challenger model no longer exists")
    if str(model["dataset_version"]) != cycle.target_dataset_version:
        raise ValueError(
            "cycle challenger dataset_version no longer matches cycle"
        )

    dataset_path = Path(dataset_file)
    if not dataset_path.is_file():
        raise ValueError(f"dataset file does not exist: {dataset_path}")
    observed_version = _dataset_version_from_file(dataset_path)
    if observed_version != cycle.target_dataset_version:
        raise ValueError(
            "walk-forward dataset checksum/version does not match cycle"
        )

    frame = pd.read_csv(dataset_path)
    if "forward_end_observed_at" not in frame.columns:
        raise ValueError(
            "walk-forward dataset is missing forward_end_observed_at"
        )
    forward_times = pd.to_datetime(
        frame["forward_end_observed_at"],
        utc=True,
        errors="coerce",
    )
    if forward_times.isna().any():
        raise ValueError(
            "walk-forward dataset contains invalid forward timestamps"
        )
    cutoff = pd.Timestamp(cycle.plan_as_of)
    if (forward_times > cutoff).any():
        raise ValueError(
            "walk-forward dataset contains labels after cycle cutoff"
        )

    phase3_ready = storage.phase_is_promoted(
        PHASE3,
        evidence_type=PHASE3_EVIDENCE_TYPE,
    )
    report = evaluate_ml_walk_forward(
        frame,
        phase3_ready=phase3_ready,
        criteria=criteria,
        fold_criteria=fold_criteria,
        inference_config=inference_config,
    )
    evidence = {
        "cycle_id": cycle.cycle_id,
        "model_id": cycle.challenger_model_id,
        "dataset_version": cycle.target_dataset_version,
        "plan_as_of": cycle.plan_as_of,
        "report": report.to_record(),
    }
    evidence_id = storage.save_model_live_evidence(
        model_id=cycle.challenger_model_id,
        evidence_type=WALK_FORWARD_EVIDENCE_TYPE,
        status=(
            "QUALIFIED"
            if report.walk_forward_qualified
            else "REJECTED"
        ),
        evidence=evidence,
    )
    return RetrainingWalkForwardResult(
        cycle_id=cycle.cycle_id,
        model_id=cycle.challenger_model_id,
        dataset_version=cycle.target_dataset_version,
        evidence_id=evidence_id,
        report=report,
    )


def start_retraining_cycle_with_dataset(
    storage: Storage,
    *,
    pools: Sequence[MLRetrainPoolSpec],
    output_file: str | Path,
    criteria: ContinuousLearningCriteria = ContinuousLearningCriteria(),
    as_of: str | None = None,
    cycle_id: str | None = None,
    lookback_observations: int = 12,
    forward_observations: int = 2,
    step_observations: int | None = None,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> RetrainingDatasetCycleResult:
    if active_retraining_cycle(storage) is not None:
        raise ValueError("an active retraining cycle already exists")

    cutoff = (
        as_of
        if as_of is not None
        else datetime.now(timezone.utc).isoformat()
    )
    dataset = build_multi_pool_ml_action_dataset(
        str(storage.path),
        pools=pools,
        lookback_observations=lookback_observations,
        forward_observations=forward_observations,
        step_observations=step_observations,
        half_widths=half_widths,
        center_offsets=center_offsets,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
        max_observed_at=cutoff,
    )

    output_path = Path(output_file).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.frame.to_csv(
        output_path,
        index=False,
        lineterminator="\n",
        float_format="%.12g",
    )
    written_digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    if written_digest != dataset.report.dataset_sha256:
        raise RuntimeError(
            "written retraining dataset checksum does not match report"
        )

    cycle = start_retraining_cycle(
        storage,
        target_dataset_version=dataset.report.dataset_version,
        criteria=criteria,
        as_of=cutoff,
        cycle_id=cycle_id,
    )
    storage.save_model_live_evidence(
        model_id=cycle.champion_model_id,
        evidence_type=RETRAIN_DATASET_EVIDENCE_TYPE,
        status="BUILT",
        evidence={
            "cycle_id": cycle.cycle_id,
            "cutoff": cycle.plan_as_of,
            "target_dataset_version": cycle.target_dataset_version,
            "dataset": dataset.report.to_record(),
            "output_file": str(output_path),
        },
    )
    return RetrainingDatasetCycleResult(
        cycle=cycle,
        dataset=dataset.report,
        output_file=str(output_path),
    )
