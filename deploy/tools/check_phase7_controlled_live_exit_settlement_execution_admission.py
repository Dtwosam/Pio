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
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_EXECUTION_ADMISSION_V1"
)

READINESS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_settlement_execution_readiness.py"
)
RUST_WALLET = Path("rust-executor/src/wallet.rs")
RUST_MAIN = Path("rust-executor/src/main.rs")
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "432118fb35f785df4a39910b4a45aab02a85388e",
    RUST_WALLET: "30d183f3fcf49ec4c0f78c9b5a5c2fde0a520967",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
}

KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"
WALLET_STATUS_COMMAND = "wallet-status"

DYNAMIC_READINESS_FIELDS = frozenset(
    {
        "current_block_height",
        "block_height_remaining",
        "readiness_checked_at",
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
    "opened_decision_id",
    "exit_decision_id",
    "exit_signature",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "destination_config_sha256",
    "final_settlement_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "saved_current_block_height",
    "fresh_current_block_height",
    "fresh_block_height_remaining",
    "block_height_monotonic",
    "blockhash_not_expired",
    "rpc_endpoint_sha256",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "executor_binary_hash_matches_expected",
    "executor_binary_hash_trust_external",
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
    "human_exact_settlement_transaction_authorization_verified",
    "transaction_authorization_not_expired",
    "final_transaction_still_unsigned",
    "final_exact_simulation_still_succeeded",
    "settlement_execution_admission_ready",
    "requires_immediate_single_execution",
    "requires_fresh_blockhash_recheck_at_execution",
    "requires_separate_single_shot_submitter",
    "requires_post_settlement_confirmation",
    "requires_post_close_account_absence_proof",
    "requires_post_exit_state_reconciliation",
    "requires_separate_phase7_promotion_action",
    "settlement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_entry_authorized",
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


def _load_readiness_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT settlement admission dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT settlement admission dependency mismatch: {relative}"
            )
    return _load_module(
        source / READINESS_TOOL,
        "phase7_exit_settlement_execution_admission_readiness",
    )


def _regular_file(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


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
    digest = _sha256_bytes(binary.read_bytes())
    if digest != expected_sha256:
        raise ValueError(
            "executor binary SHA-256 does not match external trust root"
        )
    return binary, digest


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
    executor_binary: Path,
    keypair_path: Path,
) -> dict[str, Any]:
    env = dict(os.environ)
    env[KEYPAIR_ENV] = str(keypair_path)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("SOLANA_RPC_URL", None)
    env.pop("RPC_URL", None)

    completed = subprocess.run(
        [str(executor_binary), WALLET_STATUS_COMMAND],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        raise ValueError(
            "executor wallet-status failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("executor wallet-status returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("executor wallet-status returned invalid result")
    return value


def _readiness_static_binding(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if key not in DYNAMIC_READINESS_FIELDS
    }


def validate_exit_settlement_execution_admission(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT settlement execution admission must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"admission_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement execution admission schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement execution admission format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement execution admission type"
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
            "Phase 7 EXIT settlement execution admission lineage mismatch"
        )

    for field in (
        "saved_readiness_sha256",
        "expected_saved_readiness_sha256",
        "fresh_readiness_sha256",
        "readiness_static_binding_sha256",
        "destination_config_sha256",
        "final_settlement_transaction_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "keypair_path_sha256",
        "wallet_status_sha256",
        "admission_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement admission {field} is invalid"
            )

    if (
        report["saved_readiness_sha256"]
        != report["expected_saved_readiness_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement admission saved readiness digest mismatch"
        )
    if (
        report["executor_binary_sha256"]
        != report["expected_executor_binary_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement admission binary trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "exit_signature",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "recent_blockhash",
        "executor_binary_path",
        "wallet_status_command",
        "wallet_keypair_source",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT settlement admission {field} is invalid"
            )

    for field in (
        "last_valid_block_height",
        "saved_current_block_height",
        "fresh_current_block_height",
        "fresh_block_height_remaining",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT settlement admission {field} is invalid"
            )
    if (
        report["fresh_current_block_height"]
        < report["saved_current_block_height"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement admission block height regressed"
        )
    if (
        report["fresh_current_block_height"]
        > report["last_valid_block_height"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement admission blockhash expired"
        )
    if (
        report["fresh_block_height_remaining"]
        != report["last_valid_block_height"]
        - report["fresh_current_block_height"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement admission block-height remainder mismatch"
        )
    if report["wallet_status_command"] != WALLET_STATUS_COMMAND:
        raise ValueError(
            "Phase 7 EXIT settlement admission wallet-status command mismatch"
        )
    if report["wallet_keypair_source"] != KEYPAIR_ENV:
        raise ValueError(
            "Phase 7 EXIT settlement admission keypair source mismatch"
        )

    for field in (
        "block_height_monotonic",
        "blockhash_not_expired",
        "executor_binary_hash_matches_expected",
        "executor_binary_hash_trust_external",
        "runtime_live_submit_opt_in_present",
        "keypair_path_absolute",
        "keypair_path_not_symlink",
        "keypair_path_regular_file",
        "keypair_owner_only_permissions",
        "wallet_identity_matches_readiness",
        "executor_keypair_identity_verified",
        "fresh_readiness_valid",
        "fresh_readiness_static_binding_matches_saved",
        "human_exact_settlement_transaction_authorization_verified",
        "transaction_authorization_not_expired",
        "final_transaction_still_unsigned",
        "final_exact_simulation_still_succeeded",
        "settlement_execution_admission_ready",
        "requires_immediate_single_execution",
        "requires_fresh_blockhash_recheck_at_execution",
        "requires_separate_single_shot_submitter",
        "requires_post_settlement_confirmation",
        "requires_post_close_account_absence_proof",
        "requires_post_exit_state_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement admission requires {field}=true"
            )

    if report.get("wrapper_read_private_key_bytes") is not False:
        raise ValueError(
            "Phase 7 EXIT settlement admission wrapper must not read private key bytes"
        )

    for field in (
        "settlement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement admission requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["admission_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 7 EXIT settlement admission digest mismatch"
        )


def build_exit_settlement_execution_admission(
    *,
    source_tree: str | Path,
    saved_readiness_path: str | Path,
    expected_saved_readiness_sha256: str,
    saved_finalization_path: str | Path,
    expected_finalization_sha256: str,
    saved_authorization_verification_path: str | Path,
    expected_saved_authorization_verification_sha256: str,
    authorization_request_path: str | Path,
    authorization_payload_path: str | Path,
    authorization_signature_path: str | Path,
    authorization_allowed_signers_path: str | Path,
    expected_authorization_allowed_signers_sha256: str,
    rpc_url: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    readiness_module = _load_readiness_module(source)

    saved = _load_json(
        saved_readiness_path,
        label="saved Phase 7 EXIT settlement execution readiness",
    )
    readiness_module.validate_exit_settlement_execution_readiness(saved)
    if not _is_hex_digest(expected_saved_readiness_sha256, 64):
        raise ValueError(
            "expected saved Phase 7 EXIT settlement readiness digest is invalid"
        )
    if saved["readiness_sha256"] != expected_saved_readiness_sha256:
        raise ValueError(
            "saved Phase 7 EXIT settlement readiness digest mismatch"
        )

    fresh = readiness_module.build_exit_settlement_execution_readiness(
        source_tree=source,
        saved_finalization_path=saved_finalization_path,
        expected_finalization_sha256=expected_finalization_sha256,
        saved_authorization_verification_path=(
            saved_authorization_verification_path
        ),
        expected_saved_authorization_verification_sha256=(
            expected_saved_authorization_verification_sha256
        ),
        authorization_request_path=authorization_request_path,
        authorization_payload_path=authorization_payload_path,
        authorization_signature_path=authorization_signature_path,
        authorization_allowed_signers_path=(
            authorization_allowed_signers_path
        ),
        expected_authorization_allowed_signers_sha256=(
            expected_authorization_allowed_signers_sha256
        ),
        rpc_url=rpc_url,
        now=now,
    )

    saved_static = _readiness_static_binding(saved)
    fresh_static = _readiness_static_binding(fresh)
    if fresh_static != saved_static:
        raise ValueError(
            "fresh Phase 7 EXIT settlement readiness static binding differs from saved readiness"
        )
    if fresh["current_block_height"] < saved["current_block_height"]:
        raise ValueError(
            "fresh Phase 7 EXIT settlement readiness block height regressed"
        )

    if os.environ.get(LIVE_SUBMIT_ENV) != "1":
        raise ValueError(
            f"{LIVE_SUBMIT_ENV}=1 is required for settlement execution admission"
        )
    keypair_raw = os.environ.get(KEYPAIR_ENV)
    if not keypair_raw:
        raise ValueError(
            f"{KEYPAIR_ENV} is required for settlement execution admission"
        )
    keypair_path, path_checks = _validate_keypair_path(keypair_raw)
    binary, binary_sha = _verify_executor_binary(
        executor_binary_path=executor_binary_path,
        expected_sha256=expected_executor_binary_sha256,
    )
    wallet_status = _wallet_status(
        executor_binary=binary,
        keypair_path=keypair_path,
    )
    if wallet_status.get("pubkey") != saved["executor_wallet_pubkey"]:
        raise ValueError(
            "executor keypair wallet differs from settlement readiness"
        )
    if wallet_status.get("keypair_source") != KEYPAIR_ENV:
        raise ValueError(
            "executor wallet-status keypair source mismatch"
        )
    if wallet_status.get("permissions_verified") is not True:
        raise ValueError(
            "executor wallet-status did not verify keypair permissions"
        )

    for field in (
        "human_exact_settlement_transaction_authorization_verified",
        "exact_settlement_authorization_not_expired",
        "final_transaction_unsigned",
        "final_exact_simulation_succeeded",
        "settlement_execution_readiness_ready",
        "requires_keypair_identity_admission",
        "requires_immediate_execution_after_readiness",
        "requires_fresh_blockhash_recheck_at_execution",
        "requires_single_submission_only",
    ):
        if fresh.get(field) is not True:
            raise ValueError(
                f"fresh Phase 7 EXIT settlement readiness lost {field}"
            )

    for artifact, label in (
        (saved, "saved settlement execution readiness"),
        (fresh, "fresh settlement execution readiness"),
    ):
        for field in (
            "settlement_authorized",
            "transaction_signing_authorized",
            "transaction_submission_authorized",
            "automatic_resubmission_authorized",
            "new_live_entry_authorized",
            "new_live_capital_authorized",
            "phase7_promotion_authorized",
            "phase7_promotion_persisted",
            "production_file_modified",
            "production_repository_git_mutated",
            "production_pio_database_modified",
        ):
            if artifact.get(field) is not False:
                raise ValueError(
                    f"{label} unexpectedly authorizes {field}"
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
        "saved_readiness_sha256": saved["readiness_sha256"],
        "expected_saved_readiness_sha256": (
            expected_saved_readiness_sha256
        ),
        "fresh_readiness_sha256": fresh["readiness_sha256"],
        "readiness_static_binding_sha256": _sha256_value(saved_static),
        "opened_decision_id": saved["opened_decision_id"],
        "exit_decision_id": saved["exit_decision_id"],
        "exit_signature": saved["exit_signature"],
        "pool_address": saved["pool_address"],
        "position_address": saved["position_address"],
        "executor_wallet_pubkey": saved["executor_wallet_pubkey"],
        "destination_config_sha256": saved["destination_config_sha256"],
        "final_settlement_transaction_sha256": saved[
            "final_settlement_transaction_sha256"
        ],
        "recent_blockhash": saved["recent_blockhash"],
        "last_valid_block_height": saved["last_valid_block_height"],
        "saved_current_block_height": saved["current_block_height"],
        "fresh_current_block_height": fresh["current_block_height"],
        "fresh_block_height_remaining": fresh["block_height_remaining"],
        "block_height_monotonic": True,
        "blockhash_not_expired": True,
        "rpc_endpoint_sha256": saved["rpc_endpoint_sha256"],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": expected_executor_binary_sha256,
        "executor_binary_hash_matches_expected": True,
        "executor_binary_hash_trust_external": True,
        "runtime_live_submit_opt_in_present": True,
        "keypair_path_sha256": _sha256_text(str(keypair_path)),
        "keypair_path_absolute": path_checks["absolute"],
        "keypair_path_not_symlink": path_checks["not_symlink"],
        "keypair_path_regular_file": path_checks["regular"],
        "keypair_owner_only_permissions": path_checks["owner_only"],
        "wallet_status_command": WALLET_STATUS_COMMAND,
        "wallet_status_sha256": _sha256_value(wallet_status),
        "wallet_keypair_source": wallet_status["keypair_source"],
        "wallet_identity_matches_readiness": True,
        "executor_keypair_identity_verified": True,
        "wrapper_read_private_key_bytes": False,
        "fresh_readiness_valid": True,
        "fresh_readiness_static_binding_matches_saved": True,
        "human_exact_settlement_transaction_authorization_verified": True,
        "transaction_authorization_not_expired": True,
        "final_transaction_still_unsigned": True,
        "final_exact_simulation_still_succeeded": True,
        "settlement_execution_admission_ready": True,
        "requires_immediate_single_execution": True,
        "requires_fresh_blockhash_recheck_at_execution": True,
        "requires_separate_single_shot_submitter": True,
        "requires_post_settlement_confirmation": True,
        "requires_post_close_account_absence_proof": True,
        "requires_post_exit_state_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "settlement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "admission_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_settlement_execution_admission(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Perform keypair-identity admission for one exact Phase 7 EXIT "
            "settlement transaction: rebuild readiness, recheck block height, "
            "verify the trusted executor binary and confirm the isolated "
            "keypair maps to the expected wallet. This wrapper never reads "
            "private key bytes and performs no signing or submission."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--expected-saved-readiness-sha256", required=True)
    parser.add_argument("--saved-finalization", required=True)
    parser.add_argument("--expected-finalization-sha256", required=True)
    parser.add_argument("--saved-authorization-verification", required=True)
    parser.add_argument(
        "--expected-saved-authorization-verification-sha256",
        required=True,
    )
    parser.add_argument("--authorization-request", required=True)
    parser.add_argument("--authorization-payload", required=True)
    parser.add_argument("--authorization-signature", required=True)
    parser.add_argument("--authorization-allowed-signers", required=True)
    parser.add_argument(
        "--expected-authorization-allowed-signers-sha256",
        required=True,
    )
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_exit_settlement_execution_admission(
        source_tree=args.source_tree,
        saved_readiness_path=args.saved_readiness,
        expected_saved_readiness_sha256=(
            args.expected_saved_readiness_sha256
        ),
        saved_finalization_path=args.saved_finalization,
        expected_finalization_sha256=args.expected_finalization_sha256,
        saved_authorization_verification_path=(
            args.saved_authorization_verification
        ),
        expected_saved_authorization_verification_sha256=(
            args.expected_saved_authorization_verification_sha256
        ),
        authorization_request_path=args.authorization_request,
        authorization_payload_path=args.authorization_payload,
        authorization_signature_path=args.authorization_signature,
        authorization_allowed_signers_path=(
            args.authorization_allowed_signers
        ),
        expected_authorization_allowed_signers_sha256=(
            args.expected_authorization_allowed_signers_sha256
        ),
        rpc_url=args.rpc_url,
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=(
            args.expected_executor_binary_sha256
        ),
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
