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
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXECUTION_RECEIPT_V1"

CONFIRMATION_TOOL = Path(
    "deploy/tools/reconcile_phase7_controlled_live_transaction_confirmation.py"
)
EXECUTE_ONCE_TOOL = Path(
    "deploy/tools/execute_phase7_controlled_live_transaction_once.py"
)
RUST_EXECUTION_RECEIPT = Path("rust-executor/src/execution_receipt.rs")
RUST_TRANSACTION_EVENTS = Path("rust-executor/src/transaction_events.rs")
RUST_EXECUTION_STORE = Path("rust-executor/src/execution_store.rs")
RUST_MAIN = Path("rust-executor/src/main.rs")

REVIEWED_SOURCE_BLOBS = {
    CONFIRMATION_TOOL: "a3d445a92ac488a09789a89b8e06bd42242c0f2b",
    EXECUTE_ONCE_TOOL: "7580321ce1e418f1fa3d612c196748bef3c0e59a",
    RUST_EXECUTION_RECEIPT: "440d9f22e26ffcbeb81eb13db480ebf018658209",
    RUST_TRANSACTION_EVENTS: "8bbe5c385efb9d97c14c78f40343861582eb7e05",
    RUST_EXECUTION_STORE: "72bcad76da58e84a3900b59657f1c652a0f35edf",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
}

RECEIPT_COMMAND = "execution-receipt"
STATUS_COMMAND = "execution-intent-status"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

RECEIPT_FIELDS = (
    "decision_id",
    "signature",
    "mode",
    "action",
    "pool_address",
    "intent_status",
    "slot",
    "block_time",
    "network_fee_lamports",
    "compute_units_consumed",
    "succeeded",
    "event_count",
    "add_request_count",
    "rebalance_request_count",
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_confirmation_sha256",
    "expected_confirmation_sha256",
    "saved_execution_once_sha256",
    "expected_execution_once_sha256",
    "decision_id",
    "signature",
    "pool_address",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "rpc_endpoint_sha256",
    "execution_database_path",
    "execution_database_sha256_before",
    "execution_database_sha256_after",
    "execution_wal_sha256_before",
    "execution_wal_sha256_after",
    "execution_shm_sha256_before",
    "execution_shm_sha256_after",
    "pre_intent_status",
    "pre_signature_matches",
    "receipt_command",
    "receipt",
    "receipt_sha256",
    "receipt_terminal_status_matches_confirmation",
    "receipt_signature_matches",
    "receipt_pool_matches_execution",
    "receipt_is_live_enter",
    "receipt_success_matches_confirmation",
    "execution_database_unchanged",
    "signing_environment_stripped",
    "automatic_resubmission_performed",
    "receipt_ready",
    "transaction_chain_confirmation_observed",
    "transaction_failure_observed",
    "live_capital_effect_reconciled",
    "requires_position_state_reconciliation",
    "requires_learning_label_reconciliation",
    "requires_separate_phase7_promotion_action",
    "phase7_promotion_persisted",
    "production_repository_git_mutated",
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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 receipt dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 7 receipt dependency mismatch: {relative}")
    confirmation = _load_module(
        source / CONFIRMATION_TOOL,
        "phase7_receipt_confirmation",
    )
    execution = _load_module(
        source / EXECUTE_ONCE_TOOL,
        "phase7_receipt_execute_once",
    )
    return confirmation, execution


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"unsafe file type for receipt state: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(path: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(path),
        "wal": _regular_hash_or_none(Path(str(path) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(path) + "-shm")),
    }


def _receipt_env(*, rpc_url: str) -> dict[str, str]:
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
    timeout: int = 30,
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"reviewed receipt command failed with return code {completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("reviewed receipt command returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("reviewed receipt command result is invalid")
    return value


def _intent_status(
    *,
    executor_binary: Path,
    execution_database: Path,
    decision_id: str,
    env: dict[str, str],
) -> dict[str, Any]:
    return _run_json(
        [
            str(executor_binary),
            STATUS_COMMAND,
            str(execution_database),
            decision_id,
        ],
        env=env,
        timeout=20,
    )


def _validate_receipt(
    receipt: dict[str, Any],
    *,
    decision_id: str,
    signature: str,
    pool_address: str,
    expected_status: str,
) -> None:
    if set(receipt) != set(RECEIPT_FIELDS):
        raise ValueError("Phase 7 execution receipt schema mismatch")
    if receipt.get("decision_id") != decision_id:
        raise ValueError("Phase 7 receipt decision mismatch")
    if receipt.get("signature") != signature:
        raise ValueError("Phase 7 receipt signature mismatch")
    if receipt.get("pool_address") != pool_address:
        raise ValueError("Phase 7 receipt pool mismatch")
    if receipt.get("mode") != "LIVE":
        raise ValueError("Phase 7 receipt must be LIVE")
    if receipt.get("action") != "ENTER":
        raise ValueError("Phase 7 controlled evidence receipt must be ENTER")
    if receipt.get("intent_status") != expected_status:
        raise ValueError("Phase 7 receipt terminal status mismatch")

    expected_success = expected_status == "CONFIRMED"
    if receipt.get("succeeded") is not expected_success:
        raise ValueError("Phase 7 receipt success outcome mismatch")

    for field in ("slot", "event_count", "add_request_count", "rebalance_request_count"):
        value = receipt.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 7 receipt {field} is invalid")

    for field in ("block_time", "network_fee_lamports", "compute_units_consumed"):
        value = receipt.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError(f"Phase 7 receipt {field} is invalid")


def validate_receipt_artifact(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 receipt artifact must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"receipt_artifact_sha256"}:
        raise ValueError("Phase 7 receipt artifact schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 receipt format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 receipt artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 receipt lineage mismatch")

    for field in (
        "saved_confirmation_sha256",
        "expected_confirmation_sha256",
        "saved_execution_once_sha256",
        "expected_execution_once_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "rpc_endpoint_sha256",
        "execution_database_sha256_before",
        "execution_database_sha256_after",
        "receipt_sha256",
        "receipt_artifact_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 receipt {field} is invalid")

    for field in (
        "execution_wal_sha256_before",
        "execution_wal_sha256_after",
        "execution_shm_sha256_before",
        "execution_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 7 receipt {field} is invalid")

    if report["saved_confirmation_sha256"] != report["expected_confirmation_sha256"]:
        raise ValueError("Phase 7 receipt confirmation digest mismatch")
    if report["saved_execution_once_sha256"] != report["expected_execution_once_sha256"]:
        raise ValueError("Phase 7 receipt execute-once digest mismatch")
    if report["executor_binary_sha256"] != report["expected_executor_binary_sha256"]:
        raise ValueError("Phase 7 receipt executor trust-root mismatch")
    if report.get("receipt_command") != RECEIPT_COMMAND:
        raise ValueError("Phase 7 receipt command mismatch")
    if report.get("pre_intent_status") not in {"CONFIRMED", "FAILED"}:
        raise ValueError("Phase 7 receipt requires terminal pre-state")

    receipt = report.get("receipt")
    if not isinstance(receipt, dict):
        raise ValueError("Phase 7 nested receipt is invalid")
    _validate_receipt(
        receipt,
        decision_id=report["decision_id"],
        signature=report["signature"],
        pool_address=report["pool_address"],
        expected_status=report["pre_intent_status"],
    )
    if report["receipt_sha256"] != _sha256_value(receipt):
        raise ValueError("Phase 7 receipt digest mismatch")

    for field in (
        "pre_signature_matches",
        "receipt_terminal_status_matches_confirmation",
        "receipt_signature_matches",
        "receipt_pool_matches_execution",
        "receipt_is_live_enter",
        "receipt_success_matches_confirmation",
        "execution_database_unchanged",
        "signing_environment_stripped",
        "receipt_ready",
        "requires_position_state_reconciliation",
        "requires_learning_label_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 receipt requires {field}=true")

    if report.get("automatic_resubmission_performed") is not False:
        raise ValueError("Phase 7 receipt requires automatic_resubmission_performed=false")
    if report.get("live_capital_effect_reconciled") is not False:
        raise ValueError("Phase 7 receipt requires live_capital_effect_reconciled=false")
    if report.get("phase7_promotion_persisted") is not False:
        raise ValueError("Phase 7 receipt requires phase7_promotion_persisted=false")
    if report.get("production_repository_git_mutated") is not False:
        raise ValueError("Phase 7 receipt requires production_repository_git_mutated=false")

    expected_confirmed = report["pre_intent_status"] == "CONFIRMED"
    if report.get("transaction_chain_confirmation_observed") is not expected_confirmed:
        raise ValueError("Phase 7 receipt confirmation binding mismatch")
    if report.get("transaction_failure_observed") is not (not expected_confirmed):
        raise ValueError("Phase 7 receipt failure binding mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["receipt_artifact_sha256"] != expected:
        raise ValueError("Phase 7 receipt artifact digest mismatch")


def build_receipt_artifact(
    *,
    source_tree: str | Path,
    saved_confirmation_path: str | Path,
    expected_confirmation_sha256: str,
    saved_execution_once_path: str | Path,
    expected_execution_once_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    confirmation_module, execution_module = _load_reviewed_modules(source)

    confirmation = _load_json(
        saved_confirmation_path,
        label="saved Phase 7 confirmation artifact",
    )
    confirmation_module.validate_confirmation_report(confirmation)
    if not _is_hex_digest(expected_confirmation_sha256, 64):
        raise ValueError("expected Phase 7 confirmation digest is invalid")
    if confirmation["confirmation_sha256"] != expected_confirmation_sha256:
        raise ValueError("saved Phase 7 confirmation digest mismatch")
    if confirmation.get("observation_status") not in {"CONFIRMED", "FAILED"}:
        raise ValueError("Phase 7 receipt requires terminal confirmation outcome")
    if confirmation.get("execution_receipt_required") is not True:
        raise ValueError("Phase 7 confirmation does not require execution receipt")
    if confirmation.get("automatic_resubmission_performed") is not False:
        raise ValueError("Phase 7 receipt refuses resubmitted confirmation artifact")

    execution = _load_json(
        saved_execution_once_path,
        label="saved Phase 7 execute-once artifact",
    )
    execution_module.validate_execution_once_report(execution)
    if not _is_hex_digest(expected_execution_once_sha256, 64):
        raise ValueError("expected Phase 7 execute-once digest is invalid")
    if execution["execution_once_sha256"] != expected_execution_once_sha256:
        raise ValueError("saved Phase 7 execute-once digest mismatch")
    if confirmation["saved_execution_once_sha256"] != execution["execution_once_sha256"]:
        raise ValueError("confirmation/execute-once binding mismatch")
    if confirmation["decision_id"] != execution["decision_id"]:
        raise ValueError("confirmation/execute-once decision mismatch")
    if confirmation["signature"] != execution["signature"]:
        raise ValueError("confirmation/execute-once signature mismatch")

    if _sha256_text(rpc_url) != execution["rpc_endpoint_sha256"]:
        raise ValueError("receipt RPC endpoint differs from execute-once artifact")

    binary = Path(execution["executor_binary_path"]).resolve(strict=True)
    binary_sha = _sha256_bytes(binary.read_bytes())
    if binary_sha != execution["executor_binary_sha256"]:
        raise ValueError("executor binary changed after execute-once")
    if binary_sha != execution["expected_executor_binary_sha256"]:
        raise ValueError("executor binary trust-root mismatch")

    execution_database = Path(execution["execution_database_path"]).resolve(strict=True)
    env = _receipt_env(rpc_url=rpc_url)

    pre = _intent_status(
        executor_binary=binary,
        execution_database=execution_database,
        decision_id=execution["decision_id"],
        env=env,
    )
    expected_status = confirmation["post_intent_status"]
    if pre.get("status") != expected_status:
        raise ValueError("Phase 7 receipt current intent status differs from confirmation")
    if pre.get("signature") != execution["signature"]:
        raise ValueError("Phase 7 receipt current signature differs from execution artifact")

    before = _database_state(execution_database)
    if before["database"] is None:
        raise ValueError("execution database is missing")

    receipt = _run_json(
        [
            str(binary),
            RECEIPT_COMMAND,
            str(execution_database),
            execution["decision_id"],
        ],
        env=env,
        timeout=30,
    )
    _validate_receipt(
        receipt,
        decision_id=execution["decision_id"],
        signature=execution["signature"],
        pool_address=execution["pool_address"],
        expected_status=expected_status,
    )

    after = _database_state(execution_database)
    if after != before:
        raise ValueError("execution database changed during receipt construction")

    confirmed = expected_status == "CONFIRMED"
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
        "saved_confirmation_sha256": confirmation["confirmation_sha256"],
        "expected_confirmation_sha256": expected_confirmation_sha256,
        "saved_execution_once_sha256": execution["execution_once_sha256"],
        "expected_execution_once_sha256": expected_execution_once_sha256,
        "decision_id": execution["decision_id"],
        "signature": execution["signature"],
        "pool_address": execution["pool_address"],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": execution[
            "expected_executor_binary_sha256"
        ],
        "rpc_endpoint_sha256": execution["rpc_endpoint_sha256"],
        "execution_database_path": str(execution_database),
        "execution_database_sha256_before": before["database"],
        "execution_database_sha256_after": after["database"],
        "execution_wal_sha256_before": before["wal"],
        "execution_wal_sha256_after": after["wal"],
        "execution_shm_sha256_before": before["shm"],
        "execution_shm_sha256_after": after["shm"],
        "pre_intent_status": expected_status,
        "pre_signature_matches": True,
        "receipt_command": RECEIPT_COMMAND,
        "receipt": receipt,
        "receipt_sha256": _sha256_value(receipt),
        "receipt_terminal_status_matches_confirmation": True,
        "receipt_signature_matches": True,
        "receipt_pool_matches_execution": True,
        "receipt_is_live_enter": True,
        "receipt_success_matches_confirmation": True,
        "execution_database_unchanged": True,
        "signing_environment_stripped": True,
        "automatic_resubmission_performed": False,
        "receipt_ready": True,
        "transaction_chain_confirmation_observed": confirmed,
        "transaction_failure_observed": not confirmed,
        "live_capital_effect_reconciled": False,
        "requires_position_state_reconciliation": True,
        "requires_learning_label_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
    }
    report = {
        **identity,
        "receipt_artifact_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_receipt_artifact(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only Phase 7 execution receipt after terminal transaction "
            "confirmation. This tool strips signing/live-submit environment and "
            "never submits, retries, or promotes Phase 7."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-confirmation", required=True)
    parser.add_argument("--expected-confirmation-sha256", required=True)
    parser.add_argument("--saved-execution-once", required=True)
    parser.add_argument("--expected-execution-once-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = build_receipt_artifact(
        source_tree=args.source_tree,
        saved_confirmation_path=args.saved_confirmation,
        expected_confirmation_sha256=args.expected_confirmation_sha256,
        saved_execution_once_path=args.saved_execution_once,
        expected_execution_once_sha256=args.expected_execution_once_sha256,
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
