from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PHASE5_EVIDENCE_STATUS_V1"

POST_CYCLE_AUDIT_TOOL = Path(
    "deploy/tools/check_manual_market_paper_one_cycle_post_cycle.py"
)
PHASE5_VALIDATION_MODULE = Path(
    "python-learner/src/meteora_learner/phase5_validation.py"
)
PAPER_ENDURANCE_MODULE = Path(
    "python-learner/src/meteora_learner/paper_endurance.py"
)
PAPER_AUDIT_MODULE = Path(
    "python-learner/src/meteora_learner/paper_audit.py"
)
PHASE_PROMOTION_MODULE = Path(
    "python-learner/src/meteora_learner/phase_promotion.py"
)
STORAGE_MODULE = Path(
    "python-learner/src/meteora_learner/storage.py"
)

REVIEWED_SOURCE_BLOBS = {
    POST_CYCLE_AUDIT_TOOL: "0cfc64b99284f8e5c4a7cba3fdaa868a34dd7655",
    PHASE5_VALIDATION_MODULE: "460ced4f73d60f415767de359e9a54a1e28271e1",
    PAPER_ENDURANCE_MODULE: "d3ed9ce6b5c096f3bf7c2ab57c78c943d0eebd6d",
    PAPER_AUDIT_MODULE: "f58e27d7a8c629aca1235daf3b49134e3a63ad25",
    PHASE_PROMOTION_MODULE: "209cc3b29e3d3759079818b48d08734f050da4ac",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

INSPECTION_TIMEOUT_SECONDS = 45

EXPECTED_PHASE5_CRITERIA = {
    "min_runtime_hours": 72.0,
    "min_terminal_ticks": 500,
    "min_success_rate_pct": 99.0,
    "max_dependency_blocked_pct": 5.0,
    "max_consecutive_failures": 1,
    "max_stale_running_ticks": 0,
    "stale_running_after_seconds": 900,
    "min_applied_chain_valuations": 100,
    "min_distinct_positions_valued": 3,
    "min_closed_positions": 3,
    "min_distinct_pools": 2,
}

PHASE5_EVIDENCE_HELPER = r"""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from meteora_learner.phase5_validation import (
    Phase5PromotionCriteria,
    evaluate_phase5_promotion,
)
from meteora_learner.storage import Storage

database = Path(sys.argv[1])
account = sys.argv[2]

with tempfile.TemporaryDirectory(prefix="pio-phase5-evidence.") as tmp:
    snapshot = Path(tmp) / "pio.db"

    source = sqlite3.connect(
        f"file:{database}?mode=ro",
        uri=True,
    )
    try:
        source.execute("PRAGMA query_only=ON")
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    storage = Storage(snapshot)
    report = evaluate_phase5_promotion(
        storage,
        account_id=account,
        criteria=Phase5PromotionCriteria(),
    )
    print(json.dumps(report.to_record(), sort_keys=True))
"""

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "post_cycle_audit_sha256",
    "one_cycle_receipt_sha256",
    "production_repository",
    "paper_database_path",
    "account",
    "run_id",
    "database_sha256_before",
    "database_sha256_after",
    "wal_sha256_before",
    "wal_sha256_after",
    "database_snapshot_only",
    "source_database_unchanged",
    "source_wal_unchanged",
    "phase5_criteria",
    "phase5_criteria_sha256",
    "phase3_promoted",
    "endurance",
    "endurance_sha256",
    "ledger_audit",
    "ledger_audit_sha256",
    "closed_positions",
    "distinct_valued_pools",
    "phase5_promotion_ready",
    "phase5_reasons",
    "phase5_evidence_status_ready",
    "requires_additional_paper_evidence",
    "requires_separate_phase5_promotion_action",
    "requires_separate_timer_authorization",
    "phase5_promotion_persisted",
    "phase5_promotion_authorized",
    "recurring_paper_automation_authorized",
    "paper_timer_enable_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_paper_database_modified",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _git_blob_sha(path: Path) -> str:
    return _git_blob_sha_bytes(path.read_bytes())


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
            raise ValueError(f"Phase 5 evidence dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 5 evidence dependency mismatch: {relative}")
    return _load_module(
        source / POST_CYCLE_AUDIT_TOOL,
        "manual_market_paper_phase5_evidence_post_cycle",
    )


def _paper_database(production: Path, expected: str) -> Path:
    expected_path = Path(expected)
    if not expected_path.is_absolute():
        raise ValueError("Phase 5 PAPER database path must be absolute")
    database = production / "data" / "pio.db"
    if expected_path.resolve(strict=False) != database.resolve(strict=False):
        raise ValueError("Phase 5 PAPER database binding mismatch")
    data_dir = production / "data"
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError("Phase 5 PAPER data directory is unsafe")
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError("Phase 5 PAPER database is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("Phase 5 PAPER database must be a regular file")
    return database


def _sha256_regular_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Phase 5 evidence sidecar is unsafe: {path.name}")
    return _sha256_bytes(path.read_bytes())


def _evaluate_phase5(
    *,
    source: Path,
    database: Path,
    account: str,
) -> dict[str, Any]:
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(source / "python-learner" / "src")
    env["PIO_DATABASE_PATH"] = str(database)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            PHASE5_EVIDENCE_HELPER,
            str(database),
            account,
        ],
        cwd=source,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=INSPECTION_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise ValueError("Phase 5 PAPER evidence evaluation failed")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Phase 5 evidence evaluator returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Phase 5 evidence evaluator result is invalid")
    return value


def _validate_phase5_result(result: dict[str, Any], *, account: str) -> None:
    if result.get("account_id") != account:
        raise ValueError("Phase 5 evidence account binding mismatch")
    if not isinstance(result.get("phase3_promoted"), bool):
        raise ValueError("Phase 5 evidence phase3_promoted is invalid")

    criteria = result.get("criteria")
    if criteria != EXPECTED_PHASE5_CRITERIA:
        raise ValueError("Phase 5 evidence criteria drifted from reviewed defaults")

    endurance = result.get("endurance")
    ledger = result.get("ledger_audit")
    if not isinstance(endurance, dict) or not isinstance(ledger, dict):
        raise ValueError("Phase 5 evidence nested reports are invalid")
    if not isinstance(endurance.get("passing"), bool):
        raise ValueError("Phase 5 endurance passing flag is invalid")
    if not isinstance(ledger.get("passing"), bool):
        raise ValueError("Phase 5 ledger passing flag is invalid")

    for field in ("closed_positions", "distinct_valued_pools"):
        value = result.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 5 evidence {field} is invalid")

    if not isinstance(result.get("promotion_ready"), bool):
        raise ValueError("Phase 5 promotion_ready is invalid")
    reasons = result.get("reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("Phase 5 evidence reasons are invalid")
    if result["promotion_ready"] is not (len(reasons) == 0):
        raise ValueError("Phase 5 promotion-ready/reasons mismatch")


def validate_phase5_evidence_status(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 5 evidence status must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"phase5_evidence_status_sha256"}:
        raise ValueError("Phase 5 evidence status schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 5 evidence status format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 5 evidence status type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 5 evidence source lineage mismatch")

    for field in (
        "post_cycle_audit_sha256",
        "one_cycle_receipt_sha256",
        "database_sha256_before",
        "database_sha256_after",
        "phase5_criteria_sha256",
        "endurance_sha256",
        "ledger_audit_sha256",
        "phase5_evidence_status_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 5 evidence {field} is invalid")

    for field in ("wal_sha256_before", "wal_sha256_after"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 5 evidence {field} is invalid")

    for field in (
        "production_repository",
        "paper_database_path",
        "account",
        "run_id",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 5 evidence {field} is invalid")

    if report.get("phase5_criteria") != EXPECTED_PHASE5_CRITERIA:
        raise ValueError("Phase 5 evidence criteria mismatch")
    if report["phase5_criteria_sha256"] != _sha256_bytes(
        _canonical_bytes(report["phase5_criteria"])
    ):
        raise ValueError("Phase 5 evidence criteria digest mismatch")

    endurance = report.get("endurance")
    ledger = report.get("ledger_audit")
    if not isinstance(endurance, dict) or not isinstance(ledger, dict):
        raise ValueError("Phase 5 evidence nested report schema mismatch")
    if report["endurance_sha256"] != _sha256_bytes(_canonical_bytes(endurance)):
        raise ValueError("Phase 5 endurance digest mismatch")
    if report["ledger_audit_sha256"] != _sha256_bytes(_canonical_bytes(ledger)):
        raise ValueError("Phase 5 ledger digest mismatch")

    for field in ("closed_positions", "distinct_valued_pools"):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Phase 5 evidence {field} is invalid")

    if not isinstance(report.get("phase3_promoted"), bool):
        raise ValueError("Phase 5 evidence phase3_promoted is invalid")
    if not isinstance(report.get("phase5_promotion_ready"), bool):
        raise ValueError("Phase 5 evidence promotion-ready flag is invalid")
    reasons = report.get("phase5_reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("Phase 5 evidence reasons are invalid")
    if report["phase5_promotion_ready"] is not (len(reasons) == 0):
        raise ValueError("Phase 5 evidence promotion-ready/reasons mismatch")

    for field in (
        "database_snapshot_only",
        "source_database_unchanged",
        "source_wal_unchanged",
        "phase5_evidence_status_ready",
        "requires_separate_phase5_promotion_action",
        "requires_separate_timer_authorization",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 5 evidence requires {field}=true")

    expected_additional = not report["phase5_promotion_ready"]
    if report.get("requires_additional_paper_evidence") is not expected_additional:
        raise ValueError("Phase 5 additional-evidence flag mismatch")

    for field in (
        "phase5_promotion_persisted",
        "phase5_promotion_authorized",
        "recurring_paper_automation_authorized",
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_paper_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(f"Phase 5 evidence requires {field}=false")

    if report["database_sha256_before"] != report["database_sha256_after"]:
        raise ValueError("Phase 5 evidence source database changed")
    if report["wal_sha256_before"] != report["wal_sha256_after"]:
        raise ValueError("Phase 5 evidence source WAL changed")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["phase5_evidence_status_sha256"] != expected:
        raise ValueError("Phase 5 evidence status digest mismatch")


def build_phase5_evidence_status(
    *,
    repository: str | Path,
    source_tree: str | Path,
    post_cycle_audit_path: str | Path,
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
        post_cycle_audit_path,
        label="one-cycle post-cycle audit",
    )
    audit_module.validate_post_cycle_audit(audit)

    if audit.get("post_cycle_audit_ready") is not True:
        raise ValueError("one-cycle post-cycle audit is not ready")
    for field in (
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
    ):
        if audit.get(field) is not False:
            raise ValueError(f"post-cycle audit unexpectedly authorizes {field}")
    if Path(str(audit["production_repository"])).resolve() != production:
        raise ValueError("Phase 5 evidence production repository binding mismatch")

    database = _paper_database(
        production,
        str(audit["paper_database_path"]),
    )
    wal = Path(str(database) + "-wal")

    database_before = _sha256_regular_or_none(database)
    if database_before is None:
        raise ValueError("Phase 5 PAPER database disappeared")
    wal_before = _sha256_regular_or_none(wal)

    result = _evaluate_phase5(
        source=source,
        database=database,
        account=str(audit["account"]),
    )
    _validate_phase5_result(result, account=str(audit["account"]))

    database_after = _sha256_regular_or_none(database)
    wal_after = _sha256_regular_or_none(wal)
    if database_after != database_before:
        raise ValueError("production PAPER database changed during Phase 5 evaluation")
    if wal_after != wal_before:
        raise ValueError("production PAPER WAL changed during Phase 5 evaluation")

    criteria = result["criteria"]
    endurance = result["endurance"]
    ledger = result["ledger_audit"]
    promotion_ready = bool(result["promotion_ready"])
    reasons = list(result["reasons"])

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
        "post_cycle_audit_sha256": audit["post_cycle_audit_sha256"],
        "one_cycle_receipt_sha256": audit["one_cycle_receipt_sha256"],
        "production_repository": str(production),
        "paper_database_path": str(database),
        "account": audit["account"],
        "run_id": audit["run_id"],
        "database_sha256_before": database_before,
        "database_sha256_after": database_after,
        "wal_sha256_before": wal_before,
        "wal_sha256_after": wal_after,
        "database_snapshot_only": True,
        "source_database_unchanged": True,
        "source_wal_unchanged": True,
        "phase5_criteria": criteria,
        "phase5_criteria_sha256": _sha256_bytes(_canonical_bytes(criteria)),
        "phase3_promoted": bool(result["phase3_promoted"]),
        "endurance": endurance,
        "endurance_sha256": _sha256_bytes(_canonical_bytes(endurance)),
        "ledger_audit": ledger,
        "ledger_audit_sha256": _sha256_bytes(_canonical_bytes(ledger)),
        "closed_positions": int(result["closed_positions"]),
        "distinct_valued_pools": int(result["distinct_valued_pools"]),
        "phase5_promotion_ready": promotion_ready,
        "phase5_reasons": reasons,
        "phase5_evidence_status_ready": True,
        "requires_additional_paper_evidence": not promotion_ready,
        "requires_separate_phase5_promotion_action": True,
        "requires_separate_timer_authorization": True,
        "phase5_promotion_persisted": False,
        "phase5_promotion_authorized": False,
        "recurring_paper_automation_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified": False,
    }
    report = {
        **identity,
        "phase5_evidence_status_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase5_evidence_status(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the exact reviewed Phase 5 PAPER promotion criteria "
            "against a private snapshot of the production PAPER database after "
            "a successful one-cycle proof. This tool never persists promotion, "
            "enables recurring automation, starts/restarts services, moves "
            "detector state, signs/submits transactions, or uses live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-cycle-audit", required=True)
    args = parser.parse_args()

    report = build_phase5_evidence_status(
        repository=args.repo,
        source_tree=args.source_tree,
        post_cycle_audit_path=args.post_cycle_audit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
