from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_FINAL_STATE_RECONCILIATION_PLAN_V1"
)

PRINCIPAL_RECEIPT_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_terminal_receipt.py"
)
SETTLEMENT_RECEIPT_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_settlement_terminal_receipt.py"
)
ACCOUNT_ABSENCE_TOOL = Path(
    "deploy/tools/prove_phase7_controlled_live_exit_settlement_account_absent.py"
)
PYTHON_STORAGE = Path("python-learner/src/meteora_learner/storage.py")
PYTHON_TRANSACTION_INGEST = Path(
    "python-learner/src/meteora_learner/transaction_event_ingest.py"
)
PYTHON_RECEIPT_INGEST = Path(
    "python-learner/src/meteora_learner/execution_receipt_ingest.py"
)
PYTHON_EFFECTS = Path(
    "python-learner/src/meteora_learner/live_execution_effects.py"
)
PYTHON_POSITION_LEDGER = Path(
    "python-learner/src/meteora_learner/live_position_ledger.py"
)
PYTHON_POSITION_CLOSURE = Path(
    "python-learner/src/meteora_learner/live_position_closure.py"
)
PYTHON_POSITION_OUTCOME = Path(
    "python-learner/src/meteora_learner/live_position_outcome.py"
)
PYTHON_RECEIPT_AUDIT = Path(
    "python-learner/src/meteora_learner/execution_receipt_audit.py"
)
PYTHON_LIVE_AUDIT = Path(
    "python-learner/src/meteora_learner/live_execution_audit.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_TRANSACTION_EVENTS = Path("rust-executor/src/transaction_events.rs")

REVIEWED_SOURCE_BLOBS = {
    PRINCIPAL_RECEIPT_TOOL: "23f2f9d936c6b35c44dcb9cb548308b17f2dff10",
    SETTLEMENT_RECEIPT_TOOL: "06e6ad463483cd213083c6cfb008f343a7b0cda1",
    ACCOUNT_ABSENCE_TOOL: "d37f2861c115357db9ea43d0f6e2bb478ca0df01",
    PYTHON_STORAGE: "39bcc99413df357b89d261e854861e9e4a3fff23",
    PYTHON_TRANSACTION_INGEST: "160fb0678e42321a4f04d46c3e97fd8580a45abf",
    PYTHON_RECEIPT_INGEST: "947a5233358a80b32d52eb5e753c8a64a5582b26",
    PYTHON_EFFECTS: "1a076002912631b9bccf35a3dfa9818f05786dfb",
    PYTHON_POSITION_LEDGER: "afe0edcaf3dd00c79dae2a068f2d1517e48bd414",
    PYTHON_POSITION_CLOSURE: "eefa73c6a155f4ea78605c0ca69ea10991bb0522",
    PYTHON_POSITION_OUTCOME: "055e5b11faca3ddb49596d5ea14e55b8c44a2527",
    PYTHON_RECEIPT_AUDIT: "4acded692422181c5f8cb2b58ca89dbac51b61cf",
    PYTHON_LIVE_AUDIT: "707c04d4f1fb22d5633284d16121e74d14aa6eb8",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    RUST_TRANSACTION_EVENTS: "8bbe5c385efb9d97c14c78f40343861582eb7e05",
}

INSPECT_COMMAND = "inspect-transaction-events-env"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

ACTION_INGEST_PRINCIPAL_CHAIN = "INGEST_PRINCIPAL_EXIT_CHAIN"
ACTION_INGEST_PRINCIPAL_RECEIPT = "INGEST_PRINCIPAL_EXIT_RECEIPT"
ACTION_APPLY_PRINCIPAL_EFFECT = "APPLY_PRINCIPAL_EXIT_EFFECT"
ACTION_APPLY_PRINCIPAL_POSITION = "APPLY_PRINCIPAL_EXIT_POSITION"
ACTION_INGEST_SETTLEMENT_CHAIN = "INGEST_SETTLEMENT_CHAIN"
ACTION_INGEST_SETTLEMENT_RECEIPT = "INGEST_SETTLEMENT_RECEIPT"
ACTION_APPLY_SETTLEMENT_EFFECT = "APPLY_SETTLEMENT_EFFECT"
ACTION_FINALIZE_CLOSURE = "FINALIZE_POSITION_CLOSURE"
ACTION_BUILD_OUTCOME = "BUILD_POSITION_OUTCOME"

ACTION_ORDER = (
    ACTION_INGEST_PRINCIPAL_CHAIN,
    ACTION_INGEST_PRINCIPAL_RECEIPT,
    ACTION_APPLY_PRINCIPAL_EFFECT,
    ACTION_APPLY_PRINCIPAL_POSITION,
    ACTION_INGEST_SETTLEMENT_CHAIN,
    ACTION_INGEST_SETTLEMENT_RECEIPT,
    ACTION_APPLY_SETTLEMENT_EFFECT,
    ACTION_FINALIZE_CLOSURE,
    ACTION_BUILD_OUTCOME,
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_principal_receipt_sha256",
    "expected_principal_receipt_sha256",
    "saved_settlement_receipt_sha256",
    "expected_settlement_receipt_sha256",
    "saved_account_absence_proof_sha256",
    "expected_account_absence_proof_sha256",
    "opened_decision_id",
    "principal_exit_decision_id",
    "settlement_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "destination_config_sha256",
    "rpc_endpoint_sha256",
    "principal_signature",
    "settlement_signature",
    "principal_transaction_slot",
    "settlement_transaction_slot",
    "closure_proof_slot",
    "observed_at",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "principal_transaction_snapshot_sha256",
    "settlement_transaction_snapshot_sha256",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "pre_principal_chain_present",
    "pre_principal_receipt_present",
    "pre_principal_effect_present",
    "pre_principal_position_event_present",
    "pre_settlement_chain_present",
    "pre_settlement_receipt_present",
    "pre_settlement_effect_present",
    "pre_closure_proof_present",
    "pre_close_event_present",
    "pre_position_outcome_present",
    "planned_actions",
    "reconciliation_required",
    "private_replay_succeeded",
    "principal_position_next_status",
    "closure_status",
    "position_outcome",
    "private_target_state_sha256",
    "receipt_audit_after_private_replay",
    "live_ledger_audit_after_private_replay",
    "global_receipt_audit_clean_after_private_replay",
    "global_live_ledger_clean_after_private_replay",
    "reconciliation_plan_ready",
    "requires_separate_reconciliation_apply",
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


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


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


def _normalize_observed_at(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("observed_at is required")
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _safe_regular_hash(path: Path) -> str | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"unsafe production database file type: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(path: Path) -> dict[str, str | None]:
    return {
        "database": _safe_regular_hash(path),
        "wal": _safe_regular_hash(Path(str(path) + "-wal")),
        "shm": _safe_regular_hash(Path(str(path) + "-shm")),
    }


def _backup_database(source: Path, destination: Path) -> None:
    uri = f"file:{source.as_posix()}?mode=ro"
    src = sqlite3.connect(uri, uri=True)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _load_reviewed(source: Path) -> tuple[Any, Any, Any, dict[str, Any]]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT final reconciliation dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT final reconciliation dependency mismatch: {relative}"
            )

    principal_module = _load_module(
        source / PRINCIPAL_RECEIPT_TOOL,
        "phase7_exit_final_reconciliation_principal_receipt",
    )
    settlement_module = _load_module(
        source / SETTLEMENT_RECEIPT_TOOL,
        "phase7_exit_final_reconciliation_settlement_receipt",
    )
    absence_module = _load_module(
        source / ACCOUNT_ABSENCE_TOOL,
        "phase7_exit_final_reconciliation_account_absence",
    )

    python_src = source / "python-learner" / "src"
    if str(python_src) not in sys.path:
        sys.path.insert(0, str(python_src))

    from meteora_learner.execution_receipt_audit import audit_execution_receipts
    from meteora_learner.execution_receipt_ingest import ingest_execution_receipt
    from meteora_learner.live_execution_audit import audit_live_execution_ledger
    from meteora_learner.live_execution_effects import apply_live_execution_effect
    from meteora_learner.live_position_closure import finalize_live_position_closure
    from meteora_learner.live_position_ledger import apply_live_position_effect
    from meteora_learner.live_position_outcome import build_live_position_outcome
    from meteora_learner.storage import Storage
    from meteora_learner.transaction_event_ingest import ingest_transaction_events

    runtime = {
        "Storage": Storage,
        "ingest_transaction_events": ingest_transaction_events,
        "ingest_execution_receipt": ingest_execution_receipt,
        "apply_live_execution_effect": apply_live_execution_effect,
        "apply_live_position_effect": apply_live_position_effect,
        "finalize_live_position_closure": finalize_live_position_closure,
        "build_live_position_outcome": build_live_position_outcome,
        "audit_execution_receipts": audit_execution_receipts,
        "audit_live_execution_ledger": audit_live_execution_ledger,
    }
    return principal_module, settlement_module, absence_module, runtime


def _inspect_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("RPC_URL", None)
    env["SOLANA_RPC_URL"] = rpc_url
    return env


def _inspect_transaction(
    *,
    executor_binary: Path,
    signature: str,
    rpc_url: str,
) -> dict[str, Any]:
    completed = subprocess.run(
        [str(executor_binary), INSPECT_COMMAND, signature],
        env=_inspect_env(rpc_url=rpc_url),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError(
            "Phase 7 EXIT final reconciliation transaction inspection failed "
            f"with return code {completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Phase 7 EXIT final reconciliation inspection returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 7 EXIT final reconciliation inspection result is invalid"
        )
    return value


def _validate_snapshot(
    snapshot: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    comparisons = (
        ("signature", "signature"),
        ("slot", "transaction_slot"),
        ("block_time", "block_time"),
        ("network_fee_lamports", "network_fee_lamports"),
        ("compute_units_consumed", "compute_units_consumed"),
        ("succeeded", "transaction_succeeded"),
    )
    for snapshot_field, receipt_field in comparisons:
        if snapshot.get(snapshot_field) != receipt.get(receipt_field):
            raise ValueError(
                f"transaction snapshot {snapshot_field} differs from terminal receipt"
            )

    for snapshot_field, receipt_field in (
        ("events", "event_count"),
        ("add_requests", "add_request_count"),
        ("rebalance_requests", "rebalance_request_count"),
    ):
        value = snapshot.get(snapshot_field)
        if not isinstance(value, list):
            raise ValueError(
                f"transaction snapshot {snapshot_field} is invalid"
            )
        if len(value) != receipt[receipt_field]:
            raise ValueError(
                f"transaction snapshot {snapshot_field} count differs from receipt"
            )

    if _sha256_value(snapshot) != receipt["transaction_snapshot_sha256"]:
        raise ValueError(
            "transaction snapshot digest differs from terminal receipt"
        )


def _receipt_payload(
    receipt: dict[str, Any],
    *,
    decision_id: str,
) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "signature": receipt["signature"],
        "mode": "LIVE",
        "action": "EXIT",
        "pool_address": receipt["pool_address"],
        "intent_status": "CONFIRMED",
        "slot": receipt["transaction_slot"],
        "block_time": receipt["block_time"],
        "network_fee_lamports": receipt["network_fee_lamports"],
        "compute_units_consumed": receipt["compute_units_consumed"],
        "succeeded": True,
        "event_count": receipt["event_count"],
        "add_request_count": receipt["add_request_count"],
        "rebalance_request_count": receipt["rebalance_request_count"],
    }


def _row_exists(
    conn: sqlite3.Connection,
    query: str,
    args: tuple[Any, ...],
) -> bool:
    return conn.execute(query, args).fetchone() is not None


def _pre_state(
    database: Path,
    *,
    principal_decision_id: str,
    principal_signature: str,
    settlement_decision_id: str,
    settlement_signature: str,
    position_address: str,
) -> dict[str, bool]:
    conn = sqlite3.connect(database)
    try:
        return {
            "principal_chain": _row_exists(
                conn,
                "SELECT 1 FROM chain_transaction_snapshots WHERE signature = ?",
                (principal_signature,),
            ),
            "principal_receipt": _row_exists(
                conn,
                "SELECT 1 FROM live_execution_receipts WHERE decision_id = ?",
                (principal_decision_id,),
            ),
            "principal_effect": _row_exists(
                conn,
                "SELECT 1 FROM live_execution_effects WHERE decision_id = ?",
                (principal_decision_id,),
            ),
            "principal_position_event": _row_exists(
                conn,
                "SELECT 1 FROM live_position_events WHERE decision_id = ?",
                (principal_decision_id,),
            ),
            "settlement_chain": _row_exists(
                conn,
                "SELECT 1 FROM chain_transaction_snapshots WHERE signature = ?",
                (settlement_signature,),
            ),
            "settlement_receipt": _row_exists(
                conn,
                "SELECT 1 FROM live_execution_receipts WHERE decision_id = ?",
                (settlement_decision_id,),
            ),
            "settlement_effect": _row_exists(
                conn,
                "SELECT 1 FROM live_execution_effects WHERE decision_id = ?",
                (settlement_decision_id,),
            ),
            "closure_proof": _row_exists(
                conn,
                "SELECT 1 FROM live_position_closure_proofs WHERE decision_id = ?",
                (settlement_decision_id,),
            ),
            "close_event": _row_exists(
                conn,
                "SELECT 1 FROM live_position_events WHERE decision_id = ?",
                (settlement_decision_id,),
            ),
            "position_outcome": _row_exists(
                conn,
                "SELECT 1 FROM live_position_outcomes WHERE position_address = ?",
                (position_address,),
            ),
        }
    finally:
        conn.close()


def _planned_actions(pre: dict[str, bool]) -> list[str]:
    mapping = (
        ("principal_chain", ACTION_INGEST_PRINCIPAL_CHAIN),
        ("principal_receipt", ACTION_INGEST_PRINCIPAL_RECEIPT),
        ("principal_effect", ACTION_APPLY_PRINCIPAL_EFFECT),
        ("principal_position_event", ACTION_APPLY_PRINCIPAL_POSITION),
        ("settlement_chain", ACTION_INGEST_SETTLEMENT_CHAIN),
        ("settlement_receipt", ACTION_INGEST_SETTLEMENT_RECEIPT),
        ("settlement_effect", ACTION_APPLY_SETTLEMENT_EFFECT),
        ("closure_proof", ACTION_FINALIZE_CLOSURE),
        ("position_outcome", ACTION_BUILD_OUTCOME),
    )
    return [action for field, action in mapping if not pre[field]]


def _rows(
    conn: sqlite3.Connection,
    query: str,
    args: tuple[Any, ...],
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    values = conn.execute(query, args).fetchall()
    return [
        {key: value for key, value in dict(row).items() if key != "id"}
        for row in values
    ]


def _target_state(
    database: Path,
    *,
    principal_decision_id: str,
    principal_signature: str,
    settlement_decision_id: str,
    settlement_signature: str,
    position_address: str,
) -> dict[str, Any]:
    conn = sqlite3.connect(database)
    try:
        return {
            "principal_chain_snapshot": _rows(
                conn,
                "SELECT * FROM chain_transaction_snapshots WHERE signature = ?",
                (principal_signature,),
            ),
            "principal_chain_events": _rows(
                conn,
                "SELECT * FROM chain_transaction_events "
                "WHERE signature = ? ORDER BY event_index ASC",
                (principal_signature,),
            ),
            "principal_receipt": _rows(
                conn,
                "SELECT * FROM live_execution_receipts WHERE decision_id = ?",
                (principal_decision_id,),
            ),
            "principal_effect": _rows(
                conn,
                "SELECT * FROM live_execution_effects WHERE decision_id = ?",
                (principal_decision_id,),
            ),
            "principal_position_event": _rows(
                conn,
                "SELECT * FROM live_position_events WHERE decision_id = ?",
                (principal_decision_id,),
            ),
            "settlement_chain_snapshot": _rows(
                conn,
                "SELECT * FROM chain_transaction_snapshots WHERE signature = ?",
                (settlement_signature,),
            ),
            "settlement_chain_events": _rows(
                conn,
                "SELECT * FROM chain_transaction_events "
                "WHERE signature = ? ORDER BY event_index ASC",
                (settlement_signature,),
            ),
            "settlement_receipt": _rows(
                conn,
                "SELECT * FROM live_execution_receipts WHERE decision_id = ?",
                (settlement_decision_id,),
            ),
            "settlement_effect": _rows(
                conn,
                "SELECT * FROM live_execution_effects WHERE decision_id = ?",
                (settlement_decision_id,),
            ),
            "closure_proof": _rows(
                conn,
                "SELECT * FROM live_position_closure_proofs WHERE decision_id = ?",
                (settlement_decision_id,),
            ),
            "close_event": _rows(
                conn,
                "SELECT * FROM live_position_events WHERE decision_id = ?",
                (settlement_decision_id,),
            ),
            "live_position": _rows(
                conn,
                "SELECT * FROM live_positions WHERE position_address = ?",
                (position_address,),
            ),
            "position_outcome": _rows(
                conn,
                "SELECT * FROM live_position_outcomes WHERE position_address = ?",
                (position_address,),
            ),
        }
    finally:
        conn.close()


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ValueError("runtime result did not produce a record")
    return result


def validate_exit_final_reconciliation_plan(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT final reconciliation plan must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"plan_sha256"}:
        raise ValueError(
            "Phase 7 EXIT final reconciliation plan schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT final reconciliation plan format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT final reconciliation plan type"
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
            "Phase 7 EXIT final reconciliation plan lineage mismatch"
        )

    for field in (
        "saved_principal_receipt_sha256",
        "expected_principal_receipt_sha256",
        "saved_settlement_receipt_sha256",
        "expected_settlement_receipt_sha256",
        "saved_account_absence_proof_sha256",
        "expected_account_absence_proof_sha256",
        "destination_config_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "principal_transaction_snapshot_sha256",
        "settlement_transaction_snapshot_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "private_target_state_sha256",
        "plan_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT final reconciliation plan {field} is invalid"
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
                f"Phase 7 EXIT final reconciliation plan {field} is invalid"
            )

    if report["saved_principal_receipt_sha256"] != report[
        "expected_principal_receipt_sha256"
    ]:
        raise ValueError("principal EXIT receipt digest mismatch")
    if report["saved_settlement_receipt_sha256"] != report[
        "expected_settlement_receipt_sha256"
    ]:
        raise ValueError("settlement receipt digest mismatch")
    if report["saved_account_absence_proof_sha256"] != report[
        "expected_account_absence_proof_sha256"
    ]:
        raise ValueError("account-absence proof digest mismatch")
    if report["executor_binary_sha256"] != report[
        "expected_executor_binary_sha256"
    ]:
        raise ValueError("executor trust-root mismatch")
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError("production Pio database changed during planning")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("production Pio WAL changed during planning")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("production Pio SHM changed during planning")

    for field in (
        "opened_decision_id",
        "principal_exit_decision_id",
        "settlement_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "principal_signature",
        "settlement_signature",
        "observed_at",
        "executor_binary_path",
        "pio_database_path",
        "principal_position_next_status",
        "closure_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT final reconciliation plan {field} is invalid"
            )

    if report["principal_exit_decision_id"] == report["settlement_decision_id"]:
        raise ValueError(
            "principal EXIT and settlement decision identities must differ"
        )
    if report["principal_signature"] == report["settlement_signature"]:
        raise ValueError(
            "principal EXIT and settlement signatures must differ"
        )

    for field in (
        "principal_transaction_slot",
        "settlement_transaction_slot",
        "closure_proof_slot",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(
                f"Phase 7 EXIT final reconciliation plan {field} is invalid"
            )
    if report["settlement_transaction_slot"] < report["principal_transaction_slot"]:
        raise ValueError("settlement transaction predates principal EXIT")
    if report["closure_proof_slot"] < report["settlement_transaction_slot"]:
        raise ValueError("closure proof predates settlement transaction")

    for field in (
        "pre_principal_chain_present",
        "pre_principal_receipt_present",
        "pre_principal_effect_present",
        "pre_principal_position_event_present",
        "pre_settlement_chain_present",
        "pre_settlement_receipt_present",
        "pre_settlement_effect_present",
        "pre_closure_proof_present",
        "pre_close_event_present",
        "pre_position_outcome_present",
        "reconciliation_required",
        "private_replay_succeeded",
        "global_receipt_audit_clean_after_private_replay",
        "global_live_ledger_clean_after_private_replay",
        "reconciliation_plan_ready",
        "requires_separate_reconciliation_apply",
        "requires_post_apply_audit",
        "requires_learning_label_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(
                f"Phase 7 EXIT final reconciliation plan {field} is invalid"
            )

    planned = report.get("planned_actions")
    if not isinstance(planned, list):
        raise ValueError("planned_actions must be a list")
    if len(planned) != len(set(planned)):
        raise ValueError("planned_actions contains duplicates")
    positions = [ACTION_ORDER.index(item) for item in planned]
    if any(item not in ACTION_ORDER for item in planned):
        raise ValueError("planned_actions contains unsupported action")
    if positions != sorted(positions):
        raise ValueError("planned_actions are out of order")
    if report["reconciliation_required"] is not bool(planned):
        raise ValueError("reconciliation-required flag mismatch")
    if report["requires_separate_reconciliation_apply"] is not bool(planned):
        raise ValueError("reconciliation-apply flag mismatch")

    if report["private_replay_succeeded"] is not True:
        raise ValueError("private reconciliation replay did not succeed")
    if report["principal_position_next_status"] != "LIQUIDITY_REMOVED":
        raise ValueError("principal EXIT replay did not remove liquidity")
    if report["closure_status"] != "CLOSED":
        raise ValueError("settlement replay did not close position")
    outcome = report.get("position_outcome")
    if not isinstance(outcome, dict):
        raise ValueError("position outcome is invalid")
    if outcome.get("position_address") != report["position_address"]:
        raise ValueError("position outcome address mismatch")
    if outcome.get("closed_decision_id") != report["settlement_decision_id"]:
        raise ValueError("position outcome settlement decision mismatch")
    if outcome.get("label_status") not in {"ATOMIC_ONLY", "VALUED"}:
        raise ValueError("position outcome label status is invalid")

    if not isinstance(report.get("receipt_audit_after_private_replay"), dict):
        raise ValueError("receipt audit is invalid")
    if not isinstance(report.get("live_ledger_audit_after_private_replay"), dict):
        raise ValueError("live-ledger audit is invalid")

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
                f"Phase 7 EXIT final reconciliation plan requires {field}=false"
            )

    if report["reconciliation_plan_ready"] is not True:
        raise ValueError("final reconciliation plan is not ready")
    if report["requires_post_apply_audit"] is not True:
        raise ValueError("final reconciliation requires post-apply audit")
    if report["requires_learning_label_reconciliation"] is not True:
        raise ValueError("closed position still requires learning-label reconciliation")
    if report["requires_separate_phase7_promotion_action"] is not True:
        raise ValueError("Phase 7 promotion boundary was lost")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["plan_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT final reconciliation plan digest mismatch"
        )


def build_exit_final_reconciliation_plan(
    *,
    source_tree: str | Path,
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
    (
        principal_module,
        settlement_module,
        absence_module,
        runtime,
    ) = _load_reviewed(source)

    principal = _load_json(
        saved_principal_receipt_path,
        label="saved Phase 7 principal EXIT terminal receipt",
    )
    principal_module.validate_exit_terminal_receipt(principal)
    settlement = _load_json(
        saved_settlement_receipt_path,
        label="saved Phase 7 EXIT settlement terminal receipt",
    )
    settlement_module.validate_exit_settlement_terminal_receipt(settlement)
    absence = _load_json(
        saved_account_absence_proof_path,
        label="saved Phase 7 EXIT settlement account-absence proof",
    )
    absence_module.validate_exit_settlement_account_absence_proof(absence)

    for expected, actual, label in (
        (
            expected_principal_receipt_sha256,
            principal["receipt_sha256"],
            "principal EXIT terminal receipt",
        ),
        (
            expected_settlement_receipt_sha256,
            settlement["receipt_sha256"],
            "settlement terminal receipt",
        ),
        (
            expected_account_absence_proof_sha256,
            absence["proof_sha256"],
            "account-absence proof",
        ),
    ):
        if not _is_hex_digest(expected, 64) or actual != expected:
            raise ValueError(f"saved {label} digest mismatch")

    for artifact, label in (
        (principal, "principal EXIT terminal receipt"),
        (settlement, "settlement terminal receipt"),
    ):
        if artifact.get("terminal_receipt_status") != "CONFIRMED":
            raise ValueError(f"{label} is not confirmed")
        if artifact.get("transaction_succeeded") is not True:
            raise ValueError(f"{label} did not succeed")
        if artifact.get("confirmation_matches_transaction_snapshot") is not True:
            raise ValueError(f"{label} lost transaction-snapshot binding")
        if artifact.get("automatic_resubmission_performed") is not False:
            raise ValueError(f"{label} unexpectedly auto-resubmitted")
        if artifact.get("production_pio_database_modified") is not False:
            raise ValueError(f"{label} already mutated production Pio database")

    if principal.get("remove_liquidity_event_matches_execution") is not True:
        raise ValueError("principal EXIT receipt lacks matching RemoveLiquidity event")
    if settlement.get("settlement_events_match_request") is not True:
        raise ValueError("settlement receipt event set does not match request")
    if absence.get("position_account_absence_proven") is not True:
        raise ValueError("position account absence has not been proven")
    if absence.get("post_exit_state_reconciliation_ready") is not True:
        raise ValueError("account-absence proof is not reconciliation-ready")
    if absence.get("production_pio_database_modified") is not False:
        raise ValueError("account-absence proof mutated production Pio database")

    binding_fields = (
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "rpc_endpoint_sha256",
    )
    for field in binding_fields:
        if principal.get(field) != settlement.get(field):
            raise ValueError(
                f"principal EXIT and settlement {field} binding mismatch"
            )
        if absence.get(field) != settlement.get(field):
            raise ValueError(
                f"settlement receipt and account-absence {field} binding mismatch"
            )
    if absence.get("destination_config_sha256") != settlement.get(
        "destination_config_sha256"
    ):
        raise ValueError("settlement destination-config binding mismatch")
    if absence.get("settlement_signature") != settlement["signature"]:
        raise ValueError("settlement signature binding mismatch")
    if absence.get("settlement_transaction_slot") != settlement[
        "transaction_slot"
    ]:
        raise ValueError("settlement slot binding mismatch")
    if settlement["transaction_slot"] < principal["transaction_slot"]:
        raise ValueError("settlement transaction predates principal EXIT")
    if absence["closure_rpc_context_slot"] < settlement["transaction_slot"]:
        raise ValueError("account-absence proof predates settlement")

    if _sha256_text(rpc_url) != principal["rpc_endpoint_sha256"]:
        raise ValueError("final reconciliation RPC endpoint mismatch")

    observed = _normalize_observed_at(observed_at)

    binary = Path(executor_binary_path).expanduser()
    if binary.is_symlink():
        raise ValueError("executor binary must not be a symlink")
    binary = binary.resolve(strict=True)
    metadata = os.lstat(binary)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("executor binary must be a regular file")
    binary_sha = _sha256_bytes(binary.read_bytes())
    if not _is_hex_digest(expected_executor_binary_sha256, 64):
        raise ValueError("expected executor binary SHA-256 is invalid")
    if binary_sha != expected_executor_binary_sha256:
        raise ValueError("executor binary differs from external trust root")

    principal_snapshot = _inspect_transaction(
        executor_binary=binary,
        signature=principal["signature"],
        rpc_url=rpc_url,
    )
    settlement_snapshot = _inspect_transaction(
        executor_binary=binary,
        signature=settlement["signature"],
        rpc_url=rpc_url,
    )
    _validate_snapshot(principal_snapshot, principal)
    _validate_snapshot(settlement_snapshot, settlement)

    database = Path(pio_database_path).expanduser()
    if database.is_symlink():
        raise ValueError("Pio database must not be a symlink")
    database = database.resolve(strict=True)
    db_meta = os.lstat(database)
    if not stat.S_ISREG(db_meta.st_mode):
        raise ValueError("Pio database must be a regular file")
    before = _database_state(database)
    if before["database"] is None:
        raise ValueError("Pio database is missing")

    settlement_decision_id = (
        "PHASE7_EXIT_SETTLEMENT:"
        + settlement["saved_request_sha256"]
    )
    if settlement_decision_id == principal["exit_decision_id"]:
        raise ValueError("settlement decision identity collides with principal EXIT")

    pre = _pre_state(
        database,
        principal_decision_id=principal["exit_decision_id"],
        principal_signature=principal["signature"],
        settlement_decision_id=settlement_decision_id,
        settlement_signature=settlement["signature"],
        position_address=principal["position_address"],
    )
    planned = _planned_actions(pre)

    principal_receipt_payload = _receipt_payload(
        principal,
        decision_id=principal["exit_decision_id"],
    )
    settlement_receipt_payload = _receipt_payload(
        settlement,
        decision_id=settlement_decision_id,
    )
    closure_payload = {
        "position": principal["position_address"],
        "closed": True,
        "rpc_context_slot": absence["closure_rpc_context_slot"],
    }

    with tempfile.TemporaryDirectory(
        prefix="pio-phase7-exit-final-reconcile-plan-"
    ) as tmp:
        private_db = Path(tmp) / "pio.db"
        _backup_database(database, private_db)
        storage = runtime["Storage"](private_db)

        runtime["ingest_transaction_events"](
            storage,
            principal_snapshot,
            observed_at=observed,
        )
        principal_receipt_result = runtime["ingest_execution_receipt"](
            storage,
            principal_receipt_payload,
            observed_at=observed,
        )
        if principal_receipt_result.chain_snapshot_reconciled is not True:
            raise ValueError(
                "principal EXIT receipt did not reconcile to chain snapshot"
            )
        principal_effect = runtime["apply_live_execution_effect"](
            storage,
            principal["exit_decision_id"],
        )
        if (
            principal_effect.effect.position_address
            != principal["position_address"]
        ):
            raise ValueError("principal EXIT effect position mismatch")
        principal_position = runtime["apply_live_position_effect"](
            storage,
            principal["exit_decision_id"],
        )
        if principal_position.position_address != principal["position_address"]:
            raise ValueError("principal EXIT position mutation mismatch")
        if principal_position.next_status != "LIQUIDITY_REMOVED":
            raise ValueError(
                "principal EXIT did not produce LIQUIDITY_REMOVED status"
            )

        runtime["ingest_transaction_events"](
            storage,
            settlement_snapshot,
            observed_at=observed,
        )
        settlement_receipt_result = runtime["ingest_execution_receipt"](
            storage,
            settlement_receipt_payload,
            observed_at=observed,
        )
        if settlement_receipt_result.chain_snapshot_reconciled is not True:
            raise ValueError(
                "settlement receipt did not reconcile to chain snapshot"
            )
        settlement_effect = runtime["apply_live_execution_effect"](
            storage,
            settlement_decision_id,
        )
        if (
            settlement_effect.effect.position_address
            != principal["position_address"]
        ):
            raise ValueError("settlement effect position mismatch")

        closure = runtime["finalize_live_position_closure"](
            storage,
            decision_id=settlement_decision_id,
            proof=closure_payload,
        )
        if closure.position_address != principal["position_address"]:
            raise ValueError("closure proof position mismatch")
        if closure.closed is not True:
            raise ValueError("position closure replay did not close position")

        outcome_result = runtime["build_live_position_outcome"](
            storage,
            position_address=principal["position_address"],
        )
        outcome_record = outcome_result.outcome.to_record()
        if outcome_record["closed_decision_id"] != settlement_decision_id:
            raise ValueError("position outcome closed decision mismatch")

        receipt_audit = runtime["audit_execution_receipts"](storage)
        live_audit = runtime["audit_live_execution_ledger"](storage)
        receipt_audit_record = _record(receipt_audit)
        live_audit_record = _record(live_audit)

        target = _target_state(
            private_db,
            principal_decision_id=principal["exit_decision_id"],
            principal_signature=principal["signature"],
            settlement_decision_id=settlement_decision_id,
            settlement_signature=settlement["signature"],
            position_address=principal["position_address"],
        )
        target_sha = _sha256_value(target)

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during final reconciliation planning"
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
        "saved_principal_receipt_sha256": principal["receipt_sha256"],
        "expected_principal_receipt_sha256": (
            expected_principal_receipt_sha256
        ),
        "saved_settlement_receipt_sha256": settlement["receipt_sha256"],
        "expected_settlement_receipt_sha256": (
            expected_settlement_receipt_sha256
        ),
        "saved_account_absence_proof_sha256": absence["proof_sha256"],
        "expected_account_absence_proof_sha256": (
            expected_account_absence_proof_sha256
        ),
        "opened_decision_id": principal["opened_decision_id"],
        "principal_exit_decision_id": principal["exit_decision_id"],
        "settlement_decision_id": settlement_decision_id,
        "pool_address": principal["pool_address"],
        "position_address": principal["position_address"],
        "executor_wallet_pubkey": principal["executor_wallet_pubkey"],
        "destination_config_sha256": settlement[
            "destination_config_sha256"
        ],
        "rpc_endpoint_sha256": principal["rpc_endpoint_sha256"],
        "principal_signature": principal["signature"],
        "settlement_signature": settlement["signature"],
        "principal_transaction_slot": principal["transaction_slot"],
        "settlement_transaction_slot": settlement["transaction_slot"],
        "closure_proof_slot": absence["closure_rpc_context_slot"],
        "observed_at": observed,
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": (
            expected_executor_binary_sha256
        ),
        "principal_transaction_snapshot_sha256": _sha256_value(
            principal_snapshot
        ),
        "settlement_transaction_snapshot_sha256": _sha256_value(
            settlement_snapshot
        ),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "pre_principal_chain_present": pre["principal_chain"],
        "pre_principal_receipt_present": pre["principal_receipt"],
        "pre_principal_effect_present": pre["principal_effect"],
        "pre_principal_position_event_present": pre[
            "principal_position_event"
        ],
        "pre_settlement_chain_present": pre["settlement_chain"],
        "pre_settlement_receipt_present": pre["settlement_receipt"],
        "pre_settlement_effect_present": pre["settlement_effect"],
        "pre_closure_proof_present": pre["closure_proof"],
        "pre_close_event_present": pre["close_event"],
        "pre_position_outcome_present": pre["position_outcome"],
        "planned_actions": planned,
        "reconciliation_required": bool(planned),
        "private_replay_succeeded": True,
        "principal_position_next_status": "LIQUIDITY_REMOVED",
        "closure_status": "CLOSED",
        "position_outcome": outcome_record,
        "private_target_state_sha256": target_sha,
        "receipt_audit_after_private_replay": receipt_audit_record,
        "live_ledger_audit_after_private_replay": live_audit_record,
        "global_receipt_audit_clean_after_private_replay": bool(
            receipt_audit_record.get("clean")
        ),
        "global_live_ledger_clean_after_private_replay": bool(
            live_audit_record.get("clean")
        ),
        "reconciliation_plan_ready": True,
        "requires_separate_reconciliation_apply": bool(planned),
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
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "plan_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_final_reconciliation_plan(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a private-replay plan for the final Phase 7 EXIT lifecycle. "
            "The plan replays the confirmed principal EXIT to LIQUIDITY_REMOVED, "
            "replays confirmed settlement fees/rewards, applies the account-"
            "absence closure proof to CLOSED, and builds the immutable atomic "
            "position outcome on a copied Pio database. Production state is "
            "never mutated by this planning step."
        )
    )
    parser.add_argument("--source-tree", required=True)
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

    report = build_exit_final_reconciliation_plan(
        source_tree=args.source_tree,
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
