from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EVIDENCE_STATUS_V1"

PHASE6_POST_PROMOTION_TOOL = Path(
    "deploy/tools/check_phase6_prelive_post_promotion.py"
)
PHASE7_VALIDATION_MODULE = Path(
    "python-learner/src/meteora_learner/phase7_validation.py"
)
LIVE_EXECUTION_AUDIT_MODULE = Path(
    "python-learner/src/meteora_learner/live_execution_audit.py"
)
PHASE_PROMOTION_MODULE = Path(
    "python-learner/src/meteora_learner/phase_promotion.py"
)
STORAGE_MODULE = Path(
    "python-learner/src/meteora_learner/storage.py"
)

REVIEWED_SOURCE_BLOBS = {
    PHASE6_POST_PROMOTION_TOOL: "a62643733bfab622cb8e59777cf31f4720ef6ce0",
    PHASE7_VALIDATION_MODULE: "10c0ed6e4b52524a15279d8e80216a2149d0bc33",
    LIVE_EXECUTION_AUDIT_MODULE: "707c04d4f1fb22d5633284d16121e74d14aa6eb8",
    PHASE_PROMOTION_MODULE: "209cc3b29e3d3759079818b48d08734f050da4ac",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

EXPECTED_CRITERIA = {
    "min_closed_positions": 3,
    "min_distinct_pools": 2,
    "min_confirmed_receipts": 6,
    "max_failed_receipts": 0,
    "max_open_positions_at_validation": 0,
}

EVALUATOR_HELPER = r"""
import json
from pathlib import Path
import sys

from meteora_learner.phase7_validation import (
    Phase7PromotionCriteria,
    evaluate_phase7_promotion,
)
from meteora_learner.storage import Storage

database = Path(sys.argv[1])
storage = Storage(database)
report = evaluate_phase7_promotion(
    storage,
    criteria=Phase7PromotionCriteria(),
)
print(json.dumps(report.to_record(), sort_keys=True))
"""

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "phase6_post_promotion_audit_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "snapshot_only",
    "source_database_unchanged",
    "phase6_promotion_confirmed",
    "phase7_criteria",
    "phase7_criteria_sha256",
    "phase7_report",
    "phase7_report_sha256",
    "phase6_promoted",
    "ledger_audit",
    "ledger_audit_sha256",
    "confirmed_receipts",
    "failed_receipts",
    "closed_positions",
    "open_positions",
    "distinct_closed_pools",
    "valued_closed_positions",
    "labeled_closed_positions",
    "phase7_promotion_ready",
    "phase7_reasons",
    "phase7_evidence_status_ready",
    "requires_additional_controlled_live_evidence",
    "requires_separate_controlled_live_authorization",
    "requires_separate_phase7_promotion_action",
    "phase7_promotion_persisted",
    "phase7_promotion_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "production_source_file_modified",
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
            raise ValueError(f"Phase 7 evidence dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 7 evidence dependency mismatch: {relative}")
    return _load_module(
        source / PHASE6_POST_PROMOTION_TOOL,
        "phase7_evidence_phase6_post_promotion",
    )


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 7 database sidecar is unsafe: {path}")
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


def _evaluate_phase7(
    *,
    source: Path,
    pio_database: Path,
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
        raise ValueError("Phase 7 evidence evaluator failed")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Phase 7 evidence evaluator returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Phase 7 evidence evaluator result is invalid")
    return value


def _validate_phase7_result(result: dict[str, Any]) -> None:
    if result.get("criteria") != EXPECTED_CRITERIA:
        raise ValueError("Phase 7 criteria drifted from reviewed defaults")
    if result.get("phase6_promoted") is not True:
        raise ValueError("Phase 7 evidence requires persisted Phase 6 promotion")

    ledger = result.get("ledger_audit")
    if not isinstance(ledger, dict):
        raise ValueError("Phase 7 ledger audit result is invalid")
    if not isinstance(ledger.get("clean"), bool):
        raise ValueError("Phase 7 ledger audit clean flag is invalid")

    for field in (
        "confirmed_receipts",
        "failed_receipts",
        "closed_positions",
        "open_positions",
        "distinct_closed_pools",
        "valued_closed_positions",
        "labeled_closed_positions",
    ):
        value = result.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 7 result {field} is invalid")

    if not isinstance(result.get("promotion_ready"), bool):
        raise ValueError("Phase 7 promotion-ready result is invalid")
    reasons = result.get("reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("Phase 7 reasons are invalid")
    if result["promotion_ready"] is not (len(reasons) == 0):
        raise ValueError("Phase 7 promotion-ready/reasons mismatch")

    if result["promotion_ready"]:
        if ledger["clean"] is not True:
            raise ValueError("ready Phase 7 evidence requires a clean ledger")
        if result["failed_receipts"] != 0:
            raise ValueError("ready Phase 7 evidence requires zero failed receipts")
        if result["open_positions"] != 0:
            raise ValueError("ready Phase 7 evidence requires zero open positions")
        if result["valued_closed_positions"] != result["closed_positions"]:
            raise ValueError("ready Phase 7 evidence requires full valuation coverage")
        if result["labeled_closed_positions"] != result["closed_positions"]:
            raise ValueError("ready Phase 7 evidence requires full label coverage")


def validate_phase7_evidence_status(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 evidence status must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"phase7_evidence_status_sha256"}:
        raise ValueError("Phase 7 evidence status schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 evidence status format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 evidence status type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 evidence status source lineage mismatch")

    for field in (
        "phase6_post_promotion_audit_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "phase7_criteria_sha256",
        "phase7_report_sha256",
        "ledger_audit_sha256",
        "phase7_evidence_status_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 evidence status {field} is invalid")

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 7 evidence status {field} is invalid")

    for field in ("production_repository", "pio_database_path"):
        value = report.get(field)
        if not isinstance(value, str) or not value.startswith("/"):
            raise ValueError(f"Phase 7 evidence status {field} is invalid")

    if report.get("phase7_criteria") != EXPECTED_CRITERIA:
        raise ValueError("Phase 7 evidence status criteria mismatch")
    if report["phase7_criteria_sha256"] != _sha256_bytes(
        _canonical_bytes(report["phase7_criteria"])
    ):
        raise ValueError("Phase 7 criteria digest mismatch")

    nested = report.get("phase7_report")
    if not isinstance(nested, dict):
        raise ValueError("Phase 7 nested report is invalid")
    _validate_phase7_result(nested)
    if report["phase7_report_sha256"] != _sha256_bytes(
        _canonical_bytes(nested)
    ):
        raise ValueError("Phase 7 report digest mismatch")

    ledger = report.get("ledger_audit")
    if not isinstance(ledger, dict):
        raise ValueError("Phase 7 evidence status ledger audit is invalid")
    if report["ledger_audit_sha256"] != _sha256_bytes(
        _canonical_bytes(ledger)
    ):
        raise ValueError("Phase 7 ledger audit digest mismatch")

    bindings = (
        ("phase6_promoted", "phase6_promoted"),
        ("ledger_audit", "ledger_audit"),
        ("confirmed_receipts", "confirmed_receipts"),
        ("failed_receipts", "failed_receipts"),
        ("closed_positions", "closed_positions"),
        ("open_positions", "open_positions"),
        ("distinct_closed_pools", "distinct_closed_pools"),
        ("valued_closed_positions", "valued_closed_positions"),
        ("labeled_closed_positions", "labeled_closed_positions"),
        ("phase7_promotion_ready", "promotion_ready"),
        ("phase7_reasons", "reasons"),
    )
    for outer, inner in bindings:
        if report.get(outer) != nested.get(inner):
            raise ValueError(f"Phase 7 evidence status {outer} binding mismatch")

    if report["pio_database_sha256_before"] != report["pio_database_sha256_after"]:
        raise ValueError("Phase 7 Pio database changed during evaluation")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("Phase 7 Pio WAL changed during evaluation")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("Phase 7 Pio SHM changed during evaluation")

    for field in (
        "snapshot_only",
        "source_database_unchanged",
        "phase6_promotion_confirmed",
        "phase7_evidence_status_ready",
        "requires_separate_controlled_live_authorization",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 evidence status requires {field}=true")

    expected_more = not report["phase7_promotion_ready"]
    if report.get("requires_additional_controlled_live_evidence") is not expected_more:
        raise ValueError("Phase 7 additional-evidence flag mismatch")

    for field in (
        "phase7_promotion_persisted",
        "phase7_promotion_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 7 evidence status requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["phase7_evidence_status_sha256"] != expected:
        raise ValueError("Phase 7 evidence status digest mismatch")


def build_phase7_evidence_status(
    *,
    repository: str | Path,
    source_tree: str | Path,
    phase6_post_promotion_audit_path: str | Path,
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
        phase6_post_promotion_audit_path,
        label="Phase 6 post-promotion audit",
    )
    audit_module.validate_post_promotion_audit(audit)

    if audit.get("post_promotion_audit_ready") is not True:
        raise ValueError("Phase 6 post-promotion audit is not ready")
    if audit.get("phase6_promotion_confirmed") is not True:
        raise ValueError("Phase 6 promotion is not confirmed")
    if audit.get("phase6_promotion_persisted") is not True:
        raise ValueError("Phase 6 promotion is not persisted")
    if audit.get("requires_separate_phase7_workflow") is not True:
        raise ValueError("Phase 6 audit does not expose the Phase 7 boundary")

    for field in (
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
    ):
        if audit.get(field) is not False:
            raise ValueError(f"Phase 6 post-promotion audit unexpectedly authorizes {field}")

    if Path(str(audit["production_repository"])).resolve() != production:
        raise ValueError("Phase 7 production repository binding mismatch")

    pio_database = _validate_database_path(
        audit["pio_database_path"],
        label="Phase 7 Pio database",
    )
    expected_pio = (production / "data" / "pio.db").resolve(strict=False)
    if pio_database != expected_pio:
        raise ValueError("Phase 7 Pio database is outside reviewed production path")

    before = _database_state(pio_database)

    with tempfile.TemporaryDirectory(prefix="pio-phase7-evidence.") as tmp:
        snapshot = Path(tmp) / "pio.db"
        _snapshot_sqlite(pio_database, snapshot)
        result = _evaluate_phase7(
            source=source,
            pio_database=snapshot,
        )

    after = _database_state(pio_database)
    if after != before:
        raise ValueError("production Pio database changed during Phase 7 evaluation")

    _validate_phase7_result(result)
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
        "phase6_post_promotion_audit_sha256": audit[
            "post_promotion_audit_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(pio_database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "snapshot_only": True,
        "source_database_unchanged": True,
        "phase6_promotion_confirmed": True,
        "phase7_criteria": dict(EXPECTED_CRITERIA),
        "phase7_criteria_sha256": _sha256_bytes(
            _canonical_bytes(EXPECTED_CRITERIA)
        ),
        "phase7_report": result,
        "phase7_report_sha256": _sha256_bytes(_canonical_bytes(result)),
        "phase6_promoted": bool(result["phase6_promoted"]),
        "ledger_audit": result["ledger_audit"],
        "ledger_audit_sha256": _sha256_bytes(
            _canonical_bytes(result["ledger_audit"])
        ),
        "confirmed_receipts": int(result["confirmed_receipts"]),
        "failed_receipts": int(result["failed_receipts"]),
        "closed_positions": int(result["closed_positions"]),
        "open_positions": int(result["open_positions"]),
        "distinct_closed_pools": int(result["distinct_closed_pools"]),
        "valued_closed_positions": int(result["valued_closed_positions"]),
        "labeled_closed_positions": int(result["labeled_closed_positions"]),
        "phase7_promotion_ready": promotion_ready,
        "phase7_reasons": list(result["reasons"]),
        "phase7_evidence_status_ready": True,
        "requires_additional_controlled_live_evidence": not promotion_ready,
        "requires_separate_controlled_live_authorization": True,
        "requires_separate_phase7_promotion_action": True,
        "phase7_promotion_persisted": False,
        "phase7_promotion_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "phase7_evidence_status_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase7_evidence_status(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the reviewed Phase 7 controlled-LIVE promotion criteria "
            "from a private snapshot after confirmed Phase 6 promotion. "
            "This tool never authorizes controlled LIVE, live-submit, signing, "
            "transaction submission, Phase 7 persistence, or live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase6-post-promotion-audit", required=True)
    args = parser.parse_args()

    report = build_phase7_evidence_status(
        repository=args.repo,
        source_tree=args.source_tree,
        phase6_post_promotion_audit_path=args.phase6_post_promotion_audit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
