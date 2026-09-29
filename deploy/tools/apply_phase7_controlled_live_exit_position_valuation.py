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
    "PHASE7_CONTROLLED_LIVE_EXIT_POSITION_VALUATION_APPLY_V1"
)

PLANNER_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_position_valuation_plan.py"
)
REVIEWED_SOURCE_BLOBS = {
    PLANNER_TOOL: "6dfabd7bfe19a0723aff2b0606800e4515f23f32",
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
    "quote_unit",
    "max_age_seconds",
    "planned_actions",
    "applied_actions",
    "valuation",
    "valuation_reused_existing",
    "expected_valuation_target_sha256",
    "actual_valuation_target_sha256",
    "target_state_matches_plan",
    "post_outcome_label_status",
    "valuation_apply_complete",
    "valuation_remaining",
    "requires_post_valuation_audit",
    "learning_label_reconciliation_required",
    "learning_label_reconciliation_ready",
    "phase7_evidence_status_recheck_required",
    "requires_separate_phase7_promotion_action",
    "new_live_entry_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
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


def _load_planner(source: Path) -> Any:
    path = source / PLANNER_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed valuation planner is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[PLANNER_TOOL]:
        raise ValueError("reviewed valuation planner blob mismatch")
    return _load_module(
        path,
        "phase7_exit_position_valuation_apply_planner",
    )


def validate_exit_position_valuation_apply(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 EXIT valuation apply must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"apply_sha256"}:
        raise ValueError("Phase 7 EXIT valuation apply schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 EXIT valuation apply format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 EXIT valuation apply type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 EXIT valuation apply lineage mismatch")

    for field in (
        "saved_plan_sha256",
        "expected_plan_sha256",
        "fresh_plan_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "expected_valuation_target_sha256",
        "actual_valuation_target_sha256",
        "apply_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT valuation apply {field} is invalid"
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
                f"Phase 7 EXIT valuation apply {field} is invalid"
            )

    if report["saved_plan_sha256"] != report["expected_plan_sha256"]:
        raise ValueError("valuation apply saved-plan digest mismatch")
    if report["fresh_plan_sha256"] != report["saved_plan_sha256"]:
        raise ValueError("valuation apply fresh-plan digest mismatch")
    if report["actual_valuation_target_sha256"] != report[
        "expected_valuation_target_sha256"
    ]:
        raise ValueError("valuation apply target-state digest mismatch")

    for field in (
        "opened_decision_id",
        "principal_exit_decision_id",
        "settlement_decision_id",
        "pool_address",
        "position_address",
        "pio_database_path",
        "quote_unit",
        "post_outcome_label_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT valuation apply {field} is invalid"
            )

    if report["post_outcome_label_status"] != "VALUED":
        raise ValueError("valuation apply did not produce VALUED outcome")

    planned = report.get("planned_actions")
    applied = report.get("applied_actions")
    if planned != [ "VALUE_LIVE_POSITION_OUTCOME" ]:
        raise ValueError("valuation apply requires exact planned valuation action")
    if applied != planned:
        raise ValueError("valuation applied actions differ from plan")

    if not isinstance(report.get("valuation"), dict):
        raise ValueError("valuation apply valuation record is invalid")
    valuation = report["valuation"]
    if valuation.get("position_address") != report["position_address"]:
        raise ValueError("valuation apply position mismatch")
    if valuation.get("pool_address") != report["pool_address"]:
        raise ValueError("valuation apply pool mismatch")
    if valuation.get("closed_decision_id") != report[
        "settlement_decision_id"
    ]:
        raise ValueError("valuation apply settlement decision mismatch")
    if valuation.get("quote_unit") != report["quote_unit"]:
        raise ValueError("valuation apply quote unit mismatch")
    if valuation.get("max_age_seconds") != report["max_age_seconds"]:
        raise ValueError("valuation apply max-age mismatch")

    for field in (
        "target_state_matches_plan",
        "valuation_apply_complete",
        "requires_post_valuation_audit",
        "learning_label_reconciliation_required",
        "learning_label_reconciliation_ready",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT valuation apply requires {field}=true"
            )
    if report.get("valuation_remaining") is not False:
        raise ValueError("valuation apply still has remaining work")
    if not isinstance(report.get("valuation_reused_existing"), bool):
        raise ValueError("valuation apply reuse flag is invalid")
    if not isinstance(report.get("production_pio_database_modified"), bool):
        raise ValueError("valuation apply DB mutation flag is invalid")

    for field in (
        "new_live_entry_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_repository_git_mutated",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT valuation apply requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["apply_sha256"] != expected:
        raise ValueError("Phase 7 EXIT valuation apply digest mismatch")


def apply_exit_position_valuation(
    *,
    source_tree: str | Path,
    saved_plan_path: str | Path,
    expected_plan_sha256: str,
    saved_post_apply_audit_path: str | Path,
    expected_post_apply_audit_sha256: str,
    pio_database_path: str | Path,
    max_age_seconds: int = 300,
    quote_unit: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    planner = _load_planner(source)

    saved = json.loads(
        Path(saved_plan_path).resolve(strict=True).read_text(encoding="utf-8")
    )
    if not isinstance(saved, dict):
        raise ValueError("saved valuation plan must be a JSON object")
    planner.validate_exit_position_valuation_plan(saved)
    if (
        not _is_hex_digest(expected_plan_sha256, 64)
        or saved["plan_sha256"] != expected_plan_sha256
    ):
        raise ValueError("saved valuation plan digest mismatch")
    if saved.get("valuation_plan_ready") is not True:
        raise ValueError("saved valuation plan is not ready")
    if saved.get("valuation_required") is not True:
        raise ValueError("saved valuation plan requires no apply")
    if saved.get("requires_separate_valuation_apply") is not True:
        raise ValueError("saved valuation plan does not permit apply")
    if saved.get("planned_actions") != [planner.ACTION_VALUE_POSITION]:
        raise ValueError("saved valuation plan action list is invalid")

    for field in (
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_pio_database_modified",
    ):
        if saved.get(field) is not False:
            raise ValueError(
                f"saved valuation plan unexpectedly sets {field}"
            )

    fresh = planner.build_exit_position_valuation_plan(
        source_tree=source,
        saved_post_apply_audit_path=saved_post_apply_audit_path,
        expected_post_apply_audit_sha256=(
            expected_post_apply_audit_sha256
        ),
        pio_database_path=pio_database_path,
        max_age_seconds=max_age_seconds,
        quote_unit=quote_unit,
    )
    planner.validate_exit_position_valuation_plan(fresh)
    if fresh["plan_sha256"] != saved["plan_sha256"]:
        raise ValueError("fresh valuation plan differs from saved plan")

    _audit_module, runtime = planner._load_reviewed(source)
    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != saved["pio_database_path"]:
        raise ValueError("valuation Pio database path differs from plan")

    before = planner._database_state(database)
    if before["database"] != saved["pio_database_sha256_before"]:
        raise ValueError("Pio database changed after valuation planning")
    if before["wal"] != saved["pio_wal_sha256_before"]:
        raise ValueError("Pio WAL changed after valuation planning")
    if before["shm"] != saved["pio_shm_sha256_before"]:
        raise ValueError("Pio SHM changed after valuation planning")

    storage = runtime["Storage"].__new__(runtime["Storage"])
    storage.path = database
    result = runtime["value_live_position_outcome"](
        storage,
        position_address=saved["position_address"],
        max_age_seconds=saved["max_age_seconds"],
        quote_unit=saved["quote_unit"],
    )
    valuation = result.valuation.to_record()
    post = planner._valuation_state(
        database,
        position_address=saved["position_address"],
    )
    target_sha = planner._sha256_bytes(planner._canonical_bytes(post))
    if target_sha != saved["private_valuation_target_sha256"]:
        raise ValueError(
            "production valuation target differs from private replay"
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
        "principal_exit_decision_id": saved[
            "principal_exit_decision_id"
        ],
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
        "quote_unit": saved["quote_unit"],
        "max_age_seconds": saved["max_age_seconds"],
        "planned_actions": [planner.ACTION_VALUE_POSITION],
        "applied_actions": [planner.ACTION_VALUE_POSITION],
        "valuation": valuation,
        "valuation_reused_existing": bool(result.reused_existing),
        "expected_valuation_target_sha256": saved[
            "private_valuation_target_sha256"
        ],
        "actual_valuation_target_sha256": target_sha,
        "target_state_matches_plan": True,
        "post_outcome_label_status": post["outcome_label_status"],
        "valuation_apply_complete": True,
        "valuation_remaining": False,
        "requires_post_valuation_audit": True,
        "learning_label_reconciliation_required": True,
        "learning_label_reconciliation_ready": True,
        "phase7_evidence_status_recheck_required": True,
        "requires_separate_phase7_promotion_action": True,
        "new_live_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
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
    validate_exit_position_valuation_apply(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the exact reviewed Phase 7 EXIT position valuation after "
            "rebuilding the private valuation plan and proving production Pio "
            "state has not drifted. No signing, submission, new capital, or "
            "Phase 7 promotion is authorized."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-plan", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--saved-post-apply-audit", required=True)
    parser.add_argument("--expected-post-apply-audit-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    parser.add_argument("--max-age-seconds", type=int, default=300)
    parser.add_argument("--quote-unit")
    args = parser.parse_args()

    report = apply_exit_position_valuation(
        source_tree=args.source_tree,
        saved_plan_path=args.saved_plan,
        expected_plan_sha256=args.expected_plan_sha256,
        saved_post_apply_audit_path=args.saved_post_apply_audit,
        expected_post_apply_audit_sha256=(
            args.expected_post_apply_audit_sha256
        ),
        pio_database_path=args.pio_db,
        max_age_seconds=args.max_age_seconds,
        quote_unit=args.quote_unit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
