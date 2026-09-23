from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
from typing import Any

import joblib
import sklearn

from .ml_training import MLV1Bundle


ARTIFACT_FORMAT_VERSION = 1
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class MLArtifactMetadata:
    artifact_format_version: int
    model_id: str
    created_at: str
    artifact_filename: str
    artifact_sha256: str
    python_version: str
    sklearn_version: str
    feature_columns: tuple[str, ...]
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    train_rows: int
    validation_rows: int
    metrics: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SavedMLArtifact:
    artifact_path: Path
    metadata_path: Path
    metadata: MLArtifactMetadata


def _safe_model_id(model_id: str) -> str:
    if not model_id or not _SAFE_MODEL_ID.fullmatch(model_id):
        raise ValueError(
            "model_id may contain only letters, numbers, dot, underscore and dash"
        )
    return model_id


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_compatible(stored: str, current: str) -> bool:
    def major_minor(value: str) -> tuple[str, str]:
        parts = value.split(".")
        return (
            parts[0] if parts else "",
            parts[1] if len(parts) > 1 else "",
        )

    return major_minor(stored) == major_minor(current)


def save_ml_v1_artifact(
    bundle: MLV1Bundle,
    *,
    directory: str | Path,
    model_id: str,
) -> SavedMLArtifact:
    model_id = _safe_model_id(model_id)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)

    artifact_path = root / f"{model_id}.joblib"
    metadata_path = root / f"{model_id}.json"
    temp_artifact = root / f".{model_id}.joblib.tmp"
    temp_metadata = root / f".{model_id}.json.tmp"

    joblib.dump(bundle, temp_artifact)
    checksum = _sha256(temp_artifact)

    metadata = MLArtifactMetadata(
        artifact_format_version=ARTIFACT_FORMAT_VERSION,
        model_id=model_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        artifact_filename=artifact_path.name,
        artifact_sha256=checksum,
        python_version=platform.python_version(),
        sklearn_version=sklearn.__version__,
        feature_columns=tuple(bundle.feature_columns),
        train_start=bundle.train_start,
        train_end=bundle.train_end,
        validation_start=bundle.validation_start,
        validation_end=bundle.validation_end,
        train_rows=bundle.train_rows,
        validation_rows=bundle.validation_rows,
        metrics=asdict(bundle.metrics),
    )
    temp_metadata.write_text(
        json.dumps(metadata.to_record(), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    temp_artifact.replace(artifact_path)
    temp_metadata.replace(metadata_path)

    return SavedMLArtifact(
        artifact_path=artifact_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def load_ml_v1_artifact(
    *,
    artifact_path: str | Path,
    metadata_path: str | Path,
    require_runtime_match: bool = True,
) -> MLV1Bundle:
    """
    Load a trusted local artifact after metadata/checksum/runtime validation.

    joblib uses pickle internally. Never call this on an artifact from an
    untrusted source.
    """
    artifact = Path(artifact_path)
    metadata_file = Path(metadata_path)
    if not artifact.is_file() or not metadata_file.is_file():
        raise ValueError("artifact and metadata files must both exist")

    raw = json.loads(metadata_file.read_text(encoding="utf-8"))
    if int(raw.get("artifact_format_version", -1)) != ARTIFACT_FORMAT_VERSION:
        raise ValueError("unsupported ML artifact format version")
    if str(raw.get("artifact_filename")) != artifact.name:
        raise ValueError("artifact filename does not match metadata")
    if str(raw.get("artifact_sha256")) != _sha256(artifact):
        raise ValueError("ML artifact checksum mismatch")

    if require_runtime_match:
        stored_python = str(raw.get("python_version", ""))
        stored_sklearn = str(raw.get("sklearn_version", ""))
        if not _runtime_compatible(stored_python, platform.python_version()):
            raise ValueError(
                "ML artifact Python major/minor version does not match runtime"
            )
        if not _runtime_compatible(stored_sklearn, sklearn.__version__):
            raise ValueError(
                "ML artifact scikit-learn major/minor version does not match runtime"
            )

    bundle = joblib.load(artifact)
    if not isinstance(bundle, MLV1Bundle):
        raise ValueError("artifact does not contain an MLV1Bundle")

    metadata_features = tuple(str(item) for item in raw.get("feature_columns", []))
    if tuple(bundle.feature_columns) != metadata_features:
        raise ValueError("artifact feature columns do not match metadata")

    if bundle.train_start != str(raw.get("train_start")):
        raise ValueError("artifact train_start does not match metadata")
    if bundle.validation_start != str(raw.get("validation_start")):
        raise ValueError("artifact validation_start does not match metadata")
    return bundle
