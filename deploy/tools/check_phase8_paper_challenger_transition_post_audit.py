from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAPER_CHALLENGER_TRANSITION_POST_EXECUTION_AUDIT_V1"
)

EXECUTOR_TOOL = Path(
    "deploy/tools/run_phase8_paper_challenger_transition_once.py"
)
REVIEWED_SOURCE_BLOBS = {
    EXECUTOR_TOOL: "a3ede90d408065ccb3ed2faa6798b662dc46b20a",
}

ROUTE_PAPER_EVIDENCE_REVIEW = "PHASE8_PAPER_CHALLENGER_EVIDENCE_REVIEW"
NEXT_DEBT_TYPE = "PAPER_CHALLENGER_EVIDENCE_REQUIRED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "execution_receipt_sha256",
    "production_repository",
    "pio_database_path",
    "receipt_database_sha256_after",
    "receipt_wal_sha256_after",
    "receipt_shm_sha256_after",
    "audit_database_sha256_before",
    "audit_database_sha256_after",
    "audit_wal_sha256_before",
    "audit_wal_sha256_after",
    "audit_shm_sha256_before",
    "audit_shm_sha256_after",
    "receipt_research_artifacts_after",
    "audit_research_artifacts_before",
    "audit_research_artifacts_after",
    "production_database_matches_receipt",
    "production_database_unchanged_by_audit",
    "production_research_artifacts_match_receipt",
    "production_research_artifacts_unchanged_by_audit",
    "model_id",
    "active_cycle_id",
    "receipt_model_status_after",
    "receipt_cycle_status_after",
    "fresh_phase8_evidence_status",
    "fresh_phase8_evidence_status_sha256",
    "fresh_phase8_evidence_plan",
    "fresh_phase8_evidence_plan_sha256",
    "fresh_phase8_operator_handoff",
    "fresh_phase8_operator_handoff_sha256",
    "fresh_model_status",
    "fresh_cycle_status",
    "next_debt_type",
    "next_scope",
    "continuation_route",
    "transition_receipt_valid",
    "model_transition_verified",
    "cycle_sync_verified",
    "model_cycle_binding_verified",
    "phase7_dependency_satisfied",
    "phase8_research_only",
    "phase8_read_only",
    "phase8_policy_actionable",
    "phase8_execution_wired",
    "post_transition_audit_ready",
    "paper_challenger_active",
    "paper_account_required",
    "challenger_closed_trade_evidence_required",
    "incumbent_closed_trade_evidence_required",
    "requires_manual_paper_evidence_inputs",
    "requires_separate_paper_evidence_action",
    "paper_evidence_collection_authorized",
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
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified_by_audit",
    "production_research_artifacts_modified_by_audit",
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


def _load_executor(source: Path) -> Any:
    path = source / EXECUTOR_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 8 PAPER transition executor is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[EXECUTOR_TOOL]:
        raise ValueError(
            "reviewed Phase 8 PAPER transition executor blob mismatch"
        )
    return _load_module(
        path,
        "phase8_paper_transition_post_audit_executor",
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


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ValueError(
            "Phase 8 PAPER transition audit runtime value is invalid"
        )
    return result


def _snapshot_sqlite(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def validate_phase8_paper_challenger_transition_post_audit(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 8 PAPER transition post-audit must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"post_audit_sha256"}:
        raise ValueError(
            "Phase 8 PAPER transition post-audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 8 PAPER transition post-audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 8 PAPER transition post-audit type"
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
            "Phase 8 PAPER transition post-audit lineage mismatch"
        )

    for field in (
        "execution_receipt_sha256",
        "receipt_database_sha256_after",
        "audit_database_sha256_before",
        "audit_database_sha256_after",
        "fresh_phase8_evidence_status_sha256",
        "fresh_phase8_evidence_plan_sha256",
        "fresh_phase8_operator_handoff_sha256",
        "post_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 8 PAPER transition post-audit {field} is invalid"
            )

    for field in (
        "receipt_wal_sha256_after",
        "receipt_shm_sha256_after",
        "audit_wal_sha256_before",
        "audit_wal_sha256_after",
        "audit_shm_sha256_before",
        "audit_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 8 PAPER transition post-audit {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "model_id",
        "active_cycle_id",
        "receipt_model_status_after",
        "receipt_cycle_status_after",
        "fresh_model_status",
        "fresh_cycle_status",
        "next_debt_type",
        "next_scope",
        "continuation_route",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 8 PAPER transition post-audit {field} is invalid"
            )

    for field in (
        "receipt_research_artifacts_after",
        "audit_research_artifacts_before",
        "audit_research_artifacts_after",
    ):
        if not isinstance(report.get(field), list):
            raise ValueError(
                f"Phase 8 PAPER transition post-audit {field} is invalid"
            )

    if report["receipt_model_status_after"] != "PAPER_CHALLENGER":
        raise ValueError(
            "Phase 8 PAPER transition receipt model status mismatch"
        )
    if report["receipt_cycle_status_after"] != "PAPER_CHALLENGER":
        raise ValueError(
            "Phase 8 PAPER transition receipt cycle status mismatch"
        )
    if report["fresh_model_status"] != "PAPER_CHALLENGER":
        raise ValueError(
            "Phase 8 PAPER transition fresh model status mismatch"
        )
    if report["fresh_cycle_status"] != "PAPER_CHALLENGER":
        raise ValueError(
            "Phase 8 PAPER transition fresh cycle status mismatch"
        )
    if report["next_debt_type"] != NEXT_DEBT_TYPE:
        raise ValueError(
            "Phase 8 PAPER transition post-audit next debt mismatch"
        )
    if report["next_scope"] != report["model_id"]:
        raise ValueError(
            "Phase 8 PAPER transition post-audit next scope mismatch"
        )
    if report["continuation_route"] != ROUTE_PAPER_EVIDENCE_REVIEW:
        raise ValueError(
            "Phase 8 PAPER transition post-audit route mismatch"
        )

    for field in (
        "production_database_matches_receipt",
        "production_database_unchanged_by_audit",
        "production_research_artifacts_match_receipt",
        "production_research_artifacts_unchanged_by_audit",
        "transition_receipt_valid",
        "model_transition_verified",
        "cycle_sync_verified",
        "model_cycle_binding_verified",
        "phase7_dependency_satisfied",
        "phase8_research_only",
        "phase8_read_only",
        "post_transition_audit_ready",
        "paper_challenger_active",
        "paper_account_required",
        "challenger_closed_trade_evidence_required",
        "incumbent_closed_trade_evidence_required",
        "requires_manual_paper_evidence_inputs",
        "requires_separate_paper_evidence_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                "Phase 8 PAPER transition post-audit requires "
                f"{field}=true"
            )

    for field in (
        "phase8_policy_actionable",
        "phase8_execution_wired",
        "paper_evidence_collection_authorized",
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
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified_by_audit",
        "production_research_artifacts_modified_by_audit",
    ):
        if report.get(field) is not False:
            raise ValueError(
                "Phase 8 PAPER transition post-audit requires "
                f"{field}=false"
            )

    if report["audit_database_sha256_before"] != report[
        "audit_database_sha256_after"
    ]:
        raise ValueError(
            "Phase 8 PAPER transition post-audit modified the database"
        )
    if report["audit_wal_sha256_before"] != report[
        "audit_wal_sha256_after"
    ]:
        raise ValueError(
            "Phase 8 PAPER transition post-audit modified the WAL"
        )
    if report["audit_shm_sha256_before"] != report[
        "audit_shm_sha256_after"
    ]:
        raise ValueError(
            "Phase 8 PAPER transition post-audit modified the SHM"
        )
    if report["audit_research_artifacts_before"] != report[
        "audit_research_artifacts_after"
    ]:
        raise ValueError(
            "Phase 8 PAPER transition post-audit modified research artifacts"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["post_audit_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 8 PAPER transition post-audit digest mismatch"
        )


def build_phase8_paper_challenger_transition_post_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    execution_receipt_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    executor = _load_executor(source)
    receipt = _load_json(
        execution_receipt_path,
        label="Phase 8 PAPER transition execution receipt",
    )
    executor.validate_phase8_paper_challenger_transition_execution_receipt(
        receipt
    )

    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError(
            "Phase 8 PAPER transition receipt repository mismatch"
        )
    database = executor._production_database(production)
    if Path(str(receipt["pio_database_path"])).resolve() != database:
        raise ValueError(
            "Phase 8 PAPER transition receipt database mismatch"
        )

    readiness = executor._load_readiness_module(source)
    audit_module, _, _ = readiness._load_reviewed(source)
    _, _, runtime = audit_module._load_reviewed(source)

    before = executor._database_state(database)
    expected_after = {
        "database": receipt["pio_database_sha256_after"],
        "wal": receipt["pio_wal_sha256_after"],
        "shm": receipt["pio_shm_sha256_after"],
    }
    if before != expected_after:
        raise ValueError(
            "production Pio database changed after PAPER transition receipt"
        )

    artifacts_before = executor._research_artifacts(
        source,
        readiness,
        database.parent,
    )
    if artifacts_before != receipt["research_artifacts_after"]:
        raise ValueError(
            "production Phase 8 research artifacts changed after "
            "PAPER transition receipt"
        )

    with tempfile.TemporaryDirectory(
        prefix="pio-phase8-paper-transition-post-audit-"
    ) as tmp:
        private_db = Path(tmp) / "pio.db"
        _snapshot_sqlite(database, private_db)
        storage = runtime["Storage"](private_db)
        status = _record(
            runtime["evaluate_phase8_evidence_status"](storage)
        )
        plan = _record(
            runtime["build_phase8_evidence_plan"](storage)
        )
        handoff = _record(
            runtime["build_phase8_operator_handoff"](storage)
        )

    after = executor._database_state(database)
    artifacts_after = executor._research_artifacts(
        source,
        readiness,
        database.parent,
    )
    if after != before:
        raise ValueError(
            "production Pio database changed during PAPER transition audit"
        )
    if artifacts_after != artifacts_before:
        raise ValueError(
            "production research artifacts changed during PAPER transition audit"
        )

    model_id = str(receipt["model_id"])
    cycle_id = str(receipt["active_cycle_id"])
    fresh_model_status = status.get("active_cycle_challenger_status")
    fresh_cycle_status = status.get("active_cycle_status")
    if status.get("active_cycle_id") != cycle_id:
        raise ValueError(
            "fresh Phase 8 active cycle differs from transition receipt"
        )
    if status.get("active_cycle_challenger_model_id") != model_id:
        raise ValueError(
            "fresh Phase 8 challenger differs from transition receipt"
        )
    if fresh_model_status != "PAPER_CHALLENGER":
        raise ValueError(
            "fresh Phase 8 challenger is not PAPER_CHALLENGER"
        )
    if fresh_cycle_status != "PAPER_CHALLENGER":
        raise ValueError(
            "fresh Phase 8 cycle is not PAPER_CHALLENGER"
        )
    if status.get("phase7_promoted") is not True:
        raise ValueError(
            "Phase 8 PAPER transition lost its Phase 7 dependency"
        )

    next_action = plan.get("next_action")
    if not isinstance(next_action, dict):
        raise ValueError(
            "Phase 8 PAPER transition plan has no next action"
        )
    if next_action.get("debt_type") != NEXT_DEBT_TYPE:
        raise ValueError(
            "Phase 8 PAPER transition did not route to evidence collection"
        )
    if next_action.get("scope") != model_id:
        raise ValueError(
            "Phase 8 PAPER transition evidence scope changed"
        )
    if next_action.get("operator_required") is not True:
        raise ValueError(
            "Phase 8 PAPER evidence action must remain operator-gated"
        )

    if handoff.get("status") != "MANUAL_REQUIRED":
        raise ValueError(
            "Phase 8 PAPER evidence handoff is not manual-required"
        )
    if handoff.get("automatic_action_available") is not False:
        raise ValueError(
            "Phase 8 PAPER evidence collection must not be automatic"
        )
    if handoff.get("operator_action_required") is not True:
        raise ValueError(
            "Phase 8 PAPER evidence collection must require an operator"
        )
    if handoff.get("manual_input_required") is not False:
        raise ValueError(
            "Phase 8 PAPER evidence handoff manual-input state changed"
        )
    if handoff.get("suggested_command") is not None:
        raise ValueError(
            "Phase 8 PAPER evidence handoff must not invent a command"
        )
    if (
        handoff.get("debt_type") != NEXT_DEBT_TYPE
        or handoff.get("scope") != model_id
    ):
        raise ValueError(
            "Phase 8 PAPER evidence handoff scope changed"
        )

    for value, label in (
        (status, "status"),
        (plan, "plan"),
        (handoff, "handoff"),
    ):
        if value.get("research_only") is not True:
            raise ValueError(
                f"Phase 8 PAPER transition fresh {label} is not research-only"
            )
        if value.get("policy_actionable") is not False:
            raise ValueError(
                f"Phase 8 PAPER transition fresh {label} became policy-actionable"
            )
        if value.get("execution_wired") is not False:
            raise ValueError(
                f"Phase 8 PAPER transition fresh {label} became execution-wired"
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
        "execution_receipt_sha256": receipt["receipt_sha256"],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "receipt_database_sha256_after": receipt[
            "pio_database_sha256_after"
        ],
        "receipt_wal_sha256_after": receipt["pio_wal_sha256_after"],
        "receipt_shm_sha256_after": receipt["pio_shm_sha256_after"],
        "audit_database_sha256_before": before["database"],
        "audit_database_sha256_after": after["database"],
        "audit_wal_sha256_before": before["wal"],
        "audit_wal_sha256_after": after["wal"],
        "audit_shm_sha256_before": before["shm"],
        "audit_shm_sha256_after": after["shm"],
        "receipt_research_artifacts_after": receipt[
            "research_artifacts_after"
        ],
        "audit_research_artifacts_before": artifacts_before,
        "audit_research_artifacts_after": artifacts_after,
        "production_database_matches_receipt": True,
        "production_database_unchanged_by_audit": True,
        "production_research_artifacts_match_receipt": True,
        "production_research_artifacts_unchanged_by_audit": True,
        "model_id": model_id,
        "active_cycle_id": cycle_id,
        "receipt_model_status_after": receipt["model_status_after"],
        "receipt_cycle_status_after": receipt["cycle_status_after"],
        "fresh_phase8_evidence_status": status,
        "fresh_phase8_evidence_status_sha256": _sha256_bytes(
            _canonical_bytes(status)
        ),
        "fresh_phase8_evidence_plan": plan,
        "fresh_phase8_evidence_plan_sha256": _sha256_bytes(
            _canonical_bytes(plan)
        ),
        "fresh_phase8_operator_handoff": handoff,
        "fresh_phase8_operator_handoff_sha256": _sha256_bytes(
            _canonical_bytes(handoff)
        ),
        "fresh_model_status": str(fresh_model_status),
        "fresh_cycle_status": str(fresh_cycle_status),
        "next_debt_type": NEXT_DEBT_TYPE,
        "next_scope": model_id,
        "continuation_route": ROUTE_PAPER_EVIDENCE_REVIEW,
        "transition_receipt_valid": True,
        "model_transition_verified": True,
        "cycle_sync_verified": True,
        "model_cycle_binding_verified": True,
        "phase7_dependency_satisfied": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "post_transition_audit_ready": True,
        "paper_challenger_active": True,
        "paper_account_required": True,
        "challenger_closed_trade_evidence_required": True,
        "incumbent_closed_trade_evidence_required": True,
        "requires_manual_paper_evidence_inputs": True,
        "requires_separate_paper_evidence_action": True,
        "paper_evidence_collection_authorized": False,
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
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified_by_audit": False,
        "production_research_artifacts_modified_by_audit": False,
    }
    report = {
        **identity,
        "post_audit_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paper_challenger_transition_post_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit one completed Phase 8 PAPER_CHALLENGER state transition "
            "without mutating production. The audit proves model/cycle sync "
            "and routes the next step to explicit PAPER evidence review. It "
            "does not start paper trades, choose a PAPER account, use live "
            "capital, submit transactions, or authorize Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--execution-receipt", required=True)
    args = parser.parse_args()

    report = build_phase8_paper_challenger_transition_post_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        execution_receipt_path=args.execution_receipt,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
