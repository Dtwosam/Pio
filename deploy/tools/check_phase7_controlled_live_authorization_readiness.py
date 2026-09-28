from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_AUTHORIZATION_READINESS_V1"

STATUS_TOOL = Path("deploy/tools/check_phase7_controlled_live_evidence_status.py")
PLAN_TOOL = Path("deploy/tools/build_phase7_controlled_live_evidence_plan.py")
PREFLIGHT_TOOL = Path("deploy/tools/check_phase7_controlled_live_input_preflight.py")
SIGNER_TOOL = Path("deploy/tools/build_phase7_controlled_live_signed_authorization.py")
PHASE6_AUDIT_TOOL = Path("deploy/tools/check_phase6_prelive_post_promotion.py")

REVIEWED_SOURCE_BLOBS = {
    STATUS_TOOL: "77aa894be5d3e04534e521d159fa4743e759ef06",
    PLAN_TOOL: "3afb813bc08a57bc0a9d1a8d2df4439e4bfa8824",
    PREFLIGHT_TOOL: "3696ac1b747cedc6bd3e2c10c84c28ad81cfa711",
    SIGNER_TOOL: "c18c16dae3f65491ce4a660ec54f01dfe53a1cfa",
    PHASE6_AUDIT_TOOL: "a62643733bfab622cb8e59777cf31f4720ef6ce0",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_phase7_evidence_status_sha256",
    "fresh_phase7_evidence_status_sha256",
    "saved_phase7_evidence_plan_sha256",
    "fresh_phase7_evidence_plan_sha256",
    "saved_input_preflight_sha256",
    "fresh_input_preflight_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "phase6_post_promotion_audit_sha256",
    "production_repository",
    "pio_database_path",
    "controlled_live_config_sha256",
    "proposal_sha256",
    "input_manifest_sha256",
    "executor_wallet_pubkey",
    "approval_payload_sha256",
    "approval_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "fresh_status_matches_saved",
    "fresh_plan_matches_saved",
    "fresh_preflight_matches_saved",
    "fresh_authorization_matches_saved",
    "human_controlled_live_authorization_verified",
    "approval_not_expired",
    "phase6_dependency_current",
    "ledger_clean",
    "failed_receipts_zero",
    "open_positions_zero",
    "existing_closed_positions_fully_valued",
    "existing_closed_positions_fully_labeled",
    "new_entry_evidence_candidate",
    "single_pool_scope",
    "single_position_scope",
    "single_daily_entry_scope",
    "rebalance_disabled",
    "exit_enabled",
    "authorization_readiness_ready",
    "requires_rust_controlled_live_check",
    "requires_rust_risk_gate",
    "requires_exact_presign_evidence",
    "requires_proposal_transaction_account_binding",
    "requires_fresh_blockhash",
    "requires_final_simulation",
    "requires_wallet_authorization",
    "requires_live_submit_feature",
    "requires_runtime_live_submit_opt_in",
    "requires_separate_transaction_execution_gate",
    "requires_confirmation_receipt_reconciliation",
    "requires_separate_phase7_promotion_action",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any, Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 readiness dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 7 readiness dependency mismatch: {relative}")

    return (
        _load_module(source / STATUS_TOOL, "phase7_readiness_status"),
        _load_module(source / PLAN_TOOL, "phase7_readiness_plan"),
        _load_module(source / PREFLIGHT_TOOL, "phase7_readiness_preflight"),
        _load_module(source / SIGNER_TOOL, "phase7_readiness_signer"),
        _load_module(source / PHASE6_AUDIT_TOOL, "phase7_readiness_phase6"),
    )


def validate_phase7_authorization_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 authorization readiness must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("Phase 7 authorization readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 authorization readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 authorization readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 authorization readiness lineage mismatch")

    for field in (
        "saved_phase7_evidence_status_sha256",
        "fresh_phase7_evidence_status_sha256",
        "saved_phase7_evidence_plan_sha256",
        "fresh_phase7_evidence_plan_sha256",
        "saved_input_preflight_sha256",
        "fresh_input_preflight_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "phase6_post_promotion_audit_sha256",
        "controlled_live_config_sha256",
        "proposal_sha256",
        "input_manifest_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 authorization readiness {field} invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "executor_wallet_pubkey",
        "approver_principal",
        "approval_id",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 7 authorization readiness {field} invalid")

    if not report["production_repository"].startswith("/"):
        raise ValueError("Phase 7 readiness production repository must be absolute")
    if not report["pio_database_path"].startswith("/"):
        raise ValueError("Phase 7 readiness Pio database path must be absolute")

    for field in (
        "fresh_status_matches_saved",
        "fresh_plan_matches_saved",
        "fresh_preflight_matches_saved",
        "fresh_authorization_matches_saved",
        "human_controlled_live_authorization_verified",
        "approval_not_expired",
        "phase6_dependency_current",
        "ledger_clean",
        "failed_receipts_zero",
        "open_positions_zero",
        "existing_closed_positions_fully_valued",
        "existing_closed_positions_fully_labeled",
        "new_entry_evidence_candidate",
        "single_pool_scope",
        "single_position_scope",
        "single_daily_entry_scope",
        "rebalance_disabled",
        "exit_enabled",
        "authorization_readiness_ready",
        "requires_rust_controlled_live_check",
        "requires_rust_risk_gate",
        "requires_exact_presign_evidence",
        "requires_proposal_transaction_account_binding",
        "requires_fresh_blockhash",
        "requires_final_simulation",
        "requires_wallet_authorization",
        "requires_live_submit_feature",
        "requires_runtime_live_submit_opt_in",
        "requires_separate_transaction_execution_gate",
        "requires_confirmation_receipt_reconciliation",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 authorization readiness requires {field}=true")

    for field in (
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 7 authorization readiness requires {field}=false")

    digest_pairs = (
        ("saved_phase7_evidence_status_sha256", "fresh_phase7_evidence_status_sha256"),
        ("saved_phase7_evidence_plan_sha256", "fresh_phase7_evidence_plan_sha256"),
        ("saved_input_preflight_sha256", "fresh_input_preflight_sha256"),
        (
            "saved_signed_authorization_verification_sha256",
            "fresh_signed_authorization_verification_sha256",
        ),
    )
    for saved, fresh in digest_pairs:
        if report[saved] != report[fresh]:
            raise ValueError(f"Phase 7 authorization readiness {saved}/{fresh} mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["readiness_sha256"] != expected:
        raise ValueError("Phase 7 authorization readiness digest mismatch")


def build_phase7_authorization_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    phase6_post_promotion_audit_path: str | Path,
    saved_phase7_evidence_status_path: str | Path,
    saved_phase7_evidence_plan_path: str | Path,
    saved_input_preflight_path: str | Path,
    controlled_live_config_path: str | Path,
    proposal_path: str | Path,
    executor_wallet_pubkey: str,
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

    (
        status_module,
        plan_module,
        preflight_module,
        signer_module,
        phase6_module,
    ) = _load_reviewed_modules(source)

    phase6 = _load_json(
        phase6_post_promotion_audit_path,
        label="Phase 6 post-promotion audit",
    )
    saved_status = _load_json(
        saved_phase7_evidence_status_path,
        label="saved Phase 7 evidence status",
    )
    saved_plan = _load_json(
        saved_phase7_evidence_plan_path,
        label="saved Phase 7 evidence plan",
    )
    saved_preflight = _load_json(
        saved_input_preflight_path,
        label="saved Phase 7 input preflight",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved Phase 7 signed authorization verification",
    )

    phase6_module.validate_post_promotion_audit(phase6)
    status_module.validate_phase7_evidence_status(saved_status)
    plan_module.validate_phase7_evidence_plan(saved_plan)
    preflight_module.validate_phase7_input_preflight(saved_preflight)
    signer_module.validate_verification(saved_verification)

    if phase6.get("post_promotion_audit_sha256") != saved_status.get(
        "phase6_post_promotion_audit_sha256"
    ):
        raise ValueError("Phase 7 status/Phase 6 audit binding mismatch")
    if Path(str(phase6["production_repository"])).resolve() != production:
        raise ValueError("Phase 7 production repository binding mismatch")
    if saved_plan.get("phase7_evidence_status_sha256") != saved_status.get(
        "phase7_evidence_status_sha256"
    ):
        raise ValueError("Phase 7 plan/status binding mismatch")
    if saved_preflight.get("phase7_evidence_plan_sha256") != saved_plan.get(
        "plan_sha256"
    ):
        raise ValueError("Phase 7 preflight/plan binding mismatch")
    if saved_verification.get("input_preflight_sha256") != saved_preflight.get(
        "input_preflight_sha256"
    ):
        raise ValueError("Phase 7 signature/preflight binding mismatch")

    fresh_status = status_module.build_phase7_evidence_status(
        repository=production,
        source_tree=source,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
    )
    status_module.validate_phase7_evidence_status(fresh_status)
    if fresh_status != saved_status:
        raise ValueError("fresh Phase 7 evidence status differs from saved status")

    fresh_plan = plan_module.build_phase7_evidence_plan(
        source_tree=source,
        phase7_evidence_status_path=saved_phase7_evidence_status_path,
    )
    plan_module.validate_phase7_evidence_plan(fresh_plan)
    if fresh_plan != saved_plan:
        raise ValueError("fresh Phase 7 evidence plan differs from saved plan")

    fresh_preflight = preflight_module.build_phase7_input_preflight(
        source_tree=source,
        phase7_evidence_plan_path=saved_phase7_evidence_plan_path,
        controlled_live_config_path=controlled_live_config_path,
        proposal_path=proposal_path,
        executor_wallet_pubkey=executor_wallet_pubkey,
    )
    preflight_module.validate_phase7_input_preflight(fresh_preflight)
    if fresh_preflight != saved_preflight:
        raise ValueError("fresh Phase 7 input preflight differs from saved preflight")

    fresh_verification = signer_module.verify_authorization(
        source_tree=source,
        input_preflight_path=saved_input_preflight_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        now=now,
    )
    signer_module.validate_verification(fresh_verification)
    if fresh_verification != saved_verification:
        raise ValueError(
            "fresh Phase 7 authorization verification differs from saved verification"
        )

    ledger = fresh_status["ledger_audit"]
    if ledger.get("clean") is not True:
        raise ValueError("Phase 7 readiness requires a clean live ledger")
    if fresh_status.get("failed_receipts") != 0:
        raise ValueError("Phase 7 readiness requires zero failed receipts")
    if fresh_status.get("open_positions") != 0:
        raise ValueError("Phase 7 readiness requires zero open positions")
    if fresh_status.get("valued_closed_positions") != fresh_status.get(
        "closed_positions"
    ):
        raise ValueError("Phase 7 readiness requires full valuation coverage")
    if fresh_status.get("labeled_closed_positions") != fresh_status.get(
        "closed_positions"
    ):
        raise ValueError("Phase 7 readiness requires full learning-label coverage")
    if fresh_plan.get("new_entry_evidence_candidate") is not True:
        raise ValueError("Phase 7 readiness does not permit new entry evidence")

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
        "saved_phase7_evidence_status_sha256": saved_status[
            "phase7_evidence_status_sha256"
        ],
        "fresh_phase7_evidence_status_sha256": fresh_status[
            "phase7_evidence_status_sha256"
        ],
        "saved_phase7_evidence_plan_sha256": saved_plan["plan_sha256"],
        "fresh_phase7_evidence_plan_sha256": fresh_plan["plan_sha256"],
        "saved_input_preflight_sha256": saved_preflight[
            "input_preflight_sha256"
        ],
        "fresh_input_preflight_sha256": fresh_preflight[
            "input_preflight_sha256"
        ],
        "saved_signed_authorization_verification_sha256": saved_verification[
            "verification_sha256"
        ],
        "fresh_signed_authorization_verification_sha256": fresh_verification[
            "verification_sha256"
        ],
        "phase6_post_promotion_audit_sha256": phase6[
            "post_promotion_audit_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": fresh_status["pio_database_path"],
        "controlled_live_config_sha256": fresh_preflight[
            "controlled_live_config_sha256"
        ],
        "proposal_sha256": fresh_preflight["proposal_sha256"],
        "input_manifest_sha256": fresh_preflight["input_manifest_sha256"],
        "executor_wallet_pubkey": fresh_preflight["executor_wallet_pubkey"],
        "approval_payload_sha256": fresh_verification[
            "approval_payload_sha256"
        ],
        "approval_signature_sha256": fresh_verification[
            "approval_signature_sha256"
        ],
        "allowed_signers_sha256": fresh_verification["allowed_signers_sha256"],
        "approver_principal": fresh_verification["approver_principal"],
        "approval_id": fresh_verification["approval_id"],
        "fresh_status_matches_saved": True,
        "fresh_plan_matches_saved": True,
        "fresh_preflight_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_controlled_live_authorization_verified": True,
        "approval_not_expired": True,
        "phase6_dependency_current": True,
        "ledger_clean": True,
        "failed_receipts_zero": True,
        "open_positions_zero": True,
        "existing_closed_positions_fully_valued": True,
        "existing_closed_positions_fully_labeled": True,
        "new_entry_evidence_candidate": True,
        "single_pool_scope": True,
        "single_position_scope": True,
        "single_daily_entry_scope": True,
        "rebalance_disabled": True,
        "exit_enabled": True,
        "authorization_readiness_ready": True,
        "requires_rust_controlled_live_check": True,
        "requires_rust_risk_gate": True,
        "requires_exact_presign_evidence": True,
        "requires_proposal_transaction_account_binding": True,
        "requires_fresh_blockhash": True,
        "requires_final_simulation": True,
        "requires_wallet_authorization": True,
        "requires_live_submit_feature": True,
        "requires_runtime_live_submit_opt_in": True,
        "requires_separate_transaction_execution_gate": True,
        "requires_confirmation_receipt_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase7_authorization_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Perform the final read-only freshness check for one exact Phase 7 "
            "controlled-LIVE evidence attempt. The gate re-evaluates current "
            "Phase 7 state, re-derives the plan/preflight, and re-verifies the "
            "short-lived human signature. It still does not authorize signing, "
            "submission, or live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase6-post-promotion-audit", required=True)
    parser.add_argument("--saved-phase7-evidence-status", required=True)
    parser.add_argument("--saved-phase7-evidence-plan", required=True)
    parser.add_argument("--saved-input-preflight", required=True)
    parser.add_argument("--controlled-live-config", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--executor-wallet-pubkey", required=True)
    parser.add_argument("--saved-signed-authorization-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_phase7_authorization_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        phase6_post_promotion_audit_path=args.phase6_post_promotion_audit,
        saved_phase7_evidence_status_path=args.saved_phase7_evidence_status,
        saved_phase7_evidence_plan_path=args.saved_phase7_evidence_plan,
        saved_input_preflight_path=args.saved_input_preflight,
        controlled_live_config_path=args.controlled_live_config,
        proposal_path=args.proposal,
        executor_wallet_pubkey=args.executor_wallet_pubkey,
        saved_signed_authorization_verification_path=(
            args.saved_signed_authorization_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
