from __future__ import annotations

import argparse
import fcntl
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE6_PRELIVE_PROMOTION_PERSISTENCE_RECEIPT_V1"

READINESS_TOOL = Path("deploy/tools/check_phase6_prelive_promotion_readiness.py")
STATUS_TOOL = Path("deploy/tools/check_phase6_prelive_evidence_status.py")
REQUEST_TOOL = Path("deploy/tools/build_phase6_prelive_promotion_request.py")
PHASE_PROMOTION_MODULE = Path("python-learner/src/meteora_learner/phase_promotion.py")
STORAGE_MODULE = Path("python-learner/src/meteora_learner/storage.py")

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "a347220a5e9a766de5477e09fc966fb94832cc96",
    STATUS_TOOL: "306497b937778cb8b77462f1457937f3499ca6ea",
    REQUEST_TOOL: "214e18373a096444a8d710e7a4a40dcabfb8d811",
    PHASE_PROMOTION_MODULE: "209cc3b29e3d3759079818b48d08734f050da4ac",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

LOCK_PATH = Path("/run/lock/pio-phase6-promotion.lock")
PHASE5 = "PHASE5"
PHASE5_EVIDENCE_TYPE = "PHASE5_PROMOTION_V1"
PHASE6 = "PHASE6"
PHASE6_EVIDENCE_TYPE = "PHASE6_PROMOTION_V1"

CURRENT_COLUMNS = (
    "phase_name",
    "promoted_at",
    "evidence_type",
    "qualified",
    "evidence_json",
)
HISTORY_COLUMNS = (
    "id",
    "phase_name",
    "promoted_at",
    "evidence_type",
    "qualified",
    "evidence_json",
)
REQUIRED_HISTORY_TRIGGERS = (
    "phase_promotion_history_no_update",
    "phase_promotion_history_no_delete",
)

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_promotion_readiness_sha256",
    "fresh_promotion_readiness_sha256",
    "phase6_evidence_status_sha256",
    "promotion_request_sha256",
    "production_repository",
    "pio_database_path",
    "execution_database_path",
    "approver_principal",
    "approval_id",
    "phase_name",
    "evidence_type",
    "promoted_at",
    "evidence_record",
    "evidence_sha256",
    "runtime_lock_used",
    "schema_contract_verified",
    "phase5_dependency_rechecked",
    "in_transaction_replay_check_passed",
    "history_id",
    "history_inserted",
    "current_record_inserted",
    "rows_changed",
    "history_record_verified",
    "current_record_verified",
    "database_transaction_committed",
    "phase6_promotion_persistence_authorized",
    "phase6_promotion_persisted",
    "requires_post_promotion_audit",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
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
            raise ValueError(f"Phase 6 persistence dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 6 persistence dependency mismatch: {relative}")

    readiness = _load_module(source / READINESS_TOOL, "phase6_persistence_readiness")
    status = _load_module(source / STATUS_TOOL, "phase6_persistence_status")
    request = _load_module(source / REQUEST_TOOL, "phase6_persistence_request")
    return readiness, status, request


def _safe_database(production: Path, expected_path: str) -> Path:
    expected = Path(expected_path)
    database = production / "data" / "pio.db"
    if not expected.is_absolute():
        raise ValueError("Phase 6 persistence database binding is not absolute")
    if expected.resolve(strict=False) != database.resolve(strict=False):
        raise ValueError("Phase 6 persistence database binding mismatch")
    data_dir = production / "data"
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError("Phase 6 persistence data directory is unsafe")
    st = os.lstat(database)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("Phase 6 persistence database must be regular")
    return database


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return tuple(str(row[1]) for row in rows)


def _verify_schema_contract(conn: sqlite3.Connection) -> None:
    if _table_columns(conn, "phase_promotion_evidence") != CURRENT_COLUMNS:
        raise ValueError("Phase 6 persistence current-table schema mismatch")
    if _table_columns(conn, "phase_promotion_evidence_history") != HISTORY_COLUMNS:
        raise ValueError("Phase 6 persistence history-table schema mismatch")

    rows = conn.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'trigger'
          AND name IN (?, ?)
        ORDER BY name
        """,
        tuple(REQUIRED_HISTORY_TRIGGERS),
    ).fetchall()
    trigger_sql = {
        str(row[0]): " ".join(str(row[1] or "").split())
        for row in rows
    }
    if set(trigger_sql) != set(REQUIRED_HISTORY_TRIGGERS):
        raise ValueError("Phase 6 persistence immutable-history triggers missing")
    expected_abort = "RAISE(ABORT, 'phase_promotion_evidence_history is immutable')"
    if (
        "BEFORE UPDATE ON phase_promotion_evidence_history"
        not in trigger_sql["phase_promotion_history_no_update"]
        or expected_abort not in trigger_sql["phase_promotion_history_no_update"]
    ):
        raise ValueError("Phase 6 persistence history-update trigger drifted")
    if (
        "BEFORE DELETE ON phase_promotion_evidence_history"
        not in trigger_sql["phase_promotion_history_no_delete"]
        or expected_abort not in trigger_sql["phase_promotion_history_no_delete"]
    ):
        raise ValueError("Phase 6 persistence history-delete trigger drifted")


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


def _evidence_record(
    *,
    status: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    if status.get("phase6_promotion_ready") is not True:
        raise ValueError("Phase 6 persistence evidence is not ready")
    if status.get("phase6_reasons") != []:
        raise ValueError("Phase 6 persistence evidence has blockers")
    if status.get("phase5_promoted") is not True:
        raise ValueError("Phase 6 persistence requires Phase 5 promotion")

    if status.get("phase6_report_sha256") != request.get("phase6_report_sha256"):
        raise ValueError("Phase 6 persistence report digest binding mismatch")
    if status.get("phase6_criteria_sha256") != request.get("phase6_criteria_sha256"):
        raise ValueError("Phase 6 persistence criteria digest binding mismatch")
    if status.get("pio_database_sha256_after") != request.get("pio_database_sha256"):
        raise ValueError("Phase 6 persistence Pio DB digest binding mismatch")
    if status.get("execution_database_sha256_after") != request.get(
        "execution_database_sha256"
    ):
        raise ValueError("Phase 6 persistence execution DB digest binding mismatch")
    if status.get("distinct_authorized_wallets") != request.get(
        "distinct_authorized_wallets"
    ):
        raise ValueError("Phase 6 persistence wallet binding mismatch")

    record = status.get("phase6_report")
    if not isinstance(record, dict):
        raise ValueError("Phase 6 persistence report is missing")
    if _sha256_bytes(_canonical_bytes(record)) != request["phase6_report_sha256"]:
        raise ValueError("Phase 6 persistence report bytes mismatch")
    return record


def _persist_exact_phase6_in_transaction(
    *,
    conn: sqlite3.Connection,
    evidence_record: dict[str, Any],
) -> dict[str, Any]:
    _verify_schema_contract(conn)

    phase5 = conn.execute(
        """
        SELECT evidence_type, qualified
        FROM phase_promotion_evidence
        WHERE phase_name = ?
        LIMIT 1
        """,
        (PHASE5,),
    ).fetchone()
    if (
        phase5 is None
        or str(phase5[0]) != PHASE5_EVIDENCE_TYPE
        or not bool(phase5[1])
    ):
        raise ValueError("Phase 5 is no longer promoted inside transaction")

    current = conn.execute(
        """
        SELECT phase_name, promoted_at, evidence_type, qualified, evidence_json
        FROM phase_promotion_evidence
        WHERE phase_name = ?
        LIMIT 1
        """,
        (PHASE6,),
    ).fetchone()
    history_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM phase_promotion_evidence_history
            WHERE phase_name = ?
            """,
            (PHASE6,),
        ).fetchone()[0]
    )
    if current is not None or history_count != 0:
        raise ValueError("Phase 6 promotion replay detected inside transaction")

    promoted_at = datetime.now(timezone.utc).isoformat()
    evidence_json = json.dumps(evidence_record, separators=(",", ":"))
    before_changes = conn.total_changes

    history_cursor = conn.execute(
        """
        INSERT INTO phase_promotion_evidence_history(
            phase_name, promoted_at, evidence_type, qualified, evidence_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (PHASE6, promoted_at, PHASE6_EVIDENCE_TYPE, 1, evidence_json),
    )
    history_id = int(history_cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO phase_promotion_evidence(
            phase_name, promoted_at, evidence_type, qualified, evidence_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (PHASE6, promoted_at, PHASE6_EVIDENCE_TYPE, 1, evidence_json),
    )

    rows_changed = conn.total_changes - before_changes
    if rows_changed != 2:
        raise ValueError("Phase 6 persistence changed unexpected row count")

    current_row = conn.execute(
        """
        SELECT phase_name, promoted_at, evidence_type, qualified, evidence_json
        FROM phase_promotion_evidence
        WHERE phase_name = ?
        """,
        (PHASE6,),
    ).fetchone()
    history_row = conn.execute(
        """
        SELECT phase_name, promoted_at, evidence_type, qualified, evidence_json
        FROM phase_promotion_evidence_history
        WHERE id = ?
        """,
        (history_id,),
    ).fetchone()

    expected = {
        "phase_name": PHASE6,
        "promoted_at": promoted_at,
        "evidence_type": PHASE6_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": evidence_record,
    }
    if _row_payload(current_row) != expected:
        raise ValueError("Phase 6 current promotion row verification failed")
    if _row_payload(history_row) != expected:
        raise ValueError("Phase 6 history row verification failed")

    return {
        "promoted_at": promoted_at,
        "history_id": history_id,
        "rows_changed": rows_changed,
        "schema_contract_verified": True,
        "phase5_dependency_rechecked": True,
        "in_transaction_replay_check_passed": True,
        "history_inserted": True,
        "current_record_inserted": True,
        "history_record_verified": True,
        "current_record_verified": True,
    }


def validate_persistence_receipt(receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("Phase 6 persistence receipt must be a JSON object")
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError("Phase 6 persistence receipt schema mismatch")
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 6 persistence receipt format")
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 6 persistence receipt type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 6 persistence receipt lineage mismatch")

    for field in (
        "saved_promotion_readiness_sha256",
        "fresh_promotion_readiness_sha256",
        "phase6_evidence_status_sha256",
        "promotion_request_sha256",
        "evidence_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(receipt.get(field), 64):
            raise ValueError(f"Phase 6 persistence receipt {field} invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "execution_database_path",
        "approver_principal",
        "approval_id",
        "promoted_at",
    ):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(f"Phase 6 persistence receipt {field} invalid")

    if receipt.get("phase_name") != PHASE6:
        raise ValueError("Phase 6 persistence receipt phase mismatch")
    if receipt.get("evidence_type") != PHASE6_EVIDENCE_TYPE:
        raise ValueError("Phase 6 persistence receipt evidence type mismatch")

    evidence = receipt.get("evidence_record")
    if not isinstance(evidence, dict):
        raise ValueError("Phase 6 persistence receipt evidence is invalid")
    if receipt["evidence_sha256"] != _sha256_bytes(_canonical_bytes(evidence)):
        raise ValueError("Phase 6 persistence receipt evidence digest mismatch")

    if not isinstance(receipt.get("history_id"), int) or receipt["history_id"] <= 0:
        raise ValueError("Phase 6 persistence history id invalid")
    if receipt.get("rows_changed") != 2:
        raise ValueError("Phase 6 persistence must change exactly two rows")

    for field in (
        "runtime_lock_used",
        "schema_contract_verified",
        "phase5_dependency_rechecked",
        "in_transaction_replay_check_passed",
        "history_inserted",
        "current_record_inserted",
        "history_record_verified",
        "current_record_verified",
        "database_transaction_committed",
        "phase6_promotion_persistence_authorized",
        "phase6_promotion_persisted",
        "requires_post_promotion_audit",
    ):
        if receipt.get(field) is not True:
            raise ValueError(f"Phase 6 persistence receipt requires {field}=true")

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
    ):
        if receipt.get(field) is not False:
            raise ValueError(f"Phase 6 persistence receipt requires {field}=false")

    if (
        receipt["saved_promotion_readiness_sha256"]
        != receipt["fresh_promotion_readiness_sha256"]
    ):
        raise ValueError("Phase 6 persistence readiness digest mismatch")

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if receipt["receipt_sha256"] != expected:
        raise ValueError("Phase 6 persistence receipt digest mismatch")


def persist_phase6_promotion(
    *,
    repository: str | Path,
    source_tree: str | Path,
    phase5_post_promotion_audit_path: str | Path,
    saved_phase6_evidence_status_path: str | Path,
    promotion_request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    execution_database_path: str | Path,
    promotion_readiness_path: str | Path,
    expected_promotion_readiness_sha256: str,
    lock_path: str | Path = LOCK_PATH,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir() or not production.is_dir():
        raise ValueError("Phase 6 persistence source/production path invalid")
    if not _is_hex_digest(expected_promotion_readiness_sha256, 64):
        raise ValueError("expected Phase 6 readiness digest invalid")

    readiness_module, status_module, request_module = _load_reviewed_modules(source)
    saved_readiness = _load_json(
        promotion_readiness_path,
        label="saved Phase 6 promotion readiness",
    )
    status = _load_json(
        saved_phase6_evidence_status_path,
        label="saved Phase 6 evidence status",
    )
    request = _load_json(
        promotion_request_path,
        label="Phase 6 promotion request",
    )
    readiness_module.validate_phase6_promotion_readiness(saved_readiness)
    status_module.validate_phase6_evidence_status(status)
    request_module.validate_phase6_promotion_request(request)

    if saved_readiness["readiness_sha256"] != expected_promotion_readiness_sha256:
        raise ValueError("saved Phase 6 readiness digest mismatch")
    if saved_readiness.get("promotion_readiness_ready") is not True:
        raise ValueError("saved Phase 6 promotion readiness is not ready")
    if saved_readiness.get("requires_atomic_phase6_persistence") is not True:
        raise ValueError("saved readiness does not require this persistence executor")
    if Path(saved_readiness["production_repository"]).resolve() != production:
        raise ValueError("Phase 6 persistence repository binding mismatch")
    if status["phase6_evidence_status_sha256"] != saved_readiness[
        "saved_phase6_evidence_status_sha256"
    ]:
        raise ValueError("Phase 6 persistence status/readiness binding mismatch")
    if request["request_sha256"] != saved_readiness["promotion_request_sha256"]:
        raise ValueError("Phase 6 persistence request/readiness binding mismatch")

    database = _safe_database(production, saved_readiness["pio_database_path"])
    evidence_record = _evidence_record(status=status, request=request)

    lock = Path(lock_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Phase 6 promotion persistence lock is busy") from exc

        conn = sqlite3.connect(database, timeout=20.0, isolation_level=None)
        committed = False
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")

            fresh = readiness_module.build_phase6_promotion_readiness(
                repository=production,
                source_tree=source,
                phase5_post_promotion_audit_path=phase5_post_promotion_audit_path,
                saved_phase6_evidence_status_path=saved_phase6_evidence_status_path,
                promotion_request_path=promotion_request_path,
                saved_signed_authorization_verification_path=(
                    saved_signed_authorization_verification_path
                ),
                signed_payload_path=signed_payload_path,
                signature_path=signature_path,
                allowed_signers_path=allowed_signers_path,
                expected_allowed_signers_sha256=expected_allowed_signers_sha256,
                execution_database_path=execution_database_path,
                now=now,
            )
            readiness_module.validate_phase6_promotion_readiness(fresh)
            if fresh != saved_readiness:
                raise ValueError("fresh Phase 6 promotion readiness drifted")

            persisted = _persist_exact_phase6_in_transaction(
                conn=conn,
                evidence_record=evidence_record,
            )
            conn.commit()
            committed = True
        except Exception:
            if not committed:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
            raise
        finally:
            conn.close()

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
        "saved_promotion_readiness_sha256": saved_readiness["readiness_sha256"],
        "fresh_promotion_readiness_sha256": fresh["readiness_sha256"],
        "phase6_evidence_status_sha256": status["phase6_evidence_status_sha256"],
        "promotion_request_sha256": request["request_sha256"],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "execution_database_path": status["execution_database_path"],
        "approver_principal": saved_readiness["approver_principal"],
        "approval_id": saved_readiness["approval_id"],
        "phase_name": PHASE6,
        "evidence_type": PHASE6_EVIDENCE_TYPE,
        "promoted_at": persisted["promoted_at"],
        "evidence_record": evidence_record,
        "evidence_sha256": _sha256_bytes(_canonical_bytes(evidence_record)),
        "runtime_lock_used": True,
        "schema_contract_verified": persisted["schema_contract_verified"],
        "phase5_dependency_rechecked": persisted["phase5_dependency_rechecked"],
        "in_transaction_replay_check_passed": persisted[
            "in_transaction_replay_check_passed"
        ],
        "history_id": persisted["history_id"],
        "history_inserted": persisted["history_inserted"],
        "current_record_inserted": persisted["current_record_inserted"],
        "rows_changed": persisted["rows_changed"],
        "history_record_verified": persisted["history_record_verified"],
        "current_record_verified": persisted["current_record_verified"],
        "database_transaction_committed": True,
        "phase6_promotion_persistence_authorized": True,
        "phase6_promotion_persisted": True,
        "requires_post_promotion_audit": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
    }
    receipt = {
        **identity,
        "receipt_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_persistence_receipt(receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Atomically persist exactly one reviewed Phase 6 promotion record "
            "after a final locked readiness recheck. This writer changes only "
            "Phase 6 promotion evidence rows and never authorizes controlled "
            "LIVE, live-submit, signing/submission, or live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase5-post-promotion-audit", required=True)
    parser.add_argument("--saved-phase6-evidence-status", required=True)
    parser.add_argument("--promotion-request", required=True)
    parser.add_argument("--saved-signed-authorization-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--execution-db", required=True)
    parser.add_argument("--promotion-readiness", required=True)
    parser.add_argument("--expected-promotion-readiness-sha256", required=True)
    parser.add_argument("--lock-path", default=str(LOCK_PATH))
    parser.add_argument("--now")
    args = parser.parse_args()

    receipt = persist_phase6_promotion(
        repository=args.repo,
        source_tree=args.source_tree,
        phase5_post_promotion_audit_path=args.phase5_post_promotion_audit,
        saved_phase6_evidence_status_path=args.saved_phase6_evidence_status,
        promotion_request_path=args.promotion_request,
        saved_signed_authorization_verification_path=(
            args.saved_signed_authorization_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
        execution_database_path=args.execution_db,
        promotion_readiness_path=args.promotion_readiness,
        expected_promotion_readiness_sha256=(
            args.expected_promotion_readiness_sha256
        ),
        lock_path=args.lock_path,
        now=args.now,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
