from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_EVIDENCE_COLLECTION_REQUEST_V1"

COLLECTION_PLAN_TOOL = Path(
    "deploy/tools/build_manual_market_paper_phase5_evidence_collection_plan.py"
)
REVIEWED_SOURCE_BLOBS = {
    COLLECTION_PLAN_TOOL: "bef3d59207630528becf362915e148f06b3cb03a",
}

AUTHORIZATION_SCOPE = "PHASE5_PAPER_EVIDENCE_COLLECTION_WINDOW_ONLY"

SCHEDULER_INTERVAL_SECONDS = 300
SCHEDULER_LEASE_SECONDS = 900
MIN_COLLECTION_SECONDS = 3600
MAX_COLLECTION_SECONDS = 72 * 3600

SAFE_SCHEDULER_EXTRA_ARGS = (
    "--max-positions",
    "3",
    "--refresh-jupiter-quotes",
)
SAFE_SCHEDULER_EXTRA_ARGS_TEXT = " ".join(SAFE_SCHEDULER_EXTRA_ARGS)

EXCLUDED_SCOPES = (
    "NEW_MARKET_ENTRY_CREATION",
    "PHASE5_PROMOTION_PERSISTENCE",
    "SERVICE_RESTART",
    "DETECTOR_CURSOR_MOVEMENT",
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
    "collection_plan_sha256",
    "account",
    "run_id",
    "authorization_scope",
    "excluded_scopes",
    "scheduler_service_blob",
    "scheduler_timer_blob",
    "scheduler_cli_blob",
    "scheduler_module_blob",
    "scheduler_interval_seconds",
    "scheduler_lease_seconds",
    "scheduler_extra_args",
    "scheduler_extra_args_text",
    "max_positions",
    "refresh_jupiter_quotes",
    "requested_collection_seconds",
    "requested_collection_hours",
    "nominal_timer_slots",
    "entry_diversity_evidence_needed",
    "requires_separate_manual_entry_authorization",
    "automatic_stop_required",
    "fresh_collection_readiness_recheck_required",
    "explicit_human_authorization_required",
    "collection_request_ready",
    "collection_authorization_present",
    "collection_execution_authorized",
    "new_market_entry_authorized",
    "phase5_promotion_persisted",
    "phase5_promotion_authorized",
    "recurring_paper_automation_authorized",
    "paper_timer_enable_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_paper_database_modified",
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


def _load_plan_module(source: Path) -> Any:
    path = source / COLLECTION_PLAN_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 5 evidence collection plan tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[COLLECTION_PLAN_TOOL]:
        raise ValueError("reviewed Phase 5 evidence collection plan blob mismatch")
    return _load_module(
        path,
        "manual_market_paper_phase5_collection_request_plan",
    )


def _requested_collection_seconds(plan: dict[str, Any]) -> int:
    raw_hours = plan.get("minimum_nominal_collection_hours")
    if (
        not isinstance(raw_hours, (int, float))
        or isinstance(raw_hours, bool)
        or not math.isfinite(float(raw_hours))
        or float(raw_hours) < 0
    ):
        raise ValueError("Phase 5 collection plan nominal hours are invalid")
    seconds = max(
        MIN_COLLECTION_SECONDS,
        math.ceil(float(raw_hours) * 3600.0),
    )
    if seconds > MAX_COLLECTION_SECONDS:
        raise ValueError("Phase 5 collection request exceeds 72-hour maximum")
    return seconds


def validate_phase5_evidence_collection_request(
    request: dict[str, Any],
) -> None:
    if not isinstance(request, dict):
        raise ValueError("Phase 5 evidence collection request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("Phase 5 evidence collection request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 5 evidence collection request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 5 evidence collection request type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 5 collection-request lineage mismatch")

    for field in ("collection_plan_sha256", "request_sha256"):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(f"Phase 5 collection-request {field} invalid")
    for field in (
        "scheduler_service_blob",
        "scheduler_timer_blob",
        "scheduler_cli_blob",
        "scheduler_module_blob",
    ):
        if not _is_hex_digest(request.get(field), 40):
            raise ValueError(f"Phase 5 collection-request {field} invalid")

    for field in ("account", "run_id"):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(f"Phase 5 collection-request {field} invalid")

    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("Phase 5 collection-request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError("Phase 5 collection-request excluded scopes mismatch")

    if request.get("scheduler_interval_seconds") != SCHEDULER_INTERVAL_SECONDS:
        raise ValueError("Phase 5 collection-request interval mismatch")
    if request.get("scheduler_lease_seconds") != SCHEDULER_LEASE_SECONDS:
        raise ValueError("Phase 5 collection-request lease mismatch")
    if request.get("scheduler_extra_args") != list(SAFE_SCHEDULER_EXTRA_ARGS):
        raise ValueError("Phase 5 collection-request scheduler args mismatch")
    if request.get("scheduler_extra_args_text") != SAFE_SCHEDULER_EXTRA_ARGS_TEXT:
        raise ValueError("Phase 5 collection-request scheduler args text mismatch")
    if request.get("max_positions") != 3:
        raise ValueError("Phase 5 collection-request max_positions mismatch")
    if request.get("refresh_jupiter_quotes") is not True:
        raise ValueError("Phase 5 collection-request must refresh Jupiter quotes")

    seconds = request.get("requested_collection_seconds")
    if (
        not isinstance(seconds, int)
        or isinstance(seconds, bool)
        or seconds < MIN_COLLECTION_SECONDS
        or seconds > MAX_COLLECTION_SECONDS
    ):
        raise ValueError("Phase 5 collection-request duration invalid")
    expected_hours = seconds / 3600.0
    if request.get("requested_collection_hours") != expected_hours:
        raise ValueError("Phase 5 collection-request hour conversion mismatch")
    expected_slots = math.ceil(seconds / SCHEDULER_INTERVAL_SECONDS)
    if request.get("nominal_timer_slots") != expected_slots:
        raise ValueError("Phase 5 collection-request timer-slot mismatch")

    for field in (
        "entry_diversity_evidence_needed",
        "requires_separate_manual_entry_authorization",
        "automatic_stop_required",
        "fresh_collection_readiness_recheck_required",
        "explicit_human_authorization_required",
        "collection_request_ready",
    ):
        if not isinstance(request.get(field), bool):
            raise ValueError(f"Phase 5 collection-request {field} must be boolean")
    for field in (
        "requires_separate_manual_entry_authorization",
        "automatic_stop_required",
        "fresh_collection_readiness_recheck_required",
        "explicit_human_authorization_required",
        "collection_request_ready",
    ):
        if request.get(field) is not True:
            raise ValueError(f"Phase 5 collection-request requires {field}=true")

    for field in (
        "collection_authorization_present",
        "collection_execution_authorized",
        "new_market_entry_authorized",
        "phase5_promotion_persisted",
        "phase5_promotion_authorized",
        "recurring_paper_automation_authorized",
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_paper_database_modified",
    ):
        if request.get(field) is not False:
            raise ValueError(f"Phase 5 collection-request requires {field}=false")

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if request["request_sha256"] != expected:
        raise ValueError("Phase 5 evidence collection request digest mismatch")


def build_phase5_evidence_collection_request(
    *,
    source_tree: str | Path,
    collection_plan_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    plan_module = _load_plan_module(source)
    plan = _load_json(
        collection_plan_path,
        label="Phase 5 evidence collection plan",
    )
    plan_module.validate_phase5_evidence_collection_plan(plan)

    if plan.get("collection_plan_ready") is not True:
        raise ValueError("Phase 5 evidence collection plan is not ready")
    if plan.get("phase5_promotion_ready") is True:
        raise ValueError("Phase 5 promotion is already ready; collection is unnecessary")
    if plan.get("recurring_scheduler_evidence_needed") is not True:
        raise ValueError("Phase 5 recurring scheduler evidence is not needed")
    if plan.get("phase3_dependency_blocked") is not False:
        raise ValueError("Phase 5 collection is blocked on persistent Phase 3 promotion")
    if plan.get("historical_failure_streak_blocked") is not False:
        raise ValueError(
            "Phase 5 collection cannot repair the historical failure-streak blocker"
        )
    if plan.get("ledger_passing") is not True:
        raise ValueError("Phase 5 collection requires a passing PAPER ledger")

    seconds = _requested_collection_seconds(plan)

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
        "collection_plan_sha256": plan["collection_plan_sha256"],
        "account": plan["account"],
        "run_id": plan["run_id"],
        "authorization_scope": AUTHORIZATION_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "scheduler_service_blob": plan["scheduler_service_blob"],
        "scheduler_timer_blob": plan["scheduler_timer_blob"],
        "scheduler_cli_blob": plan["scheduler_cli_blob"],
        "scheduler_module_blob": plan["scheduler_module_blob"],
        "scheduler_interval_seconds": SCHEDULER_INTERVAL_SECONDS,
        "scheduler_lease_seconds": SCHEDULER_LEASE_SECONDS,
        "scheduler_extra_args": list(SAFE_SCHEDULER_EXTRA_ARGS),
        "scheduler_extra_args_text": SAFE_SCHEDULER_EXTRA_ARGS_TEXT,
        "max_positions": 3,
        "refresh_jupiter_quotes": True,
        "requested_collection_seconds": seconds,
        "requested_collection_hours": seconds / 3600.0,
        "nominal_timer_slots": math.ceil(
            seconds / SCHEDULER_INTERVAL_SECONDS
        ),
        "entry_diversity_evidence_needed": bool(
            plan["entry_diversity_evidence_needed"]
        ),
        "requires_separate_manual_entry_authorization": True,
        "automatic_stop_required": True,
        "fresh_collection_readiness_recheck_required": True,
        "explicit_human_authorization_required": True,
        "collection_request_ready": True,
        "collection_authorization_present": False,
        "collection_execution_authorized": False,
        "new_market_entry_authorized": False,
        "phase5_promotion_persisted": False,
        "phase5_promotion_authorized": False,
        "recurring_paper_automation_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_phase5_evidence_collection_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request for one bounded Phase 5 PAPER "
            "evidence-collection window. The request binds the reviewed "
            "five-minute scheduler, caps management at three PAPER positions, "
            "requires quote refresh, and requires a future automatic stop. "
            "It does not enable the timer, execute PAPER, persist promotion, "
            "create entries, or authorize live capital."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--collection-plan", required=True)
    args = parser.parse_args()

    request = build_phase5_evidence_collection_request(
        source_tree=args.source_tree,
        collection_plan_path=args.collection_plan,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
