from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_TRANSACTION_EXECUTION_REQUEST_V1"

PRESUBMIT_GATE_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_presubmit_evidence.py"
)
REVIEWED_SOURCE_BLOBS = {
    PRESUBMIT_GATE_TOOL: "180980842a673354b024db78195b6bfe0209ab6f",
}

AUTHORIZATION_SCOPE = "SIGN_AND_SUBMIT_EXACT_PHASE7_CONTROLLED_LIVE_TRANSACTION_ONLY"
EXCLUDED_SCOPES = (
    "DIFFERENT_DECISION_ID",
    "DIFFERENT_POOL",
    "DIFFERENT_EXECUTOR_WALLET",
    "DIFFERENT_PROPOSAL",
    "DIFFERENT_CONTROLLED_LIVE_CONFIG",
    "DIFFERENT_PREPARED_TRANSACTION",
    "DIFFERENT_FINAL_SIMULATION",
    "REBALANCE",
    "MULTIPLE_SUBMISSIONS",
    "UNATTENDED_LIVE_OPERATION",
    "CONTROLLED_LIVE_LIMIT_WIDENING",
    "PHASE7_PROMOTION",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "excluded_scopes",
    "presubmit_gate_sha256",
    "authorization_readiness_sha256",
    "decision_id",
    "pool_address",
    "executor_wallet_pubkey",
    "phase7_evidence_status_sha256",
    "phase7_evidence_plan_sha256",
    "input_preflight_sha256",
    "controlled_live_authorization_verification_sha256",
    "controlled_live_config_sha256",
    "proposal_sha256",
    "input_manifest_sha256",
    "execution_request_sha256",
    "risk_report_sha256",
    "transaction_guard_sha256",
    "wallet_authorization_sha256",
    "prepared_transaction_sha256",
    "final_simulation_sha256",
    "execution_intent_snapshot_sha256",
    "prepared_last_valid_block_height",
    "prepared_rpc_context_slot",
    "final_simulation_rpc_context_slot",
    "presubmit_evidence_ready",
    "transaction_execution_request_ready",
    "explicit_human_transaction_authorization_required",
    "fresh_presubmit_recheck_required",
    "fresh_blockhash_expiry_recheck_required",
    "live_submit_feature_required",
    "runtime_live_submit_opt_in_required",
    "confirmation_receipt_reconciliation_required",
    "separate_phase7_promotion_required",
    "transaction_execution_authorization_present",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "phase7_promotion_persisted",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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


def _load_presubmit_module(source: Path) -> Any:
    path = source / PRESUBMIT_GATE_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 7 presubmit tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[PRESUBMIT_GATE_TOOL]:
        raise ValueError("reviewed Phase 7 presubmit tool blob mismatch")
    return _load_module(path, "phase7_transaction_request_presubmit")


def validate_phase7_transaction_execution_request(
    request: dict[str, Any],
) -> None:
    if not isinstance(request, dict):
        raise ValueError("Phase 7 transaction request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("Phase 7 transaction request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 transaction request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 transaction request type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 transaction request lineage mismatch")
    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("Phase 7 transaction request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError("Phase 7 transaction request excluded scopes mismatch")

    for field in (
        "presubmit_gate_sha256",
        "authorization_readiness_sha256",
        "phase7_evidence_status_sha256",
        "phase7_evidence_plan_sha256",
        "input_preflight_sha256",
        "controlled_live_authorization_verification_sha256",
        "controlled_live_config_sha256",
        "proposal_sha256",
        "input_manifest_sha256",
        "execution_request_sha256",
        "risk_report_sha256",
        "transaction_guard_sha256",
        "wallet_authorization_sha256",
        "prepared_transaction_sha256",
        "final_simulation_sha256",
        "execution_intent_snapshot_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(f"Phase 7 transaction request {field} is invalid")

    for field in ("decision_id", "pool_address", "executor_wallet_pubkey"):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(f"Phase 7 transaction request {field} is invalid")

    for field in (
        "prepared_last_valid_block_height",
        "prepared_rpc_context_slot",
        "final_simulation_rpc_context_slot",
    ):
        value = request.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 7 transaction request {field} is invalid")
    if request["prepared_last_valid_block_height"] <= 0:
        raise ValueError("Phase 7 transaction request block-height ceiling is invalid")

    for field in (
        "presubmit_evidence_ready",
        "transaction_execution_request_ready",
        "explicit_human_transaction_authorization_required",
        "fresh_presubmit_recheck_required",
        "fresh_blockhash_expiry_recheck_required",
        "live_submit_feature_required",
        "runtime_live_submit_opt_in_required",
        "confirmation_receipt_reconciliation_required",
        "separate_phase7_promotion_required",
    ):
        if request.get(field) is not True:
            raise ValueError(f"Phase 7 transaction request requires {field}=true")

    for field in (
        "transaction_execution_authorization_present",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "phase7_promotion_persisted",
    ):
        if request.get(field) is not False:
            raise ValueError(f"Phase 7 transaction request requires {field}=false")

    identity = {field: request[field] for field in REQUEST_FIELDS}
    if request["request_sha256"] != _sha256(identity):
        raise ValueError("Phase 7 transaction request digest mismatch")


def build_phase7_transaction_execution_request(
    *,
    source_tree: str | Path,
    presubmit_gate_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    presubmit_module = _load_presubmit_module(source)
    presubmit = _load_json(
        presubmit_gate_path,
        label="Phase 7 presubmit evidence gate",
    )
    presubmit_module.validate_phase7_presubmit_evidence_gate(presubmit)

    if presubmit.get("presubmit_evidence_ready") is not True:
        raise ValueError("Phase 7 presubmit evidence is not ready")
    if presubmit.get("execution_intent_status") != "SIMULATION_PASSED":
        raise ValueError("Phase 7 presubmit intent is not SIMULATION_PASSED")
    if presubmit.get("signature_absent") is not True:
        raise ValueError("Phase 7 presubmit intent already has a signature")
    if presubmit.get("error_absent") is not True:
        raise ValueError("Phase 7 presubmit intent already has an error")
    if presubmit.get("requires_fresh_blockhash_expiry_recheck") is not True:
        raise ValueError("Phase 7 presubmit lost blockhash-expiry recheck")
    if presubmit.get("requires_separate_transaction_execution_gate") is not True:
        raise ValueError("Phase 7 presubmit lost transaction-execution boundary")

    for field in (
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "phase7_promotion_persisted",
    ):
        if presubmit.get(field) is not False:
            raise ValueError(f"Phase 7 presubmit unexpectedly authorizes {field}")

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
        "authorization_scope": AUTHORIZATION_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "presubmit_gate_sha256": presubmit["presubmit_gate_sha256"],
        "authorization_readiness_sha256": presubmit[
            "fresh_authorization_readiness_sha256"
        ],
        "decision_id": presubmit["decision_id"],
        "pool_address": presubmit["pool_address"],
        "executor_wallet_pubkey": presubmit["executor_wallet_pubkey"],
        "phase7_evidence_status_sha256": presubmit[
            "phase7_evidence_status_sha256"
        ],
        "phase7_evidence_plan_sha256": presubmit["phase7_evidence_plan_sha256"],
        "input_preflight_sha256": presubmit["input_preflight_sha256"],
        "controlled_live_authorization_verification_sha256": presubmit[
            "signed_authorization_verification_sha256"
        ],
        "controlled_live_config_sha256": presubmit[
            "controlled_live_config_sha256"
        ],
        "proposal_sha256": presubmit["proposal_sha256"],
        "input_manifest_sha256": presubmit["input_manifest_sha256"],
        "execution_request_sha256": presubmit["execution_request_sha256"],
        "risk_report_sha256": presubmit["risk_report_sha256"],
        "transaction_guard_sha256": presubmit["transaction_guard_sha256"],
        "wallet_authorization_sha256": presubmit[
            "wallet_authorization_sha256"
        ],
        "prepared_transaction_sha256": presubmit[
            "prepared_transaction_sha256"
        ],
        "final_simulation_sha256": presubmit["final_simulation_sha256"],
        "execution_intent_snapshot_sha256": presubmit[
            "execution_intent_snapshot_sha256"
        ],
        "prepared_last_valid_block_height": presubmit[
            "prepared_last_valid_block_height"
        ],
        "prepared_rpc_context_slot": presubmit["prepared_rpc_context_slot"],
        "final_simulation_rpc_context_slot": presubmit[
            "final_simulation_rpc_context_slot"
        ],
        "presubmit_evidence_ready": True,
        "transaction_execution_request_ready": True,
        "explicit_human_transaction_authorization_required": True,
        "fresh_presubmit_recheck_required": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "live_submit_feature_required": True,
        "runtime_live_submit_opt_in_required": True,
        "confirmation_receipt_reconciliation_required": True,
        "separate_phase7_promotion_required": True,
        "transaction_execution_authorization_present": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
    }
    request = {
        **identity,
        "request_sha256": _sha256(identity),
    }
    validate_phase7_transaction_execution_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request for one exact Phase 7 transaction "
            "attempt from a ready presubmit evidence gate. This tool does not "
            "access production, load keys, sign, submit, or authorize live capital."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--presubmit-gate", required=True)
    args = parser.parse_args()

    request = build_phase7_transaction_execution_request(
        source_tree=args.source_tree,
        presubmit_gate_path=args.presubmit_gate,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
