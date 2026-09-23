from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .ml_training import MLV1Bundle
from .phase_promotion import PHASE3, PHASE3_EVIDENCE_TYPE
from .storage import Storage


OFFLINE_CANDIDATE = "OFFLINE_CANDIDATE"
OFFLINE_QUALIFIED = "OFFLINE_QUALIFIED"
PAPER_CHALLENGER = "PAPER_CHALLENGER"
CHAMPION = "CHAMPION"
REJECTED = "REJECTED"
ROLLED_BACK = "ROLLED_BACK"

_ALLOWED_TRANSITIONS = {
    OFFLINE_CANDIDATE: {REJECTED},
    OFFLINE_QUALIFIED: {REJECTED},
    PAPER_CHALLENGER: {REJECTED},
    CHAMPION: {ROLLED_BACK},
    REJECTED: set(),
    ROLLED_BACK: set(),
}


@dataclass(frozen=True)
class ModelRegistryRecord:
    model_id: str
    model_family: str
    feature_version: str
    dataset_version: str
    status: str
    artifact_uri: str | None
    train_start: str | None
    train_end: str | None
    validation_start: str | None
    validation_end: str | None
    train_rows: int | None
    validation_rows: int | None
    metrics: dict[str, Any]
    notes: str | None


def _to_record(raw: dict[str, Any]) -> ModelRegistryRecord:
    return ModelRegistryRecord(
        model_id=str(raw["model_id"]),
        model_family=str(raw["model_family"]),
        feature_version=str(raw["feature_version"]),
        dataset_version=str(raw["dataset_version"]),
        status=str(raw["status"]),
        artifact_uri=(
            str(raw["artifact_uri"])
            if raw.get("artifact_uri") is not None
            else None
        ),
        train_start=raw.get("train_start"),
        train_end=raw.get("train_end"),
        validation_start=raw.get("validation_start"),
        validation_end=raw.get("validation_end"),
        train_rows=(
            int(raw["train_rows"])
            if raw.get("train_rows") is not None
            else None
        ),
        validation_rows=(
            int(raw["validation_rows"])
            if raw.get("validation_rows") is not None
            else None
        ),
        metrics=json.loads(str(raw["metrics_json"])),
        notes=raw.get("notes"),
    )


def register_ml_v1_bundle(
    storage: Storage,
    *,
    model_id: str,
    bundle: MLV1Bundle,
    dataset_version: str,
    artifact_uri: str | None = None,
    notes: str | None = None,
) -> ModelRegistryRecord:
    if not model_id.strip():
        raise ValueError("model_id is required")
    if not dataset_version.strip():
        raise ValueError("dataset_version is required")

    storage.register_model(
        model_id=model_id,
        model_family="ML_V1_HIST_GRADIENT_BOOSTING",
        feature_version="ML_ACTION_FEATURES_V1",
        dataset_version=dataset_version,
        metrics=bundle.metadata()["metrics"],
        artifact_uri=artifact_uri,
        train_start=bundle.train_start,
        train_end=bundle.train_end,
        validation_start=bundle.validation_start,
        validation_end=bundle.validation_end,
        train_rows=bundle.train_rows,
        validation_rows=bundle.validation_rows,
        notes=notes,
    )
    raw = storage.model_registry_entry(model_id)
    if raw is None:
        raise RuntimeError("registered model disappeared")
    return _to_record(raw)


def qualify_offline_challenger(
    storage: Storage,
    *,
    model_id: str,
    validation: Any,
    notes: str | None = None,
) -> ModelRegistryRecord:
    raw = storage.model_registry_entry(model_id)
    if raw is None:
        raise ValueError(f"unknown model_id: {model_id}")
    if str(raw["status"]) != OFFLINE_CANDIDATE:
        raise ValueError("model is not in OFFLINE_CANDIDATE status")
    if not bool(getattr(validation, "offline_qualified", False)):
        raise ValueError("offline challenger has not passed validation")
    if not storage.phase_is_promoted(
        PHASE3,
        evidence_type=PHASE3_EVIDENCE_TYPE,
    ):
        raise ValueError("Phase 3 deterministic policy is not persistently promoted")
    if not bool(getattr(validation, "phase3_ready", False)):
        raise ValueError("offline validation was not evaluated with Phase 3 ready")
    if str(getattr(validation, "validation_start", "")) != str(
        raw.get("validation_start")
    ):
        raise ValueError("offline validation window start does not match model")
    if str(getattr(validation, "validation_end", "")) != str(
        raw.get("validation_end")
    ):
        raise ValueError("offline validation window end does not match model")

    to_record = getattr(validation, "to_record", None)
    evidence = to_record() if callable(to_record) else {
        "offline_qualified": True,
        "phase3_ready": True,
        "validation_start": getattr(validation, "validation_start"),
        "validation_end": getattr(validation, "validation_end"),
    }
    storage.save_model_offline_evidence(
        model_id=model_id,
        evidence_type="OFFLINE_CHALLENGER_V1",
        qualified=True,
        evidence=evidence,
    )
    storage.qualify_model_offline(model_id, notes=notes)
    updated = storage.model_registry_entry(model_id)
    if updated is None:
        raise RuntimeError("qualified model disappeared")
    return _to_record(updated)


def start_paper_challenger(
    storage: Storage,
    *,
    model_id: str,
    notes: str | None = None,
) -> ModelRegistryRecord:
    storage.start_model_paper_challenger(model_id, notes=notes)
    updated = storage.model_registry_entry(model_id)
    if updated is None:
        raise RuntimeError("paper challenger disappeared")
    return _to_record(updated)


def transition_model(
    storage: Storage,
    *,
    model_id: str,
    new_status: str,
    notes: str | None = None,
) -> ModelRegistryRecord:
    if new_status == CHAMPION:
        raise ValueError(
            "CHAMPION promotion requires qualified paper validation; "
            "use promote_paper_challenger"
        )

    raw = storage.model_registry_entry(model_id)
    if raw is None:
        raise ValueError(f"unknown model_id: {model_id}")
    current = str(raw["status"])

    allowed = _ALLOWED_TRANSITIONS.get(current)
    if allowed is None or new_status not in allowed:
        raise ValueError(
            f"invalid model transition: {current} -> {new_status}"
        )

    storage.update_model_status(
        model_id,
        expected_status=current,
        new_status=new_status,
        notes=notes,
    )
    updated = storage.model_registry_entry(model_id)
    if updated is None:
        raise RuntimeError("updated model disappeared")
    return _to_record(updated)


def model_record(
    storage: Storage,
    *,
    model_id: str,
) -> ModelRegistryRecord:
    raw = storage.model_registry_entry(model_id)
    if raw is None:
        raise ValueError(f"unknown model_id: {model_id}")
    return _to_record(raw)


def current_champion(storage: Storage) -> ModelRegistryRecord | None:
    raw = storage.current_model_champion()
    return _to_record(raw) if raw is not None else None
