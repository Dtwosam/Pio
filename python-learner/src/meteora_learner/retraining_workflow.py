from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Sequence

from .continuous_learning import ContinuousLearningCriteria
from .ml_retraining_dataset import (
    MLRetrainPoolSpec,
    MultiPoolMLDatasetReport,
    build_multi_pool_ml_action_dataset,
)
from .retraining_cycle import (
    RetrainingCycle,
    active_retraining_cycle,
    start_retraining_cycle,
)
from .storage import Storage


RETRAIN_DATASET_EVIDENCE_TYPE = "CONTINUOUS_RETRAIN_DATASET_V1"


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

    output_path = Path(output_file)
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
