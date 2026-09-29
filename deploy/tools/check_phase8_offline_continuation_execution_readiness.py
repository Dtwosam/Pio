from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_OFFLINE_CONTINUATION_EXECUTION_READINESS_V1"
)

POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_offline_step_post_audit.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_phase8_offline_step_continuation_request.py"
)
SIGNER_TOOL = Path(
    "deploy/tools/build_phase8_offline_continuation_signed_authorization.py"
)

REVIEWED_SOURCE_BLOBS = {
    POST_AUDIT_TOOL: "dff8ad3e889912ed3d142e7222fec6436bd20b2e",
    REQUEST_TOOL: "864a945d90469f72740943015df9bf3d6396574c",
    SIGNER_TOOL: "0de8e00faabd903fe3de3c9a57c37a9d4657a82a",
}

ALLOWED_DEBT_TYPES = {
    "RETRAIN_DATASET_BUILD_READY",
    "RETRAIN_OFFLINE_TRAIN_READY",
    "RETRAIN_OFFLINE_VALIDATION_READY",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_post_audit_sha256",
    "fresh_post_audit_sha256",
    "saved_post_audit_state_sha256",
    "fresh_post_audit_state_sha256",
    "continuation_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "source_execution_receipt_sha256",
    "source_offline_step_request_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "research_artifacts_sha256",
    "research_artifact_count",
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
    "fresh_post_audit_state_matches_saved",
    "fresh_continuation_request_matches_saved",
    "fresh_authorization_matches_saved",
    "human_continuation_offline_step_authorization_verified",
    "approval_not_expired",
    "automatic_action_available",
    "allowed_offline_debt_type",
    "one_step_only",
    "fresh_post_audit_recheck_completed",
    "phase8_research_only",
    "phase8_read_only",
    "phase8_policy_actionable",
    "phase8_execution_wired",
    "continuation_execution_readiness_ready",
    "readiness_only",
    "requires_immediate_one_shot_continuation_executor",
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
    "production_research_artifacts_modified",
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
    if not resolved.is_file():
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
                "Phase 8 continuation readiness dependency missing: "
                f"{relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                "Phase 8 continuation readiness dependency mismatch: "
                f"{relative}"
            )
    return (
        _load_module(
            source / POST_AUDIT_TOOL,
            "phase8_continuation_readiness_post_audit",
        ),
        _load_module(
            source / REQUEST_TOOL,
            "phase8_continuation_readiness_request",
        ),
        _load_module(
            source / SIGNER_TOOL,
            "phase8_continuation_readiness_signer",
        ),
    )


def _operator_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 8 continuation readiness operator handoff is invalid"
        )
    return {
        "status": value.get("status"),
        "automatic_action_available": value.get(
            "automatic_action_available"
        ),
        "operator_action_required": value.get(
            "operator_action_required"
        ),
        "manual_input_required": value.get("manual_input_required"),
        "debt_type": value.get("debt_type"),
        "scope": value.get("scope"),
        "reason": value.get("reason"),
        "suggested_command": value.get("suggested_command"),
        "research_only": value.get("research_only"),
        "read_only": value.get("read_only"),
        "policy_actionable": value.get("policy_actionable"),
        "execution_wired": value.get("execution_wired"),
    }


def _audit_state_projection(value: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "execution_receipt_sha256",
        "offline_step_request_sha256",
        "signed_authorization_verification_sha256",
        "production_repository",
        "pio_database_path",
        "receipt_database_sha256_after",
        "receipt_wal_sha256_after",
        "receipt_shm_sha256_after",
        "receipt_research_artifacts_after",
        "audit_research_artifacts_after",
        "execution_research_artifact_change_count",
        "audit_database_sha256_after",
        "audit_wal_sha256_after",
        "audit_shm_sha256_after",
        "executed_debt_type",
        "executed_scope",
        "execution_status",
        "execution_completed",
        "execution_progressed",
        "next_debt_type",
        "next_scope",
        "continuation_route",
        "phase7_dependency_satisfied",
        "phase8_research_only",
        "phase8_read_only",
        "phase8_policy_actionable",
        "phase8_execution_wired",
        "phase8_promotion_ready",
        "phase8_persisted_current",
        "post_step_audit_ready",
        "requires_separate_phase8_action",
        "requires_new_human_authorization",
        "next_offline_step_reauthorization_required",
        "paper_challenger_review_required",
        "phase8_promotion_review_required",
        "manual_or_evidence_review_required",
        "failure_review_required",
        "phase8_current",
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
    )
    projection = {field: value.get(field) for field in fields}
    projection["operator"] = _operator_projection(
        value.get("fresh_phase8_operator_handoff")
    )
    return projection


def _audit_state_sha256(value: dict[str, Any]) -> str:
    return _sha256_bytes(
        _canonical_bytes(_audit_state_projection(value))
    )


def validate_phase8_offline_continuation_execution_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 8 continuation execution readiness must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError(
            "Phase 8 continuation execution readiness schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 8 continuation readiness format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 8 continuation readiness type"
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
            "Phase 8 continuation readiness lineage mismatch"
        )

    for field in (
        "saved_post_audit_sha256",
        "fresh_post_audit_sha256",
        "saved_post_audit_state_sha256",
        "fresh_post_audit_state_sha256",
        "continuation_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "source_execution_receipt_sha256",
        "source_offline_step_request_sha256",
        "pio_database_sha256",
        "research_artifacts_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 8 continuation readiness {field} is invalid"
            )

    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 8 continuation readiness {field} is invalid"
            )

    count = report.get("research_artifact_count")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
    ):
        raise ValueError(
            "Phase 8 continuation readiness artifact count is invalid"
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
                f"Phase 8 continuation readiness {field} is invalid"
            )

    for field in (
        "fresh_post_audit_state_matches_saved",
        "fresh_continuation_request_matches_saved",
        "fresh_authorization_matches_saved",
        "human_continuation_offline_step_authorization_verified",
        "approval_not_expired",
        "automatic_action_available",
        "allowed_offline_debt_type",
        "one_step_only",
        "fresh_post_audit_recheck_completed",
        "phase8_research_only",
        "phase8_read_only",
        "continuation_execution_readiness_ready",
        "readiness_only",
        "requires_immediate_one_shot_continuation_executor",
        "requires_post_step_audit",
    ):
        if report.get(field) is not True:
            raise ValueError(
                "Phase 8 continuation readiness requires "
                f"{field}=true"
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
        "production_research_artifacts_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                "Phase 8 continuation readiness requires "
                f"{field}=false"
            )

    if report["debt_type"] not in ALLOWED_DEBT_TYPES:
        raise ValueError(
            "Phase 8 continuation readiness debt type is not allowed"
        )
    if (
        report["saved_post_audit_state_sha256"]
        != report["fresh_post_audit_state_sha256"]
    ):
        raise ValueError(
            "Phase 8 continuation readiness post-audit state mismatch"
        )
    if (
        report["saved_signed_authorization_verification_sha256"]
        != report["fresh_signed_authorization_verification_sha256"]
    ):
        raise ValueError(
            "Phase 8 continuation readiness authorization mismatch"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 8 continuation readiness digest mismatch"
        )


def build_phase8_offline_continuation_execution_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_post_audit_path: str | Path,
    execution_receipt_path: str | Path,
    continuation_request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    audit_module, request_module, signer_module = _load_reviewed(
        source
    )
    saved_audit = _load_json(
        saved_post_audit_path,
        label="saved Phase 8 post-step audit",
    )
    request = _load_json(
        continuation_request_path,
        label="Phase 8 offline continuation request",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved Phase 8 continuation authorization verification",
    )
    audit_module.validate_phase8_offline_step_post_audit(saved_audit)
    request_module.validate_phase8_offline_continuation_request(
        request
    )
    signer_module.validate_verification(saved_verification)

    if (
        request["source_post_audit_sha256"]
        != saved_audit["post_audit_sha256"]
    ):
        raise ValueError(
            "Phase 8 continuation request/post-audit binding mismatch"
        )
    if (
        request["source_execution_receipt_sha256"]
        != saved_audit["execution_receipt_sha256"]
    ):
        raise ValueError(
            "Phase 8 continuation request/receipt binding mismatch"
        )
    if (
        request["source_offline_step_request_sha256"]
        != saved_audit["offline_step_request_sha256"]
    ):
        raise ValueError(
            "Phase 8 continuation prior-request lineage mismatch"
        )
    if saved_verification["request_sha256"] != request["request_sha256"]:
        raise ValueError(
            "Phase 8 continuation verification/request binding mismatch"
        )

    for field in (
        "source_post_audit_sha256",
        "source_execution_receipt_sha256",
        "source_offline_step_request_sha256",
        "pio_database_sha256",
        "pio_wal_sha256",
        "pio_shm_sha256",
        "research_artifacts_sha256",
        "research_artifact_count",
        "debt_type",
        "scope",
    ):
        if saved_verification.get(field) != request.get(field):
            raise ValueError(
                "Phase 8 continuation verification "
                f"{field} binding mismatch"
            )

    if (
        saved_verification.get(
            "human_continuation_offline_step_authorization_verified"
        )
        is not True
    ):
        raise ValueError(
            "Phase 8 continuation human authorization is not verified"
        )
    if saved_verification.get("approval_not_expired") is not True:
        raise ValueError(
            "saved Phase 8 continuation authorization is expired"
        )

    if (
        Path(str(saved_audit["production_repository"])).resolve()
        != production
    ):
        raise ValueError(
            "Phase 8 continuation post-audit repository mismatch"
        )
    if Path(str(request["production_repository"])).resolve() != production:
        raise ValueError(
            "Phase 8 continuation request repository mismatch"
        )

    route_next = getattr(
        audit_module,
        "ROUTE_NEXT_OFFLINE",
        "PHASE8_NEXT_OFFLINE_STEP_REAUTHORIZATION",
    )
    if saved_audit.get("continuation_route") != route_next:
        raise ValueError(
            "saved Phase 8 post-audit is no longer offline-continuation routed"
        )

    fresh_audit = audit_module.build_phase8_offline_step_post_audit(
        repository=production,
        source_tree=source,
        execution_receipt_path=execution_receipt_path,
    )
    audit_module.validate_phase8_offline_step_post_audit(fresh_audit)
    saved_audit_state_sha = _audit_state_sha256(saved_audit)
    fresh_audit_state_sha = _audit_state_sha256(fresh_audit)
    if fresh_audit_state_sha != saved_audit_state_sha:
        raise ValueError(
            "fresh Phase 8 post-audit decisive state differs from saved audit"
        )

    fresh_request = (
        request_module.build_phase8_offline_continuation_request(
            repository=production,
            source_tree=source,
            post_audit_path=saved_post_audit_path,
            expected_post_audit_sha256=saved_audit[
                "post_audit_sha256"
            ],
        )
    )
    request_module.validate_phase8_offline_continuation_request(
        fresh_request
    )
    if fresh_request != request:
        raise ValueError(
            "fresh Phase 8 continuation request differs from saved request"
        )

    fresh_verification = signer_module.verify_authorization(
        source_tree=source,
        request_path=continuation_request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=(
            expected_allowed_signers_sha256
        ),
        now=now,
    )
    signer_module.validate_verification(fresh_verification)
    if fresh_verification != saved_verification:
        raise ValueError(
            "fresh Phase 8 continuation authorization differs "
            "from saved verification"
        )

    if fresh_audit.get("next_debt_type") != request["debt_type"]:
        raise ValueError(
            "fresh Phase 8 post-audit debt type differs from request"
        )
    if fresh_audit.get("next_scope") != request["scope"]:
        raise ValueError(
            "fresh Phase 8 post-audit scope differs from request"
        )

    operator = fresh_audit.get("fresh_phase8_operator_handoff")
    operator_projection = _operator_projection(operator)
    expected_operator = {
        "status": "AUTOMATIC_ACTION",
        "automatic_action_available": True,
        "operator_action_required": False,
        "manual_input_required": False,
        "debt_type": request["debt_type"],
        "scope": request["scope"],
        "reason": request["reason"],
        "suggested_command": request["suggested_command"],
        "research_only": True,
        "read_only": True,
        "policy_actionable": False,
        "execution_wired": False,
    }
    if operator_projection != expected_operator:
        raise ValueError(
            "fresh Phase 8 operator action differs from continuation request"
        )

    if request["debt_type"] not in ALLOWED_DEBT_TYPES:
        raise ValueError(
            "Phase 8 continuation readiness debt type is not allowed"
        )

    for artifact, label in (
        (saved_audit, "saved post-audit"),
        (fresh_audit, "fresh post-audit"),
        (request, "continuation request"),
        (saved_verification, "continuation authorization"),
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
        "saved_post_audit_sha256": saved_audit[
            "post_audit_sha256"
        ],
        "fresh_post_audit_sha256": fresh_audit[
            "post_audit_sha256"
        ],
        "saved_post_audit_state_sha256": saved_audit_state_sha,
        "fresh_post_audit_state_sha256": fresh_audit_state_sha,
        "continuation_request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": (
            saved_verification["verification_sha256"]
        ),
        "fresh_signed_authorization_verification_sha256": (
            fresh_verification["verification_sha256"]
        ),
        "source_execution_receipt_sha256": request[
            "source_execution_receipt_sha256"
        ],
        "source_offline_step_request_sha256": request[
            "source_offline_step_request_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": request["pio_database_path"],
        "pio_database_sha256": request["pio_database_sha256"],
        "pio_wal_sha256": request["pio_wal_sha256"],
        "pio_shm_sha256": request["pio_shm_sha256"],
        "research_artifacts_sha256": request[
            "research_artifacts_sha256"
        ],
        "research_artifact_count": request[
            "research_artifact_count"
        ],
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
        "fresh_post_audit_state_matches_saved": True,
        "fresh_continuation_request_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_continuation_offline_step_authorization_verified": True,
        "approval_not_expired": True,
        "automatic_action_available": True,
        "allowed_offline_debt_type": True,
        "one_step_only": True,
        "fresh_post_audit_recheck_completed": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "continuation_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_one_shot_continuation_executor": True,
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
        "production_research_artifacts_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_offline_continuation_execution_readiness(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshly recheck the Phase 8 post-step audit, exact continuation "
            "request, and short-lived human signature before one additional "
            "offline research step. This readiness stage never executes the "
            "step, starts PAPER, uses live capital, submits transactions, or "
            "persists Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-post-audit", required=True)
    parser.add_argument("--execution-receipt", required=True)
    parser.add_argument("--continuation-request", required=True)
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

    report = build_phase8_offline_continuation_execution_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_post_audit_path=args.saved_post_audit,
        execution_receipt_path=args.execution_receipt,
        continuation_request_path=args.continuation_request,
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
