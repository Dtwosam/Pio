from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_PROMOTION_REQUEST_V1"

HANDOFF_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_completion_evidence_handoff.py"
)
REVIEWED_SOURCE_BLOBS = {
    HANDOFF_TOOL: "3fdb4e289ff93fcd017f6a565cf16cfeebc2814b",
}

AUTHORIZATION_SCOPE = "PERSIST_EXACT_PHASE7_PROMOTION_EVIDENCE_ONLY"
EXCLUDED_SCOPES = (
    "NEW_LIVE_ENTRY",
    "CONTROLLED_LIVE_ACTIVATION",
    "LIVE_SUBMIT",
    "TRANSACTION_SIGNING",
    "TRANSACTION_SUBMISSION",
    "LIVE_CAPITAL",
    "AUTOMATIC_RESUBMISSION",
    "SERVICE_RESTART",
    "DETECTOR_CURSOR_MOVEMENT",
    "PRODUCTION_FILE_MUTATION",
    "PRODUCTION_GIT_MUTATION",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "excluded_scopes",
    "completion_handoff_sha256",
    "phase7_evidence_status_sha256",
    "phase7_evidence_plan_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "confirmed_receipts",
    "failed_receipts",
    "closed_positions",
    "open_positions",
    "distinct_closed_pools",
    "valued_closed_positions",
    "labeled_closed_positions",
    "ledger_clean",
    "phase7_promotion_ready",
    "phase7_reasons",
    "promotion_request_ready",
    "phase7_promotion_authorization_present",
    "phase7_promotion_persisted",
    "explicit_human_authorization_required",
    "fresh_phase7_promotion_recheck_required",
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
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_handoff_module(source: Path) -> Any:
    path = source / HANDOFF_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 7 completion handoff is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[HANDOFF_TOOL]:
        raise ValueError("reviewed Phase 7 completion handoff blob mismatch")
    return _load_module(path, "phase7_promotion_request_handoff")


def validate_phase7_promotion_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError("Phase 7 promotion request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("Phase 7 promotion request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 promotion request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 promotion request type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 promotion request lineage mismatch")

    for field in (
        "completion_handoff_sha256",
        "phase7_evidence_status_sha256",
        "phase7_evidence_plan_sha256",
        "pio_database_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(f"Phase 7 promotion request {field} is invalid")

    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("Phase 7 promotion request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError("Phase 7 promotion request excluded scopes mismatch")

    for field in (
        "production_repository",
        "pio_database_path",
    ):
        value = request.get(field)
        if not isinstance(value, str) or not value.startswith("/"):
            raise ValueError(f"Phase 7 promotion request {field} is invalid")

    for field in (
        "confirmed_receipts",
        "failed_receipts",
        "closed_positions",
        "open_positions",
        "distinct_closed_pools",
        "valued_closed_positions",
        "labeled_closed_positions",
    ):
        value = request.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 7 promotion request {field} is invalid")

    if request.get("ledger_clean") is not True:
        raise ValueError("Phase 7 promotion request requires a clean ledger")
    if request.get("phase7_promotion_ready") is not True:
        raise ValueError("Phase 7 promotion request requires ready evidence")
    if request.get("phase7_reasons") != []:
        raise ValueError("Phase 7 promotion request requires zero blocking reasons")
    if request.get("failed_receipts") != 0:
        raise ValueError("Phase 7 promotion request requires zero failed receipts")
    if request.get("open_positions") != 0:
        raise ValueError("Phase 7 promotion request requires zero open positions")
    if request["closed_positions"] <= 0:
        raise ValueError("Phase 7 promotion request requires closed positions")
    if request["valued_closed_positions"] != request["closed_positions"]:
        raise ValueError("Phase 7 promotion request requires full valuation coverage")
    if request["labeled_closed_positions"] != request["closed_positions"]:
        raise ValueError("Phase 7 promotion request requires full label coverage")

    for field in (
        "promotion_request_ready",
        "explicit_human_authorization_required",
        "fresh_phase7_promotion_recheck_required",
    ):
        if request.get(field) is not True:
            raise ValueError(f"Phase 7 promotion request requires {field}=true")

    for field in (
        "phase7_promotion_authorization_present",
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
    ):
        if request.get(field) is not False:
            raise ValueError(f"Phase 7 promotion request requires {field}=false")

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if request["request_sha256"] != expected:
        raise ValueError("Phase 7 promotion request digest mismatch")


def build_phase7_promotion_request(
    *,
    source_tree: str | Path,
    completion_handoff_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    handoff_module = _load_handoff_module(source)
    handoff = _load_json(
        completion_handoff_path,
        label="Phase 7 EXIT completion evidence handoff",
    )
    handoff_module.validate_exit_completion_evidence_handoff(handoff)

    if handoff.get("continuation_route") != handoff_module.ROUTE_PROMOTION:
        raise ValueError("Phase 7 completion handoff is not routed to promotion")
    if handoff.get("phase7_promotion_ready") is not True:
        raise ValueError("Phase 7 completion evidence is not promotion-ready")
    if handoff.get("phase7_reasons") != []:
        raise ValueError("Phase 7 completion evidence still has blockers")
    if handoff.get("ledger_clean") is not True:
        raise ValueError("Phase 7 completion evidence ledger is not clean")
    if handoff.get("failed_receipts") != 0:
        raise ValueError("Phase 7 completion evidence has failed receipts")
    if handoff.get("open_positions") != 0:
        raise ValueError("Phase 7 completion evidence has open positions")
    if handoff.get("valued_closed_positions") != handoff.get("closed_positions"):
        raise ValueError("Phase 7 completion evidence lacks valuation coverage")
    if handoff.get("labeled_closed_positions") != handoff.get("closed_positions"):
        raise ValueError("Phase 7 completion evidence lacks label coverage")

    for field in (
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "production_pio_database_modified",
    ):
        if handoff.get(field) is not False:
            raise ValueError(
                f"Phase 7 promotion request refuses handoff {field}=true"
            )

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
        "completion_handoff_sha256": handoff["handoff_sha256"],
        "phase7_evidence_status_sha256": handoff[
            "phase7_evidence_status_sha256"
        ],
        "phase7_evidence_plan_sha256": handoff[
            "phase7_evidence_plan_sha256"
        ],
        "production_repository": handoff["production_repository"],
        "pio_database_path": handoff["pio_database_path"],
        "pio_database_sha256": handoff[
            "fresh_status_database_sha256_after"
        ],
        "confirmed_receipts": handoff["confirmed_receipts"],
        "failed_receipts": handoff["failed_receipts"],
        "closed_positions": handoff["closed_positions"],
        "open_positions": handoff["open_positions"],
        "distinct_closed_pools": handoff["distinct_closed_pools"],
        "valued_closed_positions": handoff["valued_closed_positions"],
        "labeled_closed_positions": handoff["labeled_closed_positions"],
        "ledger_clean": handoff["ledger_clean"],
        "phase7_promotion_ready": True,
        "phase7_reasons": [],
        "promotion_request_ready": True,
        "phase7_promotion_authorization_present": False,
        "phase7_promotion_persisted": False,
        "explicit_human_authorization_required": True,
        "fresh_phase7_promotion_recheck_required": True,
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
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_phase7_promotion_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing Phase 7 promotion request from an exact "
            "EXIT-completion evidence handoff that is already promotion-ready. "
            "Explicit human authorization and a fresh evidence recheck remain "
            "required before any promotion evidence may be persisted."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--completion-handoff", required=True)
    args = parser.parse_args()

    report = build_phase7_promotion_request(
        source_tree=args.source_tree,
        completion_handoff_path=args.completion_handoff,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
