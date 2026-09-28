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
ARTIFACT_TYPE = "PHASE6_PRELIVE_PROMOTION_READINESS_V1"

STATUS_TOOL = Path("deploy/tools/check_phase6_prelive_evidence_status.py")
REQUEST_TOOL = Path("deploy/tools/build_phase6_prelive_promotion_request.py")
SIGNER_TOOL = Path(
    "deploy/tools/build_phase6_prelive_promotion_signed_authorization.py"
)
STORAGE_MODULE = Path("python-learner/src/meteora_learner/storage.py")

REVIEWED_SOURCE_BLOBS = {
    STATUS_TOOL: "acb5d0aade6d572fb37b6fec63c4267216cb9549",
    REQUEST_TOOL: "372cb5cc573075f2f7660431df8a3939fe8bdea4",
    SIGNER_TOOL: "c09a0ab24445af48c90ed0325458b61aa9f772a5",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

PHASE5 = "PHASE5"
PHASE5_EVIDENCE_TYPE = "PHASE5_PROMOTION_V1"
PHASE6 = "PHASE6"
PHASE6_EVIDENCE_TYPE = "PHASE6_PROMOTION_V1"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_phase6_evidence_status_sha256",
    "fresh_phase6_evidence_status_sha256",
    "promotion_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "production_repository",
    "pio_database_path",
    "execution_database_path",
    "pio_database_sha256",
    "execution_database_sha256",
    "phase6_criteria_sha256",
    "phase6_report_sha256",
    "approval_payload_sha256",
    "approval_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "fresh_status_matches_saved",
    "fresh_authorization_matches_saved",
    "human_phase6_promotion_authorization_verified",
    "approval_not_expired",
    "phase5_dependency_current",
    "phase6_current_record_absent",
    "phase6_history_count",
    "phase6_history_empty",
    "anti_replay_ready",
    "passed_enter_intents",
    "distinct_pools",
    "blocked_intents",
    "distinct_authorized_wallets",
    "phase6_promotion_ready",
    "phase6_reasons",
    "promotion_readiness_ready",
    "requires_atomic_phase6_persistence",
    "phase6_promotion_persisted",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
    "production_execution_database_modified",
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
            raise ValueError(f"Phase 6 readiness dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 6 readiness dependency mismatch: {relative}")

    status = _load_module(source / STATUS_TOOL, "phase6_readiness_status")
    request = _load_module(source / REQUEST_TOOL, "phase6_readiness_request")
    signer = _load_module(source / SIGNER_TOOL, "phase6_readiness_signer")
    return status, request, signer


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 6 readiness sidecar is unsafe: {path.name}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _promotion_snapshot(database: Path) -> dict[str, Any]:
    if database.is_symlink() or not database.is_file():
        raise ValueError("Phase 6 readiness Pio database is invalid")

    before = _database_state(database)
    if before["database"] is None:
        raise ValueError("Phase 6 readiness Pio database is missing")

    with tempfile.TemporaryDirectory(prefix="pio-phase6-promotion-readiness.") as tmp:
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
            phase5 = conn.execute(
                """
                SELECT evidence_type, qualified
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE5,),
            ).fetchone()
            phase6 = conn.execute(
                """
                SELECT evidence_type, qualified
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE6,),
            ).fetchone()
            phase6_history_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM phase_promotion_evidence_history
                    WHERE phase_name = ?
                    """,
                    (PHASE6,),
                ).fetchone()[0]
            )
        except sqlite3.Error as exc:
            raise ValueError(
                "Phase 6 readiness promotion tables are unavailable"
            ) from exc
        finally:
            conn.close()

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database sidecars changed during Phase 6 readiness"
        )

    phase5_current = bool(
        phase5 is not None
        and str(phase5[0]) == PHASE5_EVIDENCE_TYPE
        and bool(phase5[1])
    )
    phase6_absent = phase6 is None
    return {
        "phase5_dependency_current": phase5_current,
        "phase6_current_record_absent": phase6_absent,
        "phase6_history_count": phase6_history_count,
        "phase6_history_empty": phase6_history_count == 0,
        "database_state": before,
    }


def validate_phase6_promotion_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 6 promotion readiness must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("Phase 6 promotion readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 6 promotion readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 6 promotion readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 6 promotion readiness lineage mismatch")

    for field in (
        "saved_phase6_evidence_status_sha256",
        "fresh_phase6_evidence_status_sha256",
        "promotion_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "pio_database_sha256",
        "execution_database_sha256",
        "phase6_criteria_sha256",
        "phase6_report_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 6 promotion readiness {field} invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "execution_database_path",
        "approver_principal",
        "approval_id",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 6 promotion readiness {field} invalid")

    if not report["production_repository"].startswith("/"):
        raise ValueError("Phase 6 promotion readiness repository must be absolute")

    for field in (
        "fresh_status_matches_saved",
        "fresh_authorization_matches_saved",
        "human_phase6_promotion_authorization_verified",
        "approval_not_expired",
        "phase5_dependency_current",
        "phase6_current_record_absent",
        "phase6_history_empty",
        "anti_replay_ready",
        "phase6_promotion_ready",
        "promotion_readiness_ready",
        "requires_atomic_phase6_persistence",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 6 promotion readiness requires {field}=true")

    if report.get("phase6_history_count") != 0:
        raise ValueError("Phase 6 promotion readiness requires zero history rows")
    if report.get("phase6_reasons") != []:
        raise ValueError("Phase 6 promotion readiness requires no blocking reasons")

    for field in (
        "passed_enter_intents",
        "distinct_pools",
        "blocked_intents",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 6 promotion readiness {field} invalid")

    wallets = report.get("distinct_authorized_wallets")
    if (
        not isinstance(wallets, list)
        or len(wallets) != 1
        or not isinstance(wallets[0], str)
        or not wallets[0]
    ):
        raise ValueError("Phase 6 promotion readiness requires one wallet")

    for field in (
        "phase6_promotion_persisted",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
        "production_execution_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 6 promotion readiness requires {field}=false")

    if (
        report["saved_phase6_evidence_status_sha256"]
        != report["fresh_phase6_evidence_status_sha256"]
    ):
        raise ValueError("Phase 6 promotion readiness status digest mismatch")
    if (
        report["saved_signed_authorization_verification_sha256"]
        != report["fresh_signed_authorization_verification_sha256"]
    ):
        raise ValueError("Phase 6 promotion readiness authorization digest mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["readiness_sha256"] != expected:
        raise ValueError("Phase 6 promotion readiness digest mismatch")


def build_phase6_promotion_readiness(
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
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    status_module, request_module, signer_module = _load_reviewed_modules(source)

    saved_status = _load_json(
        saved_phase6_evidence_status_path,
        label="saved Phase 6 evidence status",
    )
    request = _load_json(
        promotion_request_path,
        label="Phase 6 promotion request",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved Phase 6 signed authorization verification",
    )

    status_module.validate_phase6_evidence_status(saved_status)
    request_module.validate_phase6_promotion_request(request)
    signer_module.validate_verification(saved_verification)

    if request.get("phase6_evidence_status_sha256") != saved_status.get(
        "phase6_evidence_status_sha256"
    ):
        raise ValueError("Phase 6 promotion request/status binding mismatch")
    if request.get("production_repository") != str(production):
        raise ValueError("Phase 6 promotion request repository binding mismatch")
    if Path(str(request["execution_database_path"])).resolve() != Path(
        execution_database_path
    ).resolve():
        raise ValueError("Phase 6 promotion request execution DB binding mismatch")
    if saved_verification.get("request_sha256") != request.get("request_sha256"):
        raise ValueError("Phase 6 signed verification/request binding mismatch")
    if saved_verification.get(
        "human_phase6_promotion_authorization_verified"
    ) is not True:
        raise ValueError("Phase 6 human promotion authorization is not verified")

    fresh_status = status_module.build_phase6_evidence_status(
        repository=production,
        source_tree=source,
        phase5_post_promotion_audit_path=phase5_post_promotion_audit_path,
        execution_database_path=execution_database_path,
    )
    status_module.validate_phase6_evidence_status(fresh_status)
    if fresh_status != saved_status:
        raise ValueError("fresh Phase 6 evidence status differs from saved status")

    fresh_verification = signer_module.verify_authorization(
        source_tree=source,
        request_path=promotion_request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        now=now,
    )
    signer_module.validate_verification(fresh_verification)
    if fresh_verification != saved_verification:
        raise ValueError(
            "fresh Phase 6 authorization verification differs from saved verification"
        )

    pio_database = Path(str(fresh_status["pio_database_path"])).resolve()
    anti_replay = _promotion_snapshot(pio_database)
    if anti_replay["phase5_dependency_current"] is not True:
        raise ValueError("Phase 5 promotion dependency is no longer current")
    if anti_replay["phase6_current_record_absent"] is not True:
        raise ValueError("Phase 6 promotion already exists")
    if anti_replay["phase6_history_empty"] is not True:
        raise ValueError("Phase 6 promotion history is not empty")

    if fresh_status.get("phase6_promotion_ready") is not True:
        raise ValueError("fresh Phase 6 evidence is not promotion-ready")
    if fresh_status.get("phase6_reasons") != []:
        raise ValueError("fresh Phase 6 evidence has blocking reasons")

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
        "saved_phase6_evidence_status_sha256": saved_status[
            "phase6_evidence_status_sha256"
        ],
        "fresh_phase6_evidence_status_sha256": fresh_status[
            "phase6_evidence_status_sha256"
        ],
        "promotion_request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": saved_verification[
            "verification_sha256"
        ],
        "fresh_signed_authorization_verification_sha256": fresh_verification[
            "verification_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(pio_database),
        "execution_database_path": fresh_status["execution_database_path"],
        "pio_database_sha256": fresh_status["pio_database_sha256_after"],
        "execution_database_sha256": fresh_status[
            "execution_database_sha256_after"
        ],
        "phase6_criteria_sha256": fresh_status["phase6_criteria_sha256"],
        "phase6_report_sha256": fresh_status["phase6_report_sha256"],
        "approval_payload_sha256": fresh_verification[
            "approval_payload_sha256"
        ],
        "approval_signature_sha256": fresh_verification[
            "approval_signature_sha256"
        ],
        "allowed_signers_sha256": fresh_verification["allowed_signers_sha256"],
        "approver_principal": fresh_verification["approver_principal"],
        "approval_id": fresh_verification["approval_id"],
        "fresh_status_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_phase6_promotion_authorization_verified": True,
        "approval_not_expired": True,
        "phase5_dependency_current": True,
        "phase6_current_record_absent": True,
        "phase6_history_count": 0,
        "phase6_history_empty": True,
        "anti_replay_ready": True,
        "passed_enter_intents": fresh_status["passed_enter_intents"],
        "distinct_pools": fresh_status["distinct_pools"],
        "blocked_intents": fresh_status["blocked_intents"],
        "distinct_authorized_wallets": fresh_status[
            "distinct_authorized_wallets"
        ],
        "phase6_promotion_ready": True,
        "phase6_reasons": [],
        "promotion_readiness_ready": True,
        "requires_atomic_phase6_persistence": True,
        "phase6_promotion_persisted": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "production_execution_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase6_promotion_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Perform the final read-only Phase 6 promotion readiness recheck. "
            "The tool re-evaluates the current pre-live corpus, re-verifies the "
            "short-lived human signature, and proves Phase 6 has never been "
            "persisted. It does not write promotion evidence or authorize LIVE."
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
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_phase6_promotion_readiness(
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
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
