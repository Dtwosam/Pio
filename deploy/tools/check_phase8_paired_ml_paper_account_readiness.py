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
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE8_PAIRED_ML_PAPER_ACCOUNT_READINESS_V1"

PAIR_INPUT_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_entry_inputs.py"
)
REVIEWED_SOURCE_BLOBS = {
    PAIR_INPUT_TOOL: "2b30f230ea8489ffcb952873e04e2e62acb7f5cb",
}

STATUS_READY = "READY"
STATUS_ACCOUNT_CREATION_REQUIRED = "ACCOUNT_CREATION_REQUIRED"
STATUS_EXISTING_ACCOUNT_MISSING = "EXISTING_ACCOUNT_MISSING"
STATUS_ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
STATUS_INSUFFICIENT_CASH = "INSUFFICIENT_PAPER_CASH"
STATUS_ID_CONFLICT = "PAIR_ID_CONFLICT"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "paired_entry_input_verification_sha256",
    "source_post_audit_sha256",
    "source_paper_evidence_input_verification_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "model_id",
    "active_cycle_id",
    "account_mode",
    "account_id",
    "requested_starting_cash_quote",
    "pair_id",
    "pool_address",
    "capital_quote",
    "entry_cost_quote",
    "required_pair_cash_quote",
    "incumbent_position_id",
    "challenger_position_id",
    "incumbent_event_key",
    "challenger_event_key",
    "account_exists",
    "account_starting_equity_quote",
    "account_cash_quote",
    "account_open_positions",
    "account_closed_positions",
    "starting_equity_matches_request",
    "paper_cash_sufficient_for_pair",
    "pair_position_ids_available",
    "pair_event_keys_available",
    "cycle_status",
    "cycle_active_key",
    "incumbent_model_id",
    "incumbent_model_status",
    "challenger_model_id",
    "challenger_model_status",
    "cycle_model_binding_valid",
    "readiness_status",
    "account_creation_required",
    "account_ready",
    "paired_entry_preconditions_ready",
    "readiness_only",
    "requires_separate_account_creation_authorization",
    "requires_separate_paired_entry_authorization",
    "requires_fresh_pair_execution_readiness",
    "paper_account_creation_authorized",
    "paper_pair_entry_authorized",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "new_live_capital_authorized",
    "phase8_promotion_authorized",
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


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


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


def _load_pair_input_module(source: Path) -> Any:
    path = source / PAIR_INPUT_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed paired PAPER input tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[PAIR_INPUT_TOOL]:
        raise ValueError("reviewed paired PAPER input tool blob mismatch")
    return _load_module(path, "phase8_paired_paper_account_input")


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"unsafe Pio database state file: {path}")
    return _sha256_path(path)


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _production_database(production: Path) -> Path:
    data = production / "data"
    if data.is_symlink() or not data.is_dir():
        raise ValueError("Phase 8 paired PAPER data directory is unsafe")
    database = data / "pio.db"
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError("Phase 8 paired PAPER database is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("Phase 8 paired PAPER database is unsafe")
    return database.resolve()


def _account_state(
    conn: sqlite3.Connection,
    account_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT starting_equity_quote, cash_quote
        FROM paper_accounts
        WHERE account_id = ?
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    if row is None:
        return None
    positions = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END),
            SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END)
        FROM paper_positions
        WHERE account_id = ?
        """,
        (account_id,),
    ).fetchone()
    return {
        "starting_equity_quote": float(row[0]),
        "cash_quote": float(row[1]),
        "open_positions": int(positions[0] or 0),
        "closed_positions": int(positions[1] or 0),
    }


def _pair_ids_available(
    conn: sqlite3.Connection,
    *,
    incumbent_position_id: str,
    challenger_position_id: str,
    incumbent_event_key: str,
    challenger_event_key: str,
) -> tuple[bool, bool]:
    position_conflict = conn.execute(
        """
        SELECT 1
        FROM paper_positions
        WHERE position_id IN (?, ?)
        LIMIT 1
        """,
        (incumbent_position_id, challenger_position_id),
    ).fetchone()
    event_conflict = conn.execute(
        """
        SELECT 1
        FROM paper_events
        WHERE event_key IN (?, ?)
        LIMIT 1
        """,
        (incumbent_event_key, challenger_event_key),
    ).fetchone()
    return position_conflict is None, event_conflict is None


def _cycle_state(
    conn: sqlite3.Connection,
    cycle_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT status, active_key, champion_model_id, challenger_model_id
        FROM continuous_learning_cycles
        WHERE cycle_id = ?
        """,
        (cycle_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown retraining cycle: {cycle_id}")
    incumbent_id = str(row[2])
    challenger_id = str(row[3]) if row[3] is not None else None
    statuses = dict(
        conn.execute(
            """
            SELECT model_id, status
            FROM model_registry
            WHERE model_id IN (?, ?)
            """,
            (incumbent_id, challenger_id or ""),
        ).fetchall()
    )
    return {
        "cycle_status": str(row[0]),
        "cycle_active_key": str(row[1]) if row[1] is not None else None,
        "incumbent_model_id": incumbent_id,
        "incumbent_model_status": statuses.get(incumbent_id),
        "challenger_model_id": challenger_id,
        "challenger_model_status": statuses.get(challenger_id),
    }


def validate_phase8_paired_ml_paper_account_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 8 paired PAPER account readiness must be an object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("Phase 8 paired PAPER account readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 8 paired PAPER readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 8 paired PAPER readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 8 paired PAPER readiness lineage mismatch")

    for field in (
        "paired_entry_input_verification_sha256",
        "source_post_audit_sha256",
        "source_paper_evidence_input_verification_sha256",
        "pio_database_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 8 paired PAPER readiness {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 8 paired PAPER readiness {field} is invalid"
            )

    allowed_statuses = {
        STATUS_READY,
        STATUS_ACCOUNT_CREATION_REQUIRED,
        STATUS_EXISTING_ACCOUNT_MISSING,
        STATUS_ACCOUNT_MISMATCH,
        STATUS_INSUFFICIENT_CASH,
        STATUS_ID_CONFLICT,
    }
    if report.get("readiness_status") not in allowed_statuses:
        raise ValueError("Phase 8 paired PAPER readiness status is invalid")

    for field in (
        "readiness_only",
        "requires_separate_paired_entry_authorization",
        "requires_fresh_pair_execution_readiness",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 8 paired PAPER readiness requires {field}=true"
            )
    for field in (
        "paper_account_creation_authorized",
        "paper_pair_entry_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 8 paired PAPER readiness requires {field}=false"
            )

    ready = report["readiness_status"] == STATUS_READY
    if report.get("account_ready") is not ready:
        raise ValueError("Phase 8 paired PAPER account_ready/status mismatch")
    if report.get("paired_entry_preconditions_ready") is not ready:
        raise ValueError(
            "Phase 8 paired PAPER preconditions/status mismatch"
        )
    creation_required = (
        report["readiness_status"] == STATUS_ACCOUNT_CREATION_REQUIRED
    )
    if report.get("account_creation_required") is not creation_required:
        raise ValueError(
            "Phase 8 paired PAPER account-creation/status mismatch"
        )
    if (
        report.get("requires_separate_account_creation_authorization")
        is not creation_required
    ):
        raise ValueError(
            "Phase 8 paired PAPER account-creation authorization mismatch"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("Phase 8 paired PAPER readiness digest mismatch")


def build_phase8_paired_ml_paper_account_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    post_audit_path: str | Path,
    paper_evidence_input_path: str | Path,
    paired_entry_input_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    module = _load_pair_input_module(source)
    verification = module.verify_phase8_paired_ml_paper_entry_inputs(
        source_tree=source,
        post_audit_path=post_audit_path,
        paper_evidence_input_path=paper_evidence_input_path,
        input_path=paired_entry_input_path,
    )
    if verification.get("paired_entry_inputs_ready") is not True:
        raise ValueError("Phase 8 paired PAPER entry inputs are not ready")
    if Path(str(verification["production_repository"])).resolve() != production:
        raise ValueError("Phase 8 paired PAPER repository binding mismatch")

    database = _production_database(production)
    if Path(str(verification["pio_database_path"])).resolve() != database:
        raise ValueError("Phase 8 paired PAPER database binding mismatch")
    before = _database_state(database)

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        account = _account_state(conn, verification["account_id"])
        ids_available, events_available = _pair_ids_available(
            conn,
            incumbent_position_id=verification["incumbent_position_id"],
            challenger_position_id=verification["challenger_position_id"],
            incumbent_event_key=verification["incumbent_event_key"],
            challenger_event_key=verification["challenger_event_key"],
        )
        cycle = _cycle_state(conn, verification["active_cycle_id"])
    finally:
        conn.close()

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "Phase 8 paired PAPER account readiness changed production database"
        )

    if (
        cycle["cycle_status"] != "PAPER_CHALLENGER"
        or cycle["cycle_active_key"] != "ACTIVE"
        or cycle["challenger_model_id"] != verification["model_id"]
        or cycle["incumbent_model_status"] != "CHAMPION"
        or cycle["challenger_model_status"] != "PAPER_CHALLENGER"
    ):
        raise ValueError(
            "Phase 8 paired PAPER cycle/model binding is no longer valid"
        )

    requested_cash = verification["starting_cash_quote"]
    account_exists = account is not None
    starting_matches = (
        account_exists
        and (
            verification["account_mode"] != "CREATE"
            or float(account["starting_equity_quote"]) == float(requested_cash)
        )
    )
    required_cash = 2.0 * (
        float(verification["capital_quote"])
        + float(verification["entry_cost_quote"])
    )
    cash_sufficient = (
        account_exists and float(account["cash_quote"]) >= required_cash
    )

    if not ids_available or not events_available:
        status = STATUS_ID_CONFLICT
    elif not account_exists and verification["account_mode"] == "CREATE":
        status = STATUS_ACCOUNT_CREATION_REQUIRED
    elif not account_exists:
        status = STATUS_EXISTING_ACCOUNT_MISSING
    elif not starting_matches:
        status = STATUS_ACCOUNT_MISMATCH
    elif not cash_sufficient:
        status = STATUS_INSUFFICIENT_CASH
    else:
        status = STATUS_READY

    creation_required = status == STATUS_ACCOUNT_CREATION_REQUIRED
    ready = status == STATUS_READY

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
        "paired_entry_input_verification_sha256": verification[
            "verification_sha256"
        ],
        "source_post_audit_sha256": verification[
            "source_post_audit_sha256"
        ],
        "source_paper_evidence_input_verification_sha256": verification[
            "source_paper_evidence_input_verification_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": before["database"],
        "pio_wal_sha256": before["wal"],
        "pio_shm_sha256": before["shm"],
        "model_id": verification["model_id"],
        "active_cycle_id": verification["active_cycle_id"],
        "account_mode": verification["account_mode"],
        "account_id": verification["account_id"],
        "requested_starting_cash_quote": requested_cash,
        "pair_id": verification["pair_id"],
        "pool_address": verification["pool_address"],
        "capital_quote": verification["capital_quote"],
        "entry_cost_quote": verification["entry_cost_quote"],
        "required_pair_cash_quote": required_cash,
        "incumbent_position_id": verification["incumbent_position_id"],
        "challenger_position_id": verification["challenger_position_id"],
        "incumbent_event_key": verification["incumbent_event_key"],
        "challenger_event_key": verification["challenger_event_key"],
        "account_exists": account_exists,
        "account_starting_equity_quote": (
            account["starting_equity_quote"] if account else None
        ),
        "account_cash_quote": account["cash_quote"] if account else None,
        "account_open_positions": account["open_positions"] if account else 0,
        "account_closed_positions": account["closed_positions"] if account else 0,
        "starting_equity_matches_request": bool(starting_matches),
        "paper_cash_sufficient_for_pair": bool(cash_sufficient),
        "pair_position_ids_available": ids_available,
        "pair_event_keys_available": events_available,
        **cycle,
        "cycle_model_binding_valid": True,
        "readiness_status": status,
        "account_creation_required": creation_required,
        "account_ready": ready,
        "paired_entry_preconditions_ready": ready,
        "readiness_only": True,
        "requires_separate_account_creation_authorization": creation_required,
        "requires_separate_paired_entry_authorization": True,
        "requires_fresh_pair_execution_readiness": True,
        "paper_account_creation_authorized": False,
        "paper_pair_entry_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_account_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only readiness check for the explicit Phase 8 paired ML "
            "PAPER account and pair ids. Missing CREATE accounts are reported "
            "as a separate creation dependency; nothing is created here."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-audit", required=True)
    parser.add_argument("--paper-evidence-inputs", required=True)
    parser.add_argument("--paired-entry-inputs", required=True)
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_account_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        post_audit_path=args.post_audit,
        paper_evidence_input_path=args.paper_evidence_inputs,
        paired_entry_input_path=args.paired_entry_inputs,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
