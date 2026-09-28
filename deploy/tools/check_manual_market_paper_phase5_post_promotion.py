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
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_POST_PROMOTION_AUDIT_V1"

PERSISTENCE_TOOL = Path(
    "deploy/tools/persist_manual_market_paper_phase5_promotion.py"
)
PROMOTION_REQUEST_TOOL = Path(
    "deploy/tools/build_manual_market_paper_phase5_promotion_request.py"
)
PHASE5_STATUS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_status.py"
)
COLLECTION_READINESS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_collection_readiness.py"
)

REVIEWED_SOURCE_BLOBS = {
    PERSISTENCE_TOOL: "4231d6903834ffd0915315369f8b4a743b8e79e0",
    PROMOTION_REQUEST_TOOL: "a6fed80a99e79c3f9f6482139cd0e0b374c63b2d",
    PHASE5_STATUS_TOOL: "f3b90f3100c23f8454172f3bb97482e31df5921d",
    COLLECTION_READINESS_TOOL: "6c72b5d63cf92eb48048f16e68f9258fa3be13dc",
}

PHASE3 = "PHASE3"
PHASE3_EVIDENCE_TYPE = "PHASE3_PROMOTION_V1"
PHASE5 = "PHASE5"
PHASE5_EVIDENCE_TYPE = "PHASE5_PROMOTION_V1"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "persistence_receipt_sha256",
    "promotion_request_sha256",
    "fresh_phase5_evidence_status_sha256",
    "production_repository",
    "paper_database_path",
    "account",
    "run_id",
    "phase_name",
    "evidence_type",
    "promoted_at",
    "history_id",
    "evidence_sha256",
    "current_record",
    "history_record",
    "phase3_current_record",
    "phase3_dependency_still_promoted",
    "current_record_matches_receipt",
    "history_record_matches_receipt",
    "phase5_history_count",
    "exact_single_history_entry",
    "fresh_phase5_evidence_matches_receipt",
    "service_unit",
    "timer_unit",
    "service_inactive",
    "timer_inactive",
    "timer_disabled",
    "units_match_reviewed",
    "no_unit_drop_ins",
    "database_sha256_before",
    "database_sha256_after",
    "wal_sha256_before",
    "wal_sha256_after",
    "shm_sha256_before",
    "shm_sha256_after",
    "promotion_snapshot_read_only",
    "post_promotion_audit_ready",
    "phase5_promotion_confirmed",
    "phase5_promotion_persisted",
    "requires_separate_phase6_workflow",
    "paper_timer_enable_authorized",
    "paper_collection_start_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "new_market_entry_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_paper_database_modified_by_audit",
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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 5 post-promotion audit dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 5 post-promotion audit dependency mismatch: {relative}"
            )
    persistence = _load_module(
        source / PERSISTENCE_TOOL,
        "manual_market_paper_phase5_post_promotion_persistence",
    )
    request = _load_module(
        source / PROMOTION_REQUEST_TOOL,
        "manual_market_paper_phase5_post_promotion_request",
    )
    status = _load_module(
        source / PHASE5_STATUS_TOOL,
        "manual_market_paper_phase5_post_promotion_status",
    )
    runtime = _load_module(
        source / COLLECTION_READINESS_TOOL,
        "manual_market_paper_phase5_post_promotion_runtime",
    )
    return persistence, request, status, runtime


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 5 post-promotion sidecar is unsafe: {path.name}")
    return _sha256_bytes(path.read_bytes())


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
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError("Phase 5 post-promotion database is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("Phase 5 post-promotion database must be regular")

    wal = Path(str(database) + "-wal")
    shm = Path(str(database) + "-shm")
    before = {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(wal),
        "shm": _regular_hash_or_none(shm),
    }

    with tempfile.TemporaryDirectory(prefix="pio-phase5-post-promotion.") as tmp:
        snapshot = Path(tmp) / "pio.db"
        source_uri = f"file:{database}?mode=ro"
        if before["wal"] is None:
            source_uri += "&immutable=1"
        source = sqlite3.connect(source_uri, uri=True)
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
            current_row = conn.execute(
                """
                SELECT phase_name, promoted_at, evidence_type,
                       qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE5,),
            ).fetchone()
            history_rows = conn.execute(
                """
                SELECT id, phase_name, promoted_at, evidence_type,
                       qualified, evidence_json
                FROM phase_promotion_evidence_history
                WHERE phase_name = ?
                ORDER BY id ASC
                """,
                (PHASE5,),
            ).fetchall()
            phase3_row = conn.execute(
                """
                SELECT phase_name, promoted_at, evidence_type,
                       qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE3,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise ValueError("Phase 5 promotion tables are unavailable") from exc
        finally:
            conn.close()

    after = {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(wal),
        "shm": _regular_hash_or_none(shm),
    }
    if after != before:
        raise ValueError(
            "production PAPER database sidecars changed during post-promotion audit"
        )

    history = [
        {
            "id": int(row[0]),
            **_row_payload(tuple(row[1:])),
        }
        for row in history_rows
    ]
    return {
        "current_record": _row_payload(current_row),
        "history": history,
        "phase3_current_record": _row_payload(phase3_row),
        "database_sha256_before": before["database"],
        "database_sha256_after": after["database"],
        "wal_sha256_before": before["wal"],
        "wal_sha256_after": after["wal"],
        "shm_sha256_before": before["shm"],
        "shm_sha256_after": after["shm"],
    }


def _validate_fresh_evidence(
    *,
    fresh: dict[str, Any],
    request: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    if fresh.get("account") != receipt.get("account"):
        raise ValueError("Phase 5 post-promotion fresh account mismatch")
    if fresh.get("run_id") != receipt.get("run_id"):
        raise ValueError("Phase 5 post-promotion fresh run-id mismatch")
    if fresh.get("phase3_promoted") is not True:
        raise ValueError("Phase 5 post-promotion Phase 3 dependency regressed")
    if fresh.get("phase5_promotion_ready") is not True:
        raise ValueError("Phase 5 post-promotion evidence is no longer ready")
    if fresh.get("phase5_reasons") != []:
        raise ValueError("Phase 5 post-promotion evidence has blockers")

    for digest_field, value_field in (
        ("phase5_criteria_sha256", "phase5_criteria"),
        ("endurance_sha256", "endurance"),
        ("ledger_audit_sha256", "ledger_audit"),
    ):
        value = fresh.get(value_field)
        if not isinstance(value, dict):
            raise ValueError(f"Phase 5 post-promotion {value_field} invalid")
        if _sha256_bytes(_canonical_bytes(value)) != request.get(digest_field):
            raise ValueError(
                f"Phase 5 post-promotion {value_field} drifted from signed evidence"
            )
    if fresh.get("closed_positions") != request.get("closed_positions"):
        raise ValueError("Phase 5 post-promotion closed positions drifted")
    if fresh.get("distinct_valued_pools") != request.get(
        "distinct_valued_pools"
    ):
        raise ValueError("Phase 5 post-promotion pool diversity drifted")


def validate_post_promotion_audit(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 5 post-promotion audit must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"post_promotion_audit_sha256"}:
        raise ValueError("Phase 5 post-promotion audit schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 5 post-promotion audit format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 5 post-promotion audit type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 5 post-promotion audit lineage mismatch")

    for field in (
        "persistence_receipt_sha256",
        "promotion_request_sha256",
        "fresh_phase5_evidence_status_sha256",
        "evidence_sha256",
        "database_sha256_before",
        "database_sha256_after",
        "post_promotion_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 5 post-promotion audit {field} invalid")

    for field in (
        "wal_sha256_before",
        "wal_sha256_after",
        "shm_sha256_before",
        "shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 5 post-promotion audit {field} invalid")

    for field in (
        "production_repository",
        "paper_database_path",
        "account",
        "run_id",
        "phase_name",
        "evidence_type",
        "promoted_at",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 5 post-promotion audit {field} invalid")

    if report["phase_name"] != PHASE5:
        raise ValueError("Phase 5 post-promotion phase binding mismatch")
    if report["evidence_type"] != PHASE5_EVIDENCE_TYPE:
        raise ValueError("Phase 5 post-promotion evidence type mismatch")
    if not isinstance(report.get("history_id"), int) or report["history_id"] <= 0:
        raise ValueError("Phase 5 post-promotion history id invalid")
    if report.get("phase5_history_count") != 1:
        raise ValueError("Phase 5 post-promotion requires one history entry")

    for record_field in (
        "current_record",
        "history_record",
        "phase3_current_record",
        "service_unit",
        "timer_unit",
    ):
        if not isinstance(report.get(record_field), dict):
            raise ValueError(f"Phase 5 post-promotion {record_field} invalid")

    for field in (
        "phase3_dependency_still_promoted",
        "current_record_matches_receipt",
        "history_record_matches_receipt",
        "exact_single_history_entry",
        "fresh_phase5_evidence_matches_receipt",
        "service_inactive",
        "timer_inactive",
        "timer_disabled",
        "units_match_reviewed",
        "no_unit_drop_ins",
        "promotion_snapshot_read_only",
        "post_promotion_audit_ready",
        "phase5_promotion_confirmed",
        "phase5_promotion_persisted",
        "requires_separate_phase6_workflow",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 5 post-promotion audit requires {field}=true")

    if report["database_sha256_before"] != report["database_sha256_after"]:
        raise ValueError("Phase 5 post-promotion database changed during audit")
    if report["wal_sha256_before"] != report["wal_sha256_after"]:
        raise ValueError("Phase 5 post-promotion WAL changed during audit")
    if report["shm_sha256_before"] != report["shm_sha256_after"]:
        raise ValueError("Phase 5 post-promotion SHM changed during audit")

    current = report["current_record"]
    history = report["history_record"]
    phase3 = report["phase3_current_record"]
    if (
        current.get("phase_name") != PHASE5
        or current.get("promoted_at") != report["promoted_at"]
        or current.get("evidence_type") != PHASE5_EVIDENCE_TYPE
        or current.get("qualified") is not True
    ):
        raise ValueError("Phase 5 post-promotion current record is inconsistent")
    evidence = current.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("Phase 5 post-promotion current evidence is invalid")
    if _sha256_bytes(_canonical_bytes(evidence)) != report["evidence_sha256"]:
        raise ValueError("Phase 5 post-promotion evidence digest mismatch")
    if (
        history.get("id") != report["history_id"]
        or {key: value for key, value in history.items() if key != "id"} != current
    ):
        raise ValueError("Phase 5 post-promotion history/current mismatch")
    if (
        phase3.get("phase_name") != PHASE3
        or phase3.get("evidence_type") != PHASE3_EVIDENCE_TYPE
        or phase3.get("qualified") is not True
    ):
        raise ValueError("Phase 5 post-promotion Phase 3 record is inconsistent")

    for unit_field in ("service_unit", "timer_unit"):
        unit = report[unit_field]
        if unit.get("fragment_matches_reviewed") is not True:
            raise ValueError(
                f"Phase 5 post-promotion {unit_field} fragment drifted"
            )
        if unit.get("no_drop_ins") is not True:
            raise ValueError(
                f"Phase 5 post-promotion {unit_field} has drop-ins"
            )

    if report["service_unit"].get("active_state") != "inactive":
        raise ValueError("Phase 5 post-promotion PAPER service is active")
    if report["timer_unit"].get("active_state") != "inactive":
        raise ValueError("Phase 5 post-promotion PAPER timer is active")
    if report["timer_unit"].get("unit_file_state") != "disabled":
        raise ValueError("Phase 5 post-promotion PAPER timer is persistent")

    for field in (
        "paper_timer_enable_authorized",
        "paper_collection_start_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "new_market_entry_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_paper_database_modified_by_audit",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 5 post-promotion audit requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["post_promotion_audit_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("Phase 5 post-promotion audit digest mismatch")


def build_post_promotion_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    post_cycle_audit_path: str | Path,
    promotion_request_path: str | Path,
    persistence_receipt_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    persistence, request_module, status_module, runtime = (
        _load_reviewed_modules(source)
    )
    receipt = _load_json(
        persistence_receipt_path,
        label="Phase 5 promotion persistence receipt",
    )
    request = _load_json(
        promotion_request_path,
        label="Phase 5 promotion request",
    )
    persistence.validate_persistence_receipt(receipt)
    request_module.validate_phase5_promotion_request(request)

    if receipt.get("phase5_promotion_persisted") is not True:
        raise ValueError("Phase 5 persistence receipt is not persisted")
    if receipt.get("requires_post_promotion_audit") is not True:
        raise ValueError("Phase 5 persistence receipt does not require this audit")
    if request.get("request_sha256") != receipt.get("promotion_request_sha256"):
        raise ValueError("Phase 5 post-promotion request binding mismatch")
    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError("Phase 5 post-promotion repository binding mismatch")

    database = production / "data" / "pio.db"
    if Path(str(receipt["paper_database_path"])).resolve(strict=False) != (
        database.resolve(strict=False)
    ):
        raise ValueError("Phase 5 post-promotion database binding mismatch")

    snapshot = _promotion_snapshot(database)
    current = snapshot["current_record"]
    history = snapshot["history"]
    phase3 = snapshot["phase3_current_record"]

    expected = {
        "phase_name": PHASE5,
        "promoted_at": receipt["promoted_at"],
        "evidence_type": PHASE5_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": receipt["evidence_record"],
    }
    current_matches = current == expected
    history_count = len(history)
    history_record = history[0] if history_count == 1 else None
    expected_history = {
        "id": receipt["history_id"],
        **expected,
    }
    history_matches = history_record == expected_history

    if not current_matches:
        raise ValueError("Phase 5 current promotion row differs from receipt")
    if history_count != 1:
        raise ValueError("Phase 5 history count differs from first-promotion receipt")
    if not history_matches:
        raise ValueError("Phase 5 immutable history row differs from receipt")

    phase3_ok = bool(
        phase3
        and phase3.get("phase_name") == PHASE3
        and phase3.get("evidence_type") == PHASE3_EVIDENCE_TYPE
        and phase3.get("qualified") is True
    )
    if not phase3_ok:
        raise ValueError("Phase 3 promotion regressed after Phase 5 persistence")

    fresh = status_module.build_phase5_evidence_status(
        repository=production,
        source_tree=source,
        post_cycle_audit_path=post_cycle_audit_path,
    )
    status_module.validate_phase5_evidence_status(fresh)
    _validate_fresh_evidence(
        fresh=fresh,
        request=request,
        receipt=receipt,
    )

    account = str(receipt["account"])
    service = runtime._unit_snapshot(
        source=source,
        unit=f"pio-paper@{account}.service",
        reviewed_relative=runtime.PAPER_SERVICE_UNIT,
    )
    timer = runtime._unit_snapshot(
        source=source,
        unit=f"pio-paper@{account}.timer",
        reviewed_relative=runtime.PAPER_TIMER_UNIT,
    )
    runtime._validate_unit_snapshot(service)
    runtime._validate_unit_snapshot(timer)

    service_inactive = service["active_state"] == "inactive"
    timer_inactive = timer["active_state"] == "inactive"
    timer_disabled = timer["unit_file_state"] == "disabled"
    units_match = bool(
        service["fragment_matches_reviewed"]
        and timer["fragment_matches_reviewed"]
    )
    no_drop_ins = bool(service["no_drop_ins"] and timer["no_drop_ins"])
    if not service_inactive:
        raise ValueError("Phase 5 post-promotion PAPER service is active")
    if not timer_inactive:
        raise ValueError("Phase 5 post-promotion PAPER timer is active")
    if not timer_disabled:
        raise ValueError("Phase 5 post-promotion PAPER timer became persistent")
    if not units_match:
        raise ValueError("Phase 5 post-promotion PAPER unit bytes drifted")
    if not no_drop_ins:
        raise ValueError("Phase 5 post-promotion PAPER unit drop-ins detected")

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
        "fresh_phase5_evidence_status_sha256": fresh[
            "phase5_evidence_status_sha256"
        ],
        "production_repository": str(production),
        "paper_database_path": str(database),
        "account": receipt["account"],
        "run_id": receipt["run_id"],
        "phase_name": PHASE5,
        "evidence_type": PHASE5_EVIDENCE_TYPE,
        "promoted_at": receipt["promoted_at"],
        "history_id": receipt["history_id"],
        "evidence_sha256": receipt["evidence_sha256"],
        "current_record": current,
        "history_record": history_record,
        "phase3_current_record": phase3,
        "phase3_dependency_still_promoted": True,
        "current_record_matches_receipt": True,
        "history_record_matches_receipt": True,
        "phase5_history_count": 1,
        "exact_single_history_entry": True,
        "fresh_phase5_evidence_matches_receipt": True,
        "service_unit": service,
        "timer_unit": timer,
        "service_inactive": True,
        "timer_inactive": True,
        "timer_disabled": True,
        "units_match_reviewed": True,
        "no_unit_drop_ins": True,
        "database_sha256_before": snapshot["database_sha256_before"],
        "database_sha256_after": snapshot["database_sha256_after"],
        "wal_sha256_before": snapshot["wal_sha256_before"],
        "wal_sha256_after": snapshot["wal_sha256_after"],
        "shm_sha256_before": snapshot["shm_sha256_before"],
        "shm_sha256_after": snapshot["shm_sha256_after"],
        "promotion_snapshot_read_only": True,
        "post_promotion_audit_ready": True,
        "phase5_promotion_confirmed": True,
        "phase5_promotion_persisted": True,
        "requires_separate_phase6_workflow": True,
        "paper_timer_enable_authorized": False,
        "paper_collection_start_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "new_market_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified_by_audit": False,
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
            "Audit a persisted Phase 5 promotion without mutating production. "
            "The audit verifies the exact current/history promotion rows, "
            "rebuilds Phase 5 PAPER evidence from a private database snapshot, "
            "and requires the PAPER timer/service to remain inactive and the "
            "timer disabled. It does not start Phase 6 or authorize live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-cycle-audit", required=True)
    parser.add_argument("--promotion-request", required=True)
    parser.add_argument("--persistence-receipt", required=True)
    args = parser.parse_args()

    report = build_post_promotion_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        post_cycle_audit_path=args.post_cycle_audit,
        promotion_request_path=args.promotion_request,
        persistence_receipt_path=args.persistence_receipt,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
