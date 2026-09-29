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
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_ZERO_LIQUIDITY_PROOF_V2"

RECEIPT_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_terminal_receipt.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_STATE_READER = Path("rust-executor/src/state_reader.rs")
REVIEWED_SOURCE_BLOBS = {
    RECEIPT_TOOL: "23f2f9d936c6b35c44dcb9cb548308b17f2dff10",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    RUST_STATE_READER: "30d1435af1329bca07f73d6639539b43503e84e9",
}

INSPECT_COMMAND = "inspect-position-env"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"
DEFAULT_PUBKEY = "11111111111111111111111111111111"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_receipt_sha256",
    "expected_receipt_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "signature",
    "transaction_slot",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "inspection_command",
    "position_snapshot_sha256",
    "capture_slot_start",
    "capture_slot_end",
    "snapshot_position_address",
    "snapshot_pool_address",
    "snapshot_owner",
    "snapshot_fee_owner",
    "lower_bin_id",
    "upper_bin_id",
    "total_x_amount",
    "total_y_amount",
    "fee_x",
    "fee_y",
    "reward_one",
    "reward_two",
    "total_claimed_fee_x_amount",
    "total_claimed_fee_y_amount",
    "bin_count",
    "nonzero_position_liquidity_bins",
    "nonzero_position_x_bins",
    "nonzero_position_y_bins",
    "post_transaction_snapshot_order_valid",
    "position_account_present",
    "pool_identity_matches",
    "owner_identity_matches",
    "fee_owner_supported_for_settlement",
    "principal_x_zero",
    "principal_y_zero",
    "all_position_liquidity_zero",
    "all_position_x_zero",
    "all_position_y_zero",
    "zero_liquidity_proven",
    "claimable_fee_or_reward_may_remain",
    "settlement_preparation_required",
    "position_account_close_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "settlement_authorized",
    "transaction_signing_performed",
    "transaction_submission_attempted",
    "automatic_resubmission_performed",
    "live_capital_effect_reconciled",
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
                f"Phase 7 EXIT zero-liquidity dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT zero-liquidity dependency mismatch: {relative}"
            )
    return _load_module(
        source / RECEIPT_TOOL,
        "phase7_exit_zero_liquidity_receipt",
    )


def _inspection_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("RPC_URL", None)
    env["SOLANA_RPC_URL"] = rpc_url
    return env


def _inspect_position(
    *,
    executor_binary: Path,
    position_address: str,
    env: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        [str(executor_binary), INSPECT_COMMAND, position_address],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=45,
    )
    if completed.returncode != 0:
        raise ValueError(
            "Phase 7 EXIT position inspection failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Phase 7 EXIT position inspection returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 7 EXIT position inspection returned invalid result"
        )
    return value


def _parse_nonnegative_amount(name: str, raw: Any) -> int:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"Phase 7 EXIT position {name} is invalid")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Phase 7 EXIT position {name} is not an integer string"
        ) from exc
    if value < 0:
        raise ValueError(
            f"Phase 7 EXIT position {name} must be nonnegative"
        )
    return value


def validate_exit_zero_liquidity_proof(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"proof_sha256"}:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT zero-liquidity proof format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT zero-liquidity proof type"
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
            "Phase 7 EXIT zero-liquidity proof lineage mismatch"
        )

    for field in (
        "saved_receipt_sha256",
        "expected_receipt_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "position_snapshot_sha256",
        "proof_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT zero-liquidity proof {field} is invalid"
            )

    if report["saved_receipt_sha256"] != report["expected_receipt_sha256"]:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof receipt digest mismatch"
        )
    if (
        report["executor_binary_sha256"]
        != report["expected_executor_binary_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof executor trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "signature",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "executor_binary_path",
        "inspection_command",
        "snapshot_position_address",
        "snapshot_pool_address",
        "snapshot_owner",
        "snapshot_fee_owner",
        "total_x_amount",
        "total_y_amount",
        "fee_x",
        "fee_y",
        "reward_one",
        "reward_two",
        "total_claimed_fee_x_amount",
        "total_claimed_fee_y_amount",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT zero-liquidity proof {field} is invalid"
            )

    if report["inspection_command"] != INSPECT_COMMAND:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity inspection command mismatch"
        )

    for field in (
        "transaction_slot",
        "capture_slot_start",
        "capture_slot_end",
        "lower_bin_id",
        "upper_bin_id",
        "bin_count",
        "nonzero_position_liquidity_bins",
        "nonzero_position_x_bins",
        "nonzero_position_y_bins",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"Phase 7 EXIT zero-liquidity proof {field} is invalid"
            )

    if report["transaction_slot"] <= 0:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof transaction slot invalid"
        )
    if report["capture_slot_start"] < report["transaction_slot"]:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity snapshot predates confirmed transaction"
        )
    if report["capture_slot_end"] < report["capture_slot_start"]:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity snapshot slot range invalid"
        )
    if report["bin_count"] < 0:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity bin count invalid"
        )
    for field in (
        "nonzero_position_liquidity_bins",
        "nonzero_position_x_bins",
        "nonzero_position_y_bins",
    ):
        if report[field] != 0:
            raise ValueError(
                f"Phase 7 EXIT zero-liquidity proof found nonzero bin state: {field}"
            )

    for field in (
        "post_transaction_snapshot_order_valid",
        "position_account_present",
        "pool_identity_matches",
        "owner_identity_matches",
        "fee_owner_supported_for_settlement",
        "principal_x_zero",
        "principal_y_zero",
        "all_position_liquidity_zero",
        "all_position_x_zero",
        "all_position_y_zero",
        "zero_liquidity_proven",
        "settlement_preparation_required",
        "position_account_close_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT zero-liquidity proof requires {field}=true"
            )

    if not isinstance(
        report.get("claimable_fee_or_reward_may_remain"),
        bool,
    ):
        raise ValueError(
            "Phase 7 EXIT zero-liquidity claimable flag invalid"
        )

    for field in (
        "settlement_authorized",
        "transaction_signing_performed",
        "transaction_submission_attempted",
        "automatic_resubmission_performed",
        "live_capital_effect_reconciled",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT zero-liquidity proof requires {field}=false"
            )

    if report["snapshot_position_address"] != report["position_address"]:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity position identity mismatch"
        )
    if report["snapshot_pool_address"] != report["pool_address"]:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity pool identity mismatch"
        )
    if report["snapshot_owner"] != report["executor_wallet_pubkey"]:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity owner identity mismatch"
        )
    if report["snapshot_fee_owner"] not in {
        DEFAULT_PUBKEY,
        report["executor_wallet_pubkey"],
    }:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity fee owner is unsupported for settlement"
        )

    for field in (
        "total_x_amount",
        "total_y_amount",
        "fee_x",
        "fee_y",
        "reward_one",
        "reward_two",
        "total_claimed_fee_x_amount",
        "total_claimed_fee_y_amount",
    ):
        value = _parse_nonnegative_amount(field, report[field])
        if field in {"total_x_amount", "total_y_amount"} and value != 0:
            raise ValueError(
                f"Phase 7 EXIT zero-liquidity {field} is not zero"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["proof_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof digest mismatch"
        )


def build_exit_zero_liquidity_proof(
    *,
    source_tree: str | Path,
    saved_receipt_path: str | Path,
    expected_receipt_sha256: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    receipt_module = _load_receipt_module(source)

    receipt = _load_json(
        saved_receipt_path,
        label="saved Phase 7 EXIT terminal receipt",
    )
    receipt_module.validate_exit_terminal_receipt(receipt)
    if not _is_hex_digest(expected_receipt_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT terminal receipt digest is invalid"
        )
    if receipt["receipt_sha256"] != expected_receipt_sha256:
        raise ValueError(
            "saved Phase 7 EXIT terminal receipt digest mismatch"
        )
    if receipt.get("terminal_receipt_status") != "CONFIRMED":
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof requires CONFIRMED receipt"
        )
    if receipt.get("transaction_succeeded") is not True:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof requires successful transaction"
        )
    if receipt.get("remove_liquidity_event_matches_execution") is not True:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof requires matching remove-liquidity event"
        )
    if receipt.get("matching_remove_liquidity_event_count") != 1:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof requires one matching remove-liquidity event"
        )
    if receipt.get("position_zero_liquidity_proof_required") is not True:
        raise ValueError(
            "Phase 7 EXIT terminal receipt does not require zero-liquidity proof"
        )
    if receipt.get("settlement_authorized") is not False:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof refuses pre-authorized settlement"
        )
    if receipt.get("automatic_resubmission_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof refuses auto-resubmitted execution"
        )
    if receipt.get("live_capital_effect_reconciled") is not False:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof expects unreconciled capital effect"
        )

    if _sha256_text(rpc_url) != receipt["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT zero-liquidity proof RPC endpoint mismatch"
        )

    binary = _regular_executable(
        executor_binary_path,
        label="Phase 7 executor binary",
    )
    binary_sha = _sha256_bytes(binary.read_bytes())
    if not _is_hex_digest(expected_executor_binary_sha256, 64):
        raise ValueError(
            "expected Phase 7 executor binary digest is invalid"
        )
    if binary_sha != expected_executor_binary_sha256:
        raise ValueError(
            "Phase 7 executor binary differs from external trust root"
        )

    snapshot = _inspect_position(
        executor_binary=binary,
        position_address=receipt["position_address"],
        env=_inspection_env(rpc_url=rpc_url),
    )
    expected_snapshot_fields = {
        "position_address",
        "capture_slot_start",
        "capture_slot_end",
        "pool_address",
        "owner",
        "fee_owner",
        "lower_bin_id",
        "upper_bin_id",
        "total_x_amount",
        "total_y_amount",
        "fee_x",
        "fee_y",
        "reward_one",
        "reward_two",
        "last_updated_at",
        "total_claimed_fee_x_amount",
        "total_claimed_fee_y_amount",
        "supports_limit_order",
        "reward_mints",
        "bins",
    }
    if set(snapshot) != expected_snapshot_fields:
        raise ValueError(
            "Phase 7 EXIT position snapshot schema mismatch"
        )

    for field in (
        "position_address",
        "pool_address",
        "owner",
        "fee_owner",
        "total_x_amount",
        "total_y_amount",
        "fee_x",
        "fee_y",
        "reward_one",
        "reward_two",
        "total_claimed_fee_x_amount",
        "total_claimed_fee_y_amount",
    ):
        if not isinstance(snapshot.get(field), str) or not snapshot[field]:
            raise ValueError(
                f"Phase 7 EXIT position snapshot {field} is invalid"
            )

    if snapshot["position_address"] != receipt["position_address"]:
        raise ValueError(
            "Phase 7 EXIT position snapshot address mismatch"
        )
    if snapshot["pool_address"] != receipt["pool_address"]:
        raise ValueError(
            "Phase 7 EXIT position snapshot pool mismatch"
        )
    if snapshot["owner"] != receipt["executor_wallet_pubkey"]:
        raise ValueError(
            "Phase 7 EXIT position snapshot owner mismatch"
        )
    if snapshot["fee_owner"] not in {
        DEFAULT_PUBKEY,
        receipt["executor_wallet_pubkey"],
    }:
        raise ValueError(
            "Phase 7 EXIT position fee owner is unsupported for settlement"
        )

    for field in (
        "capture_slot_start",
        "capture_slot_end",
        "lower_bin_id",
        "upper_bin_id",
        "last_updated_at",
    ):
        value = snapshot.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"Phase 7 EXIT position snapshot {field} is invalid"
            )

    if snapshot["capture_slot_start"] < receipt["transaction_slot"]:
        raise ValueError(
            "Phase 7 EXIT position snapshot predates confirmed transaction"
        )
    if snapshot["capture_slot_end"] < snapshot["capture_slot_start"]:
        raise ValueError(
            "Phase 7 EXIT position snapshot slot range invalid"
        )

    for field in (
        "total_x_amount",
        "total_y_amount",
        "fee_x",
        "fee_y",
        "reward_one",
        "reward_two",
        "total_claimed_fee_x_amount",
        "total_claimed_fee_y_amount",
    ):
        _parse_nonnegative_amount(field, snapshot[field])

    if _parse_nonnegative_amount(
        "total_x_amount",
        snapshot["total_x_amount"],
    ) != 0:
        raise ValueError(
            "Phase 7 EXIT position still has principal X amount"
        )
    if _parse_nonnegative_amount(
        "total_y_amount",
        snapshot["total_y_amount"],
    ) != 0:
        raise ValueError(
            "Phase 7 EXIT position still has principal Y amount"
        )

    bins = snapshot.get("bins")
    if not isinstance(bins, list):
        raise ValueError(
            "Phase 7 EXIT position snapshot bins are invalid"
        )

    nonzero_liquidity = 0
    nonzero_x = 0
    nonzero_y = 0
    for index, item in enumerate(bins):
        if not isinstance(item, dict):
            raise ValueError(
                f"Phase 7 EXIT position bin {index} is invalid"
            )
        for field in (
            "position_liquidity",
            "position_x_amount",
            "position_y_amount",
        ):
            value = _parse_nonnegative_amount(
                f"bin_{index}_{field}",
                item.get(field),
            )
            if field == "position_liquidity" and value != 0:
                nonzero_liquidity += 1
            elif field == "position_x_amount" and value != 0:
                nonzero_x += 1
            elif field == "position_y_amount" and value != 0:
                nonzero_y += 1

    if nonzero_liquidity:
        raise ValueError(
            "Phase 7 EXIT position still has nonzero liquidity shares"
        )
    if nonzero_x or nonzero_y:
        raise ValueError(
            "Phase 7 EXIT position bins still contain principal token amounts"
        )

    claimable = any(
        _parse_nonnegative_amount(field, snapshot[field]) > 0
        for field in ("fee_x", "fee_y", "reward_one", "reward_two")
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
        "saved_receipt_sha256": receipt["receipt_sha256"],
        "expected_receipt_sha256": expected_receipt_sha256,
        "opened_decision_id": receipt["opened_decision_id"],
        "exit_decision_id": receipt["exit_decision_id"],
        "signature": receipt["signature"],
        "transaction_slot": receipt["transaction_slot"],
        "pool_address": receipt["pool_address"],
        "position_address": receipt["position_address"],
        "executor_wallet_pubkey": receipt["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": receipt["rpc_endpoint_sha256"],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": (
            expected_executor_binary_sha256
        ),
        "inspection_command": INSPECT_COMMAND,
        "position_snapshot_sha256": _sha256_value(snapshot),
        "capture_slot_start": snapshot["capture_slot_start"],
        "capture_slot_end": snapshot["capture_slot_end"],
        "snapshot_position_address": snapshot["position_address"],
        "snapshot_pool_address": snapshot["pool_address"],
        "snapshot_owner": snapshot["owner"],
        "snapshot_fee_owner": snapshot["fee_owner"],
        "lower_bin_id": snapshot["lower_bin_id"],
        "upper_bin_id": snapshot["upper_bin_id"],
        "total_x_amount": snapshot["total_x_amount"],
        "total_y_amount": snapshot["total_y_amount"],
        "fee_x": snapshot["fee_x"],
        "fee_y": snapshot["fee_y"],
        "reward_one": snapshot["reward_one"],
        "reward_two": snapshot["reward_two"],
        "total_claimed_fee_x_amount": snapshot[
            "total_claimed_fee_x_amount"
        ],
        "total_claimed_fee_y_amount": snapshot[
            "total_claimed_fee_y_amount"
        ],
        "bin_count": len(bins),
        "nonzero_position_liquidity_bins": 0,
        "nonzero_position_x_bins": 0,
        "nonzero_position_y_bins": 0,
        "post_transaction_snapshot_order_valid": True,
        "position_account_present": True,
        "pool_identity_matches": True,
        "owner_identity_matches": True,
        "fee_owner_supported_for_settlement": True,
        "principal_x_zero": True,
        "principal_y_zero": True,
        "all_position_liquidity_zero": True,
        "all_position_x_zero": True,
        "all_position_y_zero": True,
        "zero_liquidity_proven": True,
        "claimable_fee_or_reward_may_remain": claimable,
        "settlement_preparation_required": True,
        "position_account_close_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "settlement_authorized": False,
        "transaction_signing_performed": False,
        "transaction_submission_attempted": False,
        "automatic_resubmission_performed": False,
        "live_capital_effect_reconciled": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "proof_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_zero_liquidity_proof(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prove that a confirmed Phase 7 EXIT removed all principal "
            "liquidity from the still-present Meteora position. The snapshot "
            "must be at or after the confirmed EXIT transaction slot. Fees or "
            "rewards may remain claimable for a separate settlement/close "
            "transaction. This tool is read-only."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-receipt", required=True)
    parser.add_argument("--expected-receipt-sha256", required=True)
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = build_exit_zero_liquidity_proof(
        source_tree=args.source_tree,
        saved_receipt_path=args.saved_receipt,
        expected_receipt_sha256=args.expected_receipt_sha256,
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=(
            args.expected_executor_binary_sha256
        ),
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
