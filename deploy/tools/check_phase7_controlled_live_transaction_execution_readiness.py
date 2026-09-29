from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any
from urllib import request as urllib_request
from urllib.parse import urlparse


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_TRANSACTION_EXECUTION_READINESS_V1"

PRESUBMIT_GATE_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_presubmit_evidence.py"
)
TRANSACTION_REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_transaction_request.py"
)
TRANSACTION_SIGNER_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_transaction_signed_authorization.py"
)
EXECUTOR_CARGO = Path("rust-executor/Cargo.toml")
EXECUTOR_MAIN = Path("rust-executor/src/main.rs")
EXECUTOR_SUBMISSION = Path("rust-executor/src/submission.rs")
EXECUTOR_BLOCKHASH = Path("rust-executor/src/blockhash.rs")

REVIEWED_SOURCE_BLOBS = {
    PRESUBMIT_GATE_TOOL: "180980842a673354b024db78195b6bfe0209ab6f",
    TRANSACTION_REQUEST_TOOL: "068173f916999bf0bef5c61dcf6cc996b8811804",
    TRANSACTION_SIGNER_TOOL: "4889d419f559503add46282fe40bb2d71ccd5ee0",
    EXECUTOR_CARGO: "3cef1e243d550d17892ccc13dbdafd387fa1e46c",
    EXECUTOR_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    EXECUTOR_SUBMISSION: "8a8ca557963d2216ca30e9946800e75959f2701c",
    EXECUTOR_BLOCKHASH: "792153a0bb1527b16d7f7fabf40fed3752225df4",
}

LIVE_SUBMIT_RUNTIME_GUARD = (
    b"controlled live submission is runtime-disabled; "
    b"set PIO_LIVE_SUBMIT_ENABLED=1 only for an explicitly approved controlled-live run"
)
LIVE_SUBMIT_ENV_NAME = "PIO_LIVE_SUBMIT_ENABLED"
RPC_COMMITMENT = "confirmed"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_presubmit_gate_sha256",
    "fresh_presubmit_gate_sha256",
    "saved_transaction_request_sha256",
    "fresh_transaction_request_sha256",
    "saved_transaction_authorization_verification_sha256",
    "fresh_transaction_authorization_verification_sha256",
    "decision_id",
    "pool_address",
    "executor_wallet_pubkey",
    "prepared_transaction_sha256",
    "final_simulation_sha256",
    "execution_intent_snapshot_sha256",
    "prepared_last_valid_block_height",
    "current_block_height",
    "block_height_remaining",
    "blockhash_not_expired",
    "rpc_commitment",
    "rpc_endpoint_sha256",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "executor_binary_hash_matches_expected",
    "executor_binary_hash_trust_external",
    "live_submit_feature_verified",
    "runtime_live_submit_opt_in_present",
    "fresh_presubmit_matches_saved",
    "fresh_request_matches_saved",
    "fresh_transaction_authorization_matches_saved",
    "human_transaction_execution_authorization_verified",
    "transaction_authorization_not_expired",
    "execution_intent_still_unsigned",
    "execution_intent_still_error_free",
    "final_simulation_still_succeeded",
    "transaction_execution_readiness_ready",
    "readiness_only",
    "requires_immediate_execution_after_readiness",
    "requires_executor_native_blockhash_recheck",
    "requires_single_submission_only",
    "requires_confirmation_receipt_reconciliation",
    "requires_separate_phase7_promotion_action",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "phase7_promotion_persisted",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
    "production_execution_database_modified",
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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 transaction-readiness dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 transaction-readiness dependency mismatch: {relative}"
            )

    presubmit = _load_module(
        source / PRESUBMIT_GATE_TOOL,
        "phase7_transaction_readiness_presubmit",
    )
    request_module = _load_module(
        source / TRANSACTION_REQUEST_TOOL,
        "phase7_transaction_readiness_request",
    )
    signer = _load_module(
        source / TRANSACTION_SIGNER_TOOL,
        "phase7_transaction_readiness_signer",
    )
    return presubmit, request_module, signer


def _verify_executor_binary(
    *,
    executor_binary_path: str | Path,
    expected_sha256: str,
) -> tuple[Path, str]:
    if not _is_hex_digest(expected_sha256, 64):
        raise ValueError("expected executor binary SHA-256 is invalid")
    binary = _regular_file(
        executor_binary_path,
        label="Phase 7 executor binary",
    )
    payload = binary.read_bytes()
    digest = _sha256_bytes(payload)
    if digest != expected_sha256:
        raise ValueError("executor binary SHA-256 does not match external trust root")
    if LIVE_SUBMIT_RUNTIME_GUARD not in payload:
        raise ValueError(
            "executor binary does not expose the reviewed live-submit runtime guard"
        )
    return binary, digest


def _rpc_block_height(
    rpc_url: str,
    *,
    timeout_seconds: float = 5.0,
) -> int:
    if not isinstance(rpc_url, str) or not rpc_url.strip():
        raise ValueError("Solana RPC URL is required")
    parsed = urlparse(rpc_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Solana RPC URL must use http or https")

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBlockHeight",
            "params": [{"commitment": RPC_COMMITMENT}],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    req = urllib_request.Request(
        rpc_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read()
    except Exception as exc:
        raise ValueError("failed to fetch current Solana block height") from exc

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Solana block-height response is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Solana block-height response is invalid")
    if value.get("error") is not None:
        raise ValueError("Solana block-height RPC returned an error")
    height = value.get("result")
    if not isinstance(height, int) or isinstance(height, bool) or height < 0:
        raise ValueError("Solana block-height result is invalid")
    return height


def validate_transaction_execution_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 transaction execution readiness must be an object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("Phase 7 transaction execution readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 transaction readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 transaction readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 transaction readiness lineage mismatch")

    for field in (
        "saved_presubmit_gate_sha256",
        "fresh_presubmit_gate_sha256",
        "saved_transaction_request_sha256",
        "fresh_transaction_request_sha256",
        "saved_transaction_authorization_verification_sha256",
        "fresh_transaction_authorization_verification_sha256",
        "prepared_transaction_sha256",
        "final_simulation_sha256",
        "execution_intent_snapshot_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 transaction readiness {field} is invalid")

    for field in (
        "decision_id",
        "pool_address",
        "executor_wallet_pubkey",
        "executor_binary_path",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 7 transaction readiness {field} is invalid")

    for field in (
        "prepared_last_valid_block_height",
        "current_block_height",
        "block_height_remaining",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 7 transaction readiness {field} is invalid")

    if report["current_block_height"] > report["prepared_last_valid_block_height"]:
        raise ValueError("Phase 7 prepared blockhash is expired")
    if report["block_height_remaining"] != (
        report["prepared_last_valid_block_height"]
        - report["current_block_height"]
    ):
        raise ValueError("Phase 7 block-height remaining calculation mismatch")
    if report.get("rpc_commitment") != RPC_COMMITMENT:
        raise ValueError("Phase 7 RPC commitment mismatch")

    if (
        report["saved_presubmit_gate_sha256"]
        != report["fresh_presubmit_gate_sha256"]
    ):
        raise ValueError("Phase 7 fresh presubmit gate differs from saved gate")
    if (
        report["saved_transaction_request_sha256"]
        != report["fresh_transaction_request_sha256"]
    ):
        raise ValueError("Phase 7 fresh transaction request differs from saved request")
    if (
        report["saved_transaction_authorization_verification_sha256"]
        != report["fresh_transaction_authorization_verification_sha256"]
    ):
        raise ValueError(
            "Phase 7 fresh transaction authorization differs from saved verification"
        )
    if (
        report["executor_binary_sha256"]
        != report["expected_executor_binary_sha256"]
    ):
        raise ValueError("Phase 7 executor binary trust-root mismatch")

    for field in (
        "blockhash_not_expired",
        "executor_binary_hash_matches_expected",
        "executor_binary_hash_trust_external",
        "live_submit_feature_verified",
        "runtime_live_submit_opt_in_present",
        "fresh_presubmit_matches_saved",
        "fresh_request_matches_saved",
        "fresh_transaction_authorization_matches_saved",
        "human_transaction_execution_authorization_verified",
        "transaction_authorization_not_expired",
        "execution_intent_still_unsigned",
        "execution_intent_still_error_free",
        "final_simulation_still_succeeded",
        "transaction_execution_readiness_ready",
        "readiness_only",
        "requires_immediate_execution_after_readiness",
        "requires_executor_native_blockhash_recheck",
        "requires_single_submission_only",
        "requires_confirmation_receipt_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 transaction readiness requires {field}=true"
            )

    for field in (
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
        "production_execution_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 transaction readiness requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["readiness_sha256"] != expected:
        raise ValueError("Phase 7 transaction execution readiness digest mismatch")


def build_transaction_execution_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    phase6_post_promotion_audit_path: str | Path,
    saved_phase7_evidence_status_path: str | Path,
    saved_phase7_evidence_plan_path: str | Path,
    saved_input_preflight_path: str | Path,
    controlled_live_config_path: str | Path,
    proposal_path: str | Path,
    executor_wallet_pubkey: str,
    saved_controlled_live_authorization_verification_path: str | Path,
    controlled_live_signed_payload_path: str | Path,
    controlled_live_signature_path: str | Path,
    controlled_live_allowed_signers_path: str | Path,
    expected_controlled_live_allowed_signers_sha256: str,
    saved_authorization_readiness_path: str | Path,
    expected_authorization_readiness_sha256: str,
    execution_database_path: str | Path,
    saved_presubmit_gate_path: str | Path,
    transaction_request_path: str | Path,
    saved_transaction_authorization_verification_path: str | Path,
    transaction_signed_payload_path: str | Path,
    transaction_signature_path: str | Path,
    transaction_allowed_signers_path: str | Path,
    expected_transaction_allowed_signers_sha256: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    presubmit_module, request_module, signer_module = _load_reviewed_modules(source)

    saved_presubmit = _load_json(
        saved_presubmit_gate_path,
        label="saved Phase 7 presubmit gate",
    )
    saved_request = _load_json(
        transaction_request_path,
        label="saved Phase 7 transaction request",
    )
    saved_transaction_verification = _load_json(
        saved_transaction_authorization_verification_path,
        label="saved Phase 7 transaction authorization verification",
    )
    presubmit_module.validate_phase7_presubmit_evidence_gate(saved_presubmit)
    request_module.validate_phase7_transaction_execution_request(saved_request)
    signer_module.validate_verification(saved_transaction_verification)

    if saved_request.get("presubmit_gate_sha256") != saved_presubmit.get(
        "presubmit_gate_sha256"
    ):
        raise ValueError("Phase 7 transaction request/presubmit binding mismatch")
    if saved_transaction_verification.get("request_sha256") != saved_request.get(
        "request_sha256"
    ):
        raise ValueError("Phase 7 transaction authorization/request binding mismatch")
    if saved_transaction_verification.get(
        "human_transaction_execution_authorization_verified"
    ) is not True:
        raise ValueError("Phase 7 human transaction authorization is not verified")

    fresh_presubmit = presubmit_module.build_phase7_presubmit_evidence_gate(
        repository=production,
        source_tree=source,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
        saved_phase7_evidence_status_path=saved_phase7_evidence_status_path,
        saved_phase7_evidence_plan_path=saved_phase7_evidence_plan_path,
        saved_input_preflight_path=saved_input_preflight_path,
        controlled_live_config_path=controlled_live_config_path,
        proposal_path=proposal_path,
        executor_wallet_pubkey=executor_wallet_pubkey,
        saved_signed_authorization_verification_path=(
            saved_controlled_live_authorization_verification_path
        ),
        signed_payload_path=controlled_live_signed_payload_path,
        signature_path=controlled_live_signature_path,
        allowed_signers_path=controlled_live_allowed_signers_path,
        expected_allowed_signers_sha256=(
            expected_controlled_live_allowed_signers_sha256
        ),
        saved_authorization_readiness_path=saved_authorization_readiness_path,
        expected_authorization_readiness_sha256=(
            expected_authorization_readiness_sha256
        ),
        execution_database_path=execution_database_path,
        now=now,
    )
    presubmit_module.validate_phase7_presubmit_evidence_gate(fresh_presubmit)
    if fresh_presubmit != saved_presubmit:
        raise ValueError("fresh Phase 7 presubmit gate differs from saved gate")

    with tempfile.TemporaryDirectory(prefix="pio-phase7-tx-readiness.") as tmp:
        fresh_presubmit_path = Path(tmp) / "fresh-presubmit.json"
        fresh_presubmit_path.write_text(
            json.dumps(fresh_presubmit, sort_keys=True),
            encoding="utf-8",
        )
        fresh_request = request_module.build_phase7_transaction_execution_request(
            source_tree=source,
            presubmit_gate_path=fresh_presubmit_path,
        )
    request_module.validate_phase7_transaction_execution_request(fresh_request)
    if fresh_request != saved_request:
        raise ValueError("fresh Phase 7 transaction request differs from saved request")

    fresh_transaction_verification = signer_module.verify_authorization(
        source_tree=source,
        request_path=transaction_request_path,
        payload_path=transaction_signed_payload_path,
        signature_path=transaction_signature_path,
        allowed_signers_path=transaction_allowed_signers_path,
        expected_allowed_signers_sha256=(
            expected_transaction_allowed_signers_sha256
        ),
        now=now,
    )
    signer_module.validate_verification(fresh_transaction_verification)
    if fresh_transaction_verification != saved_transaction_verification:
        raise ValueError(
            "fresh Phase 7 transaction authorization differs from saved verification"
        )

    binary, binary_sha = _verify_executor_binary(
        executor_binary_path=executor_binary_path,
        expected_sha256=expected_executor_binary_sha256,
    )
    if os.environ.get(LIVE_SUBMIT_ENV_NAME) != "1":
        raise ValueError(
            "Phase 7 runtime live-submit opt-in is absent from the readiness environment"
        )

    current_block_height = _rpc_block_height(rpc_url)
    last_valid = saved_request["prepared_last_valid_block_height"]
    if current_block_height > last_valid:
        raise ValueError("Phase 7 prepared transaction blockhash has expired")
    remaining = last_valid - current_block_height

    if saved_presubmit.get("signature_absent") is not True:
        raise ValueError("Phase 7 execution intent is no longer unsigned")
    if saved_presubmit.get("error_absent") is not True:
        raise ValueError("Phase 7 execution intent now has an error")
    if saved_presubmit.get("final_simulation_succeeded") is not True:
        raise ValueError("Phase 7 final simulation is no longer successful")

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
        "saved_presubmit_gate_sha256": saved_presubmit[
            "presubmit_gate_sha256"
        ],
        "fresh_presubmit_gate_sha256": fresh_presubmit[
            "presubmit_gate_sha256"
        ],
        "saved_transaction_request_sha256": saved_request["request_sha256"],
        "fresh_transaction_request_sha256": fresh_request["request_sha256"],
        "saved_transaction_authorization_verification_sha256": (
            saved_transaction_verification["verification_sha256"]
        ),
        "fresh_transaction_authorization_verification_sha256": (
            fresh_transaction_verification["verification_sha256"]
        ),
        "decision_id": saved_request["decision_id"],
        "pool_address": saved_request["pool_address"],
        "executor_wallet_pubkey": saved_request["executor_wallet_pubkey"],
        "prepared_transaction_sha256": saved_request[
            "prepared_transaction_sha256"
        ],
        "final_simulation_sha256": saved_request["final_simulation_sha256"],
        "execution_intent_snapshot_sha256": saved_request[
            "execution_intent_snapshot_sha256"
        ],
        "prepared_last_valid_block_height": last_valid,
        "current_block_height": current_block_height,
        "block_height_remaining": remaining,
        "blockhash_not_expired": True,
        "rpc_commitment": RPC_COMMITMENT,
        "rpc_endpoint_sha256": _sha256_text(rpc_url),
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": expected_executor_binary_sha256,
        "executor_binary_hash_matches_expected": True,
        "executor_binary_hash_trust_external": True,
        "live_submit_feature_verified": True,
        "runtime_live_submit_opt_in_present": True,
        "fresh_presubmit_matches_saved": True,
        "fresh_request_matches_saved": True,
        "fresh_transaction_authorization_matches_saved": True,
        "human_transaction_execution_authorization_verified": True,
        "transaction_authorization_not_expired": True,
        "execution_intent_still_unsigned": True,
        "execution_intent_still_error_free": True,
        "final_simulation_still_succeeded": True,
        "transaction_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_execution_after_readiness": True,
        "requires_executor_native_blockhash_recheck": True,
        "requires_single_submission_only": True,
        "requires_confirmation_receipt_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "production_execution_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_transaction_execution_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Perform the final read-only Phase 7 transaction execution readiness "
            "check. The gate reproduces presubmit/request/signature evidence, "
            "checks an externally trusted executor-binary hash, verifies the "
            "compiled live-submit guard by scanning the binary, requires the "
            "runtime opt-in in this environment, and reads current Solana block "
            "height. It never signs or submits a transaction."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase6-post-promotion-audit", required=True)
    parser.add_argument("--saved-phase7-evidence-status", required=True)
    parser.add_argument("--saved-phase7-evidence-plan", required=True)
    parser.add_argument("--saved-input-preflight", required=True)
    parser.add_argument("--controlled-live-config", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--executor-wallet-pubkey", required=True)
    parser.add_argument(
        "--saved-controlled-live-authorization-verification",
        required=True,
    )
    parser.add_argument("--controlled-live-signed-payload", required=True)
    parser.add_argument("--controlled-live-signature", required=True)
    parser.add_argument("--controlled-live-allowed-signers", required=True)
    parser.add_argument(
        "--expected-controlled-live-allowed-signers-sha256",
        required=True,
    )
    parser.add_argument("--saved-authorization-readiness", required=True)
    parser.add_argument("--expected-authorization-readiness-sha256", required=True)
    parser.add_argument("--execution-db", required=True)
    parser.add_argument("--saved-presubmit-gate", required=True)
    parser.add_argument("--transaction-request", required=True)
    parser.add_argument(
        "--saved-transaction-authorization-verification",
        required=True,
    )
    parser.add_argument("--transaction-signed-payload", required=True)
    parser.add_argument("--transaction-signature", required=True)
    parser.add_argument("--transaction-allowed-signers", required=True)
    parser.add_argument(
        "--expected-transaction-allowed-signers-sha256",
        required=True,
    )
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_transaction_execution_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        phase6_post_promotion_audit_path=args.phase6_post_promotion_audit,
        saved_phase7_evidence_status_path=args.saved_phase7_evidence_status,
        saved_phase7_evidence_plan_path=args.saved_phase7_evidence_plan,
        saved_input_preflight_path=args.saved_input_preflight,
        controlled_live_config_path=args.controlled_live_config,
        proposal_path=args.proposal,
        executor_wallet_pubkey=args.executor_wallet_pubkey,
        saved_controlled_live_authorization_verification_path=(
            args.saved_controlled_live_authorization_verification
        ),
        controlled_live_signed_payload_path=args.controlled_live_signed_payload,
        controlled_live_signature_path=args.controlled_live_signature,
        controlled_live_allowed_signers_path=args.controlled_live_allowed_signers,
        expected_controlled_live_allowed_signers_sha256=(
            args.expected_controlled_live_allowed_signers_sha256
        ),
        saved_authorization_readiness_path=args.saved_authorization_readiness,
        expected_authorization_readiness_sha256=(
            args.expected_authorization_readiness_sha256
        ),
        execution_database_path=args.execution_db,
        saved_presubmit_gate_path=args.saved_presubmit_gate,
        transaction_request_path=args.transaction_request,
        saved_transaction_authorization_verification_path=(
            args.saved_transaction_authorization_verification
        ),
        transaction_signed_payload_path=args.transaction_signed_payload,
        transaction_signature_path=args.transaction_signature,
        transaction_allowed_signers_path=args.transaction_allowed_signers,
        expected_transaction_allowed_signers_sha256=(
            args.expected_transaction_allowed_signers_sha256
        ),
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=args.expected_executor_binary_sha256,
        rpc_url=args.rpc_url,
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
