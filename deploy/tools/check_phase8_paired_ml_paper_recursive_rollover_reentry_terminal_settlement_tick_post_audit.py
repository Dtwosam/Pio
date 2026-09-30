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
ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_TERMINAL_SETTLEMENT_TICK_POST_EXECUTION_AUDIT_V1"
)

EXECUTOR_TOOL = Path(
    "deploy/tools/run_phase8_paired_ml_paper_recursive_rollover_reentry_terminal_settlement_tick_once.py"
)
PAPER_AUDIT_MODULE = Path(
    "python-learner/src/meteora_learner/paper_audit.py"
)
REVIEWED_SOURCE_BLOBS = {
    EXECUTOR_TOOL: "3ed6ea57a447c2b2cb65858b41e3139544fdb099",
    PAPER_AUDIT_MODULE: "f58e27d7a8c629aca1235daf3b49134e3a63ad25",
}

DEBT_NEXT_SETTLEMENT_TICK = "PAPER_PAIR_SETTLEMENT_REQUIRED"
DEBT_RECOVERY = "PAPER_SETTLEMENT_TICK_RECOVERY_REQUIRED"
DEBT_FINAL_EVALUATION = "PAPER_PAIR_FINAL_EVALUATION_REQUIRED"

ROUTE_NEXT_SETTLEMENT_TICK = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_TERMINAL_SETTLEMENT_NEXT_TICK_REVIEW"
)
ROUTE_RECOVERY = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_TERMINAL_SETTLEMENT_RECOVERY_REVIEW"
)
ROUTE_FINAL_EVALUATION = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_POST_SETTLEMENT_FINAL_EVALUATION_REVIEW"
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "execution_receipt_sha256",
    "source_terminal_evaluation_sha256",
    "source_terminal_post_audit_sha256",
    "source_recursive_rollover_reentry_post_audit_sha256",
    "recursive_rollover_reentry_entry_request_sha256",
    "recursive_rollover_reentry_input_verification_sha256",
    "source_final_evaluation_sha256",
    "production_repository",
    "pio_database_path",
    "receipt_database_sha256_after",
    "receipt_wal_sha256_after",
    "receipt_shm_sha256_after",
    "audit_database_sha256_before",
    "audit_database_sha256_after",
    "audit_wal_sha256_before",
    "audit_wal_sha256_after",
    "audit_shm_sha256_before",
    "audit_shm_sha256_after",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "previous_pair_id",
    "pair_id",
    "pool_address",
    "entry_observed_at",
    "terminal_tick_observed_at",
    "incumbent_position_id",
    "challenger_position_id",
    "open_position_id",
    "closed_position_id",
    "open_position_policy_source",
    "open_position_model_id",
    "settlement_cycle_id",
    "expected_run_id",
    "target_chain_observed_at",
    "fresh_run",
    "fresh_run_item",
    "fresh_valuation",
    "receipt_settlement_tick_complete",
    "receipt_settlement_tick_partial_failure",
    "open_position_after",
    "closed_position_after",
    "paper_ledger_audit",
    "paper_ledger_audit_sha256",
    "cycle_status",
    "cycle_active_key",
    "fresh_incumbent_model_status",
    "fresh_challenger_model_status",
    "receipt_valid",
    "database_matches_receipt",
    "database_unchanged_by_audit",
    "run_identity_verified",
    "run_scope_verified",
    "run_counts_verified",
    "applied_item_has_applied_valuation",
    "closed_leg_stayed_closed",
    "ledger_audit_passing",
    "cycle_model_binding_verified",
    "settlement_tick_complete_verified",
    "settlement_tick_partial_failure_verified",
    "open_leg_still_open",
    "open_leg_now_closed",
    "next_debt_type",
    "next_scope",
    "continuation_route",
    "next_settlement_tick_review_ready",
    "settlement_recovery_review_ready",
    "final_pair_evaluation_ready",
    "separate_next_action_authorization_required",
    "post_tick_audit_ready",
    "paper_settlement_tick_authorized",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified_by_audit",
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
                f"paired PAPER settlement post-audit dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"paired PAPER settlement post-audit dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / EXECUTOR_TOOL,
            "phase8_recursive_rollover_reentry_terminal_settlement_post_audit_executor",
        ),
        _load_module(
            source / PAPER_AUDIT_MODULE,
            "meteora_learner.phase8_terminal_settlement_ledger_audit",
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


def _row_dict(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    names = [item[0] for item in cursor.description or ()]
    return {name: row[index] for index, name in enumerate(names)}


def _position(conn: sqlite3.Connection, position_id: str) -> dict[str, Any]:
    cursor = conn.execute(
        """
        SELECT position_id, account_id, pool_address, status,
               policy_source, model_id, opened_at, closed_at,
               current_mark_quote, realized_pnl_quote
        FROM paper_positions
        WHERE position_id = ?
        LIMIT 1
        """,
        (position_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(
            f"paired PAPER settlement post-audit position missing: {position_id}"
        )
    value = _row_dict(cursor, row)
    value["current_mark_quote"] = float(value["current_mark_quote"])
    if value["realized_pnl_quote"] is not None:
        value["realized_pnl_quote"] = float(value["realized_pnl_quote"])
    return value


def _valuation(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    observed_at: str,
) -> dict[str, Any] | None:
    cursor = conn.execute(
        """
        SELECT position_id, observed_at, status, active_bin_id,
               mark_quote, fee_delta_quote, reward_delta_quote
        FROM paper_chain_valuations
        WHERE position_id = ? AND observed_at = ?
        LIMIT 1
        """,
        (position_id, observed_at),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    value = _row_dict(cursor, row)
    value["active_bin_id"] = int(value["active_bin_id"])
    for field in ("mark_quote", "fee_delta_quote", "reward_delta_quote"):
        value[field] = float(value[field])
    return value


def validate_phase8_paired_ml_paper_recursive_rollover_reentry_terminal_settlement_tick_post_audit(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "paired PAPER settlement tick post-audit must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"post_audit_sha256"}:
        raise ValueError(
            "paired PAPER settlement tick post-audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported paired PAPER settlement tick post-audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected paired PAPER settlement tick post-audit type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "paired PAPER settlement tick post-audit lineage mismatch"
        )
    for field in (
        "execution_receipt_sha256",
        "source_terminal_evaluation_sha256",
        "source_terminal_post_audit_sha256",
        "source_recursive_rollover_reentry_post_audit_sha256",
        "recursive_rollover_reentry_entry_request_sha256",
        "recursive_rollover_reentry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "receipt_database_sha256_after",
        "audit_database_sha256_before",
        "audit_database_sha256_after",
        "paper_ledger_audit_sha256",
        "post_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"paired PAPER settlement tick post-audit {field} is invalid"
            )
    for field in (
        "receipt_wal_sha256_after",
        "receipt_shm_sha256_after",
        "audit_wal_sha256_before",
        "audit_wal_sha256_after",
        "audit_shm_sha256_before",
        "audit_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"paired PAPER settlement tick post-audit {field} is invalid"
            )

    if report["pair_id"] == report["previous_pair_id"]:
        raise ValueError(
            "recursive rollover re-entry paired PAPER settlement post-audit pair id was not advanced"
        )

    for field in (
        "fresh_run",
        "fresh_run_item",
        "open_position_after",
        "closed_position_after",
        "paper_ledger_audit",
    ):
        if not isinstance(report.get(field), dict):
            raise ValueError(
                f"paired PAPER settlement tick post-audit {field} is invalid"
            )
    if report.get("fresh_valuation") is not None and not isinstance(
        report["fresh_valuation"], dict
    ):
        raise ValueError(
            "paired PAPER settlement tick post-audit valuation is invalid"
        )

    for field in (
        "receipt_valid",
        "database_matches_receipt",
        "database_unchanged_by_audit",
        "run_identity_verified",
        "run_scope_verified",
        "run_counts_verified",
        "applied_item_has_applied_valuation",
        "closed_leg_stayed_closed",
        "ledger_audit_passing",
        "cycle_model_binding_verified",
        "settlement_tick_complete_verified",
        "settlement_tick_partial_failure_verified",
        "separate_next_action_authorization_required",
        "post_tick_audit_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"paired PAPER settlement tick post-audit requires {field}=true"
            )
    for field in (
        "paper_settlement_tick_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified_by_audit",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"paired PAPER settlement tick post-audit requires {field}=false"
            )

    if report["audit_database_sha256_before"] != report[
        "audit_database_sha256_after"
    ]:
        raise ValueError(
            "paired PAPER settlement tick post-audit modified database"
        )
    if report["audit_wal_sha256_before"] != report["audit_wal_sha256_after"]:
        raise ValueError(
            "paired PAPER settlement tick post-audit modified WAL"
        )
    if report["audit_shm_sha256_before"] != report["audit_shm_sha256_after"]:
        raise ValueError(
            "paired PAPER settlement tick post-audit modified SHM"
        )

    if report["settlement_tick_partial_failure_verified"] is not True:
        raise ValueError(
            "paired PAPER settlement tick partial-failure verification missing"
        )
    if report["settlement_tick_complete_verified"] is not True:
        raise ValueError(
            "paired PAPER settlement tick complete verification missing"
        )

    if report["receipt_settlement_tick_partial_failure"]:
        if report["settlement_recovery_review_ready"] is not True:
            raise ValueError(
                "paired PAPER settlement partial failure lacks recovery route"
            )
        if report["continuation_route"] != ROUTE_RECOVERY:
            raise ValueError(
                "paired PAPER settlement partial failure route mismatch"
            )
    else:
        if report["open_leg_still_open"]:
            if report["open_leg_now_closed"]:
                raise ValueError(
                    "paired PAPER settlement open-leg state is inconsistent"
                )
            if report["next_settlement_tick_review_ready"] is not True:
                raise ValueError(
                    "paired PAPER settlement next-tick review not ready"
                )
            if report["continuation_route"] != ROUTE_NEXT_SETTLEMENT_TICK:
                raise ValueError(
                    "paired PAPER settlement next-tick route mismatch"
                )
        else:
            if report["open_leg_now_closed"] is not True:
                raise ValueError(
                    "paired PAPER settlement final closure is missing"
                )
            if report["final_pair_evaluation_ready"] is not True:
                raise ValueError(
                    "paired PAPER settlement final evaluation not ready"
                )
            if report["continuation_route"] != ROUTE_FINAL_EVALUATION:
                raise ValueError(
                    "paired PAPER settlement final route mismatch"
                )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["post_audit_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "paired PAPER settlement tick post-audit digest mismatch"
        )


def build_phase8_paired_ml_paper_recursive_rollover_reentry_terminal_settlement_tick_post_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    execution_receipt_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    executor, ledger_module = _load_reviewed(source)
    receipt = _load_json(
        execution_receipt_path,
        label="paired PAPER settlement tick execution receipt",
    )
    executor.validate_phase8_paired_ml_paper_recursive_rollover_reentry_terminal_settlement_tick_execution_receipt(
        receipt
    )
    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError(
            "paired PAPER settlement receipt repository binding mismatch"
        )
    if receipt["pair_id"] == receipt["previous_pair_id"]:
        raise ValueError(
            "recursive rollover re-entry paired PAPER settlement receipt pair id was not advanced"
        )

    readiness = executor._load_readiness(source)
    settlement_readiness, _, _, live_module, _ = readiness._load_reviewed(source)
    database = settlement_readiness._database_path(production)
    before = settlement_readiness._database_state(database)
    if Path(str(receipt["pio_database_path"])).resolve() != database:
        raise ValueError(
            "paired PAPER settlement receipt database binding mismatch"
        )
    expected = {
        "database": receipt["pio_database_sha256_after"],
        "wal": receipt["pio_wal_sha256_after"],
        "shm": receipt["pio_shm_sha256_after"],
    }
    if before != expected:
        raise ValueError(
            "Pio database changed after paired PAPER settlement receipt"
        )

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        run_cursor = conn.execute(
            """
            SELECT run_id, observed_at, status, items_total,
                   items_applied, items_skipped, items_failed
            FROM paper_runs
            WHERE run_id = ?
            LIMIT 1
            """,
            (receipt["expected_run_id"],),
        )
        run_row = run_cursor.fetchone()
        if run_row is None:
            raise ValueError(
                "paired PAPER settlement persisted run is missing"
            )
        fresh_run = _row_dict(run_cursor, run_row)
        for field in (
            "items_total",
            "items_applied",
            "items_skipped",
            "items_failed",
        ):
            fresh_run[field] = int(fresh_run[field])

        item_cursor = conn.execute(
            """
            SELECT position_id, status, result_json, error
            FROM paper_run_items
            WHERE run_id = ?
            LIMIT 2
            """,
            (receipt["expected_run_id"],),
        )
        item_rows = item_cursor.fetchall()
        if len(item_rows) != 1:
            raise ValueError(
                "paired PAPER settlement persisted run scope changed"
            )
        position_id, status, result_json, error = item_rows[0]
        fresh_item = {
            "position_id": str(position_id),
            "status": str(status),
            "result": (
                json.loads(str(result_json))
                if result_json is not None
                else None
            ),
            "error": str(error) if error is not None else None,
        }

        valuation = _valuation(
            conn,
            position_id=receipt["open_position_id"],
            observed_at=receipt["target_chain_observed_at"],
        )
        open_position = _position(conn, receipt["open_position_id"])
        closed_position = _position(conn, receipt["closed_position_id"])

        cycle = conn.execute(
            """
            SELECT c.status, c.active_key,
                   c.champion_model_id, c.challenger_model_id,
                   champion.status, challenger.status
            FROM continuous_learning_cycles AS c
            JOIN model_registry AS champion
              ON champion.model_id = c.champion_model_id
            JOIN model_registry AS challenger
              ON challenger.model_id = c.challenger_model_id
            WHERE c.cycle_id = ?
            LIMIT 1
            """,
            (receipt["active_cycle_id"],),
        ).fetchone()
        if cycle is None:
            raise ValueError(
                "paired PAPER settlement active cycle is missing"
            )
    finally:
        conn.close()

    ledger = ledger_module.audit_paper_ledger(
        live_module.Storage(database),
        account_id=receipt["account_id"],
    )
    ledger_record = ledger.to_record()
    if ledger.passing is not True:
        raise ValueError(
            "paired PAPER settlement ledger audit failed: "
            + "; ".join(ledger.reasons)
        )

    after = settlement_readiness._database_state(database)
    if after != before:
        raise ValueError(
            "paired PAPER settlement post-audit changed production database"
        )

    if fresh_run["run_id"] != receipt["expected_run_id"]:
        raise ValueError(
            "paired PAPER settlement persisted run identity changed"
        )
    if fresh_run["observed_at"] != receipt["target_chain_observed_at"]:
        raise ValueError(
            "paired PAPER settlement persisted chain snapshot changed"
        )
    if fresh_item["position_id"] != receipt["open_position_id"]:
        raise ValueError(
            "paired PAPER settlement persisted position scope changed"
        )
    for field in (
        "items_total",
        "items_applied",
        "items_skipped",
        "items_failed",
    ):
        if fresh_run[field] != receipt[field]:
            raise ValueError(
                "paired PAPER settlement persisted run counts changed"
            )
    if fresh_item["status"] != receipt["run_item_status"]:
        raise ValueError(
            "paired PAPER settlement persisted item status changed"
        )

    applied_has_valuation = True
    if fresh_item["status"] == "APPLIED":
        applied_has_valuation = (
            valuation is not None
            and valuation.get("status") == "APPLIED"
        )
    if not applied_has_valuation:
        raise ValueError(
            "paired PAPER settlement APPLIED item lacks APPLIED valuation"
        )

    if closed_position["status"] != "CLOSED":
        raise ValueError(
            "paired PAPER settlement previously closed leg reopened"
        )
    if (
        open_position["policy_source"]
        != receipt["open_position_policy_source"]
        or open_position["model_id"] != receipt["open_position_model_id"]
        or open_position["pool_address"] != receipt["pool_address"]
    ):
        raise ValueError(
            "paired PAPER settlement open-leg binding changed"
        )

    cycle_ok = (
        str(cycle[0]) == "PAPER_CHALLENGER"
        and str(cycle[1]) == "ACTIVE"
        and str(cycle[2]) == receipt["incumbent_model_id"]
        and str(cycle[3]) == receipt["challenger_model_id"]
        and str(cycle[4]) == "CHAMPION"
        and str(cycle[5]) == "PAPER_CHALLENGER"
    )
    if not cycle_ok:
        raise ValueError(
            "paired PAPER settlement cycle/model binding changed"
        )

    partial = bool(receipt["settlement_tick_partial_failure"])
    complete = bool(receipt["settlement_tick_complete"])
    still_open = open_position["status"] == "OPEN"
    now_closed = open_position["status"] == "CLOSED"
    if not (still_open or now_closed):
        raise ValueError(
            "paired PAPER settlement open leg has invalid post-tick status"
        )

    if partial:
        next_debt = DEBT_RECOVERY
        route = ROUTE_RECOVERY
        next_tick_ready = False
        recovery_ready = True
        final_ready = False
    elif still_open:
        next_debt = DEBT_NEXT_SETTLEMENT_TICK
        route = ROUTE_NEXT_SETTLEMENT_TICK
        next_tick_ready = True
        recovery_ready = False
        final_ready = False
    else:
        next_debt = DEBT_FINAL_EVALUATION
        route = ROUTE_FINAL_EVALUATION
        next_tick_ready = False
        recovery_ready = False
        final_ready = True

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        "execution_receipt_sha256": receipt["receipt_sha256"],
        "source_terminal_evaluation_sha256": receipt[
            "source_terminal_evaluation_sha256"
        ],
        "source_terminal_post_audit_sha256": receipt[
            "source_terminal_post_audit_sha256"
        ],
        "source_recursive_rollover_reentry_post_audit_sha256": receipt[
            "source_recursive_rollover_reentry_post_audit_sha256"
        ],
        "recursive_rollover_reentry_entry_request_sha256": receipt[
            "recursive_rollover_reentry_entry_request_sha256"
        ],
        "recursive_rollover_reentry_input_verification_sha256": receipt[
            "recursive_rollover_reentry_input_verification_sha256"
        ],
        "source_final_evaluation_sha256": receipt[
            "source_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "receipt_database_sha256_after": receipt[
            "pio_database_sha256_after"
        ],
        "receipt_wal_sha256_after": receipt["pio_wal_sha256_after"],
        "receipt_shm_sha256_after": receipt["pio_shm_sha256_after"],
        "audit_database_sha256_before": before["database"],
        "audit_database_sha256_after": after["database"],
        "audit_wal_sha256_before": before["wal"],
        "audit_wal_sha256_after": after["wal"],
        "audit_shm_sha256_before": before["shm"],
        "audit_shm_sha256_after": after["shm"],
        "active_cycle_id": receipt["active_cycle_id"],
        "incumbent_model_id": receipt["incumbent_model_id"],
        "challenger_model_id": receipt["challenger_model_id"],
        "account_id": receipt["account_id"],
        "previous_pair_id": receipt["previous_pair_id"],
        "pair_id": receipt["pair_id"],
        "pool_address": receipt["pool_address"],
        "entry_observed_at": receipt["entry_observed_at"],
        "terminal_tick_observed_at": receipt["terminal_tick_observed_at"],
        "incumbent_position_id": receipt["incumbent_position_id"],
        "challenger_position_id": receipt["challenger_position_id"],
        "open_position_id": receipt["open_position_id"],
        "closed_position_id": receipt["closed_position_id"],
        "open_position_policy_source": receipt[
            "open_position_policy_source"
        ],
        "open_position_model_id": receipt["open_position_model_id"],
        "settlement_cycle_id": receipt["settlement_cycle_id"],
        "expected_run_id": receipt["expected_run_id"],
        "target_chain_observed_at": receipt["target_chain_observed_at"],
        "fresh_run": fresh_run,
        "fresh_run_item": fresh_item,
        "fresh_valuation": valuation,
        "receipt_settlement_tick_complete": complete,
        "receipt_settlement_tick_partial_failure": partial,
        "open_position_after": open_position,
        "closed_position_after": closed_position,
        "paper_ledger_audit": ledger_record,
        "paper_ledger_audit_sha256": _sha256_bytes(
            _canonical_bytes(ledger_record)
        ),
        "cycle_status": str(cycle[0]),
        "cycle_active_key": str(cycle[1]),
        "fresh_incumbent_model_status": str(cycle[4]),
        "fresh_challenger_model_status": str(cycle[5]),
        "receipt_valid": True,
        "database_matches_receipt": True,
        "database_unchanged_by_audit": True,
        "run_identity_verified": True,
        "run_scope_verified": True,
        "run_counts_verified": True,
        "applied_item_has_applied_valuation": applied_has_valuation,
        "closed_leg_stayed_closed": True,
        "ledger_audit_passing": True,
        "cycle_model_binding_verified": True,
        "settlement_tick_complete_verified": True,
        "settlement_tick_partial_failure_verified": True,
        "open_leg_still_open": still_open,
        "open_leg_now_closed": now_closed,
        "next_debt_type": next_debt,
        "next_scope": receipt["challenger_model_id"],
        "continuation_route": route,
        "next_settlement_tick_review_ready": next_tick_ready,
        "settlement_recovery_review_ready": recovery_ready,
        "final_pair_evaluation_ready": final_ready,
        "separate_next_action_authorization_required": True,
        "post_tick_audit_ready": True,
        "paper_settlement_tick_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified_by_audit": False,
    }
    report = {
        **identity,
        "post_audit_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_recursive_rollover_reentry_terminal_settlement_tick_post_audit(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit one single-position terminal PAPER settlement tick. "
            "The audit verifies the persisted run, valuation and ledger, "
            "then routes to another settlement tick, recovery, or final "
            "two-leg evaluation. It performs no PAPER or live mutation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--execution-receipt", required=True)
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_recursive_rollover_reentry_terminal_settlement_tick_post_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        execution_receipt_path=args.execution_receipt,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
