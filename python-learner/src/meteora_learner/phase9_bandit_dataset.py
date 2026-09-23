from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .contextual_bandit import (
    ContextualBanditCriteria,
    ContextualBanditReport,
    bandit_examples_from_records,
    evaluate_contextual_bandit,
)
from .ml_retraining_dataset import (
    MLRetrainPoolSpec,
    MultiPoolMLDataset,
    build_multi_pool_ml_action_dataset,
)
from .phase9_explicit_inputs import (
    Phase9ExplicitInputsArtifact,
    load_phase9_explicit_inputs,
)
from .storage import Storage
from .strategy import StrategyType


PHASE9_BANDIT_DATASET_EVIDENCE_TYPE = "PHASE9_BANDIT_DATASET_V1"
PHASE9_BANDIT_DATASET_SCOPE = "__CONTEXTUAL_BANDIT_DATASET__"


@dataclass(frozen=True)
class Phase9BanditDatasetArtifact:
    evidence_id: int
    artifact_sha256: str
    dataset_sha256: str
    dataset_version: str
    cutoff: str
    output_file: str
    explicit_input_evidence_id: int
    explicit_input_artifact_sha256: str
    build_parameters: dict[str, Any]
    dataset: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9BanditDatasetLineage:
    source_type: str
    dataset_evidence_id: int
    dataset_artifact_sha256: str
    dataset_version: str
    dataset_sha256: str
    cutoff: str
    output_file: str
    explicit_input_evidence_id: int
    explicit_input_artifact_sha256: str


@dataclass(frozen=True)
class Phase9BanditResearchResult:
    lineage: Phase9BanditDatasetLineage
    report: ContextualBanditReport

    def to_record(self) -> dict[str, Any]:
        return {
            "lineage": asdict(self.lineage),
            "report": self.report.to_record(),
        }


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Phase 9 bandit dataset timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _common_cutoff(
    storage: Storage,
    *,
    pool_addresses: tuple[str, ...],
) -> str:
    if not pool_addresses:
        raise ValueError("bandit dataset requires at least one pool")
    latest: list[datetime] = []
    with storage.connect() as conn:
        for pool in pool_addresses:
            row = conn.execute(
                """
                SELECT observed_at
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                ORDER BY julianday(observed_at) DESC, id DESC
                LIMIT 1
                """,
                (pool,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"bandit dataset pool has no chain observations: {pool}"
                )
            latest.append(_time(str(row[0])))
    return min(latest).isoformat()


def _build_parameters(
    artifact: Phase9ExplicitInputsArtifact,
    *,
    lookback_observations: int,
    forward_observations: int,
    step_observations: int | None,
) -> dict[str, Any]:
    portfolio = artifact.inputs.portfolio
    return {
        "lookback_observations": lookback_observations,
        "forward_observations": forward_observations,
        "step_observations": step_observations,
        "half_widths": list(portfolio.half_widths),
        "center_offsets": list(portfolio.center_offsets),
        "strategies": list(portfolio.strategies),
        "max_share_bps": portfolio.max_share_bps,
        "favor_x_in_active_bin": portfolio.favor_x_in_active_bin,
    }


def _pool_specs(
    artifact: Phase9ExplicitInputsArtifact,
) -> tuple[MLRetrainPoolSpec, ...]:
    return tuple(
        MLRetrainPoolSpec(
            pool_address=item.pool_address,
            amount_x=item.amount_x,
            amount_y=item.amount_y,
            network_cost_y_atomic=item.network_cost_y_atomic,
        )
        for item in artifact.inputs.pool_inputs
    )


def _build_dataset(
    storage: Storage,
    *,
    artifact: Phase9ExplicitInputsArtifact,
    cutoff: str,
    build_parameters: dict[str, Any],
) -> MultiPoolMLDataset:
    return build_multi_pool_ml_action_dataset(
        str(storage.path),
        pools=_pool_specs(artifact),
        lookback_observations=int(
            build_parameters["lookback_observations"]
        ),
        forward_observations=int(
            build_parameters["forward_observations"]
        ),
        step_observations=(
            int(build_parameters["step_observations"])
            if build_parameters["step_observations"] is not None
            else None
        ),
        half_widths=tuple(
            int(value) for value in build_parameters["half_widths"]
        ),
        center_offsets=tuple(
            int(value) for value in build_parameters["center_offsets"]
        ),
        strategies=tuple(
            StrategyType(str(value))
            for value in build_parameters["strategies"]
        ),
        max_share_bps=int(build_parameters["max_share_bps"]),
        favor_x_in_active_bin=bool(
            build_parameters["favor_x_in_active_bin"]
        ),
        max_observed_at=cutoff,
    )


def _dataset_bytes(dataset: MultiPoolMLDataset) -> bytes:
    return dataset.frame.to_csv(
        index=False,
        lineterminator="\n",
        float_format="%.12g",
    ).encode("utf-8")


def build_phase9_bandit_dataset(
    storage: Storage,
    *,
    artifact: Phase9ExplicitInputsArtifact,
    output_directory: str | Path | None = None,
    cutoff: str | None = None,
    lookback_observations: int = 12,
    forward_observations: int = 2,
    step_observations: int | None = None,
) -> tuple[dict[str, Any], bytes]:
    if len(artifact.inputs.pool_inputs) < 3:
        raise ValueError(
            "Phase 9 contextual-bandit research requires at least three "
            "explicit pool inputs"
        )
    selected_cutoff = (
        _time(cutoff).isoformat()
        if cutoff is not None
        else _common_cutoff(
            storage,
            pool_addresses=tuple(
                item.pool_address
                for item in artifact.inputs.pool_inputs
            ),
        )
    )
    parameters = _build_parameters(
        artifact,
        lookback_observations=lookback_observations,
        forward_observations=forward_observations,
        step_observations=step_observations,
    )
    dataset = _build_dataset(
        storage,
        artifact=artifact,
        cutoff=selected_cutoff,
        build_parameters=parameters,
    )
    raw = _dataset_bytes(dataset)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != dataset.report.dataset_sha256:
        raise RuntimeError(
            "Phase 9 bandit dataset bytes do not match dataset report SHA-256"
        )

    root = (
        Path(output_directory).expanduser().resolve()
        if output_directory is not None
        else (Path(storage.path).expanduser().resolve().parent
              / "phase9_bandit_datasets")
    )
    output_path = root / f"phase9-bandit-{digest[:16]}.csv"
    payload = {
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "cutoff": selected_cutoff,
        "output_file": str(output_path),
        "explicit_input_evidence_id": artifact.evidence_id,
        "explicit_input_artifact_sha256": artifact.artifact_sha256,
        "build_parameters": parameters,
        "dataset": dataset.report.to_record(),
    }
    payload["artifact_sha256"] = _canonical_sha256(payload)
    return payload, raw


def persist_phase9_bandit_dataset(
    storage: Storage,
    *,
    payload: dict[str, Any],
    raw_dataset: bytes,
) -> Phase9BanditDatasetArtifact:
    output_path = Path(str(payload["output_file"]))
    if not output_path.is_absolute():
        raise ValueError("Phase 9 bandit dataset output path must be absolute")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_symlink():
        raise ValueError("Phase 9 bandit dataset file must not be a symlink")

    expected_sha = str(payload["dataset"]["dataset_sha256"])
    if output_path.exists():
        if not output_path.is_file():
            raise ValueError(
                "Phase 9 bandit dataset output path is not a regular file"
            )
        if hashlib.sha256(output_path.read_bytes()).hexdigest() != expected_sha:
            raise ValueError(
                "existing Phase 9 bandit dataset file checksum mismatch"
            )
    else:
        temp = output_path.with_suffix(output_path.suffix + ".tmp")
        if temp.exists():
            temp.unlink()
        temp.write_bytes(raw_dataset)
        if hashlib.sha256(temp.read_bytes()).hexdigest() != expected_sha:
            temp.unlink(missing_ok=True)
            raise RuntimeError(
                "written Phase 9 bandit dataset checksum mismatch"
            )
        temp.replace(output_path)

    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_BANDIT_DATASET_EVIDENCE_TYPE,
        pool_address=PHASE9_BANDIT_DATASET_SCOPE,
    )
    evidence_id: int
    if (
        latest is not None
        and json.loads(json.dumps(latest.get("evidence"), sort_keys=True))
        == json.loads(json.dumps(payload, sort_keys=True))
    ):
        evidence_id = int(latest["id"])
    else:
        evidence_id = storage.save_advanced_edge_evidence(
            edge_type=PHASE9_BANDIT_DATASET_EVIDENCE_TYPE,
            pool_address=PHASE9_BANDIT_DATASET_SCOPE,
            as_of=str(payload["cutoff"]),
            status="BUILT",
            qualified=False,
            evidence=payload,
        )

    return Phase9BanditDatasetArtifact(
        evidence_id=evidence_id,
        artifact_sha256=str(payload["artifact_sha256"]),
        dataset_sha256=expected_sha,
        dataset_version=str(payload["dataset"]["dataset_version"]),
        cutoff=str(payload["cutoff"]),
        output_file=str(output_path),
        explicit_input_evidence_id=int(
            payload["explicit_input_evidence_id"]
        ),
        explicit_input_artifact_sha256=str(
            payload["explicit_input_artifact_sha256"]
        ),
        build_parameters=dict(payload["build_parameters"]),
        dataset=dict(payload["dataset"]),
    )


def load_phase9_bandit_dataset(
    storage: Storage,
    *,
    evidence_id: int | None = None,
    verify_rebuild: bool = True,
) -> tuple[Phase9BanditDatasetArtifact, MultiPoolMLDataset]:
    if evidence_id is None:
        row = storage.latest_advanced_edge_evidence(
            edge_type=PHASE9_BANDIT_DATASET_EVIDENCE_TYPE,
            pool_address=PHASE9_BANDIT_DATASET_SCOPE,
        )
    else:
        with storage.connect() as conn:
            raw = conn.execute(
                """
                SELECT id, status, qualified, evidence_json
                FROM advanced_edge_evidence
                WHERE id = ?
                  AND edge_type = ?
                  AND pool_address = ?
                LIMIT 1
                """,
                (
                    evidence_id,
                    PHASE9_BANDIT_DATASET_EVIDENCE_TYPE,
                    PHASE9_BANDIT_DATASET_SCOPE,
                ),
            ).fetchone()
        row = (
            {
                "id": int(raw[0]),
                "status": str(raw[1]),
                "qualified": bool(raw[2]),
                "evidence": json.loads(str(raw[3])),
            }
            if raw is not None
            else None
        )
    if row is None:
        raise ValueError("Phase 9 bandit dataset artifact is missing")
    if str(row.get("status", "")) != "BUILT" or bool(row.get("qualified")):
        raise ValueError("Phase 9 bandit dataset artifact status is invalid")

    payload = row.get("evidence")
    if not isinstance(payload, dict):
        raise ValueError("Phase 9 bandit dataset evidence is malformed")
    if (
        payload.get("research_only") is not True
        or payload.get("policy_actionable") is not False
        or payload.get("execution_wired") is not False
    ):
        raise ValueError(
            "Phase 9 bandit dataset violates research-only boundary"
        )

    artifact_hash = str(payload.get("artifact_sha256", "")).strip()
    unsigned = dict(payload)
    unsigned.pop("artifact_sha256", None)
    if not artifact_hash or _canonical_sha256(unsigned) != artifact_hash:
        raise ValueError(
            "Phase 9 bandit dataset artifact SHA-256 does not match metadata"
        )

    dataset_raw = payload.get("dataset")
    parameters = payload.get("build_parameters")
    if not isinstance(dataset_raw, dict) or not isinstance(parameters, dict):
        raise ValueError("Phase 9 bandit dataset metadata is incomplete")

    explicit_id = int(payload["explicit_input_evidence_id"])
    explicit = load_phase9_explicit_inputs(
        storage,
        evidence_id=explicit_id,
    )
    if explicit is None:
        raise ValueError(
            "Phase 9 bandit dataset explicit input artifact is missing"
        )
    if (
        explicit.artifact_sha256
        != str(payload["explicit_input_artifact_sha256"])
    ):
        raise ValueError(
            "Phase 9 bandit dataset explicit input checksum mismatch"
        )

    path = Path(str(payload["output_file"]))
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError(
            "Phase 9 bandit dataset file must be an absolute regular file"
        )
    file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected_digest = str(dataset_raw.get("dataset_sha256", "")).strip()
    expected_version = str(dataset_raw.get("dataset_version", "")).strip()
    if (
        not expected_digest
        or file_digest != expected_digest
        or expected_version != f"ML_ACTION_DATASET_V1:{file_digest[:16]}"
    ):
        raise ValueError(
            "Phase 9 bandit dataset file checksum/version mismatch"
        )

    rebuilt = _build_dataset(
        storage,
        artifact=explicit,
        cutoff=_time(str(payload["cutoff"])).isoformat(),
        build_parameters=parameters,
    )
    if verify_rebuild:
        rebuilt_raw = _dataset_bytes(rebuilt)
        if hashlib.sha256(rebuilt_raw).hexdigest() != expected_digest:
            raise ValueError(
                "Phase 9 bandit dataset does not rebuild from persisted "
                "chain history and explicit inputs"
            )
        if rebuilt.report.to_record() != dataset_raw:
            raise ValueError(
                "Phase 9 bandit dataset report does not reproduce"
            )

    artifact = Phase9BanditDatasetArtifact(
        evidence_id=int(row["id"]),
        artifact_sha256=artifact_hash,
        dataset_sha256=expected_digest,
        dataset_version=expected_version,
        cutoff=str(payload["cutoff"]),
        output_file=str(path),
        explicit_input_evidence_id=explicit_id,
        explicit_input_artifact_sha256=explicit.artifact_sha256,
        build_parameters=dict(parameters),
        dataset=dict(dataset_raw),
    )
    return artifact, rebuilt


def evaluate_phase9_contextual_bandit_from_dataset(
    storage: Storage,
    *,
    dataset_evidence_id: int | None = None,
    criteria: ContextualBanditCriteria = ContextualBanditCriteria(),
) -> Phase9BanditResearchResult:
    artifact, dataset = load_phase9_bandit_dataset(
        storage,
        evidence_id=dataset_evidence_id,
        verify_rebuild=True,
    )
    examples = bandit_examples_from_records(
        dataset.frame.to_dict("records")
    )
    report = evaluate_contextual_bandit(
        storage,
        examples=examples,
        criteria=criteria,
    )
    return Phase9BanditResearchResult(
        lineage=Phase9BanditDatasetLineage(
            source_type=PHASE9_BANDIT_DATASET_EVIDENCE_TYPE,
            dataset_evidence_id=artifact.evidence_id,
            dataset_artifact_sha256=artifact.artifact_sha256,
            dataset_version=artifact.dataset_version,
            dataset_sha256=artifact.dataset_sha256,
            cutoff=artifact.cutoff,
            output_file=artifact.output_file,
            explicit_input_evidence_id=(
                artifact.explicit_input_evidence_id
            ),
            explicit_input_artifact_sha256=(
                artifact.explicit_input_artifact_sha256
            ),
        ),
        report=report,
    )
