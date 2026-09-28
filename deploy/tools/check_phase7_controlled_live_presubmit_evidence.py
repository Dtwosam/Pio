from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_PRESUBMIT_EVIDENCE_GATE_V1"

AUTHORIZATION_READINESS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_authorization_readiness.py"
)
INPUT_PREFLIGHT_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_input_preflight.py"
)
RUST_EXECUTION_GUARD = Path("rust-executor/src/execution_guard.rs")
RUST_DRY_RUN = Path("rust-executor/src/dry_run.rs")
RUST_EXECUTION_STORE = Path("rust-executor/src/execution_store.rs")
RUST_TRANSACTION_GUARD = Path("rust-executor/src/transaction_guard.rs")
RUST_WALLET_GUARD = Path("rust-executor/src/wallet_guard.rs")
RUST_BLOCKHASH = Path("rust-executor/src/blockhash.rs")
RUST_SIMULATION = Path("rust-executor/src/simulation.rs")

REVIEWED_SOURCE_BLOBS = {
    AUTHORIZATION_READINESS_TOOL: "0df067b15e349736aee022c7404c5417c1c1b8b5",
    INPUT_PREFLIGHT_TOOL: "3696ac1b747cedc6bd3e2c10c84c28ad81cfa711",
    RUST_EXECUTION_GUARD: "cb8da4cac3fb231972d8b47a3988d864d723cfb1",
    RUST_DRY_RUN: "b4b463f8cc1f3f8127560d04570f3b4f13e39420",
    RUST_EXECUTION_STORE: "72bcad76da58e84a3900b59657f1c652a0f35edf",
    RUST_TRANSACTION_GUARD: "ca8189735003d4e2f2f97e4c498c4d1d67c0f878",
    RUST_WALLET_GUARD: "4f3b8c061263a5937a216357130d9fb5bbf5bfb2",
    RUST_BLOCKHASH: "792153a0bb1527b16d7f7fabf40fed3752225df4",
    RUST_SIMULATION: "d3224a83b463962b21d8671649755afaad70870b",
}

EXPECTED_STATUS = "SIMULATION_PASSED"
EXPECTED_MODE = "LIVE"
EXPECTED_ACTION = "ENTER"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_authorization_readiness_sha256",
    "fresh_authorization_readiness_sha256",
    "expected_authorization_readiness_sha256",
    "fresh_authorization_readiness_matches_saved",
    "production_repository",
    "pio_database_path",
    "execution_database_path",
    "execution_database_sha256_before",
    "execution_database_sha256_after",
    "execution_wal_sha256_before",
    "execution_wal_sha256_after",
    "execution_shm_sha256_before",
    "execution_shm_sha256_after",
    "execution_database_snapshot_only",
    "execution_database_unchanged",
    "decision_id",
    "mode",
    "action",
    "pool_address",
    "executor_wallet_pubkey",
    "phase7_evidence_status_sha256",
    "phase7_evidence_plan_sha256",
    "input_preflight_sha256",
    "signed_authorization_verification_sha256",
    "controlled_live_config_sha256",
    "proposal_sha256",
    "input_manifest_sha256",
    "execution_request_sha256",
    "risk_config_sha256",
    "transaction_guard_config_sha256",
    "risk_report_sha256",
    "initial_simulation_sha256",
    "transaction_guard_sha256",
    "wallet_authorization_sha256",
    "prepared_transaction_sha256",
    "final_simulation_sha256",
    "execution_intent_snapshot_sha256",
    "execution_intent_status",
    "execution_request_matches_preflight_proposal",
    "risk_accepted",
    "risk_identity_matches_proposal",
    "initial_simulation_succeeded",
    "transaction_guard_accepted",
    "transaction_guard_requires_unsigned",
    "transaction_guard_requires_proposal_pool",
    "transaction_guard_fee_payer_matches_wallet",
    "transaction_guard_single_signature",
    "transaction_guard_transaction_unsigned",
    "wallet_authorization_accepted",
    "wallet_authorization_matches_executor",
    "wallet_authorization_fee_payer_matches_executor",
    "prepared_transaction_unsigned",
    "prepared_transaction_present",
    "prepared_recent_blockhash_present",
    "prepared_last_valid_block_height",
    "prepared_rpc_context_slot",
    "final_simulation_succeeded",
    "final_simulation_rpc_context_slot",
    "signature_absent",
    "error_absent",
    "presubmit_evidence_ready",
    "requires_fresh_blockhash_expiry_recheck",
    "requires_live_submit_feature",
    "requires_runtime_live_submit_opt_in",
    "requires_separate_transaction_execution_gate",
    "requires_confirmation_receipt_reconciliation",
    "requires_separate_phase7_promotion_action",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "phase7_promotion_persisted",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
    "production_execution_database_modified",
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


def _verify_reviewed_source(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 presubmit dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 7 presubmit dependency mismatch: {relative}")
    readiness = _load_module(
        source / AUTHORIZATION_READINESS_TOOL,
        "phase7_presubmit_authorization_readiness",
    )
    preflight = _load_module(
        source / INPUT_PREFLIGHT_TOOL,
        "phase7_presubmit_input_preflight",
    )
    return readiness, preflight


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 7 execution DB sidecar is unsafe: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _validate_database_path(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    before = _database_state(source)
    if before["database"] is None:
        raise ValueError(f"SQLite source disappeared: {source}")
    uri = f"file:{source}?mode=ro"
    if before["wal"] is None:
        uri += "&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        target = sqlite3.connect(destination)
        try:
            connection.backup(target)
        finally:
            target.close()
    finally:
        connection.close()
    after = _database_state(source)
    if after != before:
        raise ValueError(f"SQLite source changed during snapshot: {source}")


def _parse_json_text(raw: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{label} is missing")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must decode to an object")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _load_intent_snapshot(
    *,
    database: Path,
    decision_id: str,
    expected_proposal: dict[str, Any],
    executor_wallet_pubkey: str,
) -> dict[str, Any]:
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute(
            """
            SELECT decision_id, mode, action, pool_address, request_json,
                   status, created_at_unix, updated_at_unix, risk_json,
                   simulation_json, transaction_guard_json,
                   wallet_authorization_json, prepared_transaction_json,
                   final_simulation_json, signature, error
            FROM execution_intents
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("Phase 7 execution-intent table is unavailable") from exc
    finally:
        conn.close()

    if row is None:
        raise ValueError("Phase 7 execution intent is missing")

    (
        row_decision_id,
        mode,
        action,
        pool_address,
        request_raw,
        status,
        created_at_unix,
        updated_at_unix,
        risk_raw,
        simulation_raw,
        guard_raw,
        wallet_raw,
        prepared_raw,
        final_simulation_raw,
        signature,
        error,
    ) = row

    if row_decision_id != decision_id:
        raise ValueError("Phase 7 execution intent decision ID mismatch")
    if mode != EXPECTED_MODE or action != EXPECTED_ACTION:
        raise ValueError("Phase 7 execution intent mode/action mismatch")
    if pool_address != expected_proposal.get("pool_address"):
        raise ValueError("Phase 7 execution intent pool mismatch")
    if status != EXPECTED_STATUS:
        raise ValueError(
            f"Phase 7 execution intent must remain {EXPECTED_STATUS}; got {status}"
        )
    _nonnegative_int(created_at_unix, label="execution created_at_unix")
    _nonnegative_int(updated_at_unix, label="execution updated_at_unix")

    request = _parse_json_text(request_raw, label="execution request_json")
    if set(request) != {"request", "risk_config", "transaction_guard_config"}:
        raise ValueError("Phase 7 execution request safety-input schema mismatch")
    request_payload = request.get("request")
    if not isinstance(request_payload, dict):
        raise ValueError("Phase 7 execution request payload is invalid")
    if set(request_payload) != {"proposal", "transaction_base64"}:
        raise ValueError("Phase 7 execution request payload schema mismatch")
    if request_payload.get("proposal") != expected_proposal:
        raise ValueError("Phase 7 execution request proposal differs from preflight")
    transaction_base64 = request_payload.get("transaction_base64")
    if not isinstance(transaction_base64, str) or not transaction_base64:
        raise ValueError("Phase 7 execution request transaction bytes are missing")

    risk_config = request.get("risk_config")
    transaction_config = request.get("transaction_guard_config")
    if not isinstance(risk_config, dict) or not risk_config:
        raise ValueError("Phase 7 persisted risk config is invalid")
    if not isinstance(transaction_config, dict) or not transaction_config:
        raise ValueError("Phase 7 persisted transaction-guard config is invalid")
    if transaction_config.get("expected_fee_payer") != executor_wallet_pubkey:
        raise ValueError("Phase 7 transaction policy fee payer differs from executor")
    if transaction_config.get("require_unsigned") is not True:
        raise ValueError("Phase 7 transaction policy must require unsigned input")
    if transaction_config.get("require_proposal_pool_account") is not True:
        raise ValueError("Phase 7 transaction policy must bind proposal pool")

    risk = _parse_json_text(risk_raw, label="execution risk_json")
    initial_simulation = _parse_json_text(
        simulation_raw,
        label="execution simulation_json",
    )
    guard = _parse_json_text(
        guard_raw,
        label="execution transaction_guard_json",
    )
    wallet = _parse_json_text(
        wallet_raw,
        label="execution wallet_authorization_json",
    )
    prepared = _parse_json_text(
        prepared_raw,
        label="execution prepared_transaction_json",
    )
    final_simulation = _parse_json_text(
        final_simulation_raw,
        label="execution final_simulation_json",
    )

    if risk.get("accepted") is not True or risk.get("reason") != "approved":
        raise ValueError("Phase 7 persisted risk report is not accepted")
    if risk.get("decision_id") != decision_id:
        raise ValueError("Phase 7 persisted risk decision ID mismatch")
    if risk.get("mode") != EXPECTED_MODE or risk.get("action") != EXPECTED_ACTION:
        raise ValueError("Phase 7 persisted risk identity mismatch")

    if initial_simulation.get("succeeded") is not True:
        raise ValueError("Phase 7 initial simulation is not successful")
    _nonnegative_int(
        initial_simulation.get("rpc_context_slot"),
        label="initial simulation rpc_context_slot",
    )

    if guard.get("accepted") is not True:
        raise ValueError("Phase 7 transaction guard is not accepted")
    if guard.get("fee_payer") != executor_wallet_pubkey:
        raise ValueError("Phase 7 transaction guard fee payer mismatch")
    if guard.get("required_signatures") != 1:
        raise ValueError("Phase 7 transaction guard must require one signature")
    if guard.get("signatures_all_default") is not True:
        raise ValueError("Phase 7 transaction guard found a signed transaction")

    if wallet.get("accepted") is not True:
        raise ValueError("Phase 7 wallet authorization is not accepted")
    if wallet.get("wallet_pubkey") != executor_wallet_pubkey:
        raise ValueError("Phase 7 wallet authorization wallet mismatch")
    if wallet.get("transaction_fee_payer") != executor_wallet_pubkey:
        raise ValueError("Phase 7 wallet authorization fee payer mismatch")

    prepared_base64 = prepared.get("transaction_base64")
    recent_blockhash = prepared.get("recent_blockhash")
    if not isinstance(prepared_base64, str) or not prepared_base64:
        raise ValueError("Phase 7 prepared transaction bytes are missing")
    if not isinstance(recent_blockhash, str) or not recent_blockhash:
        raise ValueError("Phase 7 prepared blockhash is missing")
    last_valid_block_height = _positive_int(
        prepared.get("last_valid_block_height"),
        label="prepared last_valid_block_height",
    )
    prepared_slot = _nonnegative_int(
        prepared.get("rpc_context_slot"),
        label="prepared rpc_context_slot",
    )
    if prepared.get("signatures_all_default") is not True:
        raise ValueError("Phase 7 prepared transaction is already signed")

    if final_simulation.get("succeeded") is not True:
        raise ValueError("Phase 7 exact final simulation is not successful")
    final_slot = _nonnegative_int(
        final_simulation.get("rpc_context_slot"),
        label="final simulation rpc_context_slot",
    )

    if signature is not None:
        raise ValueError("Phase 7 execution intent already has a transaction signature")
    if error is not None:
        raise ValueError("Phase 7 execution intent already has an error")

    snapshot_identity = {
        "decision_id": row_decision_id,
        "mode": mode,
        "action": action,
        "pool_address": pool_address,
        "status": status,
        "created_at_unix": created_at_unix,
        "updated_at_unix": updated_at_unix,
        "request": request,
        "risk": risk,
        "initial_simulation": initial_simulation,
        "transaction_guard": guard,
        "wallet_authorization": wallet,
        "prepared_transaction": prepared,
        "final_simulation": final_simulation,
        "signature": signature,
        "error": error,
    }

    return {
        "decision_id": row_decision_id,
        "mode": mode,
        "action": action,
        "pool_address": pool_address,
        "status": status,
        "request": request,
        "risk_config": risk_config,
        "transaction_guard_config": transaction_config,
        "risk": risk,
        "initial_simulation": initial_simulation,
        "transaction_guard": guard,
        "wallet_authorization": wallet,
        "prepared_transaction": prepared,
        "final_simulation": final_simulation,
        "signature": signature,
        "error": error,
        "last_valid_block_height": last_valid_block_height,
        "prepared_rpc_context_slot": prepared_slot,
        "final_simulation_rpc_context_slot": final_slot,
        "snapshot_sha256": _sha256_value(snapshot_identity),
    }


def validate_phase7_presubmit_evidence_gate(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 presubmit gate must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"presubmit_gate_sha256"}:
        raise ValueError("Phase 7 presubmit gate schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 presubmit gate format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 presubmit gate type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 presubmit gate source lineage mismatch")

    for field in (
        "saved_authorization_readiness_sha256",
        "fresh_authorization_readiness_sha256",
        "expected_authorization_readiness_sha256",
        "execution_database_sha256_before",
        "execution_database_sha256_after",
        "phase7_evidence_status_sha256",
        "phase7_evidence_plan_sha256",
        "input_preflight_sha256",
        "signed_authorization_verification_sha256",
        "controlled_live_config_sha256",
        "proposal_sha256",
        "input_manifest_sha256",
        "execution_request_sha256",
        "risk_config_sha256",
        "transaction_guard_config_sha256",
        "risk_report_sha256",
        "initial_simulation_sha256",
        "transaction_guard_sha256",
        "wallet_authorization_sha256",
        "prepared_transaction_sha256",
        "final_simulation_sha256",
        "execution_intent_snapshot_sha256",
        "presubmit_gate_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 presubmit gate {field} is invalid")

    for field in (
        "execution_wal_sha256_before",
        "execution_wal_sha256_after",
        "execution_shm_sha256_before",
        "execution_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 7 presubmit gate {field} is invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "execution_database_path",
        "decision_id",
        "mode",
        "action",
        "pool_address",
        "executor_wallet_pubkey",
        "execution_intent_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 7 presubmit gate {field} is invalid")

    if report["mode"] != EXPECTED_MODE or report["action"] != EXPECTED_ACTION:
        raise ValueError("Phase 7 presubmit gate mode/action mismatch")
    if report["execution_intent_status"] != EXPECTED_STATUS:
        raise ValueError("Phase 7 presubmit gate requires SIMULATION_PASSED")

    if (
        report["saved_authorization_readiness_sha256"]
        != report["fresh_authorization_readiness_sha256"]
        or report["saved_authorization_readiness_sha256"]
        != report["expected_authorization_readiness_sha256"]
    ):
        raise ValueError("Phase 7 presubmit gate readiness digest mismatch")

    for field in (
        "fresh_authorization_readiness_matches_saved",
        "execution_database_snapshot_only",
        "execution_database_unchanged",
        "execution_request_matches_preflight_proposal",
        "risk_accepted",
        "risk_identity_matches_proposal",
        "initial_simulation_succeeded",
        "transaction_guard_accepted",
        "transaction_guard_requires_unsigned",
        "transaction_guard_requires_proposal_pool",
        "transaction_guard_fee_payer_matches_wallet",
        "transaction_guard_single_signature",
        "transaction_guard_transaction_unsigned",
        "wallet_authorization_accepted",
        "wallet_authorization_matches_executor",
        "wallet_authorization_fee_payer_matches_executor",
        "prepared_transaction_unsigned",
        "prepared_transaction_present",
        "prepared_recent_blockhash_present",
        "final_simulation_succeeded",
        "signature_absent",
        "error_absent",
        "presubmit_evidence_ready",
        "requires_fresh_blockhash_expiry_recheck",
        "requires_live_submit_feature",
        "requires_runtime_live_submit_opt_in",
        "requires_separate_transaction_execution_gate",
        "requires_confirmation_receipt_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 presubmit gate requires {field}=true")

    _positive_int(
        report.get("prepared_last_valid_block_height"),
        label="prepared_last_valid_block_height",
    )
    _nonnegative_int(
        report.get("prepared_rpc_context_slot"),
        label="prepared_rpc_context_slot",
    )
    _nonnegative_int(
        report.get("final_simulation_rpc_context_slot"),
        label="final_simulation_rpc_context_slot",
    )

    if report["execution_database_sha256_before"] != report[
        "execution_database_sha256_after"
    ]:
        raise ValueError("Phase 7 execution DB changed during presubmit audit")
    if report["execution_wal_sha256_before"] != report[
        "execution_wal_sha256_after"
    ]:
        raise ValueError("Phase 7 execution WAL changed during presubmit audit")
    if report["execution_shm_sha256_before"] != report[
        "execution_shm_sha256_after"
    ]:
        raise ValueError("Phase 7 execution SHM changed during presubmit audit")

    for field in (
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
        "production_execution_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 7 presubmit gate requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_value(identity)
    if report["presubmit_gate_sha256"] != expected:
        raise ValueError("Phase 7 presubmit gate digest mismatch")


def build_phase7_presubmit_evidence_gate(
    *,
    repository: str | Path,
    source_tree: str | Path,
    phase6_post_promotion_audit_path: str | Path,
    saved_phase7_evidence_status_path: str | Path,
    saved_phase7_evidence_plan_path: str | Path,
    saved_input_preflight_path: str | Path,
    controlled_live_config_path: str | Path,
    proposal_path: str | Path,
    executor_wallet_pubkey: str,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    saved_authorization_readiness_path: str | Path,
    expected_authorization_readiness_sha256: str,
    execution_database_path: str | Path,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")
    if not _is_hex_digest(expected_authorization_readiness_sha256, 64):
        raise ValueError("expected Phase 7 authorization-readiness SHA-256 is invalid")

    readiness_module, preflight_module = _verify_reviewed_source(source)
    saved_readiness = _load_json(
        saved_authorization_readiness_path,
        label="saved Phase 7 authorization readiness",
    )
    readiness_module.validate_phase7_authorization_readiness(saved_readiness)
    if (
        saved_readiness.get("readiness_sha256")
        != expected_authorization_readiness_sha256
    ):
        raise ValueError("saved Phase 7 authorization-readiness digest mismatch")

    fresh_readiness = readiness_module.build_phase7_authorization_readiness(
        repository=production,
        source_tree=source,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
        saved_phase7_evidence_status_path=saved_phase7_evidence_status_path,
        saved_phase7_evidence_plan_path=saved_phase7_evidence_plan_path,
        saved_input_preflight_path=saved_input_preflight_path,
        controlled_live_config_path=controlled_live_config_path,
        proposal_path=proposal_path,
        executor_wallet_pubkey=executor_wallet_pubkey,
        saved_signed_authorization_verification_path=(
            saved_signed_authorization_verification_path
        ),
        signed_payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        now=now,
    )
    readiness_module.validate_phase7_authorization_readiness(fresh_readiness)
    if fresh_readiness != saved_readiness:
        raise ValueError(
            "fresh Phase 7 authorization readiness differs from saved readiness"
        )
    if fresh_readiness.get("authorization_readiness_ready") is not True:
        raise ValueError("fresh Phase 7 authorization readiness is not ready")
    for field in (
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
    ):
        if fresh_readiness.get(field) is not False:
            raise ValueError(f"fresh Phase 7 readiness unexpectedly authorizes {field}")

    preflight = _load_json(
        saved_input_preflight_path,
        label="saved Phase 7 input preflight",
    )
    preflight_module.validate_phase7_input_preflight(preflight)
    proposal = preflight.get("proposal")
    if not isinstance(proposal, dict):
        raise ValueError("Phase 7 preflight proposal is invalid")
    decision_id = proposal.get("decision_id")
    if not isinstance(decision_id, str) or not decision_id:
        raise ValueError("Phase 7 proposal decision ID is invalid")
    if preflight.get("executor_wallet_pubkey") != executor_wallet_pubkey:
        raise ValueError("Phase 7 executor wallet differs from preflight")

    if fresh_readiness.get("saved_input_preflight_sha256") != preflight.get(
        "input_preflight_sha256"
    ):
        raise ValueError("Phase 7 readiness/preflight binding mismatch")

    execution_database = _validate_database_path(
        execution_database_path,
        label="Phase 7 execution database",
    )
    before = _database_state(execution_database)

    with tempfile.TemporaryDirectory(prefix="pio-phase7-presubmit.") as tmp:
        snapshot = Path(tmp) / "execution.db"
        _snapshot_sqlite(execution_database, snapshot)
        intent = _load_intent_snapshot(
            database=snapshot,
            decision_id=decision_id,
            expected_proposal=proposal,
            executor_wallet_pubkey=executor_wallet_pubkey,
        )

    after = _database_state(execution_database)
    if after != before:
        raise ValueError("production execution DB changed during Phase 7 presubmit audit")

    request = intent["request"]
    guard_config = intent["transaction_guard_config"]
    risk_config = intent["risk_config"]
    risk = intent["risk"]
    initial_simulation = intent["initial_simulation"]
    guard = intent["transaction_guard"]
    wallet = intent["wallet_authorization"]
    prepared = intent["prepared_transaction"]
    final_simulation = intent["final_simulation"]

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
        "saved_authorization_readiness_sha256": saved_readiness[
            "readiness_sha256"
        ],
        "fresh_authorization_readiness_sha256": fresh_readiness[
            "readiness_sha256"
        ],
        "expected_authorization_readiness_sha256": (
            expected_authorization_readiness_sha256
        ),
        "fresh_authorization_readiness_matches_saved": True,
        "production_repository": str(production),
        "pio_database_path": fresh_readiness["pio_database_path"],
        "execution_database_path": str(execution_database),
        "execution_database_sha256_before": before["database"],
        "execution_database_sha256_after": after["database"],
        "execution_wal_sha256_before": before["wal"],
        "execution_wal_sha256_after": after["wal"],
        "execution_shm_sha256_before": before["shm"],
        "execution_shm_sha256_after": after["shm"],
        "execution_database_snapshot_only": True,
        "execution_database_unchanged": True,
        "decision_id": intent["decision_id"],
        "mode": intent["mode"],
        "action": intent["action"],
        "pool_address": intent["pool_address"],
        "executor_wallet_pubkey": executor_wallet_pubkey,
        "phase7_evidence_status_sha256": fresh_readiness[
            "saved_phase7_evidence_status_sha256"
        ],
        "phase7_evidence_plan_sha256": fresh_readiness[
            "saved_phase7_evidence_plan_sha256"
        ],
        "input_preflight_sha256": preflight["input_preflight_sha256"],
        "signed_authorization_verification_sha256": fresh_readiness[
            "saved_signed_authorization_verification_sha256"
        ],
        "controlled_live_config_sha256": preflight[
            "controlled_live_config_sha256"
        ],
        "proposal_sha256": preflight["proposal_sha256"],
        "input_manifest_sha256": preflight["input_manifest_sha256"],
        "execution_request_sha256": _sha256_value(request),
        "risk_config_sha256": _sha256_value(risk_config),
        "transaction_guard_config_sha256": _sha256_value(guard_config),
        "risk_report_sha256": _sha256_value(risk),
        "initial_simulation_sha256": _sha256_value(initial_simulation),
        "transaction_guard_sha256": _sha256_value(guard),
        "wallet_authorization_sha256": _sha256_value(wallet),
        "prepared_transaction_sha256": _sha256_value(prepared),
        "final_simulation_sha256": _sha256_value(final_simulation),
        "execution_intent_snapshot_sha256": intent["snapshot_sha256"],
        "execution_intent_status": intent["status"],
        "execution_request_matches_preflight_proposal": True,
        "risk_accepted": True,
        "risk_identity_matches_proposal": True,
        "initial_simulation_succeeded": True,
        "transaction_guard_accepted": True,
        "transaction_guard_requires_unsigned": True,
        "transaction_guard_requires_proposal_pool": True,
        "transaction_guard_fee_payer_matches_wallet": True,
        "transaction_guard_single_signature": True,
        "transaction_guard_transaction_unsigned": True,
        "wallet_authorization_accepted": True,
        "wallet_authorization_matches_executor": True,
        "wallet_authorization_fee_payer_matches_executor": True,
        "prepared_transaction_unsigned": True,
        "prepared_transaction_present": True,
        "prepared_recent_blockhash_present": True,
        "prepared_last_valid_block_height": intent["last_valid_block_height"],
        "prepared_rpc_context_slot": intent["prepared_rpc_context_slot"],
        "final_simulation_succeeded": True,
        "final_simulation_rpc_context_slot": intent[
            "final_simulation_rpc_context_slot"
        ],
        "signature_absent": True,
        "error_absent": True,
        "presubmit_evidence_ready": True,
        "requires_fresh_blockhash_expiry_recheck": True,
        "requires_live_submit_feature": True,
        "requires_runtime_live_submit_opt_in": True,
        "requires_separate_transaction_execution_gate": True,
        "requires_confirmation_receipt_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "production_execution_database_modified": False,
    }
    report = {
        **identity,
        "presubmit_gate_sha256": _sha256_value(identity),
    }
    validate_phase7_presubmit_evidence_gate(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run the complete Phase 7 controlled-LIVE authorization freshness "
            "chain and inspect one exact persisted Rust execution intent from a "
            "private SQLite snapshot. A ready result proves presubmit evidence "
            "only: it does not sign, submit, enable live-submit, or authorize "
            "live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase6-post-promotion-audit", required=True)
    parser.add_argument("--saved-phase7-evidence-status", required=True)
    parser.add_argument("--saved-phase7-evidence-plan", required=True)
    parser.add_argument("--saved-input-preflight", required=True)
    parser.add_argument("--controlled-live-config", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--executor-wallet-pubkey", required=True)
    parser.add_argument("--saved-signed-authorization-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--saved-authorization-readiness", required=True)
    parser.add_argument("--expected-authorization-readiness-sha256", required=True)
    parser.add_argument("--execution-db", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_phase7_presubmit_evidence_gate(
        repository=args.repo,
        source_tree=args.source_tree,
        phase6_post_promotion_audit_path=args.phase6_post_promotion_audit,
        saved_phase7_evidence_status_path=args.saved_phase7_evidence_status,
        saved_phase7_evidence_plan_path=args.saved_phase7_evidence_plan,
        saved_input_preflight_path=args.saved_input_preflight,
        controlled_live_config_path=args.controlled_live_config,
        proposal_path=args.proposal,
        executor_wallet_pubkey=args.executor_wallet_pubkey,
        saved_signed_authorization_verification_path=(
            args.saved_signed_authorization_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
        saved_authorization_readiness_path=args.saved_authorization_readiness,
        expected_authorization_readiness_sha256=(
            args.expected_authorization_readiness_sha256
        ),
        execution_database_path=args.execution_db,
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
