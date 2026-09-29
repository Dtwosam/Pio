from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any
import uuid


FORMAT_VERSION = 1
PAYLOAD_ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_SIGNED_AUTHORIZATION_PAYLOAD_V1"
)
VERIFICATION_ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_SIGNED_AUTHORIZATION_VERIFICATION_V1"
)

REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_settlement_authorization_request.py"
)
REVIEWED_SOURCE_BLOBS = {
    REQUEST_TOOL: "9e5ecc58a2505f31515673ab54d8dc49d42843e8",
}

SIGNATURE_NAMESPACE = (
    "pio-phase7-controlled-live-exit-settlement-authorization-v1"
)
AUTHORIZATION_SCOPE = (
    "AUTHORIZE_SIGN_AND_SUBMIT_EXACT_PHASE7_EXIT_SETTLEMENT_TRANSACTION_ONLY"
)
DECISION = (
    "AUTHORIZE_SIGN_AND_SUBMIT_EXACT_PHASE7_EXIT_SETTLEMENT_TRANSACTION"
)
DEFAULT_TTL_SECONDS = 120
MAX_TTL_SECONDS = 300
MAX_CLOCK_SKEW_SECONDS = 30
PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:+/-]{0,127}$")

BINDING_FIELDS = (
    "request_sha256",
    "finalization_sha256",
    "preparation_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "exit_signature",
    "zero_liquidity_snapshot_sha256",
    "zero_liquidity_capture_slot_start",
    "zero_liquidity_capture_slot_end",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "destination_config_sha256",
    "user_token_x",
    "user_token_y",
    "reward_token_destinations",
    "final_settlement_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "exact_simulation_sha256",
    "final_guard_sha256",
    "final_wallet_authorization_sha256",
    "finalizer_binary_sha256",
    "request_created_at",
)

PAYLOAD_FIELDS = (
    "format_version",
    "artifact_type",
    "signature_namespace",
    "approval_id",
    "approver_principal",
    "issued_at",
    "expires_at",
    "decision",
    "authorization_scope",
    *BINDING_FIELDS,
    "human_exact_settlement_transaction_authorization_intent",
    "fresh_blockhash_expiry_recheck_required",
    "final_settlement_execution_gate_required",
    "single_submission_only_required",
    "post_settlement_confirmation_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "exact_transaction_authorization_present",
    "settlement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_entry_authorized",
    "new_live_capital_authorized",
    "phase7_promotion_authorized",
    "phase7_promotion_persisted",
)

VERIFICATION_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    *BINDING_FIELDS,
    "authorization_payload_sha256",
    "authorization_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "issued_at",
    "expires_at",
    "decision",
    "authorization_scope",
    "request_valid",
    "payload_valid",
    "trust_root_digest_matches",
    "signature_verified",
    "authorization_not_expired",
    "human_exact_settlement_transaction_authorization_verified",
    "fresh_blockhash_expiry_recheck_required",
    "final_settlement_execution_gate_required",
    "single_submission_only_required",
    "post_settlement_confirmation_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "exact_transaction_authorization_present",
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
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
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
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _ssh_keygen_path() -> Path:
    raw = shutil.which("ssh-keygen")
    if raw is None:
        raise ValueError("ssh-keygen is not available")
    path = Path(raw).resolve(strict=True)
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not (st.st_mode & stat.S_IXUSR):
        raise ValueError("ssh-keygen executable is invalid")
    return path


def _load_request_module(source: Path) -> Any:
    path = source / REQUEST_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 7 EXIT settlement authorization request tool is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[REQUEST_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT settlement authorization request blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_settlement_signed_authorization_request",
    )


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


def _principal(raw: Any) -> str:
    if not isinstance(raw, str) or PRINCIPAL_RE.fullmatch(raw) is None:
        raise ValueError(
            "Phase 7 EXIT settlement authorization principal is invalid"
        )
    return raw


def _approval_id(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError(
            "Phase 7 EXIT settlement authorization approval_id is invalid"
        )
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            "Phase 7 EXIT settlement authorization approval_id is invalid"
        ) from exc
    if str(parsed) != raw:
        raise ValueError(
            "Phase 7 EXIT settlement authorization approval_id is not canonical"
        )
    return raw


def _validate_request_for_authorization(request: dict[str, Any]) -> None:
    for field in (
        "settlement_finalization_verified",
        "transaction_unsigned",
        "exact_simulation_verified",
        "zero_liquidity_lineage_verified",
        "exact_settlement_transaction_authorization_request_ready",
        "explicit_human_exact_transaction_authorization_required",
        "fresh_blockhash_expiry_recheck_required",
        "single_submission_only_required",
        "post_settlement_confirmation_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement request requires {field}=true"
            )
    for field in (
        "exact_transaction_authorization_present",
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
        if request.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement request unexpectedly authorizes {field}"
            )


def validate_payload(
    payload: dict[str, Any],
    *,
    request: dict[str, Any],
) -> None:
    if not isinstance(payload, dict):
        raise ValueError(
            "Phase 7 EXIT settlement authorization payload must be an object"
        )
    if set(payload) != set(PAYLOAD_FIELDS) | {"payload_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement authorization payload schema mismatch"
        )
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement authorization payload format"
        )
    if payload.get("artifact_type") != PAYLOAD_ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement authorization payload type"
        )
    if payload.get("signature_namespace") != SIGNATURE_NAMESPACE:
        raise ValueError(
            "Phase 7 EXIT settlement authorization namespace mismatch"
        )
    if payload.get("decision") != DECISION:
        raise ValueError(
            "Phase 7 EXIT settlement authorization decision mismatch"
        )
    if payload.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "Phase 7 EXIT settlement authorization scope mismatch"
        )

    _approval_id(payload.get("approval_id"))
    _principal(payload.get("approver_principal"))
    issued = _canonical_utc(
        payload.get("issued_at"),
        label="Phase 7 EXIT settlement authorization issued_at",
    )
    expires = _canonical_utc(
        payload.get("expires_at"),
        label="Phase 7 EXIT settlement authorization expires_at",
    )
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0 or lifetime > MAX_TTL_SECONDS:
        raise ValueError(
            "Phase 7 EXIT settlement authorization lifetime is invalid"
        )

    for field in BINDING_FIELDS:
        if payload.get(field) != request.get(field):
            raise ValueError(
                f"Phase 7 EXIT settlement authorization {field} binding mismatch"
            )

    for field in (
        "human_exact_settlement_transaction_authorization_intent",
        "fresh_blockhash_expiry_recheck_required",
        "final_settlement_execution_gate_required",
        "single_submission_only_required",
        "post_settlement_confirmation_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if payload.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement authorization requires {field}=true"
            )

    for field in (
        "exact_transaction_authorization_present",
        "settlement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
    ):
        if payload.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement authorization requires {field}=false"
            )

    identity = {field: payload[field] for field in PAYLOAD_FIELDS}
    if payload["payload_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError(
            "Phase 7 EXIT settlement authorization payload digest mismatch"
        )


def build_payload(
    *,
    source_tree: str | Path,
    request_path: str | Path,
    approver_principal: str,
    issued_at: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    approval_id: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    request_module = _load_request_module(source)
    request = _load_json(
        request_path,
        label="Phase 7 EXIT settlement authorization request",
    )
    request_module.validate_exit_settlement_authorization_request(request)
    _validate_request_for_authorization(request)

    principal = _principal(approver_principal)
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or ttl_seconds <= 0
        or ttl_seconds > MAX_TTL_SECONDS
    ):
        raise ValueError(
            "Phase 7 EXIT settlement authorization ttl_seconds must be "
            f"1..{MAX_TTL_SECONDS}"
        )

    issued_dt = (
        _canonical_utc(
            issued_at,
            label="Phase 7 EXIT settlement authorization issued_at",
        )
        if issued_at is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    request_created = _canonical_utc(
        request["request_created_at"],
        label="Phase 7 EXIT settlement authorization request created_at",
    )
    if issued_dt < request_created - timedelta(
        seconds=MAX_CLOCK_SKEW_SECONDS
    ):
        raise ValueError(
            "Phase 7 EXIT settlement authorization cannot predate its request"
        )
    expires_dt = issued_dt + timedelta(seconds=ttl_seconds)

    normalized_id = _approval_id(
        approval_id if approval_id is not None else str(uuid.uuid4())
    )
    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": PAYLOAD_ARTIFACT_TYPE,
        "signature_namespace": SIGNATURE_NAMESPACE,
        "approval_id": normalized_id,
        "approver_principal": principal,
        "issued_at": _format_utc(issued_dt),
        "expires_at": _format_utc(expires_dt),
        "decision": DECISION,
        "authorization_scope": AUTHORIZATION_SCOPE,
        **{field: request[field] for field in BINDING_FIELDS},
        "human_exact_settlement_transaction_authorization_intent": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "final_settlement_execution_gate_required": True,
        "single_submission_only_required": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exact_transaction_authorization_present": False,
        "settlement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
    }
    payload = {
        **identity,
        "payload_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_payload(payload, request=request)
    return payload


def emit_signing_bytes(
    *,
    source_tree: str | Path,
    request_path: str | Path,
    payload_path: str | Path,
) -> bytes:
    source = Path(source_tree).resolve()
    request_module = _load_request_module(source)
    request = _load_json(
        request_path,
        label="Phase 7 EXIT settlement authorization request",
    )
    request_module.validate_exit_settlement_authorization_request(request)
    _validate_request_for_authorization(request)
    payload = _load_json(
        payload_path,
        label="Phase 7 EXIT settlement authorization payload",
    )
    validate_payload(payload, request=request)
    return _canonical_bytes(payload)


def validate_verification(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT settlement authorization verification must be an object"
        )
    if set(report) != set(VERIFICATION_FIELDS) | {"verification_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement authorization verification schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement authorization verification format"
        )
    if report.get("artifact_type") != VERIFICATION_ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement authorization verification type"
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
            "Phase 7 EXIT settlement authorization verification lineage mismatch"
        )

    for field in (
        "request_sha256",
        "finalization_sha256",
        "preparation_sha256",
        "zero_liquidity_snapshot_sha256",
        "rpc_endpoint_sha256",
        "destination_config_sha256",
        "final_settlement_transaction_sha256",
        "exact_simulation_sha256",
        "final_guard_sha256",
        "final_wallet_authorization_sha256",
        "finalizer_binary_sha256",
        "authorization_payload_sha256",
        "authorization_signature_sha256",
        "allowed_signers_sha256",
        "verification_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement authorization verification {field} is invalid"
            )

    for field in (
        "request_valid",
        "payload_valid",
        "trust_root_digest_matches",
        "signature_verified",
        "authorization_not_expired",
        "human_exact_settlement_transaction_authorization_verified",
        "fresh_blockhash_expiry_recheck_required",
        "final_settlement_execution_gate_required",
        "single_submission_only_required",
        "post_settlement_confirmation_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
        "exact_transaction_authorization_present",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement authorization verification requires {field}=true"
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
                f"Phase 7 EXIT settlement authorization verification requires {field}=false"
            )

    issued = _canonical_utc(
        report.get("issued_at"),
        label="Phase 7 EXIT settlement authorization verification issued_at",
    )
    expires = _canonical_utc(
        report.get("expires_at"),
        label="Phase 7 EXIT settlement authorization verification expires_at",
    )
    if (expires - issued).total_seconds() <= 0:
        raise ValueError(
            "Phase 7 EXIT settlement authorization verification lifetime invalid"
        )

    identity = {field: report[field] for field in VERIFICATION_FIELDS}
    if report["verification_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 7 EXIT settlement authorization verification digest mismatch"
        )


def verify_authorization(
    *,
    source_tree: str | Path,
    request_path: str | Path,
    payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    request_module = _load_request_module(source)
    request = _load_json(
        request_path,
        label="Phase 7 EXIT settlement authorization request",
    )
    request_module.validate_exit_settlement_authorization_request(request)
    _validate_request_for_authorization(request)
    payload = _load_json(
        payload_path,
        label="Phase 7 EXIT settlement authorization payload",
    )
    validate_payload(payload, request=request)

    signature = _regular_file(
        signature_path,
        label="Phase 7 EXIT settlement authorization signature",
    )
    allowed = _regular_file(
        allowed_signers_path,
        label="Phase 7 EXIT settlement authorization allowed_signers",
    )
    if not _is_hex_digest(expected_allowed_signers_sha256, 64):
        raise ValueError(
            "expected settlement authorization trust-root digest is invalid"
        )
    allowed_sha = _sha256_path(allowed)
    if allowed_sha != expected_allowed_signers_sha256:
        raise ValueError(
            "Phase 7 EXIT settlement authorization trust-root digest mismatch"
        )

    issued = _canonical_utc(
        payload["issued_at"],
        label="Phase 7 EXIT settlement authorization issued_at",
    )
    expires = _canonical_utc(
        payload["expires_at"],
        label="Phase 7 EXIT settlement authorization expires_at",
    )
    now_dt = (
        _canonical_utc(
            now,
            label="Phase 7 EXIT settlement authorization verification now",
        )
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    if now_dt < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError(
            "Phase 7 EXIT settlement authorization is not yet valid"
        )
    if now_dt > expires:
        raise ValueError(
            "Phase 7 EXIT settlement authorization has expired"
        )

    completed = subprocess.run(
        [
            str(_ssh_keygen_path()),
            "-Y",
            "verify",
            "-f",
            str(allowed),
            "-I",
            payload["approver_principal"],
            "-n",
            SIGNATURE_NAMESPACE,
            "-s",
            str(signature),
        ],
        input=_canonical_bytes(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(
            "Phase 7 EXIT settlement authorization signature verification failed"
        )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": VERIFICATION_ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        **{field: request[field] for field in BINDING_FIELDS},
        "authorization_payload_sha256": payload["payload_sha256"],
        "authorization_signature_sha256": _sha256_path(signature),
        "allowed_signers_sha256": allowed_sha,
        "approver_principal": payload["approver_principal"],
        "approval_id": payload["approval_id"],
        "issued_at": payload["issued_at"],
        "expires_at": payload["expires_at"],
        "decision": DECISION,
        "authorization_scope": AUTHORIZATION_SCOPE,
        "request_valid": True,
        "payload_valid": True,
        "trust_root_digest_matches": True,
        "signature_verified": True,
        "authorization_not_expired": True,
        "human_exact_settlement_transaction_authorization_verified": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "final_settlement_execution_gate_required": True,
        "single_submission_only_required": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exact_transaction_authorization_present": True,
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
        "verification_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_verification(report)
    return report


def _build_main(args: argparse.Namespace) -> None:
    print(json.dumps(build_payload(
        source_tree=args.source_tree,
        request_path=args.request,
        approver_principal=args.approver_principal,
        issued_at=args.issued_at,
        ttl_seconds=args.ttl_seconds,
        approval_id=args.approval_id,
    ), indent=2, sort_keys=True))


def _emit_main(args: argparse.Namespace) -> None:
    sys.stdout.buffer.write(emit_signing_bytes(
        source_tree=args.source_tree,
        request_path=args.request,
        payload_path=args.payload,
    ))


def _verify_main(args: argparse.Namespace) -> None:
    print(json.dumps(verify_authorization(
        source_tree=args.source_tree,
        request_path=args.request,
        payload_path=args.payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
        now=args.now,
    ), indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build and verify a short-lived detached-SSH human authorization "
            "for signing and single-shot submission of one exact finalized "
            "Phase 7 EXIT settlement transaction. Verification grants no "
            "signing or submission by itself."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-payload")
    build.add_argument("--source-tree", required=True)
    build.add_argument("--request", required=True)
    build.add_argument("--approver-principal", required=True)
    build.add_argument("--issued-at")
    build.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    build.add_argument("--approval-id")
    build.set_defaults(handler=_build_main)

    emit = subparsers.add_parser("emit-signing-bytes")
    emit.add_argument("--source-tree", required=True)
    emit.add_argument("--request", required=True)
    emit.add_argument("--payload", required=True)
    emit.set_defaults(handler=_emit_main)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--source-tree", required=True)
    verify.add_argument("--request", required=True)
    verify.add_argument("--payload", required=True)
    verify.add_argument("--signature", required=True)
    verify.add_argument("--allowed-signers", required=True)
    verify.add_argument("--expected-allowed-signers-sha256", required=True)
    verify.add_argument("--now")
    verify.set_defaults(handler=_verify_main)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
