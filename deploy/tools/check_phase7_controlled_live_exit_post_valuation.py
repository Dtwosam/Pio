from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_POST_VALUATION_AUDIT_V1"

VALUATION_APPLY_TOOL = Path(
    "deploy/tools/apply_phase7_controlled_live_exit_position_valuation.py"
)
REVIEWED_SOURCE_BLOBS = {
    VALUATION_APPLY_TOOL: "3dc23032685ce51c41da731cc85623dcdfdc8621",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_valuation_apply_sha256",
    "expected_valuation_apply_sha256",
    "saved_valuation_plan_sha256",
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
    "outcome_label_status",
    "valuation_present",
    "persisted_valuation_matches_apply",
    "valuation",
    "learning_label_present",
    "learning_label_reconciliation_required",
    "learning_label_reconciliation_ready",
    "phase7_evidence_status_recheck_required",
    "requires_separate_phase7_promotion_action",
    "post_valuation_audit_ready",
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
    path = source / VALUATION_APPLY_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 7 EXIT valuation apply is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[VALUATION_APPLY_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT valuation apply blob mismatch"
        )
    apply_module = _load_module(
        path,
        "phase7_exit_post_valuation_apply",
    )
    planner = apply_module._load_planner(source)
    return apply_module, planner


def _state(
    database: Path,
    *,
    position_address: str,
) -> dict[str, Any]:
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        outcome = conn.execute(
            """
            SELECT label_status, raw_json
            FROM live_position_outcomes
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if outcome is None:
            raise ValueError(
                "post-valuation audit is missing position outcome"
            )
        if str(outcome["label_status"]) != "VALUED":
            raise ValueError(
                "post-valuation audit requires VALUED position outcome"
            )

        valuation = conn.execute(
            """
            SELECT raw_json
            FROM live_position_valuations
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if valuation is None:
            raise ValueError(
                "post-valuation audit is missing persisted valuation"
            )
        try:
            valuation_record = json.loads(str(valuation["raw_json"]))
        except json.JSONDecodeError as exc:
            raise ValueError(
                "persisted valuation raw_json is invalid"
            ) from exc
        if not isinstance(valuation_record, dict):
            raise ValueError(
                "persisted valuation raw_json must be an object"
            )

        label = conn.execute(
            """
            SELECT raw_json
            FROM live_learning_labels
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()

        return {
            "outcome_label_status": "VALUED",
            "valuation": valuation_record,
            "learning_label_present": label is not None,
        }
    finally:
        conn.close()


def validate_exit_post_valuation_audit(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT post-valuation audit must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"audit_sha256"}:
        raise ValueError(
            "Phase 7 EXIT post-valuation audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT post-valuation audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT post-valuation audit type"
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
            "Phase 7 EXIT post-valuation audit lineage mismatch"
        )

    for field in (
        "saved_valuation_apply_sha256",
        "expected_valuation_apply_sha256",
        "saved_valuation_plan_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT post-valuation audit {field} is invalid"
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
                f"Phase 7 EXIT post-valuation audit {field} is invalid"
            )

    if report["saved_valuation_apply_sha256"] != report[
        "expected_valuation_apply_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT post-valuation apply digest mismatch"
        )
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "production Pio database changed during post-valuation audit"
        )
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError(
            "production Pio WAL changed during post-valuation audit"
        )
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError(
            "production Pio SHM changed during post-valuation audit"
        )

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
                f"Phase 7 EXIT post-valuation audit {field} is invalid"
            )

    if report["outcome_label_status"] != "VALUED":
        raise ValueError(
            "Phase 7 EXIT post-valuation audit requires VALUED outcome"
        )
    if not isinstance(report.get("valuation"), dict):
        raise ValueError(
            "Phase 7 EXIT post-valuation audit valuation is invalid"
        )
    if report["valuation"].get("position_address") != report[
        "position_address"
    ]:
        raise ValueError(
            "Phase 7 EXIT post-valuation valuation position mismatch"
        )
    if report["valuation"].get("pool_address") != report["pool_address"]:
        raise ValueError(
            "Phase 7 EXIT post-valuation valuation pool mismatch"
        )
    if report["valuation"].get("closed_decision_id") != report[
        "settlement_decision_id"
    ]:
        raise ValueError(
            "Phase 7 EXIT post-valuation valuation closure mismatch"
        )

    for field in (
        "production_source_stable_during_audit",
        "valuation_present",
        "persisted_valuation_matches_apply",
        "learning_label_reconciliation_required",
        "learning_label_reconciliation_ready",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
        "post_valuation_audit_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT post-valuation audit requires {field}=true"
            )
    if report.get("learning_label_present") is not False:
        raise ValueError(
            "Phase 7 EXIT post-valuation audit requires learning_label_present=false"
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
                f"Phase 7 EXIT post-valuation audit requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["audit_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT post-valuation audit digest mismatch"
        )


def build_exit_post_valuation_audit(
    *,
    source_tree: str | Path,
    saved_valuation_apply_path: str | Path,
    expected_valuation_apply_sha256: str,
    pio_database_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    apply_module, planner = _load_reviewed(source)

    applied = json.loads(
        Path(saved_valuation_apply_path).resolve(strict=True).read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(applied, dict):
        raise ValueError(
            "saved Phase 7 EXIT valuation apply must be a JSON object"
        )
    apply_module.validate_exit_position_valuation_apply(applied)
    if (
        not _is_hex_digest(expected_valuation_apply_sha256, 64)
        or applied["apply_sha256"] != expected_valuation_apply_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT valuation apply digest mismatch"
        )

    for field in (
        "target_state_matches_plan",
        "valuation_apply_complete",
        "requires_post_valuation_audit",
        "learning_label_reconciliation_required",
        "learning_label_reconciliation_ready",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
        "production_pio_database_modified",
    ):
        if applied.get(field) is not True:
            raise ValueError(
                f"post-valuation audit requires apply {field}=true"
            )
    if applied.get("valuation_remaining") is not False:
        raise ValueError(
            "saved valuation apply still has remaining work"
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
                f"post-valuation audit refuses apply {field}=true"
            )

    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != applied["pio_database_path"]:
        raise ValueError(
            "post-valuation Pio database differs from apply artifact"
        )
    before = planner._database_state(database)
    if before["database"] != applied["pio_database_sha256_after"]:
        raise ValueError(
            "Pio database changed after valuation apply"
        )
    if before["wal"] != applied["pio_wal_sha256_after"]:
        raise ValueError(
            "Pio WAL changed after valuation apply"
        )
    if before["shm"] != applied["pio_shm_sha256_after"]:
        raise ValueError(
            "Pio SHM changed after valuation apply"
        )

    state = _state(
        database,
        position_address=applied["position_address"],
    )
    if state["valuation"] != applied["valuation"]:
        raise ValueError(
            "persisted valuation differs from valuation apply artifact"
        )
    if state["learning_label_present"]:
        raise ValueError(
            "learning label already exists before learning-label reconciliation"
        )

    after = planner._database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during post-valuation audit"
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
        "saved_valuation_apply_sha256": applied["apply_sha256"],
        "expected_valuation_apply_sha256": (
            expected_valuation_apply_sha256
        ),
        "saved_valuation_plan_sha256": applied["saved_plan_sha256"],
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
        "outcome_label_status": state["outcome_label_status"],
        "valuation_present": True,
        "persisted_valuation_matches_apply": True,
        "valuation": state["valuation"],
        "learning_label_present": False,
        "learning_label_reconciliation_required": True,
        "learning_label_reconciliation_ready": True,
        "phase7_evidence_status_recheck_required": True,
        "requires_separate_phase7_promotion_action": True,
        "post_valuation_audit_ready": True,
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
    validate_exit_post_valuation_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of the applied Phase 7 EXIT closed-position "
            "valuation. The audit proves the production outcome is VALUED, "
            "the persisted immutable valuation matches the apply artifact, "
            "and no learning label exists yet."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-valuation-apply", required=True)
    parser.add_argument("--expected-valuation-apply-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    args = parser.parse_args()

    report = build_exit_post_valuation_audit(
        source_tree=args.source_tree,
        saved_valuation_apply_path=args.saved_valuation_apply,
        expected_valuation_apply_sha256=(
            args.expected_valuation_apply_sha256
        ),
        pio_database_path=args.pio_db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
