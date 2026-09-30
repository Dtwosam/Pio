from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import uuid


FORMAT_VERSION = 1
PAYLOAD_ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_ENTRY_SIGNED_AUTHORIZATION_PAYLOAD_V1"
)
VERIFICATION_ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_ENTRY_SIGNED_AUTHORIZATION_VERIFICATION_V1"
)

REQUEST_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_recursive_rollover_entry_request.py"
)
BASE_SIGNER_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_entry_signed_authorization.py"
)
REVIEWED_SOURCE_BLOBS = {
    REQUEST_TOOL: "6d95e462b20746d1a6d07c0b6816c3f63aa3916a",
    BASE_SIGNER_TOOL: "ffba2650c1ee24e9296149757a0d5cb65382ced3",
}

SIGNATURE_NAMESPACE = "pio-phase8-recursive-rollover-paired-ml-paper-entry-authorization-v1"
AUTHORIZATION_SCOPE = "OPEN_ONE_PHASE8_RECURSIVE_ROLLOVER_PAIRED_ML_PAPER_ENTRY_ONLY"
DECISION = "AUTHORIZE_ONE_PHASE8_RECURSIVE_ROLLOVER_PAIRED_ML_PAPER_ENTRY"
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 900
MAX_CLOCK_SKEW_SECONDS = 60

BINDING_FIELDS = (
    "request_sha256",
    "source_recursive_rollover_account_readiness_sha256",
    "recursive_rollover_entry_input_verification_sha256",
    "source_final_evaluation_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "previous_pair_id",
    "pair_id",
    "pool_address",
    "capital_quote",
    "entry_cost_quote",
    "required_pair_cash_quote",
    "incumbent_position_id",
    "challenger_position_id",
    "incumbent_event_key",
    "challenger_event_key",
    "reason",
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
    "human_authorization_intent",
    "fresh_account_readiness_recheck_required",
    "fresh_model_inference_readiness_required",
    "same_candidate_frame_required",
    "same_decision_snapshot_required",
    "equal_capital_required",
    "atomic_pair_open_required",
    "zero_open_positions_required",
    "previous_pair_closed_required",
    "post_pair_audit_required",
    "paper_pair_entry_executed",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
)

VERIFICATION_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    *BINDING_FIELDS,
    "approval_payload_sha256",
    "approval_signature_sha256",
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
    "approval_not_expired",
    "human_recursive_rollover_paired_paper_entry_authorization_verified",
    "fresh_account_readiness_recheck_required",
    "fresh_model_inference_readiness_required",
    "same_candidate_frame_required",
    "same_decision_snapshot_required",
    "equal_capital_required",
    "atomic_pair_open_required",
    "zero_open_positions_required",
    "previous_pair_closed_required",
    "post_pair_audit_required",
    "paper_pair_entry_executed",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
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


def _git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _git_blob_sha(path: Path) -> str:
    return _git_blob_sha_bytes(path.read_bytes())


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"recursive rollover paired PAPER signer dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"recursive rollover paired PAPER signer dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / REQUEST_TOOL,
            "phase8_recursive_rollover_paired_paper_signed_request",
        ),
        _load_module(
            source / BASE_SIGNER_TOOL,
            "phase8_repeat_paired_paper_base_signer",
        ),
    )


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


def _validate_bindings(value: dict[str, Any], base_signer: Any) -> None:
    for field in (
        "request_sha256",
        "source_recursive_rollover_account_readiness_sha256",
        "recursive_rollover_entry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "pio_database_sha256",
    ):
        if not base_signer._is_hex_digest(value.get(field), 64):
            raise ValueError(
                f"recursive rollover paired PAPER authorization {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        digest = value.get(field)
        if digest is not None and not base_signer._is_hex_digest(digest, 64):
            raise ValueError(
                f"recursive rollover paired PAPER authorization {field} is invalid"
            )
    for field in (
        "production_repository",
        "pio_database_path",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_id",
        "previous_pair_id",
        "pair_id",
        "pool_address",
        "incumbent_position_id",
        "challenger_position_id",
        "incumbent_event_key",
        "challenger_event_key",
        "reason",
    ):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError(
                f"recursive rollover paired PAPER authorization {field} is invalid"
            )
    if value["pair_id"] == value["previous_pair_id"]:
        raise ValueError(
            "recursive rollover paired PAPER authorization pair id was not advanced"
        )
    try:
        capital = Decimal(str(value["capital_quote"]))
        cost = Decimal(str(value["entry_cost_quote"]))
        required = Decimal(str(value["required_pair_cash_quote"]))
    except Exception as exc:
        raise ValueError(
            "recursive rollover paired PAPER authorization economics are invalid"
        ) from exc
    if not capital.is_finite() or capital <= 0:
        raise ValueError(
            "recursive rollover paired PAPER authorization capital_quote is invalid"
        )
    if not cost.is_finite() or cost < 0:
        raise ValueError(
            "recursive rollover paired PAPER authorization entry_cost_quote is invalid"
        )
    if not required.is_finite() or required != 2 * (capital + cost):
        raise ValueError(
            "recursive rollover paired PAPER authorization required cash mismatch"
        )
    if value["incumbent_model_id"] == value["challenger_model_id"]:
        raise ValueError(
            "recursive rollover paired PAPER authorization model identities must differ"
        )
    if value["incumbent_position_id"] == value["challenger_position_id"]:
        raise ValueError(
            "recursive rollover paired PAPER authorization position ids must differ"
        )
    if value["incumbent_event_key"] == value["challenger_event_key"]:
        raise ValueError(
            "recursive rollover paired PAPER authorization event keys must differ"
        )


def validate_payload(
    payload: dict[str, Any],
    *,
    request: dict[str, Any],
    base_signer: Any,
) -> None:
    if set(payload) != set(PAYLOAD_FIELDS) | {"payload_sha256"}:
        raise ValueError(
            "recursive rollover paired PAPER signed payload schema mismatch"
        )
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported recursive rollover paired PAPER signed payload format"
        )
    if payload.get("artifact_type") != PAYLOAD_ARTIFACT_TYPE:
        raise ValueError(
            "unexpected recursive rollover paired PAPER signed payload type"
        )
    if payload.get("signature_namespace") != SIGNATURE_NAMESPACE:
        raise ValueError(
            "recursive rollover paired PAPER signed payload namespace mismatch"
        )

    base_signer._approval_id(payload.get("approval_id"))
    base_signer._principal(payload.get("approver_principal"))
    issued = base_signer._canonical_utc(payload.get("issued_at"))
    expires = base_signer._canonical_utc(payload.get("expires_at"))
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0 or lifetime > MAX_TTL_SECONDS:
        raise ValueError(
            "recursive rollover paired PAPER signed authorization lifetime is invalid"
        )
    if payload.get("decision") != DECISION:
        raise ValueError(
            "recursive rollover paired PAPER signed authorization decision mismatch"
        )
    if payload.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "recursive rollover paired PAPER signed authorization scope mismatch"
        )

    _validate_bindings(payload, base_signer)
    for field in BINDING_FIELDS:
        if payload.get(field) != request.get(field):
            raise ValueError(
                f"recursive rollover paired PAPER signed authorization {field} binding mismatch"
            )

    for field in (
        "human_authorization_intent",
        "fresh_account_readiness_recheck_required",
        "fresh_model_inference_readiness_required",
        "same_candidate_frame_required",
        "same_decision_snapshot_required",
        "equal_capital_required",
        "atomic_pair_open_required",
        "zero_open_positions_required",
        "previous_pair_closed_required",
        "post_pair_audit_required",
    ):
        if payload.get(field) is not True:
            raise ValueError(
                f"recursive rollover paired PAPER signed authorization requires {field}=true"
            )

    for field in (
        "paper_pair_entry_executed",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if payload.get(field) is not False:
            raise ValueError(
                f"recursive rollover paired PAPER signed authorization requires {field}=false"
            )

    identity = {field: payload[field] for field in PAYLOAD_FIELDS}
    if payload["payload_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "recursive rollover paired PAPER signed payload digest mismatch"
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
    request_module, base_signer = _load_reviewed(source)
    request = _load_json(
        request_path,
        label="recursive rollover paired PAPER entry request",
    )
    request_module.validate_phase8_paired_ml_paper_recursive_rollover_entry_request(
        request
    )

    for field in (
        "request_ready",
        "explicit_human_authorization_required",
        "fresh_account_readiness_recheck_required",
        "fresh_model_inference_readiness_required",
        "same_candidate_frame_required",
        "same_decision_snapshot_required",
        "equal_capital_required",
        "atomic_pair_open_required",
        "zero_open_positions_required",
        "previous_pair_closed_required",
        "post_pair_audit_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"recursive rollover paired PAPER request requires {field}=true"
            )
    for field in (
        "paper_pair_entry_authorization_present",
        "paper_pair_entry_authorized",
        "paper_pair_entry_executed",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
    ):
        if request.get(field) is not False:
            raise ValueError(
                f"recursive rollover paired PAPER request requires {field}=false"
            )

    principal = base_signer._principal(approver_principal)
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds <= 0
        or ttl_seconds > MAX_TTL_SECONDS
    ):
        raise ValueError(
            f"recursive rollover paired PAPER ttl_seconds must be 1..{MAX_TTL_SECONDS}"
        )
    issued_dt = (
        base_signer._canonical_utc(issued_at)
        if issued_at is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    expires_dt = issued_dt + timedelta(seconds=ttl_seconds)
    normalized_id = base_signer._approval_id(
        approval_id if approval_id is not None else str(uuid.uuid4())
    )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": PAYLOAD_ARTIFACT_TYPE,
        "signature_namespace": SIGNATURE_NAMESPACE,
        "approval_id": normalized_id,
        "approver_principal": principal,
        "issued_at": base_signer._format_utc(issued_dt),
        "expires_at": base_signer._format_utc(expires_dt),
        "decision": DECISION,
        "authorization_scope": AUTHORIZATION_SCOPE,
        **{field: request[field] for field in BINDING_FIELDS},
        "human_authorization_intent": True,
        "fresh_account_readiness_recheck_required": True,
        "fresh_model_inference_readiness_required": True,
        "same_candidate_frame_required": True,
        "same_decision_snapshot_required": True,
        "equal_capital_required": True,
        "atomic_pair_open_required": True,
        "zero_open_positions_required": True,
        "previous_pair_closed_required": True,
        "post_pair_audit_required": True,
        "paper_pair_entry_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    payload = {
        **identity,
        "payload_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_payload(
        payload,
        request=request,
        base_signer=base_signer,
    )
    return payload


def emit_signing_bytes(
    *,
    source_tree: str | Path,
    request_path: str | Path,
    payload_path: str | Path,
) -> bytes:
    source = Path(source_tree).resolve()
    request_module, base_signer = _load_reviewed(source)
    request = _load_json(
        request_path,
        label="recursive rollover paired PAPER entry request",
    )
    request_module.validate_phase8_paired_ml_paper_recursive_rollover_entry_request(
        request
    )
    payload = _load_json(
        payload_path,
        label="recursive rollover paired PAPER signed payload",
    )
    validate_payload(
        payload,
        request=request,
        base_signer=base_signer,
    )
    return _canonical_bytes(payload)


def _verify_signature(
    *,
    payload: dict[str, Any],
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    base_signer: Any,
) -> tuple[str, str]:
    signature = base_signer._regular_file(
        signature_path,
        label="recursive rollover paired PAPER signature",
    )
    allowed = base_signer._regular_file(
        allowed_signers_path,
        label="recursive rollover paired PAPER allowed_signers trust file",
    )
    completed = subprocess.run(
        [
            str(base_signer._ssh_keygen_path()),
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
            "recursive rollover paired PAPER detached signature verification failed"
        )
    return (
        base_signer._sha256_path(signature),
        base_signer._sha256_path(allowed),
    )


def validate_verification(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "recursive rollover paired PAPER authorization verification must be an object"
        )
    if set(report) != set(VERIFICATION_FIELDS) | {"verification_sha256"}:
        raise ValueError(
            "recursive rollover paired PAPER authorization verification schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported recursive rollover paired PAPER verification format"
        )
    if report.get("artifact_type") != VERIFICATION_ARTIFACT_TYPE:
        raise ValueError(
            "unexpected recursive rollover paired PAPER verification type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "recursive rollover paired PAPER verification lineage mismatch"
        )

    request_digests = (
        "request_sha256",
        "source_recursive_rollover_account_readiness_sha256",
        "recursive_rollover_entry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "pio_database_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "verification_sha256",
    )
    for field in request_digests:
        value = report.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise ValueError(
                f"recursive rollover paired PAPER verification {field} is invalid"
            )

    for field in (
        "request_valid",
        "payload_valid",
        "trust_root_digest_matches",
        "signature_verified",
        "approval_not_expired",
        "human_recursive_rollover_paired_paper_entry_authorization_verified",
        "fresh_account_readiness_recheck_required",
        "fresh_model_inference_readiness_required",
        "same_candidate_frame_required",
        "same_decision_snapshot_required",
        "equal_capital_required",
        "atomic_pair_open_required",
        "zero_open_positions_required",
        "previous_pair_closed_required",
        "post_pair_audit_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"recursive rollover paired PAPER verification requires {field}=true"
            )

    for field in (
        "paper_pair_entry_executed",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"recursive rollover paired PAPER verification requires {field}=false"
            )

    if report.get("decision") != DECISION:
        raise ValueError(
            "recursive rollover paired PAPER verification decision mismatch"
        )
    if report.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "recursive rollover paired PAPER verification scope mismatch"
        )

    for field in (
        "approver_principal",
        "approval_id",
        "issued_at",
        "expires_at",
        "previous_pair_id",
        "pair_id",
        "incumbent_model_id",
        "challenger_model_id",
        "incumbent_position_id",
        "challenger_position_id",
        "incumbent_event_key",
        "challenger_event_key",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"recursive rollover paired PAPER verification {field} is invalid"
            )
    if report["pair_id"] == report["previous_pair_id"]:
        raise ValueError(
            "recursive rollover paired PAPER verification pair id was not advanced"
        )
    if report["incumbent_model_id"] == report["challenger_model_id"]:
        raise ValueError(
            "recursive rollover paired PAPER verification model identities must differ"
        )
    if report["incumbent_position_id"] == report["challenger_position_id"]:
        raise ValueError(
            "recursive rollover paired PAPER verification position ids must differ"
        )
    if report["incumbent_event_key"] == report["challenger_event_key"]:
        raise ValueError(
            "recursive rollover paired PAPER verification event keys must differ"
        )
    try:
        capital = Decimal(str(report["capital_quote"]))
        cost = Decimal(str(report["entry_cost_quote"]))
        required = Decimal(str(report["required_pair_cash_quote"]))
    except Exception as exc:
        raise ValueError(
            "recursive rollover paired PAPER verification economics are invalid"
        ) from exc
    if (
        not capital.is_finite()
        or capital <= 0
        or not cost.is_finite()
        or cost < 0
        or not required.is_finite()
        or required != 2 * (capital + cost)
    ):
        raise ValueError(
            "recursive rollover paired PAPER verification economic binding mismatch"
        )

    identity = {
        field: report[field]
        for field in VERIFICATION_FIELDS
    }
    if report["verification_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "recursive rollover paired PAPER verification digest mismatch"
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
    request_module, base_signer = _load_reviewed(source)
    request = _load_json(
        request_path,
        label="recursive rollover paired PAPER entry request",
    )
    request_module.validate_phase8_paired_ml_paper_recursive_rollover_entry_request(
        request
    )
    payload = _load_json(
        payload_path,
        label="recursive rollover paired PAPER signed payload",
    )
    validate_payload(
        payload,
        request=request,
        base_signer=base_signer,
    )

    if not base_signer._is_hex_digest(
        expected_allowed_signers_sha256,
        64,
    ):
        raise ValueError(
            "recursive rollover paired PAPER expected trust-root SHA-256 is invalid"
        )

    signature_sha, allowed_sha = _verify_signature(
        payload=payload,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        base_signer=base_signer,
    )
    if allowed_sha != expected_allowed_signers_sha256:
        raise ValueError(
            "recursive rollover paired PAPER allowed_signers trust-root digest mismatch"
        )

    now_dt = (
        base_signer._canonical_utc(now)
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    issued = base_signer._canonical_utc(payload["issued_at"])
    expires = base_signer._canonical_utc(payload["expires_at"])
    if now_dt < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError(
            "recursive rollover paired PAPER authorization is not yet valid"
        )
    if now_dt > expires:
        raise ValueError(
            "recursive rollover paired PAPER authorization has expired"
        )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": VERIFICATION_ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        **{field: request[field] for field in BINDING_FIELDS},
        "approval_payload_sha256": payload["payload_sha256"],
        "approval_signature_sha256": signature_sha,
        "allowed_signers_sha256": allowed_sha,
        "approver_principal": payload["approver_principal"],
        "approval_id": payload["approval_id"],
        "issued_at": payload["issued_at"],
        "expires_at": payload["expires_at"],
        "decision": payload["decision"],
        "authorization_scope": payload["authorization_scope"],
        "request_valid": True,
        "payload_valid": True,
        "trust_root_digest_matches": True,
        "signature_verified": True,
        "approval_not_expired": True,
        "human_recursive_rollover_paired_paper_entry_authorization_verified": True,
        "fresh_account_readiness_recheck_required": True,
        "fresh_model_inference_readiness_required": True,
        "same_candidate_frame_required": True,
        "same_decision_snapshot_required": True,
        "equal_capital_required": True,
        "atomic_pair_open_required": True,
        "zero_open_positions_required": True,
        "previous_pair_closed_required": True,
        "post_pair_audit_required": True,
        "paper_pair_entry_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify a short-lived detached human signature for one "
            "repeat equal-capital ML_CHAMPION vs ML_CHALLENGER PAPER pair. "
            "The repeat authorization uses a distinct signature namespace and "
            "does not itself open positions or authorize live execution."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-payload")
    build.add_argument("--source-tree", required=True)
    build.add_argument("--request", required=True)
    build.add_argument("--approver-principal", required=True)
    build.add_argument("--issued-at")
    build.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    build.add_argument("--approval-id")

    emit = sub.add_parser("emit-signing-bytes")
    emit.add_argument("--source-tree", required=True)
    emit.add_argument("--request", required=True)
    emit.add_argument("--payload", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--source-tree", required=True)
    verify.add_argument("--request", required=True)
    verify.add_argument("--payload", required=True)
    verify.add_argument("--signature", required=True)
    verify.add_argument("--allowed-signers", required=True)
    verify.add_argument("--expected-allowed-signers-sha256", required=True)
    verify.add_argument("--now")

    args = parser.parse_args()
    if args.command == "build-payload":
        value = build_payload(
            source_tree=args.source_tree,
            request_path=args.request,
            approver_principal=args.approver_principal,
            issued_at=args.issued_at,
            ttl_seconds=args.ttl_seconds,
            approval_id=args.approval_id,
        )
        print(json.dumps(value, indent=2, sort_keys=True))
    elif args.command == "emit-signing-bytes":
        sys.stdout.buffer.write(
            emit_signing_bytes(
                source_tree=args.source_tree,
                request_path=args.request,
                payload_path=args.payload,
            )
        )
    else:
        value = verify_authorization(
            source_tree=args.source_tree,
            request_path=args.request,
            payload_path=args.payload,
            signature_path=args.signature,
            allowed_signers_path=args.allowed_signers,
            expected_allowed_signers_sha256=(
                args.expected_allowed_signers_sha256
            ),
            now=args.now,
        )
        print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
