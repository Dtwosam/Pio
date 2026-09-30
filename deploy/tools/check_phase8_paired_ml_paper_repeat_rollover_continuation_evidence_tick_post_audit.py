from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_CONTINUATION_EVIDENCE_TICK_POST_EXECUTION_AUDIT_V1"
)

EXECUTOR_TOOL = Path(
    "deploy/tools/run_phase8_paired_ml_paper_repeat_rollover_continuation_evidence_tick_once.py"
)
PAPER_AUDIT_MODULE = Path(
    "python-learner/src/meteora_learner/paper_audit.py"
)
REVIEWED_SOURCE_BLOBS = {
    EXECUTOR_TOOL: "beb2fbfec720c93daedd9490c1f5c28d96f69562",
    PAPER_AUDIT_MODULE: "f58e27d7a8c629aca1235daf3b49134e3a63ad25",
}

DEBT_NEXT_TICK = "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
DEBT_RECOVERY = "PAPER_PAIR_TICK_RECOVERY_REQUIRED"
DEBT_TERMINAL = "PAPER_PAIR_TERMINAL_EVALUATION_REQUIRED"

ROUTE_NEXT_TICK = "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_NEXT_EVIDENCE_TICK_REVIEW"
ROUTE_RECOVERY = "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_TICK_RECOVERY_REVIEW"
ROUTE_TERMINAL = "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_TERMINAL_EVALUATION_REVIEW"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "execution_receipt_sha256",
    "source_rollover_post_audit_sha256",
    "rollover_entry_request_sha256",
    "rollover_entry_input_verification_sha256",
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
    "source_previous_tick_post_audit_sha256",
    "previous_tick_observed_at",
    "requested_position_ids",
    "incumbent_position_id",
    "challenger_position_id",
    "evidence_cycle_id",
    "expected_run_id",
    "target_chain_observed_at",
    "receipt_tick_status",
    "receipt_pair_tick_complete",
    "receipt_pair_tick_partial_failure",
    "fresh_run",
    "fresh_run_items",
    "pair_valuations",
    "fresh_positions",
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
    "applied_items_have_applied_valuations",
    "pair_valuation_scope_verified",
    "ledger_audit_passing",
    "cycle_model_binding_verified",
    "tick_complete_verified",
    "tick_partial_failure_verified",
    "pair_both_open",
    "pair_any_closed",
    "next_debt_type",
    "next_scope",
    "continuation_route",
    "next_evidence_tick_review_ready",
    "tick_recovery_review_ready",
    "terminal_pair_evaluation_ready",
    "separate_next_action_authorization_required",
    "post_tick_audit_ready",
    "paper_supervisor_tick_authorized",
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
                f"paired PAPER continuation tick post-audit dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"paired PAPER continuation tick post-audit dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / EXECUTOR_TOOL,
            "phase8_rollover_paired_paper_continuation_tick_post_audit_executor",
        ),
        _load_module(
            source / PAPER_AUDIT_MODULE,
            "meteora_learner.phase8_paired_paper_tick_ledger_audit",
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


def _position(
    conn: sqlite3.Connection,
    position_id: str,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        SELECT position_id, account_id, pool_address, status,
               policy_source, model_id, strategy, min_bin_id, max_bin_id,
               opened_at, closed_at, entry_capital_quote, entry_cost_quote,
               current_mark_quote, fee_income_quote, reward_income_quote,
               rebalance_cost_quote, exit_cost_quote, realized_pnl_quote,
               rebalances
        FROM paper_positions
        WHERE position_id = ?
        LIMIT 1
        """,
        (position_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(
            f"paired PAPER continuation tick post-audit position missing: {position_id}"
        )
    value = _row_dict(cursor, row)
    for field in (
        "entry_capital_quote",
        "entry_cost_quote",
        "current_mark_quote",
        "fee_income_quote",
        "reward_income_quote",
        "rebalance_cost_quote",
        "exit_cost_quote",
    ):
        value[field] = float(value[field])
    if value["realized_pnl_quote"] is not None:
        value["realized_pnl_quote"] = float(value["realized_pnl_quote"])
    value["min_bin_id"] = int(value["min_bin_id"])
    value["max_bin_id"] = int(value["max_bin_id"])
    value["rebalances"] = int(value["rebalances"])
    return value


def _valuation(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    observed_at: str,
) -> dict[str, Any] | None:
    cursor = conn.execute(
        """
        SELECT position_id, observed_at, event_key_prefix, status,
               active_bin_id, mark_quote, fee_delta_quote,
               reward_delta_quote, inventory_x_atomic, inventory_y_atomic,
               fee_x_atomic, fee_y_atomic, reward_one_atomic,
               reward_two_atomic
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
    for field in (
        "inventory_x_atomic",
        "inventory_y_atomic",
        "fee_x_atomic",
        "fee_y_atomic",
        "reward_one_atomic",
        "reward_two_atomic",
    ):
        value[field] = int(value[field])
    return value


def validate_phase8_paired_ml_paper_repeat_rollover_continuation_evidence_tick_post_audit(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "paired PAPER continuation evidence tick post-audit must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"post_audit_sha256"}:
        raise ValueError(
            "paired PAPER continuation evidence tick post-audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported paired PAPER continuation evidence tick post-audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected paired PAPER continuation evidence tick post-audit type"
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
            "paired PAPER continuation evidence tick post-audit lineage mismatch"
        )

    for field in (
        "execution_receipt_sha256",
        "source_previous_tick_post_audit_sha256",
        "receipt_database_sha256_after",
        "audit_database_sha256_before",
        "audit_database_sha256_after",
        "paper_ledger_audit_sha256",
        "post_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"paired PAPER continuation tick post-audit {field} is invalid"
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
                f"paired PAPER continuation tick post-audit {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_id",
        "pair_id",
        "pool_address",
        "previous_tick_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "evidence_cycle_id",
        "expected_run_id",
        "target_chain_observed_at",
        "receipt_tick_status",
        "cycle_status",
        "cycle_active_key",
        "fresh_incumbent_model_status",
        "fresh_challenger_model_status",
        "next_debt_type",
        "next_scope",
        "continuation_route",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"paired PAPER continuation tick post-audit {field} is invalid"
            )

    requested = report.get("requested_position_ids")
    if requested != [
        report["incumbent_position_id"],
        report["challenger_position_id"],
    ]:
        raise ValueError(
            "paired PAPER continuation tick post-audit position scope mismatch"
        )
    if not isinstance(report.get("fresh_run"), dict):
        raise ValueError("paired PAPER continuation tick post-audit run is invalid")
    if not isinstance(report.get("fresh_run_items"), dict):
        raise ValueError(
            "paired PAPER continuation tick post-audit run items are invalid"
        )
    if set(report["fresh_run_items"]) != set(requested):
        raise ValueError(
            "paired PAPER continuation tick post-audit run item scope mismatch"
        )
    if not isinstance(report.get("pair_valuations"), dict):
        raise ValueError(
            "paired PAPER continuation tick post-audit valuations are invalid"
        )
    if set(report["pair_valuations"]) != set(requested):
        raise ValueError(
            "paired PAPER continuation tick post-audit valuation scope mismatch"
        )
    if not isinstance(report.get("fresh_positions"), dict):
        raise ValueError(
            "paired PAPER continuation tick post-audit positions are invalid"
        )
    if set(report["fresh_positions"]) != set(requested):
        raise ValueError(
            "paired PAPER continuation tick post-audit fresh position scope mismatch"
        )
    if not isinstance(report.get("paper_ledger_audit"), dict):
        raise ValueError(
            "paired PAPER continuation tick post-audit ledger report is invalid"
        )
    if report["paper_ledger_audit_sha256"] != _sha256_bytes(
        _canonical_bytes(report["paper_ledger_audit"])
    ):
        raise ValueError(
            "paired PAPER continuation tick post-audit ledger digest mismatch"
        )

    for field in (
        "receipt_valid",
        "database_matches_receipt",
        "database_unchanged_by_audit",
        "run_identity_verified",
        "run_scope_verified",
        "run_counts_verified",
        "applied_items_have_applied_valuations",
        "pair_valuation_scope_verified",
        "ledger_audit_passing",
        "cycle_model_binding_verified",
        "tick_complete_verified",
        "tick_partial_failure_verified",
        "separate_next_action_authorization_required",
        "post_tick_audit_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"paired PAPER continuation tick post-audit requires {field}=true"
            )

    for field in (
        "paper_supervisor_tick_authorized",
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
                f"paired PAPER continuation tick post-audit requires {field}=false"
            )

    if report["cycle_status"] != "PAPER_CHALLENGER":
        raise ValueError(
            "paired PAPER continuation tick post-audit cycle status mismatch"
        )
    if report["cycle_active_key"] != "ACTIVE":
        raise ValueError(
            "paired PAPER continuation tick post-audit active cycle mismatch"
        )
    if report["fresh_incumbent_model_status"] != "CHAMPION":
        raise ValueError(
            "paired PAPER continuation tick post-audit incumbent status mismatch"
        )
    if report["fresh_challenger_model_status"] != "PAPER_CHALLENGER":
        raise ValueError(
            "paired PAPER continuation tick post-audit challenger status mismatch"
        )

    partial = bool(report["receipt_pair_tick_partial_failure"])
    complete = bool(report["receipt_pair_tick_complete"])
    if complete == partial:
        raise ValueError(
            "paired PAPER continuation tick post-audit receipt outcome is inconsistent"
        )

    if partial:
        if report["next_debt_type"] != DEBT_RECOVERY:
            raise ValueError(
                "paired PAPER continuation tick post-audit recovery debt mismatch"
            )
        if report["continuation_route"] != ROUTE_RECOVERY:
            raise ValueError(
                "paired PAPER continuation tick post-audit recovery route mismatch"
            )
        if report["tick_recovery_review_ready"] is not True:
            raise ValueError(
                "paired PAPER continuation tick post-audit recovery review not ready"
            )
        if report["next_evidence_tick_review_ready"] is not False:
            raise ValueError(
                "paired PAPER continuation tick post-audit cannot request next tick"
            )
        if report["terminal_pair_evaluation_ready"] is not False:
            raise ValueError(
                "paired PAPER continuation tick post-audit cannot terminal-evaluate partial tick"
            )
    else:
        if report["tick_recovery_review_ready"] is not False:
            raise ValueError(
                "paired PAPER continuation tick post-audit unexpected recovery route"
            )
        if report["pair_both_open"]:
            if report["pair_any_closed"]:
                raise ValueError(
                    "paired PAPER continuation tick post-audit pair state is inconsistent"
                )
            if report["next_debt_type"] != DEBT_NEXT_TICK:
                raise ValueError(
                    "paired PAPER continuation tick post-audit next-tick debt mismatch"
                )
            if report["continuation_route"] != ROUTE_NEXT_TICK:
                raise ValueError(
                    "paired PAPER continuation tick post-audit next-tick route mismatch"
                )
            if report["next_evidence_tick_review_ready"] is not True:
                raise ValueError(
                    "paired PAPER continuation tick post-audit next tick review not ready"
                )
            if report["terminal_pair_evaluation_ready"] is not False:
                raise ValueError(
                    "paired PAPER continuation tick post-audit terminal route unexpected"
                )
        else:
            if report["pair_any_closed"] is not True:
                raise ValueError(
                    "paired PAPER continuation tick post-audit terminal pair state missing"
                )
            if report["next_debt_type"] != DEBT_TERMINAL:
                raise ValueError(
                    "paired PAPER continuation tick post-audit terminal debt mismatch"
                )
            if report["continuation_route"] != ROUTE_TERMINAL:
                raise ValueError(
                    "paired PAPER continuation tick post-audit terminal route mismatch"
                )
            if report["terminal_pair_evaluation_ready"] is not True:
                raise ValueError(
                    "paired PAPER continuation tick post-audit terminal review not ready"
                )
            if report["next_evidence_tick_review_ready"] is not False:
                raise ValueError(
                    "paired PAPER continuation tick post-audit closed pair cannot request tick"
                )

    if report["next_scope"] != report["challenger_model_id"]:
        raise ValueError(
            "paired PAPER continuation tick post-audit next scope mismatch"
        )
    if report["audit_database_sha256_before"] != report[
        "audit_database_sha256_after"
    ]:
        raise ValueError(
            "paired PAPER continuation tick post-audit modified database"
        )
    if report["audit_wal_sha256_before"] != report["audit_wal_sha256_after"]:
        raise ValueError(
            "paired PAPER continuation tick post-audit modified WAL"
        )
    if report["audit_shm_sha256_before"] != report["audit_shm_sha256_after"]:
        raise ValueError(
            "paired PAPER continuation tick post-audit modified SHM"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["post_audit_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "paired PAPER continuation evidence tick post-audit digest mismatch"
        )


def build_phase8_paired_ml_paper_repeat_rollover_continuation_evidence_tick_post_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    execution_receipt_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    executor, ledger_module = _load_reviewed(source)
    receipt = _load_json(
        execution_receipt_path,
        label="repeat rollover continuation paired PAPER evidence tick execution receipt",
    )
    executor.validate_phase8_paired_ml_paper_repeat_rollover_continuation_evidence_tick_execution_receipt(
        receipt
    )
    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError(
            "paired PAPER tick receipt repository binding mismatch"
        )
    if receipt["pair_id"] == receipt["previous_pair_id"]:
        raise ValueError(
            "repeat continuation tick receipt pair id was not advanced"
        )

    readiness = executor._load_readiness(source)
    (
        supervision_module,
        _,
        _,
        live_module,
        _,
    ) = readiness._load_reviewed(source)
    database = supervision_module._database_path(production)
    before = supervision_module._database_state(database)
    if Path(str(receipt["pio_database_path"])).resolve() != database:
        raise ValueError(
            "paired PAPER tick receipt database binding mismatch"
        )
    expected = {
        "database": receipt["pio_database_sha256_after"],
        "wal": receipt["pio_wal_sha256_after"],
        "shm": receipt["pio_shm_sha256_after"],
    }
    if before != expected:
        raise ValueError(
            "Pio database changed after paired PAPER tick receipt"
        )

    requested = list(receipt["requested_position_ids"])
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
                "paired PAPER tick persisted run is missing"
            )
        fresh_run = _row_dict(run_cursor, run_row)
        for field in (
            "items_total",
            "items_applied",
            "items_skipped",
            "items_failed",
        ):
            fresh_run[field] = int(fresh_run[field])

        item_rows = conn.execute(
            """
            SELECT position_id, status, result_json, error
            FROM paper_run_items
            WHERE run_id = ?
            ORDER BY position_id
            """,
            (receipt["expected_run_id"],),
        ).fetchall()
        if len(item_rows) != 2:
            raise ValueError(
                "paired PAPER tick persisted run item count changed"
            )
        fresh_items: dict[str, dict[str, Any]] = {}
        for position_id, status, result_json, error in item_rows:
            fresh_items[str(position_id)] = {
                "position_id": str(position_id),
                "status": str(status),
                "result": (
                    json.loads(str(result_json))
                    if result_json is not None
                    else None
                ),
                "error": str(error) if error is not None else None,
            }

        valuations = {
            position_id: _valuation(
                conn,
                position_id=position_id,
                observed_at=receipt["target_chain_observed_at"],
            )
            for position_id in requested
        }
        positions = {
            position_id: _position(conn, position_id)
            for position_id in requested
        }

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
                "paired PAPER tick active cycle is missing"
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
            "paired PAPER tick ledger audit failed: "
            + "; ".join(ledger.reasons)
        )

    after = supervision_module._database_state(database)
    if after != before:
        raise ValueError(
            "paired PAPER continuation tick post-audit changed production database"
        )

    if fresh_run["run_id"] != receipt["expected_run_id"]:
        raise ValueError(
            "paired PAPER tick run identity changed"
        )
    if fresh_run["observed_at"] != receipt["target_chain_observed_at"]:
        raise ValueError(
            "paired PAPER tick run chain snapshot changed"
        )
    if set(fresh_items) != set(requested):
        raise ValueError(
            "paired PAPER tick run item scope changed"
        )
    if fresh_run["status"] != receipt["tick_status"]:
        raise ValueError(
            "paired PAPER tick run status differs from receipt"
        )
    count_fields = (
        ("items_total", "items_total"),
        ("items_applied", "items_applied"),
        ("items_skipped", "items_skipped"),
        ("items_failed", "items_failed"),
    )
    for run_field, receipt_field in count_fields:
        if int(fresh_run[run_field]) != int(receipt[receipt_field]):
            raise ValueError(
                "paired PAPER tick persisted run counts differ from receipt"
            )
    if {
        position_id: item["status"]
        for position_id, item in fresh_items.items()
    } != receipt["run_item_statuses"]:
        raise ValueError(
            "paired PAPER tick persisted item statuses differ from receipt"
        )

    applied_ok = True
    for position_id, item in fresh_items.items():
        valuation = valuations[position_id]
        if item["status"] == "APPLIED":
            if valuation is None or valuation["status"] != "APPLIED":
                applied_ok = False
    if not applied_ok:
        raise ValueError(
            "paired PAPER tick applied item lacks applied valuation"
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
            "paired PAPER tick cycle/model binding changed"
        )

    partial = bool(receipt["pair_tick_partial_failure"])
    complete = bool(receipt["pair_tick_complete"])
    both_open = all(
        positions[position_id]["status"] == "OPEN"
        for position_id in requested
    )
    any_closed = any(
        positions[position_id]["status"] == "CLOSED"
        for position_id in requested
    )

    if partial:
        next_debt = DEBT_RECOVERY
        route = ROUTE_RECOVERY
        next_tick_ready = False
        recovery_ready = True
        terminal_ready = False
    elif both_open:
        next_debt = DEBT_NEXT_TICK
        route = ROUTE_NEXT_TICK
        next_tick_ready = True
        recovery_ready = False
        terminal_ready = False
    else:
        next_debt = DEBT_TERMINAL
        route = ROUTE_TERMINAL
        next_tick_ready = False
        recovery_ready = False
        terminal_ready = True

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
        "execution_receipt_sha256": receipt["receipt_sha256"],
        "source_rollover_post_audit_sha256": receipt[
            "source_rollover_post_audit_sha256"
        ],
        "rollover_entry_request_sha256": receipt[
            "rollover_entry_request_sha256"
        ],
        "rollover_entry_input_verification_sha256": receipt[
            "rollover_entry_input_verification_sha256"
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
        "source_previous_tick_post_audit_sha256": receipt[
            "source_previous_tick_post_audit_sha256"
        ],
        "previous_tick_observed_at": receipt[
            "previous_tick_observed_at"
        ],
        "requested_position_ids": requested,
        "incumbent_position_id": receipt["incumbent_position_id"],
        "challenger_position_id": receipt["challenger_position_id"],
        "evidence_cycle_id": receipt["evidence_cycle_id"],
        "expected_run_id": receipt["expected_run_id"],
        "target_chain_observed_at": receipt[
            "target_chain_observed_at"
        ],
        "receipt_tick_status": receipt["tick_status"],
        "receipt_pair_tick_complete": complete,
        "receipt_pair_tick_partial_failure": partial,
        "fresh_run": fresh_run,
        "fresh_run_items": fresh_items,
        "pair_valuations": valuations,
        "fresh_positions": positions,
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
        "applied_items_have_applied_valuations": True,
        "pair_valuation_scope_verified": True,
        "ledger_audit_passing": True,
        "cycle_model_binding_verified": True,
        "tick_complete_verified": complete or partial,
        "tick_partial_failure_verified": complete or partial,
        "pair_both_open": both_open,
        "pair_any_closed": any_closed,
        "next_debt_type": next_debt,
        "next_scope": receipt["challenger_model_id"],
        "continuation_route": route,
        "next_evidence_tick_review_ready": next_tick_ready,
        "tick_recovery_review_ready": recovery_ready,
        "terminal_pair_evaluation_ready": terminal_ready,
        "separate_next_action_authorization_required": True,
        "post_tick_audit_ready": True,
        "paper_supervisor_tick_authorized": False,
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
        "post_audit_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_paired_ml_paper_repeat_rollover_continuation_evidence_tick_post_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit one completed repeat-rollover-continuation pair-scoped Phase 8 PAPER evidence tick. "
            "The audit validates the persisted run, per-position valuation "
            "state and full PAPER ledger, then routes to the next evidence "
            "review, recovery review or terminal pair evaluation. It performs "
            "no PAPER or live mutation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--execution-receipt", required=True)
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_repeat_rollover_continuation_evidence_tick_post_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        execution_receipt_path=args.execution_receipt,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
