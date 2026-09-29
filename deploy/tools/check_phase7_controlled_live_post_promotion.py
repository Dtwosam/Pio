from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_POST_PROMOTION_AUDIT_V1"

PERSISTENCE_TOOL = Path(
    "deploy/tools/persist_phase7_controlled_live_promotion.py"
)
STATUS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_evidence_status.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_promotion_request.py"
)

REVIEWED_SOURCE_BLOBS = {
    PERSISTENCE_TOOL: "0eb045a4d1fda23351a52c1e8754eeb6e9399868",
    STATUS_TOOL: "77aa894be5d3e04534e521d159fa4743e759ef06",
    REQUEST_TOOL: "7cc053123d767aa5f6c5b6a94bdb90777c859d47",
}

PHASE6 = "PHASE6"
PHASE6_EVIDENCE_TYPE = "PHASE6_PROMOTION_V1"
PHASE7 = "PHASE7"
PHASE7_EVIDENCE_TYPE = "PHASE7_PROMOTION_V1"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "persistence_receipt_sha256",
    "promotion_request_sha256",
    "fresh_phase7_evidence_status_sha256",
    "production_repository",
    "pio_database_path",
    "phase_name",
    "evidence_type",
    "promoted_at",
    "history_id",
    "evidence_sha256",
    "current_record",
    "history_record",
    "phase6_current_record",
    "phase6_dependency_still_promoted",
    "current_record_matches_receipt",
    "history_record_matches_receipt",
    "phase7_history_count",
    "exact_single_history_entry",
    "fresh_phase7_report_matches_receipt",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "promotion_snapshot_read_only",
    "source_database_unchanged",
    "post_promotion_audit_ready",
    "phase7_promotion_confirmed",
    "phase7_promotion_persisted",
    "requires_separate_phase8_workflow",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified_by_audit",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _git_blob_sha(path: Path) -> str:
    return _git_blob_sha_bytes(path.read_bytes())


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: str | Path, *, label: str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_reviewed(source: Path) -> tuple[Any, Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 post-promotion dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 post-promotion dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / PERSISTENCE_TOOL,
            "phase7_post_promotion_persistence",
        ),
        _load_module(
            source / STATUS_TOOL,
            "phase7_post_promotion_status",
        ),
        _load_module(
            source / REQUEST_TOOL,
            "phase7_post_promotion_request",
        ),
    )


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 7 post-promotion sidecar is unsafe: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _row_payload(
    row: tuple[Any, ...] | None,
) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "phase_name": str(row[0]),
        "promoted_at": str(row[1]),
        "evidence_type": str(row[2]),
        "qualified": bool(row[3]),
        "evidence": json.loads(str(row[4])),
    }


def _promotion_snapshot(database: Path) -> dict[str, Any]:
    if database.is_symlink() or not database.is_file():
        raise ValueError(
            "Phase 7 post-promotion database is invalid"
        )
    before = _database_state(database)
    if before["database"] is None:
        raise ValueError(
            "Phase 7 post-promotion database is missing"
        )

    with tempfile.TemporaryDirectory(
        prefix="pio-phase7-post-promotion-"
    ) as tmp:
        snapshot = Path(tmp) / "pio.db"
        uri = f"file:{database}?mode=ro"
        if before["wal"] is None:
            uri += "&immutable=1"
        source = sqlite3.connect(uri, uri=True)
        try:
            source.execute("PRAGMA query_only=ON")
            destination = sqlite3.connect(snapshot)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()

        conn = sqlite3.connect(snapshot)
        try:
            current = conn.execute(
                """
                SELECT phase_name, promoted_at, evidence_type,
                       qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE7,),
            ).fetchone()
            history_rows = conn.execute(
                """
                SELECT id, phase_name, promoted_at, evidence_type,
                       qualified, evidence_json
                FROM phase_promotion_evidence_history
                WHERE phase_name = ?
                ORDER BY id ASC
                """,
                (PHASE7,),
            ).fetchall()
            phase6 = conn.execute(
                """
                SELECT phase_name, promoted_at, evidence_type,
                       qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE6,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise ValueError(
                "Phase 7 promotion tables are unavailable"
            ) from exc
        finally:
            conn.close()

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during Phase 7 post-promotion audit"
        )

    history = [
        {
            "id": int(row[0]),
            **_row_payload(tuple(row[1:])),
        }
        for row in history_rows
    ]
    return {
        "current_record": _row_payload(current),
        "history": history,
        "phase6_current_record": _row_payload(phase6),
        "state_before": before,
        "state_after": after,
    }


def _validate_phase7_row(
    row: dict[str, Any] | None,
    *,
    expected_evidence_sha256: str,
    label: str,
) -> None:
    if not isinstance(row, dict):
        raise ValueError(
            f"Phase 7 post-promotion {label} is missing"
        )
    if row.get("phase_name") != PHASE7:
        raise ValueError(
            f"Phase 7 post-promotion {label} phase mismatch"
        )
    if row.get("evidence_type") != PHASE7_EVIDENCE_TYPE:
        raise ValueError(
            f"Phase 7 post-promotion {label} evidence type mismatch"
        )
    if row.get("qualified") is not True:
        raise ValueError(
            f"Phase 7 post-promotion {label} is not qualified"
        )
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError(
            f"Phase 7 post-promotion {label} evidence is invalid"
        )
    if _sha256_bytes(
        _canonical_bytes(evidence)
    ) != expected_evidence_sha256:
        raise ValueError(
            f"Phase 7 post-promotion {label} evidence digest mismatch"
        )


def validate_post_promotion_audit(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 post-promotion audit must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"post_promotion_audit_sha256"}:
        raise ValueError(
            "Phase 7 post-promotion audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 post-promotion audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 post-promotion audit type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "Phase 7 post-promotion audit lineage mismatch"
        )

    for field in (
        "persistence_receipt_sha256",
        "promotion_request_sha256",
        "fresh_phase7_evidence_status_sha256",
        "evidence_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "post_promotion_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 post-promotion audit {field} is invalid"
            )
    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 7 post-promotion audit {field} is invalid"
            )

    if report.get("phase_name") != PHASE7:
        raise ValueError(
            "Phase 7 post-promotion audit phase mismatch"
        )
    if report.get("evidence_type") != PHASE7_EVIDENCE_TYPE:
        raise ValueError(
            "Phase 7 post-promotion audit evidence type mismatch"
        )

    _validate_phase7_row(
        report.get("current_record"),
        expected_evidence_sha256=report["evidence_sha256"],
        label="current record",
    )
    history = report.get("history_record")
    if not isinstance(history, dict):
        raise ValueError(
            "Phase 7 post-promotion history record is invalid"
        )
    _validate_phase7_row(
        {key: value for key, value in history.items() if key != "id"},
        expected_evidence_sha256=report["evidence_sha256"],
        label="history record",
    )
    if history.get("id") != report.get("history_id"):
        raise ValueError(
            "Phase 7 post-promotion history id mismatch"
        )

    phase6 = report.get("phase6_current_record")
    if (
        not isinstance(phase6, dict)
        or phase6.get("phase_name") != PHASE6
        or phase6.get("evidence_type") != PHASE6_EVIDENCE_TYPE
        or phase6.get("qualified") is not True
    ):
        raise ValueError(
            "Phase 7 post-promotion Phase 6 dependency is invalid"
        )

    for field in (
        "phase6_dependency_still_promoted",
        "current_record_matches_receipt",
        "history_record_matches_receipt",
        "exact_single_history_entry",
        "fresh_phase7_report_matches_receipt",
        "promotion_snapshot_read_only",
        "source_database_unchanged",
        "post_promotion_audit_ready",
        "phase7_promotion_confirmed",
        "phase7_promotion_persisted",
        "requires_separate_phase8_workflow",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 post-promotion audit requires {field}=true"
            )
    if report.get("phase7_history_count") != 1:
        raise ValueError(
            "Phase 7 post-promotion requires one history entry"
        )
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "Pio database changed during Phase 7 post-promotion audit"
        )
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError(
            "Pio WAL changed during Phase 7 post-promotion audit"
        )
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError(
            "Pio SHM changed during Phase 7 post-promotion audit"
        )

    for field in (
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified_by_audit",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 post-promotion audit requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["post_promotion_audit_sha256"] != expected:
        raise ValueError(
            "Phase 7 post-promotion audit digest mismatch"
        )


def build_post_promotion_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    persistence_receipt_path: str | Path,
    promotion_request_path: str | Path,
    phase6_post_promotion_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    persistence_module, status_module, request_module = _load_reviewed(source)

    receipt = _load_json(
        persistence_receipt_path,
        label="Phase 7 promotion persistence receipt",
    )
    request = _load_json(
        promotion_request_path,
        label="Phase 7 promotion request",
    )
    persistence_module.validate_persistence_receipt(receipt)
    request_module.validate_phase7_promotion_request(request)

    if receipt["promotion_request_sha256"] != request["request_sha256"]:
        raise ValueError(
            "Phase 7 persistence receipt/request binding mismatch"
        )
    if receipt.get("phase7_promotion_persisted") is not True:
        raise ValueError(
            "Phase 7 persistence receipt is not persisted"
        )
    if receipt.get("requires_post_promotion_audit") is not True:
        raise ValueError(
            "Phase 7 persistence receipt does not require audit"
        )

    database = (production / "data" / "pio.db").resolve(strict=True)
    if Path(receipt["pio_database_path"]).resolve() != database:
        raise ValueError(
            "Phase 7 post-promotion database binding mismatch"
        )
    snapshot = _promotion_snapshot(database)
    current = snapshot["current_record"]
    history = snapshot["history"]
    phase6 = snapshot["phase6_current_record"]

    _validate_phase7_row(
        current,
        expected_evidence_sha256=receipt["evidence_sha256"],
        label="current record",
    )
    if len(history) != 1:
        raise ValueError(
            "Phase 7 post-promotion requires exactly one history entry"
        )
    _validate_phase7_row(
        {key: value for key, value in history[0].items() if key != "id"},
        expected_evidence_sha256=receipt["evidence_sha256"],
        label="history record",
    )
    if (
        current["promoted_at"] != receipt["promoted_at"]
        or history[0]["promoted_at"] != receipt["promoted_at"]
        or history[0]["id"] != receipt["history_id"]
    ):
        raise ValueError(
            "Phase 7 persisted promotion rows differ from receipt"
        )
    if (
        not isinstance(phase6, dict)
        or phase6.get("phase_name") != PHASE6
        or phase6.get("evidence_type") != PHASE6_EVIDENCE_TYPE
        or phase6.get("qualified") is not True
    ):
        raise ValueError(
            "Phase 6 dependency is no longer promoted"
        )

    fresh_status = status_module.build_phase7_evidence_status(
        repository=production,
        source_tree=source,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
    )
    status_module.validate_phase7_evidence_status(fresh_status)
    if fresh_status.get("phase7_promotion_ready") is not True:
        raise ValueError(
            "fresh Phase 7 report is no longer promotion-ready"
        )
    if fresh_status.get("phase7_reasons") != []:
        raise ValueError(
            "fresh Phase 7 report has blockers"
        )
    if fresh_status.get("phase7_report_sha256") != receipt[
        "phase7_report_sha256"
    ]:
        raise ValueError(
            "fresh Phase 7 report differs from persisted receipt"
        )

    before = snapshot["state_before"]
    after = snapshot["state_after"]
    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "persistence_receipt_sha256": receipt["receipt_sha256"],
        "promotion_request_sha256": request["request_sha256"],
        "fresh_phase7_evidence_status_sha256": fresh_status[
            "phase7_evidence_status_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "phase_name": PHASE7,
        "evidence_type": PHASE7_EVIDENCE_TYPE,
        "promoted_at": receipt["promoted_at"],
        "history_id": receipt["history_id"],
        "evidence_sha256": receipt["evidence_sha256"],
        "current_record": current,
        "history_record": history[0],
        "phase6_current_record": phase6,
        "phase6_dependency_still_promoted": True,
        "current_record_matches_receipt": True,
        "history_record_matches_receipt": True,
        "phase7_history_count": 1,
        "exact_single_history_entry": True,
        "fresh_phase7_report_matches_receipt": True,
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "promotion_snapshot_read_only": True,
        "source_database_unchanged": True,
        "post_promotion_audit_ready": True,
        "phase7_promotion_confirmed": True,
        "phase7_promotion_persisted": True,
        "requires_separate_phase8_workflow": True,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified_by_audit": False,
    }
    report = {
        **identity,
        "post_promotion_audit_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_post_promotion_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of persisted Phase 7 promotion evidence. "
            "Confirms exactly one immutable Phase 7 history/current record, "
            "the Phase 6 dependency, and a fresh matching promotion-ready "
            "report without granting any live authority."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--persistence-receipt", required=True)
    parser.add_argument("--promotion-request", required=True)
    parser.add_argument("--phase6-post-promotion-audit", required=True)
    args = parser.parse_args()

    report = build_post_promotion_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        persistence_receipt_path=args.persistence_receipt,
        promotion_request_path=args.promotion_request,
        phase6_post_promotion_audit_path=args.phase6_post_promotion_audit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
