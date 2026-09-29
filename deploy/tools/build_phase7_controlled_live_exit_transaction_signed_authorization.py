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
    "PHASE7_CONTROLLED_LIVE_EXIT_TRANSACTION_SIGNED_AUTHORIZATION_PAYLOAD_V1"
)
VERIFICATION_ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_TRANSACTION_SIGNED_AUTHORIZATION_VERIFICATION_V1"
)

REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_transaction_authorization_request.py"
)
REVIEWED_SOURCE_BLOBS = {
    REQUEST_TOOL: "a0b790d3f67a66a6e4a53b8dc7e7fceb4828ac47",
}

SIGNATURE_NAMESPACE = (
    "pio-phase7-controlled-live-exit-transaction-authorization-v1"
)
AUTHORIZATION_SCOPE = (
    "AUTHORIZE_SIGN_AND_SUBMIT_EXACT_PHASE7_EXIT_TRANSACTION_ONLY"
)
DECISION = (
    "AUTHORIZE_SIGN_AND_SUBMIT_EXACT_PHASE7_EXIT_TRANSACTION"
)

DEFAULT_TTL_SECONDS = 120
MAX_TTL_SECONDS = 300
MAX_CLOCK_SKEW_SECONDS = 30
PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:+/-]{0,127}$")

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
    "request_sha256",
    "finalization_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "final_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "exact_simulation_sha256",
    "final_guard_sha256",
    "final_wallet_authorization_sha256",
    "finalizer_binary_sha256",
    "decision_expires_at",
    "preparation_authorization_expires_at",
    "human_exact_exit_transaction_authorization_intent",
    "authorization_must_not_outlive_preparation_authorization",
    "authorization_must_not_outlive_decision",
    "fresh_blockhash_expiry_recheck_required",
    "fresh_presubmit_recheck_required",
    "final_exit_execution_gate_required",
    "single_submission_only_required",
    "post_exit_confirmation_required",
    "post_exit_closure_proof_required",
    "post_exit_state_reconciliation_required",
    "exact_transaction_authorization_present",
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
)

VERIFICATION_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "request_sha256",
    "finalization_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "final_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "exact_simulation_sha256",
    "final_guard_sha256",
    "final_wallet_authorization_sha256",
    "finalizer_binary_sha256",
    "decision_expires_at",
    "preparation_authorization_expires_at",
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
    "authorization_within_preparation_authorization_expiry",
    "authorization_within_decision_expiry",
    "human_exact_exit_transaction_authorization_verified",
    "fresh_blockhash_expiry_recheck_required",
    "fresh_presubmit_recheck_required",
    "final_exit_execution_gate_required",
    "single_submission_only_required",
    "post_exit_confirmation_required",
    "post_exit_closure_proof_required",
    "post_exit_state_reconciliation_required",
    "exact_transaction_authorization_present",
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


def _load_request_module(source: Path) -> Any:
    path = source / REQUEST_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 7 EXIT transaction authorization request tool is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[REQUEST_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT transaction authorization request blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_transaction_signed_authorization_request",
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
            "Phase 7 EXIT transaction authorization principal is invalid"
        )
    return raw


def _approval_id(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError(
            "Phase 7 EXIT transaction authorization approval_id is invalid"
        )
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            "Phase 7 EXIT transaction authorization approval_id is invalid"
        ) from exc
    if str(parsed) != raw:
        raise ValueError(
            "Phase 7 EXIT transaction authorization approval_id is not canonical"
        )
    return raw


def _validate_request_for_authorization(request: dict[str, Any]) -> None:
    if (
        request.get("exact_exit_transaction_authorization_request_ready")
        is not True
    ):
        raise ValueError(
            "Phase 7 EXIT transaction authorization request is not ready"
        )
    if (
        request.get(
            "explicit_human_exact_transaction_authorization_required"
        )
        is not True
    ):
        raise ValueError(
            "Phase 7 EXIT transaction request lost human-authorization boundary"
        )
    if request.get("exact_transaction_authorization_present") is not False:
        raise ValueError(
            "Phase 7 EXIT transaction request already contains authorization"
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
        if request.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT transaction request unexpectedly authorizes {field}"
            )


def validate_payload(
    payload: dict[str, Any],
    *,
    request: dict[str, Any],
) -> None:
    if not isinstance(payload, dict):
        raise ValueError(
            "Phase 7 EXIT transaction authorization payload must be an object"
        )
    if set(payload) != set(PAYLOAD_FIELDS) | {"payload_sha256"}:
        raise ValueError(
            "Phase 7 EXIT transaction authorization payload schema mismatch"
        )
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT transaction authorization payload format"
        )
    if payload.get("artifact_type") != PAYLOAD_ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT transaction authorization payload type"
        )
    if payload.get("signature_namespace") != SIGNATURE_NAMESPACE:
        raise ValueError(
            "Phase 7 EXIT transaction authorization namespace mismatch"
        )
    if payload.get("decision") != DECISION:
        raise ValueError(
            "Phase 7 EXIT transaction authorization decision mismatch"
        )
    if payload.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "Phase 7 EXIT transaction authorization scope mismatch"
        )

    _approval_id(payload.get("approval_id"))
    _principal(payload.get("approver_principal"))
    issued = _canonical_utc(
        payload.get("issued_at"),
        label="Phase 7 EXIT transaction authorization issued_at",
    )
    expires = _canonical_utc(
        payload.get("expires_at"),
        label="Phase 7 EXIT transaction authorization expires_at",
    )
    preparation_expires = _canonical_utc(
        payload.get("preparation_authorization_expires_at"),
        label="Phase 7 EXIT preparation authorization expires_at",
    )
    decision_expires = _canonical_utc(
        payload.get("decision_expires_at"),
        label="Phase 7 EXIT decision expires_at",
    )
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0 or lifetime > MAX_TTL_SECONDS:
        raise ValueError(
            "Phase 7 EXIT transaction authorization lifetime is invalid"
        )
    if expires > preparation_expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization outlives preparation authorization"
        )
    if expires > decision_expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization outlives signed EXIT decision"
        )

    for field in (
        "request_sha256",
        "finalization_sha256",
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "final_transaction_sha256",
        "recent_blockhash",
        "last_valid_block_height",
        "exact_simulation_sha256",
        "final_guard_sha256",
        "final_wallet_authorization_sha256",
        "finalizer_binary_sha256",
        "decision_expires_at",
        "preparation_authorization_expires_at",
    ):
        if payload.get(field) != request.get(field):
            raise ValueError(
                f"Phase 7 EXIT transaction authorization {field} binding mismatch"
            )

    for field in (
        "human_exact_exit_transaction_authorization_intent",
        "authorization_must_not_outlive_preparation_authorization",
        "authorization_must_not_outlive_decision",
        "fresh_blockhash_expiry_recheck_required",
        "fresh_presubmit_recheck_required",
        "final_exit_execution_gate_required",
        "single_submission_only_required",
        "post_exit_confirmation_required",
        "post_exit_closure_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if payload.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT transaction authorization requires {field}=true"
            )

    for field in (
        "exact_transaction_authorization_present",
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
    ):
        if payload.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT transaction authorization requires {field}=false"
            )

    identity = {field: payload[field] for field in PAYLOAD_FIELDS}
    if payload["payload_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError(
            "Phase 7 EXIT transaction authorization payload digest mismatch"
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
        label="Phase 7 EXIT transaction authorization request",
    )
    request_module.validate_exit_transaction_authorization_request(request)
    _validate_request_for_authorization(request)

    principal = _principal(approver_principal)
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or ttl_seconds <= 0
        or ttl_seconds > MAX_TTL_SECONDS
    ):
        raise ValueError(
            "Phase 7 EXIT transaction authorization ttl_seconds must be "
            f"1..{MAX_TTL_SECONDS}"
        )

    issued_dt = (
        _canonical_utc(
            issued_at,
            label="Phase 7 EXIT transaction authorization issued_at",
        )
        if issued_at is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    request_created = _canonical_utc(
        request["request_created_at"],
        label="Phase 7 EXIT transaction authorization request created_at",
    )
    preparation_expires = _canonical_utc(
        request["preparation_authorization_expires_at"],
        label="Phase 7 EXIT preparation authorization expires_at",
    )
    decision_expires = _canonical_utc(
        request["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    if issued_dt < request_created - timedelta(
        seconds=MAX_CLOCK_SKEW_SECONDS
    ):
        raise ValueError(
            "Phase 7 EXIT transaction authorization cannot predate its request"
        )
    if issued_dt > preparation_expires:
        raise ValueError(
            "Phase 7 EXIT preparation authorization has expired"
        )
    if issued_dt > decision_expires:
        raise ValueError("Phase 7 EXIT decision has expired")

    expires_dt = issued_dt + timedelta(seconds=ttl_seconds)
    if expires_dt > preparation_expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization ttl outlives preparation authorization"
        )
    if expires_dt > decision_expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization ttl outlives signed EXIT decision"
        )

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
        "request_sha256": request["request_sha256"],
        "finalization_sha256": request["finalization_sha256"],
        "opened_decision_id": request["opened_decision_id"],
        "exit_decision_id": request["exit_decision_id"],
        "pool_address": request["pool_address"],
        "position_address": request["position_address"],
        "executor_wallet_pubkey": request["executor_wallet_pubkey"],
        "final_transaction_sha256": request["final_transaction_sha256"],
        "recent_blockhash": request["recent_blockhash"],
        "last_valid_block_height": request["last_valid_block_height"],
        "exact_simulation_sha256": request["exact_simulation_sha256"],
        "final_guard_sha256": request["final_guard_sha256"],
        "final_wallet_authorization_sha256": request[
            "final_wallet_authorization_sha256"
        ],
        "finalizer_binary_sha256": request["finalizer_binary_sha256"],
        "decision_expires_at": request["decision_expires_at"],
        "preparation_authorization_expires_at": request[
            "preparation_authorization_expires_at"
        ],
        "human_exact_exit_transaction_authorization_intent": True,
        "authorization_must_not_outlive_preparation_authorization": True,
        "authorization_must_not_outlive_decision": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "fresh_presubmit_recheck_required": True,
        "final_exit_execution_gate_required": True,
        "single_submission_only_required": True,
        "post_exit_confirmation_required": True,
        "post_exit_closure_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exact_transaction_authorization_present": False,
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
        label="Phase 7 EXIT transaction authorization request",
    )
    request_module.validate_exit_transaction_authorization_request(request)
    payload = _load_json(
        payload_path,
        label="Phase 7 EXIT transaction signed authorization payload",
    )
    validate_payload(payload, request=request)
    return _canonical_bytes(payload)


def _ssh_keygen_path() -> Path:
    raw = shutil.which("ssh-keygen")
    if raw is None:
        raise ValueError("ssh-keygen is not available")
    path = Path(raw).resolve(strict=True)
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not (st.st_mode & stat.S_IXUSR):
        raise ValueError("ssh-keygen executable is invalid")
    return path


def _verify_signature(
    *,
    payload: dict[str, Any],
    signature_path: str | Path,
    allowed_signers_path: str | Path,
) -> tuple[str, str]:
    signature = _regular_file(
        signature_path,
        label="Phase 7 EXIT transaction authorization signature",
    )
    allowed = _regular_file(
        allowed_signers_path,
        label="Phase 7 EXIT transaction authorization allowed_signers trust file",
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
            "Phase 7 EXIT transaction authorization signature verification failed"
        )
    return _sha256_path(signature), _sha256_path(allowed)


def validate_verification(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT transaction authorization verification must be an object"
        )
    if set(report) != set(VERIFICATION_FIELDS) | {"verification_sha256"}:
        raise ValueError(
            "Phase 7 EXIT transaction authorization verification schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT transaction authorization verification format"
        )
    if report.get("artifact_type") != VERIFICATION_ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT transaction authorization verification type"
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
            "Phase 7 EXIT transaction authorization verification lineage mismatch"
        )

    for field in (
        "request_sha256",
        "finalization_sha256",
        "final_transaction_sha256",
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
                f"Phase 7 EXIT transaction authorization verification {field} is invalid"
            )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "recent_blockhash",
        "decision_expires_at",
        "preparation_authorization_expires_at",
        "approver_principal",
        "approval_id",
        "issued_at",
        "expires_at",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT transaction authorization verification {field} is invalid"
            )

    _principal(report["approver_principal"])
    _approval_id(report["approval_id"])
    issued = _canonical_utc(
        report["issued_at"],
        label="Phase 7 EXIT transaction authorization issued_at",
    )
    expires = _canonical_utc(
        report["expires_at"],
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
    if expires <= issued:
        raise ValueError(
            "Phase 7 EXIT transaction authorization verification lifetime invalid"
        )
    if expires > preparation_expires or expires > decision_expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization verification expiry invalid"
        )

    if (
        not isinstance(report.get("last_valid_block_height"), int)
        or isinstance(report["last_valid_block_height"], bool)
        or report["last_valid_block_height"] <= 0
    ):
        raise ValueError(
            "Phase 7 EXIT transaction authorization block-height ceiling invalid"
        )
    if report.get("decision") != DECISION:
        raise ValueError(
            "Phase 7 EXIT transaction authorization verification decision mismatch"
        )
    if report.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "Phase 7 EXIT transaction authorization verification scope mismatch"
        )

    for field in (
        "request_valid",
        "payload_valid",
        "trust_root_digest_matches",
        "signature_verified",
        "authorization_not_expired",
        "authorization_within_preparation_authorization_expiry",
        "authorization_within_decision_expiry",
        "human_exact_exit_transaction_authorization_verified",
        "fresh_blockhash_expiry_recheck_required",
        "fresh_presubmit_recheck_required",
        "final_exit_execution_gate_required",
        "single_submission_only_required",
        "post_exit_confirmation_required",
        "post_exit_closure_proof_required",
        "post_exit_state_reconciliation_required",
        "exact_transaction_authorization_present",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT transaction authorization verification requires {field}=true"
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
                f"Phase 7 EXIT transaction authorization verification requires {field}=false"
            )

    identity = {field: report[field] for field in VERIFICATION_FIELDS}
    if report["verification_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 7 EXIT transaction authorization verification digest mismatch"
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
    if not _is_hex_digest(expected_allowed_signers_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT transaction allowed_signers SHA-256 is invalid"
        )

    request_module = _load_request_module(source)
    request = _load_json(
        request_path,
        label="Phase 7 EXIT transaction authorization request",
    )
    request_module.validate_exit_transaction_authorization_request(request)
    _validate_request_for_authorization(request)
    payload = _load_json(
        payload_path,
        label="Phase 7 EXIT transaction signed authorization payload",
    )
    validate_payload(payload, request=request)

    issued = _canonical_utc(
        payload["issued_at"],
        label="Phase 7 EXIT transaction authorization issued_at",
    )
    expires = _canonical_utc(
        payload["expires_at"],
        label="Phase 7 EXIT transaction authorization expires_at",
    )
    preparation_expires = _canonical_utc(
        payload["preparation_authorization_expires_at"],
        label="Phase 7 EXIT preparation authorization expires_at",
    )
    decision_expires = _canonical_utc(
        payload["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    now_dt = (
        _canonical_utc(
            now,
            label="Phase 7 EXIT transaction authorization now",
        )
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    if now_dt < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError(
            "Phase 7 EXIT transaction authorization is not yet valid"
        )
    if now_dt > expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization has expired"
        )
    if now_dt > preparation_expires:
        raise ValueError(
            "Phase 7 EXIT preparation authorization has expired"
        )
    if now_dt > decision_expires:
        raise ValueError("Phase 7 EXIT decision has expired")

    signature_sha, allowed_sha = _verify_signature(
        payload=payload,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
    )
    if allowed_sha != expected_allowed_signers_sha256:
        raise ValueError(
            "Phase 7 EXIT transaction authorization trust-root digest mismatch"
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
        "request_sha256": request["request_sha256"],
        "finalization_sha256": request["finalization_sha256"],
        "opened_decision_id": request["opened_decision_id"],
        "exit_decision_id": request["exit_decision_id"],
        "pool_address": request["pool_address"],
        "position_address": request["position_address"],
        "executor_wallet_pubkey": request["executor_wallet_pubkey"],
        "final_transaction_sha256": request["final_transaction_sha256"],
        "recent_blockhash": request["recent_blockhash"],
        "last_valid_block_height": request["last_valid_block_height"],
        "exact_simulation_sha256": request["exact_simulation_sha256"],
        "final_guard_sha256": request["final_guard_sha256"],
        "final_wallet_authorization_sha256": request[
            "final_wallet_authorization_sha256"
        ],
        "finalizer_binary_sha256": request["finalizer_binary_sha256"],
        "decision_expires_at": request["decision_expires_at"],
        "preparation_authorization_expires_at": request[
            "preparation_authorization_expires_at"
        ],
        "authorization_payload_sha256": payload["payload_sha256"],
        "authorization_signature_sha256": signature_sha,
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
        "authorization_within_preparation_authorization_expiry": True,
        "authorization_within_decision_expiry": True,
        "human_exact_exit_transaction_authorization_verified": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "fresh_presubmit_recheck_required": True,
        "final_exit_execution_gate_required": True,
        "single_submission_only_required": True,
        "post_exit_confirmation_required": True,
        "post_exit_closure_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exact_transaction_authorization_present": True,
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
        "verification_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_verification(report)
    return report


def _build_main(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            build_payload(
                source_tree=args.source_tree,
                request_path=args.request,
                approver_principal=args.approver_principal,
                issued_at=args.issued_at,
                ttl_seconds=args.ttl_seconds,
                approval_id=args.approval_id,
            ),
            indent=2,
            sort_keys=True,
        )
    )


def _emit_main(args: argparse.Namespace) -> None:
    sys.stdout.buffer.write(
        emit_signing_bytes(
            source_tree=args.source_tree,
            request_path=args.request,
            payload_path=args.payload,
        )
    )


def _verify_main(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            verify_authorization(
                source_tree=args.source_tree,
                request_path=args.request,
                payload_path=args.payload,
                signature_path=args.signature,
                allowed_signers_path=args.allowed_signers,
                expected_allowed_signers_sha256=(
                    args.expected_allowed_signers_sha256
                ),
                now=args.now,
            ),
            indent=2,
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build and verify a short-lived detached-SSH authorization for "
            "signing and single-shot submission of one exact finalized Phase 7 "
            "EXIT transaction. Verification authenticates human intent only; "
            "signing/submission still require a fresh execution gate."
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
