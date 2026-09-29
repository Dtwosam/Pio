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
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_SIGNED_TRANSACTION_VERIFICATION_V1"
)

REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_settlement_single_execution_request.py"
)
VERIFIER_SOURCE = Path(
    "rust-executor/src/bin/phase7-exit-settlement-signed-transaction-verifier.rs"
)
REVIEWED_SOURCE_BLOBS = {
    REQUEST_TOOL: "ce6bf3688f75d7af025875b56e9d6fe16c7b010f",
    VERIFIER_SOURCE: "66ce7152b12b56e17419790b5b6e43476339e065",
}

KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_request_sha256",
    "expected_request_sha256",
    "request_valid",
    "destination_config_sha256",
    "verifier_binary_path",
    "verifier_binary_sha256",
    "expected_verifier_binary_sha256",
    "signed_transaction_file_sha256",
    "request_final_settlement_transaction_sha256",
    "signed_transaction_sha256",
    "signature",
    "executor_wallet_pubkey",
    "recent_blockhash",
    "unsigned_message_matches_signed_message",
    "fee_payer_matches_executor",
    "blockhash_matches_request",
    "single_required_signature",
    "signed_transaction_has_one_signature",
    "signature_non_default",
    "signature_verified",
    "exact_signed_settlement_transaction_verified",
    "signing_environment_stripped",
    "live_submit_environment_stripped",
    "rpc_environment_stripped",
    "verification_only",
    "transaction_signing_performed",
    "transaction_submission_attempted",
    "automatic_retry_performed",
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


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


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


def _load_request_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT settlement signed-verification dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT settlement signed-verification dependency mismatch: {relative}"
            )
    return _load_module(
        source / REQUEST_TOOL,
        "phase7_exit_settlement_signed_transaction_verification_request",
    )


def _verification_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("SOLANA_RPC_URL", None)
    env.pop("RPC_URL", None)
    return env


def _run_verifier(
    *,
    verifier_binary: Path,
    request_path: Path,
    signed_transaction_path: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(verifier_binary),
            str(request_path),
            str(signed_transaction_path),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        raise ValueError(
            "Phase 7 EXIT settlement signed-transaction verifier failed with "
            f"return code {completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Phase 7 EXIT settlement signed-transaction verifier returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 7 EXIT settlement signed-transaction verifier returned invalid result"
        )
    return value


def validate_settlement_signed_transaction_verification(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT settlement signed-transaction verification must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"verification_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement signed-transaction verification schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement signed-transaction verification format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement signed-transaction verification type"
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
            "Phase 7 EXIT settlement signed-transaction verification lineage mismatch"
        )

    for field in (
        "saved_request_sha256",
        "expected_request_sha256",
        "destination_config_sha256",
        "verifier_binary_sha256",
        "expected_verifier_binary_sha256",
        "signed_transaction_file_sha256",
        "request_final_settlement_transaction_sha256",
        "signed_transaction_sha256",
        "verification_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement signed-verification {field} is invalid"
            )

    if report["saved_request_sha256"] != report["expected_request_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement signed-verification request digest mismatch"
        )
    if report["verifier_binary_sha256"] != report[
        "expected_verifier_binary_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement signed-verifier trust-root mismatch"
        )

    for field in (
        "signature",
        "executor_wallet_pubkey",
        "recent_blockhash",
        "verifier_binary_path",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT settlement signed-verification {field} is invalid"
            )

    for field in (
        "request_valid",
        "unsigned_message_matches_signed_message",
        "fee_payer_matches_executor",
        "blockhash_matches_request",
        "single_required_signature",
        "signed_transaction_has_one_signature",
        "signature_non_default",
        "signature_verified",
        "exact_signed_settlement_transaction_verified",
        "signing_environment_stripped",
        "live_submit_environment_stripped",
        "rpc_environment_stripped",
        "verification_only",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement signed-verification requires {field}=true"
            )

    for field in (
        "transaction_signing_performed",
        "transaction_submission_attempted",
        "automatic_retry_performed",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement signed-verification requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["verification_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 7 EXIT settlement signed-verification digest mismatch"
        )


def build_settlement_signed_transaction_verification(
    *,
    source_tree: str | Path,
    saved_request_path: str | Path,
    expected_request_sha256: str,
    signed_transaction_path: str | Path,
    verifier_binary_path: str | Path,
    expected_verifier_binary_sha256: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    request_module = _load_request_module(source)

    request_path = _regular_file(
        saved_request_path,
        label="saved Phase 7 EXIT settlement single-execution request",
    )
    request = _load_json(
        request_path,
        label="saved Phase 7 EXIT settlement single-execution request",
    )
    request_module.validate_exit_settlement_single_execution_request(request)
    if not _is_hex_digest(expected_request_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT settlement single-execution request digest is invalid"
        )
    if request["request_sha256"] != expected_request_sha256:
        raise ValueError(
            "saved Phase 7 EXIT settlement single-execution request digest mismatch"
        )

    signed_path = _regular_file(
        signed_transaction_path,
        label="Phase 7 EXIT signed settlement transaction",
    )
    verifier = _regular_executable(
        verifier_binary_path,
        label="Phase 7 EXIT settlement signed-transaction verifier binary",
    )
    verifier_sha = _sha256_path(verifier)
    if not _is_hex_digest(expected_verifier_binary_sha256, 64):
        raise ValueError(
            "expected settlement signed-verifier binary digest is invalid"
        )
    if verifier_sha != expected_verifier_binary_sha256:
        raise ValueError(
            "settlement signed-verifier binary differs from external trust root"
        )

    nested = _run_verifier(
        verifier_binary=verifier,
        request_path=request_path,
        signed_transaction_path=signed_path,
        env=_verification_env(),
    )
    expected_nested_fields = {
        "request_sha256",
        "destination_config_sha256",
        "final_settlement_transaction_sha256",
        "signed_transaction_sha256",
        "signature",
        "executor_wallet_pubkey",
        "recent_blockhash",
        "unsigned_message_matches_signed_message",
        "fee_payer_matches_executor",
        "blockhash_matches_request",
        "single_required_signature",
        "signed_transaction_has_one_signature",
        "signature_non_default",
        "signature_verified",
        "exact_signed_settlement_transaction_verified",
    }
    if set(nested) != expected_nested_fields:
        raise ValueError(
            "Phase 7 EXIT settlement signed-verifier report schema mismatch"
        )

    for nested_field, request_field in (
        ("request_sha256", "request_sha256"),
        ("destination_config_sha256", "destination_config_sha256"),
        (
            "final_settlement_transaction_sha256",
            "final_settlement_transaction_sha256",
        ),
        ("executor_wallet_pubkey", "executor_wallet_pubkey"),
        ("recent_blockhash", "recent_blockhash"),
    ):
        if nested.get(nested_field) != request.get(request_field):
            raise ValueError(
                f"Phase 7 EXIT settlement signed-verification {nested_field} binding mismatch"
            )

    for field in (
        "unsigned_message_matches_signed_message",
        "fee_payer_matches_executor",
        "blockhash_matches_request",
        "single_required_signature",
        "signed_transaction_has_one_signature",
        "signature_non_default",
        "signature_verified",
        "exact_signed_settlement_transaction_verified",
    ):
        if nested.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement signed-verifier requires {field}=true"
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
        "saved_request_sha256": request["request_sha256"],
        "expected_request_sha256": expected_request_sha256,
        "request_valid": True,
        "destination_config_sha256": request[
            "destination_config_sha256"
        ],
        "verifier_binary_path": str(verifier),
        "verifier_binary_sha256": verifier_sha,
        "expected_verifier_binary_sha256": (
            expected_verifier_binary_sha256
        ),
        "signed_transaction_file_sha256": _sha256_path(signed_path),
        "request_final_settlement_transaction_sha256": request[
            "final_settlement_transaction_sha256"
        ],
        "signed_transaction_sha256": nested["signed_transaction_sha256"],
        "signature": nested["signature"],
        "executor_wallet_pubkey": nested["executor_wallet_pubkey"],
        "recent_blockhash": nested["recent_blockhash"],
        "unsigned_message_matches_signed_message": True,
        "fee_payer_matches_executor": True,
        "blockhash_matches_request": True,
        "single_required_signature": True,
        "signed_transaction_has_one_signature": True,
        "signature_non_default": True,
        "signature_verified": True,
        "exact_signed_settlement_transaction_verified": True,
        "signing_environment_stripped": True,
        "live_submit_environment_stripped": True,
        "rpc_environment_stripped": True,
        "verification_only": True,
        "transaction_signing_performed": False,
        "transaction_submission_attempted": False,
        "automatic_retry_performed": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "verification_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_settlement_signed_transaction_verification(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Seal proof that externally signed Phase 7 EXIT settlement bytes "
            "are the exact finalized settlement message. This tool strips "
            "keypair, live-submit and RPC environment and performs no signing "
            "or submission."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-request", required=True)
    parser.add_argument("--expected-request-sha256", required=True)
    parser.add_argument("--signed-transaction", required=True)
    parser.add_argument("--verifier-binary", required=True)
    parser.add_argument("--expected-verifier-binary-sha256", required=True)
    args = parser.parse_args()

    report = build_settlement_signed_transaction_verification(
        source_tree=args.source_tree,
        saved_request_path=args.saved_request,
        expected_request_sha256=args.expected_request_sha256,
        signed_transaction_path=args.signed_transaction,
        verifier_binary_path=args.verifier_binary,
        expected_verifier_binary_sha256=(
            args.expected_verifier_binary_sha256
        ),
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
