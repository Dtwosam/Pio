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
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_STATE_RECONCILIATION_PLAN_V1"

RECEIPT_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_execution_receipt.py"
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
PYTHON_RECEIPT_AUDIT = Path(
    "python-learner/src/meteora_learner/execution_receipt_audit.py"
)
PYTHON_LIVE_AUDIT = Path(
    "python-learner/src/meteora_learner/live_execution_audit.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_TRANSACTION_EVENTS = Path("rust-executor/src/transaction_events.rs")

REVIEWED_SOURCE_BLOBS = {
    RECEIPT_TOOL: "b7d096fdf1baeff85bc311a630df5c77dc220ce1",
    PYTHON_STORAGE: "39bcc99413df357b89d261e854861e9e4a3fff23",
    PYTHON_TRANSACTION_INGEST: "160fb0678e42321a4f04d46c3e97fd8580a45abf",
    PYTHON_RECEIPT_INGEST: "947a5233358a80b32d52eb5e753c8a64a5582b26",
    PYTHON_EFFECTS: "1a076002912631b9bccf35a3dfa9818f05786dfb",
    PYTHON_POSITION_LEDGER: "afe0edcaf3dd00c79dae2a068f2d1517e48bd414",
    PYTHON_RECEIPT_AUDIT: "4acded692422181c5f8cb2b58ca89dbac51b61cf",
    PYTHON_LIVE_AUDIT: "707c04d4f1fb22d5633284d16121e74d14aa6eb8",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    RUST_TRANSACTION_EVENTS: "8bbe5c385efb9d97c14c78f40343861582eb7e05",
}

INSPECT_COMMAND = "inspect-transaction-events-env"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

ACTION_INGEST_CHAIN = "INGEST_CHAIN_TRANSACTION_EVENTS"
ACTION_INGEST_RECEIPT = "INGEST_EXECUTION_RECEIPT"
ACTION_APPLY_EFFECT = "APPLY_LIVE_EXECUTION_EFFECT"
ACTION_APPLY_POSITION = "APPLY_LIVE_POSITION_EFFECT"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_execution_receipt_sha256",
    "expected_execution_receipt_sha256",
    "decision_id",
    "signature",
    "pool_address",
    "intent_status",
    "succeeded",
    "observed_at",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "rpc_endpoint_sha256",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "production_source_stable_during_plan",
    "transaction_snapshot_sha256",
    "transaction_snapshot_matches_receipt",
    "pre_chain_snapshot_present",
    "pre_execution_receipt_present",
    "pre_execution_effect_present",
    "pre_position_event_present",
    "planned_actions",
    "reconciliation_required",
    "private_replay_succeeded",
    "expected_position_address",
    "private_target_state_sha256",
    "receipt_audit_after_private_replay",
    "live_ledger_audit_after_private_replay",
    "global_receipt_audit_clean_after_private_replay",
    "global_live_ledger_clean_after_private_replay",
    "reconciliation_plan_ready",
    "requires_separate_reconciliation_apply",
    "requires_post_apply_audit",
    "requires_position_state_reconciliation",
    "requires_learning_label_reconciliation",
    "requires_separate_phase7_promotion_action",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
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


def _load_reviewed_runtime(source: Path) -> tuple[Any, dict[str, Any]]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 reconciliation-plan dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 reconciliation-plan dependency mismatch: {relative}"
            )

    receipt_module = _load_module(
        source / RECEIPT_TOOL,
        "phase7_state_reconciliation_plan_receipt",
    )

    python_src = source / "python-learner" / "src"
    if str(python_src) not in sys.path:
        sys.path.insert(0, str(python_src))

    from meteora_learner.execution_receipt_audit import audit_execution_receipts
    from meteora_learner.execution_receipt_ingest import ingest_execution_receipt
    from meteora_learner.live_execution_audit import audit_live_execution_ledger
    from meteora_learner.live_execution_effects import apply_live_execution_effect
    from meteora_learner.live_position_ledger import apply_live_position_effect
    from meteora_learner.storage import Storage
    from meteora_learner.transaction_event_ingest import ingest_transaction_events

    runtime = {
        "Storage": Storage,
        "ingest_transaction_events": ingest_transaction_events,
        "ingest_execution_receipt": ingest_execution_receipt,
        "apply_live_execution_effect": apply_live_execution_effect,
        "apply_live_position_effect": apply_live_position_effect,
        "audit_execution_receipts": audit_execution_receipts,
        "audit_live_execution_ledger": audit_live_execution_ledger,
    }
    return receipt_module, runtime


def _inspect_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env["SOLANA_RPC_URL"] = rpc_url
    env.pop("RPC_URL", None)
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
            f"reviewed transaction-event inspection failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("transaction-event inspection returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("transaction-event inspection result is invalid")
    return value


def _validate_transaction_snapshot(
    snapshot: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    if snapshot.get("signature") != receipt["signature"]:
        raise ValueError("transaction snapshot signature differs from receipt")
    if snapshot.get("slot") != receipt["slot"]:
        raise ValueError("transaction snapshot slot differs from receipt")
    if snapshot.get("block_time") != receipt["block_time"]:
        raise ValueError("transaction snapshot block_time differs from receipt")
    if snapshot.get("network_fee_lamports") != receipt["network_fee_lamports"]:
        raise ValueError("transaction snapshot network fee differs from receipt")
    if snapshot.get("compute_units_consumed") != receipt["compute_units_consumed"]:
        raise ValueError("transaction snapshot compute units differ from receipt")
    if snapshot.get("succeeded") is not receipt["succeeded"]:
        raise ValueError("transaction snapshot outcome differs from receipt")

    events = snapshot.get("events")
    add_requests = snapshot.get("add_requests")
    rebalance_requests = snapshot.get("rebalance_requests")
    if not isinstance(events, list):
        raise ValueError("transaction snapshot events must be a list")
    if not isinstance(add_requests, list):
        raise ValueError("transaction snapshot add_requests must be a list")
    if not isinstance(rebalance_requests, list):
        raise ValueError("transaction snapshot rebalance_requests must be a list")
    if len(events) != receipt["event_count"]:
        raise ValueError("transaction snapshot event count differs from receipt")
    if len(add_requests) != receipt["add_request_count"]:
        raise ValueError("transaction snapshot add-request count differs from receipt")
    if len(rebalance_requests) != receipt["rebalance_request_count"]:
        raise ValueError(
            "transaction snapshot rebalance-request count differs from receipt"
        )


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


def _row_exists(
    conn: sqlite3.Connection,
    *,
    query: str,
    args: tuple[Any, ...],
) -> bool:
    return conn.execute(query, args).fetchone() is not None


def _pre_state(
    database: Path,
    *,
    decision_id: str,
    signature: str,
) -> dict[str, bool]:
    conn = sqlite3.connect(database)
    try:
        return {
            "chain": _row_exists(
                conn,
                query=(
                    "SELECT 1 FROM chain_transaction_snapshots "
                    "WHERE signature = ?"
                ),
                args=(signature,),
            ),
            "receipt": _row_exists(
                conn,
                query=(
                    "SELECT 1 FROM live_execution_receipts "
                    "WHERE decision_id = ?"
                ),
                args=(decision_id,),
            ),
            "effect": _row_exists(
                conn,
                query=(
                    "SELECT 1 FROM live_execution_effects "
                    "WHERE decision_id = ?"
                ),
                args=(decision_id,),
            ),
            "position_event": _row_exists(
                conn,
                query=(
                    "SELECT 1 FROM live_position_events "
                    "WHERE decision_id = ?"
                ),
                args=(decision_id,),
            ),
        }
    finally:
        conn.close()


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
    decision_id: str,
    signature: str,
    position_address: str | None,
) -> dict[str, Any]:
    conn = sqlite3.connect(database)
    try:
        state = {
            "chain_transaction_snapshot": _rows(
                conn,
                "SELECT * FROM chain_transaction_snapshots WHERE signature = ?",
                (signature,),
            ),
            "chain_transaction_events": _rows(
                conn,
                (
                    "SELECT * FROM chain_transaction_events "
                    "WHERE signature = ? ORDER BY event_index ASC"
                ),
                (signature,),
            ),
            "chain_add_liquidity_requests": _rows(
                conn,
                (
                    "SELECT * FROM chain_add_liquidity_requests "
                    "WHERE signature = ? ORDER BY instruction_index ASC"
                ),
                (signature,),
            ),
            "chain_rebalance_requests": _rows(
                conn,
                (
                    "SELECT * FROM chain_rebalance_requests "
                    "WHERE signature = ? ORDER BY parent_ix_index ASC"
                ),
                (signature,),
            ),
            "live_execution_receipt": _rows(
                conn,
                (
                    "SELECT * FROM live_execution_receipts "
                    "WHERE decision_id = ?"
                ),
                (decision_id,),
            ),
            "live_execution_effect": _rows(
                conn,
                (
                    "SELECT * FROM live_execution_effects "
                    "WHERE decision_id = ?"
                ),
                (decision_id,),
            ),
            "live_position_event": _rows(
                conn,
                (
                    "SELECT * FROM live_position_events "
                    "WHERE decision_id = ?"
                ),
                (decision_id,),
            ),
            "live_position": [],
        }
        if position_address is not None:
            state["live_position"] = _rows(
                conn,
                "SELECT * FROM live_positions WHERE position_address = ?",
                (position_address,),
            )
        return state
    finally:
        conn.close()


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ValueError("audit result did not produce a record")
    return result


def validate_reconciliation_plan(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 reconciliation plan must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"plan_sha256"}:
        raise ValueError("Phase 7 reconciliation plan schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 reconciliation plan format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 reconciliation plan type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 reconciliation plan lineage mismatch")

    for field in (
        "saved_execution_receipt_sha256",
        "expected_execution_receipt_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "rpc_endpoint_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "transaction_snapshot_sha256",
        "private_target_state_sha256",
        "plan_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 reconciliation plan {field} is invalid")

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 7 reconciliation plan {field} is invalid")

    if (
        report["saved_execution_receipt_sha256"]
        != report["expected_execution_receipt_sha256"]
    ):
        raise ValueError("Phase 7 reconciliation plan receipt digest mismatch")
    if report["executor_binary_sha256"] != report["expected_executor_binary_sha256"]:
        raise ValueError("Phase 7 reconciliation plan executor trust-root mismatch")
    if report.get("intent_status") not in {"CONFIRMED", "FAILED"}:
        raise ValueError("Phase 7 reconciliation plan requires terminal receipt")
    if not isinstance(report.get("succeeded"), bool):
        raise ValueError("Phase 7 reconciliation plan succeeded flag is invalid")
    if (report["intent_status"] == "CONFIRMED") is not report["succeeded"]:
        raise ValueError("Phase 7 reconciliation plan outcome binding mismatch")

    allowed_actions = {
        ACTION_INGEST_CHAIN,
        ACTION_INGEST_RECEIPT,
        ACTION_APPLY_EFFECT,
        ACTION_APPLY_POSITION,
    }
    actions = report.get("planned_actions")
    if not isinstance(actions, list) or any(
        not isinstance(item, str) or item not in allowed_actions for item in actions
    ):
        raise ValueError("Phase 7 reconciliation plan actions are invalid")
    if len(actions) != len(set(actions)):
        raise ValueError("Phase 7 reconciliation plan actions contain duplicates")
    if report.get("reconciliation_required") is not bool(actions):
        raise ValueError("Phase 7 reconciliation-required binding mismatch")
    if report["intent_status"] == "FAILED" and ACTION_APPLY_POSITION in actions:
        raise ValueError("failed receipt cannot plan a live-position mutation")

    for field in (
        "production_source_stable_during_plan",
        "transaction_snapshot_matches_receipt",
        "private_replay_succeeded",
        "reconciliation_plan_ready",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 reconciliation plan requires {field}=true")

    required = bool(actions)
    if report.get("requires_separate_reconciliation_apply") is not required:
        raise ValueError("Phase 7 reconciliation apply-required binding mismatch")
    if report.get("requires_post_apply_audit") is not required:
        raise ValueError("Phase 7 reconciliation post-audit binding mismatch")
    position_required = ACTION_APPLY_POSITION in actions
    if report.get("requires_position_state_reconciliation") is not position_required:
        raise ValueError("Phase 7 position-reconciliation binding mismatch")
    label_required = report["intent_status"] == "CONFIRMED"
    if report.get("requires_learning_label_reconciliation") is not label_required:
        raise ValueError("Phase 7 learning-label reconciliation binding mismatch")

    for field in (
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 7 reconciliation plan requires {field}=false")

    for field in (
        "pre_chain_snapshot_present",
        "pre_execution_receipt_present",
        "pre_execution_effect_present",
        "pre_position_event_present",
        "global_receipt_audit_clean_after_private_replay",
        "global_live_ledger_clean_after_private_replay",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(f"Phase 7 reconciliation plan {field} is invalid")

    if not isinstance(report.get("receipt_audit_after_private_replay"), dict):
        raise ValueError("Phase 7 reconciliation plan receipt audit is invalid")
    if not isinstance(report.get("live_ledger_audit_after_private_replay"), dict):
        raise ValueError("Phase 7 reconciliation plan live-ledger audit is invalid")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["plan_sha256"] != expected:
        raise ValueError("Phase 7 reconciliation plan digest mismatch")


def build_reconciliation_plan(
    *,
    source_tree: str | Path,
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
    receipt_module, runtime = _load_reviewed_runtime(source)

    artifact = _load_json(
        saved_execution_receipt_path,
        label="saved Phase 7 execution receipt artifact",
    )
    receipt_module.validate_receipt_artifact(artifact)
    if not _is_hex_digest(expected_execution_receipt_sha256, 64):
        raise ValueError("expected Phase 7 receipt artifact digest is invalid")
    if artifact["receipt_artifact_sha256"] != expected_execution_receipt_sha256:
        raise ValueError("saved Phase 7 receipt artifact digest mismatch")
    if artifact.get("receipt_ready") is not True:
        raise ValueError("saved Phase 7 receipt artifact is not ready")
    if artifact.get("automatic_resubmission_performed") is not False:
        raise ValueError("Phase 7 reconciliation refuses resubmitted execution")
    if artifact.get("phase7_promotion_persisted") is not False:
        raise ValueError("Phase 7 promotion was unexpectedly persisted")

    receipt = artifact.get("receipt")
    if not isinstance(receipt, dict):
        raise ValueError("saved Phase 7 receipt payload is invalid")
    if receipt.get("mode") != "LIVE" or receipt.get("action") != "ENTER":
        raise ValueError("Phase 7 reconciliation plan supports the controlled LIVE ENTER receipt")

    observed_at = _normalize_observed_at(observed_at)

    if _sha256_text(rpc_url) != artifact["rpc_endpoint_sha256"]:
        raise ValueError("reconciliation RPC endpoint differs from receipt artifact")

    binary = Path(executor_binary_path).resolve(strict=True)
    binary_sha = _sha256_bytes(binary.read_bytes())
    if binary_sha != expected_executor_binary_sha256:
        raise ValueError("executor binary differs from external trust root")
    if binary_sha != artifact["executor_binary_sha256"]:
        raise ValueError("executor binary differs from receipt artifact")

    pio_database = Path(pio_database_path).resolve(strict=True)
    if pio_database.is_symlink() or not pio_database.is_file():
        raise ValueError("Pio database must be a regular non-symlink file")

    before = _database_state(pio_database)
    if before["database"] is None:
        raise ValueError("Pio database is missing")

    snapshot = _inspect_transaction(
        executor_binary=binary,
        signature=artifact["signature"],
        rpc_url=rpc_url,
    )
    _validate_transaction_snapshot(snapshot, receipt)

    with tempfile.TemporaryDirectory(prefix="pio-phase7-reconcile-plan-") as tmp:
        private_db = Path(tmp) / "pio.db"
        _backup_database(pio_database, private_db)
        pre = _pre_state(
            private_db,
            decision_id=artifact["decision_id"],
            signature=artifact["signature"],
        )

        storage = runtime["Storage"](private_db)
        runtime["ingest_transaction_events"](
            storage,
            snapshot,
            observed_at=observed_at,
        )
        ingest_result = runtime["ingest_execution_receipt"](
            storage,
            receipt,
            observed_at=observed_at,
        )
        if ingest_result.chain_snapshot_reconciled is not True:
            raise ValueError(
                "private replay receipt did not reconcile to transaction snapshot"
            )

        effect_result = runtime["apply_live_execution_effect"](
            storage,
            artifact["decision_id"],
        )
        effect = effect_result.effect
        position_address = effect.position_address

        if receipt["intent_status"] == "CONFIRMED":
            if not position_address:
                raise ValueError("confirmed ENTER reconciliation is missing position address")
            mutation = runtime["apply_live_position_effect"](
                storage,
                artifact["decision_id"],
            )
            if mutation.position_address != position_address:
                raise ValueError("position mutation differs from derived execution effect")
            if mutation.next_status != "OPEN":
                raise ValueError("confirmed ENTER must reconcile to OPEN live position")
        elif position_address is not None:
            raise ValueError("failed ENTER cannot derive a live position address")

        receipt_audit = runtime["audit_execution_receipts"](storage)
        live_audit = runtime["audit_live_execution_ledger"](storage)
        target = _target_state(
            private_db,
            decision_id=artifact["decision_id"],
            signature=artifact["signature"],
            position_address=position_address,
        )

    after = _database_state(pio_database)
    if after != before:
        raise ValueError("production Pio database changed during reconciliation planning")

    actions: list[str] = []
    if not pre["chain"]:
        actions.append(ACTION_INGEST_CHAIN)
    if not pre["receipt"]:
        actions.append(ACTION_INGEST_RECEIPT)
    if not pre["effect"]:
        actions.append(ACTION_APPLY_EFFECT)
    if receipt["intent_status"] == "CONFIRMED" and not pre["position_event"]:
        actions.append(ACTION_APPLY_POSITION)

    receipt_audit_record = _record(receipt_audit)
    live_audit_record = _record(live_audit)

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
        "saved_execution_receipt_sha256": artifact["receipt_artifact_sha256"],
        "expected_execution_receipt_sha256": expected_execution_receipt_sha256,
        "decision_id": artifact["decision_id"],
        "signature": artifact["signature"],
        "pool_address": artifact["pool_address"],
        "intent_status": receipt["intent_status"],
        "succeeded": receipt["succeeded"],
        "observed_at": observed_at,
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": expected_executor_binary_sha256,
        "rpc_endpoint_sha256": artifact["rpc_endpoint_sha256"],
        "pio_database_path": str(pio_database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "production_source_stable_during_plan": True,
        "transaction_snapshot_sha256": _sha256_value(snapshot),
        "transaction_snapshot_matches_receipt": True,
        "pre_chain_snapshot_present": pre["chain"],
        "pre_execution_receipt_present": pre["receipt"],
        "pre_execution_effect_present": pre["effect"],
        "pre_position_event_present": pre["position_event"],
        "planned_actions": actions,
        "reconciliation_required": bool(actions),
        "private_replay_succeeded": True,
        "expected_position_address": position_address,
        "private_target_state_sha256": _sha256_value(target),
        "receipt_audit_after_private_replay": receipt_audit_record,
        "live_ledger_audit_after_private_replay": live_audit_record,
        "global_receipt_audit_clean_after_private_replay": bool(
            receipt_audit_record.get("clean")
        ),
        "global_live_ledger_clean_after_private_replay": bool(
            live_audit_record.get("clean")
        ),
        "reconciliation_plan_ready": True,
        "requires_separate_reconciliation_apply": bool(actions),
        "requires_post_apply_audit": bool(actions),
        "requires_position_state_reconciliation": (
            ACTION_APPLY_POSITION in actions
        ),
        "requires_learning_label_reconciliation": (
            receipt["intent_status"] == "CONFIRMED"
        ),
        "requires_separate_phase7_promotion_action": True,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "plan_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_reconciliation_plan(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only Phase 7 live-state reconciliation plan from one "
            "terminal execution receipt. The tool inspects the exact transaction, "
            "replays receipt/effect/position ingestion only on a private SQLite "
            "snapshot, and never mutates production or submits another transaction."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-execution-receipt", required=True)
    parser.add_argument("--expected-execution-receipt-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--observed-at", required=True)
    args = parser.parse_args()

    report = build_reconciliation_plan(
        source_tree=args.source_tree,
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
