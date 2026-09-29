from __future__ import annotations

import argparse
from dataclasses import asdict
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
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_POSITION_VALUATION_PLAN_V1"
)

POST_APPLY_AUDIT_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_final_state_post_apply.py"
)
PYTHON_VALUATION = Path(
    "python-learner/src/meteora_learner/live_position_valuation.py"
)
PYTHON_STORAGE = Path(
    "python-learner/src/meteora_learner/storage.py"
)
PYTHON_QUOTE_REGISTRY = Path(
    "python-learner/src/meteora_learner/quote_registry.py"
)
REVIEWED_SOURCE_BLOBS = {
    POST_APPLY_AUDIT_TOOL: "1a4056521dd7be7b02bb2838b9b6e16b853f31e1",
    PYTHON_VALUATION: "f0f339be6c3483d6886cdc7eb7d2c00766f09caf",
    PYTHON_STORAGE: "39bcc99413df357b89d261e854861e9e4a3fff23",
    PYTHON_QUOTE_REGISTRY: "672f2a8061504c7d27cddb85903857e007edb024",
}

ACTION_VALUE_POSITION = "VALUE_LIVE_POSITION_OUTCOME"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_post_apply_audit_sha256",
    "expected_post_apply_audit_sha256",
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
    "post_apply_database_sha256",
    "quote_unit",
    "max_age_seconds",
    "pre_outcome_label_status",
    "pre_valuation_present",
    "planned_actions",
    "valuation_required",
    "private_replay_succeeded",
    "valuation",
    "valuation_reused_existing",
    "post_outcome_label_status",
    "private_valuation_target_sha256",
    "valuation_plan_ready",
    "requires_separate_valuation_apply",
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
                f"Phase 7 EXIT valuation-plan dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT valuation-plan dependency mismatch: {relative}"
            )

    audit_module = _load_module(
        source / POST_APPLY_AUDIT_TOOL,
        "phase7_exit_valuation_plan_post_apply_audit",
    )

    python_src = source / "python-learner" / "src"
    if str(python_src) not in sys.path:
        sys.path.insert(0, str(python_src))

    from meteora_learner.live_position_valuation import (
        value_live_position_outcome,
    )
    from meteora_learner.quote_registry import DEFAULT_QUOTE_UNIT
    from meteora_learner.storage import Storage

    runtime = {
        "Storage": Storage,
        "value_live_position_outcome": value_live_position_outcome,
        "DEFAULT_QUOTE_UNIT": DEFAULT_QUOTE_UNIT,
    }
    return audit_module, runtime


def _safe_regular_hash(path: Path) -> str | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"unsafe production database file type: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(path: Path) -> dict[str, str | None]:
    return {
        "database": _safe_regular_hash(path),
        "wal": _safe_regular_hash(Path(str(path) + "-wal")),
        "shm": _safe_regular_hash(Path(str(path) + "-shm")),
    }


def _backup_database(source: Path, destination: Path) -> None:
    uri = f"file:{source.as_posix()}?mode=ro"
    src = sqlite3.connect(uri, uri=True)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _valuation_state(
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
                "valuation plan requires immutable position outcome"
            )
        label_status = str(outcome["label_status"])
        if label_status not in {"ATOMIC_ONLY", "VALUED"}:
            raise ValueError("position outcome has invalid label_status")

        valuation = conn.execute(
            """
            SELECT *
            FROM live_position_valuations
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        valuation_record = (
            {key: value for key, value in dict(valuation).items() if key != "id"}
            if valuation is not None
            else None
        )
        if valuation_record is None and label_status == "VALUED":
            raise ValueError(
                "VALUED position outcome is missing persisted valuation"
            )
        if valuation_record is not None and label_status != "VALUED":
            raise ValueError(
                "persisted valuation requires VALUED position outcome"
            )

        try:
            outcome_record = json.loads(str(outcome["raw_json"]))
        except json.JSONDecodeError as exc:
            raise ValueError("position outcome raw_json is invalid") from exc
        if not isinstance(outcome_record, dict):
            raise ValueError("position outcome raw_json must be an object")
        if outcome_record.get("position_address") != position_address:
            raise ValueError("position outcome address mismatch")
        if outcome_record.get("label_status") != label_status:
            raise ValueError("position outcome label-status mismatch")

        return {
            "outcome_label_status": label_status,
            "outcome": outcome_record,
            "valuation": valuation_record,
        }
    finally:
        conn.close()


def validate_exit_position_valuation_plan(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT valuation plan must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"plan_sha256"}:
        raise ValueError(
            "Phase 7 EXIT valuation plan schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT valuation plan format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT valuation plan type"
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
            "Phase 7 EXIT valuation plan lineage mismatch"
        )

    for field in (
        "saved_post_apply_audit_sha256",
        "expected_post_apply_audit_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "post_apply_database_sha256",
        "private_valuation_target_sha256",
        "plan_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT valuation plan {field} is invalid"
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
                f"Phase 7 EXIT valuation plan {field} is invalid"
            )

    if report["saved_post_apply_audit_sha256"] != report[
        "expected_post_apply_audit_sha256"
    ]:
        raise ValueError("post-apply audit digest mismatch")
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "production Pio database changed during valuation planning"
        )
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError(
            "production Pio WAL changed during valuation planning"
        )
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError(
            "production Pio SHM changed during valuation planning"
        )
    if report["pio_database_sha256_before"] != report[
        "post_apply_database_sha256"
    ]:
        raise ValueError(
            "production Pio database changed after post-apply audit"
        )

    for field in (
        "opened_decision_id",
        "principal_exit_decision_id",
        "settlement_decision_id",
        "pool_address",
        "position_address",
        "pio_database_path",
        "quote_unit",
        "pre_outcome_label_status",
        "post_outcome_label_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT valuation plan {field} is invalid"
            )

    if (
        not isinstance(report.get("max_age_seconds"), int)
        or isinstance(report["max_age_seconds"], bool)
        or report["max_age_seconds"] < 0
    ):
        raise ValueError(
            "Phase 7 EXIT valuation plan max_age_seconds is invalid"
        )

    if report["pre_outcome_label_status"] not in {
        "ATOMIC_ONLY",
        "VALUED",
    }:
        raise ValueError(
            "Phase 7 EXIT valuation plan pre label status is invalid"
        )
    if report["post_outcome_label_status"] != "VALUED":
        raise ValueError(
            "Phase 7 EXIT valuation private replay did not produce VALUED outcome"
        )

    planned = report.get("planned_actions")
    if not isinstance(planned, list):
        raise ValueError("valuation planned_actions must be a list")
    if planned not in ([], [ACTION_VALUE_POSITION]):
        raise ValueError("valuation planned_actions are invalid")

    valuation_required = report["pre_outcome_label_status"] == "ATOMIC_ONLY"
    if report.get("valuation_required") is not valuation_required:
        raise ValueError(
            "Phase 7 EXIT valuation-required binding mismatch"
        )
    if report.get("pre_valuation_present") is not (not valuation_required):
        raise ValueError(
            "Phase 7 EXIT pre-valuation binding mismatch"
        )
    if bool(planned) is not valuation_required:
        raise ValueError(
            "Phase 7 EXIT valuation planned-action binding mismatch"
        )
    if report.get("requires_separate_valuation_apply") is not valuation_required:
        raise ValueError(
            "Phase 7 EXIT valuation-apply binding mismatch"
        )
    if report.get("valuation_reused_existing") is not (not valuation_required):
        raise ValueError(
            "Phase 7 EXIT valuation reuse binding mismatch"
        )

    if not isinstance(report.get("valuation"), dict):
        raise ValueError("Phase 7 EXIT valuation record is invalid")
    valuation = report["valuation"]
    if valuation.get("position_address") != report["position_address"]:
        raise ValueError("Phase 7 EXIT valuation position mismatch")
    if valuation.get("pool_address") != report["pool_address"]:
        raise ValueError("Phase 7 EXIT valuation pool mismatch")
    if valuation.get("closed_decision_id") != report[
        "settlement_decision_id"
    ]:
        raise ValueError(
            "Phase 7 EXIT valuation settlement decision mismatch"
        )
    if valuation.get("quote_unit") != report["quote_unit"]:
        raise ValueError(
            "Phase 7 EXIT valuation quote unit mismatch"
        )
    if valuation.get("max_age_seconds") != report["max_age_seconds"]:
        raise ValueError(
            "Phase 7 EXIT valuation max-age mismatch"
        )

    for field in (
        "private_replay_succeeded",
        "valuation_plan_ready",
        "requires_post_valuation_audit",
        "learning_label_reconciliation_required",
        "learning_label_reconciliation_ready",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT valuation plan requires {field}=true"
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
                f"Phase 7 EXIT valuation plan requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["plan_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT valuation plan digest mismatch"
        )


def build_exit_position_valuation_plan(
    *,
    source_tree: str | Path,
    saved_post_apply_audit_path: str | Path,
    expected_post_apply_audit_sha256: str,
    pio_database_path: str | Path,
    max_age_seconds: int = 300,
    quote_unit: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    audit_module, runtime = _load_reviewed(source)

    audit = json.loads(
        Path(saved_post_apply_audit_path).resolve(strict=True).read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(audit, dict):
        raise ValueError("saved post-apply audit must be a JSON object")
    audit_module.validate_exit_final_post_apply_audit(audit)
    if (
        not _is_hex_digest(expected_post_apply_audit_sha256, 64)
        or audit["audit_sha256"] != expected_post_apply_audit_sha256
    ):
        raise ValueError(
            "saved Phase 7 EXIT post-apply audit digest mismatch"
        )

    for field in (
        "exact_exit_lifecycle_reconciled",
        "learning_label_reconciliation_required",
        "phase7_evidence_status_recheck_required",
        "post_apply_audit_ready",
        "requires_separate_phase7_promotion_action",
    ):
        if audit.get(field) is not True:
            raise ValueError(
                f"valuation plan requires post-apply audit {field}=true"
            )
    if audit.get("position_status") != "CLOSED":
        raise ValueError(
            "valuation plan requires CLOSED reconciled position"
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
                f"valuation plan refuses post-apply audit {field}=true"
            )

    if (
        not isinstance(max_age_seconds, int)
        or isinstance(max_age_seconds, bool)
        or max_age_seconds < 0
    ):
        raise ValueError("max_age_seconds must be a non-negative integer")
    quote_unit = quote_unit or runtime["DEFAULT_QUOTE_UNIT"]
    if not isinstance(quote_unit, str) or not quote_unit.strip():
        raise ValueError("quote_unit is required")
    quote_unit = quote_unit.strip()

    database = Path(pio_database_path).expanduser().resolve(strict=True)
    if str(database) != audit["pio_database_path"]:
        raise ValueError(
            "valuation plan Pio database differs from post-apply audit"
        )
    before = _database_state(database)
    if before["database"] is None:
        raise ValueError("Pio database is missing")
    if before["database"] != audit["pio_database_sha256_after"]:
        raise ValueError(
            "Pio database changed after post-apply audit"
        )

    pre = _valuation_state(
        database,
        position_address=audit["position_address"],
    )
    pre_label = pre["outcome_label_status"]
    valuation_required = pre_label == "ATOMIC_ONLY"
    pre_valuation_present = pre["valuation"] is not None
    planned = [ACTION_VALUE_POSITION] if valuation_required else []

    with tempfile.TemporaryDirectory(
        prefix="pio-phase7-exit-valuation-plan-"
    ) as tmp:
        private_db = Path(tmp) / "pio.db"
        _backup_database(database, private_db)
        storage = runtime["Storage"](private_db)
        result = runtime["value_live_position_outcome"](
            storage,
            position_address=audit["position_address"],
            max_age_seconds=max_age_seconds,
            quote_unit=quote_unit,
        )
        valuation = result.valuation.to_record()
        post = _valuation_state(
            private_db,
            position_address=audit["position_address"],
        )
        target_sha = _sha256_bytes(_canonical_bytes(post))

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during valuation planning"
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
        "saved_post_apply_audit_sha256": audit["audit_sha256"],
        "expected_post_apply_audit_sha256": (
            expected_post_apply_audit_sha256
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
        "post_apply_database_sha256": audit[
            "pio_database_sha256_after"
        ],
        "quote_unit": quote_unit,
        "max_age_seconds": max_age_seconds,
        "pre_outcome_label_status": pre_label,
        "pre_valuation_present": pre_valuation_present,
        "planned_actions": planned,
        "valuation_required": valuation_required,
        "private_replay_succeeded": True,
        "valuation": valuation,
        "valuation_reused_existing": bool(result.reused_existing),
        "post_outcome_label_status": post["outcome_label_status"],
        "private_valuation_target_sha256": target_sha,
        "valuation_plan_ready": True,
        "requires_separate_valuation_apply": valuation_required,
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
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "plan_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_position_valuation_plan(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Privately replay the immutable closed-position valuation after "
            "Phase 7 EXIT reconciliation. Quotes are read only from persisted "
            "no-lookahead evidence, production Pio state remains unchanged, "
            "and any required valuation mutation is deferred to a separate "
            "apply step."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-post-apply-audit", required=True)
    parser.add_argument("--expected-post-apply-audit-sha256", required=True)
    parser.add_argument("--pio-db", default="/opt/pio/data/pio.db")
    parser.add_argument("--max-age-seconds", type=int, default=300)
    parser.add_argument("--quote-unit")
    args = parser.parse_args()

    report = build_exit_position_valuation_plan(
        source_tree=args.source_tree,
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
