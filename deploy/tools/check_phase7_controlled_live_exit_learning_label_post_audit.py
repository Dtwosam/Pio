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
    "PHASE7_CONTROLLED_LIVE_EXIT_LEARNING_LABEL_POST_AUDIT_V1"
)

APPLY_TOOL = Path(
    "deploy/tools/apply_phase7_controlled_live_exit_learning_label.py"
)
REVIEWED_SOURCE_BLOBS = {
    APPLY_TOOL: "c3f03268827eead26936c382183bf8538cc681ef",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_apply_sha256",
    "expected_apply_sha256",
    "saved_plan_sha256",
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
    "expected_target_state_sha256",
    "actual_target_state_sha256",
    "target_state_matches_apply",
    "outcome_label_status",
    "valuation",
    "learning_label",
    "valuation_matches_apply",
    "learning_label_matches_apply",
    "learning_label_post_audit_ready",
    "phase7_evidence_status_recheck_required",
    "requires_separate_phase7_promotion_action",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_entry_authorized",
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


def _load_apply(source: Path) -> Any:
    path = source / APPLY_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 7 EXIT learning-label apply tool is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[APPLY_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT learning-label apply blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_learning_label_post_audit_apply",
    )


def validate_exit_learning_label_post_audit(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT learning-label post-audit must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"audit_sha256"}:
        raise ValueError(
            "Phase 7 EXIT learning-label post-audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT learning-label post-audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT learning-label post-audit type"
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
            "Phase 7 EXIT learning-label post-audit lineage mismatch"
        )

    for field in (
        "saved_apply_sha256",
        "expected_apply_sha256",
        "saved_plan_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "expected_target_state_sha256",
        "actual_target_state_sha256",
        "audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT learning-label post-audit {field} is invalid"
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
                f"Phase 7 EXIT learning-label post-audit {field} is invalid"
            )

    if report["saved_apply_sha256"] != report["expected_apply_sha256"]:
        raise ValueError("learning-label apply digest mismatch")
    if report["actual_target_state_sha256"] != report[
        "expected_target_state_sha256"
    ]:
        raise ValueError("learning-label post-audit target digest mismatch")
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError("Pio database changed during learning-label audit")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("Pio WAL changed during learning-label audit")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("Pio SHM changed during learning-label audit")

    for field in (
        "opened_decision_id",
        "principal_exit_decision_id",
        "settlement_decision_id",
        "pool_address",
        "position_address",
        "pio_database_path",
        "outcome_label_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT learning-label post-audit {field} is invalid"
            )
    if report["outcome_label_status"] != "VALUED":
        raise ValueError("closed position outcome is not VALUED")

    valuation = report.get("valuation")
    label = report.get("learning_label")
    if not isinstance(valuation, dict) or not isinstance(label, dict):
        raise ValueError("learning-label post-audit records are invalid")
    if valuation.get("position_address") != report["position_address"]:
        raise ValueError("post-audit valuation position mismatch")
    if label.get("position_address") != report["position_address"]:
        raise ValueError("post-audit learning-label position mismatch")
    if label.get("decision_id") != report["opened_decision_id"]:
        raise ValueError("post-audit learning-label opening decision mismatch")
    if label.get("closed_decision_id") != report["settlement_decision_id"]:
        raise ValueError("post-audit learning-label closing decision mismatch")
    if valuation.get("closed_decision_id") != report["settlement_decision_id"]:
        raise ValueError("post-audit valuation closing decision mismatch")

    for field in (
        "target_state_matches_apply",
        "valuation_matches_apply",
        "learning_label_matches_apply",
        "learning_label_post_audit_ready",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT learning-label post-audit requires {field}=true"
            )

    for field in (
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT learning-label post-audit requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["audit_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError(
            "Phase 7 EXIT learning-label post-audit digest mismatch"
        )


def build_exit_learning_label_post_audit(
    *,
    source_tree: str | Path,
    saved_apply_path: str | Path,
    expected_apply_sha256: str,
    pio_database_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    apply_module = _load_apply(source)
    planner = apply_module._load_plan(source)

    applied = planner._load_json(
        saved_apply_path,
        label="saved Phase 7 EXIT learning-label apply",
    )
    apply_module.validate_exit_learning_label_apply(applied)
    if (
        not _is_hex_digest(expected_apply_sha256, 64)
        or applied["apply_sha256"] != expected_apply_sha256
    ):
        raise ValueError("saved learning-label apply digest mismatch")

    for field in (
        "target_state_matches_plan",
        "learning_label_apply_complete",
        "requires_post_label_audit",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
    ):
        if applied.get(field) is not True:
            raise ValueError(
                f"saved learning-label apply lost required {field}"
            )
    if applied.get("learning_label_reconciliation_remaining") is not False:
        raise ValueError("saved learning-label apply still has remaining work")

    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != applied["pio_database_path"]:
        raise ValueError(
            "learning-label audit database differs from apply artifact"
        )
    before = planner._database_state(database)
    if before["database"] != applied["pio_database_sha256_after"]:
        raise ValueError("Pio database changed after learning-label apply")
    if before["wal"] != applied["pio_wal_sha256_after"]:
        raise ValueError("Pio WAL changed after learning-label apply")
    if before["shm"] != applied["pio_shm_sha256_after"]:
        raise ValueError("Pio SHM changed after learning-label apply")

    target = planner._target_state(database, applied["position_address"])
    actual_target_sha = _sha256_bytes(planner._canonical_bytes(target))
    if actual_target_sha != applied["expected_target_state_sha256"]:
        raise ValueError(
            "production learning-label target differs from apply artifact"
        )

    outcome = target.get("outcome")
    valuation_row = target.get("valuation")
    label_row = target.get("learning_label")
    if (
        not isinstance(outcome, dict)
        or not isinstance(valuation_row, dict)
        or not isinstance(label_row, dict)
    ):
        raise ValueError(
            "learning-label post-audit target rows are incomplete"
        )
    if outcome.get("label_status") != "VALUED":
        raise ValueError("learning-label post-audit outcome is not VALUED")

    try:
        valuation = json.loads(str(valuation_row["raw_json"]))
        label = json.loads(str(label_row["raw_json"]))
    except (KeyError, json.JSONDecodeError) as exc:
        raise ValueError(
            "learning-label post-audit raw_json is invalid"
        ) from exc
    if not isinstance(valuation, dict) or not isinstance(label, dict):
        raise ValueError("learning-label post-audit raw records are invalid")
    if valuation != applied["valuation"]:
        raise ValueError("stored valuation differs from apply artifact")
    if label != applied["learning_label"]:
        raise ValueError("stored learning label differs from apply artifact")

    after = planner._database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during learning-label audit"
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
        "saved_apply_sha256": applied["apply_sha256"],
        "expected_apply_sha256": expected_apply_sha256,
        "saved_plan_sha256": applied["saved_plan_sha256"],
        "opened_decision_id": applied["opened_decision_id"],
        "principal_exit_decision_id": applied["principal_exit_decision_id"],
        "settlement_decision_id": applied["settlement_decision_id"],
        "pool_address": applied["pool_address"],
        "position_address": applied["position_address"],
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "expected_target_state_sha256": applied[
            "expected_target_state_sha256"
        ],
        "actual_target_state_sha256": actual_target_sha,
        "target_state_matches_apply": True,
        "outcome_label_status": str(outcome["label_status"]),
        "valuation": valuation,
        "learning_label": label,
        "valuation_matches_apply": True,
        "learning_label_matches_apply": True,
        "learning_label_post_audit_ready": True,
        "phase7_evidence_status_recheck_required": True,
        "requires_separate_phase7_promotion_action": True,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "audit_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_learning_label_post_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of the reviewed Phase 7 EXIT valuation and "
            "learning-label apply. This tool verifies exact persisted rows and "
            "requires the next step to be a fresh Phase 7 evidence-status "
            "evaluation; it never authorizes promotion."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-apply", required=True)
    parser.add_argument("--expected-apply-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    args = parser.parse_args()

    report = build_exit_learning_label_post_audit(
        source_tree=args.source_tree,
        saved_apply_path=args.saved_apply,
        expected_apply_sha256=args.expected_apply_sha256,
        pio_database_path=args.pio_db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
