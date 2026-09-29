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
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_TERMINAL_RECEIPT_V1"
)

OBSERVATION_TOOL = Path(
    "deploy/tools/observe_phase7_controlled_live_verified_exit_settlement_post_execution.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_settlement_single_execution_request.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_TRANSACTION_EVENTS = Path("rust-executor/src/transaction_events.rs")
RUST_EVENTS = Path("rust-executor/src/events.rs")
REVIEWED_SOURCE_BLOBS = {
    OBSERVATION_TOOL: "66507d4e48450c04c1f3c380fb1d7da38058d199",
    REQUEST_TOOL: "ce6bf3688f75d7af025875b56e9d6fe16c7b010f",
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
    "saved_request_sha256",
    "expected_request_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "destination_config_sha256",
    "reward_indices_expected",
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
    "unexpected_dlmm_event_count",
    "claim_fee_event_count",
    "matching_claim_fee_event_count",
    "claim_fee_x",
    "claim_fee_y",
    "claim_fee_active_bin_id",
    "claim_reward_event_count",
    "matching_claim_reward_event_count",
    "matching_reward_indices",
    "matching_reward_claims",
    "position_close_event_count",
    "matching_position_close_event_count",
    "position_close_position",
    "position_close_owner",
    "terminal_receipt_status",
    "terminal_receipt_ready",
    "confirmation_matches_transaction_snapshot",
    "settlement_events_match_request",
    "signing_environment_stripped",
    "live_submit_environment_stripped",
    "transaction_submission_attempted",
    "automatic_resubmission_performed",
    "position_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "failure_recovery_required",
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


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT settlement terminal receipt dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT settlement terminal receipt dependency mismatch: {relative}"
            )
    observation = _load_module(
        source / OBSERVATION_TOOL,
        "phase7_exit_settlement_terminal_receipt_observation",
    )
    request = _load_module(
        source / REQUEST_TOOL,
        "phase7_exit_settlement_terminal_receipt_request",
    )
    return observation, request


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
            f"reviewed settlement receipt command {args[0]} failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"reviewed settlement receipt command {args[0]} returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"reviewed settlement receipt command {args[0]} returned invalid result"
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


def _event_payload(item: Any, event_type: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    envelope = item.get("event")
    if not isinstance(envelope, dict):
        return None
    if envelope.get("event_type") != event_type:
        return None
    payload = envelope.get("event")
    if not isinstance(payload, dict):
        raise ValueError(
            f"Phase 7 EXIT settlement {event_type} event payload is invalid"
        )
    return payload


def _nonempty_event_string(
    payload: dict[str, Any],
    field: str,
    *,
    event_type: str,
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"Phase 7 EXIT settlement {event_type} {field} is invalid"
        )
    return value


def _nonnegative_amount(
    payload: dict[str, Any],
    field: str,
    *,
    event_type: str,
) -> str:
    value = _nonempty_event_string(
        payload,
        field,
        event_type=event_type,
    )
    try:
        if int(value) < 0:
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            f"Phase 7 EXIT settlement {event_type} {field} is invalid"
        ) from exc
    return value


def _parse_claim_fee(item: Any) -> dict[str, Any] | None:
    payload = _event_payload(item, "ClaimFee2")
    if payload is None:
        return None
    parsed = {
        "lb_pair": _nonempty_event_string(
            payload, "lb_pair", event_type="ClaimFee2"
        ),
        "position": _nonempty_event_string(
            payload, "position", event_type="ClaimFee2"
        ),
        "owner": _nonempty_event_string(
            payload, "owner", event_type="ClaimFee2"
        ),
        "fee_x": _nonnegative_amount(
            payload, "fee_x", event_type="ClaimFee2"
        ),
        "fee_y": _nonnegative_amount(
            payload, "fee_y", event_type="ClaimFee2"
        ),
    }
    active_bin_id = payload.get("active_bin_id")
    if not isinstance(active_bin_id, int) or isinstance(active_bin_id, bool):
        raise ValueError(
            "Phase 7 EXIT settlement ClaimFee2 active_bin_id is invalid"
        )
    parsed["active_bin_id"] = active_bin_id
    return parsed


def _parse_claim_reward(item: Any) -> dict[str, Any] | None:
    payload = _event_payload(item, "ClaimReward2")
    if payload is None:
        return None
    reward_index = payload.get("reward_index")
    if reward_index not in {0, 1}:
        raise ValueError(
            "Phase 7 EXIT settlement ClaimReward2 reward_index is invalid"
        )
    parsed = {
        "lb_pair": _nonempty_event_string(
            payload, "lb_pair", event_type="ClaimReward2"
        ),
        "position": _nonempty_event_string(
            payload, "position", event_type="ClaimReward2"
        ),
        "owner": _nonempty_event_string(
            payload, "owner", event_type="ClaimReward2"
        ),
        "reward_index": reward_index,
        "total_reward": _nonnegative_amount(
            payload, "total_reward", event_type="ClaimReward2"
        ),
    }
    active_bin_id = payload.get("active_bin_id")
    if not isinstance(active_bin_id, int) or isinstance(active_bin_id, bool):
        raise ValueError(
            "Phase 7 EXIT settlement ClaimReward2 active_bin_id is invalid"
        )
    parsed["active_bin_id"] = active_bin_id
    return parsed


def _parse_position_close(item: Any) -> dict[str, Any] | None:
    payload = _event_payload(item, "PositionClose")
    if payload is None:
        return None
    return {
        "position": _nonempty_event_string(
            payload, "position", event_type="PositionClose"
        ),
        "owner": _nonempty_event_string(
            payload, "owner", event_type="PositionClose"
        ),
    }


def _decoded_event_type(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    envelope = item.get("event")
    if not isinstance(envelope, dict):
        return None
    value = envelope.get("event_type")
    return value if isinstance(value, str) else None


def validate_exit_settlement_terminal_receipt(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"receipt_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement terminal receipt format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement terminal receipt type"
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
            "Phase 7 EXIT settlement terminal receipt lineage mismatch"
        )

    for field in (
        "saved_observation_sha256",
        "expected_observation_sha256",
        "saved_request_sha256",
        "expected_request_sha256",
        "destination_config_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "transaction_snapshot_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement terminal receipt {field} is invalid"
            )

    if report["saved_observation_sha256"] != report[
        "expected_observation_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt observation digest mismatch"
        )
    if report["saved_request_sha256"] != report["expected_request_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt request digest mismatch"
        )
    if report["executor_binary_sha256"] != report[
        "expected_executor_binary_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt executor trust-root mismatch"
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
                f"Phase 7 EXIT settlement terminal receipt {field} is invalid"
            )

    if report["observation_status"] not in {"CONFIRMED", "FAILED"}:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt requires terminal observation"
        )
    if report["terminal_receipt_status"] != report["observation_status"]:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt status binding mismatch"
        )
    if report["receipt_command"] != RECEIPT_COMMAND:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt command mismatch"
        )

    slot = report.get("transaction_slot")
    if not isinstance(slot, int) or isinstance(slot, bool) or slot <= 0:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt transaction slot is invalid"
        )
    for field in (
        "block_time",
        "network_fee_lamports",
        "compute_units_consumed",
    ):
        _optional_nonnegative_int(
            report.get(field),
            label=f"Phase 7 EXIT settlement terminal receipt {field}",
        )

    if not isinstance(report.get("transaction_succeeded"), bool):
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt success flag is invalid"
        )
    confirmed = report["observation_status"] == "CONFIRMED"
    failed = not confirmed
    if report["transaction_succeeded"] is not confirmed:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt outcome differs from observation"
        )

    for field in (
        "event_count",
        "add_request_count",
        "rebalance_request_count",
        "unexpected_dlmm_event_count",
        "claim_fee_event_count",
        "matching_claim_fee_event_count",
        "claim_reward_event_count",
        "matching_claim_reward_event_count",
        "position_close_event_count",
        "matching_position_close_event_count",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT settlement terminal receipt {field} is invalid"
            )
    if report["add_request_count"] != 0 or report["rebalance_request_count"] != 0:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt contains entry/rebalance request"
        )

    expected_indices = report.get("reward_indices_expected")
    matching_indices = report.get("matching_reward_indices")
    matching_claims = report.get("matching_reward_claims")
    if (
        not isinstance(expected_indices, list)
        or any(index not in {0, 1} for index in expected_indices)
        or expected_indices != sorted(set(expected_indices))
    ):
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt expected reward indices invalid"
        )
    if (
        not isinstance(matching_indices, list)
        or any(index not in {0, 1} for index in matching_indices)
        or matching_indices != sorted(set(matching_indices))
    ):
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt matching reward indices invalid"
        )
    if not isinstance(matching_claims, list):
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt reward claims invalid"
        )

    if confirmed:
        if report["unexpected_dlmm_event_count"] != 0:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement contains unexpected DLMM event"
            )
        if report["matching_claim_fee_event_count"] != 1:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement requires one matching ClaimFee2 event"
            )
        if report["matching_position_close_event_count"] != 1:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement requires one matching PositionClose event"
            )
        if matching_indices != expected_indices:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement reward claims differ from configured destinations"
            )
        if report["matching_claim_reward_event_count"] != len(expected_indices):
            raise ValueError(
                "confirmed Phase 7 EXIT settlement reward claim count mismatch"
            )
        for field in ("claim_fee_x", "claim_fee_y"):
            value = report.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"confirmed Phase 7 EXIT settlement {field} is invalid"
                )
            try:
                if int(value) < 0:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(
                    f"confirmed Phase 7 EXIT settlement {field} is invalid"
                ) from exc
        if not isinstance(report.get("claim_fee_active_bin_id"), int) or isinstance(
            report["claim_fee_active_bin_id"], bool
        ):
            raise ValueError(
                "confirmed Phase 7 EXIT settlement claim fee active bin invalid"
            )
        if report.get("position_close_position") != report["position_address"]:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement close position mismatch"
            )
        if report.get("position_close_owner") != report["executor_wallet_pubkey"]:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement close owner mismatch"
            )
        if report.get("settlement_events_match_request") is not True:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement event binding mismatch"
            )
    else:
        if report.get("settlement_events_match_request") not in {False, True}:
            raise ValueError(
                "failed Phase 7 EXIT settlement event-match flag is invalid"
            )

    for field in (
        "terminal_receipt_ready",
        "confirmation_matches_transaction_snapshot",
        "signing_environment_stripped",
        "live_submit_environment_stripped",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement terminal receipt requires {field}=true"
            )

    if report.get("position_account_absence_proof_required") is not confirmed:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt absence-proof flag mismatch"
        )
    if report.get("post_exit_state_reconciliation_required") is not confirmed:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt reconciliation flag mismatch"
        )
    if report.get("failure_recovery_required") is not failed:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt recovery flag mismatch"
        )

    for field in (
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
                f"Phase 7 EXIT settlement terminal receipt requires {field}=false"
            )

    if failed:
        if (
            not isinstance(report.get("observation_error"), str)
            or not report["observation_error"]
        ):
            raise ValueError(
                "failed Phase 7 EXIT settlement terminal receipt requires observation error"
            )
    elif report.get("observation_error") is not None:
        raise ValueError(
            "confirmed Phase 7 EXIT settlement terminal receipt cannot contain observation error"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["receipt_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt digest mismatch"
        )


def build_exit_settlement_terminal_receipt(
    *,
    source_tree: str | Path,
    saved_observation_path: str | Path,
    expected_observation_sha256: str,
    saved_request_path: str | Path,
    expected_request_sha256: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    observation_module, request_module = _load_reviewed(source)

    observation = _load_json(
        saved_observation_path,
        label="saved Phase 7 EXIT settlement post-execution observation",
    )
    observation_module.validate_post_execution_observation(observation)
    if (
        not _is_hex_digest(expected_observation_sha256, 64)
        or observation["observation_sha256"] != expected_observation_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT settlement observation digest mismatch"
        )

    request = _load_json(
        saved_request_path,
        label="saved Phase 7 EXIT settlement single-execution request",
    )
    request_module.validate_exit_settlement_single_execution_request(request)
    if (
        not _is_hex_digest(expected_request_sha256, 64)
        or request["request_sha256"] != expected_request_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT settlement request digest mismatch"
        )
    if observation["request_sha256"] != request["request_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement observation/request binding mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "destination_config_sha256",
        "rpc_endpoint_sha256",
    ):
        if observation.get(field) != request.get(field):
            raise ValueError(
                f"Phase 7 EXIT settlement observation {field} binding mismatch"
            )

    status = observation.get("observation_status")
    if status not in {"CONFIRMED", "FAILED"}:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt requires confirmed or failed observation"
        )
    if observation.get("terminal_settlement_receipt_required") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement observation does not require terminal receipt"
        )
    if observation.get("automatic_resubmission_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt refuses auto-resubmitted execution"
        )
    if observation.get("live_capital_movement_confirmed") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt expects unreconciled capital effect"
        )
    if observation.get("production_pio_database_modified") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement observation mutated production Pio database"
        )
    if _sha256_text(rpc_url) != observation["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement terminal receipt RPC endpoint mismatch"
        )

    reward_destinations = request.get("reward_token_destinations")
    if not isinstance(reward_destinations, list):
        raise ValueError(
            "Phase 7 EXIT settlement request reward destinations invalid"
        )
    expected_reward_indices = sorted(
        item["reward_index"]
        for item in reward_destinations
        if isinstance(item, dict)
        and item.get("reward_index") in {0, 1}
    )
    if len(expected_reward_indices) != len(reward_destinations):
        raise ValueError(
            "Phase 7 EXIT settlement request reward destination indices invalid"
        )
    if len(expected_reward_indices) != len(set(expected_reward_indices)):
        raise ValueError(
            "Phase 7 EXIT settlement request reward destination indices duplicated"
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
            "Phase 7 EXIT settlement transaction snapshot schema mismatch"
        )
    if snapshot.get("signature") != observation["signature"]:
        raise ValueError(
            "Phase 7 EXIT settlement transaction snapshot signature mismatch"
        )
    slot = snapshot.get("slot")
    if not isinstance(slot, int) or isinstance(slot, bool) or slot <= 0:
        raise ValueError(
            "Phase 7 EXIT settlement transaction snapshot slot is invalid"
        )
    for field in (
        "block_time",
        "network_fee_lamports",
        "compute_units_consumed",
    ):
        _optional_nonnegative_int(
            snapshot.get(field),
            label=f"Phase 7 EXIT settlement transaction snapshot {field}",
        )

    succeeded = snapshot.get("succeeded")
    if not isinstance(succeeded, bool):
        raise ValueError(
            "Phase 7 EXIT settlement transaction snapshot lacks terminal outcome"
        )
    confirmed = status == "CONFIRMED"
    if succeeded is not confirmed:
        raise ValueError(
            "Phase 7 EXIT settlement transaction snapshot outcome differs from chain observation"
        )

    add_requests = snapshot.get("add_requests")
    rebalance_requests = snapshot.get("rebalance_requests")
    events = snapshot.get("events")
    if not isinstance(add_requests, list) or not isinstance(
        rebalance_requests, list
    ) or not isinstance(events, list):
        raise ValueError(
            "Phase 7 EXIT settlement transaction snapshot collections invalid"
        )
    if add_requests or rebalance_requests:
        raise ValueError(
            "Phase 7 EXIT settlement transaction contains entry/rebalance request"
        )

    claim_fees: list[dict[str, Any]] = []
    matching_fees: list[dict[str, Any]] = []
    claim_rewards: list[dict[str, Any]] = []
    matching_rewards: list[dict[str, Any]] = []
    closes: list[dict[str, Any]] = []
    matching_closes: list[dict[str, Any]] = []
    unexpected_types: list[str] = []

    allowed_types = {"ClaimFee2", "ClaimReward2", "PositionClose"}
    for item in events:
        event_type = _decoded_event_type(item)
        if event_type is not None and event_type not in allowed_types:
            unexpected_types.append(event_type)

        fee = _parse_claim_fee(item)
        if fee is not None:
            claim_fees.append(fee)
            if (
                fee["lb_pair"] == request["pool_address"]
                and fee["position"] == request["position_address"]
                and fee["owner"] == request["executor_wallet_pubkey"]
            ):
                matching_fees.append(fee)
            continue

        reward = _parse_claim_reward(item)
        if reward is not None:
            claim_rewards.append(reward)
            if (
                reward["lb_pair"] == request["pool_address"]
                and reward["position"] == request["position_address"]
                and reward["owner"] == request["executor_wallet_pubkey"]
            ):
                matching_rewards.append(reward)
            continue

        close = _parse_position_close(item)
        if close is not None:
            closes.append(close)
            if (
                close["position"] == request["position_address"]
                and close["owner"] == request["executor_wallet_pubkey"]
            ):
                matching_closes.append(close)

    matching_indices = sorted(
        reward["reward_index"] for reward in matching_rewards
    )
    if len(matching_indices) != len(set(matching_indices)):
        raise ValueError(
            "Phase 7 EXIT settlement contains duplicate matching reward claim"
        )

    if confirmed:
        if unexpected_types:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement contains unexpected DLMM event"
            )
        if len(matching_fees) != 1:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement requires exactly one matching ClaimFee2 event"
            )
        if len(matching_closes) != 1:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement requires exactly one matching PositionClose event"
            )
        if matching_indices != expected_reward_indices:
            raise ValueError(
                "confirmed Phase 7 EXIT settlement reward events differ from configured destinations"
            )

    matching_fee = matching_fees[0] if len(matching_fees) == 1 else None
    matching_close = matching_closes[0] if len(matching_closes) == 1 else None

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
        "saved_request_sha256": request["request_sha256"],
        "expected_request_sha256": expected_request_sha256,
        "opened_decision_id": request["opened_decision_id"],
        "exit_decision_id": request["exit_decision_id"],
        "pool_address": request["pool_address"],
        "position_address": request["position_address"],
        "executor_wallet_pubkey": request["executor_wallet_pubkey"],
        "destination_config_sha256": request["destination_config_sha256"],
        "reward_indices_expected": expected_reward_indices,
        "rpc_endpoint_sha256": request["rpc_endpoint_sha256"],
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
        "unexpected_dlmm_event_count": len(unexpected_types),
        "claim_fee_event_count": len(claim_fees),
        "matching_claim_fee_event_count": len(matching_fees),
        "claim_fee_x": (
            matching_fee["fee_x"] if matching_fee is not None else None
        ),
        "claim_fee_y": (
            matching_fee["fee_y"] if matching_fee is not None else None
        ),
        "claim_fee_active_bin_id": (
            matching_fee["active_bin_id"]
            if matching_fee is not None
            else None
        ),
        "claim_reward_event_count": len(claim_rewards),
        "matching_claim_reward_event_count": len(matching_rewards),
        "matching_reward_indices": matching_indices,
        "matching_reward_claims": matching_rewards,
        "position_close_event_count": len(closes),
        "matching_position_close_event_count": len(matching_closes),
        "position_close_position": (
            matching_close["position"]
            if matching_close is not None
            else None
        ),
        "position_close_owner": (
            matching_close["owner"] if matching_close is not None else None
        ),
        "terminal_receipt_status": status,
        "terminal_receipt_ready": True,
        "confirmation_matches_transaction_snapshot": True,
        "settlement_events_match_request": (
            confirmed
            and len(matching_fees) == 1
            and matching_indices == expected_reward_indices
            and len(matching_closes) == 1
            and not unexpected_types
        ),
        "signing_environment_stripped": True,
        "live_submit_environment_stripped": True,
        "transaction_submission_attempted": False,
        "automatic_resubmission_performed": False,
        "position_account_absence_proof_required": confirmed,
        "post_exit_state_reconciliation_required": confirmed,
        "failure_recovery_required": not confirmed,
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
    validate_exit_settlement_terminal_receipt(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only terminal receipt for one Phase 7 EXIT settlement "
            "after a CONFIRMED or FAILED observation. A confirmed receipt must "
            "bind exactly one ClaimFee2 event, the configured ClaimReward2 "
            "events, and one PositionClose event to the expected pool, position "
            "and executor wallet. No submission, retry, reconciliation or "
            "promotion occurs."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-observation", required=True)
    parser.add_argument("--expected-observation-sha256", required=True)
    parser.add_argument("--saved-request", required=True)
    parser.add_argument("--expected-request-sha256", required=True)
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = build_exit_settlement_terminal_receipt(
        source_tree=args.source_tree,
        saved_observation_path=args.saved_observation,
        expected_observation_sha256=args.expected_observation_sha256,
        saved_request_path=args.saved_request,
        expected_request_sha256=args.expected_request_sha256,
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=(
            args.expected_executor_binary_sha256
        ),
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
