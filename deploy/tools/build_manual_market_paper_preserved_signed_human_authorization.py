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
    "MANUAL_MARKET_PAPER_PRESERVED_SIGNED_HUMAN_AUTHORIZATION_PAYLOAD_V1"
)
VERIFICATION_ARTIFACT_TYPE = (
    "MANUAL_MARKET_PAPER_PRESERVED_SIGNED_HUMAN_AUTHORIZATION_VERIFICATION_V1"
)

REQUEST_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_human_authorization_request.py"
)
REVIEWED_SOURCE_BLOBS = {
    REQUEST_TOOL: "f7d31b7bbd421945fd8f7ca1353832e6e89da11b",
}

SIGNATURE_NAMESPACE = "pio-manual-market-paper-preserved-authorization-v1"
AUTHORIZATION_SCOPE = "PRESERVED_FILE_MUTATIONS_ONLY"
DECISION = "AUTHORIZE_EXACT_PRESERVED_FILE_MUTATIONS"

DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 900
MAX_CLOCK_SKEW_SECONDS = 60

AUTHORIZATION_FIELDS = (
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
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
    "request_sha256",
    "authorization_review_sha256",
    "production_repository",
    "operation_count",
    "operations_sha256",
    "excluded_scopes",
    "human_authorization_intent",
    "fresh_execution_recheck_required",
    "execution_ready",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

VERIFICATION_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "request_sha256",
    "authorization_review_sha256",
    "approval_payload_sha256",
    "approval_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "issued_at",
    "expires_at",
    "decision",
    "authorization_scope",
    "signature_namespace",
    "request_valid",
    "payload_valid",
    "trust_root_digest_matches",
    "signature_verified",
    "approval_not_expired",
    "human_authorization_verified",
    "fresh_execution_recheck_required",
    "execution_ready",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:+/-]{0,127}$")


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


def _load_reviewed_request_module(source: Path) -> Any:
    path = source / REQUEST_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed human-authorization request tool is missing")
    expected = REVIEWED_SOURCE_BLOBS[REQUEST_TOOL]
    if _git_blob_sha(path) != expected:
        raise ValueError(
            "reviewed human-authorization request tool blob mismatch"
        )
    return _load_module(
        path,
        "manual_market_paper_preserved_signed_human_auth_request",
    )


def _canonical_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError("authorization timestamp must use canonical UTC Z form")
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            "authorization timestamp must use YYYY-MM-DDTHH:MM:SSZ"
        ) from exc
    return parsed


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _principal(raw: Any) -> str:
    if not isinstance(raw, str) or PRINCIPAL_RE.fullmatch(raw) is None:
        raise ValueError("authorization approver principal is invalid")
    return raw


def _approval_id(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError("authorization approval_id is invalid")
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise ValueError("authorization approval_id is invalid") from exc
    normalized = str(parsed)
    if raw != normalized:
        raise ValueError("authorization approval_id is not canonical")
    return normalized


def _operations_sha256(request: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_bytes(request["operations"]))


def validate_authorization_payload(
    payload: dict[str, Any],
    *,
    request: dict[str, Any],
) -> None:
    if not isinstance(payload, dict):
        raise ValueError("signed human authorization payload must be a JSON object")
    if set(payload) != set(PAYLOAD_FIELDS) | {"payload_sha256"}:
        raise ValueError("signed human authorization payload schema mismatch")
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported signed human authorization payload format")
    if payload.get("artifact_type") != PAYLOAD_ARTIFACT_TYPE:
        raise ValueError("unexpected signed human authorization payload type")
    if payload.get("signature_namespace") != SIGNATURE_NAMESPACE:
        raise ValueError("signed human authorization namespace mismatch")

    _approval_id(payload.get("approval_id"))
    _principal(payload.get("approver_principal"))

    issued = _canonical_utc(payload.get("issued_at"))
    expires = _canonical_utc(payload.get("expires_at"))
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0:
        raise ValueError("signed human authorization expiry must follow issuance")
    if lifetime > MAX_TTL_SECONDS:
        raise ValueError("signed human authorization lifetime exceeds maximum")

    if payload.get("decision") != DECISION:
        raise ValueError("signed human authorization decision mismatch")
    if payload.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("signed human authorization scope mismatch")
    if payload.get("request_sha256") != request.get("request_sha256"):
        raise ValueError("signed human authorization request binding mismatch")
    if payload.get("authorization_review_sha256") != request.get(
        "authorization_review_sha256"
    ):
        raise ValueError("signed human authorization review binding mismatch")
    if payload.get("production_repository") != request.get(
        "production_repository"
    ):
        raise ValueError("signed human authorization repository binding mismatch")
    if payload.get("operation_count") != request.get("operation_count"):
        raise ValueError("signed human authorization operation count mismatch")
    if payload.get("operations_sha256") != _operations_sha256(request):
        raise ValueError("signed human authorization operation digest mismatch")
    if payload.get("excluded_scopes") != request.get("excluded_scopes"):
        raise ValueError("signed human authorization excluded scopes mismatch")

    if payload.get("human_authorization_intent") is not True:
        raise ValueError("signed human authorization intent must be true")
    if payload.get("fresh_execution_recheck_required") is not True:
        raise ValueError(
            "signed human authorization requires fresh execution recheck"
        )
    if payload.get("execution_ready") is not False:
        raise ValueError("signed human authorization payload is not execution-ready")
    for field in AUTHORIZATION_FIELDS:
        if payload.get(field) is not False:
            raise ValueError(
                f"signed human authorization payload requires {field}=false"
            )

    identity = {field: payload[field] for field in PAYLOAD_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if payload.get("payload_sha256") != expected:
        raise ValueError("signed human authorization payload digest mismatch")


def build_authorization_payload(
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
    request_module = _load_reviewed_request_module(source)
    request = _load_json(request_path, label="human-authorization request")
    request_module.validate_authorization_request(request)

    principal = _principal(approver_principal)
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or ttl_seconds <= 0
        or ttl_seconds > MAX_TTL_SECONDS
    ):
        raise ValueError(
            f"authorization ttl_seconds must be 1..{MAX_TTL_SECONDS}"
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
        "request_sha256": request["request_sha256"],
        "authorization_review_sha256": request[
            "authorization_review_sha256"
        ],
        "production_repository": request["production_repository"],
        "operation_count": request["operation_count"],
        "operations_sha256": _operations_sha256(request),
        "excluded_scopes": request["excluded_scopes"],
        "human_authorization_intent": True,
        "fresh_execution_recheck_required": True,
        "execution_ready": False,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    payload = {
        **identity,
        "payload_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_authorization_payload(payload, request=request)
    return payload


def authorization_signing_bytes(
    *,
    source_tree: str | Path,
    request_path: str | Path,
    payload_path: str | Path,
) -> bytes:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    request_module = _load_reviewed_request_module(source)
    request = _load_json(request_path, label="human-authorization request")
    request_module.validate_authorization_request(request)
    payload = _load_json(
        payload_path,
        label="signed human-authorization payload",
    )
    validate_authorization_payload(payload, request=request)
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


def _verify_detached_signature(
    *,
    payload: dict[str, Any],
    signature_path: str | Path,
    allowed_signers_path: str | Path,
) -> tuple[str, str]:
    signature = _regular_file(signature_path, label="approval signature")
    allowed_signers = _regular_file(
        allowed_signers_path,
        label="allowed_signers trust file",
    )
    ssh_keygen = _ssh_keygen_path()

    completed = subprocess.run(
        [
            str(ssh_keygen),
            "-Y",
            "verify",
            "-f",
            str(allowed_signers),
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
        raise ValueError("detached human-authorization signature verification failed")

    return _sha256_path(signature), _sha256_path(allowed_signers)


def _validate_verification(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("human authorization verification must be a JSON object")
    if set(report) != set(VERIFICATION_FIELDS) | {"verification_sha256"}:
        raise ValueError("human authorization verification schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported human authorization verification format")
    if report.get("artifact_type") != VERIFICATION_ARTIFACT_TYPE:
        raise ValueError("unexpected human authorization verification type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("human authorization verification source mismatch")

    for field in (
        "request_sha256",
        "authorization_review_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "verification_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"human authorization verification {field} invalid")

    _principal(report.get("approver_principal"))
    _approval_id(report.get("approval_id"))
    _canonical_utc(report.get("issued_at"))
    _canonical_utc(report.get("expires_at"))

    if report.get("decision") != DECISION:
        raise ValueError("human authorization verification decision mismatch")
    if report.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("human authorization verification scope mismatch")
    if report.get("signature_namespace") != SIGNATURE_NAMESPACE:
        raise ValueError("human authorization verification namespace mismatch")

    for field in (
        "request_valid",
        "payload_valid",
        "trust_root_digest_matches",
        "signature_verified",
        "approval_not_expired",
        "human_authorization_verified",
        "fresh_execution_recheck_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"human authorization verification requires {field}=true"
            )

    if report.get("execution_ready") is not False:
        raise ValueError("human authorization verification must not be execution-ready")
    if report.get("production_file_modified") is not False:
        raise ValueError("human authorization verification must not modify production")
    if report.get("production_repository_git_mutated") is not False:
        raise ValueError(
            "human authorization verification must not mutate production Git"
        )
    for field in AUTHORIZATION_FIELDS:
        if report.get(field) is not False:
            raise ValueError(
                f"human authorization verification requires {field}=false"
            )

    identity = {field: report[field] for field in VERIFICATION_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report.get("verification_sha256") != expected:
        raise ValueError("human authorization verification digest mismatch")


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
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not _is_hex_digest(expected_allowed_signers_sha256, 64):
        raise ValueError("expected allowed_signers SHA-256 is invalid")

    request_module = _load_reviewed_request_module(source)
    request = _load_json(request_path, label="human-authorization request")
    request_module.validate_authorization_request(request)

    payload = _load_json(payload_path, label="signed human-authorization payload")
    validate_authorization_payload(payload, request=request)

    issued = _canonical_utc(payload["issued_at"])
    expires = _canonical_utc(payload["expires_at"])
    now_dt = (
        _canonical_utc(now)
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    if now_dt < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError("human authorization is not yet valid")
    if now_dt > expires:
        raise ValueError("human authorization has expired")

    signature_sha256, allowed_signers_sha256 = _verify_detached_signature(
        payload=payload,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
    )
    if allowed_signers_sha256 != expected_allowed_signers_sha256:
        raise ValueError("allowed_signers trust-root digest mismatch")

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
        "authorization_review_sha256": request[
            "authorization_review_sha256"
        ],
        "approval_payload_sha256": payload["payload_sha256"],
        "approval_signature_sha256": signature_sha256,
        "allowed_signers_sha256": allowed_signers_sha256,
        "approver_principal": payload["approver_principal"],
        "approval_id": payload["approval_id"],
        "issued_at": payload["issued_at"],
        "expires_at": payload["expires_at"],
        "decision": DECISION,
        "authorization_scope": AUTHORIZATION_SCOPE,
        "signature_namespace": SIGNATURE_NAMESPACE,
        "request_valid": True,
        "payload_valid": True,
        "trust_root_digest_matches": True,
        "signature_verified": True,
        "approval_not_expired": True,
        "human_authorization_verified": True,
        "fresh_execution_recheck_required": True,
        "execution_ready": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    report = {
        **identity,
        "verification_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    _validate_verification(report)
    return report


def _build_payload_main(args: argparse.Namespace) -> None:
    payload = build_authorization_payload(
        source_tree=args.source_tree,
        request_path=args.request,
        approver_principal=args.approver_principal,
        issued_at=args.issued_at,
        ttl_seconds=args.ttl_seconds,
        approval_id=args.approval_id,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


def _emit_signing_bytes_main(args: argparse.Namespace) -> None:
    sys.stdout.buffer.write(
        authorization_signing_bytes(
            source_tree=args.source_tree,
            request_path=args.request,
            payload_path=args.payload,
        )
    )


def _verify_main(args: argparse.Namespace) -> None:
    report = verify_authorization(
        source_tree=args.source_tree,
        request_path=args.request,
        payload_path=args.payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build and verify a short-lived detached-SSH-signed human "
            "authorization for the exact preserved human-authorization request. "
            "Verification authenticates the named signer against an externally "
            "provisioned allowed_signers trust root, but never accesses production "
            "or makes the request execution-ready."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "build-payload",
        help="Build the canonical short-lived payload for detached signing.",
    )
    build.add_argument("--source-tree", required=True)
    build.add_argument("--request", required=True)
    build.add_argument("--approver-principal", required=True)
    build.add_argument("--issued-at")
    build.add_argument(
        "--ttl-seconds",
        type=int,
        default=DEFAULT_TTL_SECONDS,
    )
    build.add_argument("--approval-id")
    build.set_defaults(handler=_build_payload_main)

    signing_bytes = subparsers.add_parser(
        "emit-signing-bytes",
        help=(
            "Validate the readable payload and emit the exact canonical bytes "
            "that must be detached-signed. No trailing newline is added."
        ),
    )
    signing_bytes.add_argument("--source-tree", required=True)
    signing_bytes.add_argument("--request", required=True)
    signing_bytes.add_argument("--payload", required=True)
    signing_bytes.set_defaults(handler=_emit_signing_bytes_main)

    verify = subparsers.add_parser(
        "verify",
        help="Verify a detached SSH signature against the external trust root.",
    )
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
