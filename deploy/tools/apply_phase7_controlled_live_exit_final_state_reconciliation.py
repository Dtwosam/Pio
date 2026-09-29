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
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_FINAL_STATE_RECONCILIATION_APPLY_V1"
)

PLANNER_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_final_state_reconciliation_plan.py"
)
REVIEWED_SOURCE_BLOBS = {
    PLANNER_TOOL: "3a094dd08eb09ed9245fe6a72e9f3e2895377cec",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_plan_sha256",
    "expected_plan_sha256",
    "fresh_plan_sha256",
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
    "planned_actions",
    "applied_actions",
    "concurrent_exact_reuse_observed",
    "expected_target_state_sha256",
    "actual_target_state_sha256",
    "target_state_matches_plan",
    "principal_position_status_after_apply",
    "position_status_after_apply",
    "closure_proof_present_after_apply",
    "position_outcome_present_after_apply",
    "position_outcome",
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
    "new_live_entry_authorized",
    "new_live_capital_authorized",
    "phase7_promotion_authorized",
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
    path = source / PLANNER_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 7 EXIT final reconciliation planner is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[PLANNER_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT final reconciliation planner blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_final_reconciliation_apply_planner",
    )


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ValueError("runtime result did not produce a record")
    return result


def _existing_storage(storage_type: type[Any], database: Path) -> Any:
    storage = storage_type.__new__(storage_type)
    storage.path = database
    return storage


def _position_state(
    database: Path,
    *,
    position_address: str,
    settlement_decision_id: str,
) -> tuple[str, bool, bool, dict[str, Any]]:
    import sqlite3

    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        position = conn.execute(
            """
            SELECT status, closed_decision_id, closed_signature
            FROM live_positions
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if position is None:
            raise ValueError(
                "final reconciliation apply cannot find live position"
            )
        status = str(position["status"])
        if status != "CLOSED":
            raise ValueError(
                "final reconciliation apply did not produce CLOSED position"
            )
        if str(position["closed_decision_id"] or "") != settlement_decision_id:
            raise ValueError(
                "closed position settlement decision differs from plan"
            )

        closure = conn.execute(
            """
            SELECT closed
            FROM live_position_closure_proofs
            WHERE decision_id = ? AND position_address = ?
            """,
            (settlement_decision_id, position_address),
        ).fetchone()
        closure_present = closure is not None and int(closure["closed"]) == 1
        if not closure_present:
            raise ValueError(
                "final reconciliation apply is missing closure proof"
            )

        outcome = conn.execute(
            """
            SELECT raw_json
            FROM live_position_outcomes
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if outcome is None:
            raise ValueError(
                "final reconciliation apply is missing position outcome"
            )
        try:
            outcome_record = json.loads(str(outcome["raw_json"]))
        except json.JSONDecodeError as exc:
            raise ValueError(
                "final reconciliation position outcome raw_json is invalid"
            ) from exc
        if not isinstance(outcome_record, dict):
            raise ValueError(
                "final reconciliation position outcome is invalid"
            )
        return status, closure_present, True, outcome_record
    finally:
        conn.close()


def validate_exit_final_reconciliation_apply(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT final reconciliation apply must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"apply_sha256"}:
        raise ValueError(
            "Phase 7 EXIT final reconciliation apply schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT final reconciliation apply format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT final reconciliation apply type"
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
            "Phase 7 EXIT final reconciliation apply lineage mismatch"
        )

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
            raise ValueError(
                f"Phase 7 EXIT final reconciliation apply {field} is invalid"
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
                f"Phase 7 EXIT final reconciliation apply {field} is invalid"
            )

    if report["saved_plan_sha256"] != report["expected_plan_sha256"]:
        raise ValueError("saved final reconciliation plan digest mismatch")
    if report["fresh_plan_sha256"] != report["saved_plan_sha256"]:
        raise ValueError("fresh final reconciliation plan digest mismatch")
    if report["actual_target_state_sha256"] != report[
        "expected_target_state_sha256"
    ]:
        raise ValueError("final reconciliation target-state digest mismatch")

    for field in (
        "opened_decision_id",
        "principal_exit_decision_id",
        "settlement_decision_id",
        "pool_address",
        "position_address",
        "principal_signature",
        "settlement_signature",
        "pio_database_path",
        "principal_position_status_after_apply",
        "position_status_after_apply",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT final reconciliation apply {field} is invalid"
            )

    if report["principal_position_status_after_apply"] not in {
        "LIQUIDITY_REMOVED",
        "CLOSED",
    }:
        raise ValueError(
            "principal EXIT reconciliation did not reach removed-liquidity state"
        )
    if report["position_status_after_apply"] != "CLOSED":
        raise ValueError(
            "final reconciliation apply did not close position"
        )

    planned = report.get("planned_actions")
    applied = report.get("applied_actions")
    if not isinstance(planned, list) or not planned:
        raise ValueError(
            "final reconciliation apply requires non-empty planned actions"
        )
    if planned != applied:
        raise ValueError(
            "final reconciliation applied actions differ from plan"
        )
    if len(planned) != len(set(planned)):
        raise ValueError(
            "final reconciliation applied actions contain duplicates"
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
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT final reconciliation apply requires {field}=true"
            )

    if report.get("reconciliation_remaining") is not False:
        raise ValueError(
            "Phase 7 EXIT final reconciliation apply still has remaining work"
        )

    if not isinstance(report.get("concurrent_exact_reuse_observed"), bool):
        raise ValueError(
            "final reconciliation concurrent-reuse flag is invalid"
        )
    if not isinstance(report.get("production_pio_database_modified"), bool):
        raise ValueError(
            "final reconciliation production DB mutation flag is invalid"
        )
    if not isinstance(report.get("position_outcome"), dict):
        raise ValueError(
            "final reconciliation position outcome is invalid"
        )
    if report["position_outcome"].get("position_address") != report[
        "position_address"
    ]:
        raise ValueError("final reconciliation outcome position mismatch")
    if report["position_outcome"].get("closed_decision_id") != report[
        "settlement_decision_id"
    ]:
        raise ValueError(
            "final reconciliation outcome settlement decision mismatch"
        )

    if not isinstance(report.get("receipt_audit_after_apply"), dict):
        raise ValueError("final reconciliation receipt audit is invalid")
    if not isinstance(report.get("live_ledger_audit_after_apply"), dict):
        raise ValueError("final reconciliation live-ledger audit is invalid")
    for field in (
        "global_receipt_audit_clean_after_apply",
        "global_live_ledger_clean_after_apply",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(
                f"Phase 7 EXIT final reconciliation apply {field} is invalid"
            )

    for field in (
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_repository_git_mutated",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT final reconciliation apply requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["apply_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT final reconciliation apply digest mismatch"
        )


def apply_exit_final_reconciliation(
    *,
    source_tree: str | Path,
    saved_plan_path: str | Path,
    expected_plan_sha256: str,
    saved_principal_receipt_path: str | Path,
    expected_principal_receipt_sha256: str,
    saved_settlement_receipt_path: str | Path,
    expected_settlement_receipt_sha256: str,
    saved_account_absence_proof_path: str | Path,
    expected_account_absence_proof_sha256: str,
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
        label="saved Phase 7 EXIT final reconciliation plan",
    )
    planner.validate_exit_final_reconciliation_plan(saved)
    if (
        not _is_hex_digest(expected_plan_sha256, 64)
        or saved["plan_sha256"] != expected_plan_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT final reconciliation plan digest mismatch"
        )
    if saved.get("reconciliation_plan_ready") is not True:
        raise ValueError("saved final reconciliation plan is not ready")
    if saved.get("reconciliation_required") is not True:
        raise ValueError(
            "saved final reconciliation plan requires no production apply"
        )
    if saved.get("requires_separate_reconciliation_apply") is not True:
        raise ValueError(
            "saved final reconciliation plan does not permit production apply"
        )
    planned_actions = list(saved["planned_actions"])
    if not planned_actions:
        raise ValueError("final reconciliation plan has no actions to apply")

    for field in (
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_pio_database_modified",
    ):
        if saved.get(field) is not False:
            raise ValueError(
                f"saved final reconciliation plan unexpectedly sets {field}"
            )

    fresh = planner.build_exit_final_reconciliation_plan(
        source_tree=source,
        saved_principal_receipt_path=saved_principal_receipt_path,
        expected_principal_receipt_sha256=(
            expected_principal_receipt_sha256
        ),
        saved_settlement_receipt_path=saved_settlement_receipt_path,
        expected_settlement_receipt_sha256=(
            expected_settlement_receipt_sha256
        ),
        saved_account_absence_proof_path=saved_account_absence_proof_path,
        expected_account_absence_proof_sha256=(
            expected_account_absence_proof_sha256
        ),
        pio_database_path=pio_database_path,
        executor_binary_path=executor_binary_path,
        expected_executor_binary_sha256=(
            expected_executor_binary_sha256
        ),
        rpc_url=rpc_url,
        observed_at=observed_at,
    )
    planner.validate_exit_final_reconciliation_plan(fresh)
    if fresh["plan_sha256"] != saved["plan_sha256"]:
        raise ValueError(
            "fresh Phase 7 EXIT final reconciliation plan differs from saved plan"
        )

    (
        principal_module,
        settlement_module,
        absence_module,
        runtime,
    ) = planner._load_reviewed(source)
    principal = planner._load_json(
        saved_principal_receipt_path,
        label="saved principal EXIT terminal receipt",
    )
    principal_module.validate_exit_terminal_receipt(principal)
    settlement = planner._load_json(
        saved_settlement_receipt_path,
        label="saved EXIT settlement terminal receipt",
    )
    settlement_module.validate_exit_settlement_terminal_receipt(settlement)
    absence = planner._load_json(
        saved_account_absence_proof_path,
        label="saved EXIT settlement account-absence proof",
    )
    absence_module.validate_exit_settlement_account_absence_proof(absence)

    principal_snapshot = planner._inspect_transaction(
        executor_binary=Path(executor_binary_path).resolve(strict=True),
        signature=saved["principal_signature"],
        rpc_url=rpc_url,
    )
    settlement_snapshot = planner._inspect_transaction(
        executor_binary=Path(executor_binary_path).resolve(strict=True),
        signature=saved["settlement_signature"],
        rpc_url=rpc_url,
    )
    planner._validate_snapshot(principal_snapshot, principal)
    planner._validate_snapshot(settlement_snapshot, settlement)
    if planner._sha256_value(principal_snapshot) != saved[
        "principal_transaction_snapshot_sha256"
    ]:
        raise ValueError(
            "principal EXIT transaction snapshot changed after planning"
        )
    if planner._sha256_value(settlement_snapshot) != saved[
        "settlement_transaction_snapshot_sha256"
    ]:
        raise ValueError(
            "settlement transaction snapshot changed after planning"
        )

    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != saved["pio_database_path"]:
        raise ValueError(
            "production Pio database path differs from reconciliation plan"
        )
    before = planner._database_state(database)
    if before["database"] != saved["pio_database_sha256_before"]:
        raise ValueError(
            "Pio database changed after final reconciliation planning"
        )
    if before["wal"] != saved["pio_wal_sha256_before"]:
        raise ValueError("Pio WAL changed after final reconciliation planning")
    if before["shm"] != saved["pio_shm_sha256_before"]:
        raise ValueError("Pio SHM changed after final reconciliation planning")

    storage = _existing_storage(runtime["Storage"], database)
    principal_receipt_payload = planner._receipt_payload(
        principal,
        decision_id=saved["principal_exit_decision_id"],
    )
    settlement_receipt_payload = planner._receipt_payload(
        settlement,
        decision_id=saved["settlement_decision_id"],
    )
    closure_payload = {
        "position": saved["position_address"],
        "closed": True,
        "rpc_context_slot": absence["closure_rpc_context_slot"],
    }

    applied_actions: list[str] = []
    concurrent_reuse = False

    if planner.ACTION_INGEST_PRINCIPAL_CHAIN in planned_actions:
        runtime["ingest_transaction_events"](
            storage,
            principal_snapshot,
            observed_at=saved["observed_at"],
        )
        applied_actions.append(planner.ACTION_INGEST_PRINCIPAL_CHAIN)

    if planner.ACTION_INGEST_PRINCIPAL_RECEIPT in planned_actions:
        result = runtime["ingest_execution_receipt"](
            storage,
            principal_receipt_payload,
            observed_at=saved["observed_at"],
        )
        if result.chain_snapshot_reconciled is not True:
            raise ValueError(
                "applied principal EXIT receipt did not reconcile to chain"
            )
        concurrent_reuse = concurrent_reuse or bool(result.reused_existing)
        applied_actions.append(planner.ACTION_INGEST_PRINCIPAL_RECEIPT)

    if planner.ACTION_APPLY_PRINCIPAL_EFFECT in planned_actions:
        result = runtime["apply_live_execution_effect"](
            storage,
            saved["principal_exit_decision_id"],
        )
        if result.effect.position_address != saved["position_address"]:
            raise ValueError(
                "applied principal EXIT effect position differs from plan"
            )
        concurrent_reuse = concurrent_reuse or bool(result.reused_existing)
        applied_actions.append(planner.ACTION_APPLY_PRINCIPAL_EFFECT)

    principal_position_status = "LIQUIDITY_REMOVED"
    if planner.ACTION_APPLY_PRINCIPAL_POSITION in planned_actions:
        result = runtime["apply_live_position_effect"](
            storage,
            saved["principal_exit_decision_id"],
        )
        if result.position_address != saved["position_address"]:
            raise ValueError(
                "applied principal EXIT position differs from plan"
            )
        if result.next_status != "LIQUIDITY_REMOVED":
            raise ValueError(
                "applied principal EXIT did not produce LIQUIDITY_REMOVED"
            )
        principal_position_status = result.next_status
        concurrent_reuse = concurrent_reuse or bool(
            getattr(result, "reused_existing", False)
        )
        applied_actions.append(planner.ACTION_APPLY_PRINCIPAL_POSITION)

    if planner.ACTION_INGEST_SETTLEMENT_CHAIN in planned_actions:
        runtime["ingest_transaction_events"](
            storage,
            settlement_snapshot,
            observed_at=saved["observed_at"],
        )
        applied_actions.append(planner.ACTION_INGEST_SETTLEMENT_CHAIN)

    if planner.ACTION_INGEST_SETTLEMENT_RECEIPT in planned_actions:
        result = runtime["ingest_execution_receipt"](
            storage,
            settlement_receipt_payload,
            observed_at=saved["observed_at"],
        )
        if result.chain_snapshot_reconciled is not True:
            raise ValueError(
                "applied settlement receipt did not reconcile to chain"
            )
        concurrent_reuse = concurrent_reuse or bool(result.reused_existing)
        applied_actions.append(planner.ACTION_INGEST_SETTLEMENT_RECEIPT)

    if planner.ACTION_APPLY_SETTLEMENT_EFFECT in planned_actions:
        result = runtime["apply_live_execution_effect"](
            storage,
            saved["settlement_decision_id"],
        )
        if result.effect.position_address != saved["position_address"]:
            raise ValueError(
                "applied settlement effect position differs from plan"
            )
        concurrent_reuse = concurrent_reuse or bool(result.reused_existing)
        applied_actions.append(planner.ACTION_APPLY_SETTLEMENT_EFFECT)

    if planner.ACTION_FINALIZE_CLOSURE in planned_actions:
        result = runtime["finalize_live_position_closure"](
            storage,
            decision_id=saved["settlement_decision_id"],
            proof=closure_payload,
        )
        if result.position_address != saved["position_address"]:
            raise ValueError(
                "applied closure position differs from plan"
            )
        if result.closed is not True:
            raise ValueError("applied closure did not close position")
        concurrent_reuse = concurrent_reuse or bool(result.reused_existing)
        applied_actions.append(planner.ACTION_FINALIZE_CLOSURE)

    if planner.ACTION_BUILD_OUTCOME in planned_actions:
        result = runtime["build_live_position_outcome"](
            storage,
            position_address=saved["position_address"],
        )
        outcome_record = result.outcome.to_record()
        if outcome_record["closed_decision_id"] != saved[
            "settlement_decision_id"
        ]:
            raise ValueError(
                "applied position outcome settlement decision differs from plan"
            )
        concurrent_reuse = concurrent_reuse or bool(result.reused_existing)
        applied_actions.append(planner.ACTION_BUILD_OUTCOME)

    if applied_actions != planned_actions:
        raise ValueError(
            "final reconciliation applied actions differ from reviewed plan"
        )

    actual_target = planner._target_state(
        database,
        principal_decision_id=saved["principal_exit_decision_id"],
        principal_signature=saved["principal_signature"],
        settlement_decision_id=saved["settlement_decision_id"],
        settlement_signature=saved["settlement_signature"],
        position_address=saved["position_address"],
    )
    actual_target_sha = planner._sha256_value(actual_target)
    if actual_target_sha != saved["private_target_state_sha256"]:
        raise ValueError(
            "production final reconciliation target differs from private replay"
        )

    position_status, closure_present, outcome_present, outcome_record = (
        _position_state(
            database,
            position_address=saved["position_address"],
            settlement_decision_id=saved["settlement_decision_id"],
        )
    )

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
        "opened_decision_id": saved["opened_decision_id"],
        "principal_exit_decision_id": saved["principal_exit_decision_id"],
        "settlement_decision_id": saved["settlement_decision_id"],
        "pool_address": saved["pool_address"],
        "position_address": saved["position_address"],
        "principal_signature": saved["principal_signature"],
        "settlement_signature": saved["settlement_signature"],
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
        "expected_target_state_sha256": saved["private_target_state_sha256"],
        "actual_target_state_sha256": actual_target_sha,
        "target_state_matches_plan": True,
        "principal_position_status_after_apply": principal_position_status,
        "position_status_after_apply": position_status,
        "closure_proof_present_after_apply": closure_present,
        "position_outcome_present_after_apply": outcome_present,
        "position_outcome": outcome_record,
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
        "requires_learning_label_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": modified,
    }
    report = {
        **identity,
        "apply_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_final_reconciliation_apply(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply exactly the reviewed missing Phase 7 EXIT final-state "
            "reconciliation actions after rebuilding the same private-replay "
            "plan and proving the production Pio DB has not changed since "
            "planning. This tool mutates only reconciliation state; it never "
            "signs, submits, retries, adds capital, or promotes Phase 7."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-plan", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--saved-principal-receipt", required=True)
    parser.add_argument("--expected-principal-receipt-sha256", required=True)
    parser.add_argument("--saved-settlement-receipt", required=True)
    parser.add_argument("--expected-settlement-receipt-sha256", required=True)
    parser.add_argument("--saved-account-absence-proof", required=True)
    parser.add_argument("--expected-account-absence-proof-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--observed-at", required=True)
    args = parser.parse_args()

    report = apply_exit_final_reconciliation(
        source_tree=args.source_tree,
        saved_plan_path=args.saved_plan,
        expected_plan_sha256=args.expected_plan_sha256,
        saved_principal_receipt_path=args.saved_principal_receipt,
        expected_principal_receipt_sha256=(
            args.expected_principal_receipt_sha256
        ),
        saved_settlement_receipt_path=args.saved_settlement_receipt,
        expected_settlement_receipt_sha256=(
            args.expected_settlement_receipt_sha256
        ),
        saved_account_absence_proof_path=args.saved_account_absence_proof,
        expected_account_absence_proof_sha256=(
            args.expected_account_absence_proof_sha256
        ),
        pio_database_path=args.pio_db,
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=(
            args.expected_executor_binary_sha256
        ),
        rpc_url=args.rpc_url,
        observed_at=args.observed_at,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
