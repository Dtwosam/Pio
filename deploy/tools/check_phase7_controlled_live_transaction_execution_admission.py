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
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_TRANSACTION_EXECUTION_ADMISSION_V1"

READINESS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_transaction_execution_readiness.py"
)
RUST_WALLET = Path("rust-executor/src/wallet.rs")
RUST_MAIN = Path("rust-executor/src/main.rs")

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "61bb12102b2fb03593dc4bcb9d6130882a9809de",
    RUST_WALLET: "30d183f3fcf49ec4c0f78c9b5a5c2fde0a520967",
    RUST_MAIN: "4f02019bca92e77faed70083c740cfc111db15d1",
}

KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

DYNAMIC_READINESS_FIELDS = frozenset(
    {
        "current_block_height",
        "block_height_remaining",
        "readiness_sha256",
    }
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_readiness_sha256",
    "expected_saved_readiness_sha256",
    "fresh_readiness_sha256",
    "readiness_static_binding_sha256",
    "decision_id",
    "pool_address",
    "executor_wallet_pubkey",
    "prepared_transaction_sha256",
    "final_simulation_sha256",
    "execution_intent_snapshot_sha256",
    "prepared_last_valid_block_height",
    "saved_current_block_height",
    "fresh_current_block_height",
    "fresh_block_height_remaining",
    "block_height_monotonic",
    "blockhash_not_expired",
    "rpc_endpoint_sha256",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "runtime_live_submit_opt_in_present",
    "keypair_path_sha256",
    "keypair_path_absolute",
    "keypair_path_not_symlink",
    "keypair_path_regular_file",
    "keypair_owner_only_permissions",
    "wallet_status_command",
    "wallet_status_sha256",
    "wallet_keypair_source",
    "wallet_identity_matches_readiness",
    "executor_keypair_identity_verified",
    "wrapper_read_private_key_bytes",
    "fresh_readiness_valid",
    "fresh_readiness_static_binding_matches_saved",
    "human_transaction_execution_authorization_verified",
    "transaction_authorization_not_expired",
    "execution_intent_still_unsigned",
    "execution_intent_still_error_free",
    "final_simulation_still_succeeded",
    "execution_admission_ready",
    "requires_immediate_single_execution",
    "requires_fresh_blockhash_recheck_at_execution",
    "requires_separate_single_shot_submitter",
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


def _load_readiness_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 execution-admission dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 execution-admission dependency mismatch: {relative}"
            )
    return _load_module(
        source / READINESS_TOOL,
        "phase7_transaction_execution_admission_readiness",
    )


def _readiness_static_binding(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if key not in DYNAMIC_READINESS_FIELDS
    }


def _validate_keypair_path(raw: str) -> tuple[Path, dict[str, bool]]:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{KEYPAIR_ENV} is required")
    candidate = Path(raw).expanduser()
    absolute = candidate.is_absolute()
    if not absolute:
        raise ValueError(f"{KEYPAIR_ENV} must be an absolute path")

    try:
        metadata = os.lstat(candidate)
    except FileNotFoundError as exc:
        raise ValueError("executor keypair file is missing") from exc

    not_symlink = not stat.S_ISLNK(metadata.st_mode)
    if not not_symlink:
        raise ValueError("executor keypair path must not be a symlink")
    regular = stat.S_ISREG(metadata.st_mode)
    if not regular:
        raise ValueError("executor keypair path must be a regular file")

    owner_only = True
    if os.name == "posix":
        owner_only = (stat.S_IMODE(metadata.st_mode) & 0o077) == 0
        if not owner_only:
            raise ValueError(
                "executor keypair file permissions are too broad; expected owner-only access"
            )

    return candidate.resolve(strict=True), {
        "absolute": absolute,
        "not_symlink": not_symlink,
        "regular": regular,
        "owner_only": owner_only,
    }


def _wallet_status(
    *,
    executor_binary: str | Path,
    keypair_path: Path,
) -> dict[str, Any]:
    binary = Path(executor_binary).resolve(strict=True)
    env = dict(os.environ)
    env[KEYPAIR_ENV] = str(keypair_path)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("SOLANA_RPC_URL", None)
    env.pop("RPC_URL", None)

    completed = subprocess.run(
        [str(binary), "wallet-status"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        raise ValueError("executor wallet-status failed")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("executor wallet-status returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("executor wallet-status result is invalid")
    return value


def validate_execution_admission(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 execution admission must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"admission_sha256"}:
        raise ValueError("Phase 7 execution admission schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 execution admission format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 execution admission type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 execution admission lineage mismatch")

    for field in (
        "saved_readiness_sha256",
        "expected_saved_readiness_sha256",
        "fresh_readiness_sha256",
        "readiness_static_binding_sha256",
        "prepared_transaction_sha256",
        "final_simulation_sha256",
        "execution_intent_snapshot_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "keypair_path_sha256",
        "wallet_status_sha256",
        "admission_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 execution admission {field} is invalid")

    if report["saved_readiness_sha256"] != report["expected_saved_readiness_sha256"]:
        raise ValueError("Phase 7 saved readiness digest mismatch")

    if report["executor_binary_sha256"] != report["expected_executor_binary_sha256"]:
        raise ValueError("Phase 7 executor binary trust-root mismatch")

    for field in (
        "decision_id",
        "pool_address",
        "executor_wallet_pubkey",
        "executor_binary_path",
        "wallet_status_command",
        "wallet_keypair_source",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 7 execution admission {field} is invalid")

    for field in (
        "prepared_last_valid_block_height",
        "saved_current_block_height",
        "fresh_current_block_height",
        "fresh_block_height_remaining",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 7 execution admission {field} is invalid")

    if report["fresh_current_block_height"] < report["saved_current_block_height"]:
        raise ValueError("Phase 7 current block height moved backwards")
    if report["fresh_current_block_height"] > report["prepared_last_valid_block_height"]:
        raise ValueError("Phase 7 prepared transaction blockhash is expired")
    if report["fresh_block_height_remaining"] != (
        report["prepared_last_valid_block_height"]
        - report["fresh_current_block_height"]
    ):
        raise ValueError("Phase 7 block-height remaining calculation mismatch")

    for field in (
        "block_height_monotonic",
        "blockhash_not_expired",
        "runtime_live_submit_opt_in_present",
        "keypair_path_absolute",
        "keypair_path_not_symlink",
        "keypair_path_regular_file",
        "keypair_owner_only_permissions",
        "wallet_identity_matches_readiness",
        "executor_keypair_identity_verified",
        "fresh_readiness_valid",
        "fresh_readiness_static_binding_matches_saved",
        "human_transaction_execution_authorization_verified",
        "transaction_authorization_not_expired",
        "execution_intent_still_unsigned",
        "execution_intent_still_error_free",
        "final_simulation_still_succeeded",
        "execution_admission_ready",
        "requires_immediate_single_execution",
        "requires_fresh_blockhash_recheck_at_execution",
        "requires_separate_single_shot_submitter",
        "requires_confirmation_receipt_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 execution admission requires {field}=true")

    if report.get("wrapper_read_private_key_bytes") is not False:
        raise ValueError(
            "Phase 7 execution admission requires wrapper_read_private_key_bytes=false"
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
            raise ValueError(f"Phase 7 execution admission requires {field}=false")

    if report.get("wallet_status_command") != "wallet-status":
        raise ValueError("Phase 7 execution admission wallet-status command mismatch")
    if report.get("wallet_keypair_source") != KEYPAIR_ENV:
        raise ValueError("Phase 7 execution admission wallet keypair source mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["admission_sha256"] != expected:
        raise ValueError("Phase 7 execution admission digest mismatch")


def build_execution_admission(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_transaction_execution_readiness_path: str | Path,
    expected_saved_readiness_sha256: str,
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
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    readiness_module = _load_readiness_module(source)

    saved = _load_json(
        saved_transaction_execution_readiness_path,
        label="saved Phase 7 transaction execution readiness",
    )
    readiness_module.validate_transaction_execution_readiness(saved)

    if not _is_hex_digest(expected_saved_readiness_sha256, 64):
        raise ValueError("expected saved readiness SHA-256 is invalid")
    if saved.get("readiness_sha256") != expected_saved_readiness_sha256:
        raise ValueError("saved Phase 7 readiness digest does not match expected digest")
    if saved.get("transaction_execution_readiness_ready") is not True:
        raise ValueError("saved Phase 7 transaction execution readiness is not ready")
    if saved.get("readiness_only") is not True:
        raise ValueError("saved Phase 7 transaction readiness is not readiness-only")

    for field in (
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "phase7_promotion_persisted",
    ):
        if saved.get(field) is not False:
            raise ValueError(f"saved Phase 7 readiness unexpectedly authorizes {field}")

    fresh = readiness_module.build_transaction_execution_readiness(
        repository=repository,
        source_tree=source,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
        saved_phase7_evidence_status_path=saved_phase7_evidence_status_path,
        saved_phase7_evidence_plan_path=saved_phase7_evidence_plan_path,
        saved_input_preflight_path=saved_input_preflight_path,
        controlled_live_config_path=controlled_live_config_path,
        proposal_path=proposal_path,
        executor_wallet_pubkey=executor_wallet_pubkey,
        saved_controlled_live_authorization_verification_path=(
            saved_controlled_live_authorization_verification_path
        ),
        controlled_live_signed_payload_path=controlled_live_signed_payload_path,
        controlled_live_signature_path=controlled_live_signature_path,
        controlled_live_allowed_signers_path=controlled_live_allowed_signers_path,
        expected_controlled_live_allowed_signers_sha256=(
            expected_controlled_live_allowed_signers_sha256
        ),
        saved_authorization_readiness_path=saved_authorization_readiness_path,
        expected_authorization_readiness_sha256=(
            expected_authorization_readiness_sha256
        ),
        execution_database_path=execution_database_path,
        saved_presubmit_gate_path=saved_presubmit_gate_path,
        transaction_request_path=transaction_request_path,
        saved_transaction_authorization_verification_path=(
            saved_transaction_authorization_verification_path
        ),
        transaction_signed_payload_path=transaction_signed_payload_path,
        transaction_signature_path=transaction_signature_path,
        transaction_allowed_signers_path=transaction_allowed_signers_path,
        expected_transaction_allowed_signers_sha256=(
            expected_transaction_allowed_signers_sha256
        ),
        executor_binary_path=executor_binary_path,
        expected_executor_binary_sha256=expected_executor_binary_sha256,
        rpc_url=rpc_url,
        now=now,
    )
    readiness_module.validate_transaction_execution_readiness(fresh)

    saved_static = _readiness_static_binding(saved)
    fresh_static = _readiness_static_binding(fresh)
    if fresh_static != saved_static:
        raise ValueError("fresh Phase 7 readiness static binding differs from saved readiness")

    saved_height = int(saved["current_block_height"])
    fresh_height = int(fresh["current_block_height"])
    last_valid = int(fresh["prepared_last_valid_block_height"])
    if fresh_height < saved_height:
        raise ValueError("fresh Solana block height moved backwards")
    if fresh_height > last_valid:
        raise ValueError("prepared transaction blockhash expired before execution admission")

    if os.environ.get(LIVE_SUBMIT_ENV) != "1":
        raise ValueError(
            f"{LIVE_SUBMIT_ENV}=1 is required before execution admission can be evaluated"
        )

    keypair_raw = os.environ.get(KEYPAIR_ENV, "")
    keypair_path, keypair_policy = _validate_keypair_path(keypair_raw)
    wallet = _wallet_status(
        executor_binary=fresh["executor_binary_path"],
        keypair_path=keypair_path,
    )

    wallet_pubkey = wallet.get("pubkey")
    keypair_source = wallet.get("keypair_source")
    if wallet_pubkey != fresh["executor_wallet_pubkey"]:
        raise ValueError("loaded executor keypair public key differs from readiness wallet")
    if keypair_source != KEYPAIR_ENV:
        raise ValueError("executor wallet-status keypair source mismatch")

    static_digest = _sha256_bytes(_canonical_bytes(saved_static))
    wallet_digest = _sha256_bytes(_canonical_bytes(wallet))

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
        "saved_readiness_sha256": saved["readiness_sha256"],
        "expected_saved_readiness_sha256": expected_saved_readiness_sha256,
        "fresh_readiness_sha256": fresh["readiness_sha256"],
        "readiness_static_binding_sha256": static_digest,
        "decision_id": fresh["decision_id"],
        "pool_address": fresh["pool_address"],
        "executor_wallet_pubkey": fresh["executor_wallet_pubkey"],
        "prepared_transaction_sha256": fresh["prepared_transaction_sha256"],
        "final_simulation_sha256": fresh["final_simulation_sha256"],
        "execution_intent_snapshot_sha256": fresh[
            "execution_intent_snapshot_sha256"
        ],
        "prepared_last_valid_block_height": last_valid,
        "saved_current_block_height": saved_height,
        "fresh_current_block_height": fresh_height,
        "fresh_block_height_remaining": int(fresh["block_height_remaining"]),
        "block_height_monotonic": True,
        "blockhash_not_expired": True,
        "rpc_endpoint_sha256": fresh["rpc_endpoint_sha256"],
        "executor_binary_path": fresh["executor_binary_path"],
        "executor_binary_sha256": fresh["executor_binary_sha256"],
        "expected_executor_binary_sha256": fresh[
            "expected_executor_binary_sha256"
        ],
        "runtime_live_submit_opt_in_present": True,
        "keypair_path_sha256": _sha256_text(str(keypair_path)),
        "keypair_path_absolute": keypair_policy["absolute"],
        "keypair_path_not_symlink": keypair_policy["not_symlink"],
        "keypair_path_regular_file": keypair_policy["regular"],
        "keypair_owner_only_permissions": keypair_policy["owner_only"],
        "wallet_status_command": "wallet-status",
        "wallet_status_sha256": wallet_digest,
        "wallet_keypair_source": keypair_source,
        "wallet_identity_matches_readiness": True,
        "executor_keypair_identity_verified": True,
        "wrapper_read_private_key_bytes": False,
        "fresh_readiness_valid": True,
        "fresh_readiness_static_binding_matches_saved": True,
        "human_transaction_execution_authorization_verified": True,
        "transaction_authorization_not_expired": True,
        "execution_intent_still_unsigned": True,
        "execution_intent_still_error_free": True,
        "final_simulation_still_succeeded": True,
        "execution_admission_ready": True,
        "requires_immediate_single_execution": True,
        "requires_fresh_blockhash_recheck_at_execution": True,
        "requires_separate_single_shot_submitter": True,
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
        "admission_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_execution_admission(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Perform a keypair-bound, non-executing admission check immediately "
            "before any separately reviewed one-shot Phase 7 submitter. This "
            "tool re-runs final readiness and verifies the isolated executor "
            "wallet identity, but never signs or submits a transaction."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-transaction-execution-readiness", required=True)
    parser.add_argument("--expected-saved-readiness-sha256", required=True)
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

    report = build_execution_admission(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_transaction_execution_readiness_path=(
            args.saved_transaction_execution_readiness
        ),
        expected_saved_readiness_sha256=args.expected_saved_readiness_sha256,
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
