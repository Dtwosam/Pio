from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_LEARNING_LABEL_PLAN_V2"

POST_VALUATION_AUDIT_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_post_valuation.py"
)
PYTHON_LABEL = Path(
    "python-learner/src/meteora_learner/live_learning_label.py"
)
PYTHON_STORAGE = Path("python-learner/src/meteora_learner/storage.py")
REVIEWED_SOURCE_BLOBS = {
    POST_VALUATION_AUDIT_TOOL: "5163fa5565e2a4dd3abacc4efa0bdc1e3edf457b",
    PYTHON_LABEL: "49f16559cc764dc00496167c6281631b59c39f06",
    PYTHON_STORAGE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

ACTION_BUILD_LABEL = "BUILD_LIVE_LEARNING_LABEL"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_post_valuation_audit_sha256",
    "expected_post_valuation_audit_sha256",
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
    "post_valuation_database_sha256",
    "pre_learning_label_present",
    "planned_actions",
    "private_replay_succeeded",
    "private_learning_label",
    "learning_label_reused_existing",
    "private_label_target_sha256",
    "learning_label_plan_ready",
    "requires_separate_learning_label_apply",
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


def _load_reviewed(source: Path) -> tuple[Any, dict[str, Any]]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT learning-label v2 dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT learning-label v2 dependency mismatch: {relative}"
            )

    audit_module = _load_module(
        source / POST_VALUATION_AUDIT_TOOL,
        "phase7_exit_learning_label_v2_post_valuation",
    )

    python_src = source / "python-learner" / "src"
    if str(python_src) not in sys.path:
        sys.path.insert(0, str(python_src))

    from meteora_learner.live_learning_label import build_live_learning_label
    from meteora_learner.storage import Storage

    return audit_module, {
        "Storage": Storage,
        "build_live_learning_label": build_live_learning_label,
    }


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"unsafe Pio database state file: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    before = _database_state(source)
    if before["database"] is None:
        raise ValueError("Pio database disappeared before label snapshot")
    uri = f"file:{source.as_posix()}?mode=ro"
    if before["wal"] is None:
        uri += "&immutable=1"
    src = sqlite3.connect(uri, uri=True)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if _database_state(source) != before:
        raise ValueError(
            "Pio database changed during private learning-label snapshot"
        )


def _label_state(
    database: Path,
    *,
    position_address: str,
) -> dict[str, Any]:
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        position = conn.execute(
            """
            SELECT status, opened_decision_id, closed_decision_id
            FROM live_positions
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if position is None:
            raise ValueError(
                "learning-label plan cannot find live position"
            )
        if str(position["status"]) != "CLOSED":
            raise ValueError(
                "learning-label plan requires CLOSED live position"
            )

        valuation = conn.execute(
            """
            SELECT position_address
            FROM live_position_valuations
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if valuation is None:
            raise ValueError(
                "learning-label plan requires persisted valuation"
            )

        label = conn.execute(
            """
            SELECT raw_json
            FROM live_learning_labels
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        label_record = None
        if label is not None:
            try:
                label_record = json.loads(str(label["raw_json"]))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "persisted learning-label raw_json is invalid"
                ) from exc
            if not isinstance(label_record, dict):
                raise ValueError(
                    "persisted learning-label raw_json must be an object"
                )

        return {
            "status": "CLOSED",
            "opened_decision_id": str(position["opened_decision_id"]),
            "closed_decision_id": str(position["closed_decision_id"]),
            "valuation_present": True,
            "learning_label": label_record,
        }
    finally:
        conn.close()


def validate_exit_learning_label_plan_v2(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT learning-label v2 plan must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"plan_sha256"}:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 plan schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT learning-label v2 plan format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT learning-label v2 plan type"
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
            "Phase 7 EXIT learning-label v2 lineage mismatch"
        )

    for field in (
        "saved_post_valuation_audit_sha256",
        "expected_post_valuation_audit_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "post_valuation_database_sha256",
        "private_label_target_sha256",
        "plan_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT learning-label v2 {field} is invalid"
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
                f"Phase 7 EXIT learning-label v2 {field} is invalid"
            )

    if report["saved_post_valuation_audit_sha256"] != report[
        "expected_post_valuation_audit_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 audit digest mismatch"
        )
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "production Pio database changed during label planning"
        )
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError(
            "production Pio WAL changed during label planning"
        )
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError(
            "production Pio SHM changed during label planning"
        )
    if report["pio_database_sha256_before"] != report[
        "post_valuation_database_sha256"
    ]:
        raise ValueError(
            "production Pio database changed after post-valuation audit"
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
                f"Phase 7 EXIT learning-label v2 {field} is invalid"
            )

    if report.get("pre_learning_label_present") is not False:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 requires no preexisting label"
        )
    if report.get("planned_actions") != [ACTION_BUILD_LABEL]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 planned action mismatch"
        )
    if report.get("learning_label_reused_existing") is not False:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 requires new label construction"
        )
    if not isinstance(report.get("private_learning_label"), dict):
        raise ValueError(
            "Phase 7 EXIT learning-label v2 private label is invalid"
        )
    label = report["private_learning_label"]
    if label.get("position_address") != report["position_address"]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 position mismatch"
        )
    if label.get("decision_id") != report["opened_decision_id"]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 opening decision mismatch"
        )
    if label.get("pool_address") != report["pool_address"]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 pool mismatch"
        )
    if label.get("closed_decision_id") != report[
        "settlement_decision_id"
    ]:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 closure mismatch"
        )

    for field in (
        "private_replay_succeeded",
        "learning_label_plan_ready",
        "requires_separate_learning_label_apply",
        "requires_post_label_audit",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT learning-label v2 requires {field}=true"
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
                f"Phase 7 EXIT learning-label v2 requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["plan_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT learning-label v2 plan digest mismatch"
        )


def build_exit_learning_label_plan_v2(
    *,
    source_tree: str | Path,
    saved_post_valuation_audit_path: str | Path,
    expected_post_valuation_audit_sha256: str,
    pio_database_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    audit_module, runtime = _load_reviewed(source)

    audit = json.loads(
        Path(saved_post_valuation_audit_path).resolve(strict=True).read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(audit, dict):
        raise ValueError(
            "saved Phase 7 EXIT post-valuation audit must be a JSON object"
        )
    audit_module.validate_exit_post_valuation_audit(audit)
    if (
        not _is_hex_digest(expected_post_valuation_audit_sha256, 64)
        or audit["audit_sha256"]
        != expected_post_valuation_audit_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT post-valuation audit digest mismatch"
        )

    for field in (
        "valuation_present",
        "persisted_valuation_matches_apply",
        "learning_label_reconciliation_required",
        "learning_label_reconciliation_ready",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
        "post_valuation_audit_ready",
    ):
        if audit.get(field) is not True:
            raise ValueError(
                f"learning-label v2 plan requires audit {field}=true"
            )
    if audit.get("learning_label_present") is not False:
        raise ValueError(
            "learning-label v2 plan requires no existing label"
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
        if audit.get(field) is not False:
            raise ValueError(
                f"learning-label v2 plan refuses audit {field}=true"
            )

    database = Path(pio_database_path).resolve(strict=True)
    if str(database) != audit["pio_database_path"]:
        raise ValueError(
            "learning-label v2 Pio database differs from post-valuation audit"
        )
    before = _database_state(database)
    if before["database"] != audit["pio_database_sha256_after"]:
        raise ValueError(
            "Pio database changed after post-valuation audit"
        )
    if before["wal"] != audit["pio_wal_sha256_after"]:
        raise ValueError(
            "Pio WAL changed after post-valuation audit"
        )
    if before["shm"] != audit["pio_shm_sha256_after"]:
        raise ValueError(
            "Pio SHM changed after post-valuation audit"
        )

    pre = _label_state(
        database,
        position_address=audit["position_address"],
    )
    if pre["learning_label"] is not None:
        raise ValueError(
            "learning-label v2 plan found unexpected existing label"
        )
    if pre["opened_decision_id"] != audit["opened_decision_id"]:
        raise ValueError(
            "learning-label v2 opening decision differs from audit"
        )
    if pre["closed_decision_id"] != audit["settlement_decision_id"]:
        raise ValueError(
            "learning-label v2 closing decision differs from audit"
        )

    with tempfile.TemporaryDirectory(
        prefix="pio-phase7-exit-learning-label-v2-plan-"
    ) as tmp:
        private_db = Path(tmp) / "pio.db"
        _snapshot_sqlite(database, private_db)
        storage = runtime["Storage"](private_db)
        result = runtime["build_live_learning_label"](
            storage,
            position_address=audit["position_address"],
        )
        label = result.label.to_record()
        post = _label_state(
            private_db,
            position_address=audit["position_address"],
        )
        if post["learning_label"] != label:
            raise ValueError(
                "private learning-label replay differs from persisted private label"
            )
        target_sha = _sha256_bytes(_canonical_bytes(post))

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during learning-label planning"
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
        "saved_post_valuation_audit_sha256": audit["audit_sha256"],
        "expected_post_valuation_audit_sha256": (
            expected_post_valuation_audit_sha256
        ),
        "opened_decision_id": audit["opened_decision_id"],
        "principal_exit_decision_id": audit[
            "principal_exit_decision_id"
        ],
        "settlement_decision_id": audit["settlement_decision_id"],
        "pool_address": audit["pool_address"],
        "position_address": audit["position_address"],
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "post_valuation_database_sha256": audit[
            "pio_database_sha256_after"
        ],
        "pre_learning_label_present": False,
        "planned_actions": [ACTION_BUILD_LABEL],
        "private_replay_succeeded": True,
        "private_learning_label": label,
        "learning_label_reused_existing": bool(result.reused_existing),
        "private_label_target_sha256": target_sha,
        "learning_label_plan_ready": True,
        "requires_separate_learning_label_apply": True,
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
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "plan_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_learning_label_plan_v2(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Privately build the immutable Phase 7 EXIT learning label only "
            "after valuation has been applied and audited. Production Pio state "
            "remains unchanged; label persistence is deferred to a separate "
            "stale-state-guarded apply step."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-post-valuation-audit", required=True)
    parser.add_argument("--expected-post-valuation-audit-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    args = parser.parse_args()

    report = build_exit_learning_label_plan_v2(
        source_tree=args.source_tree,
        saved_post_valuation_audit_path=args.saved_post_valuation_audit,
        expected_post_valuation_audit_sha256=(
            args.expected_post_valuation_audit_sha256
        ),
        pio_database_path=args.pio_db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
