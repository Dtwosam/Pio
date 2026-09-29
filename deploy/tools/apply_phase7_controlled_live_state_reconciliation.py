from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_STATE_RECONCILIATION_APPLY_V1"

PLANNER_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_state_reconciliation_plan.py"
)
REVIEWED_SOURCE_BLOBS = {
    PLANNER_TOOL: "c46686908f5d9252de2e090594f4eb9bdb1244bd",
}

ACTION_INGEST_CHAIN = "INGEST_CHAIN_TRANSACTION_EVENTS"
ACTION_INGEST_RECEIPT = "INGEST_EXECUTION_RECEIPT"
ACTION_APPLY_EFFECT = "APPLY_LIVE_EXECUTION_EFFECT"
ACTION_APPLY_POSITION = "APPLY_LIVE_POSITION_EFFECT"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_plan_sha256",
    "expected_plan_sha256",
    "fresh_plan_sha256",
    "decision_id",
    "signature",
    "pool_address",
    "intent_status",
    "observed_at",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "planned_actions",
    "applied_actions",
    "concurrent_exact_reuse_observed",
    "expected_position_address",
    "expected_target_state_sha256",
    "actual_target_state_sha256",
    "target_state_matches_plan",
    "receipt_audit_after_apply",
    "live_ledger_audit_after_apply",
    "global_receipt_audit_clean_after_apply",
    "global_live_ledger_clean_after_apply",
    "reconciliation_apply_complete",
    "reconciliation_remaining",
    "requires_post_apply_audit",
    "requires_learning_label_reconciliation",
    "requires_separate_phase7_promotion_action",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
    "phase7_promotion_persisted",
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


def _load_planner(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 reconciliation-apply dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 reconciliation-apply dependency mismatch: {relative}"
            )
    return _load_module(
        source / PLANNER_TOOL,
        "phase7_state_reconciliation_apply_planner",
    )


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ValueError("audit result did not produce a record")
    return result


def _existing_storage(storage_type: type[Any], database: Path) -> Any:
    storage = storage_type.__new__(storage_type)
    storage.path = database
    return storage


def validate_reconciliation_apply(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 reconciliation apply must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"apply_sha256"}:
        raise ValueError("Phase 7 reconciliation apply schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 reconciliation apply format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 reconciliation apply type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 reconciliation apply lineage mismatch")

    for field in (
        "saved_plan_sha256",
        "expected_plan_sha256",
        "fresh_plan_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "expected_target_state_sha256",
        "actual_target_state_sha256",
        "apply_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 reconciliation apply {field} is invalid")

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 7 reconciliation apply {field} is invalid")

    if report["saved_plan_sha256"] != report["expected_plan_sha256"]:
        raise ValueError("Phase 7 reconciliation apply saved-plan digest mismatch")
    if report["fresh_plan_sha256"] != report["saved_plan_sha256"]:
        raise ValueError("Phase 7 reconciliation apply fresh-plan digest mismatch")
    if report["actual_target_state_sha256"] != report["expected_target_state_sha256"]:
        raise ValueError("Phase 7 reconciliation target-state digest mismatch")

    allowed = {
        ACTION_INGEST_CHAIN,
        ACTION_INGEST_RECEIPT,
        ACTION_APPLY_EFFECT,
        ACTION_APPLY_POSITION,
    }
    planned = report.get("planned_actions")
    applied = report.get("applied_actions")
    if not isinstance(planned, list) or not planned:
        raise ValueError("Phase 7 reconciliation apply requires planned actions")
    if planned != applied:
        raise ValueError("Phase 7 reconciliation applied actions differ from plan")
    if len(planned) != len(set(planned)) or any(item not in allowed for item in planned):
        raise ValueError("Phase 7 reconciliation apply action list is invalid")
    if report["intent_status"] == "FAILED" and ACTION_APPLY_POSITION in planned:
        raise ValueError("failed receipt cannot apply live-position mutation")

    for field in (
        "target_state_matches_plan",
        "reconciliation_apply_complete",
        "requires_post_apply_audit",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 reconciliation apply requires {field}=true")

    label_required = report["intent_status"] == "CONFIRMED"
    if report.get("requires_learning_label_reconciliation") is not label_required:
        raise ValueError("Phase 7 reconciliation learning-label binding mismatch")

    if report.get("reconciliation_remaining") is not False:
        raise ValueError("Phase 7 reconciliation apply requires reconciliation_remaining=false")

    for field in (
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_persisted",
        "production_repository_git_mutated",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 7 reconciliation apply requires {field}=false")

    for field in (
        "concurrent_exact_reuse_observed",
        "global_receipt_audit_clean_after_apply",
        "global_live_ledger_clean_after_apply",
        "production_pio_database_modified",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(f"Phase 7 reconciliation apply {field} is invalid")

    if not isinstance(report.get("receipt_audit_after_apply"), dict):
        raise ValueError("Phase 7 reconciliation receipt audit is invalid")
    if not isinstance(report.get("live_ledger_audit_after_apply"), dict):
        raise ValueError("Phase 7 reconciliation live-ledger audit is invalid")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["apply_sha256"] != expected:
        raise ValueError("Phase 7 reconciliation apply digest mismatch")


def apply_reconciliation(
    *,
    source_tree: str | Path,
    saved_plan_path: str | Path,
    expected_plan_sha256: str,
    saved_execution_receipt_path: str | Path,
    expected_execution_receipt_sha256: str,
    pio_database_path: str | Path,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
    observed_at: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    planner = _load_planner(source)

    saved = planner._load_json(
        saved_plan_path,
        label="saved Phase 7 live-state reconciliation plan",
    )
    planner.validate_reconciliation_plan(saved)
    if not _is_hex_digest(expected_plan_sha256, 64):
        raise ValueError("expected Phase 7 reconciliation-plan digest is invalid")
    if saved["plan_sha256"] != expected_plan_sha256:
        raise ValueError("saved Phase 7 reconciliation-plan digest mismatch")
    if saved.get("reconciliation_plan_ready") is not True:
        raise ValueError("saved Phase 7 reconciliation plan is not ready")
    if saved.get("reconciliation_required") is not True:
        raise ValueError("saved Phase 7 reconciliation plan requires no apply")
    if saved.get("requires_separate_reconciliation_apply") is not True:
        raise ValueError("saved Phase 7 reconciliation plan does not permit apply")

    for field in (
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_persisted",
        "production_pio_database_modified",
    ):
        if saved.get(field) is not False:
            raise ValueError(f"saved Phase 7 reconciliation plan unexpectedly sets {field}")

    fresh = planner.build_reconciliation_plan(
        source_tree=source,
        saved_execution_receipt_path=saved_execution_receipt_path,
        expected_execution_receipt_sha256=expected_execution_receipt_sha256,
        pio_database_path=pio_database_path,
        executor_binary_path=executor_binary_path,
        expected_executor_binary_sha256=expected_executor_binary_sha256,
        rpc_url=rpc_url,
        observed_at=observed_at,
    )
    planner.validate_reconciliation_plan(fresh)
    if fresh["plan_sha256"] != saved["plan_sha256"]:
        raise ValueError("fresh Phase 7 reconciliation plan differs from saved plan")

    planned_actions = list(saved["planned_actions"])
    if not planned_actions:
        raise ValueError("Phase 7 reconciliation apply has no planned actions")

    artifact = planner._load_json(
        saved_execution_receipt_path,
        label="saved Phase 7 execution receipt artifact",
    )
    receipt = artifact.get("receipt")
    if not isinstance(receipt, dict):
        raise ValueError("saved Phase 7 execution receipt payload is invalid")

    binary = Path(executor_binary_path).resolve(strict=True)
    snapshot = planner._inspect_transaction(
        executor_binary=binary,
        signature=saved["signature"],
        rpc_url=rpc_url,
    )
    planner._validate_transaction_snapshot(snapshot, receipt)
    if planner._sha256_value(snapshot) != saved["transaction_snapshot_sha256"]:
        raise ValueError("transaction snapshot changed after reconciliation planning")

    database = Path(pio_database_path).resolve(strict=True)
    before = planner._database_state(database)
    if before["database"] != saved["pio_database_sha256_before"]:
        raise ValueError("Pio database changed after reconciliation planning")
    if before["wal"] != saved["pio_wal_sha256_before"]:
        raise ValueError("Pio WAL changed after reconciliation planning")
    if before["shm"] != saved["pio_shm_sha256_before"]:
        raise ValueError("Pio SHM changed after reconciliation planning")

    _receipt_module, runtime = planner._load_reviewed_runtime(source)
    storage = _existing_storage(runtime["Storage"], database)

    applied_actions: list[str] = []
    concurrent_reuse = False
    expected_position = saved["expected_position_address"]

    if ACTION_INGEST_CHAIN in planned_actions:
        runtime["ingest_transaction_events"](
            storage,
            snapshot,
            observed_at=saved["observed_at"],
        )
        applied_actions.append(ACTION_INGEST_CHAIN)

    if ACTION_INGEST_RECEIPT in planned_actions:
        result = runtime["ingest_execution_receipt"](
            storage,
            receipt,
            observed_at=saved["observed_at"],
        )
        if result.chain_snapshot_reconciled is not True:
            raise ValueError("applied execution receipt did not reconcile to chain snapshot")
        concurrent_reuse = concurrent_reuse or bool(result.reused_existing)
        applied_actions.append(ACTION_INGEST_RECEIPT)

    if ACTION_APPLY_EFFECT in planned_actions:
        result = runtime["apply_live_execution_effect"](
            storage,
            saved["decision_id"],
        )
        if result.effect.position_address != expected_position:
            raise ValueError("applied live execution effect position differs from plan")
        concurrent_reuse = concurrent_reuse or bool(result.reused_existing)
        applied_actions.append(ACTION_APPLY_EFFECT)

    if ACTION_APPLY_POSITION in planned_actions:
        result = runtime["apply_live_position_effect"](
            storage,
            saved["decision_id"],
        )
        if result.position_address != expected_position:
            raise ValueError("applied live position differs from reconciliation plan")
        if result.next_status != "OPEN":
            raise ValueError("confirmed ENTER reconciliation did not produce OPEN position")
        concurrent_reuse = concurrent_reuse or bool(
            getattr(result, "reused_existing", False)
        )
        applied_actions.append(ACTION_APPLY_POSITION)

    target = planner._target_state(
        database,
        decision_id=saved["decision_id"],
        signature=saved["signature"],
        position_address=expected_position,
    )
    actual_target_sha = planner._sha256_value(target)
    if actual_target_sha != saved["private_target_state_sha256"]:
        raise ValueError("production reconciliation target differs from reviewed private replay")

    receipt_audit = runtime["audit_execution_receipts"](storage)
    live_audit = runtime["audit_live_execution_ledger"](storage)
    receipt_audit_record = _record(receipt_audit)
    live_audit_record = _record(live_audit)

    after = planner._database_state(database)
    modified = after != before

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
        "saved_plan_sha256": saved["plan_sha256"],
        "expected_plan_sha256": expected_plan_sha256,
        "fresh_plan_sha256": fresh["plan_sha256"],
        "decision_id": saved["decision_id"],
        "signature": saved["signature"],
        "pool_address": saved["pool_address"],
        "intent_status": saved["intent_status"],
        "observed_at": saved["observed_at"],
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "planned_actions": planned_actions,
        "applied_actions": applied_actions,
        "concurrent_exact_reuse_observed": concurrent_reuse,
        "expected_position_address": expected_position,
        "expected_target_state_sha256": saved["private_target_state_sha256"],
        "actual_target_state_sha256": actual_target_sha,
        "target_state_matches_plan": True,
        "receipt_audit_after_apply": receipt_audit_record,
        "live_ledger_audit_after_apply": live_audit_record,
        "global_receipt_audit_clean_after_apply": bool(
            receipt_audit_record.get("clean")
        ),
        "global_live_ledger_clean_after_apply": bool(
            live_audit_record.get("clean")
        ),
        "reconciliation_apply_complete": True,
        "reconciliation_remaining": False,
        "requires_post_apply_audit": True,
        "requires_learning_label_reconciliation": (
            saved["intent_status"] == "CONFIRMED"
        ),
        "requires_separate_phase7_promotion_action": True,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": modified,
    }
    report = {
        **identity,
        "apply_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_reconciliation_apply(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply exactly the missing Phase 7 chain/receipt/effect/position "
            "reconciliation rows from a reviewed private-replay plan. The tool "
            "never signs, submits, retries or authorizes new live capital."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-plan", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--saved-execution-receipt", required=True)
    parser.add_argument("--expected-execution-receipt-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--observed-at", required=True)
    args = parser.parse_args()

    report = apply_reconciliation(
        source_tree=args.source_tree,
        saved_plan_path=args.saved_plan,
        expected_plan_sha256=args.expected_plan_sha256,
        saved_execution_receipt_path=args.saved_execution_receipt,
        expected_execution_receipt_sha256=(
            args.expected_execution_receipt_sha256
        ),
        pio_database_path=args.pio_db,
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=args.expected_executor_binary_sha256,
        rpc_url=args.rpc_url,
        observed_at=args.observed_at,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
