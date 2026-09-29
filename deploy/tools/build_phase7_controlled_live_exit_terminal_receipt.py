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
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_TERMINAL_RECEIPT_V1"

OBSERVATION_TOOL = Path(
    "deploy/tools/observe_phase7_controlled_live_verified_exit_post_execution.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_TRANSACTION_EVENTS = Path("rust-executor/src/transaction_events.rs")
RUST_EVENTS = Path("rust-executor/src/events.rs")
REVIEWED_SOURCE_BLOBS = {
    OBSERVATION_TOOL: "4226971f73f9f36808ff154fa5df3935663df6d9",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    RUST_TRANSACTION_EVENTS: "8bbe5c385efb9d97c14c78f40343861582eb7e05",
    RUST_EVENTS: "43da3431ae23edd1cd4825757468720801ded281",
}

RECEIPT_COMMAND = "inspect-transaction-events-env"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_observation_sha256",
    "expected_observation_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "signature",
    "observation_status",
    "observation_error",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "receipt_command",
    "transaction_snapshot_sha256",
    "transaction_slot",
    "block_time",
    "network_fee_lamports",
    "compute_units_consumed",
    "transaction_succeeded",
    "event_count",
    "add_request_count",
    "rebalance_request_count",
    "remove_liquidity_event_count",
    "matching_remove_liquidity_event_count",
    "remove_liquidity_lb_pair",
    "remove_liquidity_from",
    "remove_liquidity_position",
    "remove_liquidity_amount_x",
    "remove_liquidity_amount_y",
    "remove_liquidity_active_bin_id",
    "terminal_receipt_status",
    "terminal_receipt_ready",
    "confirmation_matches_transaction_snapshot",
    "remove_liquidity_event_matches_execution",
    "signing_environment_stripped",
    "live_submit_environment_stripped",
    "transaction_submission_attempted",
    "automatic_resubmission_performed",
    "position_zero_liquidity_proof_required",
    "failure_recovery_required",
    "settlement_authorized",
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


def _load_observation_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT terminal receipt dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT terminal receipt dependency mismatch: {relative}"
            )
    return _load_module(
        source / OBSERVATION_TOOL,
        "phase7_exit_terminal_receipt_observation",
    )


def _receipt_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("RPC_URL", None)
    env["SOLANA_RPC_URL"] = rpc_url
    return env


def _run_json(
    *,
    executor_binary: Path,
    args: list[str],
    env: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        [str(executor_binary), *args],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=45,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"reviewed EXIT receipt command {args[0]} failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"reviewed EXIT receipt command {args[0]} returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"reviewed EXIT receipt command {args[0]} returned invalid result"
        )
    return value


def _optional_nonnegative_int(
    value: Any,
    *,
    label: str,
) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} is invalid")
    return value


def _parse_remove_event(
    item: Any,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    event = item.get("event")
    if not isinstance(event, dict):
        return None
    if event.get("event_type") != "RemoveLiquidity":
        return None
    payload = event.get("event")
    if not isinstance(payload, dict):
        return None

    required_strings = (
        "lb_pair",
        "from",
        "position",
        "amount_x",
        "amount_y",
    )
    if any(
        not isinstance(payload.get(field), str) or not payload[field]
        for field in required_strings
    ):
        raise ValueError(
            "Phase 7 EXIT remove-liquidity event payload is invalid"
        )
    try:
        if int(payload["amount_x"]) < 0 or int(payload["amount_y"]) < 0:
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            "Phase 7 EXIT remove-liquidity amounts are invalid"
        ) from exc
    active_bin_id = payload.get("active_bin_id")
    if not isinstance(active_bin_id, int) or isinstance(active_bin_id, bool):
        raise ValueError(
            "Phase 7 EXIT remove-liquidity active_bin_id is invalid"
        )
    return payload


def validate_exit_terminal_receipt(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT terminal receipt must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"receipt_sha256"}:
        raise ValueError(
            "Phase 7 EXIT terminal receipt schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT terminal receipt format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT terminal receipt type"
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
            "Phase 7 EXIT terminal receipt lineage mismatch"
        )

    for field in (
        "saved_observation_sha256",
        "expected_observation_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "transaction_snapshot_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT terminal receipt {field} is invalid"
            )

    if report["saved_observation_sha256"] != report[
        "expected_observation_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT terminal receipt observation digest mismatch"
        )
    if report["executor_binary_sha256"] != report[
        "expected_executor_binary_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT terminal receipt executor trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "signature",
        "observation_status",
        "executor_binary_path",
        "receipt_command",
        "terminal_receipt_status",
    ):
        value = report.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"Phase 7 EXIT terminal receipt {field} is invalid"
            )

    if report["observation_status"] not in {"CONFIRMED", "FAILED"}:
        raise ValueError(
            "Phase 7 EXIT terminal receipt requires terminal observation"
        )
    if report["terminal_receipt_status"] != report["observation_status"]:
        raise ValueError(
            "Phase 7 EXIT terminal receipt status binding mismatch"
        )
    if report["receipt_command"] != RECEIPT_COMMAND:
        raise ValueError(
            "Phase 7 EXIT terminal receipt command mismatch"
        )

    if (
        not isinstance(report.get("transaction_slot"), int)
        or isinstance(report["transaction_slot"], bool)
        or report["transaction_slot"] <= 0
    ):
        raise ValueError(
            "Phase 7 EXIT terminal receipt transaction slot is invalid"
        )

    for field in (
        "block_time",
        "network_fee_lamports",
        "compute_units_consumed",
    ):
        _optional_nonnegative_int(
            report.get(field),
            label=f"Phase 7 EXIT terminal receipt {field}",
        )

    if not isinstance(report.get("transaction_succeeded"), bool):
        raise ValueError(
            "Phase 7 EXIT terminal receipt success flag is invalid"
        )
    confirmed = report["observation_status"] == "CONFIRMED"
    failed = not confirmed
    if report["transaction_succeeded"] is not confirmed:
        raise ValueError(
            "Phase 7 EXIT terminal receipt outcome differs from observation"
        )

    for field in (
        "event_count",
        "add_request_count",
        "rebalance_request_count",
        "remove_liquidity_event_count",
        "matching_remove_liquidity_event_count",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT terminal receipt {field} is invalid"
            )

    if report["add_request_count"] != 0:
        raise ValueError(
            "Phase 7 EXIT terminal receipt unexpectedly contains add request"
        )
    if report["rebalance_request_count"] != 0:
        raise ValueError(
            "Phase 7 EXIT terminal receipt unexpectedly contains rebalance request"
        )

    event_matches = report.get(
        "remove_liquidity_event_matches_execution"
    )
    if not isinstance(event_matches, bool):
        raise ValueError(
            "Phase 7 EXIT terminal receipt event-match flag is invalid"
        )
    if confirmed:
        if report["matching_remove_liquidity_event_count"] != 1:
            raise ValueError(
                "confirmed Phase 7 EXIT requires exactly one matching remove-liquidity event"
            )
        if event_matches is not True:
            raise ValueError(
                "confirmed Phase 7 EXIT remove-liquidity event mismatch"
            )
        for field in (
            "remove_liquidity_lb_pair",
            "remove_liquidity_from",
            "remove_liquidity_position",
            "remove_liquidity_amount_x",
            "remove_liquidity_amount_y",
        ):
            value = report.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"confirmed Phase 7 EXIT receipt {field} is invalid"
                )
        active_bin_id = report.get("remove_liquidity_active_bin_id")
        if not isinstance(active_bin_id, int) or isinstance(active_bin_id, bool):
            raise ValueError(
                "confirmed Phase 7 EXIT receipt active bin is invalid"
            )
    else:
        for field in (
            "remove_liquidity_lb_pair",
            "remove_liquidity_from",
            "remove_liquidity_position",
            "remove_liquidity_amount_x",
            "remove_liquidity_amount_y",
            "remove_liquidity_active_bin_id",
        ):
            value = report.get(field)
            if value is not None and field != "remove_liquidity_active_bin_id":
                if not isinstance(value, str):
                    raise ValueError(
                        f"failed Phase 7 EXIT receipt {field} is invalid"
                    )
            if (
                field == "remove_liquidity_active_bin_id"
                and value is not None
                and (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                )
            ):
                raise ValueError(
                    "failed Phase 7 EXIT receipt active bin is invalid"
                )

    for field in (
        "terminal_receipt_ready",
        "confirmation_matches_transaction_snapshot",
        "signing_environment_stripped",
        "live_submit_environment_stripped",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT terminal receipt requires {field}=true"
            )

    if report.get("position_zero_liquidity_proof_required") is not confirmed:
        raise ValueError(
            "Phase 7 EXIT terminal receipt zero-liquidity flag mismatch"
        )
    if report.get("failure_recovery_required") is not failed:
        raise ValueError(
            "Phase 7 EXIT terminal receipt recovery flag mismatch"
        )

    for field in (
        "transaction_submission_attempted",
        "automatic_resubmission_performed",
        "settlement_authorized",
        "live_capital_effect_reconciled",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT terminal receipt requires {field}=false"
            )

    if confirmed:
        if report["remove_liquidity_lb_pair"] != report["pool_address"]:
            raise ValueError(
                "Phase 7 EXIT receipt remove-liquidity pool mismatch"
            )
        if report["remove_liquidity_position"] != report["position_address"]:
            raise ValueError(
                "Phase 7 EXIT receipt remove-liquidity position mismatch"
            )
        if report["remove_liquidity_from"] != report["executor_wallet_pubkey"]:
            raise ValueError(
                "Phase 7 EXIT receipt remove-liquidity owner mismatch"
            )

    if failed:
        if (
            not isinstance(report.get("observation_error"), str)
            or not report["observation_error"]
        ):
            raise ValueError(
                "failed Phase 7 EXIT terminal receipt requires observation error"
            )
    elif report.get("observation_error") is not None:
        raise ValueError(
            "confirmed Phase 7 EXIT terminal receipt cannot contain observation error"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["receipt_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT terminal receipt digest mismatch"
        )


def build_exit_terminal_receipt(
    *,
    source_tree: str | Path,
    saved_observation_path: str | Path,
    expected_observation_sha256: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    observation_module = _load_observation_module(source)

    observation = _load_json(
        saved_observation_path,
        label="saved Phase 7 EXIT post-execution observation",
    )
    observation_module.validate_post_execution_observation(observation)
    if not _is_hex_digest(expected_observation_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT observation digest is invalid"
        )
    if observation["observation_sha256"] != expected_observation_sha256:
        raise ValueError(
            "saved Phase 7 EXIT observation digest mismatch"
        )

    status = observation.get("observation_status")
    if status not in {"CONFIRMED", "FAILED"}:
        raise ValueError(
            "Phase 7 EXIT terminal receipt requires confirmed or failed observation"
        )
    if observation.get("terminal_exit_receipt_required") is not True:
        raise ValueError(
            "Phase 7 EXIT observation does not require terminal receipt"
        )
    if observation.get("automatic_resubmission_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT terminal receipt refuses auto-resubmitted execution"
        )
    if observation.get("live_capital_movement_confirmed") is not False:
        raise ValueError(
            "Phase 7 EXIT terminal receipt expects unreconciled capital effect"
        )
    if observation.get("production_pio_database_modified") is not False:
        raise ValueError(
            "Phase 7 EXIT observation mutated production Pio database"
        )

    if _sha256_text(rpc_url) != observation["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT terminal receipt RPC endpoint mismatch"
        )

    binary = _regular_executable(
        executor_binary_path,
        label="Phase 7 executor binary",
    )
    binary_sha = _sha256_bytes(binary.read_bytes())
    if not _is_hex_digest(expected_executor_binary_sha256, 64):
        raise ValueError(
            "expected Phase 7 executor binary SHA-256 is invalid"
        )
    if binary_sha != expected_executor_binary_sha256:
        raise ValueError(
            "Phase 7 executor binary differs from external trust root"
        )

    snapshot = _run_json(
        executor_binary=binary,
        args=[RECEIPT_COMMAND, observation["signature"]],
        env=_receipt_env(rpc_url=rpc_url),
    )
    expected_snapshot_fields = {
        "signature",
        "slot",
        "block_time",
        "network_fee_lamports",
        "compute_units_consumed",
        "succeeded",
        "add_requests",
        "rebalance_requests",
        "events",
    }
    if set(snapshot) != expected_snapshot_fields:
        raise ValueError(
            "Phase 7 EXIT transaction snapshot schema mismatch"
        )
    if snapshot.get("signature") != observation["signature"]:
        raise ValueError(
            "Phase 7 EXIT transaction snapshot signature mismatch"
        )
    slot = snapshot.get("slot")
    if not isinstance(slot, int) or isinstance(slot, bool) or slot <= 0:
        raise ValueError(
            "Phase 7 EXIT transaction snapshot slot is invalid"
        )
    for field in (
        "block_time",
        "network_fee_lamports",
        "compute_units_consumed",
    ):
        _optional_nonnegative_int(
            snapshot.get(field),
            label=f"Phase 7 EXIT transaction snapshot {field}",
        )
    succeeded = snapshot.get("succeeded")
    if not isinstance(succeeded, bool):
        raise ValueError(
            "Phase 7 EXIT transaction snapshot lacks terminal outcome"
        )
    confirmed = status == "CONFIRMED"
    if succeeded is not confirmed:
        raise ValueError(
            "Phase 7 EXIT transaction snapshot outcome differs from chain observation"
        )

    add_requests = snapshot.get("add_requests")
    rebalance_requests = snapshot.get("rebalance_requests")
    events = snapshot.get("events")
    if not isinstance(add_requests, list):
        raise ValueError(
            "Phase 7 EXIT transaction snapshot add_requests is invalid"
        )
    if not isinstance(rebalance_requests, list):
        raise ValueError(
            "Phase 7 EXIT transaction snapshot rebalance_requests is invalid"
        )
    if not isinstance(events, list):
        raise ValueError(
            "Phase 7 EXIT transaction snapshot events is invalid"
        )
    if add_requests:
        raise ValueError(
            "Phase 7 EXIT transaction unexpectedly contains add request"
        )
    if rebalance_requests:
        raise ValueError(
            "Phase 7 EXIT transaction unexpectedly contains rebalance request"
        )

    remove_events: list[dict[str, Any]] = []
    matching_events: list[dict[str, Any]] = []
    for item in events:
        payload = _parse_remove_event(item)
        if payload is None:
            continue
        remove_events.append(payload)
        if (
            payload["lb_pair"] == observation["pool_address"]
            and payload["position"] == observation["position_address"]
            and payload["from"] == observation["executor_wallet_pubkey"]
        ):
            matching_events.append(payload)

    if confirmed and len(matching_events) != 1:
        raise ValueError(
            "confirmed Phase 7 EXIT requires exactly one matching remove-liquidity event"
        )

    matching = matching_events[0] if len(matching_events) == 1 else None

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
        "saved_observation_sha256": observation["observation_sha256"],
        "expected_observation_sha256": expected_observation_sha256,
        "opened_decision_id": observation["opened_decision_id"],
        "exit_decision_id": observation["exit_decision_id"],
        "pool_address": observation["pool_address"],
        "position_address": observation["position_address"],
        "executor_wallet_pubkey": observation["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": observation["rpc_endpoint_sha256"],
        "signature": observation["signature"],
        "observation_status": status,
        "observation_error": observation["observation_error"],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": expected_executor_binary_sha256,
        "receipt_command": RECEIPT_COMMAND,
        "transaction_snapshot_sha256": _sha256_value(snapshot),
        "transaction_slot": slot,
        "block_time": snapshot["block_time"],
        "network_fee_lamports": snapshot["network_fee_lamports"],
        "compute_units_consumed": snapshot["compute_units_consumed"],
        "transaction_succeeded": succeeded,
        "event_count": len(events),
        "add_request_count": len(add_requests),
        "rebalance_request_count": len(rebalance_requests),
        "remove_liquidity_event_count": len(remove_events),
        "matching_remove_liquidity_event_count": len(matching_events),
        "remove_liquidity_lb_pair": (
            matching["lb_pair"] if matching is not None else None
        ),
        "remove_liquidity_from": (
            matching["from"] if matching is not None else None
        ),
        "remove_liquidity_position": (
            matching["position"] if matching is not None else None
        ),
        "remove_liquidity_amount_x": (
            matching["amount_x"] if matching is not None else None
        ),
        "remove_liquidity_amount_y": (
            matching["amount_y"] if matching is not None else None
        ),
        "remove_liquidity_active_bin_id": (
            matching["active_bin_id"] if matching is not None else None
        ),
        "terminal_receipt_status": status,
        "terminal_receipt_ready": True,
        "confirmation_matches_transaction_snapshot": True,
        "remove_liquidity_event_matches_execution": (
            matching is not None
        ),
        "signing_environment_stripped": True,
        "live_submit_environment_stripped": True,
        "transaction_submission_attempted": False,
        "automatic_resubmission_performed": False,
        "position_zero_liquidity_proof_required": confirmed,
        "failure_recovery_required": not confirmed,
        "settlement_authorized": False,
        "live_capital_effect_reconciled": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "receipt_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_terminal_receipt(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only terminal receipt for one Phase 7 EXIT after a "
            "CONFIRMED or FAILED chain observation. The tool inspects the exact "
            "transaction, binds its terminal outcome and decoded remove-liquidity "
            "event, strips signing/live-submit environment, and performs no "
            "submission, retry, settlement, state reconciliation, or promotion."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-observation", required=True)
    parser.add_argument("--expected-observation-sha256", required=True)
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = build_exit_terminal_receipt(
        source_tree=args.source_tree,
        saved_observation_path=args.saved_observation,
        expected_observation_sha256=args.expected_observation_sha256,
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=(
            args.expected_executor_binary_sha256
        ),
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
