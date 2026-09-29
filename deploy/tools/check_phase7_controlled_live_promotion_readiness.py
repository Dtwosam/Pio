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
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_PROMOTION_READINESS_V1"

HANDOFF_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_completion_evidence_handoff.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_promotion_request.py"
)
SIGNER_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_promotion_signed_authorization.py"
)
STORAGE_MODULE = Path("python-learner/src/meteora_learner/storage.py")

REVIEWED_SOURCE_BLOBS = {
    HANDOFF_TOOL: "3fdb4e289ff93fcd017f6a565cf16cfeebc2814b",
    REQUEST_TOOL: "7cc053123d767aa5f6c5b6a94bdb90777c859d47",
    SIGNER_TOOL: "8a17a04f5a83dd9b74f7e8e0525f98eebcbe38af",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

PHASE6 = "PHASE6"
PHASE6_EVIDENCE_TYPE = "PHASE6_PROMOTION_V1"
PHASE7 = "PHASE7"
PHASE7_EVIDENCE_TYPE = "PHASE7_PROMOTION_V1"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_completion_handoff_sha256",
    "fresh_completion_handoff_sha256",
    "promotion_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "phase7_evidence_status_sha256",
    "phase7_evidence_plan_sha256",
    "approval_payload_sha256",
    "approval_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "fresh_handoff_matches_saved",
    "fresh_authorization_matches_saved",
    "human_phase7_promotion_authorization_verified",
    "approval_not_expired",
    "phase6_dependency_current",
    "phase7_current_record_absent",
    "phase7_history_count",
    "phase7_history_empty",
    "anti_replay_ready",
    "confirmed_receipts",
    "failed_receipts",
    "closed_positions",
    "open_positions",
    "distinct_closed_pools",
    "valued_closed_positions",
    "labeled_closed_positions",
    "phase7_promotion_ready",
    "phase7_reasons",
    "promotion_readiness_ready",
    "requires_atomic_phase7_persistence",
    "phase7_promotion_persisted",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
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
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_reviewed(source: Path) -> tuple[Any, Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 promotion readiness dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 promotion readiness dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / HANDOFF_TOOL,
            "phase7_promotion_readiness_handoff",
        ),
        _load_module(
            source / REQUEST_TOOL,
            "phase7_promotion_readiness_request",
        ),
        _load_module(
            source / SIGNER_TOOL,
            "phase7_promotion_readiness_signer",
        ),
    )


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 7 promotion readiness sidecar is unsafe: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _promotion_snapshot(database: Path) -> dict[str, Any]:
    if database.is_symlink() or not database.is_file():
        raise ValueError("Phase 7 promotion readiness Pio database is invalid")
    before = _database_state(database)
    if before["database"] is None:
        raise ValueError("Phase 7 promotion readiness Pio database is missing")

    with tempfile.TemporaryDirectory(
        prefix="pio-phase7-promotion-readiness-"
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
            phase6 = conn.execute(
                """
                SELECT evidence_type, qualified
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (PHASE6,),
            ).fetchone()
            phase7 = conn.execute(
                """
                SELECT evidence_type, qualified
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
        except sqlite3.Error as exc:
            raise ValueError(
                "Phase 7 promotion readiness tables are unavailable"
            ) from exc
        finally:
            conn.close()

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during Phase 7 promotion readiness"
        )
    phase6_current = bool(
        phase6 is not None
        and str(phase6[0]) == PHASE6_EVIDENCE_TYPE
        and bool(phase6[1])
    )
    return {
        "phase6_dependency_current": phase6_current,
        "phase7_current_record_absent": phase7 is None,
        "phase7_history_count": history_count,
        "phase7_history_empty": history_count == 0,
        "database_state": before,
    }


def validate_phase7_promotion_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 promotion readiness must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("Phase 7 promotion readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 promotion readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 promotion readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 promotion readiness lineage mismatch")

    for field in (
        "saved_completion_handoff_sha256",
        "fresh_completion_handoff_sha256",
        "promotion_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "pio_database_sha256",
        "phase7_evidence_status_sha256",
        "phase7_evidence_plan_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 promotion readiness {field} is invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "approver_principal",
        "approval_id",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 7 promotion readiness {field} is invalid")

    for field in (
        "fresh_handoff_matches_saved",
        "fresh_authorization_matches_saved",
        "human_phase7_promotion_authorization_verified",
        "approval_not_expired",
        "phase6_dependency_current",
        "phase7_current_record_absent",
        "phase7_history_empty",
        "anti_replay_ready",
        "phase7_promotion_ready",
        "promotion_readiness_ready",
        "requires_atomic_phase7_persistence",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 promotion readiness requires {field}=true"
            )

    if report.get("phase7_history_count") != 0:
        raise ValueError("Phase 7 promotion readiness requires zero history rows")
    if report.get("phase7_reasons") != []:
        raise ValueError("Phase 7 promotion readiness requires no blocking reasons")
    if report.get("failed_receipts") != 0:
        raise ValueError("Phase 7 promotion readiness requires zero failed receipts")
    if report.get("open_positions") != 0:
        raise ValueError("Phase 7 promotion readiness requires zero open positions")
    if report["valued_closed_positions"] != report["closed_positions"]:
        raise ValueError("Phase 7 readiness requires full valuation coverage")
    if report["labeled_closed_positions"] != report["closed_positions"]:
        raise ValueError("Phase 7 readiness requires full label coverage")

    for field in (
        "confirmed_receipts",
        "failed_receipts",
        "closed_positions",
        "open_positions",
        "distinct_closed_pools",
        "valued_closed_positions",
        "labeled_closed_positions",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 7 promotion readiness {field} is invalid")

    for field in (
        "phase7_promotion_persisted",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 promotion readiness requires {field}=false"
            )

    if (
        report["saved_completion_handoff_sha256"]
        != report["fresh_completion_handoff_sha256"]
    ):
        raise ValueError("Phase 7 promotion readiness handoff digest mismatch")
    if (
        report["saved_signed_authorization_verification_sha256"]
        != report["fresh_signed_authorization_verification_sha256"]
    ):
        raise ValueError(
            "Phase 7 promotion readiness authorization digest mismatch"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["readiness_sha256"] != expected:
        raise ValueError("Phase 7 promotion readiness digest mismatch")


def build_phase7_promotion_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
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

    handoff_module, request_module, signer_module = _load_reviewed(source)
    saved_handoff = _load_json(
        saved_completion_handoff_path,
        label="saved Phase 7 EXIT completion handoff",
    )
    request = _load_json(
        promotion_request_path,
        label="Phase 7 promotion request",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved Phase 7 signed authorization verification",
    )
    handoff_module.validate_exit_completion_evidence_handoff(saved_handoff)
    request_module.validate_phase7_promotion_request(request)
    signer_module.validate_verification(saved_verification)

    if request["completion_handoff_sha256"] != saved_handoff["handoff_sha256"]:
        raise ValueError("Phase 7 promotion request/handoff binding mismatch")
    if Path(str(saved_handoff["production_repository"])).resolve() != production:
        raise ValueError("Phase 7 completion handoff repository binding mismatch")
    if Path(str(request["production_repository"])).resolve() != production:
        raise ValueError("Phase 7 promotion request repository binding mismatch")
    if saved_verification["request_sha256"] != request["request_sha256"]:
        raise ValueError("Phase 7 signed verification/request binding mismatch")
    if saved_verification.get(
        "human_phase7_promotion_authorization_verified"
    ) is not True:
        raise ValueError("Phase 7 human promotion authorization is not verified")

    fresh_handoff = handoff_module.build_exit_completion_evidence_handoff(
        repository=production,
        source_tree=source,
        saved_post_label_audit_path=saved_post_label_audit_path,
        expected_post_label_audit_sha256=saved_handoff[
            "saved_post_label_audit_sha256"
        ],
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
    )
    handoff_module.validate_exit_completion_evidence_handoff(fresh_handoff)
    if fresh_handoff != saved_handoff:
        raise ValueError(
            "fresh Phase 7 completion handoff differs from saved handoff"
        )
    if fresh_handoff.get("continuation_route") != handoff_module.ROUTE_PROMOTION:
        raise ValueError("fresh Phase 7 handoff is no longer promotion-routed")

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
            "fresh Phase 7 signed authorization differs from saved verification"
        )

    database = (production / "data" / "pio.db").resolve(strict=True)
    if Path(request["pio_database_path"]).resolve() != database:
        raise ValueError("Phase 7 promotion request database binding mismatch")
    snapshot = _promotion_snapshot(database)
    if snapshot["database_state"]["database"] != request["pio_database_sha256"]:
        raise ValueError(
            "Pio database changed after Phase 7 promotion request"
        )
    if not snapshot["phase6_dependency_current"]:
        raise ValueError("Phase 6 promotion dependency is no longer current")
    if not snapshot["phase7_current_record_absent"]:
        raise ValueError("Phase 7 promotion current record already exists")
    if not snapshot["phase7_history_empty"]:
        raise ValueError("Phase 7 promotion history is not empty")

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
        "saved_completion_handoff_sha256": saved_handoff["handoff_sha256"],
        "fresh_completion_handoff_sha256": fresh_handoff["handoff_sha256"],
        "promotion_request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": saved_verification[
            "verification_sha256"
        ],
        "fresh_signed_authorization_verification_sha256": fresh_verification[
            "verification_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": snapshot["database_state"]["database"],
        "phase7_evidence_status_sha256": request[
            "phase7_evidence_status_sha256"
        ],
        "phase7_evidence_plan_sha256": request[
            "phase7_evidence_plan_sha256"
        ],
        "approval_payload_sha256": fresh_verification[
            "approval_payload_sha256"
        ],
        "approval_signature_sha256": fresh_verification[
            "approval_signature_sha256"
        ],
        "allowed_signers_sha256": fresh_verification[
            "allowed_signers_sha256"
        ],
        "approver_principal": fresh_verification["approver_principal"],
        "approval_id": fresh_verification["approval_id"],
        "fresh_handoff_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_phase7_promotion_authorization_verified": True,
        "approval_not_expired": True,
        "phase6_dependency_current": True,
        "phase7_current_record_absent": True,
        "phase7_history_count": 0,
        "phase7_history_empty": True,
        "anti_replay_ready": True,
        "confirmed_receipts": request["confirmed_receipts"],
        "failed_receipts": request["failed_receipts"],
        "closed_positions": request["closed_positions"],
        "open_positions": request["open_positions"],
        "distinct_closed_pools": request["distinct_closed_pools"],
        "valued_closed_positions": request["valued_closed_positions"],
        "labeled_closed_positions": request["labeled_closed_positions"],
        "phase7_promotion_ready": True,
        "phase7_reasons": [],
        "promotion_readiness_ready": True,
        "requires_atomic_phase7_persistence": True,
        "phase7_promotion_persisted": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase7_promotion_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshly revalidate the exact Phase 7 completion evidence handoff, "
            "human promotion authorization, Phase 6 dependency, and Phase 7 "
            "anti-replay state before a separate atomic persistence step."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
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

    report = build_phase7_promotion_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
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
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
