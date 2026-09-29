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
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_TRANSACTION_CONFIRMATION_V1"

EXECUTE_ONCE_TOOL = Path(
    "deploy/tools/execute_phase7_controlled_live_transaction_once.py"
)
RUST_CONFIRMATION = Path("rust-executor/src/confirmation.rs")
RUST_EXECUTION_STORE = Path("rust-executor/src/execution_store.rs")
RUST_MAIN = Path("rust-executor/src/main.rs")

REVIEWED_SOURCE_BLOBS = {
    EXECUTE_ONCE_TOOL: "7580321ce1e418f1fa3d612c196748bef3c0e59a",
    RUST_CONFIRMATION: "7bfd3386cf7a99c241073ea24cd529ccab52e8ea",
    RUST_EXECUTION_STORE: "72bcad76da58e84a3900b59657f1c652a0f35edf",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
}

CONFIRM_COMMAND = "execution-confirmation"
STATUS_COMMAND = "execution-intent-status"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_execution_once_sha256",
    "expected_execution_once_sha256",
    "decision_id",
    "signature",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "rpc_endpoint_sha256",
    "pio_database_path",
    "execution_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "execution_database_sha256_before",
    "execution_database_sha256_after",
    "execution_wal_sha256_before",
    "execution_wal_sha256_after",
    "execution_shm_sha256_before",
    "execution_shm_sha256_after",
    "pre_intent_status",
    "pre_signature_matches",
    "pre_error_absent",
    "confirmation_command",
    "confirmation_process_returncode",
    "observation_status",
    "observation_error",
    "confirmation_changed",
    "post_intent_status",
    "post_signature_matches",
    "automatic_resubmission_performed",
    "signing_environment_stripped",
    "pio_database_unchanged",
    "execution_database_mutated",
    "confirmation_pending",
    "transaction_chain_confirmation_observed",
    "transaction_failure_observed",
    "confirmation_recheck_required",
    "execution_receipt_required",
    "live_capital_effect_reconciled",
    "phase7_promotion_separate",
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


def _load_execute_once_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 confirmation dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 7 confirmation dependency mismatch: {relative}")
    return _load_module(
        source / EXECUTE_ONCE_TOOL,
        "phase7_confirmation_execute_once",
    )


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"unsafe file type for confirmation state: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(path: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(path),
        "wal": _regular_hash_or_none(Path(str(path) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(path) + "-shm")),
    }


def _confirmation_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env["SOLANA_RPC_URL"] = rpc_url
    env.pop("RPC_URL", None)
    return env


def _run_json(
    command: list[str],
    *,
    env: dict[str, str],
    allowed_returncodes: set[int],
    timeout: int = 30,
) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode not in allowed_returncodes:
        raise ValueError(
            f"reviewed confirmation command failed with return code {completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("reviewed confirmation command returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("reviewed confirmation command result is invalid")
    return completed.returncode, value


def _intent_status(
    *,
    executor_binary: Path,
    execution_database: Path,
    decision_id: str,
    env: dict[str, str],
) -> dict[str, Any]:
    _, value = _run_json(
        [
            str(executor_binary),
            STATUS_COMMAND,
            str(execution_database),
            decision_id,
        ],
        env=env,
        allowed_returncodes={0},
        timeout=20,
    )
    return value


def _validate_confirmation_command_report(
    report: dict[str, Any],
    *,
    decision_id: str,
    signature: str,
    returncode: int,
) -> tuple[str, str | None]:
    expected = {
        "decision_id",
        "signature",
        "observation",
        "intent_status",
        "changed",
    }
    if set(report) != expected:
        raise ValueError("Phase 7 confirmation report schema mismatch")
    if report.get("decision_id") != decision_id:
        raise ValueError("Phase 7 confirmation decision mismatch")
    if report.get("signature") != signature:
        raise ValueError("Phase 7 confirmation signature mismatch")
    if not isinstance(report.get("changed"), bool):
        raise ValueError("Phase 7 confirmation changed flag is invalid")

    observation = report.get("observation")
    if not isinstance(observation, dict):
        raise ValueError("Phase 7 confirmation observation is invalid")
    status = observation.get("status")
    if status not in {"PENDING", "CONFIRMED", "FAILED"}:
        raise ValueError("Phase 7 confirmation observation status is invalid")

    error = observation.get("error")
    if status == "FAILED":
        if not isinstance(error, str) or not error:
            raise ValueError("Phase 7 failed confirmation requires an error")
        if returncode != 2:
            raise ValueError("Phase 7 failed confirmation must return code 2")
        if report.get("intent_status") != "FAILED" or report["changed"] is not True:
            raise ValueError("Phase 7 failed confirmation state mismatch")
    elif status == "CONFIRMED":
        if error is not None:
            raise ValueError("Phase 7 confirmed observation cannot contain error")
        if returncode != 0:
            raise ValueError("Phase 7 confirmed observation return code mismatch")
        if report.get("intent_status") != "CONFIRMED" or report["changed"] is not True:
            raise ValueError("Phase 7 confirmed state mismatch")
    else:
        if error is not None:
            raise ValueError("Phase 7 pending observation cannot contain error")
        if returncode != 0:
            raise ValueError("Phase 7 pending observation return code mismatch")
        if report.get("intent_status") != "SENT" or report["changed"] is not False:
            raise ValueError("Phase 7 pending state mismatch")

    return status, error


def validate_confirmation_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 confirmation artifact must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"confirmation_sha256"}:
        raise ValueError("Phase 7 confirmation artifact schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 confirmation format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 confirmation artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 confirmation lineage mismatch")

    for field in (
        "saved_execution_once_sha256",
        "expected_execution_once_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "rpc_endpoint_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "execution_database_sha256_before",
        "execution_database_sha256_after",
        "confirmation_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 confirmation {field} is invalid")

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
        "execution_wal_sha256_before",
        "execution_wal_sha256_after",
        "execution_shm_sha256_before",
        "execution_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 7 confirmation {field} is invalid")

    if report["saved_execution_once_sha256"] != report["expected_execution_once_sha256"]:
        raise ValueError("Phase 7 confirmation execute-once digest mismatch")
    if report["executor_binary_sha256"] != report["expected_executor_binary_sha256"]:
        raise ValueError("Phase 7 confirmation executor trust-root mismatch")
    if report.get("confirmation_command") != CONFIRM_COMMAND:
        raise ValueError("Phase 7 confirmation command mismatch")
    if report.get("pre_intent_status") != "SENT":
        raise ValueError("Phase 7 confirmation pre-state must be SENT")
    if report.get("pre_signature_matches") is not True:
        raise ValueError("Phase 7 confirmation requires matching pre-signature")
    if report.get("pre_error_absent") is not True:
        raise ValueError("Phase 7 confirmation requires no pre-existing error")

    status = report.get("observation_status")
    if status not in {"PENDING", "CONFIRMED", "FAILED"}:
        raise ValueError("Phase 7 confirmation observation status is invalid")

    pending = status == "PENDING"
    confirmed = status == "CONFIRMED"
    failed = status == "FAILED"
    if report.get("confirmation_pending") is not pending:
        raise ValueError("Phase 7 confirmation pending flag mismatch")
    if report.get("transaction_chain_confirmation_observed") is not confirmed:
        raise ValueError("Phase 7 confirmation confirmed flag mismatch")
    if report.get("transaction_failure_observed") is not failed:
        raise ValueError("Phase 7 confirmation failure flag mismatch")
    if report.get("confirmation_recheck_required") is not pending:
        raise ValueError("Phase 7 confirmation recheck flag mismatch")
    if report.get("execution_receipt_required") is not (confirmed or failed):
        raise ValueError("Phase 7 confirmation receipt-required flag mismatch")

    expected_post = {
        "PENDING": "SENT",
        "CONFIRMED": "CONFIRMED",
        "FAILED": "FAILED",
    }[status]
    if report.get("post_intent_status") != expected_post:
        raise ValueError("Phase 7 confirmation post-state mismatch")
    expected_changed = status != "PENDING"
    if report.get("confirmation_changed") is not expected_changed:
        raise ValueError("Phase 7 confirmation changed binding mismatch")
    if report.get("confirmation_process_returncode") != (2 if failed else 0):
        raise ValueError("Phase 7 confirmation return code mismatch")
    if failed:
        if not isinstance(report.get("observation_error"), str) or not report[
            "observation_error"
        ]:
            raise ValueError("Phase 7 failed confirmation requires observation error")
    elif report.get("observation_error") is not None:
        raise ValueError("Phase 7 non-failed confirmation cannot contain observation error")

    for field in (
        "post_signature_matches",
        "signing_environment_stripped",
        "pio_database_unchanged",
        "phase7_promotion_separate",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 confirmation requires {field}=true")

    if report.get("automatic_resubmission_performed") is not False:
        raise ValueError(
            "Phase 7 confirmation requires automatic_resubmission_performed=false"
        )
    if report.get("live_capital_effect_reconciled") is not False:
        raise ValueError(
            "Phase 7 confirmation requires live_capital_effect_reconciled=false"
        )
    if report.get("phase7_promotion_persisted") is not False:
        raise ValueError("Phase 7 confirmation requires phase7_promotion_persisted=false")
    if report.get("production_repository_git_mutated") is not False:
        raise ValueError(
            "Phase 7 confirmation requires production_repository_git_mutated=false"
        )
    if report.get("production_pio_database_modified") is not False:
        raise ValueError(
            "Phase 7 confirmation requires production_pio_database_modified=false"
        )

    execution_mutated = report.get("execution_database_mutated")
    if not isinstance(execution_mutated, bool):
        raise ValueError("Phase 7 confirmation execution mutation flag invalid")
    if execution_mutated is not expected_changed:
        raise ValueError("Phase 7 confirmation execution mutation binding mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["confirmation_sha256"] != expected:
        raise ValueError("Phase 7 confirmation digest mismatch")


def reconcile_confirmation(
    *,
    source_tree: str | Path,
    saved_execution_once_path: str | Path,
    expected_execution_once_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    execute_once_module = _load_execute_once_module(source)

    execution = _load_json(
        saved_execution_once_path,
        label="saved Phase 7 execute-once artifact",
    )
    execute_once_module.validate_execution_once_report(execution)
    if not _is_hex_digest(expected_execution_once_sha256, 64):
        raise ValueError("expected Phase 7 execute-once digest is invalid")
    if execution["execution_once_sha256"] != expected_execution_once_sha256:
        raise ValueError("saved Phase 7 execute-once digest mismatch")
    if execution.get("submission_attempted_once") is not True:
        raise ValueError("Phase 7 execute-once artifact has no submission attempt")
    if execution.get("post_intent_status") != "SENT":
        raise ValueError("Phase 7 confirmation requires saved SENT execution")
    if execution.get("automatic_retry_performed") is not False:
        raise ValueError("Phase 7 confirmation refuses auto-retried execution artifact")
    if execution.get("live_capital_movement_confirmed") is not False:
        raise ValueError("Phase 7 confirmation expects unreconciled capital effect")

    if _sha256_text(rpc_url) != execution["rpc_endpoint_sha256"]:
        raise ValueError("confirmation RPC endpoint differs from execute-once artifact")

    binary = Path(execution["executor_binary_path"]).resolve(strict=True)
    binary_sha = _sha256_bytes(binary.read_bytes())
    if binary_sha != execution["executor_binary_sha256"]:
        raise ValueError("executor binary changed after execute-once")
    if binary_sha != execution["expected_executor_binary_sha256"]:
        raise ValueError("executor binary trust-root mismatch")

    pio_database = Path(execution["pio_database_path"]).resolve(strict=True)
    execution_database = Path(execution["execution_database_path"]).resolve(strict=True)
    env = _confirmation_env(rpc_url=rpc_url)

    pre = _intent_status(
        executor_binary=binary,
        execution_database=execution_database,
        decision_id=execution["decision_id"],
        env=env,
    )
    if pre.get("status") != "SENT":
        raise ValueError("Phase 7 confirmation requires current SENT state")
    if pre.get("signature") != execution["signature"]:
        raise ValueError("current SENT signature differs from execute-once artifact")
    if pre.get("error") is not None:
        raise ValueError("current SENT intent has unexpected error")

    pio_before = _database_state(pio_database)
    execution_before = _database_state(execution_database)
    if pio_before["database"] is None or execution_before["database"] is None:
        raise ValueError("confirmation databases are missing")

    returncode, command_report = _run_json(
        [
            str(binary),
            CONFIRM_COMMAND,
            str(execution_database),
            execution["decision_id"],
        ],
        env=env,
        allowed_returncodes={0, 2},
        timeout=30,
    )
    status, error = _validate_confirmation_command_report(
        command_report,
        decision_id=execution["decision_id"],
        signature=execution["signature"],
        returncode=returncode,
    )

    post = _intent_status(
        executor_binary=binary,
        execution_database=execution_database,
        decision_id=execution["decision_id"],
        env=env,
    )
    expected_post = {
        "PENDING": "SENT",
        "CONFIRMED": "CONFIRMED",
        "FAILED": "FAILED",
    }[status]
    if post.get("status") != expected_post:
        raise ValueError("persisted confirmation post-state mismatch")
    if post.get("signature") != execution["signature"]:
        raise ValueError("persisted confirmation signature drifted")

    pio_after = _database_state(pio_database)
    execution_after = _database_state(execution_database)
    if pio_after != pio_before:
        raise ValueError("Pio database changed during confirmation reconciliation")
    expected_changed = status != "PENDING"
    if (execution_after != execution_before) is not expected_changed:
        raise ValueError("execution database mutation does not match confirmation state")

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
        "saved_execution_once_sha256": execution["execution_once_sha256"],
        "expected_execution_once_sha256": expected_execution_once_sha256,
        "decision_id": execution["decision_id"],
        "signature": execution["signature"],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": execution[
            "expected_executor_binary_sha256"
        ],
        "rpc_endpoint_sha256": execution["rpc_endpoint_sha256"],
        "pio_database_path": str(pio_database),
        "execution_database_path": str(execution_database),
        "pio_database_sha256_before": pio_before["database"],
        "pio_database_sha256_after": pio_after["database"],
        "pio_wal_sha256_before": pio_before["wal"],
        "pio_wal_sha256_after": pio_after["wal"],
        "pio_shm_sha256_before": pio_before["shm"],
        "pio_shm_sha256_after": pio_after["shm"],
        "execution_database_sha256_before": execution_before["database"],
        "execution_database_sha256_after": execution_after["database"],
        "execution_wal_sha256_before": execution_before["wal"],
        "execution_wal_sha256_after": execution_after["wal"],
        "execution_shm_sha256_before": execution_before["shm"],
        "execution_shm_sha256_after": execution_after["shm"],
        "pre_intent_status": "SENT",
        "pre_signature_matches": True,
        "pre_error_absent": True,
        "confirmation_command": CONFIRM_COMMAND,
        "confirmation_process_returncode": returncode,
        "observation_status": status,
        "observation_error": error,
        "confirmation_changed": command_report["changed"],
        "post_intent_status": expected_post,
        "post_signature_matches": True,
        "automatic_resubmission_performed": False,
        "signing_environment_stripped": True,
        "pio_database_unchanged": True,
        "execution_database_mutated": expected_changed,
        "confirmation_pending": status == "PENDING",
        "transaction_chain_confirmation_observed": status == "CONFIRMED",
        "transaction_failure_observed": status == "FAILED",
        "confirmation_recheck_required": status == "PENDING",
        "execution_receipt_required": status in {"CONFIRMED", "FAILED"},
        "live_capital_effect_reconciled": False,
        "phase7_promotion_separate": True,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "confirmation_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_confirmation_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Observe and reconcile exactly one persisted Phase 7 transaction "
            "signature after execute-once. This tool strips all signing/live-submit "
            "environment and never retries or resubmits."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-execution-once", required=True)
    parser.add_argument("--expected-execution-once-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = reconcile_confirmation(
        source_tree=args.source_tree,
        saved_execution_once_path=args.saved_execution_once,
        expected_execution_once_sha256=args.expected_execution_once_sha256,
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["transaction_failure_observed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
