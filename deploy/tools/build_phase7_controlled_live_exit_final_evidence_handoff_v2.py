from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_FINAL_EVIDENCE_HANDOFF_V2"

POST_LABEL_AUDIT_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_post_learning_label_v2.py"
)
EVIDENCE_STATUS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_evidence_status.py"
)
EVIDENCE_PLAN_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_evidence_plan.py"
)
REVIEWED_SOURCE_BLOBS = {
    POST_LABEL_AUDIT_TOOL: "5ec3f274f178574e055fe4058c297b43d90d6a7d",
    EVIDENCE_STATUS_TOOL: "77aa894be5d3e04534e521d159fa4743e759ef06",
    EVIDENCE_PLAN_TOOL: "3afb813bc08a57bc0a9d1a8d2df4439e4bfa8824",
}

ROUTE_LEDGER = "LEDGER_RECONCILIATION"
ROUTE_FAILURE = "HISTORICAL_FAILURE_REVIEW"
ROUTE_OPEN = "OPEN_POSITION_LIFECYCLE"
ROUTE_VALUATION = "VALUATION_COMPLETION"
ROUTE_LABEL = "LABEL_COMPLETION"
ROUTE_PROMOTION = "PHASE7_PROMOTION_REVIEW"
ROUTE_MORE_EVIDENCE = "CONTROLLED_LIVE_EVIDENCE_CANDIDATE"
ROUTE_REVIEW = "EVIDENCE_REVIEW"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_post_label_audit_sha256",
    "expected_post_label_audit_sha256",
    "production_repository",
    "pio_database_path",
    "post_label_database_sha256",
    "fresh_status_database_sha256_before",
    "fresh_status_database_sha256_after",
    "database_unchanged_since_post_label_audit",
    "phase7_evidence_status_sha256",
    "phase7_evidence_plan_sha256",
    "opened_decision_id",
    "principal_exit_decision_id",
    "settlement_decision_id",
    "pool_address",
    "position_address",
    "position_status",
    "learning_label_sha256",
    "confirmed_receipts",
    "failed_receipts",
    "closed_positions",
    "open_positions",
    "distinct_closed_pools",
    "valued_closed_positions",
    "labeled_closed_positions",
    "phase7_promotion_ready",
    "phase7_reasons",
    "ledger_clean",
    "historical_failed_receipts_blocked",
    "open_positions_require_exit_reconciliation",
    "ledger_reconciliation_required",
    "valuation_completion_required",
    "label_completion_required",
    "new_entry_evidence_candidate",
    "continuation_route",
    "continuation_handoff_ready",
    "requires_operator_review",
    "requires_separate_controlled_live_authorization",
    "requires_separate_transaction_authorization",
    "requires_separate_phase7_promotion_action",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
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


def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


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
                f"Phase 7 EXIT final handoff dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT final handoff dependency mismatch: {relative}"
            )
    post = _load_module(
        source / POST_LABEL_AUDIT_TOOL,
        "phase7_exit_final_handoff_post_label",
    )
    status = _load_module(
        source / EVIDENCE_STATUS_TOOL,
        "phase7_exit_final_handoff_status",
    )
    plan = _load_module(
        source / EVIDENCE_PLAN_TOOL,
        "phase7_exit_final_handoff_plan",
    )
    return post, status, plan


def _route(plan: dict[str, Any]) -> str:
    if plan.get("ledger_reconciliation_required") is True:
        return ROUTE_LEDGER
    if plan.get("historical_failed_receipts_blocked") is True:
        return ROUTE_FAILURE
    if plan.get("open_positions_require_exit_reconciliation") is True:
        return ROUTE_OPEN
    if plan.get("valuation_completion_required") is True:
        return ROUTE_VALUATION
    if plan.get("label_completion_required") is True:
        return ROUTE_LABEL
    if plan.get("phase7_promotion_ready") is True:
        return ROUTE_PROMOTION
    if plan.get("new_entry_evidence_candidate") is True:
        return ROUTE_MORE_EVIDENCE
    return ROUTE_REVIEW


def validate_exit_final_evidence_handoff_v2(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT final handoff v2 must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"handoff_sha256"}:
        raise ValueError(
            "Phase 7 EXIT final handoff v2 schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT final handoff v2 format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT final handoff v2 type"
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
            "Phase 7 EXIT final handoff v2 lineage mismatch"
        )

    for field in (
        "saved_post_label_audit_sha256",
        "expected_post_label_audit_sha256",
        "post_label_database_sha256",
        "fresh_status_database_sha256_before",
        "fresh_status_database_sha256_after",
        "phase7_evidence_status_sha256",
        "phase7_evidence_plan_sha256",
        "learning_label_sha256",
        "handoff_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT final handoff v2 {field} is invalid"
            )

    if report["saved_post_label_audit_sha256"] != report[
        "expected_post_label_audit_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT final handoff v2 post-label digest mismatch"
        )
    if report["post_label_database_sha256"] != report[
        "fresh_status_database_sha256_before"
    ]:
        raise ValueError(
            "Phase 7 EXIT final handoff v2 database changed before status"
        )
    if report["fresh_status_database_sha256_before"] != report[
        "fresh_status_database_sha256_after"
    ]:
        raise ValueError(
            "Phase 7 EXIT final handoff v2 database changed during status"
        )

    for field in (
        "production_repository",
        "pio_database_path",
        "opened_decision_id",
        "principal_exit_decision_id",
        "settlement_decision_id",
        "pool_address",
        "position_address",
        "position_status",
        "continuation_route",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT final handoff v2 {field} is invalid"
            )
    if report["position_status"] != "CLOSED":
        raise ValueError(
            "Phase 7 EXIT final handoff v2 requires CLOSED position"
        )

    for field in (
        "confirmed_receipts",
        "failed_receipts",
        "closed_positions",
        "open_positions",
        "distinct_closed_pools",
        "valued_closed_positions",
        "labeled_closed_positions",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT final handoff v2 {field} is invalid"
            )

    if report["closed_positions"] < 1:
        raise ValueError(
            "Phase 7 EXIT final handoff v2 lost closed-position evidence"
        )
    if report["valued_closed_positions"] < 1:
        raise ValueError(
            "Phase 7 EXIT final handoff v2 lost valuation evidence"
        )
    if report["labeled_closed_positions"] < 1:
        raise ValueError(
            "Phase 7 EXIT final handoff v2 lost label evidence"
        )

    if not isinstance(report.get("phase7_promotion_ready"), bool):
        raise ValueError(
            "Phase 7 EXIT final handoff v2 promotion-ready flag is invalid"
        )
    reasons = report.get("phase7_reasons")
    if (
        not isinstance(reasons, list)
        or any(not isinstance(reason, str) or not reason for reason in reasons)
    ):
        raise ValueError(
            "Phase 7 EXIT final handoff v2 reasons are invalid"
        )

    for field in (
        "ledger_clean",
        "historical_failed_receipts_blocked",
        "open_positions_require_exit_reconciliation",
        "ledger_reconciliation_required",
        "valuation_completion_required",
        "label_completion_required",
        "new_entry_evidence_candidate",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(
                f"Phase 7 EXIT final handoff v2 {field} is invalid"
            )

    expected_route = _route(report)
    if report["continuation_route"] != expected_route:
        raise ValueError(
            "Phase 7 EXIT final handoff v2 continuation route mismatch"
        )

    for field in (
        "database_unchanged_since_post_label_audit",
        "continuation_handoff_ready",
        "requires_operator_review",
        "requires_separate_controlled_live_authorization",
        "requires_separate_transaction_authorization",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT final handoff v2 requires {field}=true"
            )

    for field in (
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT final handoff v2 requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["handoff_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT final handoff v2 digest mismatch"
        )


def build_exit_final_evidence_handoff_v2(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_post_label_audit_path: str | Path,
    expected_post_label_audit_sha256: str,
    phase6_post_promotion_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")
    post_module, status_module, plan_module = _load_reviewed(source)

    post = _load_json(
        saved_post_label_audit_path,
        label="saved Phase 7 EXIT post-label v2 audit",
    )
    post_module.validate_exit_post_learning_label_audit_v2(post)
    if (
        not _is_hex_digest(expected_post_label_audit_sha256, 64)
        or post["audit_sha256"] != expected_post_label_audit_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT post-label v2 audit digest mismatch"
        )

    for field in (
        "valuation_present",
        "learning_label_present",
        "persisted_learning_label_matches_apply",
        "learning_label_reconciliation_complete",
        "phase7_evidence_status_recheck_required",
        "phase7_evidence_status_recheck_ready",
        "requires_separate_phase7_promotion_action",
        "post_label_audit_ready",
    ):
        if post.get(field) is not True:
            raise ValueError(
                f"final handoff v2 requires post-label {field}=true"
            )
    if post.get("position_status") != "CLOSED":
        raise ValueError(
            "final handoff v2 requires CLOSED post-label position"
        )
    for field in (
        "new_live_entry_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_pio_database_modified",
    ):
        if post.get(field) is not False:
            raise ValueError(
                f"final handoff v2 refuses post-label {field}=true"
            )

    status = status_module.build_phase7_evidence_status(
        repository=production,
        source_tree=source,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
    )
    status_module.validate_phase7_evidence_status(status)
    if status.get("phase7_evidence_status_ready") is not True:
        raise ValueError(
            "fresh Phase 7 evidence status is not ready"
        )
    if Path(status["pio_database_path"]).resolve() != Path(
        post["pio_database_path"]
    ).resolve():
        raise ValueError(
            "fresh Phase 7 evidence status uses a different Pio database"
        )
    if status["pio_database_sha256_before"] != post[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "Pio database changed after post-label v2 audit"
        )
    if status["pio_database_sha256_before"] != status[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "Pio database changed during fresh Phase 7 status"
        )

    with tempfile.TemporaryDirectory(
        prefix="pio-phase7-exit-final-handoff-v2-"
    ) as tmp:
        status_path = Path(tmp) / "status.json"
        status_path.write_text(
            json.dumps(
                status,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        plan = plan_module.build_phase7_evidence_plan(
            source_tree=source,
            phase7_evidence_status_path=status_path,
        )
    plan_module.validate_phase7_evidence_plan(plan)
    if plan["phase7_evidence_status_sha256"] != status[
        "phase7_evidence_status_sha256"
    ]:
        raise ValueError(
            "fresh Phase 7 evidence plan/status binding mismatch"
        )

    if status["closed_positions"] < 1:
        raise ValueError(
            "fresh Phase 7 status lost reconciled closed position"
        )
    if status["valued_closed_positions"] < 1:
        raise ValueError(
            "fresh Phase 7 status lost reconciled valuation"
        )
    if status["labeled_closed_positions"] < 1:
        raise ValueError(
            "fresh Phase 7 status lost reconciled learning label"
        )

    route = _route(plan)

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
        "saved_post_label_audit_sha256": post["audit_sha256"],
        "expected_post_label_audit_sha256": (
            expected_post_label_audit_sha256
        ),
        "production_repository": str(production),
        "pio_database_path": status["pio_database_path"],
        "post_label_database_sha256": post[
            "pio_database_sha256_after"
        ],
        "fresh_status_database_sha256_before": status[
            "pio_database_sha256_before"
        ],
        "fresh_status_database_sha256_after": status[
            "pio_database_sha256_after"
        ],
        "database_unchanged_since_post_label_audit": True,
        "phase7_evidence_status_sha256": status[
            "phase7_evidence_status_sha256"
        ],
        "phase7_evidence_plan_sha256": plan["plan_sha256"],
        "opened_decision_id": post["opened_decision_id"],
        "principal_exit_decision_id": post[
            "principal_exit_decision_id"
        ],
        "settlement_decision_id": post["settlement_decision_id"],
        "pool_address": post["pool_address"],
        "position_address": post["position_address"],
        "position_status": post["position_status"],
        "learning_label_sha256": _sha256_value(post["learning_label"]),
        "confirmed_receipts": status["confirmed_receipts"],
        "failed_receipts": status["failed_receipts"],
        "closed_positions": status["closed_positions"],
        "open_positions": status["open_positions"],
        "distinct_closed_pools": status["distinct_closed_pools"],
        "valued_closed_positions": status["valued_closed_positions"],
        "labeled_closed_positions": status["labeled_closed_positions"],
        "phase7_promotion_ready": status["phase7_promotion_ready"],
        "phase7_reasons": list(status["phase7_reasons"]),
        "ledger_clean": bool(plan["ledger_clean"]),
        "historical_failed_receipts_blocked": bool(
            plan["historical_failed_receipts_blocked"]
        ),
        "open_positions_require_exit_reconciliation": bool(
            plan["open_positions_require_exit_reconciliation"]
        ),
        "ledger_reconciliation_required": bool(
            plan["ledger_reconciliation_required"]
        ),
        "valuation_completion_required": bool(
            plan["valuation_completion_required"]
        ),
        "label_completion_required": bool(
            plan["label_completion_required"]
        ),
        "new_entry_evidence_candidate": bool(
            plan["new_entry_evidence_candidate"]
        ),
        "continuation_route": route,
        "continuation_handoff_ready": True,
        "requires_operator_review": True,
        "requires_separate_controlled_live_authorization": True,
        "requires_separate_transaction_authorization": True,
        "requires_separate_phase7_promotion_action": True,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "handoff_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_final_evidence_handoff_v2(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Bind the completed Phase 7 EXIT CLOSED/VALUED/LABELED lifecycle "
            "to a fresh read-only Phase 7 evidence status and advisory evidence "
            "plan. The handoff returns the next evidence route and never "
            "authorizes live entry, transaction submission, new capital, or "
            "Phase 7 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-post-label-audit", required=True)
    parser.add_argument("--expected-post-label-audit-sha256", required=True)
    parser.add_argument("--phase6-post-promotion-audit", required=True)
    args = parser.parse_args()

    report = build_exit_final_evidence_handoff_v2(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_post_label_audit_path=args.saved_post_label_audit,
        expected_post_label_audit_sha256=(
            args.expected_post_label_audit_sha256
        ),
        phase6_post_promotion_audit_path=args.phase6_post_promotion_audit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
