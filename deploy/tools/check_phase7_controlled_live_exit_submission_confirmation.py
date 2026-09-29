from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any
from urllib import request as urllib_request
from urllib.parse import urlparse


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_SUBMISSION_CONFIRMATION_V1"

EXECUTION_TOOL = Path(
    "deploy/tools/execute_phase7_controlled_live_verified_exit_transaction_once.py"
)
REVIEWED_SOURCE_BLOBS = {
    EXECUTION_TOOL: "84189fa560a052ee58bf847e7f02f12385514db1",
}

RPC_COMMITMENT_CONFIRMED = frozenset({"confirmed", "finalized"})
RPC_COMMITMENT_PENDING = frozenset({None, "processed"})

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
    "signed_transaction_sha256",
    "signature",
    "rpc_endpoint_sha256",
    "execution_rpc_accepted",
    "execution_journal_status",
    "observation_status",
    "observation_error",
    "observation_slot",
    "observation_confirmation_status",
    "transaction_chain_confirmation_observed",
    "transaction_failure_observed",
    "confirmation_pending",
    "confirmation_recheck_required",
    "failed_execution_recovery_required",
    "liquidity_removal_effect_reconciliation_required",
    "zero_liquidity_proof_required",
    "separate_position_close_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "requires_separate_phase7_promotion_action",
    "automatic_resubmission_performed",
    "read_only",
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


def _load_execution_module(source: Path) -> Any:
    path = source / EXECUTION_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 7 EXIT verified execution tool is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[EXECUTION_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT verified execution tool blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_submission_confirmation_execution",
    )


def _rpc_signature_status(
    rpc_url: str,
    signature: str,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, Any] | None:
    if not isinstance(rpc_url, str) or not rpc_url.strip():
        raise ValueError("Solana RPC URL is required")
    parsed = urlparse(rpc_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Solana RPC URL must use http or https")
    if not isinstance(signature, str) or not signature.strip():
        raise ValueError("Phase 7 EXIT signature is required")

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [
                [signature],
                {"searchTransactionHistory": True},
            ],
        }
    ).encode("utf-8")
    req = urllib_request.Request(
        rpc_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(
            req,
            timeout=timeout_seconds,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ValueError(
            "failed to fetch Solana EXIT signature status"
        ) from exc

    if not isinstance(payload, dict) or payload.get("error") is not None:
        raise ValueError(
            "Solana EXIT signature status RPC returned an error"
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError(
            "Solana EXIT signature status RPC returned invalid result"
        )
    values = result.get("value")
    if not isinstance(values, list) or len(values) != 1:
        raise ValueError(
            "Solana EXIT signature status RPC returned invalid value"
        )
    status = values[0]
    if status is not None and not isinstance(status, dict):
        raise ValueError(
            "Solana EXIT signature status RPC returned invalid status"
        )
    return status


def _classify_status(
    status: dict[str, Any] | None,
) -> tuple[str, str | None, int | None, str | None]:
    if status is None:
        return "PENDING", None, None, None

    slot = status.get("slot")
    if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
        raise ValueError("Solana EXIT signature status slot is invalid")

    error = status.get("err")
    confirmation_status = status.get("confirmationStatus")
    if confirmation_status not in {
        None,
        "processed",
        "confirmed",
        "finalized",
    }:
        raise ValueError(
            "Solana EXIT signature confirmationStatus is invalid"
        )

    if error is not None:
        return (
            "FAILED",
            json.dumps(error, sort_keys=True, separators=(",", ":")),
            slot,
            confirmation_status,
        )
    if confirmation_status in RPC_COMMITMENT_CONFIRMED:
        return "CONFIRMED", None, slot, confirmation_status
    if confirmation_status in RPC_COMMITMENT_PENDING:
        return "PENDING", None, slot, confirmation_status
    raise ValueError("unreachable EXIT confirmation state")


def validate_exit_submission_confirmation(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT submission confirmation must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"confirmation_sha256"}:
        raise ValueError(
            "Phase 7 EXIT submission confirmation schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT submission confirmation format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT submission confirmation type"
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
            "Phase 7 EXIT submission confirmation lineage mismatch"
        )

    for field in (
        "saved_execution_sha256",
        "expected_execution_sha256",
        "signed_transaction_sha256",
        "rpc_endpoint_sha256",
        "confirmation_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT submission confirmation {field} is invalid"
            )
    if report["saved_execution_sha256"] != report[
        "expected_execution_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT submission confirmation execution digest mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "signature",
        "execution_journal_status",
        "observation_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT submission confirmation {field} is invalid"
            )

    if report["observation_status"] not in {
        "PENDING",
        "CONFIRMED",
        "FAILED",
    }:
        raise ValueError(
            "Phase 7 EXIT submission confirmation observation status invalid"
        )

    slot = report.get("observation_slot")
    if slot is not None and (
        not isinstance(slot, int) or isinstance(slot, bool) or slot < 0
    ):
        raise ValueError(
            "Phase 7 EXIT submission confirmation observation_slot invalid"
        )
    confirmation_status = report.get("observation_confirmation_status")
    if confirmation_status not in {
        None,
        "processed",
        "confirmed",
        "finalized",
    }:
        raise ValueError(
            "Phase 7 EXIT submission confirmation confirmationStatus invalid"
        )
    error = report.get("observation_error")
    if error is not None and not isinstance(error, str):
        raise ValueError(
            "Phase 7 EXIT submission confirmation observation_error invalid"
        )

    status = report["observation_status"]
    expected_confirmed = status == "CONFIRMED"
    expected_failed = status == "FAILED"
    expected_pending = status == "PENDING"
    if (
        report.get("transaction_chain_confirmation_observed")
        is not expected_confirmed
    ):
        raise ValueError(
            "Phase 7 EXIT submission confirmation confirmed flag mismatch"
        )
    if report.get("transaction_failure_observed") is not expected_failed:
        raise ValueError(
            "Phase 7 EXIT submission confirmation failure flag mismatch"
        )
    if report.get("confirmation_pending") is not expected_pending:
        raise ValueError(
            "Phase 7 EXIT submission confirmation pending flag mismatch"
        )
    if report.get("confirmation_recheck_required") is not expected_pending:
        raise ValueError(
            "Phase 7 EXIT submission confirmation recheck flag mismatch"
        )
    if expected_failed and not error:
        raise ValueError(
            "Phase 7 EXIT failed confirmation requires observation_error"
        )
    if not expected_failed and error is not None:
        raise ValueError(
            "Phase 7 EXIT non-failed confirmation cannot carry observation_error"
        )
    if expected_confirmed and confirmation_status not in {
        "confirmed",
        "finalized",
    }:
        raise ValueError(
            "Phase 7 EXIT confirmed observation has invalid commitment status"
        )

    if report.get("failed_execution_recovery_required") is not expected_failed:
        raise ValueError(
            "Phase 7 EXIT submission confirmation recovery flag mismatch"
        )
    for field in (
        "liquidity_removal_effect_reconciliation_required",
        "zero_liquidity_proof_required",
        "separate_position_close_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if report.get(field) is not expected_confirmed:
            raise ValueError(
                f"Phase 7 EXIT submission confirmation {field} binding mismatch"
            )
    for field in (
        "requires_separate_phase7_promotion_action",
        "read_only",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT submission confirmation requires {field}=true"
            )
    for field in (
        "automatic_resubmission_performed",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT submission confirmation requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["confirmation_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT submission confirmation digest mismatch"
        )


def build_exit_submission_confirmation(
    *,
    source_tree: str | Path,
    saved_execution_path: str | Path,
    expected_execution_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    execution_module = _load_execution_module(source)

    execution = _load_json(
        saved_execution_path,
        label="saved Phase 7 EXIT verified single execution",
    )
    execution_module.validate_verified_single_execution(execution)
    if not _is_hex_digest(expected_execution_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT execution digest is invalid"
        )
    if execution["execution_sha256"] != expected_execution_sha256:
        raise ValueError(
            "saved Phase 7 EXIT execution digest mismatch"
        )
    if execution.get("submission_attempted_once") is not True:
        raise ValueError(
            "Phase 7 EXIT confirmation requires one submission attempt"
        )
    if execution.get("exact_signed_transaction_verified") is not True:
        raise ValueError(
            "Phase 7 EXIT confirmation requires verified signed bytes"
        )
    if execution.get("automatic_retry_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT confirmation refuses automatically retried execution"
        )
    if execution.get("transaction_signing_performed") is not False:
        raise ValueError(
            "Phase 7 EXIT confirmation expected externally signed bytes"
        )
    if execution.get("production_pio_database_modified") is not False:
        raise ValueError(
            "Phase 7 EXIT confirmation refuses mutated production Pio database"
        )
    if _sha256_text(rpc_url) != execution["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT confirmation RPC endpoint mismatch"
        )

    nested = _rpc_signature_status(
        rpc_url,
        execution["signature"],
    )
    status, error, slot, confirmation_status = _classify_status(nested)

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
        "signed_transaction_sha256": execution[
            "signed_transaction_sha256"
        ],
        "signature": execution["signature"],
        "rpc_endpoint_sha256": execution["rpc_endpoint_sha256"],
        "execution_rpc_accepted": execution["rpc_accepted"],
        "execution_journal_status": execution["journal_status"],
        "observation_status": status,
        "observation_error": error,
        "observation_slot": slot,
        "observation_confirmation_status": confirmation_status,
        "transaction_chain_confirmation_observed": status == "CONFIRMED",
        "transaction_failure_observed": status == "FAILED",
        "confirmation_pending": status == "PENDING",
        "confirmation_recheck_required": status == "PENDING",
        "failed_execution_recovery_required": status == "FAILED",
        "liquidity_removal_effect_reconciliation_required": status == "CONFIRMED",
        "zero_liquidity_proof_required": status == "CONFIRMED",
        "separate_position_close_required": status == "CONFIRMED",
        "post_close_account_absence_proof_required": status == "CONFIRMED",
        "post_exit_state_reconciliation_required": status == "CONFIRMED",
        "requires_separate_phase7_promotion_action": True,
        "automatic_resubmission_performed": False,
        "read_only": True,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "confirmation_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_submission_confirmation(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Observe the exact submitted Phase 7 EXIT signature on Solana and "
            "classify it as PENDING, CONFIRMED or FAILED. This is read-only: "
            "it performs no resubmission, signing, journal mutation, position "
            "mutation, state reconciliation, or Phase 7 promotion."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-execution", required=True)
    parser.add_argument("--expected-execution-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = build_exit_submission_confirmation(
        source_tree=args.source_tree,
        saved_execution_path=args.saved_execution,
        expected_execution_sha256=args.expected_execution_sha256,
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
