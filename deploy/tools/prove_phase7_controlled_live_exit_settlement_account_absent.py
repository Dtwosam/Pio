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
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_ACCOUNT_ABSENCE_PROOF_V1"
)

TERMINAL_RECEIPT_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_settlement_terminal_receipt.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_POSITION_CLOSURE = Path("rust-executor/src/position_closure.rs")
REVIEWED_SOURCE_BLOBS = {
    TERMINAL_RECEIPT_TOOL: "06e6ad463483cd213083c6cfb008f343a7b0cda1",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    RUST_POSITION_CLOSURE: "a0dabf25f55d6e33944f7f3c2665101b28d83ff4",
}

CLOSURE_COMMAND = "verify-position-closed"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_terminal_receipt_sha256",
    "expected_terminal_receipt_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "destination_config_sha256",
    "rpc_endpoint_sha256",
    "settlement_signature",
    "settlement_transaction_slot",
    "terminal_receipt_status",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "closure_command",
    "closure_position",
    "position_account_absent",
    "closure_rpc_context_slot",
    "closure_observation_at_or_after_settlement",
    "position_account_absence_proven",
    "post_exit_state_reconciliation_required",
    "post_exit_state_reconciliation_ready",
    "signing_environment_stripped",
    "live_submit_environment_stripped",
    "transaction_submission_attempted",
    "automatic_resubmission_performed",
    "live_capital_effect_reconciled",
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


def _regular_executable(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if not (st.st_mode & stat.S_IXUSR):
        raise ValueError(f"{label} must be executable")
    return resolved


def _load_receipt_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT settlement account-absence dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT settlement account-absence dependency mismatch: {relative}"
            )
    return _load_module(
        source / TERMINAL_RECEIPT_TOOL,
        "phase7_exit_settlement_account_absence_receipt",
    )


def _closure_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("SOLANA_RPC_URL", None)
    env.pop("RPC_URL", None)
    return env


def _run_closure(
    *,
    executor_binary: Path,
    rpc_url: str,
    position_address: str,
    env: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [
            str(executor_binary),
            CLOSURE_COMMAND,
            rpc_url,
            position_address,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode not in {0, 2}:
        raise ValueError(
            "Phase 7 EXIT settlement position-closure check failed with "
            f"return code {completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Phase 7 EXIT settlement position-closure check returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 7 EXIT settlement position-closure check returned invalid result"
        )
    return completed.returncode, value


def validate_exit_settlement_account_absence_proof(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT settlement account-absence proof must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"proof_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement account-absence proof schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement account-absence proof format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement account-absence proof type"
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
            "Phase 7 EXIT settlement account-absence lineage mismatch"
        )

    for field in (
        "saved_terminal_receipt_sha256",
        "expected_terminal_receipt_sha256",
        "destination_config_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "proof_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement account-absence {field} is invalid"
            )

    if report["saved_terminal_receipt_sha256"] != report[
        "expected_terminal_receipt_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement account-absence receipt digest mismatch"
        )
    if report["executor_binary_sha256"] != report[
        "expected_executor_binary_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement account-absence executor trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "settlement_signature",
        "terminal_receipt_status",
        "executor_binary_path",
        "closure_command",
        "closure_position",
    ):
        value = report.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"Phase 7 EXIT settlement account-absence {field} is invalid"
            )

    if report["terminal_receipt_status"] != "CONFIRMED":
        raise ValueError(
            "Phase 7 EXIT settlement account-absence requires confirmed receipt"
        )
    if report["closure_command"] != CLOSURE_COMMAND:
        raise ValueError(
            "Phase 7 EXIT settlement account-absence command mismatch"
        )
    if report["closure_position"] != report["position_address"]:
        raise ValueError(
            "Phase 7 EXIT settlement account-absence position mismatch"
        )

    for field in (
        "settlement_transaction_slot",
        "closure_rpc_context_slot",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(
                f"Phase 7 EXIT settlement account-absence {field} is invalid"
            )
    if report["closure_rpc_context_slot"] < report["settlement_transaction_slot"]:
        raise ValueError(
            "Phase 7 EXIT settlement account-absence observation predates settlement"
        )

    for field in (
        "position_account_absent",
        "closure_observation_at_or_after_settlement",
        "position_account_absence_proven",
        "post_exit_state_reconciliation_required",
        "post_exit_state_reconciliation_ready",
        "signing_environment_stripped",
        "live_submit_environment_stripped",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement account-absence requires {field}=true"
            )

    for field in (
        "transaction_submission_attempted",
        "automatic_resubmission_performed",
        "live_capital_effect_reconciled",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement account-absence requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["proof_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT settlement account-absence proof digest mismatch"
        )


def build_exit_settlement_account_absence_proof(
    *,
    source_tree: str | Path,
    saved_terminal_receipt_path: str | Path,
    expected_terminal_receipt_sha256: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    receipt_module = _load_receipt_module(source)

    receipt = _load_json(
        saved_terminal_receipt_path,
        label="saved Phase 7 EXIT settlement terminal receipt",
    )
    receipt_module.validate_exit_settlement_terminal_receipt(receipt)
    if (
        not _is_hex_digest(expected_terminal_receipt_sha256, 64)
        or receipt["receipt_sha256"] != expected_terminal_receipt_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT settlement terminal receipt digest mismatch"
        )

    if receipt.get("terminal_receipt_status") != "CONFIRMED":
        raise ValueError(
            "Phase 7 EXIT settlement account-absence requires confirmed receipt"
        )
    for field in (
        "transaction_succeeded",
        "terminal_receipt_ready",
        "confirmation_matches_transaction_snapshot",
        "settlement_events_match_request",
        "position_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if receipt.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement account-absence requires receipt {field}=true"
            )
    for field in (
        "transaction_submission_attempted",
        "automatic_resubmission_performed",
        "live_capital_effect_reconciled",
        "phase7_promotion_persisted",
        "production_pio_database_modified",
    ):
        if receipt.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement account-absence refuses receipt {field}=true"
            )

    if _sha256_text(rpc_url) != receipt["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement account-absence RPC endpoint mismatch"
        )

    binary = _regular_executable(
        executor_binary_path,
        label="Phase 7 executor binary",
    )
    binary_sha = _sha256_bytes(binary.read_bytes())
    if (
        not _is_hex_digest(expected_executor_binary_sha256, 64)
        or binary_sha != expected_executor_binary_sha256
    ):
        raise ValueError(
            "Phase 7 executor binary differs from external trust root"
        )

    returncode, closure = _run_closure(
        executor_binary=binary,
        rpc_url=rpc_url,
        position_address=receipt["position_address"],
        env=_closure_env(),
    )
    if set(closure) != {
        "position",
        "closed",
        "rpc_context_slot",
    }:
        raise ValueError(
            "Phase 7 EXIT settlement position-closure result schema mismatch"
        )
    if closure.get("position") != receipt["position_address"]:
        raise ValueError(
            "Phase 7 EXIT settlement position-closure position mismatch"
        )
    if not isinstance(closure.get("closed"), bool):
        raise ValueError(
            "Phase 7 EXIT settlement position-closure closed flag invalid"
        )
    closure_slot = closure.get("rpc_context_slot")
    if (
        not isinstance(closure_slot, int)
        or isinstance(closure_slot, bool)
        or closure_slot <= 0
    ):
        raise ValueError(
            "Phase 7 EXIT settlement position-closure context slot invalid"
        )
    if returncode != 0 or closure["closed"] is not True:
        raise ValueError(
            "Phase 7 EXIT settlement position account is still present"
        )
    if closure_slot < receipt["transaction_slot"]:
        raise ValueError(
            "Phase 7 EXIT settlement account-absence observation predates settlement"
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
        "saved_terminal_receipt_sha256": receipt["receipt_sha256"],
        "expected_terminal_receipt_sha256": expected_terminal_receipt_sha256,
        "opened_decision_id": receipt["opened_decision_id"],
        "exit_decision_id": receipt["exit_decision_id"],
        "pool_address": receipt["pool_address"],
        "position_address": receipt["position_address"],
        "executor_wallet_pubkey": receipt["executor_wallet_pubkey"],
        "destination_config_sha256": receipt["destination_config_sha256"],
        "rpc_endpoint_sha256": receipt["rpc_endpoint_sha256"],
        "settlement_signature": receipt["signature"],
        "settlement_transaction_slot": receipt["transaction_slot"],
        "terminal_receipt_status": receipt["terminal_receipt_status"],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": expected_executor_binary_sha256,
        "closure_command": CLOSURE_COMMAND,
        "closure_position": closure["position"],
        "position_account_absent": True,
        "closure_rpc_context_slot": closure_slot,
        "closure_observation_at_or_after_settlement": True,
        "position_account_absence_proven": True,
        "post_exit_state_reconciliation_required": True,
        "post_exit_state_reconciliation_ready": True,
        "signing_environment_stripped": True,
        "live_submit_environment_stripped": True,
        "transaction_submission_attempted": False,
        "automatic_resubmission_performed": False,
        "live_capital_effect_reconciled": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "proof_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_settlement_account_absence_proof(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prove that the Phase 7 EXIT settlement closed the exact position "
            "account by observing confirmed account absence at or after the "
            "settlement transaction slot. This tool is read-only and performs "
            "no submission, retry, reconciliation, or promotion."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-terminal-receipt", required=True)
    parser.add_argument("--expected-terminal-receipt-sha256", required=True)
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = build_exit_settlement_account_absence_proof(
        source_tree=args.source_tree,
        saved_terminal_receipt_path=args.saved_terminal_receipt,
        expected_terminal_receipt_sha256=(
            args.expected_terminal_receipt_sha256
        ),
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=(
            args.expected_executor_binary_sha256
        ),
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
