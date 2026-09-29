from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_VERIFIED_SINGLE_EXECUTION_V1"

REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_single_execution_request.py"
)
VERIFICATION_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_signed_transaction_verification.py"
)
SUBMITTER_SOURCE = Path(
    "rust-executor/src/bin/phase7-exit-submit-verified-once.rs"
)
REVIEWED_SOURCE_BLOBS = {
    REQUEST_TOOL: "e454f917d6b86ea7f0edb9d6fe5598f799a4d58f",
    VERIFICATION_TOOL: "f27981e3104f8899738f37c04731e515afceea4a",
    SUBMITTER_SOURCE: "df20ea39f766d13f17ad5b5707e5dffaf3f82619",
}

LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_request_sha256",
    "expected_request_sha256",
    "saved_signed_verification_sha256",
    "expected_signed_verification_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "final_transaction_sha256",
    "signed_transaction_sha256",
    "signature",
    "recent_blockhash",
    "last_valid_block_height",
    "request_current_block_height",
    "submitter_binary_path",
    "submitter_binary_sha256",
    "expected_submitter_binary_sha256",
    "signed_transaction_file_sha256",
    "submission_journal_path_sha256",
    "pio_database_path_sha256",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "journal_database_sha256_before",
    "journal_database_sha256_after",
    "journal_wal_sha256_before",
    "journal_wal_sha256_after",
    "journal_shm_sha256_before",
    "journal_shm_sha256_after",
    "submission_process_returncode",
    "journal_status",
    "journal_claim_persisted_before_rpc_send",
    "execution_current_block_height",
    "block_height_monotonic",
    "blockhash_not_expired_at_execution",
    "rpc_max_retries",
    "automatic_retry_performed",
    "exact_signed_transaction_verified",
    "transaction_signing_performed",
    "submission_attempted_once",
    "rpc_accepted",
    "rpc_error",
    "uncertain_rpc_requires_recovery",
    "signed_verification_valid",
    "signed_verification_matches_request",
    "submitter_binary_hash_matches_expected",
    "submitter_binary_hash_trust_external",
    "runtime_live_submit_opt_in_present",
    "keypair_environment_stripped",
    "rpc_endpoint_matches_request",
    "dedicated_submission_journal_used",
    "journal_mutated",
    "production_pio_database_unchanged",
    "requires_confirmation_receipt_reconciliation",
    "requires_post_exit_closure_proof",
    "requires_post_exit_state_reconciliation",
    "requires_separate_phase7_promotion_action",
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


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT verified execution dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT verified execution dependency mismatch: {relative}"
            )
    request_module = _load_module(
        source / REQUEST_TOOL,
        "phase7_exit_verified_execution_request",
    )
    verification_module = _load_module(
        source / VERIFICATION_TOOL,
        "phase7_exit_verified_execution_signed_verification",
    )
    return request_module, verification_module


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"unsafe state file type: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(path: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(path),
        "wal": _regular_hash_or_none(Path(str(path) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(path) + "-shm")),
    }


def _validate_journal_path(raw: str | Path, *, pio_database: Path) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("Phase 7 EXIT submission journal path must be absolute")
    if candidate.is_symlink():
        raise ValueError("Phase 7 EXIT submission journal path must not be a symlink")
    parent = candidate.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("Phase 7 EXIT submission journal parent must be a directory")

    resolved = parent / candidate.name
    forbidden = {
        pio_database,
        Path(str(pio_database) + "-wal"),
        Path(str(pio_database) + "-shm"),
    }
    if resolved in forbidden:
        raise ValueError(
            "Phase 7 EXIT submission journal must be separate from production Pio database"
        )
    if resolved.exists():
        metadata = os.lstat(resolved)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(
                "Phase 7 EXIT submission journal must be a regular file"
            )
    return resolved


def _execution_env(*, rpc_url: str) -> dict[str, str]:
    if os.environ.get(LIVE_SUBMIT_ENV) != "1":
        raise ValueError(
            f"{LIVE_SUBMIT_ENV}=1 is required for verified EXIT single execution"
        )
    env = dict(os.environ)
    env[LIVE_SUBMIT_ENV] = "1"
    env.pop(KEYPAIR_ENV, None)
    env.pop("RPC_URL", None)
    env["SOLANA_RPC_URL"] = rpc_url
    return env


def _run_submitter(
    *,
    submitter_binary: Path,
    request_path: Path,
    signed_transaction_path: Path,
    journal_path: Path,
    env: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [
            str(submitter_binary),
            str(request_path),
            str(signed_transaction_path),
            str(journal_path),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=45,
    )
    if completed.returncode not in (0, 2):
        raise ValueError(
            "Phase 7 EXIT verified submitter failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Phase 7 EXIT verified submitter returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 7 EXIT verified submitter returned invalid result"
        )
    return completed.returncode, value


def validate_verified_single_execution(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT verified single execution must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"execution_sha256"}:
        raise ValueError(
            "Phase 7 EXIT verified single execution schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT verified single execution format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT verified single execution type"
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
            "Phase 7 EXIT verified single execution lineage mismatch"
        )

    for field in (
        "saved_request_sha256",
        "expected_request_sha256",
        "saved_signed_verification_sha256",
        "expected_signed_verification_sha256",
        "rpc_endpoint_sha256",
        "final_transaction_sha256",
        "signed_transaction_sha256",
        "submitter_binary_sha256",
        "expected_submitter_binary_sha256",
        "signed_transaction_file_sha256",
        "submission_journal_path_sha256",
        "pio_database_path_sha256",
        "execution_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT verified single execution {field} is invalid"
            )

    if report["saved_request_sha256"] != report["expected_request_sha256"]:
        raise ValueError(
            "Phase 7 EXIT verified single execution request digest mismatch"
        )
    if (
        report["saved_signed_verification_sha256"]
        != report["expected_signed_verification_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT verified single execution signed verification digest mismatch"
        )
    if (
        report["submitter_binary_sha256"]
        != report["expected_submitter_binary_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT verified single execution submitter trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "signature",
        "recent_blockhash",
        "submitter_binary_path",
        "journal_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT verified single execution {field} is invalid"
            )

    for field in (
        "last_valid_block_height",
        "request_current_block_height",
        "execution_current_block_height",
        "rpc_max_retries",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT verified single execution {field} is invalid"
            )
    if report["last_valid_block_height"] <= 0:
        raise ValueError(
            "Phase 7 EXIT verified single execution block-height ceiling invalid"
        )
    if (
        report["execution_current_block_height"]
        < report["request_current_block_height"]
    ):
        raise ValueError(
            "Phase 7 EXIT verified single execution block height regressed"
        )
    if (
        report["execution_current_block_height"]
        > report["last_valid_block_height"]
    ):
        raise ValueError(
            "Phase 7 EXIT verified single execution blockhash expired"
        )
    if report["rpc_max_retries"] != 0:
        raise ValueError(
            "Phase 7 EXIT verified single execution requires zero RPC retries"
        )

    for field in (
        "journal_claim_persisted_before_rpc_send",
        "block_height_monotonic",
        "blockhash_not_expired_at_execution",
        "exact_signed_transaction_verified",
        "submission_attempted_once",
        "uncertain_rpc_requires_recovery",
        "signed_verification_valid",
        "signed_verification_matches_request",
        "submitter_binary_hash_matches_expected",
        "submitter_binary_hash_trust_external",
        "runtime_live_submit_opt_in_present",
        "keypair_environment_stripped",
        "rpc_endpoint_matches_request",
        "dedicated_submission_journal_used",
        "journal_mutated",
        "production_pio_database_unchanged",
        "requires_confirmation_receipt_reconciliation",
        "requires_post_exit_closure_proof",
        "requires_post_exit_state_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT verified single execution requires {field}=true"
            )

    if report.get("automatic_retry_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT verified single execution requires automatic_retry_performed=false"
        )
    if report.get("transaction_signing_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT verified single execution requires transaction_signing_performed=false"
        )
    if report.get("live_capital_movement_confirmed") is not False:
        raise ValueError(
            "Phase 7 EXIT verified single execution cannot claim confirmed live capital movement"
        )
    if report.get("phase7_promotion_persisted") is not False:
        raise ValueError(
            "Phase 7 EXIT verified single execution cannot persist Phase 7 promotion"
        )
    if report.get("production_repository_git_mutated") is not False:
        raise ValueError(
            "Phase 7 EXIT verified single execution cannot mutate production repository"
        )
    if report.get("production_pio_database_modified") is not False:
        raise ValueError(
            "Phase 7 EXIT verified single execution cannot mutate production Pio database"
        )

    for field in (
        "rpc_accepted",
        "rpc_error",
    ):
        if field == "rpc_accepted":
            if not isinstance(report.get(field), bool):
                raise ValueError(
                    "Phase 7 EXIT verified single execution rpc_accepted is invalid"
                )
        else:
            if report.get(field) is not None and not isinstance(
                report.get(field), str
            ):
                raise ValueError(
                    "Phase 7 EXIT verified single execution rpc_error is invalid"
                )

    if report["rpc_accepted"]:
        if report["submission_process_returncode"] != 0:
            raise ValueError(
                "accepted Phase 7 EXIT execution must return zero"
            )
        if report["journal_status"] != "RPC_ACCEPTED":
            raise ValueError(
                "accepted Phase 7 EXIT execution journal status mismatch"
            )
        if report["rpc_error"] is not None:
            raise ValueError(
                "accepted Phase 7 EXIT execution cannot carry RPC error"
            )
    else:
        if report["submission_process_returncode"] != 2:
            raise ValueError(
                "uncertain Phase 7 EXIT execution must return two"
            )
        if report["journal_status"] != "RPC_UNCERTAIN":
            raise ValueError(
                "uncertain Phase 7 EXIT execution journal status mismatch"
            )
        if not isinstance(report["rpc_error"], str) or not report["rpc_error"]:
            raise ValueError(
                "uncertain Phase 7 EXIT execution requires RPC error"
            )

    before = (
        report["pio_database_sha256_before"],
        report["pio_wal_sha256_before"],
        report["pio_shm_sha256_before"],
    )
    after = (
        report["pio_database_sha256_after"],
        report["pio_wal_sha256_after"],
        report["pio_shm_sha256_after"],
    )
    if before != after:
        raise ValueError(
            "Phase 7 EXIT verified single execution changed production Pio database state"
        )

    journal_before = (
        report["journal_database_sha256_before"],
        report["journal_wal_sha256_before"],
        report["journal_shm_sha256_before"],
    )
    journal_after = (
        report["journal_database_sha256_after"],
        report["journal_wal_sha256_after"],
        report["journal_shm_sha256_after"],
    )
    if journal_before == journal_after:
        raise ValueError(
            "Phase 7 EXIT verified single execution journal did not change"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["execution_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT verified single execution digest mismatch"
        )


def build_verified_single_execution(
    *,
    source_tree: str | Path,
    saved_request_path: str | Path,
    expected_request_sha256: str,
    saved_signed_verification_path: str | Path,
    expected_signed_verification_sha256: str,
    signed_transaction_path: str | Path,
    submitter_binary_path: str | Path,
    expected_submitter_binary_sha256: str,
    rpc_url: str,
    submission_journal_path: str | Path,
    pio_database_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    request_module, verification_module = _load_reviewed(source)

    request_path = _regular_file(
        saved_request_path,
        label="saved Phase 7 EXIT single-execution request",
    )
    request = _load_json(
        request_path,
        label="saved Phase 7 EXIT single-execution request",
    )
    request_module.validate_exit_single_execution_request(request)
    if not _is_hex_digest(expected_request_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT single-execution request digest is invalid"
        )
    if request["request_sha256"] != expected_request_sha256:
        raise ValueError(
            "saved Phase 7 EXIT single-execution request digest mismatch"
        )

    verification = _load_json(
        saved_signed_verification_path,
        label="saved Phase 7 EXIT signed-transaction verification",
    )
    verification_module.validate_signed_transaction_verification(
        verification
    )
    if not _is_hex_digest(expected_signed_verification_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT signed verification digest is invalid"
        )
    if (
        verification["verification_sha256"]
        != expected_signed_verification_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT signed verification digest mismatch"
        )

    bindings = (
        ("saved_request_sha256", "request_sha256"),
        ("request_final_transaction_sha256", "final_transaction_sha256"),
        ("executor_wallet_pubkey", "executor_wallet_pubkey"),
        ("recent_blockhash", "recent_blockhash"),
    )
    for verification_field, request_field in bindings:
        if verification.get(verification_field) != request.get(request_field):
            raise ValueError(
                f"Phase 7 EXIT signed verification {verification_field} binding mismatch"
            )

    for field in (
        "single_execution_request_ready",
        "exact_transaction_authorization_verified",
        "keypair_identity_verified",
        "blockhash_not_expired",
        "final_transaction_unsigned",
        "exact_simulation_succeeded",
        "runtime_live_submit_opt_in_required",
        "dedicated_submission_journal_required",
        "atomic_first_submission_claim_required",
        "signature_persist_before_rpc_send_required",
        "rpc_max_retries_zero_required",
        "automatic_retry_prohibited",
        "uncertain_rpc_requires_recovery",
        "confirmation_receipt_reconciliation_required",
        "post_exit_closure_proof_required",
        "post_exit_state_reconciliation_required",
        "separate_phase7_promotion_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT execution request lost {field}"
            )

    for field in (
        "exact_signed_exit_transaction_verified",
        "signature_verified",
        "unsigned_message_matches_signed_message",
        "fee_payer_matches_executor",
        "blockhash_matches_request",
        "single_required_signature",
        "signed_transaction_has_one_signature",
        "signature_non_default",
        "verification_only",
    ):
        if verification.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT signed verification lost {field}"
            )

    for field in (
        "transaction_signing_performed",
        "transaction_submission_attempted",
        "automatic_retry_performed",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if verification.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT signed verification unexpectedly reports {field}"
            )

    signed_path = _regular_file(
        signed_transaction_path,
        label="Phase 7 EXIT signed transaction",
    )
    if _sha256_bytes(signed_path.read_bytes()) != verification[
        "signed_transaction_file_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT signed transaction file differs from sealed verification"
        )

    if _sha256_text(rpc_url) != request["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT verified execution RPC endpoint mismatch"
        )

    submitter = _regular_executable(
        submitter_binary_path,
        label="Phase 7 EXIT verified submitter binary",
    )
    submitter_sha = _sha256_bytes(submitter.read_bytes())
    if not _is_hex_digest(expected_submitter_binary_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT verified submitter binary digest is invalid"
        )
    if submitter_sha != expected_submitter_binary_sha256:
        raise ValueError(
            "Phase 7 EXIT verified submitter binary differs from external trust root"
        )

    pio_database = _regular_file(
        pio_database_path,
        label="production Pio database",
    )
    journal = _validate_journal_path(
        submission_journal_path,
        pio_database=pio_database,
    )

    pio_before = _database_state(pio_database)
    journal_before = _database_state(journal)

    returncode, nested = _run_submitter(
        submitter_binary=submitter,
        request_path=request_path,
        signed_transaction_path=signed_path,
        journal_path=journal,
        env=_execution_env(rpc_url=rpc_url),
    )

    pio_after = _database_state(pio_database)
    journal_after = _database_state(journal)

    if pio_after != pio_before:
        raise ValueError(
            "Phase 7 EXIT verified submitter changed production Pio database state"
        )
    if journal_after == journal_before:
        raise ValueError(
            "Phase 7 EXIT verified submitter did not mutate dedicated journal"
        )

    expected_nested_fields = {
        "request_sha256",
        "exit_decision_id",
        "final_transaction_sha256",
        "signed_transaction_sha256",
        "signature",
        "journal_status",
        "journal_claim_persisted_before_rpc_send",
        "current_block_height",
        "last_valid_block_height",
        "blockhash_not_expired_at_execution",
        "rpc_max_retries",
        "automatic_retry_performed",
        "exact_signed_transaction_verified",
        "signing_performed",
        "rpc_accepted",
        "rpc_error",
        "uncertain_rpc_requires_recovery",
        "submission_attempted_once",
    }
    if set(nested) != expected_nested_fields:
        raise ValueError(
            "Phase 7 EXIT verified submitter report schema mismatch"
        )

    nested_bindings = (
        ("request_sha256", request["request_sha256"]),
        ("exit_decision_id", request["exit_decision_id"]),
        ("final_transaction_sha256", request["final_transaction_sha256"]),
        ("signed_transaction_sha256", verification["signed_transaction_sha256"]),
        ("signature", verification["signature"]),
        ("last_valid_block_height", request["last_valid_block_height"]),
    )
    for field, expected in nested_bindings:
        if nested.get(field) != expected:
            raise ValueError(
                f"Phase 7 EXIT verified submitter {field} binding mismatch"
            )

    if nested.get("current_block_height", -1) < request[
        "fresh_current_block_height"
    ]:
        raise ValueError(
            "Phase 7 EXIT execution block height regressed"
        )
    if nested.get("current_block_height", -1) > request[
        "last_valid_block_height"
    ]:
        raise ValueError(
            "Phase 7 EXIT execution blockhash expired"
        )

    for field in (
        "journal_claim_persisted_before_rpc_send",
        "blockhash_not_expired_at_execution",
        "exact_signed_transaction_verified",
        "uncertain_rpc_requires_recovery",
        "submission_attempted_once",
    ):
        if nested.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT verified submitter requires {field}=true"
            )
    if nested.get("rpc_max_retries") != 0:
        raise ValueError(
            "Phase 7 EXIT verified submitter violated zero-retry rule"
        )
    if nested.get("automatic_retry_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT verified submitter performed automatic retry"
        )
    if nested.get("signing_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT verified submitter unexpectedly signed transaction"
        )
    if not isinstance(nested.get("rpc_accepted"), bool):
        raise ValueError(
            "Phase 7 EXIT verified submitter rpc_accepted is invalid"
        )

    rpc_accepted = nested["rpc_accepted"]
    if rpc_accepted:
        if returncode != 0:
            raise ValueError(
                "accepted Phase 7 EXIT submitter returned nonzero status"
            )
        if nested.get("journal_status") != "RPC_ACCEPTED":
            raise ValueError(
                "accepted Phase 7 EXIT submitter journal status mismatch"
            )
        if nested.get("rpc_error") is not None:
            raise ValueError(
                "accepted Phase 7 EXIT submitter unexpectedly reported RPC error"
            )
    else:
        if returncode != 2:
            raise ValueError(
                "uncertain Phase 7 EXIT submitter returned unexpected status"
            )
        if nested.get("journal_status") != "RPC_UNCERTAIN":
            raise ValueError(
                "uncertain Phase 7 EXIT submitter journal status mismatch"
            )
        if not isinstance(nested.get("rpc_error"), str) or not nested[
            "rpc_error"
        ]:
            raise ValueError(
                "uncertain Phase 7 EXIT submitter requires RPC error"
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
        "saved_request_sha256": request["request_sha256"],
        "expected_request_sha256": expected_request_sha256,
        "saved_signed_verification_sha256": verification[
            "verification_sha256"
        ],
        "expected_signed_verification_sha256": (
            expected_signed_verification_sha256
        ),
        "opened_decision_id": request["opened_decision_id"],
        "exit_decision_id": request["exit_decision_id"],
        "pool_address": request["pool_address"],
        "position_address": request["position_address"],
        "executor_wallet_pubkey": request["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": request["rpc_endpoint_sha256"],
        "final_transaction_sha256": request["final_transaction_sha256"],
        "signed_transaction_sha256": verification[
            "signed_transaction_sha256"
        ],
        "signature": verification["signature"],
        "recent_blockhash": request["recent_blockhash"],
        "last_valid_block_height": request["last_valid_block_height"],
        "request_current_block_height": request[
            "fresh_current_block_height"
        ],
        "submitter_binary_path": str(submitter),
        "submitter_binary_sha256": submitter_sha,
        "expected_submitter_binary_sha256": (
            expected_submitter_binary_sha256
        ),
        "signed_transaction_file_sha256": verification[
            "signed_transaction_file_sha256"
        ],
        "submission_journal_path_sha256": _sha256_text(str(journal)),
        "pio_database_path_sha256": _sha256_text(str(pio_database)),
        "pio_database_sha256_before": pio_before["database"],
        "pio_database_sha256_after": pio_after["database"],
        "pio_wal_sha256_before": pio_before["wal"],
        "pio_wal_sha256_after": pio_after["wal"],
        "pio_shm_sha256_before": pio_before["shm"],
        "pio_shm_sha256_after": pio_after["shm"],
        "journal_database_sha256_before": journal_before["database"],
        "journal_database_sha256_after": journal_after["database"],
        "journal_wal_sha256_before": journal_before["wal"],
        "journal_wal_sha256_after": journal_after["wal"],
        "journal_shm_sha256_before": journal_before["shm"],
        "journal_shm_sha256_after": journal_after["shm"],
        "submission_process_returncode": returncode,
        "journal_status": nested["journal_status"],
        "journal_claim_persisted_before_rpc_send": True,
        "execution_current_block_height": nested["current_block_height"],
        "block_height_monotonic": True,
        "blockhash_not_expired_at_execution": True,
        "rpc_max_retries": 0,
        "automatic_retry_performed": False,
        "exact_signed_transaction_verified": True,
        "transaction_signing_performed": False,
        "submission_attempted_once": True,
        "rpc_accepted": rpc_accepted,
        "rpc_error": nested["rpc_error"],
        "uncertain_rpc_requires_recovery": True,
        "signed_verification_valid": True,
        "signed_verification_matches_request": True,
        "submitter_binary_hash_matches_expected": True,
        "submitter_binary_hash_trust_external": True,
        "runtime_live_submit_opt_in_present": True,
        "keypair_environment_stripped": True,
        "rpc_endpoint_matches_request": True,
        "dedicated_submission_journal_used": True,
        "journal_mutated": True,
        "production_pio_database_unchanged": True,
        "requires_confirmation_receipt_reconciliation": True,
        "requires_post_exit_closure_proof": True,
        "requires_post_exit_state_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "live_capital_movement_confirmed": False,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "execution_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_verified_single_execution(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute one externally signed and previously verified Phase 7 "
            "EXIT transaction. The wrapper strips keypair access, requires "
            "explicit live-submit opt-in, uses a dedicated atomic submission "
            "journal, allows zero RPC retries, and requires the production Pio "
            "database to remain unchanged. RPC acceptance is not treated as "
            "confirmation; separate confirmation and closure proof are required."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-request", required=True)
    parser.add_argument("--expected-request-sha256", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--expected-signed-verification-sha256", required=True)
    parser.add_argument("--signed-transaction", required=True)
    parser.add_argument("--submitter-binary", required=True)
    parser.add_argument("--expected-submitter-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--submission-journal", required=True)
    parser.add_argument("--pio-database", required=True)
    args = parser.parse_args()

    report = build_verified_single_execution(
        source_tree=args.source_tree,
        saved_request_path=args.saved_request,
        expected_request_sha256=args.expected_request_sha256,
        saved_signed_verification_path=args.saved_signed_verification,
        expected_signed_verification_sha256=(
            args.expected_signed_verification_sha256
        ),
        signed_transaction_path=args.signed_transaction,
        submitter_binary_path=args.submitter_binary,
        expected_submitter_binary_sha256=(
            args.expected_submitter_binary_sha256
        ),
        rpc_url=args.rpc_url,
        submission_journal_path=args.submission_journal,
        pio_database_path=args.pio_database,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
