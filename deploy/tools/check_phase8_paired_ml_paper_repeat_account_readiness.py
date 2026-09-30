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
ARTIFACT_TYPE = "PHASE8_PAIRED_ML_PAPER_REPEAT_ACCOUNT_READINESS_V1"

REPEAT_INPUT_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_repeat_entry_inputs.py"
)
ACCOUNT_READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_account_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    REPEAT_INPUT_TOOL: "44e43660b417c099e4272eecc9837b29faec2004",
    ACCOUNT_READINESS_TOOL: "076a9adfd2a80f959c238e27931785ed87d0fa08",
}

STATUS_READY = "READY"
STATUS_ACCOUNT_MISSING = "ACCOUNT_MISSING"
STATUS_INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
STATUS_OPEN_POSITIONS_PRESENT = "OPEN_POSITIONS_PRESENT"
STATUS_ID_CONFLICT = "ID_CONFLICT"
STATUS_PREVIOUS_PAIR_DRIFT = "PREVIOUS_PAIR_DRIFT"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "repeat_entry_input_verification_sha256",
    "source_final_evaluation_sha256",
    "production_repository",
    "pio_database_path",
    "source_pio_database_sha256",
    "current_pio_database_sha256",
    "current_pio_wal_sha256",
    "current_pio_shm_sha256",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "previous_pair_id",
    "previous_incumbent_position_id",
    "previous_challenger_position_id",
    "previous_pair_positions_closed",
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
    "zero_open_positions_verified",
    "paper_cash_sufficient_for_pair",
    "pair_position_ids_available",
    "pair_event_keys_available",
    "cycle_status",
    "cycle_active_key",
    "fresh_incumbent_model_id",
    "fresh_incumbent_model_status",
    "fresh_challenger_model_id",
    "fresh_challenger_model_status",
    "cycle_model_binding_valid",
    "readiness_status",
    "account_ready",
    "repeat_pair_preconditions_ready",
    "readiness_only",
    "requires_separate_paired_entry_authorization",
    "requires_fresh_pair_execution_readiness",
    "paper_pair_entry_authorized",
    "paper_pair_entry_executed",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
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


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"repeat paired PAPER readiness dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"repeat paired PAPER readiness dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / REPEAT_INPUT_TOOL,
            "phase8_repeat_pair_account_input",
        ),
        _load_module(
            source / ACCOUNT_READINESS_TOOL,
            "phase8_repeat_pair_account_base",
        ),
    )


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


def validate_phase8_paired_ml_paper_repeat_account_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("repeat paired PAPER readiness must be an object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("repeat paired PAPER readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported repeat paired PAPER readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected repeat paired PAPER readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("repeat paired PAPER readiness lineage mismatch")

    for field in (
        "repeat_entry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "source_pio_database_sha256",
        "current_pio_database_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"repeat paired PAPER readiness {field} is invalid"
            )
    for field in ("current_pio_wal_sha256", "current_pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"repeat paired PAPER readiness {field} is invalid"
            )

    allowed_statuses = {
        STATUS_READY,
        STATUS_ACCOUNT_MISSING,
        STATUS_INSUFFICIENT_CASH,
        STATUS_OPEN_POSITIONS_PRESENT,
        STATUS_ID_CONFLICT,
        STATUS_PREVIOUS_PAIR_DRIFT,
    }
    if report.get("readiness_status") not in allowed_statuses:
        raise ValueError("repeat paired PAPER readiness status is invalid")

    for field in (
        "cycle_model_binding_valid",
        "readiness_only",
        "requires_separate_paired_entry_authorization",
        "requires_fresh_pair_execution_readiness",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"repeat paired PAPER readiness requires {field}=true"
            )

    for field in (
        "paper_pair_entry_authorized",
        "paper_pair_entry_executed",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"repeat paired PAPER readiness requires {field}=false"
            )

    ready = report["readiness_status"] == STATUS_READY
    if report["account_ready"] is not ready:
        raise ValueError(
            "repeat paired PAPER account_ready/status mismatch"
        )
    if report["repeat_pair_preconditions_ready"] is not ready:
        raise ValueError(
            "repeat paired PAPER preconditions/status mismatch"
        )
    if ready:
        for field in (
            "account_exists",
            "previous_pair_positions_closed",
            "zero_open_positions_verified",
            "paper_cash_sufficient_for_pair",
            "pair_position_ids_available",
            "pair_event_keys_available",
        ):
            if report.get(field) is not True:
                raise ValueError(
                    f"repeat paired PAPER READY requires {field}=true"
                )

    if report["required_pair_cash_quote"] != 2.0 * (
        float(report["capital_quote"])
        + float(report["entry_cost_quote"])
    ):
        raise ValueError(
            "repeat paired PAPER required cash binding mismatch"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("repeat paired PAPER readiness digest mismatch")


def build_phase8_paired_ml_paper_repeat_account_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    final_evaluation_path: str | Path,
    repeat_entry_input_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    repeat_module, account_module = _load_reviewed(source)
    verification = (
        repeat_module.verify_phase8_paired_ml_paper_repeat_entry_inputs(
            source_tree=source,
            final_evaluation_path=final_evaluation_path,
            input_path=repeat_entry_input_path,
        )
    )
    if verification.get("repeat_pair_inputs_ready") is not True:
        raise ValueError("repeat paired PAPER inputs are not ready")

    _, final_evaluation, _ = repeat_module._base(
        source,
        final_evaluation_path,
    )
    if (
        verification["source_final_evaluation_sha256"]
        != final_evaluation["evaluation_sha256"]
    ):
        raise ValueError(
            "repeat paired PAPER input/final evaluation binding mismatch"
        )

    if Path(str(verification["production_repository"])).resolve() != production:
        raise ValueError(
            "repeat paired PAPER repository binding mismatch"
        )
    database = account_module._production_database(production)
    if Path(str(verification["pio_database_path"])).resolve() != database:
        raise ValueError("repeat paired PAPER database binding mismatch")
    before = account_module._database_state(database)

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        account = account_module._account_state(
            conn,
            verification["account_id"],
        )
        ids_available, events_available = account_module._pair_ids_available(
            conn,
            incumbent_position_id=verification["incumbent_position_id"],
            challenger_position_id=verification["challenger_position_id"],
            incumbent_event_key=verification["incumbent_event_key"],
            challenger_event_key=verification["challenger_event_key"],
        )
        cycle = account_module._cycle_state(
            conn,
            verification["active_cycle_id"],
        )
        previous_rows = conn.execute(
            """
            SELECT position_id, status
            FROM paper_positions
            WHERE position_id IN (?, ?)
            """,
            (
                final_evaluation["incumbent_position_id"],
                final_evaluation["challenger_position_id"],
            ),
        ).fetchall()
    finally:
        conn.close()

    after = account_module._database_state(database)
    if after != before:
        raise ValueError(
            "repeat paired PAPER readiness changed production database"
        )

    cycle_valid = (
        cycle["cycle_status"] == "PAPER_CHALLENGER"
        and cycle["cycle_active_key"] == "ACTIVE"
        and cycle["incumbent_model_id"] == verification["incumbent_model_id"]
        and cycle["incumbent_model_status"] == "CHAMPION"
        and cycle["challenger_model_id"] == verification["challenger_model_id"]
        and cycle["challenger_model_status"] == "PAPER_CHALLENGER"
    )
    if not cycle_valid:
        raise ValueError(
            "repeat paired PAPER cycle/model binding changed"
        )

    previous_statuses = {
        str(position_id): str(status)
        for position_id, status in previous_rows
    }
    previous_closed = (
        len(previous_statuses) == 2
        and previous_statuses.get(
            final_evaluation["incumbent_position_id"]
        ) == "CLOSED"
        and previous_statuses.get(
            final_evaluation["challenger_position_id"]
        ) == "CLOSED"
    )

    account_exists = account is not None
    required_cash = 2.0 * (
        float(verification["capital_quote"])
        + float(verification["entry_cost_quote"])
    )
    cash_sufficient = (
        account_exists
        and float(account["cash_quote"]) >= required_cash
    )
    zero_open = (
        account_exists and int(account["open_positions"]) == 0
    )

    if not previous_closed:
        status = STATUS_PREVIOUS_PAIR_DRIFT
    elif not ids_available or not events_available:
        status = STATUS_ID_CONFLICT
    elif not account_exists:
        status = STATUS_ACCOUNT_MISSING
    elif not zero_open:
        status = STATUS_OPEN_POSITIONS_PRESENT
    elif not cash_sufficient:
        status = STATUS_INSUFFICIENT_CASH
    else:
        status = STATUS_READY

    ready = status == STATUS_READY

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        "repeat_entry_input_verification_sha256": verification[
            "verification_sha256"
        ],
        "source_final_evaluation_sha256": verification[
            "source_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "source_pio_database_sha256": verification[
            "pio_database_sha256"
        ],
        "current_pio_database_sha256": before["database"],
        "current_pio_wal_sha256": before["wal"],
        "current_pio_shm_sha256": before["shm"],
        "active_cycle_id": verification["active_cycle_id"],
        "incumbent_model_id": verification["incumbent_model_id"],
        "challenger_model_id": verification["challenger_model_id"],
        "account_id": verification["account_id"],
        "previous_pair_id": verification["previous_pair_id"],
        "previous_incumbent_position_id": final_evaluation[
            "incumbent_position_id"
        ],
        "previous_challenger_position_id": final_evaluation[
            "challenger_position_id"
        ],
        "previous_pair_positions_closed": previous_closed,
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
        "account_open_positions": (
            account["open_positions"] if account else 0
        ),
        "account_closed_positions": (
            account["closed_positions"] if account else 0
        ),
        "zero_open_positions_verified": bool(zero_open),
        "paper_cash_sufficient_for_pair": bool(cash_sufficient),
        "pair_position_ids_available": ids_available,
        "pair_event_keys_available": events_available,
        "cycle_status": cycle["cycle_status"],
        "cycle_active_key": cycle["cycle_active_key"],
        "fresh_incumbent_model_id": cycle["incumbent_model_id"],
        "fresh_incumbent_model_status": cycle["incumbent_model_status"],
        "fresh_challenger_model_id": cycle["challenger_model_id"],
        "fresh_challenger_model_status": cycle["challenger_model_status"],
        "cycle_model_binding_valid": True,
        "readiness_status": status,
        "account_ready": ready,
        "repeat_pair_preconditions_ready": ready,
        "readiness_only": True,
        "requires_separate_paired_entry_authorization": True,
        "requires_fresh_pair_execution_readiness": True,
        "paper_pair_entry_authorized": False,
        "paper_pair_entry_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_repeat_account_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build read-only account readiness for a subsequent fair paired "
            "Phase 8 PAPER entry. The previous pair must remain fully CLOSED, "
            "the account must have zero OPEN positions and enough simulated "
            "cash for both equal-capital legs, and the new pair ids must be "
            "unused. This stage authorizes no PAPER or live execution."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--final-evaluation", required=True)
    parser.add_argument("--repeat-entry-inputs", required=True)
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_repeat_account_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        final_evaluation_path=args.final_evaluation,
        repeat_entry_input_path=args.repeat_entry_inputs,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
