from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_POST_RECONCILIATION_AUDIT_V1"

APPLY_TOOL = Path(
    "deploy/tools/apply_phase7_controlled_live_state_reconciliation.py"
)
PLANNER_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_state_reconciliation_plan.py"
)

REVIEWED_SOURCE_BLOBS = {
    APPLY_TOOL: "689bbcffa425c2e8ffcdd2613fcd71cb211e0ba2",
    PLANNER_TOOL: "c46686908f5d9252de2e090594f4eb9bdb1244bd",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_reconciliation_apply_sha256",
    "expected_reconciliation_apply_sha256",
    "saved_plan_sha256",
    "decision_id",
    "signature",
    "pool_address",
    "intent_status",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "production_source_stable_during_audit",
    "expected_target_state_sha256",
    "actual_target_state_sha256",
    "target_state_matches_apply",
    "chain_snapshot_present",
    "execution_receipt_present",
    "execution_effect_present",
    "position_event_present",
    "position_address",
    "position_status",
    "receipt_audit",
    "live_ledger_audit",
    "global_receipt_audit_clean",
    "global_live_ledger_clean",
    "exact_execution_state_reconciled",
    "learning_label_reconciliation_required",
    "learning_label_reconciliation_complete",
    "open_position_lifecycle_followup_required",
    "phase7_evidence_status_recheck_required",
    "post_reconciliation_audit_ready",
    "new_live_entry_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
    "phase7_promotion_authorized",
    "phase7_promotion_persisted",
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


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 post-reconciliation dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 post-reconciliation dependency mismatch: {relative}"
            )
    apply_module = _load_module(
        source / APPLY_TOOL,
        "phase7_post_reconciliation_apply",
    )
    planner_module = _load_module(
        source / PLANNER_TOOL,
        "phase7_post_reconciliation_planner",
    )
    return apply_module, planner_module


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ValueError("audit result did not produce a record")
    return result


def _position_from_target(
    target: dict[str, Any],
    *,
    intent_status: str,
    decision_id: str,
    pool_address: str,
    expected_position_address: str | None,
) -> tuple[str | None, str | None]:
    positions = target.get("live_position")
    events = target.get("live_position_event")
    if not isinstance(positions, list) or not isinstance(events, list):
        raise ValueError("Phase 7 target state position rows are invalid")

    if intent_status == "FAILED":
        if expected_position_address is not None:
            raise ValueError("failed ENTER cannot have expected position address")
        if positions or events:
            raise ValueError("failed ENTER cannot have live position state")
        return None, None

    if intent_status != "CONFIRMED":
        raise ValueError("Phase 7 post-reconciliation audit requires terminal receipt")
    if not expected_position_address:
        raise ValueError("confirmed ENTER is missing expected position address")
    if len(positions) != 1 or len(events) != 1:
        raise ValueError("confirmed ENTER requires one position and one lifecycle event")

    position = positions[0]
    event = events[0]
    if position.get("position_address") != expected_position_address:
        raise ValueError("live position address differs from reconciliation apply")
    if position.get("pool_address") != pool_address:
        raise ValueError("live position pool differs from reconciliation apply")
    if position.get("status") != "OPEN":
        raise ValueError("confirmed ENTER live position must be OPEN")
    if position.get("opened_decision_id") != decision_id:
        raise ValueError("live position opening decision differs from apply artifact")
    if event.get("decision_id") != decision_id:
        raise ValueError("live position event decision differs from apply artifact")
    if event.get("position_address") != expected_position_address:
        raise ValueError("live position event address differs from apply artifact")
    if event.get("action") != "ENTER":
        raise ValueError("confirmed ENTER lifecycle event must be ENTER")
    if event.get("next_status") != "OPEN":
        raise ValueError("confirmed ENTER lifecycle event must end OPEN")
    return expected_position_address, "OPEN"


def validate_post_reconciliation_audit(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 post-reconciliation audit must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"audit_sha256"}:
        raise ValueError("Phase 7 post-reconciliation audit schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 post-reconciliation audit format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 post-reconciliation audit type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 post-reconciliation lineage mismatch")

    for field in (
        "saved_reconciliation_apply_sha256",
        "expected_reconciliation_apply_sha256",
        "saved_plan_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "expected_target_state_sha256",
        "actual_target_state_sha256",
        "audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 post-reconciliation {field} is invalid")

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 7 post-reconciliation {field} is invalid")

    if (
        report["saved_reconciliation_apply_sha256"]
        != report["expected_reconciliation_apply_sha256"]
    ):
        raise ValueError("Phase 7 post-reconciliation apply digest mismatch")
    if report["actual_target_state_sha256"] != report["expected_target_state_sha256"]:
        raise ValueError("Phase 7 post-reconciliation target-state mismatch")

    for field in (
        "production_source_stable_during_audit",
        "target_state_matches_apply",
        "chain_snapshot_present",
        "execution_receipt_present",
        "execution_effect_present",
        "exact_execution_state_reconciled",
        "phase7_evidence_status_recheck_required",
        "post_reconciliation_audit_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 post-reconciliation requires {field}=true")

    confirmed = report.get("intent_status") == "CONFIRMED"
    failed = report.get("intent_status") == "FAILED"
    if not (confirmed or failed):
        raise ValueError("Phase 7 post-reconciliation intent status is invalid")

    if report.get("position_event_present") is not confirmed:
        raise ValueError("Phase 7 post-reconciliation position-event binding mismatch")
    if report.get("learning_label_reconciliation_required") is not confirmed:
        raise ValueError("Phase 7 post-reconciliation label-required binding mismatch")
    if report.get("learning_label_reconciliation_complete") is not failed:
        raise ValueError("Phase 7 post-reconciliation label-complete binding mismatch")
    if report.get("open_position_lifecycle_followup_required") is not confirmed:
        raise ValueError("Phase 7 post-reconciliation lifecycle binding mismatch")

    if confirmed:
        if not isinstance(report.get("position_address"), str) or not report["position_address"]:
            raise ValueError("confirmed ENTER requires position address")
        if report.get("position_status") != "OPEN":
            raise ValueError("confirmed ENTER requires OPEN position status")
    else:
        if report.get("position_address") is not None:
            raise ValueError("failed ENTER cannot expose position address")
        if report.get("position_status") is not None:
            raise ValueError("failed ENTER cannot expose position status")

    for field in (
        "global_receipt_audit_clean",
        "global_live_ledger_clean",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(f"Phase 7 post-reconciliation {field} is invalid")

    if not isinstance(report.get("receipt_audit"), dict):
        raise ValueError("Phase 7 post-reconciliation receipt audit is invalid")
    if not isinstance(report.get("live_ledger_audit"), dict):
        raise ValueError("Phase 7 post-reconciliation live-ledger audit is invalid")

    for field in (
        "new_live_entry_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 7 post-reconciliation requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["audit_sha256"] != expected:
        raise ValueError("Phase 7 post-reconciliation audit digest mismatch")


def build_post_reconciliation_audit(
    *,
    source_tree: str | Path,
    saved_reconciliation_apply_path: str | Path,
    expected_reconciliation_apply_sha256: str,
    pio_database_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    apply_module, planner = _load_reviewed(source)

    applied = planner._load_json(
        saved_reconciliation_apply_path,
        label="saved Phase 7 reconciliation apply artifact",
    )
    apply_module.validate_reconciliation_apply(applied)
    if not _is_hex_digest(expected_reconciliation_apply_sha256, 64):
        raise ValueError("expected Phase 7 reconciliation-apply digest is invalid")
    if applied["apply_sha256"] != expected_reconciliation_apply_sha256:
        raise ValueError("saved Phase 7 reconciliation-apply digest mismatch")
    if applied.get("reconciliation_apply_complete") is not True:
        raise ValueError("saved Phase 7 reconciliation apply is incomplete")
    if applied.get("reconciliation_remaining") is not False:
        raise ValueError("saved Phase 7 reconciliation apply still has remaining work")
    if applied.get("target_state_matches_plan") is not True:
        raise ValueError("saved Phase 7 reconciliation apply target did not match plan")

    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != applied["pio_database_path"]:
        raise ValueError("post-reconciliation Pio database differs from apply artifact")

    before = planner._database_state(database)
    if before["database"] is None:
        raise ValueError("Pio database is missing")

    pre = planner._pre_state(
        database,
        decision_id=applied["decision_id"],
        signature=applied["signature"],
    )
    if not pre["chain"] or not pre["receipt"] or not pre["effect"]:
        raise ValueError("reconciled execution state is missing required persisted rows")

    expected_position = applied["expected_position_address"]
    target = planner._target_state(
        database,
        decision_id=applied["decision_id"],
        signature=applied["signature"],
        position_address=expected_position,
    )
    actual_target_sha = planner._sha256_value(target)
    if actual_target_sha != applied["expected_target_state_sha256"]:
        raise ValueError("production target state differs from reconciliation apply")

    position_address, position_status = _position_from_target(
        target,
        intent_status=applied["intent_status"],
        decision_id=applied["decision_id"],
        pool_address=applied["pool_address"],
        expected_position_address=expected_position,
    )

    with tempfile.TemporaryDirectory(prefix="pio-phase7-post-reconcile-audit-") as tmp:
        private_db = Path(tmp) / "pio.db"
        planner._backup_database(database, private_db)
        _receipt_module, runtime = planner._load_reviewed_runtime(source)
        storage = runtime["Storage"](private_db)
        receipt_audit = runtime["audit_execution_receipts"](storage)
        live_audit = runtime["audit_live_execution_ledger"](storage)

    after = planner._database_state(database)
    if after != before:
        raise ValueError("production Pio database changed during post-reconciliation audit")

    receipt_audit_record = _record(receipt_audit)
    live_audit_record = _record(live_audit)
    confirmed = applied["intent_status"] == "CONFIRMED"

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
        "saved_reconciliation_apply_sha256": applied["apply_sha256"],
        "expected_reconciliation_apply_sha256": (
            expected_reconciliation_apply_sha256
        ),
        "saved_plan_sha256": applied["saved_plan_sha256"],
        "decision_id": applied["decision_id"],
        "signature": applied["signature"],
        "pool_address": applied["pool_address"],
        "intent_status": applied["intent_status"],
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "production_source_stable_during_audit": True,
        "expected_target_state_sha256": applied["expected_target_state_sha256"],
        "actual_target_state_sha256": actual_target_sha,
        "target_state_matches_apply": True,
        "chain_snapshot_present": pre["chain"],
        "execution_receipt_present": pre["receipt"],
        "execution_effect_present": pre["effect"],
        "position_event_present": pre["position_event"],
        "position_address": position_address,
        "position_status": position_status,
        "receipt_audit": receipt_audit_record,
        "live_ledger_audit": live_audit_record,
        "global_receipt_audit_clean": bool(receipt_audit_record.get("clean")),
        "global_live_ledger_clean": bool(live_audit_record.get("clean")),
        "exact_execution_state_reconciled": True,
        "learning_label_reconciliation_required": confirmed,
        "learning_label_reconciliation_complete": not confirmed,
        "open_position_lifecycle_followup_required": confirmed,
        "phase7_evidence_status_recheck_required": True,
        "post_reconciliation_audit_ready": True,
        "new_live_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "audit_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_post_reconciliation_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of the exact Phase 7 live-state reconciliation "
            "target. This tool proves the persisted transaction/receipt/effect/"
            "position state and routes control back to Phase 7 evidence status "
            "without authorizing another live transaction."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-reconciliation-apply", required=True)
    parser.add_argument("--expected-reconciliation-apply-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    args = parser.parse_args()

    report = build_post_reconciliation_audit(
        source_tree=args.source_tree,
        saved_reconciliation_apply_path=args.saved_reconciliation_apply,
        expected_reconciliation_apply_sha256=(
            args.expected_reconciliation_apply_sha256
        ),
        pio_database_path=args.pio_db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
