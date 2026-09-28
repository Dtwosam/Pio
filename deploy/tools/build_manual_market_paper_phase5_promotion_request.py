from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_PROMOTION_REQUEST_V1"

POST_COLLECTION_AUDIT_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_post_collection.py"
)
PHASE5_STATUS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_status.py"
)

REVIEWED_SOURCE_BLOBS = {
    POST_COLLECTION_AUDIT_TOOL: "6c9f1f8fa3aa12ee4deed617637dcf6b0e569613",
    PHASE5_STATUS_TOOL: "f3b90f3100c23f8454172f3bb97482e31df5921d",
}

AUTHORIZATION_SCOPE = "PERSIST_EXACT_PHASE5_PROMOTION_EVIDENCE_ONLY"
EXCLUDED_SCOPES = (
    "PAPER_TIMER_ENABLE",
    "PAPER_COLLECTION_START",
    "SERVICE_RESTART",
    "DETECTOR_CURSOR_MOVEMENT",
    "NEW_MARKET_ENTRY_CREATION",
    "TRANSACTION_SIGNING",
    "TRANSACTION_SUBMISSION",
    "LIVE_CAPITAL",
    "PRODUCTION_FILE_MUTATION",
    "PRODUCTION_GIT_MUTATION",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "excluded_scopes",
    "post_collection_audit_sha256",
    "phase5_evidence_status_sha256",
    "production_repository",
    "account",
    "run_id",
    "phase5_criteria_sha256",
    "endurance_sha256",
    "ledger_audit_sha256",
    "closed_positions",
    "distinct_valued_pools",
    "phase3_promoted",
    "phase5_promotion_ready",
    "phase5_reasons",
    "promotion_request_ready",
    "phase5_promotion_authorization_present",
    "phase5_promotion_persisted",
    "explicit_human_authorization_required",
    "fresh_phase5_promotion_recheck_required",
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
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 5 promotion-request dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 5 promotion-request dependency mismatch: {relative}"
            )
    audit = _load_module(
        source / POST_COLLECTION_AUDIT_TOOL,
        "manual_market_paper_phase5_promotion_request_audit",
    )
    status = _load_module(
        source / PHASE5_STATUS_TOOL,
        "manual_market_paper_phase5_promotion_request_status",
    )
    return audit, status


def validate_phase5_promotion_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError("Phase 5 promotion request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("Phase 5 promotion request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 5 promotion request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 5 promotion request type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 5 promotion request lineage mismatch")

    for field in (
        "post_collection_audit_sha256",
        "phase5_evidence_status_sha256",
        "phase5_criteria_sha256",
        "endurance_sha256",
        "ledger_audit_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(f"Phase 5 promotion request {field} is invalid")

    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("Phase 5 promotion request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError("Phase 5 promotion request excluded scopes mismatch")

    repository = request.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("Phase 5 promotion request repository is invalid")
    for field in ("account", "run_id"):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(f"Phase 5 promotion request {field} is invalid")

    for field in ("closed_positions", "distinct_valued_pools"):
        value = request.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 5 promotion request {field} is invalid")

    if request.get("phase3_promoted") is not True:
        raise ValueError("Phase 5 promotion request requires Phase 3 promotion")
    if request.get("phase5_promotion_ready") is not True:
        raise ValueError("Phase 5 promotion request requires ready evidence")
    reasons = request.get("phase5_reasons")
    if not isinstance(reasons, list) or reasons:
        raise ValueError("Phase 5 promotion request requires no blocking reasons")

    for field in (
        "promotion_request_ready",
        "explicit_human_authorization_required",
        "fresh_phase5_promotion_recheck_required",
    ):
        if request.get(field) is not True:
            raise ValueError(f"Phase 5 promotion request requires {field}=true")

    for field in (
        "phase5_promotion_authorization_present",
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
    ):
        if request.get(field) is not False:
            raise ValueError(f"Phase 5 promotion request requires {field}=false")

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if request["request_sha256"] != expected:
        raise ValueError("Phase 5 promotion request digest mismatch")


def build_phase5_promotion_request(
    *,
    source_tree: str | Path,
    post_collection_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    audit_module, status_module = _load_reviewed_modules(source)
    audit = _load_json(
        post_collection_audit_path,
        label="Phase 5 post-collection audit",
    )
    audit_module.validate_post_collection_audit(audit)

    if audit.get("post_collection_audit_ready") is not True:
        raise ValueError("Phase 5 post-collection audit is not ready")
    if audit.get("phase5_promotion_ready") is not True:
        raise ValueError("Phase 5 evidence is not promotion-ready")
    if audit.get("requires_new_collection_plan") is not False:
        raise ValueError("Phase 5 promotion request requires collection to be complete")
    if audit.get("requires_additional_paper_evidence") is not False:
        raise ValueError("Phase 5 promotion request requires complete PAPER evidence")
    if audit.get("phase5_reasons") != []:
        raise ValueError("Phase 5 promotion request requires zero blocking reasons")
    if audit.get("phase5_promotion_persisted") is not False:
        raise ValueError("Phase 5 promotion is already persisted")
    if audit.get("phase5_promotion_authorized") is not False:
        raise ValueError("post-collection audit unexpectedly authorizes promotion")

    status = audit.get("fresh_phase5_evidence_status")
    if not isinstance(status, dict):
        raise ValueError("Phase 5 promotion request fresh status is missing")
    status_module.validate_phase5_evidence_status(status)

    if status.get("phase5_evidence_status_sha256") != audit.get(
        "fresh_phase5_evidence_status_sha256"
    ):
        raise ValueError("Phase 5 promotion request fresh status digest mismatch")
    if status.get("phase5_promotion_ready") is not True:
        raise ValueError("Phase 5 fresh status is not promotion-ready")
    if status.get("requires_additional_paper_evidence") is not False:
        raise ValueError("Phase 5 fresh status still requires evidence")
    if status.get("phase5_reasons") != []:
        raise ValueError("Phase 5 fresh status still has blocking reasons")
    if status.get("phase3_promoted") is not True:
        raise ValueError("Phase 5 promotion requires persistent Phase 3 promotion")

    for field in (
        "phase5_promotion_persisted",
        "phase5_promotion_authorized",
        "recurring_paper_automation_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if status.get(field) is not False:
            raise ValueError(f"Phase 5 fresh status unexpectedly authorizes {field}")

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
        "authorization_scope": AUTHORIZATION_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "post_collection_audit_sha256": audit[
            "post_collection_audit_sha256"
        ],
        "phase5_evidence_status_sha256": status[
            "phase5_evidence_status_sha256"
        ],
        "production_repository": audit["production_repository"],
        "account": status["account"],
        "run_id": status["run_id"],
        "phase5_criteria_sha256": status["phase5_criteria_sha256"],
        "endurance_sha256": status["endurance_sha256"],
        "ledger_audit_sha256": status["ledger_audit_sha256"],
        "closed_positions": status["closed_positions"],
        "distinct_valued_pools": status["distinct_valued_pools"],
        "phase3_promoted": True,
        "phase5_promotion_ready": True,
        "phase5_reasons": [],
        "promotion_request_ready": True,
        "phase5_promotion_authorization_present": False,
        "phase5_promotion_persisted": False,
        "explicit_human_authorization_required": True,
        "fresh_phase5_promotion_recheck_required": True,
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
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_phase5_promotion_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request to persist one exact ready Phase 5 "
            "promotion evidence record. The request is accepted only from a "
            "ready post-collection audit whose embedded Phase 5 status validates "
            "as promotion-ready with no blockers. It does not access production, "
            "persist promotion, start PAPER, enable timers, sign/submit "
            "transactions, or authorize live capital."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-collection-audit", required=True)
    args = parser.parse_args()

    request = build_phase5_promotion_request(
        source_tree=args.source_tree,
        post_collection_audit_path=args.post_collection_audit,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
