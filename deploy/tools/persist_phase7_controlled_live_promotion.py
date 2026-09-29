from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
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
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_PROMOTION_PERSISTENCE_RECEIPT_V1"

READINESS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_promotion_readiness.py"
)
STATUS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_evidence_status.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_promotion_request.py"
)
PHASE_PROMOTION_MODULE = Path(
    "python-learner/src/meteora_learner/phase_promotion.py"
)
STORAGE_MODULE = Path("python-learner/src/meteora_learner/storage.py")

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "e85f55f361ef9dbdeab307203a1652ecc38746c9",
    STATUS_TOOL: "77aa894be5d3e04534e521d159fa4743e759ef06",
    REQUEST_TOOL: "7cc053123d767aa5f6c5b6a94bdb90777c859d47",
    PHASE_PROMOTION_MODULE: "209cc3b29e3d3759079818b48d08734f050da4ac",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

LOCK_PATH = Path("/run/lock/pio-phase7-promotion.lock")
PHASE6 = "PHASE6"
PHASE6_EVIDENCE_TYPE = "PHASE6_PROMOTION_V1"
PHASE7 = "PHASE7"
PHASE7_EVIDENCE_TYPE = "PHASE7_PROMOTION_V1"

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
    "phase7_evidence_status_sha256",
    "promotion_request_sha256",
    "production_repository",
    "pio_database_path",
    "approver_principal",
    "approval_id",
    "phase_name",
    "evidence_type",
    "promoted_at",
    "evidence_record",
    "evidence_sha256",
    "phase7_report_sha256",
    "runtime_lock_used",
    "schema_contract_verified",
    "phase6_dependency_rechecked",
    "in_transaction_replay_check_passed",
    "history_id",
    "history_inserted",
    "current_record_inserted",
    "rows_changed",
    "history_record_verified",
    "current_record_verified",
    "database_transaction_committed",
    "phase7_promotion_persistence_authorized",
    "phase7_promotion_persisted",
    "requires_post_promotion_audit",
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
    "production_pio_database_modified_by_executor",
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
                f"Phase 7 persistence dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 persistence dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / READINESS_TOOL,
            "phase7_persistence_readiness",
        ),
        _load_module(
            source / STATUS_TOOL,
            "phase7_persistence_status",
        ),
        _load_module(
            source / REQUEST_TOOL,
            "phase7_persistence_request",
        ),
    )


def _safe_database(production: Path, expected_path: str) -> Path:
    expected = Path(expected_path)
    database = production / "data" / "pio.db"
    if not expected.is_absolute():
        raise ValueError(
            "Phase 7 persistence database binding is not absolute"
        )
    if expected.resolve(strict=False) != database.resolve(strict=False):
        raise ValueError("Phase 7 persistence database binding mismatch")
    data_dir = production / "data"
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError("Phase 7 persistence data directory is unsafe")
    st = os.lstat(database)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("Phase 7 persistence database must be regular")
    return database


def _table_columns(
    conn: sqlite3.Connection,
    table: str,
) -> tuple[str, ...]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return tuple(str(row[1]) for row in rows)


def _verify_schema_contract(conn: sqlite3.Connection) -> None:
    if _table_columns(conn, "phase_promotion_evidence") != CURRENT_COLUMNS:
        raise ValueError("Phase 7 persistence current-table schema mismatch")
    if (
        _table_columns(conn, "phase_promotion_evidence_history")
        != HISTORY_COLUMNS
    ):
        raise ValueError("Phase 7 persistence history-table schema mismatch")

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
        raise ValueError(
            "Phase 7 persistence immutable-history triggers missing"
        )
    expected_abort = (
        "RAISE(ABORT,'phase_promotion_evidence_historyisimmutable')"
    )
    update_sql = "".join(
        trigger_sql["phase_promotion_history_no_update"].split()
    )
    delete_sql = "".join(
        trigger_sql["phase_promotion_history_no_delete"].split()
    )
    if (
        "BEFOREUPDATEONphase_promotion_evidence_history"
        not in update_sql
        or expected_abort not in update_sql
    ):
        raise ValueError(
            "Phase 7 persistence history-update trigger drifted"
        )
    if (
        "BEFOREDELETEONphase_promotion_evidence_history"
        not in delete_sql
        or expected_abort not in delete_sql
    ):
        raise ValueError(
            "Phase 7 persistence history-delete trigger drifted"
        )


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


def _persist_exact_phase7_in_transaction(
    *,
    conn: sqlite3.Connection,
    evidence_record: dict[str, Any],
) -> dict[str, Any]:
    _verify_schema_contract(conn)

    phase6 = conn.execute(
        """
        SELECT evidence_type, qualified
        FROM phase_promotion_evidence
        WHERE phase_name = ?
        LIMIT 1
        """,
        (PHASE6,),
    ).fetchone()
    if (
        phase6 is None
        or str(phase6[0]) != PHASE6_EVIDENCE_TYPE
        or not bool(phase6[1])
    ):
        raise ValueError(
            "Phase 6 is no longer promoted inside transaction"
        )

    current = conn.execute(
        """
        SELECT phase_name
        FROM phase_promotion_evidence
        WHERE phase_name = ?
        LIMIT 1
        """,
        (PHASE7,),
    ).fetchone()
    history_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM phase_promotion_evidence_history
            WHERE phase_name = ?
            """,
            (PHASE7,),
        ).fetchone()[0]
    )
    if current is not None or history_count != 0:
        raise ValueError(
            "Phase 7 promotion replay detected inside transaction"
        )

    promoted_at = datetime.now(timezone.utc).isoformat()
    evidence_json = json.dumps(
        evidence_record,
        separators=(",", ":"),
    )
    before_changes = conn.total_changes

    history_cursor = conn.execute(
        """
        INSERT INTO phase_promotion_evidence_history(
            phase_name, promoted_at, evidence_type, qualified, evidence_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            PHASE7,
            promoted_at,
            PHASE7_EVIDENCE_TYPE,
            1,
            evidence_json,
        ),
    )
    history_id = int(history_cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO phase_promotion_evidence(
            phase_name, promoted_at, evidence_type, qualified, evidence_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            PHASE7,
            promoted_at,
            PHASE7_EVIDENCE_TYPE,
            1,
            evidence_json,
        ),
    )
    rows_changed = conn.total_changes - before_changes
    if rows_changed != 2:
        raise ValueError(
            "Phase 7 persistence changed unexpected row count"
        )

    current_row = conn.execute(
        """
        SELECT phase_name, promoted_at, evidence_type, qualified, evidence_json
        FROM phase_promotion_evidence
        WHERE phase_name = ?
        """,
        (PHASE7,),
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
        "phase_name": PHASE7,
        "promoted_at": promoted_at,
        "evidence_type": PHASE7_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": evidence_record,
    }
    if _row_payload(current_row) != expected:
        raise ValueError(
            "Phase 7 current promotion row verification failed"
        )
    if _row_payload(history_row) != expected:
        raise ValueError(
            "Phase 7 history row verification failed"
        )

    return {
        "promoted_at": promoted_at,
        "history_id": history_id,
        "rows_changed": rows_changed,
        "schema_contract_verified": True,
        "phase6_dependency_rechecked": True,
        "in_transaction_replay_check_passed": True,
        "history_inserted": True,
        "current_record_inserted": True,
        "history_record_verified": True,
        "current_record_verified": True,
    }


def validate_persistence_receipt(receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict):
        raise ValueError(
            "Phase 7 persistence receipt must be a JSON object"
        )
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError(
            "Phase 7 persistence receipt schema mismatch"
        )
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 persistence receipt format"
        )
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 persistence receipt type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "Phase 7 persistence receipt lineage mismatch"
        )

    for field in (
        "saved_promotion_readiness_sha256",
        "fresh_promotion_readiness_sha256",
        "phase7_evidence_status_sha256",
        "promotion_request_sha256",
        "evidence_sha256",
        "phase7_report_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(receipt.get(field), 64):
            raise ValueError(
                f"Phase 7 persistence receipt {field} is invalid"
            )

    if receipt["saved_promotion_readiness_sha256"] != receipt[
        "fresh_promotion_readiness_sha256"
    ]:
        raise ValueError(
            "Phase 7 persistence readiness digest mismatch"
        )
    if receipt.get("phase_name") != PHASE7:
        raise ValueError(
            "Phase 7 persistence receipt phase mismatch"
        )
    if receipt.get("evidence_type") != PHASE7_EVIDENCE_TYPE:
        raise ValueError(
            "Phase 7 persistence receipt evidence type mismatch"
        )
    if not isinstance(receipt.get("evidence_record"), dict):
        raise ValueError(
            "Phase 7 persistence receipt evidence is invalid"
        )
    if _sha256_bytes(
        _canonical_bytes(receipt["evidence_record"])
    ) != receipt["evidence_sha256"]:
        raise ValueError(
            "Phase 7 persistence receipt evidence digest mismatch"
        )
    if receipt["evidence_sha256"] != receipt["phase7_report_sha256"]:
        raise ValueError(
            "Phase 7 persistence report/evidence digest mismatch"
        )

    for field in (
        "runtime_lock_used",
        "schema_contract_verified",
        "phase6_dependency_rechecked",
        "in_transaction_replay_check_passed",
        "history_inserted",
        "current_record_inserted",
        "history_record_verified",
        "current_record_verified",
        "database_transaction_committed",
        "phase7_promotion_persistence_authorized",
        "phase7_promotion_persisted",
        "requires_post_promotion_audit",
        "production_pio_database_modified_by_executor",
    ):
        if receipt.get(field) is not True:
            raise ValueError(
                f"Phase 7 persistence receipt requires {field}=true"
            )
    if receipt.get("rows_changed") != 2:
        raise ValueError(
            "Phase 7 persistence receipt requires exactly two changed rows"
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
    ):
        if receipt.get(field) is not False:
            raise ValueError(
                f"Phase 7 persistence receipt requires {field}=false"
            )

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if receipt["receipt_sha256"] != expected:
        raise ValueError(
            "Phase 7 persistence receipt digest mismatch"
        )


def persist_phase7_promotion(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_readiness_path: str | Path,
    saved_completion_handoff_path: str | Path,
    saved_post_label_audit_path: str | Path,
    phase6_post_promotion_audit_path: str | Path,
    promotion_request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    readiness_module, status_module, request_module = _load_reviewed(source)
    saved_readiness = _load_json(
        saved_readiness_path,
        label="saved Phase 7 promotion readiness",
    )
    request = _load_json(
        promotion_request_path,
        label="Phase 7 promotion request",
    )
    readiness_module.validate_phase7_promotion_readiness(saved_readiness)
    request_module.validate_phase7_promotion_request(request)

    fresh_readiness = readiness_module.build_phase7_promotion_readiness(
        repository=production,
        source_tree=source,
        saved_completion_handoff_path=saved_completion_handoff_path,
        saved_post_label_audit_path=saved_post_label_audit_path,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
        promotion_request_path=promotion_request_path,
        saved_signed_authorization_verification_path=(
            saved_signed_authorization_verification_path
        ),
        signed_payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=(
            expected_allowed_signers_sha256
        ),
        now=now,
    )
    readiness_module.validate_phase7_promotion_readiness(fresh_readiness)
    if fresh_readiness != saved_readiness:
        raise ValueError(
            "fresh Phase 7 promotion readiness differs from saved readiness"
        )

    fresh_status = status_module.build_phase7_evidence_status(
        repository=production,
        source_tree=source,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
    )
    status_module.validate_phase7_evidence_status(fresh_status)
    if fresh_status["phase7_evidence_status_sha256"] != request[
        "phase7_evidence_status_sha256"
    ]:
        raise ValueError(
            "fresh Phase 7 evidence status differs from promotion request"
        )
    if fresh_status.get("phase7_promotion_ready") is not True:
        raise ValueError(
            "fresh Phase 7 evidence is no longer promotion-ready"
        )
    if fresh_status.get("phase7_reasons") != []:
        raise ValueError(
            "fresh Phase 7 evidence has blocking reasons"
        )
    evidence_record = fresh_status.get("phase7_report")
    if not isinstance(evidence_record, dict):
        raise ValueError(
            "fresh Phase 7 promotion report is missing"
        )
    evidence_sha = _sha256_bytes(_canonical_bytes(evidence_record))
    if evidence_sha != fresh_status.get("phase7_report_sha256"):
        raise ValueError(
            "fresh Phase 7 promotion report digest mismatch"
        )

    database = _safe_database(
        production,
        fresh_readiness["pio_database_path"],
    )
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        conn = sqlite3.connect(database)
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = _persist_exact_phase7_in_transaction(
                conn=conn,
                evidence_record=evidence_record,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

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
        "saved_promotion_readiness_sha256": saved_readiness[
            "readiness_sha256"
        ],
        "fresh_promotion_readiness_sha256": fresh_readiness[
            "readiness_sha256"
        ],
        "phase7_evidence_status_sha256": fresh_status[
            "phase7_evidence_status_sha256"
        ],
        "promotion_request_sha256": request["request_sha256"],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "approver_principal": fresh_readiness["approver_principal"],
        "approval_id": fresh_readiness["approval_id"],
        "phase_name": PHASE7,
        "evidence_type": PHASE7_EVIDENCE_TYPE,
        "promoted_at": result["promoted_at"],
        "evidence_record": evidence_record,
        "evidence_sha256": evidence_sha,
        "phase7_report_sha256": fresh_status["phase7_report_sha256"],
        "runtime_lock_used": True,
        "schema_contract_verified": result["schema_contract_verified"],
        "phase6_dependency_rechecked": result[
            "phase6_dependency_rechecked"
        ],
        "in_transaction_replay_check_passed": result[
            "in_transaction_replay_check_passed"
        ],
        "history_id": result["history_id"],
        "history_inserted": result["history_inserted"],
        "current_record_inserted": result["current_record_inserted"],
        "rows_changed": result["rows_changed"],
        "history_record_verified": result["history_record_verified"],
        "current_record_verified": result["current_record_verified"],
        "database_transaction_committed": True,
        "phase7_promotion_persistence_authorized": True,
        "phase7_promotion_persisted": True,
        "requires_post_promotion_audit": True,
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
        "production_pio_database_modified_by_executor": True,
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
            "Atomically persist exactly one Phase 7 promotion evidence record "
            "after a fresh promotion-readiness recheck and verified human "
            "authorization. No trading or transaction authority is granted."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--saved-completion-handoff", required=True)
    parser.add_argument("--saved-post-label-audit", required=True)
    parser.add_argument("--phase6-post-promotion-audit", required=True)
    parser.add_argument("--promotion-request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    receipt = persist_phase7_promotion(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_readiness_path=args.saved_readiness,
        saved_completion_handoff_path=args.saved_completion_handoff,
        saved_post_label_audit_path=args.saved_post_label_audit,
        phase6_post_promotion_audit_path=args.phase6_post_promotion_audit,
        promotion_request_path=args.promotion_request,
        saved_signed_authorization_verification_path=(
            args.saved_signed_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=(
            args.expected_allowed_signers_sha256
        ),
        now=args.now,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
