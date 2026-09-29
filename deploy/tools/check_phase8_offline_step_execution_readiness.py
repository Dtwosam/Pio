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
ARTIFACT_TYPE = "PHASE8_OFFLINE_STEP_EXECUTION_READINESS_V1"

HANDOFF_TOOL = Path("deploy/tools/build_phase8_post_phase7_operator_handoff.py")
REQUEST_TOOL = Path("deploy/tools/build_phase8_offline_step_request.py")
SIGNER_TOOL = Path("deploy/tools/build_phase8_offline_step_signed_authorization.py")

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
    "offline_step_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "debt_type",
    "scope",
    "reason",
    "suggested_command",
    "approval_payload_sha256",
    "approval_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "authorization_expires_at",
    "fresh_handoff_matches_saved",
    "fresh_authorization_matches_saved",
    "human_offline_step_authorization_verified",
    "approval_not_expired",
    "automatic_action_available",
    "allowed_offline_debt_type",
    "one_step_only",
    "fresh_phase8_handoff_recheck_completed",
    "phase8_research_only",
    "phase8_read_only",
    "phase8_policy_actionable",
    "phase8_execution_wired",
    "offline_step_execution_readiness_ready",
    "readiness_only",
    "requires_immediate_one_shot_offline_executor",
    "requires_post_step_audit",
    "offline_step_execution_authorized",
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
        _load_module(source / HANDOFF_TOOL, "phase8_offline_readiness_handoff"),
        _load_module(source / REQUEST_TOOL, "phase8_offline_readiness_request"),
        _load_module(source / SIGNER_TOOL, "phase8_offline_readiness_signer"),
    )


def _production_database(production: Path) -> Path:
    data = production / "data"
    if data.is_symlink() or not data.is_dir():
        raise ValueError("Phase 8 offline-step readiness data directory is unsafe")
    database = data / "pio.db"
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError(
            "Phase 8 offline-step readiness Pio database is missing"
        ) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("Phase 8 offline-step readiness Pio database is unsafe")
    return database.resolve()


def validate_phase8_offline_step_execution_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 8 offline-step execution readiness must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError(
            "Phase 8 offline-step execution readiness schema mismatch"
        )
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
        "offline_step_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "pio_database_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
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
        "reason",
        "suggested_command",
        "approver_principal",
        "approval_id",
        "authorization_expires_at",
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
        "automatic_action_available",
        "allowed_offline_debt_type",
        "one_step_only",
        "fresh_phase8_handoff_recheck_completed",
        "phase8_research_only",
        "phase8_read_only",
        "offline_step_execution_readiness_ready",
        "readiness_only",
        "requires_immediate_one_shot_offline_executor",
        "requires_post_step_audit",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 8 offline-step readiness requires {field}=true"
            )

    for field in (
        "phase8_policy_actionable",
        "phase8_execution_wired",
        "offline_step_execution_authorized",
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
        raise ValueError(
            "Phase 8 offline-step readiness handoff digest mismatch"
        )
    if (
        report["saved_signed_authorization_verification_sha256"]
        != report["fresh_signed_authorization_verification_sha256"]
    ):
        raise ValueError(
            "Phase 8 offline-step readiness authorization digest mismatch"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError("Phase 8 offline-step readiness digest mismatch")


def build_phase8_offline_step_execution_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_phase8_handoff_path: str | Path,
    phase7_post_promotion_audit_path: str | Path,
    offline_step_request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source_candidate = Path(source_tree).expanduser()
    production_candidate = Path(repository).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    if production_candidate.is_symlink():
        raise ValueError("production repository root must not be a symlink")
    source = source_candidate.resolve()
    production = production_candidate.resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    handoff_module, request_module, signer_module = _load_reviewed(source)
    saved_handoff = _load_json(
        saved_phase8_handoff_path,
        label="saved Phase 8 handoff",
    )
    request = _load_json(
        offline_step_request_path,
        label="Phase 8 offline-step request",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved Phase 8 offline-step authorization verification",
    )
    handoff_module.validate_phase8_post_phase7_handoff(saved_handoff)
    request_module.validate_phase8_offline_step_request(request)
    signer_module.validate_verification(saved_verification)

    if (
        request["saved_phase8_handoff_sha256"]
        != saved_handoff["handoff_sha256"]
    ):
        raise ValueError(
            "Phase 8 offline-step request/handoff binding mismatch"
        )
    if saved_verification["request_sha256"] != request["request_sha256"]:
        raise ValueError(
            "Phase 8 offline-step authorization/request binding mismatch"
        )
    for field in (
        "saved_phase8_handoff_sha256",
        "pio_database_sha256",
        "debt_type",
        "scope",
    ):
        if saved_verification.get(field) != request.get(field):
            raise ValueError(
                f"Phase 8 offline-step authorization {field} binding mismatch"
            )
    if (
        saved_verification.get(
            "human_offline_step_authorization_verified"
        )
        is not True
    ):
        raise ValueError(
            "Phase 8 human offline-step authorization is not verified"
        )
    if saved_verification.get("approval_not_expired") is not True:
        raise ValueError(
            "saved Phase 8 offline-step authorization is expired"
        )

    if (
        Path(str(saved_handoff["production_repository"])).resolve()
        != production
    ):
        raise ValueError("Phase 8 handoff repository binding mismatch")
    if Path(str(request["production_repository"])).resolve() != production:
        raise ValueError(
            "Phase 8 offline-step request repository binding mismatch"
        )

    operator = saved_handoff.get("phase8_operator_handoff")
    if not isinstance(operator, dict):
        raise ValueError("Phase 8 operator handoff is missing")
    for field in ("debt_type", "scope", "reason", "suggested_command"):
        if operator.get(field) != request.get(field):
            raise ValueError(
                f"Phase 8 offline-step request {field} drifted from handoff"
            )

    allowed_debt_types = getattr(
        request_module,
        "ALLOWED_DEBT_TYPES",
        set(),
    )
    if request.get("debt_type") not in allowed_debt_types:
        raise ValueError(
            "Phase 8 offline-step readiness debt type is not allowed"
        )
    for field in (
        "automatic_action_available",
        "allowed_offline_debt_type",
        "one_step_only",
        "request_ready",
        "fresh_phase8_handoff_recheck_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                "Phase 8 offline-step readiness requires "
                f"request {field}=true"
            )

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
        raise ValueError(
            "fresh Phase 8 handoff differs from saved handoff"
        )

    fresh_verification = signer_module.verify_authorization(
        source_tree=source,
        request_path=offline_step_request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        now=now,
    )
    signer_module.validate_verification(fresh_verification)
    if fresh_verification != saved_verification:
        raise ValueError(
            "fresh Phase 8 offline-step authorization differs "
            "from saved verification"
        )

    database = _production_database(production)
    if Path(str(request["pio_database_path"])).resolve() != database:
        raise ValueError(
            "Phase 8 offline-step request database binding mismatch"
        )
    database_sha256 = _sha256_path(database)
    if database_sha256 != request["pio_database_sha256"]:
        raise ValueError(
            "Pio database changed after Phase 8 offline-step request"
        )

    for artifact, label in (
        (request, "offline-step request"),
        (saved_verification, "offline-step authorization verification"),
        (fresh_handoff, "fresh Phase 8 handoff"),
    ):
        for field in (
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
            if field in artifact and artifact.get(field) is not False:
                raise ValueError(
                    f"Phase 8 {label} unexpectedly authorizes {field}"
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
        "saved_phase8_handoff_sha256": saved_handoff[
            "handoff_sha256"
        ],
        "fresh_phase8_handoff_sha256": fresh_handoff[
            "handoff_sha256"
        ],
        "offline_step_request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": (
            saved_verification["verification_sha256"]
        ),
        "fresh_signed_authorization_verification_sha256": (
            fresh_verification["verification_sha256"]
        ),
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": database_sha256,
        "debt_type": request["debt_type"],
        "scope": request["scope"],
        "reason": request["reason"],
        "suggested_command": request["suggested_command"],
        "approval_payload_sha256": fresh_verification[
            "approval_payload_sha256"
        ],
        "approval_signature_sha256": fresh_verification[
            "approval_signature_sha256"
        ],
        "allowed_signers_sha256": fresh_verification[
            "allowed_signers_sha256"
        ],
        "approver_principal": fresh_verification[
            "approver_principal"
        ],
        "approval_id": fresh_verification["approval_id"],
        "authorization_expires_at": fresh_verification["expires_at"],
        "fresh_handoff_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_offline_step_authorization_verified": True,
        "approval_not_expired": True,
        "automatic_action_available": True,
        "allowed_offline_debt_type": True,
        "one_step_only": True,
        "fresh_phase8_handoff_recheck_completed": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "offline_step_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_one_shot_offline_executor": True,
        "requires_post_step_audit": True,
        "offline_step_execution_authorized": False,
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
        "readiness_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_offline_step_execution_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshly revalidate the Phase 8 post-Phase 7 handoff and "
            "short-lived human authorization for exactly one allowed offline "
            "research step. This readiness stage never runs the step, starts "
            "PAPER, submits a transaction, uses live capital, or mutates "
            "production state."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-phase8-handoff", required=True)
    parser.add_argument("--phase7-post-promotion-audit", required=True)
    parser.add_argument("--offline-step-request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument(
        "--expected-allowed-signers-sha256",
        required=True,
    )
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_phase8_offline_step_execution_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_phase8_handoff_path=args.saved_phase8_handoff,
        phase7_post_promotion_audit_path=(
            args.phase7_post_promotion_audit
        ),
        offline_step_request_path=args.offline_step_request,
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
