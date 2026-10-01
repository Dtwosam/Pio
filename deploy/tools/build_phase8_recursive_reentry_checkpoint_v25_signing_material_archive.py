from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_SIGNING_MATERIAL_ARCHIVE_V1"
)

SIGNER_TOOL = Path(
    "deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_signed_authorization.py"
)
BUNDLE_TOOL = Path(
    "deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle.py"
)
SESSION_ARCHIVE_TOOL = Path(
    "deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_operator_session_archive.py"
)

REVIEWED_SOURCE_BLOBS = {
    SIGNER_TOOL: "f2792861dd2a45260cbe8e72d615ce42bdb2efe3",
    BUNDLE_TOOL: "99be522c3a9c9e71b8d6657b6a23133b1a4bef0f",
    SESSION_ARCHIVE_TOOL: "e5f7f83039e085e3b1fcc85822ea6122655d9621",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_tree",
    "request_file_sha256",
    "payload_file_sha256",
    "signing_bytes_file_sha256",
    "signature_file_sha256",
    "allowed_signers_file_sha256",
    "signed_verification_file_sha256",
    "evidence_bundle_file_sha256",
    "session_archive_file_sha256",
    "request_sha256",
    "payload_sha256",
    "verification_sha256",
    "evidence_bundle_sha256",
    "session_archive_sha256",
    "signature_namespace",
    "approver_principal",
    "approval_id",
    "issued_at",
    "expires_at",
    "authorization_scope",
    "request_file_bound_to_bundle",
    "verification_file_bound_to_bundle",
    "request_file_bound_to_session_archive",
    "verification_file_bound_to_session_archive",
    "payload_digest_bound_to_verification",
    "payload_digest_bound_to_bundle",
    "signature_digest_bound_to_verification",
    "signature_digest_bound_to_bundle",
    "trust_root_digest_bound_to_verification",
    "trust_root_digest_bound_to_bundle",
    "signing_bytes_exact",
    "cryptographic_signature_verified",
    "trust_root_digest_verified",
    "evidence_bundle_bound_to_session_archive",
    "signing_material_archive_ready",
    "archive_read_only",
    "historical_authorization_only",
    "authorization_currently_reusable",
    "authorization_validity_rechecked",
    "current_database_revalidation_performed",
    "next_action_authorized",
    "future_checkpoint_refresh_authorized",
    "paper_supervisor_tick_authorized",
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


def _is_hex_digest(value: Any, length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _safe_source_tree(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    source = candidate.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    return source


def _regular_bytes(
    path: str | Path,
    *,
    label: str,
) -> tuple[Path, bytes, str]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    payload = resolved.read_bytes()
    return resolved, payload, _sha256_bytes(payload)


def _regular_json(
    path: str | Path,
    *,
    label: str,
) -> tuple[dict[str, Any], str]:
    _, payload, digest = _regular_bytes(path, label=label)
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value, digest


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_reviewed(source: Path) -> dict[str, Any]:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"checkpoint v25 signing archive dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"checkpoint v25 signing archive dependency mismatch: {relative}"
            )

    return {
        "signer": _load_module(
            source / SIGNER_TOOL,
            "phase8_v25_signing_archive_signer",
        ),
        "bundle": _load_module(
            source / BUNDLE_TOOL,
            "phase8_v25_signing_archive_bundle",
        ),
        "session": _load_module(
            source / SESSION_ARCHIVE_TOOL,
            "phase8_v25_signing_archive_session",
        ),
    }


def _require_false(value: dict[str, Any], *fields: str) -> None:
    for field in fields:
        if value.get(field) is not False:
            raise ValueError(
                f"checkpoint v25 signing archive requires {field}=false"
            )


def validate_phase8_recursive_reentry_checkpoint_v25_signing_material_archive(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("checkpoint v25 signing archive must be an object")
    if set(report) != set(REPORT_FIELDS) | {"signing_material_archive_sha256"}:
        raise ValueError("checkpoint v25 signing archive schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint v25 signing archive format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected checkpoint v25 signing archive type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("checkpoint v25 signing archive source lineage mismatch")

    for field in (
        "request_file_sha256",
        "payload_file_sha256",
        "signing_bytes_file_sha256",
        "signature_file_sha256",
        "allowed_signers_file_sha256",
        "signed_verification_file_sha256",
        "evidence_bundle_file_sha256",
        "session_archive_file_sha256",
        "request_sha256",
        "payload_sha256",
        "verification_sha256",
        "evidence_bundle_sha256",
        "session_archive_sha256",
        "signing_material_archive_sha256",
    ):
        if not _is_hex_digest(report.get(field)):
            raise ValueError(f"checkpoint v25 signing archive {field} invalid")

    for field in (
        "source_tree",
        "signature_namespace",
        "approver_principal",
        "approval_id",
        "issued_at",
        "expires_at",
        "authorization_scope",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"checkpoint v25 signing archive {field} invalid")

    for field in (
        "request_file_bound_to_bundle",
        "verification_file_bound_to_bundle",
        "request_file_bound_to_session_archive",
        "verification_file_bound_to_session_archive",
        "payload_digest_bound_to_verification",
        "payload_digest_bound_to_bundle",
        "signature_digest_bound_to_verification",
        "signature_digest_bound_to_bundle",
        "trust_root_digest_bound_to_verification",
        "trust_root_digest_bound_to_bundle",
        "signing_bytes_exact",
        "cryptographic_signature_verified",
        "trust_root_digest_verified",
        "evidence_bundle_bound_to_session_archive",
        "signing_material_archive_ready",
        "archive_read_only",
        "historical_authorization_only",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"checkpoint v25 signing archive requires {field}=true"
            )

    for field in (
        "authorization_currently_reusable",
        "authorization_validity_rechecked",
        "current_database_revalidation_performed",
        "next_action_authorized",
        "future_checkpoint_refresh_authorized",
        "paper_supervisor_tick_authorized",
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
                f"checkpoint v25 signing archive requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["signing_material_archive_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("checkpoint v25 signing archive digest mismatch")


def build_phase8_recursive_reentry_checkpoint_v25_signing_material_archive(
    *,
    source_tree: str | Path,
    request_path: str | Path,
    payload_path: str | Path,
    signing_bytes_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    signed_verification_path: str | Path,
    evidence_bundle_path: str | Path,
    session_archive_path: str | Path,
) -> dict[str, Any]:
    source = _safe_source_tree(source_tree)
    modules = _load_reviewed(source)
    signer = modules["signer"]

    request, request_file_sha = _regular_json(
        request_path,
        label="checkpoint v25 continuation request",
    )
    payload, payload_file_sha = _regular_json(
        payload_path,
        label="checkpoint v25 signed payload",
    )
    signing_path, signing_bytes, signing_bytes_sha = _regular_bytes(
        signing_bytes_path,
        label="checkpoint v25 signing bytes",
    )
    signature, signature_bytes, signature_file_sha = _regular_bytes(
        signature_path,
        label="checkpoint v25 detached signature",
    )
    allowed, allowed_bytes, allowed_file_sha = _regular_bytes(
        allowed_signers_path,
        label="checkpoint v25 allowed_signers trust root",
    )
    verification, verification_file_sha = _regular_json(
        signed_verification_path,
        label="checkpoint v25 signed authorization verification",
    )
    bundle, bundle_file_sha = _regular_json(
        evidence_bundle_path,
        label="checkpoint v25 evidence bundle",
    )
    session, session_file_sha = _regular_json(
        session_archive_path,
        label="checkpoint v25 operator session archive",
    )

    request_module = signer._load_request(source)
    request_module.validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_request(
        request
    )
    signer.validate_payload(payload, request=request)
    signer.validate_verification(verification)
    modules[
        "bundle"
    ].validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle(
        bundle
    )
    modules[
        "session"
    ].validate_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
        session
    )

    expected_signing_bytes = signer.emit_signing_bytes(
        source_tree=source,
        request_path=request_path,
        payload_path=payload_path,
    )
    if signing_bytes != expected_signing_bytes:
        raise ValueError("checkpoint v25 signing bytes do not match reviewed payload")

    signature_sha, allowed_sha = signer._verify_signature(
        payload=payload,
        signature_path=signature,
        allowed_signers_path=allowed,
    )
    if signature_sha != signature_file_sha:
        raise ValueError("checkpoint v25 detached signature digest mismatch")
    if allowed_sha != allowed_file_sha:
        raise ValueError("checkpoint v25 trust-root digest mismatch")

    if request_file_sha != bundle["artifact_file_sha256"]["request"]:
        raise ValueError("checkpoint v25 request file not bound to evidence bundle")
    if verification_file_sha != bundle["artifact_file_sha256"][
        "signed_authorization_verification"
    ]:
        raise ValueError(
            "checkpoint v25 verification file not bound to evidence bundle"
        )
    if request_file_sha != session["core_artifact_file_sha256"]["request"]:
        raise ValueError("checkpoint v25 request file not bound to session archive")
    if verification_file_sha != session["core_artifact_file_sha256"][
        "signed_authorization_verification"
    ]:
        raise ValueError(
            "checkpoint v25 verification file not bound to session archive"
        )

    if payload["payload_sha256"] != verification["approval_payload_sha256"]:
        raise ValueError("checkpoint v25 payload/verification digest mismatch")
    if payload["payload_sha256"] != bundle["approval_payload_sha256"]:
        raise ValueError("checkpoint v25 payload/bundle digest mismatch")
    if signature_sha != verification["approval_signature_sha256"]:
        raise ValueError("checkpoint v25 signature/verification digest mismatch")
    if signature_sha != bundle["approval_signature_sha256"]:
        raise ValueError("checkpoint v25 signature/bundle digest mismatch")
    if allowed_sha != verification["allowed_signers_sha256"]:
        raise ValueError("checkpoint v25 trust-root/verification digest mismatch")
    if allowed_sha != bundle["allowed_signers_sha256"]:
        raise ValueError("checkpoint v25 trust-root/bundle digest mismatch")

    for field in (
        "approver_principal",
        "approval_id",
        "issued_at",
        "expires_at",
        "authorization_scope",
    ):
        if payload[field] != verification[field]:
            raise ValueError(
                f"checkpoint v25 payload/verification {field} mismatch"
            )

    if session["evidence_bundle_sha256"] != bundle["bundle_sha256"]:
        raise ValueError("checkpoint v25 bundle/session archive digest mismatch")
    if session["evidence_bundle_file_sha256"] != bundle_file_sha:
        raise ValueError("checkpoint v25 bundle file/session archive mismatch")

    _require_false(
        verification,
        "paper_supervisor_tick_executed",
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
        "source_tree": str(source),
        "request_file_sha256": request_file_sha,
        "payload_file_sha256": payload_file_sha,
        "signing_bytes_file_sha256": signing_bytes_sha,
        "signature_file_sha256": signature_file_sha,
        "allowed_signers_file_sha256": allowed_file_sha,
        "signed_verification_file_sha256": verification_file_sha,
        "evidence_bundle_file_sha256": bundle_file_sha,
        "session_archive_file_sha256": session_file_sha,
        "request_sha256": request["request_sha256"],
        "payload_sha256": payload["payload_sha256"],
        "verification_sha256": verification["verification_sha256"],
        "evidence_bundle_sha256": bundle["bundle_sha256"],
        "session_archive_sha256": session["session_archive_sha256"],
        "signature_namespace": signer.SIGNATURE_NAMESPACE,
        "approver_principal": payload["approver_principal"],
        "approval_id": payload["approval_id"],
        "issued_at": payload["issued_at"],
        "expires_at": payload["expires_at"],
        "authorization_scope": payload["authorization_scope"],
        "request_file_bound_to_bundle": True,
        "verification_file_bound_to_bundle": True,
        "request_file_bound_to_session_archive": True,
        "verification_file_bound_to_session_archive": True,
        "payload_digest_bound_to_verification": True,
        "payload_digest_bound_to_bundle": True,
        "signature_digest_bound_to_verification": True,
        "signature_digest_bound_to_bundle": True,
        "trust_root_digest_bound_to_verification": True,
        "trust_root_digest_bound_to_bundle": True,
        "signing_bytes_exact": True,
        "cryptographic_signature_verified": True,
        "trust_root_digest_verified": True,
        "evidence_bundle_bound_to_session_archive": True,
        "signing_material_archive_ready": True,
        "archive_read_only": True,
        "historical_authorization_only": True,
        "authorization_currently_reusable": False,
        "authorization_validity_rechecked": False,
        "current_database_revalidation_performed": False,
        "next_action_authorized": False,
        "future_checkpoint_refresh_authorized": False,
        "paper_supervisor_tick_authorized": False,
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
        "signing_material_archive_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_recursive_reentry_checkpoint_v25_signing_material_archive(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Archive and independently verify the raw signing materials for an "
            "already-completed checkpoint v25 operator session. The tool checks "
            "the request, payload, exact signing bytes, detached SSH signature, "
            "allowed_signers trust root, signed-verification report, evidence "
            "bundle and session archive. Historical verification does not make "
            "the authorization reusable and authorizes no next action."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--signing-bytes", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--signed-verification", required=True)
    parser.add_argument("--evidence-bundle", required=True)
    parser.add_argument("--session-archive", required=True)
    args = parser.parse_args()

    report = (
        build_phase8_recursive_reentry_checkpoint_v25_signing_material_archive(
            source_tree=args.source_tree,
            request_path=args.request,
            payload_path=args.payload,
            signing_bytes_path=args.signing_bytes,
            signature_path=args.signature,
            allowed_signers_path=args.allowed_signers,
            signed_verification_path=args.signed_verification,
            evidence_bundle_path=args.evidence_bundle,
            session_archive_path=args.session_archive,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
