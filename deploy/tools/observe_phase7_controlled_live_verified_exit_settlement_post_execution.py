from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_POST_EXECUTION_OBSERVATION_V1"
)

EXECUTION_TOOL = Path(
    "deploy/tools/execute_phase7_controlled_live_verified_exit_settlement_once.py"
)
OBSERVER_SOURCE = Path(
    "rust-executor/src/bin/phase7-exit-confirmation-observer.rs"
)
REVIEWED_SOURCE_BLOBS = {
    EXECUTION_TOOL: "e1b4380df893bdbc83ba6941e4bfa5cb46538b59",
    OBSERVER_SOURCE: "9c7899ea1a02c7cc8d49bc2fe65dd720a31840a5",
}

KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_execution_sha256",
    "expected_execution_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "destination_config_sha256",
    "rpc_endpoint_sha256",
    "request_sha256",
    "final_settlement_transaction_sha256",
    "signed_transaction_sha256",
    "signature",
    "execution_journal_status",
    "execution_rpc_accepted",
    "execution_rpc_error",
    "submission_journal_path_sha256",
    "journal_database_sha256_before",
    "journal_database_sha256_after",
    "journal_wal_sha256_before",
    "journal_wal_sha256_after",
    "journal_shm_sha256_before",
    "journal_shm_sha256_after",
    "journal_request_sha256",
    "journal_final_settlement_transaction_sha256",
    "journal_signed_transaction_sha256",
    "journal_exit_decision_id",
    "journal_signature",
    "journal_status",
    "journal_rpc_error",
    "journal_signed_transaction_sha256_recomputed",
    "journal_row_matches_execution",
    "observer_binary_path",
    "observer_binary_sha256",
    "expected_observer_binary_sha256",
    "observer_binary_hash_matches_expected",
    "observer_binary_hash_trust_external",
    "observation_signature",
    "observation_status",
    "observation_error",
    "observation_commitment",
    "observation_search_transaction_history",
    "observation_only",
    "observer_submission_attempted",
    "observer_automatic_retry_performed",
    "signing_environment_stripped",
    "live_submit_environment_stripped",
    "rpc_endpoint_matches_execution",
    "journal_opened_read_only",
    "journal_unchanged",
    "confirmation_pending",
    "transaction_chain_confirmation_observed",
    "transaction_failure_observed",
    "confirmation_recheck_required",
    "terminal_settlement_receipt_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "failure_recovery_required",
    "automatic_resubmission_performed",
    "live_capital_movement_confirmed",
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


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


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


def _regular_file(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _regular_executable(path: str | Path, *, label: str) -> Path:
    resolved = _regular_file(path, label=label)
    st = os.lstat(resolved)
    if not (st.st_mode & stat.S_IXUSR):
        raise ValueError(f"{label} must be executable")
    return resolved


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"unsafe journal state file type: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(path: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(path),
        "wal": _regular_hash_or_none(Path(str(path) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(path) + "-shm")),
    }


def _load_execution_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT settlement post-execution dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT settlement post-execution dependency mismatch: {relative}"
            )
    return _load_module(
        source / EXECUTION_TOOL,
        "phase7_exit_post_execution_execute_once",
    )


def _observation_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("RPC_URL", None)
    env["SOLANA_RPC_URL"] = rpc_url
    return env


def _run_observer(
    *,
    observer_binary: Path,
    signature: str,
    env: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        [str(observer_binary), signature],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer returned invalid result"
        )
    return value


def _read_journal_row(
    *,
    journal_path: Path,
    request_sha256: str,
) -> dict[str, Any]:
    uri = f"file:{journal_path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise ValueError(
            "failed to open Phase 7 EXIT settlement submission journal read-only"
        ) from exc
    try:
        row = connection.execute(
            """
            SELECT
                request_sha256,
                final_settlement_transaction_sha256,
                signed_transaction_sha256,
                exit_decision_id,
                signature,
                signed_transaction_base64,
                status,
                rpc_error
            FROM phase7_exit_settlement_submission_journal
            WHERE request_sha256 = ?
            LIMIT 1
            """,
            (request_sha256,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError(
            "failed to read Phase 7 EXIT settlement submission journal"
        ) from exc
    finally:
        connection.close()

    if row is None:
        raise ValueError(
            "Phase 7 EXIT settlement submission journal has no matching request"
        )
    (
        row_request,
        final_settlement_transaction_sha256,
        signed_transaction_sha256,
        exit_decision_id,
        signature,
        signed_transaction_base64,
        status,
        rpc_error,
    ) = row
    if not all(
        isinstance(value, str) and value
        for value in (
            row_request,
            final_settlement_transaction_sha256,
            signed_transaction_sha256,
            exit_decision_id,
            signature,
            signed_transaction_base64,
            status,
        )
    ):
        raise ValueError(
            "Phase 7 EXIT settlement submission journal row is invalid"
        )
    if rpc_error is not None and not isinstance(rpc_error, str):
        raise ValueError(
            "Phase 7 EXIT settlement submission journal rpc_error is invalid"
        )
    return {
        "request_sha256": row_request,
        "final_settlement_transaction_sha256": final_settlement_transaction_sha256,
        "signed_transaction_sha256": signed_transaction_sha256,
        "exit_decision_id": exit_decision_id,
        "signature": signature,
        "signed_transaction_base64": signed_transaction_base64,
        "status": status,
        "rpc_error": rpc_error,
    }


def validate_post_execution_observation(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT settlement post-execution observation must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"observation_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution observation schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement post-execution observation format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement post-execution observation type"
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
            "Phase 7 EXIT settlement post-execution observation lineage mismatch"
        )

    for field in (
        "saved_execution_sha256",
        "expected_execution_sha256",
        "rpc_endpoint_sha256",
        "destination_config_sha256",
        "request_sha256",
        "final_settlement_transaction_sha256",
        "signed_transaction_sha256",
        "submission_journal_path_sha256",
        "journal_signed_transaction_sha256_recomputed",
        "observer_binary_sha256",
        "expected_observer_binary_sha256",
        "observation_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement post-execution observation {field} is invalid"
            )

    for field in (
        "journal_database_sha256_before",
        "journal_database_sha256_after",
        "journal_wal_sha256_before",
        "journal_wal_sha256_after",
        "journal_shm_sha256_before",
        "journal_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 7 EXIT settlement post-execution observation {field} is invalid"
            )

    if report["saved_execution_sha256"] != report["expected_execution_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution observation execution digest mismatch"
        )
    if (
        report["observer_binary_sha256"]
        != report["expected_observer_binary_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement post-execution observer trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "signature",
        "execution_journal_status",
        "journal_request_sha256",
        "journal_final_settlement_transaction_sha256",
        "journal_signed_transaction_sha256",
        "journal_exit_decision_id",
        "journal_signature",
        "journal_status",
        "observer_binary_path",
        "observation_signature",
        "observation_status",
        "observation_commitment",
    ):
        value = report.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"Phase 7 EXIT settlement post-execution observation {field} is invalid"
            )

    if report["execution_journal_status"] not in {
        "RPC_ACCEPTED",
        "RPC_UNCERTAIN",
    }:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution execution journal status is invalid"
        )
    if report["journal_status"] != report["execution_journal_status"]:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution journal status binding mismatch"
        )
    if report["observation_status"] not in {
        "PENDING",
        "CONFIRMED",
        "FAILED",
    }:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution chain status is invalid"
        )
    if report["observation_commitment"] != "CONFIRMED":
        raise ValueError(
            "Phase 7 EXIT settlement post-execution commitment mismatch"
        )

    pending = report["observation_status"] == "PENDING"
    confirmed = report["observation_status"] == "CONFIRMED"
    failed = report["observation_status"] == "FAILED"

    if report.get("confirmation_pending") is not pending:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution pending flag mismatch"
        )
    if report.get("transaction_chain_confirmation_observed") is not confirmed:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution confirmed flag mismatch"
        )
    if report.get("transaction_failure_observed") is not failed:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution failure flag mismatch"
        )
    if report.get("confirmation_recheck_required") is not pending:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution recheck flag mismatch"
        )
    if report.get("terminal_settlement_receipt_required") is not (confirmed or failed):
        raise ValueError(
            "Phase 7 EXIT settlement post-execution receipt flag mismatch"
        )
    if report.get("post_close_account_absence_proof_required") is not confirmed:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution closure-proof flag mismatch"
        )
    if report.get("post_exit_state_reconciliation_required") is not confirmed:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution reconciliation flag mismatch"
        )
    if report.get("failure_recovery_required") is not failed:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution recovery flag mismatch"
        )

    if failed:
        if (
            not isinstance(report.get("observation_error"), str)
            or not report["observation_error"]
        ):
            raise ValueError(
                "Phase 7 EXIT settlement failed observation requires error"
            )
    elif report.get("observation_error") is not None:
        raise ValueError(
            "Phase 7 EXIT settlement non-failed observation cannot contain error"
        )

    if report.get("execution_rpc_accepted") is not (
        report["execution_journal_status"] == "RPC_ACCEPTED"
    ):
        raise ValueError(
            "Phase 7 EXIT settlement post-execution RPC acceptance binding mismatch"
        )
    if report["execution_journal_status"] == "RPC_ACCEPTED":
        if report.get("execution_rpc_error") is not None:
            raise ValueError(
                "Phase 7 EXIT settlement accepted execution cannot contain rpc_error"
            )
    elif (
        not isinstance(report.get("execution_rpc_error"), str)
        or not report["execution_rpc_error"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement uncertain execution requires rpc_error"
        )

    for field in (
        "journal_row_matches_execution",
        "observer_binary_hash_matches_expected",
        "observer_binary_hash_trust_external",
        "observation_search_transaction_history",
        "observation_only",
        "signing_environment_stripped",
        "live_submit_environment_stripped",
        "rpc_endpoint_matches_execution",
        "journal_opened_read_only",
        "journal_unchanged",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement post-execution observation requires {field}=true"
            )

    for field in (
        "observer_submission_attempted",
        "observer_automatic_retry_performed",
        "automatic_resubmission_performed",
        "live_capital_movement_confirmed",
        "phase7_promotion_persisted",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement post-execution observation requires {field}=false"
            )

    if report["journal_database_sha256_before"] is None:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution journal database is missing"
        )
    if (
        report["journal_database_sha256_before"]
        != report["journal_database_sha256_after"]
        or report["journal_wal_sha256_before"]
        != report["journal_wal_sha256_after"]
        or report["journal_shm_sha256_before"]
        != report["journal_shm_sha256_after"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement post-execution journal changed during observation"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["observation_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution observation digest mismatch"
        )


def build_post_execution_observation(
    *,
    source_tree: str | Path,
    saved_execution_path: str | Path,
    expected_execution_sha256: str,
    observer_binary_path: str | Path,
    expected_observer_binary_sha256: str,
    rpc_url: str,
    submission_journal_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    execution_module = _load_execution_module(source)

    execution = _load_json(
        saved_execution_path,
        label="saved Phase 7 EXIT settlement verified single execution",
    )
    execution_module.validate_verified_single_execution(execution)
    if not _is_hex_digest(expected_execution_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT settlement execution digest is invalid"
        )
    if execution["execution_sha256"] != expected_execution_sha256:
        raise ValueError(
            "saved Phase 7 EXIT settlement execution digest mismatch"
        )

    for field in (
        "journal_claim_persisted_before_rpc_send",
        "exact_signed_transaction_verified",
        "submission_attempted_once",
        "uncertain_rpc_requires_recovery",
        "signed_verification_valid",
        "signed_verification_matches_request",
        "dedicated_submission_journal_used",
        "journal_mutated",
        "production_pio_database_unchanged",
        "requires_post_settlement_confirmation",
        "requires_post_close_account_absence_proof",
        "requires_post_exit_state_reconciliation",
    ):
        if execution.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement execution lost required boundary {field}"
            )

    if execution.get("automatic_retry_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution refuses auto-retried execution"
        )
    if execution.get("transaction_signing_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution requires externally signed execution"
        )
    if execution.get("live_capital_movement_confirmed") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution expects unconfirmed capital movement"
        )
    if execution.get("production_pio_database_modified") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement execution mutated production Pio database"
        )

    journal_status = execution.get("journal_status")
    if journal_status not in {"RPC_ACCEPTED", "RPC_UNCERTAIN"}:
        raise ValueError(
            "Phase 7 EXIT settlement execution has invalid journal status"
        )
    expected_rpc_accepted = journal_status == "RPC_ACCEPTED"
    if execution.get("rpc_accepted") is not expected_rpc_accepted:
        raise ValueError(
            "Phase 7 EXIT settlement execution RPC acceptance/status mismatch"
        )

    if _sha256_text(rpc_url) != execution["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement post-execution RPC endpoint mismatch"
        )

    journal = _regular_file(
        submission_journal_path,
        label="Phase 7 EXIT settlement submission journal",
    )
    if _sha256_text(str(journal)) != execution[
        "submission_journal_path_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement submission journal path differs from execution artifact"
        )

    before = _database_state(journal)
    if before["database"] is None:
        raise ValueError(
            "Phase 7 EXIT settlement submission journal database is missing"
        )
    row = _read_journal_row(
        journal_path=journal,
        request_sha256=execution["saved_request_sha256"],
    )
    after_read = _database_state(journal)
    if after_read != before:
        raise ValueError(
            "Phase 7 EXIT settlement submission journal changed during read-only lookup"
        )

    bindings = (
        ("request_sha256", "saved_request_sha256"),
        ("final_settlement_transaction_sha256", "final_settlement_transaction_sha256"),
        ("signed_transaction_sha256", "signed_transaction_sha256"),
        ("exit_decision_id", "exit_decision_id"),
        ("signature", "signature"),
        ("status", "journal_status"),
    )
    for row_field, execution_field in bindings:
        if row.get(row_field) != execution.get(execution_field):
            raise ValueError(
                f"Phase 7 EXIT settlement submission journal {row_field} binding mismatch"
            )
    recomputed_signed_sha = _sha256_text(
        row["signed_transaction_base64"]
    )
    if recomputed_signed_sha != row["signed_transaction_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement journal signed transaction digest mismatch"
        )
    if journal_status == "RPC_ACCEPTED":
        if row["rpc_error"] is not None:
            raise ValueError(
                "Phase 7 EXIT settlement accepted journal row cannot contain rpc_error"
            )
    else:
        if (
            not isinstance(row["rpc_error"], str)
            or not row["rpc_error"]
        ):
            raise ValueError(
                "Phase 7 EXIT settlement uncertain journal row requires rpc_error"
            )

    observer = _regular_executable(
        observer_binary_path,
        label="Phase 7 EXIT settlement confirmation observer binary",
    )
    observer_sha = _sha256_bytes(observer.read_bytes())
    if not _is_hex_digest(expected_observer_binary_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT settlement confirmation observer binary digest is invalid"
        )
    if observer_sha != expected_observer_binary_sha256:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer binary differs from external trust root"
        )

    nested = _run_observer(
        observer_binary=observer,
        signature=execution["signature"],
        env=_observation_env(rpc_url=rpc_url),
    )
    expected_nested_fields = {
        "signature",
        "status",
        "error",
        "commitment",
        "search_transaction_history",
        "observation_only",
        "automatic_retry_performed",
        "transaction_submission_attempted",
    }
    if set(nested) != expected_nested_fields:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer schema mismatch"
        )
    if nested.get("signature") != execution["signature"]:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observation signature mismatch"
        )
    status = nested.get("status")
    if status not in {"PENDING", "CONFIRMED", "FAILED"}:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observation status is invalid"
        )
    error = nested.get("error")
    if status == "FAILED":
        if not isinstance(error, str) or not error:
            raise ValueError(
                "Phase 7 EXIT settlement failed chain observation requires error"
            )
    elif error is not None:
        raise ValueError(
            "Phase 7 EXIT settlement non-failed chain observation cannot contain error"
        )
    if nested.get("commitment") != "CONFIRMED":
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer commitment mismatch"
        )
    if nested.get("search_transaction_history") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer must search transaction history"
        )
    if nested.get("observation_only") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer lost read-only boundary"
        )
    if nested.get("automatic_retry_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer retried unexpectedly"
        )
    if nested.get("transaction_submission_attempted") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement confirmation observer attempted submission"
        )

    final_state = _database_state(journal)
    if final_state != before:
        raise ValueError(
            "Phase 7 EXIT settlement submission journal changed during chain observation"
        )

    pending = status == "PENDING"
    confirmed = status == "CONFIRMED"
    failed = status == "FAILED"

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
        "saved_execution_sha256": execution["execution_sha256"],
        "expected_execution_sha256": expected_execution_sha256,
        "opened_decision_id": execution["opened_decision_id"],
        "exit_decision_id": execution["exit_decision_id"],
        "pool_address": execution["pool_address"],
        "position_address": execution["position_address"],
        "executor_wallet_pubkey": execution["executor_wallet_pubkey"],
        "destination_config_sha256": execution["destination_config_sha256"],
        "rpc_endpoint_sha256": execution["rpc_endpoint_sha256"],
        "request_sha256": execution["saved_request_sha256"],
        "final_settlement_transaction_sha256": execution["final_settlement_transaction_sha256"],
        "signed_transaction_sha256": execution["signed_transaction_sha256"],
        "signature": execution["signature"],
        "execution_journal_status": journal_status,
        "execution_rpc_accepted": execution["rpc_accepted"],
        "execution_rpc_error": execution["rpc_error"],
        "submission_journal_path_sha256": execution[
            "submission_journal_path_sha256"
        ],
        "journal_database_sha256_before": before["database"],
        "journal_database_sha256_after": final_state["database"],
        "journal_wal_sha256_before": before["wal"],
        "journal_wal_sha256_after": final_state["wal"],
        "journal_shm_sha256_before": before["shm"],
        "journal_shm_sha256_after": final_state["shm"],
        "journal_request_sha256": row["request_sha256"],
        "journal_final_settlement_transaction_sha256": row[
            "final_settlement_transaction_sha256"
        ],
        "journal_signed_transaction_sha256": row[
            "signed_transaction_sha256"
        ],
        "journal_exit_decision_id": row["exit_decision_id"],
        "journal_signature": row["signature"],
        "journal_status": row["status"],
        "journal_rpc_error": row["rpc_error"],
        "journal_signed_transaction_sha256_recomputed": (
            recomputed_signed_sha
        ),
        "journal_row_matches_execution": True,
        "observer_binary_path": str(observer),
        "observer_binary_sha256": observer_sha,
        "expected_observer_binary_sha256": (
            expected_observer_binary_sha256
        ),
        "observer_binary_hash_matches_expected": True,
        "observer_binary_hash_trust_external": True,
        "observation_signature": nested["signature"],
        "observation_status": status,
        "observation_error": error,
        "observation_commitment": nested["commitment"],
        "observation_search_transaction_history": True,
        "observation_only": True,
        "observer_submission_attempted": False,
        "observer_automatic_retry_performed": False,
        "signing_environment_stripped": True,
        "live_submit_environment_stripped": True,
        "rpc_endpoint_matches_execution": True,
        "journal_opened_read_only": True,
        "journal_unchanged": True,
        "confirmation_pending": pending,
        "transaction_chain_confirmation_observed": confirmed,
        "transaction_failure_observed": failed,
        "confirmation_recheck_required": pending,
        "terminal_settlement_receipt_required": confirmed or failed,
        "post_close_account_absence_proof_required": confirmed,
        "post_exit_state_reconciliation_required": confirmed,
        "failure_recovery_required": failed,
        "automatic_resubmission_performed": False,
        "live_capital_movement_confirmed": False,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "observation_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_post_execution_observation(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Observe the chain status of one submitted Phase 7 EXIT settlement signature "
            "and bind that observation to the exact verified execution artifact "
            "and dedicated submission journal. The tool opens the journal "
            "read-only, strips keypair/live-submit environment, performs no "
            "submission or retry, and does not claim position closure."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-execution", required=True)
    parser.add_argument("--expected-execution-sha256", required=True)
    parser.add_argument("--observer-binary", required=True)
    parser.add_argument("--expected-observer-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--submission-journal", required=True)
    args = parser.parse_args()

    report = build_post_execution_observation(
        source_tree=args.source_tree,
        saved_execution_path=args.saved_execution,
        expected_execution_sha256=args.expected_execution_sha256,
        observer_binary_path=args.observer_binary,
        expected_observer_binary_sha256=(
            args.expected_observer_binary_sha256
        ),
        rpc_url=args.rpc_url,
        submission_journal_path=args.submission_journal,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
