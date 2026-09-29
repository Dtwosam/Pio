from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_FINAL_STATE_POST_RECONCILIATION_AUDIT_V1"
)

APPLY_TOOL = Path(
    "deploy/tools/apply_phase7_controlled_live_exit_final_state_reconciliation.py"
)
REVIEWED_SOURCE_BLOBS = {
    APPLY_TOOL: "88ed38eaa910b813dc5fd17da22e25d077d9ef42",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_apply_sha256",
    "expected_apply_sha256",
    "saved_plan_sha256",
    "opened_decision_id",
    "principal_exit_decision_id",
    "settlement_decision_id",
    "pool_address",
    "position_address",
    "principal_signature",
    "settlement_signature",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "expected_target_state_sha256",
    "actual_target_state_sha256",
    "target_state_matches_apply",
    "position_status",
    "closure_proof_present",
    "position_outcome_present",
    "position_outcome",
    "receipt_audit",
    "live_ledger_audit",
    "global_receipt_audit_clean",
    "global_live_ledger_clean",
    "exact_exit_lifecycle_reconciled",
    "learning_label_reconciliation_required",
    "phase7_evidence_status_recheck_required",
    "open_position_lifecycle_followup_required",
    "requires_separate_phase7_promotion_action",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_entry_authorized",
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


def _load_apply(source: Path) -> Any:
    path = source / APPLY_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 7 EXIT final reconciliation apply tool is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[APPLY_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT final reconciliation apply blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_final_post_reconciliation_apply",
    )


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        raise ValueError("audit result lacks to_record()")
    if not isinstance(result, dict):
        raise ValueError("audit result did not produce a record")
    return result


def _existing_storage(storage_type: type[Any], database: Path) -> Any:
    storage = storage_type.__new__(storage_type)
    storage.path = database
    return storage


def validate_exit_final_post_reconciliation_audit(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT post-reconciliation audit must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"audit_sha256"}:
        raise ValueError(
            "Phase 7 EXIT post-reconciliation audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT post-reconciliation audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT post-reconciliation audit type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "Phase 7 EXIT post-reconciliation audit lineage mismatch"
        )

    for field in (
        "saved_apply_sha256",
        "expected_apply_sha256",
        "saved_plan_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "expected_target_state_sha256",
        "actual_target_state_sha256",
        "audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT post-reconciliation audit {field} is invalid"
            )

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 7 EXIT post-reconciliation audit {field} is invalid"
            )

    if report["saved_apply_sha256"] != report["expected_apply_sha256"]:
        raise ValueError("post-reconciliation apply digest mismatch")
    if report["actual_target_state_sha256"] != report[
        "expected_target_state_sha256"
    ]:
        raise ValueError("post-reconciliation target-state digest mismatch")
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError("production Pio database changed during audit")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("production Pio WAL changed during audit")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("production Pio SHM changed during audit")

    for field in (
        "opened_decision_id",
        "principal_exit_decision_id",
        "settlement_decision_id",
        "pool_address",
        "position_address",
        "principal_signature",
        "settlement_signature",
        "pio_database_path",
        "position_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT post-reconciliation audit {field} is invalid"
            )

    if report["position_status"] != "CLOSED":
        raise ValueError(
            "Phase 7 EXIT post-reconciliation position is not CLOSED"
        )
    if not isinstance(report.get("position_outcome"), dict):
        raise ValueError(
            "Phase 7 EXIT post-reconciliation outcome is invalid"
        )
    if report["position_outcome"].get("position_address") != report[
        "position_address"
    ]:
        raise ValueError("post-reconciliation outcome position mismatch")
    if report["position_outcome"].get("closed_decision_id") != report[
        "settlement_decision_id"
    ]:
        raise ValueError(
            "post-reconciliation outcome settlement decision mismatch"
        )

    for field in (
        "target_state_matches_apply",
        "closure_proof_present",
        "position_outcome_present",
        "global_receipt_audit_clean",
        "global_live_ledger_clean",
        "exact_exit_lifecycle_reconciled",
        "learning_label_reconciliation_required",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT post-reconciliation audit requires {field}=true"
            )

    if report.get("open_position_lifecycle_followup_required") is not False:
        raise ValueError(
            "closed Phase 7 EXIT cannot require open-position lifecycle followup"
        )

    if not isinstance(report.get("receipt_audit"), dict):
        raise ValueError("post-reconciliation receipt audit is invalid")
    if not isinstance(report.get("live_ledger_audit"), dict):
        raise ValueError("post-reconciliation live-ledger audit is invalid")

    for field in (
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT post-reconciliation audit requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["audit_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT post-reconciliation audit digest mismatch"
        )


def build_exit_final_post_reconciliation_audit(
    *,
    source_tree: str | Path,
    saved_apply_path: str | Path,
    expected_apply_sha256: str,
    pio_database_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    apply_module = _load_apply(source)

    applied = apply_module._load_planner(source)._load_json(
        saved_apply_path,
        label="saved Phase 7 EXIT final reconciliation apply",
    )
    apply_module.validate_exit_final_reconciliation_apply(applied)
    if (
        not _is_hex_digest(expected_apply_sha256, 64)
        or applied["apply_sha256"] != expected_apply_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT final reconciliation apply digest mismatch"
        )

    for field in (
        "target_state_matches_plan",
        "closure_proof_present_after_apply",
        "position_outcome_present_after_apply",
        "reconciliation_apply_complete",
        "requires_post_apply_audit",
        "requires_learning_label_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if applied.get(field) is not True:
            raise ValueError(
                f"saved final reconciliation apply lost required {field}"
            )
    if applied.get("reconciliation_remaining") is not False:
        raise ValueError(
            "saved final reconciliation apply still has remaining work"
        )
    if applied.get("position_status_after_apply") != "CLOSED":
        raise ValueError(
            "saved final reconciliation apply did not close position"
        )

    planner = apply_module._load_planner(source)
    _principal, _settlement, _absence, runtime = planner._load_reviewed(
        source
    )

    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != applied["pio_database_path"]:
        raise ValueError(
            "post-reconciliation Pio database differs from apply artifact"
        )
    before = planner._database_state(database)
    if before["database"] != applied["pio_database_sha256_after"]:
        raise ValueError(
            "Pio database changed after final reconciliation apply"
        )
    if before["wal"] != applied["pio_wal_sha256_after"]:
        raise ValueError(
            "Pio WAL changed after final reconciliation apply"
        )
    if before["shm"] != applied["pio_shm_sha256_after"]:
        raise ValueError(
            "Pio SHM changed after final reconciliation apply"
        )

    target = planner._target_state(
        database,
        principal_decision_id=applied["principal_exit_decision_id"],
        principal_signature=applied["principal_signature"],
        settlement_decision_id=applied["settlement_decision_id"],
        settlement_signature=applied["settlement_signature"],
        position_address=applied["position_address"],
    )
    actual_target_sha = planner._sha256_value(target)
    if actual_target_sha != applied["expected_target_state_sha256"]:
        raise ValueError(
            "production target state differs from reconciliation apply"
        )

    position_status, closure_present, outcome_present, outcome_record = (
        apply_module._position_state(
            database,
            position_address=applied["position_address"],
            settlement_decision_id=applied["settlement_decision_id"],
        )
    )

    storage = _existing_storage(runtime["Storage"], database)
    receipt_audit = runtime["audit_execution_receipts"](storage)
    live_audit = runtime["audit_live_execution_ledger"](storage)
    receipt_record = _record(receipt_audit)
    live_record = _record(live_audit)
    receipt_clean = bool(receipt_record.get("clean"))
    live_clean = bool(live_record.get("clean"))
    if not receipt_clean:
        raise ValueError(
            "global execution-receipt audit is not clean after final reconciliation"
        )
    if not live_clean:
        raise ValueError(
            "global live-ledger audit is not clean after final reconciliation"
        )

    after = planner._database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during post-reconciliation audit"
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
        "saved_apply_sha256": applied["apply_sha256"],
        "expected_apply_sha256": expected_apply_sha256,
        "saved_plan_sha256": applied["saved_plan_sha256"],
        "opened_decision_id": applied["opened_decision_id"],
        "principal_exit_decision_id": applied[
            "principal_exit_decision_id"
        ],
        "settlement_decision_id": applied["settlement_decision_id"],
        "pool_address": applied["pool_address"],
        "position_address": applied["position_address"],
        "principal_signature": applied["principal_signature"],
        "settlement_signature": applied["settlement_signature"],
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "expected_target_state_sha256": applied[
            "expected_target_state_sha256"
        ],
        "actual_target_state_sha256": actual_target_sha,
        "target_state_matches_apply": True,
        "position_status": position_status,
        "closure_proof_present": closure_present,
        "position_outcome_present": outcome_present,
        "position_outcome": outcome_record,
        "receipt_audit": receipt_record,
        "live_ledger_audit": live_record,
        "global_receipt_audit_clean": True,
        "global_live_ledger_clean": True,
        "exact_exit_lifecycle_reconciled": True,
        "learning_label_reconciliation_required": True,
        "phase7_evidence_status_recheck_required": True,
        "open_position_lifecycle_followup_required": False,
        "requires_separate_phase7_promotion_action": True,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
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
    validate_exit_final_post_reconciliation_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of the final reconciled Phase 7 EXIT lifecycle. "
            "This tool proves the production target state, CLOSED position, "
            "closure proof, immutable atomic outcome, and global ledger audits "
            "without mutating state or authorizing any transaction or promotion."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-apply", required=True)
    parser.add_argument("--expected-apply-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    args = parser.parse_args()

    report = build_exit_final_post_reconciliation_audit(
        source_tree=args.source_tree,
        saved_apply_path=args.saved_apply,
        expected_apply_sha256=args.expected_apply_sha256,
        pio_database_path=args.pio_db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
