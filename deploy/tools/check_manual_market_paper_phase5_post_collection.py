from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_POST_COLLECTION_AUDIT_V1"

STARTER_TOOL = Path(
    "deploy/tools/start_manual_market_paper_phase5_evidence_collection.py"
)
READINESS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_collection_readiness.py"
)
PHASE5_STATUS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_status.py"
)

REVIEWED_SOURCE_BLOBS = {
    STARTER_TOOL: "0b2f6eff265e61af2ef49daf58feab702232536d",
    READINESS_TOOL: "6c72b5d63cf92eb48048f16e68f9258fa3be13dc",
    PHASE5_STATUS_TOOL: "f3b90f3100c23f8454172f3bb97482e31df5921d",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "activation_receipt_sha256",
    "collection_readiness_sha256",
    "pre_collection_phase5_evidence_status_sha256",
    "fresh_phase5_evidence_status_sha256",
    "production_repository",
    "account",
    "run_id",
    "target_timer_unit",
    "target_service_unit",
    "requested_collection_seconds",
    "collection_started_at",
    "collection_deadline",
    "audit_checked_at",
    "deadline_reached",
    "service_unit",
    "timer_unit",
    "service_inactive",
    "timer_inactive",
    "timer_disabled",
    "units_match_reviewed",
    "no_unit_drop_ins",
    "bounded_window_ended",
    "fresh_phase5_evidence_status",
    "evidence_advanced",
    "phase5_promotion_ready",
    "phase5_reasons",
    "requires_additional_paper_evidence",
    "post_collection_audit_ready",
    "requires_new_collection_plan",
    "requires_separate_phase5_promotion_action",
    "phase5_promotion_persisted",
    "phase5_promotion_authorized",
    "persistent_recurring_paper_automation_authorized",
    "paper_timer_enable_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_paper_database_modified_by_audit",
)

ADVANCEMENT_FIELDS = (
    ("endurance", "runtime_hours"),
    ("endurance", "terminal_ticks"),
    ("endurance", "applied_chain_valuations"),
    ("endurance", "distinct_positions_valued"),
    (None, "closed_positions"),
    (None, "distinct_valued_pools"),
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
            raise ValueError(f"post-collection audit dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"post-collection audit dependency mismatch: {relative}")

    starter = _load_module(
        source / STARTER_TOOL,
        "manual_market_paper_phase5_post_collection_starter",
    )
    readiness = _load_module(
        source / READINESS_TOOL,
        "manual_market_paper_phase5_post_collection_readiness",
    )
    status = _load_module(
        source / PHASE5_STATUS_TOOL,
        "manual_market_paper_phase5_post_collection_status",
    )
    return starter, readiness, status


def _canonical_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError("post-collection timestamp must use canonical UTC Z form")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            "post-collection timestamp must use YYYY-MM-DDTHH:MM:SSZ"
        ) from exc


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: Any, *, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"post-collection {label} is invalid")
    return float(value)


def _evidence_advanced(
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    for parent, field in ADVANCEMENT_FIELDS:
        before_value = before[field] if parent is None else before[parent][field]
        after_value = after[field] if parent is None else after[parent][field]
        if _number(after_value, label=field) > _number(before_value, label=field):
            return True
    return len(after.get("phase5_reasons", [])) < len(
        before.get("phase5_reasons", [])
    )


def validate_post_collection_audit(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("post-collection audit must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"post_collection_audit_sha256"}:
        raise ValueError("post-collection audit schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported post-collection audit format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected post-collection audit type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("post-collection audit lineage mismatch")

    for field in (
        "activation_receipt_sha256",
        "collection_readiness_sha256",
        "pre_collection_phase5_evidence_status_sha256",
        "fresh_phase5_evidence_status_sha256",
        "post_collection_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"post-collection audit {field} is invalid")

    for field in (
        "production_repository",
        "account",
        "run_id",
        "target_timer_unit",
        "target_service_unit",
        "collection_started_at",
        "collection_deadline",
        "audit_checked_at",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"post-collection audit {field} is invalid")

    if not report["production_repository"].startswith("/"):
        raise ValueError("post-collection production repository must be absolute")
    account = report["account"]
    if report["target_timer_unit"] != f"pio-paper@{account}.timer":
        raise ValueError("post-collection timer binding mismatch")
    if report["target_service_unit"] != f"pio-paper@{account}.service":
        raise ValueError("post-collection service binding mismatch")

    seconds = report.get("requested_collection_seconds")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        raise ValueError("post-collection requested duration is invalid")

    started = _canonical_utc(report["collection_started_at"])
    deadline = _canonical_utc(report["collection_deadline"])
    checked = _canonical_utc(report["audit_checked_at"])
    if (deadline - started).total_seconds() != seconds:
        raise ValueError("post-collection deadline binding mismatch")
    if checked < deadline:
        raise ValueError("post-collection audit ran before deadline")

    for field in (
        "deadline_reached",
        "service_inactive",
        "timer_inactive",
        "timer_disabled",
        "units_match_reviewed",
        "no_unit_drop_ins",
        "bounded_window_ended",
        "post_collection_audit_ready",
        "requires_separate_phase5_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"post-collection audit requires {field}=true")

    for unit_field in ("service_unit", "timer_unit"):
        value = report.get(unit_field)
        if not isinstance(value, dict):
            raise ValueError(f"post-collection {unit_field} is invalid")
        for flag in ("fragment_matches_reviewed", "no_drop_ins"):
            if value.get(flag) is not True:
                raise ValueError(
                    f"post-collection {unit_field} requires {flag}=true"
                )

    if report["service_unit"].get("active_state") != "inactive":
        raise ValueError("post-collection PAPER service is not inactive")
    if report["timer_unit"].get("active_state") != "inactive":
        raise ValueError("post-collection PAPER timer is not inactive")
    if report["timer_unit"].get("unit_file_state") != "disabled":
        raise ValueError("post-collection PAPER timer is not disabled")

    fresh = report.get("fresh_phase5_evidence_status")
    if not isinstance(fresh, dict):
        raise ValueError("post-collection fresh Phase 5 status is invalid")
    if fresh.get("phase5_evidence_status_sha256") != report[
        "fresh_phase5_evidence_status_sha256"
    ]:
        raise ValueError("post-collection fresh status digest mismatch")
    if fresh.get("account") != report["account"]:
        raise ValueError("post-collection fresh status account mismatch")
    if fresh.get("run_id") != report["run_id"]:
        raise ValueError("post-collection fresh status run-id mismatch")

    if report.get("phase5_promotion_ready") is not fresh.get(
        "phase5_promotion_ready"
    ):
        raise ValueError("post-collection promotion-ready mismatch")
    if report.get("phase5_reasons") != fresh.get("phase5_reasons"):
        raise ValueError("post-collection Phase 5 reasons mismatch")
    if report.get("requires_additional_paper_evidence") is not fresh.get(
        "requires_additional_paper_evidence"
    ):
        raise ValueError("post-collection additional-evidence mismatch")
    if report.get("requires_new_collection_plan") is not (
        not bool(report["phase5_promotion_ready"])
    ):
        raise ValueError("post-collection collection-plan requirement mismatch")

    if not isinstance(report.get("evidence_advanced"), bool):
        raise ValueError("post-collection evidence_advanced must be boolean")
    if not isinstance(report.get("phase5_promotion_ready"), bool):
        raise ValueError("post-collection promotion-ready must be boolean")
    reasons = report.get("phase5_reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("post-collection Phase 5 reasons are invalid")

    for field in (
        "phase5_promotion_persisted",
        "phase5_promotion_authorized",
        "persistent_recurring_paper_automation_authorized",
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_paper_database_modified_by_audit",
    ):
        if report.get(field) is not False:
            raise ValueError(f"post-collection audit requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["post_collection_audit_sha256"] != expected:
        raise ValueError("post-collection audit digest mismatch")


def build_post_collection_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    post_cycle_audit_path: str | Path,
    pre_collection_phase5_evidence_status_path: str | Path,
    activation_receipt_path: str | Path,
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

    starter, readiness, status_module = _load_reviewed_modules(source)

    receipt = _load_json(
        activation_receipt_path,
        label="Phase 5 evidence collection activation receipt",
    )
    pre_status = _load_json(
        pre_collection_phase5_evidence_status_path,
        label="pre-collection Phase 5 evidence status",
    )
    starter.validate_activation_receipt(receipt)
    status_module.validate_phase5_evidence_status(pre_status)

    if receipt.get("bounded_collection_started") is not True:
        raise ValueError("Phase 5 collection receipt is not started")
    if receipt.get("requires_post_collection_audit") is not True:
        raise ValueError("Phase 5 collection receipt does not require this audit")
    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError("post-collection production repository binding mismatch")
    if receipt.get("phase5_evidence_status_sha256") != pre_status.get(
        "phase5_evidence_status_sha256"
    ):
        raise ValueError("post-collection pre-status binding mismatch")
    if receipt.get("account") != pre_status.get("account"):
        raise ValueError("post-collection account binding mismatch")
    if receipt.get("run_id") != pre_status.get("run_id"):
        raise ValueError("post-collection run-id binding mismatch")

    checked = (
        _canonical_utc(now)
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    deadline = _canonical_utc(receipt["collection_deadline"])
    if checked < deadline:
        raise ValueError("post-collection audit cannot run before collection deadline")

    account = str(receipt["account"])
    service = readiness._unit_snapshot(
        source=source,
        unit=str(receipt["target_service_unit"]),
        reviewed_relative=readiness.PAPER_SERVICE_UNIT,
    )
    timer = readiness._unit_snapshot(
        source=source,
        unit=str(receipt["target_timer_unit"]),
        reviewed_relative=readiness.PAPER_TIMER_UNIT,
    )
    readiness._validate_unit_snapshot(service)
    readiness._validate_unit_snapshot(timer)

    service_inactive = service["active_state"] == "inactive"
    timer_inactive = timer["active_state"] == "inactive"
    timer_disabled = timer["unit_file_state"] == "disabled"
    units_match_reviewed = bool(
        service["fragment_matches_reviewed"]
        and timer["fragment_matches_reviewed"]
    )
    no_unit_drop_ins = bool(service["no_drop_ins"] and timer["no_drop_ins"])

    if not service_inactive:
        raise ValueError("post-collection PAPER service is still active")
    if not timer_inactive:
        raise ValueError("post-collection PAPER timer is still active")
    if not timer_disabled:
        raise ValueError("post-collection PAPER timer became persistent")
    if not units_match_reviewed:
        raise ValueError("post-collection PAPER unit bytes drifted")
    if not no_unit_drop_ins:
        raise ValueError("post-collection PAPER unit drop-ins detected")

    fresh = status_module.build_phase5_evidence_status(
        repository=production,
        source_tree=source,
        post_cycle_audit_path=post_cycle_audit_path,
    )
    status_module.validate_phase5_evidence_status(fresh)

    if fresh.get("account") != receipt["account"]:
        raise ValueError("fresh Phase 5 status account changed")
    if fresh.get("run_id") != receipt["run_id"]:
        raise ValueError("fresh Phase 5 status run-id changed")

    advanced = _evidence_advanced(pre_status, fresh)
    promotion_ready = bool(fresh["phase5_promotion_ready"])
    reasons = list(fresh["phase5_reasons"])

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
        "activation_receipt_sha256": receipt["receipt_sha256"],
        "collection_readiness_sha256": receipt[
            "collection_readiness_sha256"
        ],
        "pre_collection_phase5_evidence_status_sha256": pre_status[
            "phase5_evidence_status_sha256"
        ],
        "fresh_phase5_evidence_status_sha256": fresh[
            "phase5_evidence_status_sha256"
        ],
        "production_repository": str(production),
        "account": receipt["account"],
        "run_id": receipt["run_id"],
        "target_timer_unit": receipt["target_timer_unit"],
        "target_service_unit": receipt["target_service_unit"],
        "requested_collection_seconds": receipt["requested_collection_seconds"],
        "collection_started_at": receipt["started_at"],
        "collection_deadline": receipt["collection_deadline"],
        "audit_checked_at": _format_utc(checked),
        "deadline_reached": True,
        "service_unit": service,
        "timer_unit": timer,
        "service_inactive": True,
        "timer_inactive": True,
        "timer_disabled": True,
        "units_match_reviewed": True,
        "no_unit_drop_ins": True,
        "bounded_window_ended": True,
        "fresh_phase5_evidence_status": fresh,
        "evidence_advanced": advanced,
        "phase5_promotion_ready": promotion_ready,
        "phase5_reasons": reasons,
        "requires_additional_paper_evidence": bool(
            fresh["requires_additional_paper_evidence"]
        ),
        "post_collection_audit_ready": True,
        "requires_new_collection_plan": not promotion_ready,
        "requires_separate_phase5_promotion_action": True,
        "phase5_promotion_persisted": False,
        "phase5_promotion_authorized": False,
        "persistent_recurring_paper_automation_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified_by_audit": False,
    }
    report = {
        **identity,
        "post_collection_audit_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_post_collection_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit a completed bounded Phase 5 PAPER evidence-collection "
            "window. The audit requires the deadline to have passed, proves the "
            "PAPER service/timer are inactive with the timer still disabled and "
            "reviewed unit bytes unchanged, then rebuilds Phase 5 evidence from "
            "the snapshot-only evaluator. It never starts/stops/enables units, "
            "persists promotion, creates entries, signs/submits transactions, "
            "or uses live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-cycle-audit", required=True)
    parser.add_argument("--pre-collection-phase5-evidence-status", required=True)
    parser.add_argument("--activation-receipt", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_post_collection_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        post_cycle_audit_path=args.post_cycle_audit,
        pre_collection_phase5_evidence_status_path=(
            args.pre_collection_phase5_evidence_status
        ),
        activation_receipt_path=args.activation_receipt,
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
