from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE6_PRELIVE_EVIDENCE_STATUS_V1"

PHASE5_POST_PROMOTION_TOOL = Path(
    "deploy/tools/check_manual_market_paper_phase5_post_promotion.py"
)
PHASE6_VALIDATION_MODULE = Path(
    "python-learner/src/meteora_learner/phase6_validation.py"
)
PHASE_PROMOTION_MODULE = Path(
    "python-learner/src/meteora_learner/phase_promotion.py"
)
STORAGE_MODULE = Path(
    "python-learner/src/meteora_learner/storage.py"
)

REVIEWED_SOURCE_BLOBS = {
    PHASE5_POST_PROMOTION_TOOL: "ce6543c9040cc8bc0dba59e834d827e8ac802bf7",
    PHASE6_VALIDATION_MODULE: "bc2f6ee7f60953f456f7ed2b2c0972cb903e6ed1",
    PHASE_PROMOTION_MODULE: "209cc3b29e3d3759079818b48d08734f050da4ac",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

EXPECTED_CRITERIA = {
    "min_passed_enter_intents": 10,
    "min_distinct_pools": 2,
    "min_blocked_intents": 2,
    "max_postsimulation_intents": 0,
}

EVALUATOR_HELPER = r"""
import json
from pathlib import Path
import sys

from meteora_learner.phase6_validation import (
    Phase6PromotionCriteria,
    evaluate_phase6_promotion,
)
from meteora_learner.storage import Storage

pio_db = Path(sys.argv[1])
execution_db = Path(sys.argv[2])

storage = Storage(pio_db)
report = evaluate_phase6_promotion(
    storage,
    execution_db=execution_db,
    criteria=Phase6PromotionCriteria(),
)
print(json.dumps(report.to_record(), sort_keys=True))
"""

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "phase5_post_promotion_audit_sha256",
    "production_repository",
    "pio_database_path",
    "execution_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "execution_database_sha256_before",
    "execution_database_sha256_after",
    "execution_wal_sha256_before",
    "execution_wal_sha256_after",
    "execution_shm_sha256_before",
    "execution_shm_sha256_after",
    "snapshot_only",
    "source_databases_unchanged",
    "phase5_promotion_confirmed",
    "phase6_criteria",
    "phase6_criteria_sha256",
    "phase6_report",
    "phase6_report_sha256",
    "phase5_promoted",
    "passed_enter_intents",
    "distinct_pools",
    "blocked_intents",
    "postsimulation_intents",
    "invalid_passed_intents",
    "distinct_authorized_wallets",
    "phase6_promotion_ready",
    "phase6_reasons",
    "phase6_evidence_status_ready",
    "requires_additional_phase6_evidence",
    "requires_separate_phase6_promotion_action",
    "phase6_promotion_persisted",
    "phase6_promotion_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "paper_timer_enable_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
    "production_execution_database_modified",
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


def _verify_reviewed_source(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 6 evidence dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 6 evidence dependency mismatch: {relative}")
    return _load_module(
        source / PHASE5_POST_PROMOTION_TOOL,
        "phase6_evidence_phase5_post_promotion",
    )


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 6 database sidecar is unsafe: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _validate_database_path(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    before = _database_state(source)
    if before["database"] is None:
        raise ValueError(f"SQLite source disappeared: {source}")

    uri = f"file:{source}?mode=ro"
    if before["wal"] is None:
        uri += "&immutable=1"

    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        target = sqlite3.connect(destination)
        try:
            connection.backup(target)
        finally:
            target.close()
    finally:
        connection.close()

    after = _database_state(source)
    if after != before:
        raise ValueError(f"SQLite source changed during snapshot: {source}")


def _evaluate_phase6(
    *,
    source: Path,
    pio_database: Path,
    execution_database: Path,
) -> dict[str, Any]:
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(source / "python-learner" / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            EVALUATOR_HELPER,
            str(pio_database),
            str(execution_database),
        ],
        cwd=source,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise ValueError("Phase 6 evidence evaluator failed")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Phase 6 evidence evaluator returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Phase 6 evidence evaluator result is invalid")
    return value


def _validate_phase6_result(result: dict[str, Any]) -> None:
    if result.get("criteria") != EXPECTED_CRITERIA:
        raise ValueError("Phase 6 criteria drifted from reviewed defaults")
    if result.get("phase5_promoted") is not True:
        raise ValueError("Phase 6 evidence requires persisted Phase 5 promotion")

    execution_db = result.get("execution_db")
    if not isinstance(execution_db, str) or not execution_db:
        raise ValueError("Phase 6 execution database result is invalid")

    for field in (
        "passed_enter_intents",
        "distinct_pools",
        "blocked_intents",
        "postsimulation_intents",
        "invalid_passed_intents",
    ):
        value = result.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 6 result {field} is invalid")

    wallets = result.get("distinct_authorized_wallets")
    if not isinstance(wallets, list) or any(
        not isinstance(wallet, str) or not wallet for wallet in wallets
    ):
        raise ValueError("Phase 6 authorized-wallet result is invalid")
    if wallets != sorted(set(wallets)):
        raise ValueError("Phase 6 authorized-wallet result is not canonical")

    if not isinstance(result.get("promotion_ready"), bool):
        raise ValueError("Phase 6 promotion-ready result is invalid")
    reasons = result.get("reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("Phase 6 reasons are invalid")
    if result["promotion_ready"] is not (len(reasons) == 0):
        raise ValueError("Phase 6 promotion-ready/reasons mismatch")


def validate_phase6_evidence_status(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 6 evidence status must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"phase6_evidence_status_sha256"}:
        raise ValueError("Phase 6 evidence status schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 6 evidence status format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 6 evidence status type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 6 evidence status source lineage mismatch")

    for field in (
        "phase5_post_promotion_audit_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "execution_database_sha256_before",
        "execution_database_sha256_after",
        "phase6_criteria_sha256",
        "phase6_report_sha256",
        "phase6_evidence_status_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 6 evidence status {field} is invalid")

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
        "execution_wal_sha256_before",
        "execution_wal_sha256_after",
        "execution_shm_sha256_before",
        "execution_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 6 evidence status {field} is invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "execution_database_path",
    ):
        value = report.get(field)
        if not isinstance(value, str) or not value.startswith("/"):
            raise ValueError(f"Phase 6 evidence status {field} is invalid")

    if report.get("phase6_criteria") != EXPECTED_CRITERIA:
        raise ValueError("Phase 6 evidence status criteria mismatch")
    if report["phase6_criteria_sha256"] != _sha256_bytes(
        _canonical_bytes(report["phase6_criteria"])
    ):
        raise ValueError("Phase 6 criteria digest mismatch")

    nested = report.get("phase6_report")
    if not isinstance(nested, dict):
        raise ValueError("Phase 6 nested report is invalid")
    _validate_phase6_result(nested)
    if report["phase6_report_sha256"] != _sha256_bytes(
        _canonical_bytes(nested)
    ):
        raise ValueError("Phase 6 report digest mismatch")

    scalar_bindings = (
        ("phase5_promoted", "phase5_promoted"),
        ("passed_enter_intents", "passed_enter_intents"),
        ("distinct_pools", "distinct_pools"),
        ("blocked_intents", "blocked_intents"),
        ("postsimulation_intents", "postsimulation_intents"),
        ("invalid_passed_intents", "invalid_passed_intents"),
        ("distinct_authorized_wallets", "distinct_authorized_wallets"),
        ("phase6_promotion_ready", "promotion_ready"),
        ("phase6_reasons", "reasons"),
    )
    for outer, inner in scalar_bindings:
        if report.get(outer) != nested.get(inner):
            raise ValueError(f"Phase 6 evidence status {outer} binding mismatch")

    if report["pio_database_sha256_before"] != report["pio_database_sha256_after"]:
        raise ValueError("Phase 6 Pio database changed during evaluation")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("Phase 6 Pio WAL changed during evaluation")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("Phase 6 Pio SHM changed during evaluation")
    if (
        report["execution_database_sha256_before"]
        != report["execution_database_sha256_after"]
    ):
        raise ValueError("Phase 6 execution database changed during evaluation")
    if report["execution_wal_sha256_before"] != report["execution_wal_sha256_after"]:
        raise ValueError("Phase 6 execution WAL changed during evaluation")
    if report["execution_shm_sha256_before"] != report["execution_shm_sha256_after"]:
        raise ValueError("Phase 6 execution SHM changed during evaluation")

    for field in (
        "snapshot_only",
        "source_databases_unchanged",
        "phase5_promotion_confirmed",
        "phase6_evidence_status_ready",
        "requires_separate_phase6_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 6 evidence status requires {field}=true")

    expected_more = not report["phase6_promotion_ready"]
    if report.get("requires_additional_phase6_evidence") is not expected_more:
        raise ValueError("Phase 6 additional-evidence flag mismatch")

    for field in (
        "phase6_promotion_persisted",
        "phase6_promotion_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
        "production_execution_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 6 evidence status requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["phase6_evidence_status_sha256"] != expected:
        raise ValueError("Phase 6 evidence status digest mismatch")


def build_phase6_evidence_status(
    *,
    repository: str | Path,
    source_tree: str | Path,
    phase5_post_promotion_audit_path: str | Path,
    execution_database_path: str | Path,
) -> dict[str, Any]:
    source_candidate = Path(source_tree).expanduser()
    production_candidate = Path(repository).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    if production_candidate.is_symlink():
        raise ValueError("production repository root must not be a symlink")
    source = source_candidate.resolve()
    production = production_candidate.resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    audit_module = _verify_reviewed_source(source)
    audit = _load_json(
        phase5_post_promotion_audit_path,
        label="Phase 5 post-promotion audit",
    )
    audit_module.validate_post_promotion_audit(audit)

    if audit.get("post_promotion_audit_ready") is not True:
        raise ValueError("Phase 5 post-promotion audit is not ready")
    if audit.get("phase5_promotion_confirmed") is not True:
        raise ValueError("Phase 5 promotion is not confirmed")
    if audit.get("phase5_promotion_persisted") is not True:
        raise ValueError("Phase 5 promotion is not persisted")
    if audit.get("requires_separate_phase6_workflow") is not True:
        raise ValueError("Phase 5 audit does not authorize Phase 6 continuation")
    for field in (
        "paper_timer_enable_authorized",
        "paper_collection_start_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "new_market_entry_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
    ):
        if audit.get(field) is not False:
            raise ValueError(f"Phase 5 post-promotion audit unexpectedly authorizes {field}")

    if Path(str(audit["production_repository"])).resolve() != production:
        raise ValueError("Phase 6 production repository binding mismatch")

    pio_database = _validate_database_path(
        audit["paper_database_path"],
        label="Phase 6 Pio database",
    )
    expected_pio = (production / "data" / "pio.db").resolve(strict=False)
    if pio_database != expected_pio:
        raise ValueError("Phase 6 Pio database is outside reviewed production path")

    execution_database = _validate_database_path(
        execution_database_path,
        label="Phase 6 execution database",
    )

    pio_before = _database_state(pio_database)
    execution_before = _database_state(execution_database)

    with tempfile.TemporaryDirectory(prefix="pio-phase6-evidence.") as tmp:
        root = Path(tmp)
        pio_snapshot = root / "pio.db"
        execution_snapshot = root / "execution.db"
        _snapshot_sqlite(pio_database, pio_snapshot)
        _snapshot_sqlite(execution_database, execution_snapshot)
        result = _evaluate_phase6(
            source=source,
            pio_database=pio_snapshot,
            execution_database=execution_snapshot,
        )

    pio_after = _database_state(pio_database)
    execution_after = _database_state(execution_database)
    if pio_after != pio_before:
        raise ValueError("production Pio database changed during Phase 6 evaluation")
    if execution_after != execution_before:
        raise ValueError(
            "production execution database changed during Phase 6 evaluation"
        )

    _validate_phase6_result(result)
    promotion_ready = bool(result["promotion_ready"])

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
        "phase5_post_promotion_audit_sha256": audit[
            "post_promotion_audit_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(pio_database),
        "execution_database_path": str(execution_database),
        "pio_database_sha256_before": pio_before["database"],
        "pio_database_sha256_after": pio_after["database"],
        "pio_wal_sha256_before": pio_before["wal"],
        "pio_wal_sha256_after": pio_after["wal"],
        "pio_shm_sha256_before": pio_before["shm"],
        "pio_shm_sha256_after": pio_after["shm"],
        "execution_database_sha256_before": execution_before["database"],
        "execution_database_sha256_after": execution_after["database"],
        "execution_wal_sha256_before": execution_before["wal"],
        "execution_wal_sha256_after": execution_after["wal"],
        "execution_shm_sha256_before": execution_before["shm"],
        "execution_shm_sha256_after": execution_after["shm"],
        "snapshot_only": True,
        "source_databases_unchanged": True,
        "phase5_promotion_confirmed": True,
        "phase6_criteria": dict(EXPECTED_CRITERIA),
        "phase6_criteria_sha256": _sha256_bytes(
            _canonical_bytes(EXPECTED_CRITERIA)
        ),
        "phase6_report": result,
        "phase6_report_sha256": _sha256_bytes(_canonical_bytes(result)),
        "phase5_promoted": bool(result["phase5_promoted"]),
        "passed_enter_intents": int(result["passed_enter_intents"]),
        "distinct_pools": int(result["distinct_pools"]),
        "blocked_intents": int(result["blocked_intents"]),
        "postsimulation_intents": int(result["postsimulation_intents"]),
        "invalid_passed_intents": int(result["invalid_passed_intents"]),
        "distinct_authorized_wallets": list(
            result["distinct_authorized_wallets"]
        ),
        "phase6_promotion_ready": promotion_ready,
        "phase6_reasons": list(result["reasons"]),
        "phase6_evidence_status_ready": True,
        "requires_additional_phase6_evidence": not promotion_ready,
        "requires_separate_phase6_promotion_action": True,
        "phase6_promotion_persisted": False,
        "phase6_promotion_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "production_execution_database_modified": False,
    }
    report = {
        **identity,
        "phase6_evidence_status_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase6_evidence_status(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the reviewed Phase 6 pre-live promotion corpus from "
            "private SQLite snapshots after confirmed Phase 5 promotion. "
            "This tool never persists Phase 6 promotion, enables LIVE submit, "
            "signs/submits transactions, or authorizes live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase5-post-promotion-audit", required=True)
    parser.add_argument("--execution-db", required=True)
    args = parser.parse_args()

    report = build_phase6_evidence_status(
        repository=args.repo,
        source_tree=args.source_tree,
        phase5_post_promotion_audit_path=args.phase5_post_promotion_audit,
        execution_database_path=args.execution_db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
