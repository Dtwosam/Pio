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
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_EVIDENCE_COLLECTION_POST_AUDIT_V1"

ACTIVATION_TOOL = Path(
    "deploy/tools/start_manual_market_paper_phase5_evidence_collection.py"
)
READINESS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_collection_readiness.py"
)
PHASE5_STATUS_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_evidence_status.py"
)

REVIEWED_SOURCE_BLOBS = {
    ACTIVATION_TOOL: "e1a13419b9f0a27dcf0c0d016cf8c06d2cfb7547",
    READINESS_TOOL: "6c72b5d63cf92eb48048f16e68f9258fa3be13dc",
    PHASE5_STATUS_TOOL: "f3b90f3100c23f8454172f3bb97482e31df5921d",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "activation_receipt_sha256",
    "collection_readiness_sha256",
    "phase5_evidence_status_sha256",
    "account",
    "run_id",
    "collection_deadline",
    "audited_at",
    "collection_deadline_passed",
    "environment_file_sha256",
    "environment_scheduler_extra_args",
    "environment_matches_start_readiness",
    "service_unit",
    "timer_unit",
    "service_fragment_unchanged",
    "timer_fragment_unchanged",
    "no_unit_drop_ins",
    "service_quiescent",
    "timer_inactive",
    "timer_disabled",
    "collection_window_closed",
    "phase5_promotion_ready",
    "phase5_reasons",
    "endurance",
    "endurance_sha256",
    "ledger_audit",
    "ledger_audit_sha256",
    "closed_positions",
    "distinct_valued_pools",
    "post_collection_audit_ready",
    "requires_additional_paper_evidence",
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
    return (
        _load_module(
            source / ACTIVATION_TOOL,
            "phase5_post_collection_activation",
        ),
        _load_module(
            source / READINESS_TOOL,
            "phase5_post_collection_readiness",
        ),
        _load_module(
            source / PHASE5_STATUS_TOOL,
            "phase5_post_collection_status",
        ),
    )


def _parse_utc(raw: Any, *, label: str) -> datetime:
    if not isinstance(raw, str):
        raise ValueError(f"{label} must be a string")
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DDTHH:MM:SSZ") from exc
    return parsed


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        raise ValueError("post-collection audit source lineage mismatch")

    for field in (
        "activation_receipt_sha256",
        "collection_readiness_sha256",
        "phase5_evidence_status_sha256",
        "environment_file_sha256",
        "endurance_sha256",
        "ledger_audit_sha256",
        "post_collection_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"post-collection audit {field} invalid")

    for field in (
        "account",
        "run_id",
        "collection_deadline",
        "audited_at",
        "environment_scheduler_extra_args",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"post-collection audit {field} invalid")

    deadline = _parse_utc(report["collection_deadline"], label="collection deadline")
    audited = _parse_utc(report["audited_at"], label="audit time")
    if audited < deadline:
        raise ValueError("post-collection audit ran before collection deadline")

    for field in ("service_unit", "timer_unit"):
        value = report.get(field)
        if not isinstance(value, dict):
            raise ValueError(f"post-collection audit {field} invalid")

    reasons = report.get("phase5_reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("post-collection Phase 5 reasons invalid")
    if not isinstance(report.get("phase5_promotion_ready"), bool):
        raise ValueError("post-collection promotion-ready flag invalid")
    if report["phase5_promotion_ready"] is not (len(reasons) == 0):
        raise ValueError("post-collection promotion/reasons mismatch")

    endurance = report.get("endurance")
    ledger = report.get("ledger_audit")
    if not isinstance(endurance, dict) or not isinstance(ledger, dict):
        raise ValueError("post-collection evidence reports invalid")
    if report["endurance_sha256"] != _sha256_bytes(_canonical_bytes(endurance)):
        raise ValueError("post-collection endurance digest mismatch")
    if report["ledger_audit_sha256"] != _sha256_bytes(_canonical_bytes(ledger)):
        raise ValueError("post-collection ledger digest mismatch")

    for field in ("closed_positions", "distinct_valued_pools"):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"post-collection {field} invalid")

    for field in (
        "collection_deadline_passed",
        "environment_matches_start_readiness",
        "service_fragment_unchanged",
        "timer_fragment_unchanged",
        "no_unit_drop_ins",
        "service_quiescent",
        "timer_inactive",
        "timer_disabled",
        "collection_window_closed",
        "post_collection_audit_ready",
        "requires_separate_phase5_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"post-collection audit requires {field}=true")

    expected_additional = not report["phase5_promotion_ready"]
    if report.get("requires_additional_paper_evidence") is not expected_additional:
        raise ValueError("post-collection additional-evidence flag mismatch")

    if report["timer_unit"].get("active_state") != "inactive":
        raise ValueError("post-collection PAPER timer must be inactive")
    if report["timer_unit"].get("unit_file_state") != "disabled":
        raise ValueError("post-collection PAPER timer must remain disabled")
    if report["service_unit"].get("active_state") not in {"inactive", "failed"}:
        raise ValueError("post-collection PAPER service must be quiescent")

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
    collection_readiness_path: str | Path,
    activation_receipt_path: str | Path,
    now: datetime | None = None,
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

    activation_module, readiness_module, status_module = (
        _load_reviewed_modules(source)
    )
    readiness = _load_json(
        collection_readiness_path,
        label="Phase 5 collection readiness",
    )
    receipt = _load_json(
        activation_receipt_path,
        label="Phase 5 collection activation receipt",
    )
    readiness_module.validate_collection_readiness(readiness)
    activation_module.validate_activation_receipt(receipt)

    if receipt["collection_readiness_sha256"] != readiness[
        "collection_readiness_sha256"
    ]:
        raise ValueError("post-collection receipt/readiness binding mismatch")
    if receipt.get("requires_post_collection_audit") is not True:
        raise ValueError("collection activation receipt does not require audit")
    if receipt.get("paper_timer_enable_authorized") is not False:
        raise ValueError("collection receipt unexpectedly enabled persistence")
    if receipt.get("live_capital_authorized") is not False:
        raise ValueError("collection receipt unexpectedly authorizes live capital")

    audited_at = (
        now.astimezone(timezone.utc).replace(microsecond=0)
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    deadline = _parse_utc(
        receipt["collection_deadline"],
        label="collection deadline",
    )
    if audited_at < deadline:
        raise ValueError("collection deadline has not passed")

    environment_args, environment_sha256 = (
        readiness_module._parse_environment_scheduler_args(
            readiness_module.ENVIRONMENT_FILE
        )
    )
    if environment_sha256 != readiness["environment_file_sha256"]:
        raise ValueError("PAPER environment file changed during collection")
    if environment_args != readiness["environment_scheduler_extra_args"]:
        raise ValueError("PAPER scheduler args changed during collection")

    service = readiness_module._unit_snapshot(
        source=source,
        unit=readiness["service_unit"]["unit"],
        reviewed_relative=readiness_module.PAPER_SERVICE_UNIT,
    )
    timer = readiness_module._unit_snapshot(
        source=source,
        unit=readiness["timer_unit"]["unit"],
        reviewed_relative=readiness_module.PAPER_TIMER_UNIT,
    )

    service_fragment_unchanged = bool(
        service["fragment_sha256"]
        == readiness["service_unit"]["fragment_sha256"]
        and service["fragment_git_blob"]
        == readiness["service_unit"]["fragment_git_blob"]
    )
    timer_fragment_unchanged = bool(
        timer["fragment_sha256"]
        == readiness["timer_unit"]["fragment_sha256"]
        and timer["fragment_git_blob"]
        == readiness["timer_unit"]["fragment_git_blob"]
    )
    no_drop_ins = bool(service["no_drop_ins"] and timer["no_drop_ins"])
    service_quiescent = service["active_state"] in {"inactive", "failed"}
    timer_inactive = timer["active_state"] == "inactive"
    timer_disabled = timer["unit_file_state"] == "disabled"
    if not all(
        (
            service_fragment_unchanged,
            timer_fragment_unchanged,
            no_drop_ins,
            service_quiescent,
            timer_inactive,
            timer_disabled,
        )
    ):
        raise ValueError("post-collection systemd closure check failed")

    status = status_module.build_phase5_evidence_status(
        repository=production,
        source_tree=source,
        post_cycle_audit_path=post_cycle_audit_path,
    )
    status_module.validate_phase5_evidence_status(status)
    if status.get("phase5_evidence_status_ready") is not True:
        raise ValueError("fresh post-collection Phase 5 status is not ready")
    if status.get("account") != receipt["account"]:
        raise ValueError("post-collection Phase 5 account binding mismatch")

    promotion_ready = bool(status["phase5_promotion_ready"])
    reasons = list(status["phase5_reasons"])
    endurance = status["endurance"]
    ledger = status["ledger_audit"]

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
        "collection_readiness_sha256": readiness[
            "collection_readiness_sha256"
        ],
        "phase5_evidence_status_sha256": status[
            "phase5_evidence_status_sha256"
        ],
        "account": receipt["account"],
        "run_id": receipt["run_id"],
        "collection_deadline": receipt["collection_deadline"],
        "audited_at": _format_utc(audited_at),
        "collection_deadline_passed": True,
        "environment_file_sha256": environment_sha256,
        "environment_scheduler_extra_args": environment_args,
        "environment_matches_start_readiness": True,
        "service_unit": service,
        "timer_unit": timer,
        "service_fragment_unchanged": True,
        "timer_fragment_unchanged": True,
        "no_unit_drop_ins": True,
        "service_quiescent": True,
        "timer_inactive": True,
        "timer_disabled": True,
        "collection_window_closed": True,
        "phase5_promotion_ready": promotion_ready,
        "phase5_reasons": reasons,
        "endurance": endurance,
        "endurance_sha256": _sha256_bytes(_canonical_bytes(endurance)),
        "ledger_audit": ledger,
        "ledger_audit_sha256": _sha256_bytes(_canonical_bytes(ledger)),
        "closed_positions": int(status["closed_positions"]),
        "distinct_valued_pools": int(status["distinct_valued_pools"]),
        "post_collection_audit_ready": True,
        "requires_additional_paper_evidence": not promotion_ready,
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
            "Audit a completed bounded Phase 5 PAPER evidence-collection window. "
            "The tool requires the deadline to have passed, verifies the timer "
            "is inactive and still disabled, checks systemd fragments/drop-ins "
            "and scheduler environment against the start readiness, then builds "
            "a fresh read-only Phase 5 status. It never persists promotion, "
            "restarts services, enables timers, signs/submits transactions, or "
            "uses live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-cycle-audit", required=True)
    parser.add_argument("--collection-readiness", required=True)
    parser.add_argument("--activation-receipt", required=True)
    args = parser.parse_args()

    report = build_post_collection_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        post_cycle_audit_path=args.post_cycle_audit,
        collection_readiness_path=args.collection_readiness,
        activation_receipt_path=args.activation_receipt,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
