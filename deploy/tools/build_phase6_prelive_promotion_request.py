from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE6_PRELIVE_PROMOTION_REQUEST_V1"

PHASE6_STATUS_TOOL = Path("deploy/tools/check_phase6_prelive_evidence_status.py")
REVIEWED_SOURCE_BLOBS = {
    PHASE6_STATUS_TOOL: "306497b937778cb8b77462f1457937f3499ca6ea",
}

AUTHORIZATION_SCOPE = "PERSIST_EXACT_PHASE6_PROMOTION_EVIDENCE_ONLY"
EXCLUDED_SCOPES = (
    "CONTROLLED_LIVE_ACTIVATION",
    "LIVE_SUBMIT",
    "TRANSACTION_SIGNING",
    "TRANSACTION_SUBMISSION",
    "LIVE_CAPITAL",
    "SERVICE_RESTART",
    "DETECTOR_CURSOR_MOVEMENT",
    "PAPER_TIMER_ENABLE",
    "PRODUCTION_FILE_MUTATION",
    "PRODUCTION_GIT_MUTATION",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "excluded_scopes",
    "phase6_evidence_status_sha256",
    "phase5_post_promotion_audit_sha256",
    "production_repository",
    "pio_database_path",
    "execution_database_path",
    "pio_database_sha256",
    "execution_database_sha256",
    "phase6_criteria_sha256",
    "phase6_report_sha256",
    "passed_enter_intents",
    "distinct_pools",
    "blocked_intents",
    "postsimulation_intents",
    "invalid_passed_intents",
    "distinct_authorized_wallets",
    "phase5_promoted",
    "phase6_promotion_ready",
    "phase6_reasons",
    "promotion_request_ready",
    "phase6_promotion_authorization_present",
    "phase6_promotion_persisted",
    "explicit_human_authorization_required",
    "fresh_phase6_promotion_recheck_required",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "paper_timer_enable_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
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


def _load_status_module(source: Path) -> Any:
    path = source / PHASE6_STATUS_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 6 evidence-status tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[PHASE6_STATUS_TOOL]:
        raise ValueError("reviewed Phase 6 evidence-status tool blob mismatch")
    return _load_module(path, "phase6_promotion_request_status")


def validate_phase6_promotion_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError("Phase 6 promotion request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("Phase 6 promotion request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 6 promotion request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 6 promotion request type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 6 promotion request lineage mismatch")

    for field in (
        "phase6_evidence_status_sha256",
        "phase5_post_promotion_audit_sha256",
        "pio_database_sha256",
        "execution_database_sha256",
        "phase6_criteria_sha256",
        "phase6_report_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(f"Phase 6 promotion request {field} is invalid")

    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("Phase 6 promotion request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError("Phase 6 promotion request excluded scopes mismatch")

    for field in (
        "production_repository",
        "pio_database_path",
        "execution_database_path",
    ):
        value = request.get(field)
        if not isinstance(value, str) or not value.startswith("/"):
            raise ValueError(f"Phase 6 promotion request {field} is invalid")

    for field in (
        "passed_enter_intents",
        "distinct_pools",
        "blocked_intents",
        "postsimulation_intents",
        "invalid_passed_intents",
    ):
        value = request.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 6 promotion request {field} is invalid")

    wallets = request.get("distinct_authorized_wallets")
    if (
        not isinstance(wallets, list)
        or len(wallets) != 1
        or not isinstance(wallets[0], str)
        or not wallets[0]
    ):
        raise ValueError("Phase 6 promotion request requires exactly one wallet")

    if request.get("phase5_promoted") is not True:
        raise ValueError("Phase 6 promotion request requires Phase 5 promotion")
    if request.get("phase6_promotion_ready") is not True:
        raise ValueError("Phase 6 promotion request requires ready evidence")
    if request.get("phase6_reasons") != []:
        raise ValueError("Phase 6 promotion request requires zero blocking reasons")
    if request.get("postsimulation_intents") != 0:
        raise ValueError("Phase 6 promotion request forbids post-simulation intents")
    if request.get("invalid_passed_intents") != 0:
        raise ValueError("Phase 6 promotion request forbids invalid passed intents")

    for field in (
        "promotion_request_ready",
        "explicit_human_authorization_required",
        "fresh_phase6_promotion_recheck_required",
    ):
        if request.get(field) is not True:
            raise ValueError(f"Phase 6 promotion request requires {field}=true")

    for field in (
        "phase6_promotion_authorization_present",
        "phase6_promotion_persisted",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
    ):
        if request.get(field) is not False:
            raise ValueError(f"Phase 6 promotion request requires {field}=false")

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if request["request_sha256"] != expected:
        raise ValueError("Phase 6 promotion request digest mismatch")


def build_phase6_promotion_request(
    *,
    source_tree: str | Path,
    phase6_evidence_status_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    status_module = _load_status_module(source)
    status = _load_json(
        phase6_evidence_status_path,
        label="Phase 6 evidence status",
    )
    status_module.validate_phase6_evidence_status(status)

    if status.get("phase6_evidence_status_ready") is not True:
        raise ValueError("Phase 6 evidence status is not ready")
    if status.get("phase6_promotion_ready") is not True:
        raise ValueError("Phase 6 evidence is not promotion-ready")
    if status.get("requires_additional_phase6_evidence") is not False:
        raise ValueError("Phase 6 evidence is incomplete")
    if status.get("phase6_reasons") != []:
        raise ValueError("Phase 6 evidence still has blockers")
    if status.get("phase5_promoted") is not True:
        raise ValueError("Phase 6 evidence lost Phase 5 promotion")
    if status.get("postsimulation_intents") != 0:
        raise ValueError("Phase 6 evidence contains post-simulation intents")
    if status.get("invalid_passed_intents") != 0:
        raise ValueError("Phase 6 evidence contains invalid passed intents")
    wallets = status.get("distinct_authorized_wallets")
    if not isinstance(wallets, list) or len(wallets) != 1:
        raise ValueError("Phase 6 evidence must use exactly one executor wallet")

    for field in (
        "phase6_promotion_persisted",
        "phase6_promotion_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
    ):
        if status.get(field) is not False:
            raise ValueError(f"Phase 6 evidence unexpectedly authorizes {field}")

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
        "phase6_evidence_status_sha256": status[
            "phase6_evidence_status_sha256"
        ],
        "phase5_post_promotion_audit_sha256": status[
            "phase5_post_promotion_audit_sha256"
        ],
        "production_repository": status["production_repository"],
        "pio_database_path": status["pio_database_path"],
        "execution_database_path": status["execution_database_path"],
        "pio_database_sha256": status["pio_database_sha256_after"],
        "execution_database_sha256": status[
            "execution_database_sha256_after"
        ],
        "phase6_criteria_sha256": status["phase6_criteria_sha256"],
        "phase6_report_sha256": status["phase6_report_sha256"],
        "passed_enter_intents": status["passed_enter_intents"],
        "distinct_pools": status["distinct_pools"],
        "blocked_intents": status["blocked_intents"],
        "postsimulation_intents": status["postsimulation_intents"],
        "invalid_passed_intents": status["invalid_passed_intents"],
        "distinct_authorized_wallets": status["distinct_authorized_wallets"],
        "phase5_promoted": True,
        "phase6_promotion_ready": True,
        "phase6_reasons": [],
        "promotion_request_ready": True,
        "phase6_promotion_authorization_present": False,
        "phase6_promotion_persisted": False,
        "explicit_human_authorization_required": True,
        "fresh_phase6_promotion_recheck_required": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_phase6_promotion_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request for one exact ready Phase 6 "
            "pre-live promotion corpus. The request binds the current Pio and "
            "execution database digests and complete reviewed corpus metrics, "
            "but does not persist Phase 6 promotion or authorize controlled "
            "LIVE, signing/submission, or live capital."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase6-evidence-status", required=True)
    args = parser.parse_args()

    request = build_phase6_promotion_request(
        source_tree=args.source_tree,
        phase6_evidence_status_path=args.phase6_evidence_status,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
