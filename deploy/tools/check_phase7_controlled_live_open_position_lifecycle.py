from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_OPEN_POSITION_LIFECYCLE_STATUS_V1"

HANDOFF_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_post_reconciliation_handoff.py"
)
RECEIPT_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_execution_receipt.py"
)
EXECUTE_ONCE_TOOL = Path(
    "deploy/tools/execute_phase7_controlled_live_transaction_once.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_STATE_READER = Path("rust-executor/src/state_reader.rs")

REVIEWED_SOURCE_BLOBS = {
    HANDOFF_TOOL: "5cde6cb56e00df3493f669886e08d400eb241ab4",
    RECEIPT_TOOL: "b7d096fdf1baeff85bc311a630df5c77dc220ce1",
    EXECUTE_ONCE_TOOL: "7580321ce1e418f1fa3d612c196748bef3c0e59a",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    RUST_STATE_READER: "30d1435af1329bca07f73d6639539b43503e84e9",
}

INSPECT_COMMAND = "inspect-position-env"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_handoff_sha256",
    "expected_handoff_sha256",
    "saved_execution_receipt_sha256",
    "expected_execution_receipt_sha256",
    "saved_execution_once_sha256",
    "expected_execution_once_sha256",
    "decision_id",
    "signature",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "inspect_command",
    "position_snapshot_sha256",
    "capture_slot_start",
    "capture_slot_end",
    "snapshot_pool_matches",
    "snapshot_position_matches",
    "snapshot_owner_matches_executor_wallet",
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
    "bin_count",
    "position_account_present",
    "position_closed_proven",
    "open_position_snapshot_ready",
    "lifecycle_route",
    "requires_operator_review",
    "requires_separate_exit_decision",
    "requires_separate_exit_authorization",
    "requires_separate_exit_transaction",
    "requires_post_exit_confirmation",
    "requires_post_exit_closure_proof",
    "requires_post_exit_state_reconciliation",
    "new_live_entry_authorized",
    "exit_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
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


def _load_reviewed(source: Path) -> tuple[Any, Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 lifecycle dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 7 lifecycle dependency mismatch: {relative}")
    handoff = _load_module(
        source / HANDOFF_TOOL,
        "phase7_open_position_lifecycle_handoff",
    )
    receipt = _load_module(
        source / RECEIPT_TOOL,
        "phase7_open_position_lifecycle_receipt",
    )
    execute_once = _load_module(
        source / EXECUTE_ONCE_TOOL,
        "phase7_open_position_lifecycle_execute_once",
    )
    return handoff, receipt, execute_once


def _inspect_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env["SOLANA_RPC_URL"] = rpc_url
    env.pop("RPC_URL", None)
    return env


def _inspect_position(
    *,
    executor_binary: Path,
    position_address: str,
    rpc_url: str,
) -> dict[str, Any]:
    completed = subprocess.run(
        [str(executor_binary), INSPECT_COMMAND, position_address],
        env=_inspect_env(rpc_url=rpc_url),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"reviewed open-position inspection failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("open-position inspection returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("open-position inspection result is invalid")
    return value


def _required_string(snapshot: dict[str, Any], field: str) -> str:
    value = snapshot.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"open-position snapshot {field} is invalid")
    return value


def _required_int(snapshot: dict[str, Any], field: str) -> int:
    value = snapshot.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"open-position snapshot {field} is invalid")
    return value


def _validate_snapshot(
    snapshot: dict[str, Any],
    *,
    position_address: str,
    pool_address: str,
) -> None:
    if _required_string(snapshot, "position_address") != position_address:
        raise ValueError("open-position snapshot address differs from handoff")
    if _required_string(snapshot, "pool_address") != pool_address:
        raise ValueError("open-position snapshot pool differs from handoff")
    start = _required_int(snapshot, "capture_slot_start")
    end = _required_int(snapshot, "capture_slot_end")
    if start < 0 or end < 0 or end < start:
        raise ValueError("open-position capture slot range is invalid")

    for field in (
        "owner",
        "fee_owner",
        "total_x_amount",
        "total_y_amount",
        "fee_x",
        "fee_y",
        "reward_one",
        "reward_two",
    ):
        _required_string(snapshot, field)

    _required_int(snapshot, "lower_bin_id")
    _required_int(snapshot, "upper_bin_id")

    bins = snapshot.get("bins")
    if not isinstance(bins, list):
        raise ValueError("open-position snapshot bins are invalid")


def validate_open_position_lifecycle_status(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 open-position status must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"lifecycle_status_sha256"}:
        raise ValueError("Phase 7 open-position status schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 open-position status format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 open-position status type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 open-position status lineage mismatch")

    for field in (
        "saved_handoff_sha256",
        "expected_handoff_sha256",
        "saved_execution_receipt_sha256",
        "expected_execution_receipt_sha256",
        "saved_execution_once_sha256",
        "expected_execution_once_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "position_snapshot_sha256",
        "lifecycle_status_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 open-position status {field} is invalid")

    if report["saved_handoff_sha256"] != report["expected_handoff_sha256"]:
        raise ValueError("Phase 7 open-position handoff digest mismatch")
    if (
        report["saved_execution_receipt_sha256"]
        != report["expected_execution_receipt_sha256"]
    ):
        raise ValueError("Phase 7 open-position receipt digest mismatch")
    if (
        report["saved_execution_once_sha256"]
        != report["expected_execution_once_sha256"]
    ):
        raise ValueError("Phase 7 open-position execute-once digest mismatch")
    if (
        report["executor_binary_sha256"]
        != report["expected_executor_binary_sha256"]
    ):
        raise ValueError("Phase 7 open-position executor trust-root mismatch")

    for field in (
        "decision_id",
        "signature",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "executor_binary_path",
        "owner",
        "fee_owner",
        "total_x_amount",
        "total_y_amount",
        "fee_x",
        "fee_y",
        "reward_one",
        "reward_two",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 7 open-position status {field} is invalid")

    for field in (
        "capture_slot_start",
        "capture_slot_end",
        "lower_bin_id",
        "upper_bin_id",
        "bin_count",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"Phase 7 open-position status {field} is invalid")
    if report["capture_slot_start"] < 0:
        raise ValueError("Phase 7 open-position start slot is invalid")
    if report["capture_slot_end"] < report["capture_slot_start"]:
        raise ValueError("Phase 7 open-position slot range is invalid")
    if report["bin_count"] < 0:
        raise ValueError("Phase 7 open-position bin count is invalid")

    if report.get("inspect_command") != INSPECT_COMMAND:
        raise ValueError("Phase 7 open-position inspect command mismatch")
    if report.get("lifecycle_route") != "OPEN_POSITION_OBSERVATION":
        raise ValueError("Phase 7 open-position lifecycle route mismatch")

    for field in (
        "snapshot_pool_matches",
        "snapshot_position_matches",
        "snapshot_owner_matches_executor_wallet",
        "position_account_present",
        "open_position_snapshot_ready",
        "requires_operator_review",
        "requires_separate_exit_decision",
        "requires_separate_exit_authorization",
        "requires_separate_exit_transaction",
        "requires_post_exit_confirmation",
        "requires_post_exit_closure_proof",
        "requires_post_exit_state_reconciliation",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 open-position status requires {field}=true")

    if report.get("position_closed_proven") is not False:
        raise ValueError("Phase 7 open-position status requires position_closed_proven=false")

    for field in (
        "new_live_entry_authorized",
        "exit_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
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
            raise ValueError(f"Phase 7 open-position status requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["lifecycle_status_sha256"] != expected:
        raise ValueError("Phase 7 open-position status digest mismatch")


def build_open_position_lifecycle_status(
    *,
    source_tree: str | Path,
    saved_handoff_path: str | Path,
    expected_handoff_sha256: str,
    saved_execution_receipt_path: str | Path,
    expected_execution_receipt_sha256: str,
    saved_execution_once_path: str | Path,
    expected_execution_once_sha256: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    handoff_module, receipt_module, execute_once_module = _load_reviewed(source)

    handoff = _load_json(
        saved_handoff_path,
        label="saved Phase 7 post-reconciliation handoff",
    )
    handoff_module.validate_post_reconciliation_evidence_handoff(handoff)
    if not _is_hex_digest(expected_handoff_sha256, 64):
        raise ValueError("expected Phase 7 handoff digest is invalid")
    if handoff["handoff_sha256"] != expected_handoff_sha256:
        raise ValueError("saved Phase 7 handoff digest mismatch")
    if handoff.get("continuation_route") != handoff_module.ROUTE_OPEN:
        raise ValueError("Phase 7 handoff is not routed to open-position lifecycle")
    if handoff.get("position_status") != "OPEN":
        raise ValueError("Phase 7 lifecycle requires an OPEN reconciled position")
    if handoff.get("new_entry_evidence_candidate") is not False:
        raise ValueError("Phase 7 open-position lifecycle cannot expose another ENTER")

    execution = _load_json(
        saved_execution_receipt_path,
        label="saved Phase 7 execution receipt artifact",
    )
    receipt_module.validate_receipt_artifact(execution)
    if not _is_hex_digest(expected_execution_receipt_sha256, 64):
        raise ValueError("expected Phase 7 execution receipt digest is invalid")
    if execution["receipt_artifact_sha256"] != expected_execution_receipt_sha256:
        raise ValueError("saved Phase 7 execution receipt digest mismatch")

    if execution["decision_id"] != handoff["decision_id"]:
        raise ValueError("execution receipt decision differs from lifecycle handoff")
    if execution["signature"] != handoff["signature"]:
        raise ValueError("execution receipt signature differs from lifecycle handoff")
    if execution["pool_address"] != handoff["pool_address"]:
        raise ValueError("execution receipt pool differs from lifecycle handoff")
    if execution.get("transaction_chain_confirmation_observed") is not True:
        raise ValueError("open-position lifecycle requires confirmed execution receipt")

    execute_once = _load_json(
        saved_execution_once_path,
        label="saved Phase 7 execute-once artifact",
    )
    execute_once_module.validate_execution_once_report(execute_once)
    if not _is_hex_digest(expected_execution_once_sha256, 64):
        raise ValueError("expected Phase 7 execute-once digest is invalid")
    if execute_once["execution_once_sha256"] != expected_execution_once_sha256:
        raise ValueError("saved Phase 7 execute-once digest mismatch")
    if execution["saved_execution_once_sha256"] != execute_once["execution_once_sha256"]:
        raise ValueError("execution receipt/execute-once binding mismatch")
    for field in ("decision_id", "signature", "pool_address"):
        if execute_once[field] != handoff[field]:
            raise ValueError(
                f"execute-once {field} differs from lifecycle handoff"
            )

    if _sha256_text(rpc_url) != execution["rpc_endpoint_sha256"]:
        raise ValueError("lifecycle RPC endpoint differs from execution receipt")

    binary = Path(executor_binary_path).resolve(strict=True)
    binary_sha = _sha256_bytes(binary.read_bytes())
    if binary_sha != expected_executor_binary_sha256:
        raise ValueError("executor binary differs from external trust root")
    if binary_sha != execution["executor_binary_sha256"]:
        raise ValueError("executor binary differs from execution receipt")

    snapshot = _inspect_position(
        executor_binary=binary,
        position_address=handoff["position_address"],
        rpc_url=rpc_url,
    )
    _validate_snapshot(
        snapshot,
        position_address=handoff["position_address"],
        pool_address=handoff["pool_address"],
    )
    if snapshot["owner"] != execute_once["executor_wallet_pubkey"]:
        raise ValueError(
            "open-position owner differs from the authorized executor wallet"
        )

    bins = snapshot["bins"]
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
        "saved_handoff_sha256": handoff["handoff_sha256"],
        "expected_handoff_sha256": expected_handoff_sha256,
        "saved_execution_receipt_sha256": execution[
            "receipt_artifact_sha256"
        ],
        "expected_execution_receipt_sha256": expected_execution_receipt_sha256,
        "saved_execution_once_sha256": execute_once["execution_once_sha256"],
        "expected_execution_once_sha256": expected_execution_once_sha256,
        "decision_id": handoff["decision_id"],
        "signature": handoff["signature"],
        "pool_address": handoff["pool_address"],
        "position_address": handoff["position_address"],
        "executor_wallet_pubkey": execute_once["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": execution["rpc_endpoint_sha256"],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": expected_executor_binary_sha256,
        "inspect_command": INSPECT_COMMAND,
        "position_snapshot_sha256": _sha256_value(snapshot),
        "capture_slot_start": snapshot["capture_slot_start"],
        "capture_slot_end": snapshot["capture_slot_end"],
        "snapshot_pool_matches": True,
        "snapshot_position_matches": True,
        "snapshot_owner_matches_executor_wallet": True,
        "owner": snapshot["owner"],
        "fee_owner": snapshot["fee_owner"],
        "lower_bin_id": snapshot["lower_bin_id"],
        "upper_bin_id": snapshot["upper_bin_id"],
        "total_x_amount": snapshot["total_x_amount"],
        "total_y_amount": snapshot["total_y_amount"],
        "fee_x": snapshot["fee_x"],
        "fee_y": snapshot["fee_y"],
        "reward_one": snapshot["reward_one"],
        "reward_two": snapshot["reward_two"],
        "bin_count": len(bins),
        "position_account_present": True,
        "position_closed_proven": False,
        "open_position_snapshot_ready": True,
        "lifecycle_route": "OPEN_POSITION_OBSERVATION",
        "requires_operator_review": True,
        "requires_separate_exit_decision": True,
        "requires_separate_exit_authorization": True,
        "requires_separate_exit_transaction": True,
        "requires_post_exit_confirmation": True,
        "requires_post_exit_closure_proof": True,
        "requires_post_exit_state_reconciliation": True,
        "new_live_entry_authorized": False,
        "exit_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
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
        "lifecycle_status_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_open_position_lifecycle_status(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the exact reconciled Phase 7 open position through the "
            "reviewed read-only Rust state reader. This tool does not decide, "
            "authorize, sign, or submit an EXIT and does not infer closure from "
            "an inspection failure."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-handoff", required=True)
    parser.add_argument("--expected-handoff-sha256", required=True)
    parser.add_argument("--saved-execution-receipt", required=True)
    parser.add_argument("--expected-execution-receipt-sha256", required=True)
    parser.add_argument("--saved-execution-once", required=True)
    parser.add_argument("--expected-execution-once-sha256", required=True)
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = build_open_position_lifecycle_status(
        source_tree=args.source_tree,
        saved_handoff_path=args.saved_handoff,
        expected_handoff_sha256=args.expected_handoff_sha256,
        saved_execution_receipt_path=args.saved_execution_receipt,
        expected_execution_receipt_sha256=(
            args.expected_execution_receipt_sha256
        ),
        saved_execution_once_path=args.saved_execution_once,
        expected_execution_once_sha256=args.expected_execution_once_sha256,
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=args.expected_executor_binary_sha256,
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
