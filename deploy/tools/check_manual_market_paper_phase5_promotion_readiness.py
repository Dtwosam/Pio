from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
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
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_PROMOTION_READINESS_V1"

POST_COLLECTION_AUDIT_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_post_collection.py"
)
PROMOTION_REQUEST_TOOL = Path(
    "deploy/tools/build_manual_market_paper_phase5_promotion_request.py"
)
SIGNED_AUTH_TOOL = Path(
    "deploy/tools/build_manual_market_paper_phase5_promotion_signed_authorization.py"
)
PHASE5_STATUS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_status.py"
)
PHASE_PROMOTION_MODULE = Path(
    "python-learner/src/meteora_learner/phase_promotion.py"
)
STORAGE_MODULE = Path(
    "python-learner/src/meteora_learner/storage.py"
)

REVIEWED_SOURCE_BLOBS = {
    POST_COLLECTION_AUDIT_TOOL: "6c9f1f8fa3aa12ee4deed617637dcf6b0e569613",
    PROMOTION_REQUEST_TOOL: "a6fed80a99e79c3f9f6482139cd0e0b374c63b2d",
    SIGNED_AUTH_TOOL: "aa300d78ad8fa0be008588591a55fecf531a17d8",
    PHASE5_STATUS_TOOL: "f3b90f3100c23f8454172f3bb97482e31df5921d",
    PHASE_PROMOTION_MODULE: "209cc3b29e3d3759079818b48d08734f050da4ac",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

PHASE_NAME = "PHASE5"
EVIDENCE_TYPE = "PHASE5_PROMOTION_V1"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_post_collection_audit_sha256",
    "fresh_post_collection_audit_sha256",
    "promotion_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "saved_phase5_evidence_status_sha256",
    "fresh_phase5_evidence_status_sha256",
    "production_repository",
    "paper_database_path",
    "account",
    "run_id",
    "approver_principal",
    "approval_id",
    "material_post_collection_state_matches",
    "material_phase5_evidence_matches",
    "promotion_request_matches_evidence",
    "fresh_signature_matches_saved",
    "approval_not_expired",
    "phase5_current_record_present",
    "phase5_history_count",
    "phase5_promotion_absent",
    "database_sha256_before",
    "database_sha256_after",
    "wal_sha256_before",
    "wal_sha256_after",
    "shm_sha256_before",
    "shm_sha256_after",
    "promotion_snapshot_read_only",
    "phase5_promotion_readiness_ready",
    "requires_separate_persistence_executor",
    "phase5_promotion_persisted",
    "paper_timer_enable_authorized",
    "paper_collection_start_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "new_market_entry_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_paper_database_modified_by_readiness",
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
            raise ValueError(f"Phase 5 promotion readiness dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 5 promotion readiness dependency mismatch: {relative}"
            )

    post_audit = _load_module(
        source / POST_COLLECTION_AUDIT_TOOL,
        "manual_market_paper_phase5_promotion_readiness_post_audit",
    )
    request = _load_module(
        source / PROMOTION_REQUEST_TOOL,
        "manual_market_paper_phase5_promotion_readiness_request",
    )
    signer = _load_module(
        source / SIGNED_AUTH_TOOL,
        "manual_market_paper_phase5_promotion_readiness_signer",
    )
    status = _load_module(
        source / PHASE5_STATUS_TOOL,
        "manual_market_paper_phase5_promotion_readiness_status",
    )
    return post_audit, request, signer, status


def _normalized_phase5_status(value: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(value)
    normalized.pop("phase5_evidence_status_sha256", None)
    normalized.pop("endurance_sha256", None)
    endurance = normalized.get("endurance")
    if isinstance(endurance, dict):
        endurance.pop("checked_at", None)
    return normalized


def _normalized_post_collection(value: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(value)
    normalized.pop("post_collection_audit_sha256", None)
    normalized.pop("audit_checked_at", None)
    normalized.pop("fresh_phase5_evidence_status_sha256", None)
    status = normalized.get("fresh_phase5_evidence_status")
    if isinstance(status, dict):
        normalized["fresh_phase5_evidence_status"] = _normalized_phase5_status(
            status
        )
    return normalized


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 5 promotion SQLite sidecar is unsafe: {path.name}")
    return _sha256_bytes(path.read_bytes())


def _promotion_snapshot(database: Path) -> dict[str, Any]:
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError("Phase 5 promotion database is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("Phase 5 promotion database must be a regular file")

    wal = Path(str(database) + "-wal")
    shm = Path(str(database) + "-shm")
    before = {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(wal),
        "shm": _regular_hash_or_none(shm),
    }

    with tempfile.TemporaryDirectory(prefix="pio-phase5-promotion-readiness.") as tmp:
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
            current = conn.execute(
                """
                SELECT promoted_at, evidence_type, qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE_NAME,),
            ).fetchone()
            history_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM phase_promotion_evidence_history
                    WHERE phase_name = ?
                    """,
                    (PHASE_NAME,),
                ).fetchone()[0]
            )
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
        raise ValueError("production PAPER database sidecars changed during promotion check")

    current_record = None
    if current is not None:
        current_record = {
            "promoted_at": str(current[0]),
            "evidence_type": str(current[1]),
            "qualified": bool(current[2]),
            "evidence": json.loads(str(current[3])),
        }

    return {
        "current_record": current_record,
        "history_count": history_count,
        "database_sha256_before": before["database"],
        "database_sha256_after": after["database"],
        "wal_sha256_before": before["wal"],
        "wal_sha256_after": after["wal"],
        "shm_sha256_before": before["shm"],
        "shm_sha256_after": after["shm"],
    }


def validate_phase5_promotion_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 5 promotion readiness must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"promotion_readiness_sha256"}:
        raise ValueError("Phase 5 promotion readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 5 promotion readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 5 promotion readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 5 promotion readiness lineage mismatch")

    for field in (
        "saved_post_collection_audit_sha256",
        "fresh_post_collection_audit_sha256",
        "promotion_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "saved_phase5_evidence_status_sha256",
        "fresh_phase5_evidence_status_sha256",
        "database_sha256_before",
        "database_sha256_after",
        "promotion_readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 5 promotion readiness {field} invalid")

    for field in (
        "wal_sha256_before",
        "wal_sha256_after",
        "shm_sha256_before",
        "shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 5 promotion readiness {field} invalid")

    for field in (
        "production_repository",
        "paper_database_path",
        "account",
        "run_id",
        "approver_principal",
        "approval_id",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 5 promotion readiness {field} invalid")
    if not report["production_repository"].startswith("/"):
        raise ValueError("Phase 5 promotion readiness repository must be absolute")
    if not report["paper_database_path"].startswith("/"):
        raise ValueError("Phase 5 promotion readiness database must be absolute")

    for field in (
        "material_post_collection_state_matches",
        "material_phase5_evidence_matches",
        "promotion_request_matches_evidence",
        "fresh_signature_matches_saved",
        "approval_not_expired",
        "phase5_promotion_absent",
        "promotion_snapshot_read_only",
        "phase5_promotion_readiness_ready",
        "requires_separate_persistence_executor",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 5 promotion readiness requires {field}=true")

    if report.get("phase5_current_record_present") is not False:
        raise ValueError("Phase 5 promotion readiness requires no current record")
    if report.get("phase5_history_count") != 0:
        raise ValueError("Phase 5 promotion readiness requires empty history")

    if report["database_sha256_before"] != report["database_sha256_after"]:
        raise ValueError("Phase 5 promotion readiness database changed")
    if report["wal_sha256_before"] != report["wal_sha256_after"]:
        raise ValueError("Phase 5 promotion readiness WAL changed")
    if report["shm_sha256_before"] != report["shm_sha256_after"]:
        raise ValueError("Phase 5 promotion readiness SHM changed")

    for field in (
        "phase5_promotion_persisted",
        "paper_timer_enable_authorized",
        "paper_collection_start_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "new_market_entry_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_paper_database_modified_by_readiness",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 5 promotion readiness requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["promotion_readiness_sha256"] != expected:
        raise ValueError("Phase 5 promotion readiness digest mismatch")


def build_phase5_promotion_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    post_cycle_audit_path: str | Path,
    pre_collection_phase5_evidence_status_path: str | Path,
    activation_receipt_path: str | Path,
    saved_post_collection_audit_path: str | Path,
    promotion_request_path: str | Path,
    saved_signed_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
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

    post_audit_module, request_module, signer_module, status_module = (
        _load_reviewed_modules(source)
    )

    saved_post = _load_json(
        saved_post_collection_audit_path,
        label="saved Phase 5 post-collection audit",
    )
    request = _load_json(
        promotion_request_path,
        label="Phase 5 promotion request",
    )
    saved_verification = _load_json(
        saved_signed_verification_path,
        label="saved Phase 5 promotion signed verification",
    )

    post_audit_module.validate_post_collection_audit(saved_post)
    request_module.validate_phase5_promotion_request(request)
    signer_module.validate_verification(saved_verification)

    if saved_post.get("post_collection_audit_ready") is not True:
        raise ValueError("saved Phase 5 post-collection audit is not ready")
    if saved_post.get("phase5_promotion_ready") is not True:
        raise ValueError("saved Phase 5 post-collection evidence is not ready")
    if request.get("post_collection_audit_sha256") != saved_post.get(
        "post_collection_audit_sha256"
    ):
        raise ValueError("Phase 5 promotion request audit binding mismatch")
    if saved_verification.get("request_sha256") != request.get("request_sha256"):
        raise ValueError("Phase 5 promotion signed request binding mismatch")

    now_text = (
        now
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )

    fresh_post = post_audit_module.build_post_collection_audit(
        repository=production,
        source_tree=source,
        post_cycle_audit_path=post_cycle_audit_path,
        pre_collection_phase5_evidence_status_path=(
            pre_collection_phase5_evidence_status_path
        ),
        activation_receipt_path=activation_receipt_path,
        now=now_text,
    )
    post_audit_module.validate_post_collection_audit(fresh_post)

    material_post_matches = (
        _normalized_post_collection(fresh_post)
        == _normalized_post_collection(saved_post)
    )
    if not material_post_matches:
        raise ValueError("fresh Phase 5 post-collection material state drifted")

    saved_status = saved_post["fresh_phase5_evidence_status"]
    fresh_status = fresh_post["fresh_phase5_evidence_status"]
    status_module.validate_phase5_evidence_status(saved_status)
    status_module.validate_phase5_evidence_status(fresh_status)

    material_status_matches = (
        _normalized_phase5_status(fresh_status)
        == _normalized_phase5_status(saved_status)
    )
    if not material_status_matches:
        raise ValueError("fresh Phase 5 promotion evidence materially drifted")
    if saved_status.get("phase5_evidence_status_sha256") != request.get(
        "phase5_evidence_status_sha256"
    ):
        raise ValueError("signed Phase 5 promotion request status binding mismatch")
    if Path(str(request["production_repository"])).resolve() != production:
        raise ValueError("Phase 5 promotion request repository binding mismatch")
    if request.get("account") != saved_status.get("account"):
        raise ValueError("Phase 5 promotion request account binding mismatch")
    if request.get("run_id") != saved_status.get("run_id"):
        raise ValueError("Phase 5 promotion request run-id binding mismatch")
    if request.get("phase5_criteria_sha256") != saved_status.get(
        "phase5_criteria_sha256"
    ):
        raise ValueError("Phase 5 promotion request criteria binding mismatch")
    if request.get("endurance_sha256") != saved_status.get("endurance_sha256"):
        raise ValueError("Phase 5 promotion request endurance binding mismatch")
    if request.get("ledger_audit_sha256") != saved_status.get(
        "ledger_audit_sha256"
    ):
        raise ValueError("Phase 5 promotion request ledger binding mismatch")
    if request.get("closed_positions") != saved_status.get("closed_positions"):
        raise ValueError("Phase 5 promotion request closed-position binding mismatch")
    if request.get("distinct_valued_pools") != saved_status.get(
        "distinct_valued_pools"
    ):
        raise ValueError("Phase 5 promotion request pool-diversity binding mismatch")
    if fresh_status.get("phase5_promotion_ready") is not True:
        raise ValueError("fresh Phase 5 evidence is no longer promotion-ready")
    if fresh_status.get("phase5_reasons") != []:
        raise ValueError("fresh Phase 5 evidence has blocking reasons")

    fresh_verification = signer_module.verify_authorization(
        source_tree=source,
        request_path=promotion_request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        now=now_text,
    )
    signer_module.validate_verification(fresh_verification)
    if fresh_verification != saved_verification:
        raise ValueError("fresh Phase 5 promotion signature verification drifted")

    database = production / "data" / "pio.db"
    expected_database = Path(str(fresh_status["paper_database_path"]))
    if expected_database.resolve(strict=False) != database.resolve(strict=False):
        raise ValueError("Phase 5 promotion database binding mismatch")

    promotion_state = _promotion_snapshot(database)
    current_present = promotion_state["current_record"] is not None
    history_count = int(promotion_state["history_count"])
    if current_present:
        raise ValueError("Phase 5 promotion current record already exists")
    if history_count != 0:
        raise ValueError("Phase 5 promotion history is already non-empty")

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
        "saved_post_collection_audit_sha256": saved_post[
            "post_collection_audit_sha256"
        ],
        "fresh_post_collection_audit_sha256": fresh_post[
            "post_collection_audit_sha256"
        ],
        "promotion_request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": saved_verification[
            "verification_sha256"
        ],
        "fresh_signed_authorization_verification_sha256": fresh_verification[
            "verification_sha256"
        ],
        "saved_phase5_evidence_status_sha256": saved_status[
            "phase5_evidence_status_sha256"
        ],
        "fresh_phase5_evidence_status_sha256": fresh_status[
            "phase5_evidence_status_sha256"
        ],
        "production_repository": str(production),
        "paper_database_path": str(database),
        "account": request["account"],
        "run_id": request["run_id"],
        "approver_principal": fresh_verification["approver_principal"],
        "approval_id": fresh_verification["approval_id"],
        "material_post_collection_state_matches": True,
        "material_phase5_evidence_matches": True,
        "promotion_request_matches_evidence": True,
        "fresh_signature_matches_saved": True,
        "approval_not_expired": True,
        "phase5_current_record_present": False,
        "phase5_history_count": 0,
        "phase5_promotion_absent": True,
        "database_sha256_before": promotion_state["database_sha256_before"],
        "database_sha256_after": promotion_state["database_sha256_after"],
        "wal_sha256_before": promotion_state["wal_sha256_before"],
        "wal_sha256_after": promotion_state["wal_sha256_after"],
        "shm_sha256_before": promotion_state["shm_sha256_before"],
        "shm_sha256_after": promotion_state["shm_sha256_after"],
        "promotion_snapshot_read_only": True,
        "phase5_promotion_readiness_ready": True,
        "requires_separate_persistence_executor": True,
        "phase5_promotion_persisted": False,
        "paper_timer_enable_authorized": False,
        "paper_collection_start_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "new_market_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified_by_readiness": False,
    }
    report = {
        **identity,
        "promotion_readiness_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase5_promotion_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the final read-only readiness artifact before persisting "
            "Phase 5 promotion evidence. The gate rebuilds the post-collection "
            "audit, compares normalized material evidence, re-verifies the "
            "short-lived SSH authorization, and proves no Phase 5 promotion "
            "record/history exists using a private SQLite snapshot. It never "
            "persists promotion or authorizes PAPER/live-capital actions."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-cycle-audit", required=True)
    parser.add_argument("--pre-collection-phase5-evidence-status", required=True)
    parser.add_argument("--activation-receipt", required=True)
    parser.add_argument("--saved-post-collection-audit", required=True)
    parser.add_argument("--promotion-request", required=True)
    parser.add_argument("--signed-authorization-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_phase5_promotion_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        post_cycle_audit_path=args.post_cycle_audit,
        pre_collection_phase5_evidence_status_path=(
            args.pre_collection_phase5_evidence_status
        ),
        activation_receipt_path=args.activation_receipt,
        saved_post_collection_audit_path=args.saved_post_collection_audit,
        promotion_request_path=args.promotion_request,
        saved_signed_verification_path=args.signed_authorization_verification,
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
