from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_FINAL_STATE_POST_APPLY_AUDIT_V1"
)

APPLY_TOOL = Path(
    "deploy/tools/apply_phase7_controlled_live_exit_final_state_reconciliation.py"
)
PLANNER_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_final_state_reconciliation_plan.py"
)
REVIEWED_SOURCE_BLOBS = {
    APPLY_TOOL: "88ed38eaa910b813dc5fd17da22e25d077d9ef42",
    PLANNER_TOOL: "3a094dd08eb09ed9245fe6a72e9f3e2895377cec",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_reconciliation_apply_sha256",
    "expected_reconciliation_apply_sha256",
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
    "production_source_stable_during_audit",
    "expected_target_state_sha256",
    "actual_target_state_sha256",
    "target_state_matches_apply",
    "principal_chain_snapshot_present",
    "principal_execution_receipt_present",
    "principal_execution_effect_present",
    "principal_exit_event_present",
    "settlement_chain_snapshot_present",
    "settlement_execution_receipt_present",
    "settlement_execution_effect_present",
    "closure_proof_present",
    "close_event_present",
    "position_outcome_present",
    "position_status",
    "principal_exit_next_status",
    "close_prior_status",
    "close_next_status",
    "outcome_label_status",
    "position_outcome",
    "receipt_audit",
    "live_ledger_audit",
    "global_receipt_audit_clean",
    "global_live_ledger_clean",
    "exact_exit_lifecycle_reconciled",
    "live_position_valuation_required",
    "live_position_valuation_complete",
    "learning_label_reconciliation_required",
    "learning_label_reconciliation_ready",
    "phase7_evidence_status_recheck_required",
    "post_apply_audit_ready",
    "requires_separate_phase7_promotion_action",
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
            raise ValueError(
                f"Phase 7 EXIT post-apply audit dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT post-apply audit dependency mismatch: {relative}"
            )
    apply_module = _load_module(
        source / APPLY_TOOL,
        "phase7_exit_post_apply_audit_apply",
    )
    planner_module = _load_module(
        source / PLANNER_TOOL,
        "phase7_exit_post_apply_audit_planner",
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


def _row(
    conn: sqlite3.Connection,
    query: str,
    args: tuple[Any, ...],
) -> sqlite3.Row | None:
    return conn.execute(query, args).fetchone()


def _closed_lifecycle_state(
    database: Path,
    *,
    principal_exit_decision_id: str,
    settlement_decision_id: str,
    principal_signature: str,
    settlement_signature: str,
    position_address: str,
) -> dict[str, Any]:
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        position = _row(
            conn,
            """
            SELECT position_address, pool_address, status,
                   opened_decision_id, closed_decision_id,
                   closed_signature
            FROM live_positions
            WHERE position_address = ?
            """,
            (position_address,),
        )
        if position is None:
            raise ValueError(
                "post-apply audit cannot find reconciled live position"
            )
        if str(position["status"]) != "CLOSED":
            raise ValueError(
                "post-apply audit requires CLOSED live position"
            )
        if str(position["closed_decision_id"] or "") != settlement_decision_id:
            raise ValueError(
                "closed position settlement decision differs from apply artifact"
            )
        if str(position["closed_signature"] or "") != settlement_signature:
            raise ValueError(
                "closed position settlement signature differs from apply artifact"
            )

        principal_chain = _row(
            conn,
            "SELECT signature FROM chain_transaction_snapshots "
            "WHERE signature = ?",
            (principal_signature,),
        )
        principal_receipt = _row(
            conn,
            "SELECT decision_id FROM live_execution_receipts "
            "WHERE decision_id = ? AND signature = ?",
            (principal_exit_decision_id, principal_signature),
        )
        principal_effect = _row(
            conn,
            "SELECT decision_id FROM live_execution_effects "
            "WHERE decision_id = ? AND signature = ? "
            "AND position_address = ?",
            (
                principal_exit_decision_id,
                principal_signature,
                position_address,
            ),
        )
        principal_event = _row(
            conn,
            """
            SELECT action, prior_status, next_status
            FROM live_position_events
            WHERE decision_id = ?
              AND signature = ?
              AND position_address = ?
            """,
            (
                principal_exit_decision_id,
                principal_signature,
                position_address,
            ),
        )
        if principal_event is None:
            raise ValueError(
                "post-apply audit is missing principal EXIT lifecycle event"
            )
        if str(principal_event["action"]) != "EXIT":
            raise ValueError(
                "principal EXIT lifecycle event action is not EXIT"
            )
        if str(principal_event["next_status"]) != "LIQUIDITY_REMOVED":
            raise ValueError(
                "principal EXIT lifecycle event did not end LIQUIDITY_REMOVED"
            )

        settlement_chain = _row(
            conn,
            "SELECT signature FROM chain_transaction_snapshots "
            "WHERE signature = ?",
            (settlement_signature,),
        )
        settlement_receipt = _row(
            conn,
            "SELECT decision_id FROM live_execution_receipts "
            "WHERE decision_id = ? AND signature = ?",
            (settlement_decision_id, settlement_signature),
        )
        settlement_effect = _row(
            conn,
            "SELECT decision_id FROM live_execution_effects "
            "WHERE decision_id = ? AND signature = ? "
            "AND position_address = ?",
            (
                settlement_decision_id,
                settlement_signature,
                position_address,
            ),
        )
        closure = _row(
            conn,
            """
            SELECT closed
            FROM live_position_closure_proofs
            WHERE decision_id = ?
              AND signature = ?
              AND position_address = ?
            """,
            (
                settlement_decision_id,
                settlement_signature,
                position_address,
            ),
        )
        if closure is None or int(closure["closed"]) != 1:
            raise ValueError(
                "post-apply audit is missing confirmed position closure proof"
            )
        close_event = _row(
            conn,
            """
            SELECT action, prior_status, next_status
            FROM live_position_events
            WHERE decision_id = ?
              AND signature = ?
              AND position_address = ?
            """,
            (
                settlement_decision_id,
                settlement_signature,
                position_address,
            ),
        )
        if close_event is None:
            raise ValueError(
                "post-apply audit is missing CLOSE lifecycle event"
            )
        if str(close_event["action"]) != "CLOSE":
            raise ValueError(
                "settlement lifecycle event action is not CLOSE"
            )
        if str(close_event["prior_status"]) != "LIQUIDITY_REMOVED":
            raise ValueError(
                "CLOSE lifecycle event does not start at LIQUIDITY_REMOVED"
            )
        if str(close_event["next_status"]) != "CLOSED":
            raise ValueError(
                "CLOSE lifecycle event does not end CLOSED"
            )

        outcome = _row(
            conn,
            """
            SELECT closed_decision_id, label_status, raw_json
            FROM live_position_outcomes
            WHERE position_address = ?
            """,
            (position_address,),
        )
        if outcome is None:
            raise ValueError(
                "post-apply audit is missing immutable position outcome"
            )
        if str(outcome["closed_decision_id"]) != settlement_decision_id:
            raise ValueError(
                "position outcome settlement decision differs from apply artifact"
            )
        label_status = str(outcome["label_status"])
        if label_status not in {"ATOMIC_ONLY", "VALUED"}:
            raise ValueError(
                "position outcome has invalid label_status"
            )
        try:
            outcome_record = json.loads(str(outcome["raw_json"]))
        except json.JSONDecodeError as exc:
            raise ValueError(
                "position outcome raw_json is invalid"
            ) from exc
        if not isinstance(outcome_record, dict):
            raise ValueError("position outcome raw_json must be an object")
        if outcome_record.get("position_address") != position_address:
            raise ValueError("position outcome address mismatch")
        if outcome_record.get("closed_decision_id") != settlement_decision_id:
            raise ValueError("position outcome closed decision mismatch")
        if outcome_record.get("label_status") != label_status:
            raise ValueError("position outcome label status mismatch")

        return {
            "principal_chain_snapshot_present": principal_chain is not None,
            "principal_execution_receipt_present": principal_receipt is not None,
            "principal_execution_effect_present": principal_effect is not None,
            "principal_exit_event_present": True,
            "settlement_chain_snapshot_present": settlement_chain is not None,
            "settlement_execution_receipt_present": settlement_receipt is not None,
            "settlement_execution_effect_present": settlement_effect is not None,
            "closure_proof_present": True,
            "close_event_present": True,
            "position_outcome_present": True,
            "position_status": "CLOSED",
            "principal_exit_next_status": str(
                principal_event["next_status"]
            ),
            "close_prior_status": str(close_event["prior_status"]),
            "close_next_status": str(close_event["next_status"]),
            "outcome_label_status": label_status,
            "position_outcome": outcome_record,
        }
    finally:
        conn.close()


def validate_exit_final_post_apply_audit(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT post-apply audit must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"audit_sha256"}:
        raise ValueError(
            "Phase 7 EXIT post-apply audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT post-apply audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT post-apply audit type"
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
            "Phase 7 EXIT post-apply audit lineage mismatch"
        )

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
            raise ValueError(
                f"Phase 7 EXIT post-apply audit {field} is invalid"
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
                f"Phase 7 EXIT post-apply audit {field} is invalid"
            )

    if report["saved_reconciliation_apply_sha256"] != report[
        "expected_reconciliation_apply_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT post-apply audit apply digest mismatch"
        )
    if report["actual_target_state_sha256"] != report[
        "expected_target_state_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT post-apply audit target-state mismatch"
        )
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "production Pio database changed during post-apply audit"
        )
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError(
            "production Pio WAL changed during post-apply audit"
        )
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError(
            "production Pio SHM changed during post-apply audit"
        )

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
        "principal_exit_next_status",
        "close_prior_status",
        "close_next_status",
        "outcome_label_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT post-apply audit {field} is invalid"
            )

    if report["position_status"] != "CLOSED":
        raise ValueError(
            "Phase 7 EXIT post-apply audit requires CLOSED position"
        )
    if report["principal_exit_next_status"] != "LIQUIDITY_REMOVED":
        raise ValueError(
            "principal EXIT lifecycle did not end LIQUIDITY_REMOVED"
        )
    if report["close_prior_status"] != "LIQUIDITY_REMOVED":
        raise ValueError(
            "CLOSE lifecycle did not begin LIQUIDITY_REMOVED"
        )
    if report["close_next_status"] != "CLOSED":
        raise ValueError(
            "CLOSE lifecycle did not end CLOSED"
        )

    for field in (
        "production_source_stable_during_audit",
        "target_state_matches_apply",
        "principal_chain_snapshot_present",
        "principal_execution_receipt_present",
        "principal_execution_effect_present",
        "principal_exit_event_present",
        "settlement_chain_snapshot_present",
        "settlement_execution_receipt_present",
        "settlement_execution_effect_present",
        "closure_proof_present",
        "close_event_present",
        "position_outcome_present",
        "exact_exit_lifecycle_reconciled",
        "learning_label_reconciliation_required",
        "phase7_evidence_status_recheck_required",
        "post_apply_audit_ready",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT post-apply audit requires {field}=true"
            )

    label_status = report["outcome_label_status"]
    if label_status not in {"ATOMIC_ONLY", "VALUED"}:
        raise ValueError(
            "Phase 7 EXIT post-apply audit outcome label status is invalid"
        )
    valuation_required = label_status == "ATOMIC_ONLY"
    valuation_complete = label_status == "VALUED"
    if report.get("live_position_valuation_required") is not valuation_required:
        raise ValueError(
            "Phase 7 EXIT post-apply valuation-required binding mismatch"
        )
    if report.get("live_position_valuation_complete") is not valuation_complete:
        raise ValueError(
            "Phase 7 EXIT post-apply valuation-complete binding mismatch"
        )
    if report.get("learning_label_reconciliation_ready") is not valuation_complete:
        raise ValueError(
            "Phase 7 EXIT post-apply learning-label readiness mismatch"
        )

    outcome = report.get("position_outcome")
    if not isinstance(outcome, dict):
        raise ValueError(
            "Phase 7 EXIT post-apply position outcome is invalid"
        )
    if outcome.get("position_address") != report["position_address"]:
        raise ValueError(
            "Phase 7 EXIT post-apply outcome position mismatch"
        )
    if outcome.get("closed_decision_id") != report[
        "settlement_decision_id"
    ]:
        raise ValueError(
            "Phase 7 EXIT post-apply outcome settlement decision mismatch"
        )
    if outcome.get("label_status") != label_status:
        raise ValueError(
            "Phase 7 EXIT post-apply outcome label status mismatch"
        )

    if not isinstance(report.get("receipt_audit"), dict):
        raise ValueError(
            "Phase 7 EXIT post-apply receipt audit is invalid"
        )
    if not isinstance(report.get("live_ledger_audit"), dict):
        raise ValueError(
            "Phase 7 EXIT post-apply live-ledger audit is invalid"
        )
    for field in (
        "global_receipt_audit_clean",
        "global_live_ledger_clean",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(
                f"Phase 7 EXIT post-apply audit {field} is invalid"
            )

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
            raise ValueError(
                f"Phase 7 EXIT post-apply audit requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["audit_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT post-apply audit digest mismatch"
        )


def build_exit_final_post_apply_audit(
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
        label="saved Phase 7 EXIT final reconciliation apply artifact",
    )
    apply_module.validate_exit_final_reconciliation_apply(applied)
    if (
        not _is_hex_digest(expected_reconciliation_apply_sha256, 64)
        or applied["apply_sha256"]
        != expected_reconciliation_apply_sha256
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
                f"saved final reconciliation apply requires {field}=true"
            )
    if applied.get("reconciliation_remaining") is not False:
        raise ValueError(
            "saved final reconciliation apply still has remaining work"
        )
    if applied.get("position_status_after_apply") != "CLOSED":
        raise ValueError(
            "saved final reconciliation apply did not close position"
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
        if applied.get(field) is not False:
            raise ValueError(
                f"saved final reconciliation apply unexpectedly sets {field}"
            )

    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != applied["pio_database_path"]:
        raise ValueError(
            "post-apply audit Pio database differs from apply artifact"
        )

    before = planner._database_state(database)
    if before["database"] is None:
        raise ValueError("Pio database is missing")

    target = planner._target_state(
        database,
        principal_decision_id=applied["principal_exit_decision_id"],
        principal_signature=applied["principal_signature"],
        settlement_decision_id=applied["settlement_decision_id"],
        settlement_signature=applied["settlement_signature"],
        position_address=applied["position_address"],
    )
    target_sha = planner._sha256_value(target)
    if target_sha != applied["expected_target_state_sha256"]:
        raise ValueError(
            "production final reconciliation target differs from apply artifact"
        )

    lifecycle = _closed_lifecycle_state(
        database,
        principal_exit_decision_id=applied[
            "principal_exit_decision_id"
        ],
        settlement_decision_id=applied["settlement_decision_id"],
        principal_signature=applied["principal_signature"],
        settlement_signature=applied["settlement_signature"],
        position_address=applied["position_address"],
    )

    with tempfile.TemporaryDirectory(
        prefix="pio-phase7-exit-post-apply-audit-"
    ) as tmp:
        private_db = Path(tmp) / "pio.db"
        planner._backup_database(database, private_db)
        _principal, _settlement, _absence, runtime = planner._load_reviewed(
            source
        )
        storage = runtime["Storage"](private_db)
        receipt_audit = runtime["audit_execution_receipts"](storage)
        live_audit = runtime["audit_live_execution_ledger"](storage)

    after = planner._database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during post-apply audit"
        )

    receipt_audit_record = _record(receipt_audit)
    live_audit_record = _record(live_audit)
    label_status = lifecycle["outcome_label_status"]
    valuation_required = label_status == "ATOMIC_ONLY"
    valuation_complete = label_status == "VALUED"

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
        "opened_decision_id": applied["opened_decision_id"],
        "principal_exit_decision_id": applied["principal_exit_decision_id"],
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
        "production_source_stable_during_audit": True,
        "expected_target_state_sha256": applied[
            "expected_target_state_sha256"
        ],
        "actual_target_state_sha256": target_sha,
        "target_state_matches_apply": True,
        "principal_chain_snapshot_present": lifecycle[
            "principal_chain_snapshot_present"
        ],
        "principal_execution_receipt_present": lifecycle[
            "principal_execution_receipt_present"
        ],
        "principal_execution_effect_present": lifecycle[
            "principal_execution_effect_present"
        ],
        "principal_exit_event_present": lifecycle[
            "principal_exit_event_present"
        ],
        "settlement_chain_snapshot_present": lifecycle[
            "settlement_chain_snapshot_present"
        ],
        "settlement_execution_receipt_present": lifecycle[
            "settlement_execution_receipt_present"
        ],
        "settlement_execution_effect_present": lifecycle[
            "settlement_execution_effect_present"
        ],
        "closure_proof_present": lifecycle["closure_proof_present"],
        "close_event_present": lifecycle["close_event_present"],
        "position_outcome_present": lifecycle["position_outcome_present"],
        "position_status": lifecycle["position_status"],
        "principal_exit_next_status": lifecycle[
            "principal_exit_next_status"
        ],
        "close_prior_status": lifecycle["close_prior_status"],
        "close_next_status": lifecycle["close_next_status"],
        "outcome_label_status": label_status,
        "position_outcome": lifecycle["position_outcome"],
        "receipt_audit": receipt_audit_record,
        "live_ledger_audit": live_audit_record,
        "global_receipt_audit_clean": bool(
            receipt_audit_record.get("clean")
        ),
        "global_live_ledger_clean": bool(
            live_audit_record.get("clean")
        ),
        "exact_exit_lifecycle_reconciled": True,
        "live_position_valuation_required": valuation_required,
        "live_position_valuation_complete": valuation_complete,
        "learning_label_reconciliation_required": True,
        "learning_label_reconciliation_ready": valuation_complete,
        "phase7_evidence_status_recheck_required": True,
        "post_apply_audit_ready": True,
        "requires_separate_phase7_promotion_action": True,
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
    validate_exit_final_post_apply_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of the fully reconciled Phase 7 EXIT lifecycle. "
            "The audit proves the principal EXIT reached LIQUIDITY_REMOVED, "
            "the settlement produced CLOSE/CLOSED with a persisted closure "
            "proof and immutable outcome, and routes next to valuation or "
            "learning-label reconciliation without authorizing more trading."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-reconciliation-apply", required=True)
    parser.add_argument(
        "--expected-reconciliation-apply-sha256",
        required=True,
    )
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    args = parser.parse_args()

    report = build_exit_final_post_apply_audit(
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
