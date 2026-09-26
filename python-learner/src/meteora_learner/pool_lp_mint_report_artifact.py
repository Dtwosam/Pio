from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .pool_lp_mint_research import MintFeatureResearchReport


REPORT_FORMAT_VERSION = 1
_SAFE_REPORT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class MintFeatureResearchArtifact:
    report_id: str
    report_path: Path
    metadata_path: Path
    report_sha256: str
    created_at: str
    status: str
    source_dataset_sha256: str

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["report_path"] = str(self.report_path)
        record["metadata_path"] = str(self.metadata_path)
        return record


def _safe_report_id(report_id: str) -> str:
    if not report_id or not _SAFE_REPORT_ID.fullmatch(report_id):
        raise ValueError(
            "report_id may contain only letters, numbers, dot, "
            "underscore and dash"
        )
    return report_id


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def save_mint_feature_research_report(
    report: MintFeatureResearchReport,
    *,
    directory: str | Path,
    report_id: str,
) -> MintFeatureResearchArtifact:
    report_id = _safe_report_id(report_id)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)

    report_path = root / f"{report_id}.json"
    metadata_path = root / f"{report_id}.meta.json"
    report_temp = root / f".{report_id}.json.tmp"
    metadata_temp = root / f".{report_id}.meta.json.tmp"

    if report_path.exists() or metadata_path.exists():
        raise ValueError(
            f"report artifact already exists for report_id {report_id}"
        )

    payload = (
        json.dumps(
            report.to_record(),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    checksum = _sha256_bytes(payload)
    created_at = datetime.now(timezone.utc).isoformat()

    metadata = {
        "report_format_version": REPORT_FORMAT_VERSION,
        "report_id": report_id,
        "created_at": created_at,
        "report_filename": report_path.name,
        "report_sha256": checksum,
        "status": report.status,
        "source_dataset_sha256": report.source_dataset_sha256,
        "research_only": report.research_only,
        "policy_actionable": report.policy_actionable,
        "execution_wired": report.execution_wired,
    }

    report_temp.write_bytes(payload)
    metadata_temp.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_temp.replace(report_path)
    metadata_temp.replace(metadata_path)

    return MintFeatureResearchArtifact(
        report_id=report_id,
        report_path=report_path,
        metadata_path=metadata_path,
        report_sha256=checksum,
        created_at=created_at,
        status=report.status,
        source_dataset_sha256=report.source_dataset_sha256,
    )


def load_mint_feature_research_report(
    *,
    report_path: str | Path,
    metadata_path: str | Path,
) -> dict[str, Any]:
    report_file = Path(report_path)
    metadata_file = Path(metadata_path)
    if not report_file.is_file() or not metadata_file.is_file():
        raise ValueError(
            "report and metadata files must both exist"
        )

    metadata = json.loads(
        metadata_file.read_text(encoding="utf-8")
    )
    if (
        int(metadata.get("report_format_version", -1))
        != REPORT_FORMAT_VERSION
    ):
        raise ValueError(
            "unsupported mint-feature report format version"
        )
    if str(metadata.get("report_filename")) != report_file.name:
        raise ValueError(
            "report filename does not match metadata"
        )

    payload = report_file.read_bytes()
    if _sha256_bytes(payload) != str(
        metadata.get("report_sha256", "")
    ):
        raise ValueError(
            "mint-feature research report checksum mismatch"
        )

    report = json.loads(payload.decode("utf-8"))
    if report.get("research_only") is not True:
        raise ValueError(
            "mint-feature research artifact must remain research-only"
        )
    if report.get("policy_actionable") is not False:
        raise ValueError(
            "mint-feature research artifact cannot be policy-actionable"
        )
    if report.get("execution_wired") is not False:
        raise ValueError(
            "mint-feature research artifact cannot be execution-wired"
        )
    if str(report.get("status")) != str(metadata.get("status")):
        raise ValueError(
            "mint-feature research status does not match metadata"
        )
    if str(report.get("source_dataset_sha256")) != str(
        metadata.get("source_dataset_sha256")
    ):
        raise ValueError(
            "mint-feature source dataset checksum does not match metadata"
        )

    return report
