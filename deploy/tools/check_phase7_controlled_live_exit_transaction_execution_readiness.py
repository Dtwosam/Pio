from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any
from urllib import request as urllib_request
from urllib.parse import urlparse


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_TRANSACTION_EXECUTION_READINESS_V1"
)

FINALIZATION_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_transaction_finalization.py"
)
SIGNED_AUTHORIZATION_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_transaction_signed_authorization.py"
)
REVIEWED_SOURCE_BLOBS = {
    FINALIZATION_TOOL: "997ef77167b8b3443a7923083905157814cc91a7",
    SIGNED_AUTHORIZATION_TOOL: "9035d67599a76b4cb7cb26e17cfbadfeb8f0bf8a",
}

RPC_COMMITMENT = "confirmed"
MAX_CLOCK_SKEW_SECONDS = 30

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_finalization_sha256",
    "expected_finalization_sha256",
    "saved_authorization_verification_sha256",
    "fresh_authorization_verification_sha256",
    "authorization_verification_matches_saved",
    "opened_decision_id",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "final_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "current_block_height",
    "block_height_remaining",
    "blockhash_not_expired",
    "rpc_commitment",
    "rpc_endpoint_sha256",
    "decision_expires_at",
    "preparation_authorization_expires_at",
    "transaction_authorization_expires_at",
    "readiness_checked_at",
    "finalization_valid",
    "exact_transaction_authorization_signature_verified",
    "exact_transaction_authorization_not_expired",
    "preparation_authorization_not_expired",
    "decision_not_expired",
    "final_transaction_unsigned",
    "final_exact_simulation_succeeded",
    "human_exact_exit_transaction_authorization_verified",
    "exit_transaction_execution_readiness_ready",
    "readiness_only",
    "requires_keypair_identity_admission",
    "requires_immediate_execution_after_readiness",
    "requires_fresh_blockhash_recheck_at_execution",
    "requires_single_submission_only",
    "requires_confirmation_receipt_reconciliation",
    "requires_post_exit_closure_proof",
    "requires_post_exit_state_reconciliation",
    "requires_separate_phase7_promotion_action",
    "exit_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
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


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT execution-readiness dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT execution-readiness dependency mismatch: {relative}"
            )
    finalization = _load_module(
        source / FINALIZATION_TOOL,
        "phase7_exit_execution_readiness_finalization",
    )
    signer = _load_module(
        source / SIGNED_AUTHORIZATION_TOOL,
        "phase7_exit_execution_readiness_signer",
    )
    return finalization, signer


def _canonical_utc(raw: Any, *, label: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError(f"{label} must use UTC Z form")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            f"{label} must use YYYY-MM-DDTHH:MM:SSZ"
        ) from exc


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
            "failed to fetch Solana confirmed block height"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError("Solana block-height RPC returned invalid payload")
    if payload.get("error") is not None:
        raise ValueError("Solana block-height RPC returned an error")
    height = payload.get("result")
    if not isinstance(height, int) or isinstance(height, bool) or height < 0:
        raise ValueError("Solana block-height RPC returned invalid height")
    return height


def validate_exit_transaction_execution_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT transaction execution readiness format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT transaction execution readiness type"
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
            "Phase 7 EXIT transaction execution readiness lineage mismatch"
        )

    for field in (
        "saved_finalization_sha256",
        "expected_finalization_sha256",
        "saved_authorization_verification_sha256",
        "fresh_authorization_verification_sha256",
        "final_transaction_sha256",
        "rpc_endpoint_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT transaction execution readiness {field} is invalid"
            )

    if (
        report["saved_finalization_sha256"]
        != report["expected_finalization_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness finalization digest mismatch"
        )
    if (
        report["saved_authorization_verification_sha256"]
        != report["fresh_authorization_verification_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness authorization digest mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "recent_blockhash",
        "rpc_commitment",
        "decision_expires_at",
        "preparation_authorization_expires_at",
        "transaction_authorization_expires_at",
        "readiness_checked_at",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT transaction execution readiness {field} is invalid"
            )
    if report["rpc_commitment"] != RPC_COMMITMENT:
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness RPC commitment mismatch"
        )

    for field in (
        "last_valid_block_height",
        "current_block_height",
        "block_height_remaining",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT transaction execution readiness {field} is invalid"
            )
    if report["current_block_height"] > report["last_valid_block_height"]:
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness blockhash expired"
        )
    if (
        report["block_height_remaining"]
        != report["last_valid_block_height"] - report["current_block_height"]
    ):
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness block-height remainder mismatch"
        )

    checked = _canonical_utc(
        report["readiness_checked_at"],
        label="Phase 7 EXIT execution readiness checked_at",
    )
    transaction_expires = _canonical_utc(
        report["transaction_authorization_expires_at"],
        label="Phase 7 EXIT transaction authorization expires_at",
    )
    preparation_expires = _canonical_utc(
        report["preparation_authorization_expires_at"],
        label="Phase 7 EXIT preparation authorization expires_at",
    )
    decision_expires = _canonical_utc(
        report["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    if checked > transaction_expires:
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness authorization expired"
        )
    if checked > preparation_expires:
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness preparation authorization expired"
        )
    if checked > decision_expires:
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness decision expired"
        )

    for field in (
        "authorization_verification_matches_saved",
        "blockhash_not_expired",
        "finalization_valid",
        "exact_transaction_authorization_signature_verified",
        "exact_transaction_authorization_not_expired",
        "preparation_authorization_not_expired",
        "decision_not_expired",
        "final_transaction_unsigned",
        "final_exact_simulation_succeeded",
        "human_exact_exit_transaction_authorization_verified",
        "exit_transaction_execution_readiness_ready",
        "readiness_only",
        "requires_keypair_identity_admission",
        "requires_immediate_execution_after_readiness",
        "requires_fresh_blockhash_recheck_at_execution",
        "requires_single_submission_only",
        "requires_confirmation_receipt_reconciliation",
        "requires_post_exit_closure_proof",
        "requires_post_exit_state_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT transaction execution readiness requires {field}=true"
            )

    for field in (
        "exit_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
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
                f"Phase 7 EXIT transaction execution readiness requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 7 EXIT transaction execution readiness digest mismatch"
        )


def build_exit_transaction_execution_readiness(
    *,
    source_tree: str | Path,
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
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    finalization_module, signer_module = _load_reviewed(source)

    finalization = _load_json(
        saved_finalization_path,
        label="saved Phase 7 EXIT transaction finalization",
    )
    finalization_module.validate_exit_transaction_finalization(
        finalization
    )
    if not _is_hex_digest(expected_finalization_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT finalization digest is invalid"
        )
    if finalization["finalization_sha256"] != expected_finalization_sha256:
        raise ValueError(
            "saved Phase 7 EXIT finalization digest mismatch"
        )

    saved_verification = _load_json(
        saved_authorization_verification_path,
        label="saved Phase 7 EXIT transaction authorization verification",
    )
    signer_module.validate_verification(saved_verification)
    if not _is_hex_digest(
        expected_saved_authorization_verification_sha256,
        64,
    ):
        raise ValueError(
            "expected saved Phase 7 EXIT transaction authorization verification digest is invalid"
        )
    if (
        saved_verification["verification_sha256"]
        != expected_saved_authorization_verification_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT transaction authorization verification digest mismatch"
        )

    now_dt = (
        _canonical_utc(
            now,
            label="Phase 7 EXIT transaction execution readiness now",
        )
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    fresh_verification = signer_module.verify_authorization(
        source_tree=source,
        request_path=authorization_request_path,
        payload_path=authorization_payload_path,
        signature_path=authorization_signature_path,
        allowed_signers_path=authorization_allowed_signers_path,
        expected_allowed_signers_sha256=(
            expected_authorization_allowed_signers_sha256
        ),
        now=_format_utc(now_dt),
    )
    if fresh_verification != saved_verification:
        raise ValueError(
            "fresh Phase 7 EXIT transaction authorization differs from saved verification"
        )

    for verification_field, finalization_field in (
        ("finalization_sha256", "finalization_sha256"),
        ("opened_decision_id", "opened_decision_id"),
        ("exit_decision_id", "exit_decision_id"),
        ("pool_address", "pool_address"),
        ("position_address", "position_address"),
        ("executor_wallet_pubkey", "executor_wallet_pubkey"),
        ("final_transaction_sha256", "final_transaction_sha256"),
        ("recent_blockhash", "recent_blockhash"),
        ("last_valid_block_height", "last_valid_block_height"),
        ("exact_simulation_sha256", "exact_simulation_sha256"),
        ("final_guard_sha256", "final_guard_sha256"),
        (
            "final_wallet_authorization_sha256",
            "final_wallet_authorization_sha256",
        ),
        ("finalizer_binary_sha256", "finalizer_binary_sha256"),
        ("decision_expires_at", "decision_expires_at"),
        (
            "preparation_authorization_expires_at",
            "authorization_expires_at",
        ),
    ):
        if saved_verification.get(verification_field) != finalization.get(
            finalization_field
        ):
            raise ValueError(
                f"Phase 7 EXIT execution readiness {verification_field} binding mismatch"
            )

    if (
        saved_verification.get(
            "human_exact_exit_transaction_authorization_verified"
        )
        is not True
    ):
        raise ValueError(
            "Phase 7 EXIT exact transaction authorization is not verified"
        )
    if saved_verification.get("signature_verified") is not True:
        raise ValueError(
            "Phase 7 EXIT exact transaction signature is not verified"
        )
    if finalization.get("final_transaction_unsigned") is not True:
        raise ValueError(
            "Phase 7 EXIT finalized transaction is no longer unsigned"
        )
    if finalization.get("exact_simulation_succeeded") is not True:
        raise ValueError(
            "Phase 7 EXIT final exact simulation is not successful"
        )

    transaction_expires = _canonical_utc(
        saved_verification["expires_at"],
        label="Phase 7 EXIT transaction authorization expires_at",
    )
    preparation_expires = _canonical_utc(
        finalization["authorization_expires_at"],
        label="Phase 7 EXIT preparation authorization expires_at",
    )
    decision_expires = _canonical_utc(
        finalization["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    if now_dt > transaction_expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization has expired"
        )
    if now_dt > preparation_expires:
        raise ValueError(
            "Phase 7 EXIT preparation authorization has expired"
        )
    if now_dt > decision_expires:
        raise ValueError("Phase 7 EXIT decision has expired")

    if _sha256_text(rpc_url) != finalization["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT execution readiness RPC endpoint mismatch"
        )
    current_block_height = _rpc_block_height(rpc_url)
    last_valid = finalization["last_valid_block_height"]
    if current_block_height > last_valid:
        raise ValueError(
            "Phase 7 EXIT finalized transaction blockhash has expired"
        )
    remaining = last_valid - current_block_height

    for artifact, label in (
        (finalization, "EXIT transaction finalization"),
        (saved_verification, "EXIT transaction authorization verification"),
    ):
        for field in (
            "exit_authorized",
            "controlled_live_authorized",
            "live_submit_authorized",
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
        "saved_finalization_sha256": finalization["finalization_sha256"],
        "expected_finalization_sha256": expected_finalization_sha256,
        "saved_authorization_verification_sha256": saved_verification[
            "verification_sha256"
        ],
        "fresh_authorization_verification_sha256": fresh_verification[
            "verification_sha256"
        ],
        "authorization_verification_matches_saved": True,
        "opened_decision_id": finalization["opened_decision_id"],
        "exit_decision_id": finalization["exit_decision_id"],
        "pool_address": finalization["pool_address"],
        "position_address": finalization["position_address"],
        "executor_wallet_pubkey": finalization["executor_wallet_pubkey"],
        "final_transaction_sha256": finalization[
            "final_transaction_sha256"
        ],
        "recent_blockhash": finalization["recent_blockhash"],
        "last_valid_block_height": last_valid,
        "current_block_height": current_block_height,
        "block_height_remaining": remaining,
        "blockhash_not_expired": True,
        "rpc_commitment": RPC_COMMITMENT,
        "rpc_endpoint_sha256": _sha256_text(rpc_url),
        "decision_expires_at": finalization["decision_expires_at"],
        "preparation_authorization_expires_at": finalization[
            "authorization_expires_at"
        ],
        "transaction_authorization_expires_at": saved_verification[
            "expires_at"
        ],
        "readiness_checked_at": _format_utc(now_dt),
        "finalization_valid": True,
        "exact_transaction_authorization_signature_verified": True,
        "exact_transaction_authorization_not_expired": True,
        "preparation_authorization_not_expired": True,
        "decision_not_expired": True,
        "final_transaction_unsigned": True,
        "final_exact_simulation_succeeded": True,
        "human_exact_exit_transaction_authorization_verified": True,
        "exit_transaction_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_keypair_identity_admission": True,
        "requires_immediate_execution_after_readiness": True,
        "requires_fresh_blockhash_recheck_at_execution": True,
        "requires_single_submission_only": True,
        "requires_confirmation_receipt_reconciliation": True,
        "requires_post_exit_closure_proof": True,
        "requires_post_exit_state_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "exit_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
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
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_transaction_execution_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-verify the exact Phase 7 EXIT transaction authorization, "
            "confirm its finalized blockhash remains valid at confirmed "
            "commitment, and emit a read-only readiness artifact. This stage "
            "does not access the executor keypair, sign, submit, retry, or "
            "mutate production state."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-finalization", required=True)
    parser.add_argument("--expected-finalization-sha256", required=True)
    parser.add_argument(
        "--saved-authorization-verification",
        required=True,
    )
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
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_exit_transaction_execution_readiness(
        source_tree=args.source_tree,
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
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
