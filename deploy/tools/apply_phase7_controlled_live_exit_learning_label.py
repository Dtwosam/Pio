from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_LEARNING_LABEL_APPLY_V1"

PLAN_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_learning_label_plan.py"
)
REVIEWED_SOURCE_BLOBS = {
    PLAN_TOOL: "87f869434768cfdb42ccd23fbb8f67552ed3afcb",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_plan_sha256",
    "expected_plan_sha256",
    "fresh_plan_sha256",
    "opened_decision_id",
    "principal_exit_decision_id",
    "settlement_decision_id",
    "pool_address",
    "position_address",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "max_quote_age_seconds",
    "valuation",
    "learning_label",
    "valuation_reused_existing",
    "learning_label_reused_existing",
    "expected_target_state_sha256",
    "actual_target_state_sha256",
    "target_state_matches_plan",
    "learning_label_apply_complete",
    "learning_label_reconciliation_remaining",
    "requires_post_label_audit",
    "phase7_evidence_status_recheck_required",
    "requires_separate_phase7_promotion_action",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_entry_authorized",
    "new_live_capital_authorized",
    "phase7_promotion_authorized",
    "phase7_promotion_persisted",
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


def _load_plan(source: Path) -> Any:
    path = source / PLAN_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 7 EXIT learning-label plan is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[PLAN_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT learning-label plan blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_learning_label_apply_plan",
    )


def validate_exit_learning_label_apply(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 EXIT learning-label apply must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"apply_sha256"}:
        raise ValueError("Phase 7 EXIT learning-label apply schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 EXIT learning-label apply format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 EXIT learning-label apply type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 EXIT learning-label apply lineage mismatch")

    for field in (
        "saved_plan_sha256",
        "expected_plan_sha256",
        "fresh_plan_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "expected_target_state_sha256",
        "actual_target_state_sha256",
        "apply_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT learning-label apply {field} is invalid"
            )
    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 7 EXIT learning-label apply {field} is invalid"
            )

    if report["saved_plan_sha256"] != report["expected_plan_sha256"]:
        raise ValueError("learning-label saved-plan digest mismatch")
    if report["fresh_plan_sha256"] != report["saved_plan_sha256"]:
        raise ValueError("learning-label fresh-plan digest mismatch")
    if report["actual_target_state_sha256"] != report[
        "expected_target_state_sha256"
    ]:
        raise ValueError("learning-label target-state digest mismatch")

    for field in (
        "opened_decision_id",
        "principal_exit_decision_id",
        "settlement_decision_id",
        "pool_address",
        "position_address",
        "pio_database_path",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT learning-label apply {field} is invalid"
            )
    age = report.get("max_quote_age_seconds")
    if not isinstance(age, int) or isinstance(age, bool) or age < 0:
        raise ValueError("learning-label max quote age is invalid")

    valuation = report.get("valuation")
    label = report.get("learning_label")
    if not isinstance(valuation, dict) or not isinstance(label, dict):
        raise ValueError("learning-label applied records are invalid")
    if valuation.get("position_address") != report["position_address"]:
        raise ValueError("learning-label valuation position mismatch")
    if valuation.get("closed_decision_id") != report[
        "settlement_decision_id"
    ]:
        raise ValueError("learning-label valuation settlement decision mismatch")
    if label.get("position_address") != report["position_address"]:
        raise ValueError("learning-label position mismatch")
    if label.get("decision_id") != report["opened_decision_id"]:
        raise ValueError("learning-label opening decision mismatch")
    if label.get("closed_decision_id") != report["settlement_decision_id"]:
        raise ValueError("learning-label closed decision mismatch")
    if label.get("realized_pnl_quote") != valuation.get("realized_pnl_quote"):
        raise ValueError("learning-label PnL differs from valuation")
    if label.get("realized_return_bps") != valuation.get(
        "realized_return_bps"
    ):
        raise ValueError("learning-label return differs from valuation")

    for field in (
        "valuation_reused_existing",
        "learning_label_reused_existing",
        "production_pio_database_modified",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(
                f"Phase 7 EXIT learning-label apply {field} is invalid"
            )

    for field in (
        "target_state_matches_plan",
        "learning_label_apply_complete",
        "requires_post_label_audit",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT learning-label apply requires {field}=true"
            )
    if report.get("learning_label_reconciliation_remaining") is not False:
        raise ValueError("learning-label reconciliation is still incomplete")

    for field in (
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_repository_git_mutated",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT learning-label apply requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["apply_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError("Phase 7 EXIT learning-label apply digest mismatch")


def apply_exit_learning_label(
    *,
    source_tree: str | Path,
    saved_plan_path: str | Path,
    expected_plan_sha256: str,
    saved_post_reconciliation_audit_path: str | Path,
    expected_post_reconciliation_audit_sha256: str,
    pio_database_path: str | Path,
    max_quote_age_seconds: int = 300,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    planner = _load_plan(source)

    saved = planner._load_json(
        saved_plan_path,
        label="saved Phase 7 EXIT learning-label plan",
    )
    planner.validate_exit_learning_label_plan(saved)
    if (
        not _is_hex_digest(expected_plan_sha256, 64)
        or saved["plan_sha256"] != expected_plan_sha256
    ):
        raise ValueError("saved Phase 7 EXIT learning-label plan digest mismatch")
    if saved.get("learning_label_plan_ready") is not True:
        raise ValueError("saved learning-label plan is not ready")
    if saved.get("learning_label_reconciliation_required") is not True:
        raise ValueError("saved learning-label plan requires no production apply")
    if saved.get("requires_separate_learning_label_apply") is not True:
        raise ValueError("saved learning-label plan does not permit apply")

    fresh = planner.build_exit_learning_label_plan(
        source_tree=source,
        saved_post_reconciliation_audit_path=(
            saved_post_reconciliation_audit_path
        ),
        expected_post_reconciliation_audit_sha256=(
            expected_post_reconciliation_audit_sha256
        ),
        pio_database_path=pio_database_path,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    planner.validate_exit_learning_label_plan(fresh)
    if fresh["plan_sha256"] != saved["plan_sha256"]:
        raise ValueError(
            "fresh Phase 7 EXIT learning-label plan differs from saved plan"
        )

    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != saved["pio_database_path"]:
        raise ValueError("learning-label production database path changed")
    before = planner._database_state(database)
    if before["database"] != saved["pio_database_sha256_before"]:
        raise ValueError("Pio database changed after learning-label planning")
    if before["wal"] != saved["pio_wal_sha256_before"]:
        raise ValueError("Pio WAL changed after learning-label planning")
    if before["shm"] != saved["pio_shm_sha256_before"]:
        raise ValueError("Pio SHM changed after learning-label planning")

    _audit_module, runtime = planner._load_reviewed(source)
    storage_type = runtime["Storage"]
    storage = storage_type.__new__(storage_type)
    storage.path = database

    valuation_result = runtime["value_live_position_outcome"](
        storage,
        position_address=saved["position_address"],
        max_age_seconds=saved["max_quote_age_seconds"],
    )
    valuation = valuation_result.valuation.to_record()
    if valuation != saved["private_valuation"]:
        raise ValueError(
            "production valuation differs from reviewed private replay"
        )

    label_result = runtime["build_live_learning_label"](
        storage,
        position_address=saved["position_address"],
    )
    label = label_result.label.to_record()
    if label != saved["private_learning_label"]:
        raise ValueError(
            "production learning label differs from reviewed private replay"
        )

    target = planner._target_state(database, saved["position_address"])
    actual_target_sha = _sha256_bytes(planner._canonical_bytes(target))
    if actual_target_sha != saved["private_target_state_sha256"]:
        raise ValueError(
            "production learning-label target differs from private replay"
        )

    after = planner._database_state(database)
    modified = after != before

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
        "saved_plan_sha256": saved["plan_sha256"],
        "expected_plan_sha256": expected_plan_sha256,
        "fresh_plan_sha256": fresh["plan_sha256"],
        "opened_decision_id": saved["opened_decision_id"],
        "principal_exit_decision_id": saved["principal_exit_decision_id"],
        "settlement_decision_id": saved["settlement_decision_id"],
        "pool_address": saved["pool_address"],
        "position_address": saved["position_address"],
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "max_quote_age_seconds": saved["max_quote_age_seconds"],
        "valuation": valuation,
        "learning_label": label,
        "valuation_reused_existing": bool(
            valuation_result.reused_existing
        ),
        "learning_label_reused_existing": bool(
            label_result.reused_existing
        ),
        "expected_target_state_sha256": saved[
            "private_target_state_sha256"
        ],
        "actual_target_state_sha256": actual_target_sha,
        "target_state_matches_plan": True,
        "learning_label_apply_complete": True,
        "learning_label_reconciliation_remaining": False,
        "requires_post_label_audit": True,
        "phase7_evidence_status_recheck_required": True,
        "requires_separate_phase7_promotion_action": True,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": modified,
    }
    report = {
        **identity,
        "apply_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_learning_label_apply(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the reviewed Phase 7 EXIT valuation and learning label only "
            "after rebuilding the same private plan and proving the production "
            "database has not changed. This tool never signs, submits, retries, "
            "adds live capital, or promotes Phase 7."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-plan", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--saved-post-reconciliation-audit", required=True)
    parser.add_argument(
        "--expected-post-reconciliation-audit-sha256",
        required=True,
    )
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    parser.add_argument("--max-quote-age-seconds", type=int, default=300)
    args = parser.parse_args()

    report = apply_exit_learning_label(
        source_tree=args.source_tree,
        saved_plan_path=args.saved_plan,
        expected_plan_sha256=args.expected_plan_sha256,
        saved_post_reconciliation_audit_path=(
            args.saved_post_reconciliation_audit
        ),
        expected_post_reconciliation_audit_sha256=(
            args.expected_post_reconciliation_audit_sha256
        ),
        pio_database_path=args.pio_db,
        max_quote_age_seconds=args.max_quote_age_seconds,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
