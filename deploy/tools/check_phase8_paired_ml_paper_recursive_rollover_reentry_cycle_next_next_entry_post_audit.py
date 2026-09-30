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
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_CYCLE_NEXT_NEXT_ENTRY_POST_EXECUTION_AUDIT_V1"
)

EXECUTOR_TOOL = Path(
    "deploy/tools/run_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_next_entry_once.py"
)
PAPER_AUDIT_MODULE = Path(
    "python-learner/src/meteora_learner/paper_audit.py"
)
REVIEWED_SOURCE_BLOBS = {
    EXECUTOR_TOOL: "8040e38a868c5db0ebf508181221dc2d26445662",
    PAPER_AUDIT_MODULE: "f58e27d7a8c629aca1235daf3b49134e3a63ad25",
}

NEXT_DEBT_TYPE = "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
CONTINUATION_ROUTE = "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_CYCLE_NEXT_NEXT_SUPERVISION_REVIEW"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "execution_receipt_sha256",
    "recursive_rollover_reentry_cycle_next_next_entry_request_sha256",
    "recursive_rollover_reentry_cycle_next_next_input_verification_sha256",
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
    "decision_observed_at",
    "incumbent_position_id",
    "challenger_position_id",
    "incumbent_event_key",
    "challenger_event_key",
    "capital_quote",
    "entry_cost_quote",
    "required_pair_cash_quote",
    "fresh_account_cash_quote",
    "fresh_account_open_positions",
    "incumbent_position",
    "challenger_position",
    "incumbent_enter_event",
    "challenger_enter_event",
    "incumbent_counterfactual_binding",
    "challenger_counterfactual_binding",
    "incumbent_followup_event_count",
    "challenger_followup_event_count",
    "incumbent_chain_valuation_count",
    "challenger_chain_valuation_count",
    "paper_ledger_audit",
    "paper_ledger_audit_sha256",
    "cycle_status",
    "cycle_active_key",
    "fresh_incumbent_status",
    "fresh_challenger_status",
    "receipt_valid",
    "database_matches_receipt",
    "database_unchanged_by_audit",
    "account_cash_matches_receipt",
    "account_open_positions_match_receipt",
    "incumbent_position_verified",
    "challenger_position_verified",
    "equal_capital_verified",
    "enter_events_verified",
    "counterfactual_bindings_verified",
    "no_followup_paper_events_verified",
    "no_chain_valuations_applied_verified",
    "paper_ledger_audit_passing",
    "cycle_model_binding_verified",
    "recursive_rollover_reentry_cycle_next_next_pair_seed_ready_for_supervision",
    "next_debt_type",
    "next_scope",
    "continuation_route",
    "fresh_chain_state_required_before_supervision",
    "fresh_quote_state_required_before_supervision",
    "separate_supervision_authorization_required",
    "post_pair_audit_ready",
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
                f"recursive re-entry cycle next-next paired PAPER post-audit dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"recursive re-entry cycle next-next paired PAPER post-audit dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / EXECUTOR_TOOL,
            "phase8_recursive_reentry_cycle_next_paired_paper_post_audit_executor",
        ),
        _load_module(
            source / PAPER_AUDIT_MODULE,
            "meteora_learner.phase8_repeat_paired_paper_ledger_audit",
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


def _row_dict(
    cursor: sqlite3.Cursor,
    row: tuple[Any, ...],
) -> dict[str, Any]:
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
            f"recursive re-entry cycle next-next paired PAPER post-audit position missing: {position_id}"
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


def _event(
    conn: sqlite3.Connection,
    event_key: str,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        SELECT event_key, account_id, position_id, event_time, event_type,
               cash_delta_quote, position_mark_quote, fee_delta_quote,
               reward_delta_quote, cost_quote, realized_pnl_quote
        FROM paper_events
        WHERE event_key = ?
        LIMIT 1
        """,
        (event_key,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(
            f"recursive re-entry cycle next-next paired PAPER post-audit event missing: {event_key}"
        )
    value = _row_dict(cursor, row)
    for field in (
        "cash_delta_quote",
        "position_mark_quote",
        "fee_delta_quote",
        "reward_delta_quote",
        "cost_quote",
        "realized_pnl_quote",
    ):
        if value[field] is not None:
            value[field] = float(value[field])
    return value


def _counterfactual(
    conn: sqlite3.Connection,
    position_id: str,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        SELECT position_id, pool_address, entry_observed_at,
               amount_x_atomic, amount_y_atomic, capital_quote,
               max_share_bps, favor_x_active, token_x_mint, token_y_mint
        FROM paper_counterfactual_positions
        WHERE position_id = ?
        LIMIT 1
        """,
        (position_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER counterfactual binding missing: "
            + position_id
        )
    value = _row_dict(cursor, row)
    value["amount_x_atomic"] = int(value["amount_x_atomic"])
    value["amount_y_atomic"] = int(value["amount_y_atomic"])
    value["capital_quote"] = float(value["capital_quote"])
    value["max_share_bps"] = int(value["max_share_bps"])
    value["favor_x_active"] = bool(value["favor_x_active"])
    return value


def _count_followups(
    conn: sqlite3.Connection,
    position_id: str,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM paper_events
        WHERE position_id = ?
          AND event_type IN ('MARK', 'REBALANCE', 'EXIT')
        """,
        (position_id,),
    ).fetchone()
    return int(row[0])


def _count_valuations(
    conn: sqlite3.Connection,
    position_id: str,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM paper_chain_valuations
        WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()
    return int(row[0])


def validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_next_entry_post_audit(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"post_audit_sha256"}:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported recursive re-entry cycle next-next paired PAPER post-audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected recursive re-entry cycle next-next paired PAPER post-audit type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit lineage mismatch"
        )

    for field in (
        "execution_receipt_sha256",
        "recursive_rollover_reentry_cycle_next_next_entry_request_sha256",
        "recursive_rollover_reentry_cycle_next_next_input_verification_sha256",
        "source_final_evaluation_sha256",
        "receipt_database_sha256_after",
        "audit_database_sha256_before",
        "audit_database_sha256_after",
        "paper_ledger_audit_sha256",
        "post_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"recursive re-entry cycle next-next paired PAPER post-audit {field} is invalid"
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
                f"recursive re-entry cycle next-next paired PAPER post-audit {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_id",
        "previous_pair_id",
        "pair_id",
        "pool_address",
        "decision_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "incumbent_event_key",
        "challenger_event_key",
        "cycle_status",
        "cycle_active_key",
        "fresh_incumbent_status",
        "fresh_challenger_status",
        "next_debt_type",
        "next_scope",
        "continuation_route",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"recursive re-entry cycle next-next paired PAPER post-audit {field} is invalid"
            )

    if report["pair_id"] == report["previous_pair_id"]:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit pair id was not advanced"
        )

    for field in (
        "incumbent_position",
        "challenger_position",
        "incumbent_enter_event",
        "challenger_enter_event",
        "incumbent_counterfactual_binding",
        "challenger_counterfactual_binding",
        "paper_ledger_audit",
    ):
        if not isinstance(report.get(field), dict):
            raise ValueError(
                f"recursive re-entry cycle next-next paired PAPER post-audit {field} is invalid"
            )

    for field in (
        "receipt_valid",
        "database_matches_receipt",
        "database_unchanged_by_audit",
        "account_cash_matches_receipt",
        "account_open_positions_match_receipt",
        "incumbent_position_verified",
        "challenger_position_verified",
        "equal_capital_verified",
        "enter_events_verified",
        "counterfactual_bindings_verified",
        "no_followup_paper_events_verified",
        "no_chain_valuations_applied_verified",
        "paper_ledger_audit_passing",
        "cycle_model_binding_verified",
        "recursive_rollover_reentry_cycle_next_next_pair_seed_ready_for_supervision",
        "fresh_chain_state_required_before_supervision",
        "fresh_quote_state_required_before_supervision",
        "separate_supervision_authorization_required",
        "post_pair_audit_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"recursive re-entry cycle next-next paired PAPER post-audit requires {field}=true"
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
                f"recursive re-entry cycle next-next paired PAPER post-audit requires {field}=false"
            )

    if report["next_debt_type"] != NEXT_DEBT_TYPE:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit next debt mismatch"
        )
    if report["next_scope"] != report["challenger_model_id"]:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit next scope mismatch"
        )
    if report["continuation_route"] != CONTINUATION_ROUTE:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit route mismatch"
        )
    if report["cycle_status"] != "PAPER_CHALLENGER":
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit cycle status mismatch"
        )
    if report["cycle_active_key"] != "ACTIVE":
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit active cycle mismatch"
        )
    if report["fresh_incumbent_status"] != "CHAMPION":
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit incumbent status mismatch"
        )
    if report["fresh_challenger_status"] != "PAPER_CHALLENGER":
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit challenger status mismatch"
        )
    if report["fresh_account_open_positions"] != 2:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit expected exactly two open positions"
        )
    if report["incumbent_followup_event_count"] != 0:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER incumbent already has follow-up events"
        )
    if report["challenger_followup_event_count"] != 0:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER challenger already has follow-up events"
        )
    if report["incumbent_chain_valuation_count"] != 0:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER incumbent already has chain valuations"
        )
    if report["challenger_chain_valuation_count"] != 0:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER challenger already has chain valuations"
        )

    if report["audit_database_sha256_before"] != report[
        "audit_database_sha256_after"
    ]:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit modified database"
        )
    if report["audit_wal_sha256_before"] != report[
        "audit_wal_sha256_after"
    ]:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit modified WAL"
        )
    if report["audit_shm_sha256_before"] != report[
        "audit_shm_sha256_after"
    ]:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit modified SHM"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["post_audit_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit digest mismatch"
        )


def build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_next_entry_post_audit(
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
        label="recursive re-entry cycle next-next paired PAPER execution receipt",
    )
    executor.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_next_entry_execution_receipt(
        receipt
    )
    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER receipt repository mismatch"
        )

    readiness = executor._load_readiness(source)
    (
        _,
        _,
        _,
        _,
        base_readiness_module,
    ) = readiness._load_reviewed(source)
    base_account_module, _, _, pair_module = (
        base_readiness_module._load_reviewed(source)
    )
    database = base_account_module._production_database(production)
    if Path(str(receipt["pio_database_path"])).resolve() != database:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER receipt database mismatch"
        )

    before = base_account_module._database_state(database)
    expected = {
        "database": receipt["pio_database_sha256_after"],
        "wal": receipt["pio_wal_sha256_after"],
        "shm": receipt["pio_shm_sha256_after"],
    }
    if before != expected:
        raise ValueError(
            "Pio database changed after recursive re-entry cycle next-next paired PAPER receipt"
        )

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        account = conn.execute(
            """
            SELECT cash_quote,
                   (
                       SELECT COUNT(*)
                       FROM paper_positions
                       WHERE account_id = paper_accounts.account_id
                         AND status = 'OPEN'
                   )
            FROM paper_accounts
            WHERE account_id = ?
            LIMIT 1
            """,
            (receipt["account_id"],),
        ).fetchone()
        if account is None:
            raise ValueError(
                "recursive re-entry cycle next-next paired PAPER account is missing"
            )

        incumbent = _position(
            conn,
            receipt["incumbent_position_id"],
        )
        challenger = _position(
            conn,
            receipt["challenger_position_id"],
        )
        incumbent_event = _event(
            conn,
            receipt["incumbent_event_key"],
        )
        challenger_event = _event(
            conn,
            receipt["challenger_event_key"],
        )
        incumbent_binding = _counterfactual(
            conn,
            receipt["incumbent_position_id"],
        )
        challenger_binding = _counterfactual(
            conn,
            receipt["challenger_position_id"],
        )
        incumbent_followups = _count_followups(
            conn,
            receipt["incumbent_position_id"],
        )
        challenger_followups = _count_followups(
            conn,
            receipt["challenger_position_id"],
        )
        incumbent_valuations = _count_valuations(
            conn,
            receipt["incumbent_position_id"],
        )
        challenger_valuations = _count_valuations(
            conn,
            receipt["challenger_position_id"],
        )

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
                "recursive re-entry cycle next-next paired PAPER active cycle is missing"
            )
    finally:
        conn.close()

    ledger = ledger_module.audit_paper_ledger(
        pair_module.Storage(database),
        account_id=receipt["account_id"],
    )
    ledger_record = ledger.to_record()
    if ledger.passing is not True:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER ledger audit failed: "
            + "; ".join(ledger.reasons)
        )

    after = base_account_module._database_state(database)
    if after != before:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER post-audit changed production database"
        )

    fresh_cash = float(account[0])
    fresh_open_positions = int(account[1])
    if fresh_cash != float(receipt["account_cash_quote_after"]):
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER account cash differs from receipt"
        )
    if fresh_open_positions != int(
        receipt["account_open_positions_after"]
    ):
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER open-position count differs from receipt"
        )

    expected_positions = (
        (
            incumbent,
            receipt["incumbent_position"],
            "ML_CHAMPION",
            receipt["incumbent_model_id"],
        ),
        (
            challenger,
            receipt["challenger_position"],
            "ML_CHALLENGER",
            receipt["challenger_model_id"],
        ),
    )
    for fresh, saved, policy_source, model_id in expected_positions:
        if (
            fresh["position_id"] != saved["position_id"]
            or fresh["account_id"] != receipt["account_id"]
            or fresh["pool_address"] != receipt["pool_address"]
            or fresh["status"] != "OPEN"
            or fresh["policy_source"] != policy_source
            or fresh["model_id"] != model_id
            or fresh["opened_at"] != receipt["decision_observed_at"]
            or fresh["entry_capital_quote"]
            != float(receipt["capital_quote"])
            or fresh["entry_cost_quote"]
            != float(receipt["entry_cost_quote"])
        ):
            raise ValueError(
                f"recursive re-entry cycle next-next paired PAPER {policy_source} position differs from receipt"
            )

    for event, event_key, position_id in (
        (
            incumbent_event,
            receipt["incumbent_event_key"],
            receipt["incumbent_position_id"],
        ),
        (
            challenger_event,
            receipt["challenger_event_key"],
            receipt["challenger_position_id"],
        ),
    ):
        if (
            event["event_key"] != event_key
            or event["account_id"] != receipt["account_id"]
            or event["position_id"] != position_id
            or event["event_time"] != receipt["decision_observed_at"]
            or event["event_type"] != "ENTER"
            or event["cash_delta_quote"]
            != -(
                float(receipt["capital_quote"])
                + float(receipt["entry_cost_quote"])
            )
            or event["cost_quote"]
            != float(receipt["entry_cost_quote"])
        ):
            raise ValueError(
                "recursive re-entry cycle next-next paired PAPER ENTER event binding mismatch"
            )

    for binding, position_id in (
        (incumbent_binding, receipt["incumbent_position_id"]),
        (challenger_binding, receipt["challenger_position_id"]),
    ):
        if (
            binding["position_id"] != position_id
            or binding["pool_address"] != receipt["pool_address"]
            or binding["entry_observed_at"]
            != receipt["decision_observed_at"]
            or binding["capital_quote"]
            != float(receipt["capital_quote"])
        ):
            raise ValueError(
                "recursive re-entry cycle next-next paired PAPER counterfactual binding mismatch"
            )

    if incumbent_followups != 0 or challenger_followups != 0:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER pair already has follow-up events"
        )
    if incumbent_valuations != 0 or challenger_valuations != 0:
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER pair already has chain valuations"
        )

    if (
        str(cycle[0]) != "PAPER_CHALLENGER"
        or str(cycle[1]) != "ACTIVE"
        or str(cycle[2]) != receipt["incumbent_model_id"]
        or str(cycle[3]) != receipt["challenger_model_id"]
        or str(cycle[4]) != "CHAMPION"
        or str(cycle[5]) != "PAPER_CHALLENGER"
    ):
        raise ValueError(
            "recursive re-entry cycle next-next paired PAPER cycle/model state changed after entry"
        )

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
        "recursive_rollover_reentry_cycle_next_next_entry_request_sha256": receipt[
            "recursive_rollover_reentry_cycle_next_next_entry_request_sha256"
        ],
        "recursive_rollover_reentry_cycle_next_next_input_verification_sha256": receipt[
            "recursive_rollover_reentry_cycle_next_next_input_verification_sha256"
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
        "decision_observed_at": receipt["decision_observed_at"],
        "incumbent_position_id": receipt["incumbent_position_id"],
        "challenger_position_id": receipt["challenger_position_id"],
        "incumbent_event_key": receipt["incumbent_event_key"],
        "challenger_event_key": receipt["challenger_event_key"],
        "capital_quote": receipt["capital_quote"],
        "entry_cost_quote": receipt["entry_cost_quote"],
        "required_pair_cash_quote": receipt["required_pair_cash_quote"],
        "fresh_account_cash_quote": fresh_cash,
        "fresh_account_open_positions": fresh_open_positions,
        "incumbent_position": incumbent,
        "challenger_position": challenger,
        "incumbent_enter_event": incumbent_event,
        "challenger_enter_event": challenger_event,
        "incumbent_counterfactual_binding": incumbent_binding,
        "challenger_counterfactual_binding": challenger_binding,
        "incumbent_followup_event_count": incumbent_followups,
        "challenger_followup_event_count": challenger_followups,
        "incumbent_chain_valuation_count": incumbent_valuations,
        "challenger_chain_valuation_count": challenger_valuations,
        "paper_ledger_audit": ledger_record,
        "paper_ledger_audit_sha256": _sha256_bytes(
            _canonical_bytes(ledger_record)
        ),
        "cycle_status": str(cycle[0]),
        "cycle_active_key": str(cycle[1]),
        "fresh_incumbent_status": str(cycle[4]),
        "fresh_challenger_status": str(cycle[5]),
        "receipt_valid": True,
        "database_matches_receipt": True,
        "database_unchanged_by_audit": True,
        "account_cash_matches_receipt": True,
        "account_open_positions_match_receipt": True,
        "incumbent_position_verified": True,
        "challenger_position_verified": True,
        "equal_capital_verified": True,
        "enter_events_verified": True,
        "counterfactual_bindings_verified": True,
        "no_followup_paper_events_verified": True,
        "no_chain_valuations_applied_verified": True,
        "paper_ledger_audit_passing": True,
        "cycle_model_binding_verified": True,
        "recursive_rollover_reentry_cycle_next_next_pair_seed_ready_for_supervision": True,
        "next_debt_type": NEXT_DEBT_TYPE,
        "next_scope": receipt["challenger_model_id"],
        "continuation_route": CONTINUATION_ROUTE,
        "fresh_chain_state_required_before_supervision": True,
        "fresh_quote_state_required_before_supervision": True,
        "separate_supervision_authorization_required": True,
        "post_pair_audit_ready": True,
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
    validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_next_entry_post_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit one completed recursive rollover paired ML PAPER entry. The audit "
            "rechecks the two new positions, ENTER events, counterfactual "
            "bindings, accumulated PAPER ledger and active model/cycle state. "
            "It proves no follow-up valuation tick has run and authorizes no "
            "PAPER or live mutation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--execution-receipt", required=True)
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_next_next_entry_post_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        execution_receipt_path=args.execution_receipt,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
