from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_POST_LEARNING_LABEL_AUDIT_V2"

LABEL_APPLY_TOOL = Path(
    "deploy/tools/apply_phase7_controlled_live_exit_learning_label_v2.py"
)
REVIEWED_SOURCE_BLOBS = {
    LABEL_APPLY_TOOL: "f6532adb1bd34a564a8f3c330bcbd82fcb27f3fa",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_learning_label_apply_sha256",
    "expected_learning_label_apply_sha256",
    "saved_learning_label_plan_sha256",
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
    "production_source_stable_during_audit",
    "position_status",
    "valuation_present",
    "learning_label_present",
    "persisted_learning_label_matches_apply",
    "learning_label",
    "learning_label_reconciliation_complete",
    "phase7_evidence_status_recheck_required",
    "phase7_evidence_status_recheck_ready",
    "requires_separate_phase7_promotion_action",
    "post_label_audit_ready",
    "new_live_entry_authorized",
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


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    path = source / LABEL_APPLY_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 7 EXIT learning-label v2 apply is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[LABEL_APPLY_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT learning-label v2 apply blob mismatch"
        )
    apply_module = _load_module(
        path,
        "phase7_exit_post_label_apply_v2",
    )
    planner = apply_module._load_planner(source)
    return apply_module, planner


def validate_exit_post_learning_label_audit_v2(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT post-label v2 audit must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"audit_sha256"}:
        raise ValueError(
            "Phase 7 EXIT post-label v2 audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT post-label v2 audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT post-label v2 audit type"
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
            "Phase 7 EXIT post-label v2 audit lineage mismatch"
        )

    for field in (
        "saved_learning_label_apply_sha256",
        "expected_learning_label_apply_sha256",
        "saved_learning_label_plan_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT post-label v2 audit {field} is invalid"
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
                f"Phase 7 EXIT post-label v2 audit {field} is invalid"
            )

    if report["saved_learning_label_apply_sha256"] != report[
        "expected_learning_label_apply_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT post-label v2 apply digest mismatch"
        )
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "production Pio database changed during post-label audit"
        )
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError(
            "production Pio WAL changed during post-label audit"
        )
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError(
            "production Pio SHM changed during post-label audit"
        )

    for field in (
        "opened_decision_id",
        "principal_exit_decision_id",
        "settlement_decision_id",
        "pool_address",
        "position_address",
        "pio_database_path",
        "position_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT post-label v2 audit {field} is invalid"
            )
    if report["position_status"] != "CLOSED":
        raise ValueError(
            "Phase 7 EXIT post-label v2 audit requires CLOSED position"
        )

    if not isinstance(report.get("learning_label"), dict):
        raise ValueError(
            "Phase 7 EXIT post-label v2 audit learning label is invalid"
        )
    label = report["learning_label"]
    if label.get("position_address") != report["position_address"]:
        raise ValueError(
            "Phase 7 EXIT post-label v2 position mismatch"
        )
    if label.get("decision_id") != report["opened_decision_id"]:
        raise ValueError(
            "Phase 7 EXIT post-label v2 opening decision mismatch"
        )
    if label.get("pool_address") != report["pool_address"]:
        raise ValueError(
            "Phase 7 EXIT post-label v2 pool mismatch"
        )
    if label.get("closed_decision_id") != report["settlement_decision_id"]:
        raise ValueError(
            "Phase 7 EXIT post-label v2 closure mismatch"
        )

    for field in (
        "production_source_stable_during_audit",
        "valuation_present",
        "learning_label_present",
        "persisted_learning_label_matches_apply",
        "learning_label_reconciliation_complete",
        "phase7_evidence_status_recheck_required",
        "phase7_evidence_status_recheck_ready",
        "requires_separate_phase7_promotion_action",
        "post_label_audit_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT post-label v2 audit requires {field}=true"
            )

    for field in (
        "new_live_entry_authorized",
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
                f"Phase 7 EXIT post-label v2 audit requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["audit_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT post-label v2 audit digest mismatch"
        )


def build_exit_post_learning_label_audit_v2(
    *,
    source_tree: str | Path,
    saved_learning_label_apply_path: str | Path,
    expected_learning_label_apply_sha256: str,
    pio_database_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    apply_module, planner = _load_reviewed(source)

    applied = json.loads(
        Path(saved_learning_label_apply_path).resolve(strict=True).read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(applied, dict):
        raise ValueError(
            "saved Phase 7 EXIT learning-label v2 apply must be a JSON object"
        )
    apply_module.validate_exit_learning_label_apply_v2(applied)
    if (
        not _is_hex_digest(expected_learning_label_apply_sha256, 64)
        or applied["apply_sha256"]
        != expected_learning_label_apply_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT learning-label v2 apply digest mismatch"
        )

    for field in (
        "target_state_matches_plan",
        "learning_label_apply_complete",
        "requires_post_label_audit",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
        "production_pio_database_modified",
    ):
        if applied.get(field) is not True:
            raise ValueError(
                f"post-label v2 audit requires apply {field}=true"
            )
    if applied.get("learning_label_remaining") is not False:
        raise ValueError(
            "saved learning-label v2 apply still has remaining work"
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
        if applied.get(field) is not False:
            raise ValueError(
                f"post-label v2 audit refuses apply {field}=true"
            )

    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != applied["pio_database_path"]:
        raise ValueError(
            "post-label v2 Pio database differs from apply artifact"
        )
    before = planner._database_state(database)
    if before["database"] != applied["pio_database_sha256_after"]:
        raise ValueError(
            "Pio database changed after learning-label v2 apply"
        )
    if before["wal"] != applied["pio_wal_sha256_after"]:
        raise ValueError(
            "Pio WAL changed after learning-label v2 apply"
        )
    if before["shm"] != applied["pio_shm_sha256_after"]:
        raise ValueError(
            "Pio SHM changed after learning-label v2 apply"
        )

    state = planner._label_state(
        database,
        position_address=applied["position_address"],
    )
    label = state["learning_label"]
    if label is None:
        raise ValueError(
            "post-label v2 audit is missing persisted learning label"
        )
    if label != applied["learning_label"]:
        raise ValueError(
            "persisted learning label differs from v2 apply artifact"
        )
    if state["status"] != "CLOSED":
        raise ValueError(
            "post-label v2 audit requires CLOSED position"
        )
    if state["valuation_present"] is not True:
        raise ValueError(
            "post-label v2 audit requires persisted valuation"
        )
    if state["opened_decision_id"] != applied["opened_decision_id"]:
        raise ValueError(
            "post-label v2 opening decision differs from apply"
        )
    if state["closed_decision_id"] != applied["settlement_decision_id"]:
        raise ValueError(
            "post-label v2 closing decision differs from apply"
        )

    after = planner._database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during post-label v2 audit"
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
        "saved_learning_label_apply_sha256": applied["apply_sha256"],
        "expected_learning_label_apply_sha256": (
            expected_learning_label_apply_sha256
        ),
        "saved_learning_label_plan_sha256": applied["saved_plan_sha256"],
        "opened_decision_id": applied["opened_decision_id"],
        "principal_exit_decision_id": applied[
            "principal_exit_decision_id"
        ],
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
        "production_source_stable_during_audit": True,
        "position_status": "CLOSED",
        "valuation_present": True,
        "learning_label_present": True,
        "persisted_learning_label_matches_apply": True,
        "learning_label": label,
        "learning_label_reconciliation_complete": True,
        "phase7_evidence_status_recheck_required": True,
        "phase7_evidence_status_recheck_ready": True,
        "requires_separate_phase7_promotion_action": True,
        "post_label_audit_ready": True,
        "new_live_entry_authorized": False,
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
        "audit_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_post_learning_label_audit_v2(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit after the Phase 7 EXIT learning-label v2 apply. "
            "The audit proves the CLOSED position retains valuation, the exact "
            "immutable label is persisted, and a fresh Phase 7 evidence-status "
            "recheck may proceed without authorizing more trading or promotion."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-learning-label-apply", required=True)
    parser.add_argument("--expected-learning-label-apply-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    args = parser.parse_args()

    report = build_exit_post_learning_label_audit_v2(
        source_tree=args.source_tree,
        saved_learning_label_apply_path=args.saved_learning_label_apply,
        expected_learning_label_apply_sha256=(
            args.expected_learning_label_apply_sha256
        ),
        pio_database_path=args.pio_db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
