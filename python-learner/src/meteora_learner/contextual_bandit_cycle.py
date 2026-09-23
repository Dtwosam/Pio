from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from .contextual_bandit import (
    ContextualBanditCriteria,
    ContextualBanditReport,
    bandit_examples_from_records,
    evaluate_contextual_bandit,
)
from .retraining_cycle import retraining_cycle
from .retraining_workflow import RETRAIN_DATASET_EVIDENCE_TYPE
from .storage import Storage


@dataclass(frozen=True)
class CycleBanditDatasetLineage:
    cycle_id: str
    champion_model_id: str
    dataset_evidence_id: int
    dataset_version: str
    dataset_sha256: str
    cutoff: str
    output_file: str


@dataclass(frozen=True)
class CycleContextualBanditResult:
    lineage: CycleBanditDatasetLineage
    report: ContextualBanditReport

    def to_record(self) -> dict[str, Any]:
        return {
            "lineage": asdict(self.lineage),
            "report": self.report.to_record(),
        }


def _dataset_version_from_digest(digest: str) -> str:
    return f"ML_ACTION_DATASET_V1:{digest[:16]}"


def _load_cycle_dataset(
    storage: Storage,
    *,
    cycle_id: str,
) -> tuple[CycleBanditDatasetLineage, pd.DataFrame]:
    cycle = retraining_cycle(storage, cycle_id=cycle_id)
    evidence = storage.latest_model_live_evidence(
        cycle.champion_model_id,
        evidence_type=RETRAIN_DATASET_EVIDENCE_TYPE,
    )
    if evidence is None:
        raise ValueError(
            "retraining cycle has no persisted dataset evidence"
        )

    payload = evidence["evidence"]
    if str(payload.get("cycle_id")) != cycle.cycle_id:
        raise ValueError(
            "latest retraining dataset evidence belongs to another cycle"
        )
    if str(payload.get("cutoff")) != cycle.plan_as_of:
        raise ValueError(
            "retraining dataset evidence cutoff does not match cycle"
        )
    if (
        str(payload.get("target_dataset_version"))
        != cycle.target_dataset_version
    ):
        raise ValueError(
            "retraining dataset evidence version does not match cycle"
        )

    dataset = payload.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError(
            "retraining dataset evidence is missing dataset metadata"
        )
    expected_sha = str(dataset.get("dataset_sha256", "")).strip()
    expected_version = str(
        dataset.get("dataset_version", "")
    ).strip()
    if not expected_sha or not expected_version:
        raise ValueError(
            "retraining dataset evidence is missing checksum/version"
        )
    if expected_version != cycle.target_dataset_version:
        raise ValueError(
            "persisted dataset metadata version does not match cycle"
        )

    output_file = str(payload.get("output_file", "")).strip()
    if not output_file:
        raise ValueError(
            "retraining dataset evidence is missing output_file"
        )
    path = Path(output_file)
    if not path.is_file():
        raise ValueError(
            f"retraining dataset file does not exist: {path}"
        )

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha:
        raise ValueError(
            "retraining dataset file checksum does not match evidence"
        )
    if _dataset_version_from_digest(digest) != expected_version:
        raise ValueError(
            "retraining dataset file version does not match checksum"
        )

    frame = pd.read_csv(path)
    for column in (
        "decision_observed_at",
        "forward_end_observed_at",
    ):
        if column not in frame.columns:
            raise ValueError(
                f"retraining dataset is missing {column}"
            )
        times = pd.to_datetime(
            frame[column],
            utc=True,
            errors="coerce",
        )
        if times.isna().any():
            raise ValueError(
                f"retraining dataset contains invalid {column}"
            )
        cutoff = pd.Timestamp(cycle.plan_as_of)
        if (times > cutoff).any():
            raise ValueError(
                f"retraining dataset {column} exceeds cycle cutoff"
            )

    lineage = CycleBanditDatasetLineage(
        cycle_id=cycle.cycle_id,
        champion_model_id=cycle.champion_model_id,
        dataset_evidence_id=int(evidence["id"]),
        dataset_version=expected_version,
        dataset_sha256=expected_sha,
        cutoff=cycle.plan_as_of,
        output_file=str(path),
    )
    return lineage, frame


def evaluate_cycle_contextual_bandit(
    storage: Storage,
    *,
    cycle_id: str,
    criteria: ContextualBanditCriteria = ContextualBanditCriteria(),
) -> CycleContextualBanditResult:
    lineage, frame = _load_cycle_dataset(
        storage,
        cycle_id=cycle_id,
    )
    examples = bandit_examples_from_records(
        frame.to_dict("records")
    )
    report = evaluate_contextual_bandit(
        storage,
        examples=examples,
        criteria=criteria,
    )
    return CycleContextualBanditResult(
        lineage=lineage,
        report=report,
    )


def persist_cycle_contextual_bandit(
    storage: Storage,
    *,
    result: CycleContextualBanditResult,
) -> int:
    evidence = result.report.to_record()
    evidence["dataset_lineage"] = asdict(result.lineage)
    return storage.save_advanced_edge_evidence(
        edge_type="PHASE9_CONTEXTUAL_BANDIT_V1",
        pool_address="__CONTEXTUAL_BANDIT__",
        as_of=result.lineage.cutoff,
        status=result.report.status,
        qualified=result.report.research_qualified,
        evidence=evidence,
    )
