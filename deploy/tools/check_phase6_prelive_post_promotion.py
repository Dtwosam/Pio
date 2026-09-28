from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE6_PRELIVE_POST_PROMOTION_AUDIT_V1"

PERSISTENCE_TOOL = Path("deploy/tools/persist_phase6_prelive_promotion.py")
STATUS_TOOL = Path("deploy/tools/check_phase6_prelive_evidence_status.py")
REQUEST_TOOL = Path("deploy/tools/build_phase6_prelive_promotion_request.py")

REVIEWED_SOURCE_BLOBS = {
    PERSISTENCE_TOOL: "06bb96bc54a7d639d779510231e19cea0bd402ca",
    STATUS_TOOL: "306497b937778cb8b77462f1457937f3499ca6ea",
    REQUEST_TOOL: "214e18373a096444a8d710e7a4a40dcabfb8d811",
}

PHASE5 = "PHASE5"
PHASE5_EVIDENCE_TYPE = "PHASE5_PROMOTION_V1"
PHASE6 = "PHASE6"
PHASE6_EVIDENCE_TYPE = "PHASE6_PROMOTION_V1"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "persistence_receipt_sha256",
    "promotion_request_sha256",
    "fresh_phase6_evidence_status_sha256",
    "production_repository",
    "pio_database_path",
    "execution_database_path",
    "phase_name",
    "evidence_type",
    "promoted_at",
    "history_id",
    "evidence_sha256",
    "current_record",
    "history_record",
    "phase5_current_record",
    "phase5_dependency_still_promoted",
    "current_record_matches_receipt",
    "history_record_matches_receipt",
    "phase6_history_count",
    "exact_single_history_entry",
    "fresh_phase6_report_matches_receipt",
    "fresh_execution_database_matches_request",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "execution_database_sha256_before",
    "execution_database_sha256_after",
    "execution_wal_sha256_before",
    "execution_wal_sha256_after",
    "execution_shm_sha256_before",
    "execution_shm_sha256_after",
    "promotion_snapshot_read_only",
    "source_databases_unchanged",
    "post_promotion_audit_ready",
    "phase6_promotion_confirmed",
    "phase6_promotion_persisted",
    "requires_separate_phase7_workflow",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified_by_audit",
    "production_execution_database_modified_by_audit",
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
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 6 post-promotion dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 6 post-promotion dependency mismatch: {relative}"
            )

    persistence = _load_module(
        source / PERSISTENCE_TOOL,
        "phase6_post_promotion_persistence",
    )
    status = _load_module(
        source / STATUS_TOOL,
        "phase6_post_promotion_status",
    )
    request = _load_module(
        source / REQUEST_TOOL,
        "phase6_post_promotion_request",
    )
    return persistence, status, request


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 6 post-promotion sidecar is unsafe: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _row_payload(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
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
        raise ValueError("Phase 6 post-promotion database is invalid")

    before = _database_state(database)
    if before["database"] is None:
        raise ValueError("Phase 6 post-promotion database is missing")

    with tempfile.TemporaryDirectory(prefix="pio-phase6-post-promotion.") as tmp:
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
                SELECT phase_name, promoted_at, evidence_type, qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE6,),
            ).fetchone()
            history_rows = conn.execute(
                """
                SELECT id, phase_name, promoted_at, evidence_type, qualified,
                       evidence_json
                FROM phase_promotion_evidence_history
                WHERE phase_name = ?
                ORDER BY id ASC
                """,
                (PHASE6,),
            ).fetchall()
            phase5 = conn.execute(
                """
                SELECT phase_name, promoted_at, evidence_type, qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE5,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise ValueError("Phase 6 promotion tables are unavailable") from exc
        finally:
            conn.close()

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database sidecars changed during Phase 6 post-promotion audit"
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
        "phase5_current_record": _row_payload(phase5),
        "state_before": before,
        "state_after": after,
    }


def _validate_nested_row(
    row: dict[str, Any] | None,
    *,
    expected_evidence_sha256: str,
    label: str,
) -> None:
    if not isinstance(row, dict):
        raise ValueError(f"Phase 6 post-promotion {label} is missing")
    if row.get("phase_name") != PHASE6:
        raise ValueError(f"Phase 6 post-promotion {label} phase mismatch")
    if row.get("evidence_type") != PHASE6_EVIDENCE_TYPE:
        raise ValueError(f"Phase 6 post-promotion {label} evidence type mismatch")
    if row.get("qualified") is not True:
        raise ValueError(f"Phase 6 post-promotion {label} is not qualified")
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError(f"Phase 6 post-promotion {label} evidence is invalid")
    if _sha256_bytes(_canonical_bytes(evidence)) != expected_evidence_sha256:
        raise ValueError(f"Phase 6 post-promotion {label} evidence digest mismatch")


def validate_post_promotion_audit(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 6 post-promotion audit must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"post_promotion_audit_sha256"}:
        raise ValueError("Phase 6 post-promotion audit schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 6 post-promotion audit format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 6 post-promotion audit type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 6 post-promotion audit lineage mismatch")

    for field in (
        "persistence_receipt_sha256",
        "promotion_request_sha256",
        "fresh_phase6_evidence_status_sha256",
        "evidence_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "execution_database_sha256_before",
        "execution_database_sha256_after",
        "post_promotion_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 6 post-promotion audit {field} is invalid")

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
        "execution_wal_sha256_before",
        "execution_wal_sha256_after",
        "execution_shm_sha256_before",
        "execution_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 6 post-promotion audit {field} is invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "execution_database_path",
        "promoted_at",
    ):
        value = report.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Phase 6 post-promotion audit {field} is invalid")

    if report.get("phase_name") != PHASE6:
        raise ValueError("Phase 6 post-promotion audit phase mismatch")
    if report.get("evidence_type") != PHASE6_EVIDENCE_TYPE:
        raise ValueError("Phase 6 post-promotion audit evidence type mismatch")
    if not isinstance(report.get("history_id"), int) or report["history_id"] <= 0:
        raise ValueError("Phase 6 post-promotion history id is invalid")

    _validate_nested_row(
        report.get("current_record"),
        expected_evidence_sha256=report["evidence_sha256"],
        label="current record",
    )
    history = report.get("history_record")
    if not isinstance(history, dict) or history.get("id") != report["history_id"]:
        raise ValueError("Phase 6 post-promotion history record id mismatch")
    _validate_nested_row(
        history,
        expected_evidence_sha256=report["evidence_sha256"],
        label="history record",
    )

    phase5 = report.get("phase5_current_record")
    if (
        not isinstance(phase5, dict)
        or phase5.get("phase_name") != PHASE5
        or phase5.get("evidence_type") != PHASE5_EVIDENCE_TYPE
        or phase5.get("qualified") is not True
    ):
        raise ValueError("Phase 6 post-promotion Phase 5 dependency is invalid")

    for field in (
        "phase5_dependency_still_promoted",
        "current_record_matches_receipt",
        "history_record_matches_receipt",
        "exact_single_history_entry",
        "fresh_phase6_report_matches_receipt",
        "fresh_execution_database_matches_request",
        "promotion_snapshot_read_only",
        "source_databases_unchanged",
        "post_promotion_audit_ready",
        "phase6_promotion_confirmed",
        "phase6_promotion_persisted",
        "requires_separate_phase7_workflow",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 6 post-promotion audit requires {field}=true")

    if report.get("phase6_history_count") != 1:
        raise ValueError("Phase 6 post-promotion audit requires one history entry")

    if report["pio_database_sha256_before"] != report["pio_database_sha256_after"]:
        raise ValueError("Phase 6 post-promotion audit changed Pio database")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("Phase 6 post-promotion audit changed Pio WAL")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("Phase 6 post-promotion audit changed Pio SHM")
    if (
        report["execution_database_sha256_before"]
        != report["execution_database_sha256_after"]
    ):
        raise ValueError("Phase 6 post-promotion audit changed execution database")
    if report["execution_wal_sha256_before"] != report["execution_wal_sha256_after"]:
        raise ValueError("Phase 6 post-promotion audit changed execution WAL")
    if report["execution_shm_sha256_before"] != report["execution_shm_sha256_after"]:
        raise ValueError("Phase 6 post-promotion audit changed execution SHM")

    for field in (
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified_by_audit",
        "production_execution_database_modified_by_audit",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 6 post-promotion audit requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["post_promotion_audit_sha256"] != expected:
        raise ValueError("Phase 6 post-promotion audit digest mismatch")


def build_post_promotion_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    phase5_post_promotion_audit_path: str | Path,
    promotion_request_path: str | Path,
    persistence_receipt_path: str | Path,
    execution_database_path: str | Path,
) -> dict[str, Any]:
    source_candidate = Path(source_tree).expanduser()
    production_candidate = Path(repository).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    if production_candidate.is_symlink():
        raise ValueError("production repository root must not be a symlink")
    source = source_candidate.resolve()
    production = production_candidate.resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    persistence_module, status_module, request_module = _load_reviewed_modules(source)
    receipt = _load_json(
        persistence_receipt_path,
        label="Phase 6 promotion persistence receipt",
    )
    request = _load_json(
        promotion_request_path,
        label="Phase 6 promotion request",
    )
    persistence_module.validate_persistence_receipt(receipt)
    request_module.validate_phase6_promotion_request(request)

    if receipt.get("phase6_promotion_persisted") is not True:
        raise ValueError("Phase 6 promotion receipt is not persisted")
    if receipt.get("requires_post_promotion_audit") is not True:
        raise ValueError("Phase 6 promotion receipt does not require this audit")
    if receipt.get("promotion_request_sha256") != request.get("request_sha256"):
        raise ValueError("Phase 6 post-promotion request binding mismatch")
    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError("Phase 6 post-promotion repository binding mismatch")

    pio_database = Path(str(receipt["pio_database_path"])).resolve(strict=True)
    if pio_database != (production / "data" / "pio.db").resolve(strict=False):
        raise ValueError("Phase 6 post-promotion Pio database binding mismatch")

    execution_database = Path(execution_database_path).expanduser()
    if not execution_database.is_absolute():
        raise ValueError("Phase 6 post-promotion execution database must be absolute")
    execution_database = execution_database.resolve(strict=True)
    if execution_database.is_symlink() or not execution_database.is_file():
        raise ValueError("Phase 6 post-promotion execution database is invalid")
    if execution_database != Path(str(receipt["execution_database_path"])).resolve():
        raise ValueError("Phase 6 post-promotion receipt execution DB mismatch")
    if execution_database != Path(str(request["execution_database_path"])).resolve():
        raise ValueError("Phase 6 post-promotion request execution DB mismatch")

    pio_before = _database_state(pio_database)
    execution_before = _database_state(execution_database)

    snapshot = _promotion_snapshot(pio_database)
    current = snapshot["current_record"]
    history_rows = snapshot["history"]
    phase5 = snapshot["phase5_current_record"]

    expected_row = {
        "phase_name": PHASE6,
        "promoted_at": receipt["promoted_at"],
        "evidence_type": PHASE6_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": receipt["evidence_record"],
    }

    if current != expected_row:
        raise ValueError("Phase 6 current promotion row differs from receipt")
    if len(history_rows) != 1:
        raise ValueError("Phase 6 promotion history count differs from receipt")
    history = history_rows[0]
    if history.get("id") != receipt["history_id"]:
        raise ValueError("Phase 6 promotion history id differs from receipt")
    if {key: value for key, value in history.items() if key != "id"} != expected_row:
        raise ValueError("Phase 6 history promotion row differs from receipt")

    if (
        not isinstance(phase5, dict)
        or phase5.get("evidence_type") != PHASE5_EVIDENCE_TYPE
        or phase5.get("qualified") is not True
    ):
        raise ValueError("Phase 5 promotion dependency regressed")

    fresh = status_module.build_phase6_evidence_status(
        repository=production,
        source_tree=source,
        phase5_post_promotion_audit_path=phase5_post_promotion_audit_path,
        execution_database_path=execution_database,
    )
    status_module.validate_phase6_evidence_status(fresh)

    if fresh.get("phase6_promotion_ready") is not True:
        raise ValueError("fresh Phase 6 evidence is no longer promotion-ready")
    if fresh.get("phase6_reasons") != []:
        raise ValueError("fresh Phase 6 evidence has blocking reasons")
    if fresh.get("phase6_report") != receipt.get("evidence_record"):
        raise ValueError("fresh Phase 6 report differs from persisted evidence")
    if fresh.get("phase6_report_sha256") != receipt.get("evidence_sha256"):
        raise ValueError("fresh Phase 6 report digest differs from receipt")
    if fresh.get("phase6_report_sha256") != request.get("phase6_report_sha256"):
        raise ValueError("fresh Phase 6 report digest differs from request")
    if fresh.get("phase6_criteria_sha256") != request.get("phase6_criteria_sha256"):
        raise ValueError("fresh Phase 6 criteria digest differs from request")
    if fresh.get("execution_database_sha256_after") != request.get(
        "execution_database_sha256"
    ):
        raise ValueError("fresh Phase 6 execution database differs from request")

    pio_after = _database_state(pio_database)
    execution_after = _database_state(execution_database)
    if pio_after != pio_before:
        raise ValueError("Pio database changed during Phase 6 post-promotion audit")
    if execution_after != execution_before:
        raise ValueError(
            "execution database changed during Phase 6 post-promotion audit"
        )

    evidence_sha = _sha256_bytes(_canonical_bytes(receipt["evidence_record"]))
    if evidence_sha != receipt["evidence_sha256"]:
        raise ValueError("Phase 6 persistence receipt evidence digest mismatch")

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
        "fresh_phase6_evidence_status_sha256": fresh[
            "phase6_evidence_status_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(pio_database),
        "execution_database_path": str(execution_database),
        "phase_name": PHASE6,
        "evidence_type": PHASE6_EVIDENCE_TYPE,
        "promoted_at": receipt["promoted_at"],
        "history_id": receipt["history_id"],
        "evidence_sha256": evidence_sha,
        "current_record": current,
        "history_record": history,
        "phase5_current_record": phase5,
        "phase5_dependency_still_promoted": True,
        "current_record_matches_receipt": True,
        "history_record_matches_receipt": True,
        "phase6_history_count": 1,
        "exact_single_history_entry": True,
        "fresh_phase6_report_matches_receipt": True,
        "fresh_execution_database_matches_request": True,
        "pio_database_sha256_before": pio_before["database"],
        "pio_database_sha256_after": pio_after["database"],
        "pio_wal_sha256_before": pio_before["wal"],
        "pio_wal_sha256_after": pio_after["wal"],
        "pio_shm_sha256_before": pio_before["shm"],
        "pio_shm_sha256_after": pio_after["shm"],
        "execution_database_sha256_before": execution_before["database"],
        "execution_database_sha256_after": execution_after["database"],
        "execution_wal_sha256_before": execution_before["wal"],
        "execution_wal_sha256_after": execution_after["wal"],
        "execution_shm_sha256_before": execution_before["shm"],
        "execution_shm_sha256_after": execution_after["shm"],
        "promotion_snapshot_read_only": True,
        "source_databases_unchanged": True,
        "post_promotion_audit_ready": True,
        "phase6_promotion_confirmed": True,
        "phase6_promotion_persisted": True,
        "requires_separate_phase7_workflow": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified_by_audit": False,
        "production_execution_database_modified_by_audit": False,
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
            "Audit atomic Phase 6 promotion persistence. The audit proves the "
            "exact current/history rows, re-evaluates the pre-live corpus from "
            "private SQLite snapshots, and stops at a separate Phase 7 workflow. "
            "It never activates controlled LIVE, live-submit, signing/submission, "
            "or live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase5-post-promotion-audit", required=True)
    parser.add_argument("--promotion-request", required=True)
    parser.add_argument("--persistence-receipt", required=True)
    parser.add_argument("--execution-db", required=True)
    args = parser.parse_args()

    report = build_post_promotion_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        phase5_post_promotion_audit_path=args.phase5_post_promotion_audit,
        promotion_request_path=args.promotion_request,
        persistence_receipt_path=args.persistence_receipt,
        execution_database_path=args.execution_db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
