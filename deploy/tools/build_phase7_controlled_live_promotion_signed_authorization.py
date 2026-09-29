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
    "PHASE7_CONTROLLED_LIVE_PROMOTION_SIGNED_AUTHORIZATION_PAYLOAD_V1"
)
VERIFICATION_ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_PROMOTION_SIGNED_AUTHORIZATION_VERIFICATION_V1"
)

REQUEST_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_promotion_request.py"
)
REVIEWED_SOURCE_BLOBS = {
    REQUEST_TOOL: "7cc053123d767aa5f6c5b6a94bdb90777c859d47",
}

SIGNATURE_NAMESPACE = "pio-phase7-controlled-live-promotion-authorization-v1"
AUTHORIZATION_SCOPE = "PERSIST_EXACT_PHASE7_PROMOTION_EVIDENCE_ONLY"
DECISION = "AUTHORIZE_EXACT_PHASE7_PROMOTION_EVIDENCE_PERSISTENCE"

DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 900
MAX_CLOCK_SKEW_SECONDS = 60
PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:+/-]{0,127}$")

BINDING_FIELDS = (
    "request_sha256",
    "completion_handoff_sha256",
    "phase7_evidence_status_sha256",
    "phase7_evidence_plan_sha256",
    "pio_database_sha256",
    "confirmed_receipts",
    "failed_receipts",
    "closed_positions",
    "open_positions",
    "distinct_closed_pools",
    "valued_closed_positions",
    "labeled_closed_positions",
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
    "fresh_phase7_promotion_recheck_required",
    "phase7_promotion_persisted",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
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
    "human_phase7_promotion_authorization_verified",
    "fresh_phase7_promotion_recheck_required",
    "phase7_promotion_persisted",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
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
        raise ValueError("reviewed Phase 7 promotion request tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[REQUEST_TOOL]:
        raise ValueError("reviewed Phase 7 promotion request blob mismatch")
    return _load_module(path, "phase7_promotion_signed_request")


def _canonical_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError("Phase 7 authorization timestamp must use UTC Z form")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            "Phase 7 authorization timestamp must use YYYY-MM-DDTHH:MM:SSZ"
        ) from exc


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _principal(raw: Any) -> str:
    if not isinstance(raw, str) or PRINCIPAL_RE.fullmatch(raw) is None:
        raise ValueError("Phase 7 approver principal is invalid")
    return raw


def _approval_id(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError("Phase 7 approval_id is invalid")
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Phase 7 approval_id is invalid") from exc
    normalized = str(parsed)
    if raw != normalized:
        raise ValueError("Phase 7 approval_id is not canonical")
    return normalized


def validate_payload(payload: dict[str, Any], *, request: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Phase 7 signed authorization payload must be an object")
    if set(payload) != set(PAYLOAD_FIELDS) | {"payload_sha256"}:
        raise ValueError("Phase 7 signed authorization payload schema mismatch")
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 signed authorization format")
    if payload.get("artifact_type") != PAYLOAD_ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 signed authorization payload type"
        )
    if payload.get("signature_namespace") != SIGNATURE_NAMESPACE:
        raise ValueError("Phase 7 signed authorization namespace mismatch")

    _approval_id(payload.get("approval_id"))
    _principal(payload.get("approver_principal"))
    issued = _canonical_utc(payload.get("issued_at"))
    expires = _canonical_utc(payload.get("expires_at"))
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0 or lifetime > MAX_TTL_SECONDS:
        raise ValueError("Phase 7 signed authorization lifetime is invalid")

    if payload.get("decision") != DECISION:
        raise ValueError("Phase 7 signed authorization decision mismatch")
    if payload.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("Phase 7 signed authorization scope mismatch")

    for field in BINDING_FIELDS:
        if payload.get(field) != request.get(field):
            raise ValueError(
                f"Phase 7 signed authorization {field} binding mismatch"
            )

    for field in (
        "human_authorization_intent",
        "fresh_phase7_promotion_recheck_required",
    ):
        if payload.get(field) is not True:
            raise ValueError(
                f"Phase 7 signed authorization requires {field}=true"
            )

    for field in (
        "phase7_promotion_persisted",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
    ):
        if payload.get(field) is not False:
            raise ValueError(
                f"Phase 7 signed authorization requires {field}=false"
            )

    identity = {field: payload[field] for field in PAYLOAD_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if payload.get("payload_sha256") != expected:
        raise ValueError(
            "Phase 7 signed authorization payload digest mismatch"
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
    request_module = _load_request_module(source)
    request = _load_json(request_path, label="Phase 7 promotion request")
    request_module.validate_phase7_promotion_request(request)

    if request.get("promotion_request_ready") is not True:
        raise ValueError("Phase 7 promotion request is not ready")
    if request.get("phase7_promotion_authorization_present") is not False:
        raise ValueError(
            "Phase 7 promotion request already contains authorization"
        )
    if request.get("phase7_promotion_persisted") is not False:
        raise ValueError("Phase 7 promotion is already persisted")

    principal = _principal(approver_principal)
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or ttl_seconds <= 0
        or ttl_seconds > MAX_TTL_SECONDS
    ):
        raise ValueError(
            f"Phase 7 authorization ttl_seconds must be 1..{MAX_TTL_SECONDS}"
        )

    issued_dt = (
        _canonical_utc(issued_at)
        if issued_at is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
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
        "human_authorization_intent": True,
        "fresh_phase7_promotion_recheck_required": True,
        "phase7_promotion_persisted": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
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
    request = _load_json(request_path, label="Phase 7 promotion request")
    request_module.validate_phase7_promotion_request(request)
    payload = _load_json(
        payload_path,
        label="Phase 7 signed authorization payload",
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
        label="Phase 7 approval signature",
    )
    allowed = _regular_file(
        allowed_signers_path,
        label="Phase 7 allowed_signers trust file",
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
            "Phase 7 detached signature verification failed"
        )
    return _sha256_path(signature), _sha256_path(allowed)


def validate_verification(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 authorization verification must be an object"
        )
    if set(report) != set(VERIFICATION_FIELDS) | {"verification_sha256"}:
        raise ValueError(
            "Phase 7 authorization verification schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 authorization verification format"
        )
    if report.get("artifact_type") != VERIFICATION_ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 authorization verification type"
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
            "Phase 7 authorization verification lineage mismatch"
        )

    for field in (
        "request_sha256",
        "completion_handoff_sha256",
        "phase7_evidence_status_sha256",
        "phase7_evidence_plan_sha256",
        "pio_database_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "verification_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 authorization verification {field} is invalid"
            )

    _approval_id(report.get("approval_id"))
    _principal(report.get("approver_principal"))
    issued = _canonical_utc(report.get("issued_at"))
    expires = _canonical_utc(report.get("expires_at"))
    if expires <= issued:
        raise ValueError(
            "Phase 7 authorization verification lifetime is invalid"
        )

    if report.get("decision") != DECISION:
        raise ValueError(
            "Phase 7 authorization verification decision mismatch"
        )
    if report.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "Phase 7 authorization verification scope mismatch"
        )

    for field in (
        "request_valid",
        "payload_valid",
        "trust_root_digest_matches",
        "signature_verified",
        "approval_not_expired",
        "human_phase7_promotion_authorization_verified",
        "fresh_phase7_promotion_recheck_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 authorization verification requires {field}=true"
            )

    for field in (
        "phase7_promotion_persisted",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 authorization verification requires {field}=false"
            )

    identity = {field: report[field] for field in VERIFICATION_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["verification_sha256"] != expected:
        raise ValueError(
            "Phase 7 authorization verification digest mismatch"
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
    request = _load_json(request_path, label="Phase 7 promotion request")
    request_module.validate_phase7_promotion_request(request)
    payload = _load_json(
        payload_path,
        label="Phase 7 signed authorization payload",
    )
    validate_payload(payload, request=request)

    if not _is_hex_digest(expected_allowed_signers_sha256, 64):
        raise ValueError(
            "Phase 7 expected allowed_signers SHA-256 is invalid"
        )

    signature_sha, allowed_sha = _verify_signature(
        payload=payload,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
    )
    if allowed_sha != expected_allowed_signers_sha256:
        raise ValueError(
            "Phase 7 allowed_signers trust-root digest mismatch"
        )

    now_dt = (
        _canonical_utc(now)
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    issued = _canonical_utc(payload["issued_at"])
    expires = _canonical_utc(payload["expires_at"])
    if now_dt < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError("Phase 7 authorization is not yet valid")
    if now_dt > expires:
        raise ValueError("Phase 7 authorization has expired")

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
        "human_phase7_promotion_authorization_verified": True,
        "fresh_phase7_promotion_recheck_required": True,
        "phase7_promotion_persisted": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    report = {
        **identity,
        "verification_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_verification(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build, emit, or verify a short-lived detached human signature "
            "that authorizes only persistence of one exact Phase 7 promotion "
            "evidence request."
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
