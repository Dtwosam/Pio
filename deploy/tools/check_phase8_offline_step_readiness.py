from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE8_OFFLINE_STEP_READINESS_V1"

HANDOFF_TOOL = Path(
    "deploy/tools/build_phase8_post_phase7_operator_handoff.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_phase8_offline_step_request.py"
)
SIGNER_TOOL = Path(
    "deploy/tools/build_phase8_offline_step_signed_authorization.py"
)

REVIEWED_SOURCE_BLOBS = {
    HANDOFF_TOOL: "60d7bee1b76739b8540a8a345c77dc30eea28a78",
    REQUEST_TOOL: "7f5e7bcb0f90fc7eeb701d48d2a596472a15f286",
    SIGNER_TOOL: "cfeeecf88ee4894d9ec3f371cccd5ef91950c302",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_phase8_handoff_sha256",
    "fresh_phase8_handoff_sha256",
    "request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "debt_type",
    "scope",
    "approver_principal",
    "approval_id",
    "fresh_handoff_matches_saved",
    "fresh_authorization_matches_saved",
    "human_offline_step_authorization_verified",
    "approval_not_expired",
    "current_action_still_automatic",
    "current_debt_type_matches_request",
    "current_scope_matches_request",
    "database_binding_current",
    "offline_step_readiness_ready",
    "requires_separate_offline_step_executor",
    "offline_step_executed",
    "paper_challenger_transition_authorized",
    "paper_trading_authorized",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
    "phase8_policy_action_authorized",
    "phase8_execution_authorized",
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
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_reviewed(source: Path) -> tuple[Any, Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 8 offline-step readiness dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 8 offline-step readiness dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / HANDOFF_TOOL,
            "phase8_offline_step_readiness_handoff",
        ),
        _load_module(
            source / REQUEST_TOOL,
            "phase8_offline_step_readiness_request",
        ),
        _load_module(
            source / SIGNER_TOOL,
            "phase8_offline_step_readiness_signer",
        ),
    )


def validate_phase8_offline_step_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 8 offline-step readiness must be an object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("Phase 8 offline-step readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 8 offline-step readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 8 offline-step readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 8 offline-step readiness lineage mismatch")

    for field in (
        "saved_phase8_handoff_sha256",
        "fresh_phase8_handoff_sha256",
        "request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "pio_database_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 8 offline-step readiness {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "debt_type",
        "scope",
        "approver_principal",
        "approval_id",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 8 offline-step readiness {field} is invalid"
            )

    for field in (
        "fresh_handoff_matches_saved",
        "fresh_authorization_matches_saved",
        "human_offline_step_authorization_verified",
        "approval_not_expired",
        "current_action_still_automatic",
        "current_debt_type_matches_request",
        "current_scope_matches_request",
        "database_binding_current",
        "offline_step_readiness_ready",
        "requires_separate_offline_step_executor",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 8 offline-step readiness requires {field}=true"
            )

    for field in (
        "offline_step_executed",
        "paper_challenger_transition_authorized",
        "paper_trading_authorized",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase8_policy_action_authorized",
        "phase8_execution_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 8 offline-step readiness requires {field}=false"
            )

    if (
        report["saved_phase8_handoff_sha256"]
        != report["fresh_phase8_handoff_sha256"]
    ):
        raise ValueError("Phase 8 offline-step handoff digest mismatch")
    if (
        report["saved_signed_authorization_verification_sha256"]
        != report["fresh_signed_authorization_verification_sha256"]
    ):
        raise ValueError("Phase 8 offline-step authorization digest mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["readiness_sha256"] != expected:
        raise ValueError("Phase 8 offline-step readiness digest mismatch")


def build_phase8_offline_step_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_phase8_handoff_path: str | Path,
    phase7_post_promotion_audit_path: str | Path,
    request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    production = Path(repository).resolve()
    source = Path(source_tree).resolve()
    if not production.is_dir():
        raise ValueError("production repository root is invalid")
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    handoff_module, request_module, signer_module = _load_reviewed(source)
    saved_handoff = _load_json(
        saved_phase8_handoff_path,
        label="saved Phase 8 operator handoff",
    )
    request = _load_json(
        request_path,
        label="Phase 8 offline-step request",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved Phase 8 signed authorization verification",
    )
    handoff_module.validate_phase8_post_phase7_handoff(saved_handoff)
    request_module.validate_phase8_offline_step_request(request)
    signer_module.validate_verification(saved_verification)

    if request["saved_phase8_handoff_sha256"] != saved_handoff["handoff_sha256"]:
        raise ValueError("Phase 8 offline-step request/handoff binding mismatch")
    if saved_verification["request_sha256"] != request["request_sha256"]:
        raise ValueError(
            "Phase 8 offline-step signed verification/request binding mismatch"
        )
    if saved_verification.get(
        "human_offline_step_authorization_verified"
    ) is not True:
        raise ValueError("Phase 8 offline-step human authorization is not verified")

    fresh_handoff = handoff_module.build_phase8_post_phase7_handoff(
        repository=production,
        source_tree=source,
        phase7_post_promotion_audit_path=phase7_post_promotion_audit_path,
        expected_phase7_post_promotion_audit_sha256=saved_handoff[
            "saved_phase7_post_promotion_audit_sha256"
        ],
    )
    handoff_module.validate_phase8_post_phase7_handoff(fresh_handoff)
    if fresh_handoff != saved_handoff:
        raise ValueError("fresh Phase 8 handoff differs from saved handoff")

    fresh_verification = signer_module.verify_authorization(
        source_tree=source,
        request_path=request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        now=now,
    )
    signer_module.validate_verification(fresh_verification)
    if fresh_verification != saved_verification:
        raise ValueError(
            "fresh Phase 8 signed authorization differs from saved verification"
        )

    operator = fresh_handoff.get("phase8_operator_handoff")
    if not isinstance(operator, dict):
        raise ValueError("fresh Phase 8 operator handoff is missing")
    if (
        operator.get("status") != "AUTOMATIC_ACTION"
        or operator.get("automatic_action_available") is not True
        or operator.get("operator_action_required") is not False
        or operator.get("manual_input_required") is not False
    ):
        raise ValueError("fresh Phase 8 action is no longer automatic")
    if operator.get("debt_type") != request["debt_type"]:
        raise ValueError("fresh Phase 8 debt type differs from request")
    if operator.get("scope") != request["scope"]:
        raise ValueError("fresh Phase 8 scope differs from request")
    if fresh_handoff["pio_database_sha256_after"] != request[
        "pio_database_sha256"
    ]:
        raise ValueError("fresh Phase 8 database differs from request")
    if Path(request["production_repository"]).resolve() != production:
        raise ValueError("Phase 8 request repository binding mismatch")
    if Path(request["pio_database_path"]).resolve() != (
        production / "data" / "pio.db"
    ).resolve():
        raise ValueError("Phase 8 request database path binding mismatch")

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
        "saved_phase8_handoff_sha256": saved_handoff["handoff_sha256"],
        "fresh_phase8_handoff_sha256": fresh_handoff["handoff_sha256"],
        "request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": saved_verification[
            "verification_sha256"
        ],
        "fresh_signed_authorization_verification_sha256": fresh_verification[
            "verification_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": request["pio_database_path"],
        "pio_database_sha256": request["pio_database_sha256"],
        "debt_type": request["debt_type"],
        "scope": request["scope"],
        "approver_principal": fresh_verification["approver_principal"],
        "approval_id": fresh_verification["approval_id"],
        "fresh_handoff_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_offline_step_authorization_verified": True,
        "approval_not_expired": True,
        "current_action_still_automatic": True,
        "current_debt_type_matches_request": True,
        "current_scope_matches_request": True,
        "database_binding_current": True,
        "offline_step_readiness_ready": True,
        "requires_separate_offline_step_executor": True,
        "offline_step_executed": False,
        "paper_challenger_transition_authorized": False,
        "paper_trading_authorized": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_policy_action_authorized": False,
        "phase8_execution_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_offline_step_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshly revalidate the exact Phase 8 handoff, one-step request, "
            "and short-lived human authorization before a separate offline "
            "research-step executor. No PAPER or live authority is granted."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-phase8-handoff", required=True)
    parser.add_argument("--phase7-post-promotion-audit", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_phase8_offline_step_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_phase8_handoff_path=args.saved_phase8_handoff,
        phase7_post_promotion_audit_path=args.phase7_post_promotion_audit,
        request_path=args.request,
        saved_signed_authorization_verification_path=(
            args.saved_signed_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=(
            args.expected_allowed_signers_sha256
        ),
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
