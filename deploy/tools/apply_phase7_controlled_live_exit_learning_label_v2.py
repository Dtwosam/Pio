from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_LEARNING_LABEL_APPLY_V2"

PLANNER_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_learning_label_plan_v2.py"
)
REVIEWED_SOURCE_BLOBS = {
    PLANNER_TOOL: "064f358d134a1a7646e07af1ac23cb06c67a9916",
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
    "planned_actions",
    "applied_actions",
    "learning_label",
    "learning_label_reused_existing",
    "expected_label_target_sha256",
    "actual_label_target_sha256",
    "target_state_matches_plan",
    "learning_label_apply_complete",
    "learning_label_remaining",
    "requires_post_label_audit",
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
        raise ValueError("reviewed learning-label v2 planner is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[PLANNER_TOOL]:
        raise ValueError("reviewed learning-label v2 planner blob mismatch")
    return _load_module(
        path,
        "phase7_exit_learning_label_v2_apply_planner",
    )


def validate_exit_learning_label_apply_v2(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"apply_sha256"}:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT learning-label v2 apply format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT learning-label v2 apply type"
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
            "Phase 7 EXIT learning-label v2 apply lineage mismatch"
        )

    for field in (
        "saved_plan_sha256",
        "expected_plan_sha256",
        "fresh_plan_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "expected_label_target_sha256",
        "actual_label_target_sha256",
        "apply_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT learning-label v2 apply {field} is invalid"
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
                f"Phase 7 EXIT learning-label v2 apply {field} is invalid"
            )

    if report["saved_plan_sha256"] != report["expected_plan_sha256"]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 saved-plan digest mismatch"
        )
    if report["fresh_plan_sha256"] != report["saved_plan_sha256"]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 fresh-plan digest mismatch"
        )
    if report["actual_label_target_sha256"] != report[
        "expected_label_target_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 target-state digest mismatch"
        )

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
                f"Phase 7 EXIT learning-label v2 apply {field} is invalid"
            )

    expected_action = ["BUILD_LIVE_LEARNING_LABEL"]
    if report.get("planned_actions") != expected_action:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply planned action mismatch"
        )
    if report.get("applied_actions") != expected_action:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply applied action mismatch"
        )
    if report.get("learning_label_reused_existing") is not False:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply requires new label"
        )
    if report.get("production_pio_database_modified") is not True:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply requires production DB mutation"
        )

    if not isinstance(report.get("learning_label"), dict):
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply label is invalid"
        )
    label = report["learning_label"]
    if label.get("position_address") != report["position_address"]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply position mismatch"
        )
    if label.get("decision_id") != report["opened_decision_id"]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply opening decision mismatch"
        )
    if label.get("pool_address") != report["pool_address"]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply pool mismatch"
        )
    if label.get("closed_decision_id") != report["settlement_decision_id"]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply closure mismatch"
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
                f"Phase 7 EXIT learning-label v2 apply requires {field}=true"
            )
    if report.get("learning_label_remaining") is not False:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply still has remaining work"
        )

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
                f"Phase 7 EXIT learning-label v2 apply requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["apply_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 apply digest mismatch"
        )


def apply_exit_learning_label_v2(
    *,
    source_tree: str | Path,
    saved_plan_path: str | Path,
    expected_plan_sha256: str,
    saved_post_valuation_audit_path: str | Path,
    expected_post_valuation_audit_sha256: str,
    pio_database_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    planner = _load_planner(source)

    saved = json.loads(
        Path(saved_plan_path).resolve(strict=True).read_text(encoding="utf-8")
    )
    if not isinstance(saved, dict):
        raise ValueError(
            "saved Phase 7 EXIT learning-label v2 plan must be a JSON object"
        )
    planner.validate_exit_learning_label_plan_v2(saved)
    if (
        not _is_hex_digest(expected_plan_sha256, 64)
        or saved["plan_sha256"] != expected_plan_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT learning-label v2 plan digest mismatch"
        )
    if saved.get("learning_label_plan_ready") is not True:
        raise ValueError("saved learning-label v2 plan is not ready")
    if saved.get("requires_separate_learning_label_apply") is not True:
        raise ValueError(
            "saved learning-label v2 plan does not permit apply"
        )
    if saved.get("planned_actions") != [planner.ACTION_BUILD_LABEL]:
        raise ValueError(
            "saved learning-label v2 plan action list is invalid"
        )
    if saved.get("pre_learning_label_present") is not False:
        raise ValueError(
            "saved learning-label v2 plan found preexisting label"
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
        if saved.get(field) is not False:
            raise ValueError(
                f"saved learning-label v2 plan unexpectedly sets {field}"
            )

    fresh = planner.build_exit_learning_label_plan_v2(
        source_tree=source,
        saved_post_valuation_audit_path=saved_post_valuation_audit_path,
        expected_post_valuation_audit_sha256=(
            expected_post_valuation_audit_sha256
        ),
        pio_database_path=pio_database_path,
    )
    planner.validate_exit_learning_label_plan_v2(fresh)
    if fresh["plan_sha256"] != saved["plan_sha256"]:
        raise ValueError(
            "fresh Phase 7 EXIT learning-label v2 plan differs from saved plan"
        )

    _audit_module, runtime = planner._load_reviewed(source)
    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != saved["pio_database_path"]:
        raise ValueError(
            "learning-label v2 Pio database path differs from plan"
        )
    before = planner._database_state(database)
    if before["database"] != saved["pio_database_sha256_before"]:
        raise ValueError(
            "Pio database changed after learning-label v2 planning"
        )
    if before["wal"] != saved["pio_wal_sha256_before"]:
        raise ValueError(
            "Pio WAL changed after learning-label v2 planning"
        )
    if before["shm"] != saved["pio_shm_sha256_before"]:
        raise ValueError(
            "Pio SHM changed after learning-label v2 planning"
        )

    storage = runtime["Storage"].__new__(runtime["Storage"])
    storage.path = database
    result = runtime["build_live_learning_label"](
        storage,
        position_address=saved["position_address"],
    )
    if result.reused_existing is not False:
        raise ValueError(
            "learning-label v2 apply unexpectedly reused existing label"
        )
    label = result.label.to_record()
    if label != saved["private_learning_label"]:
        raise ValueError(
            "production learning label differs from private replay"
        )

    post = planner._label_state(
        database,
        position_address=saved["position_address"],
    )
    if post["learning_label"] != label:
        raise ValueError(
            "persisted production learning label differs from apply result"
        )
    target_sha = planner._sha256_bytes(planner._canonical_bytes(post))
    if target_sha != saved["private_label_target_sha256"]:
        raise ValueError(
            "production learning-label target differs from private replay"
        )

    after = planner._database_state(database)
    if after == before:
        raise ValueError(
            "learning-label v2 apply did not modify production Pio database"
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
        "planned_actions": [planner.ACTION_BUILD_LABEL],
        "applied_actions": [planner.ACTION_BUILD_LABEL],
        "learning_label": label,
        "learning_label_reused_existing": False,
        "expected_label_target_sha256": saved[
            "private_label_target_sha256"
        ],
        "actual_label_target_sha256": target_sha,
        "target_state_matches_plan": True,
        "learning_label_apply_complete": True,
        "learning_label_remaining": False,
        "requires_post_label_audit": True,
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
        "production_pio_database_modified": True,
    }
    report = {
        **identity,
        "apply_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_learning_label_apply_v2(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the exact reviewed Phase 7 EXIT learning label after "
            "rebuilding the private v2 plan and proving production Pio state "
            "has not drifted. No trading, retry, new capital, or promotion is "
            "authorized by this mutation."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-plan", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--saved-post-valuation-audit", required=True)
    parser.add_argument("--expected-post-valuation-audit-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    args = parser.parse_args()

    report = apply_exit_learning_label_v2(
        source_tree=args.source_tree,
        saved_plan_path=args.saved_plan,
        expected_plan_sha256=args.expected_plan_sha256,
        saved_post_valuation_audit_path=args.saved_post_valuation_audit,
        expected_post_valuation_audit_sha256=(
            args.expected_post_valuation_audit_sha256
        ),
        pio_database_path=args.pio_db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
