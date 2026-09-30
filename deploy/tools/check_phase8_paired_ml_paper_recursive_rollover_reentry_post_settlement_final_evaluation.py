from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_POST_SETTLEMENT_FINAL_EVALUATION_V1"
)

SETTLEMENT_POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_recursive_rollover_reentry_terminal_settlement_tick_post_audit.py"
)
CONTINUOUS_PROMOTION_MODULE = Path(
    "python-learner/src/meteora_learner/continuous_promotion.py"
)
REVIEWED_SOURCE_BLOBS = {
    SETTLEMENT_POST_AUDIT_TOOL: "045694c696f7928ebd3bd80dbda9a00cef1a905c",
    CONTINUOUS_PROMOTION_MODULE: "6ae4acd70be4e03e7e2b2f5c410e37098b4b8bc6",
}

DEBT_MORE_EVIDENCE = "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
DEBT_PROMOTION_REVIEW = "PAPER_CHALLENGER_PROMOTION_REVIEW_REQUIRED"
DEBT_VALIDATION_REVIEW = "PAPER_CHALLENGER_VALIDATION_REVIEW_REQUIRED"

ROUTE_NEXT_PAIR = "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_NEXT_PAIR_REVIEW"
ROUTE_PROMOTION = "PHASE8_CONTINUOUS_CHALLENGER_PROMOTION_REVIEW"
ROUTE_VALIDATION = "PHASE8_CONTINUOUS_CHALLENGER_VALIDATION_REVIEW"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_settlement_post_audit_sha256",
    "source_terminal_evaluation_sha256",
    "source_terminal_post_audit_sha256",
    "source_recursive_rollover_reentry_post_audit_sha256",
    "recursive_rollover_reentry_entry_request_sha256",
    "recursive_rollover_reentry_input_verification_sha256",
    "source_prior_final_evaluation_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
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
    "final_settlement_observed_at",
    "incumbent_position",
    "challenger_position",
    "pair_both_closed",
    "pair_policy_model_bindings_verified",
    "continuous_validation",
    "continuous_validation_sha256",
    "continuous_validation_criteria",
    "incumbent_closed_trades",
    "challenger_closed_trades",
    "required_incumbent_closed_trades",
    "required_challenger_closed_trades",
    "incumbent_closed_trades_remaining",
    "challenger_closed_trades_remaining",
    "closed_trade_floor_met",
    "continuous_validation_qualified",
    "continuous_validation_reasons",
    "next_debt_type",
    "next_scope",
    "continuation_route",
    "next_pair_review_ready",
    "promotion_review_ready",
    "validation_review_ready",
    "separate_next_action_authorization_required",
    "read_only",
    "new_pair_entry_authorized",
    "continuous_promotion_authorized",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
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


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    wal = Path(str(database) + "-wal")
    shm = Path(str(database) + "-shm")
    return {
        "database": _sha256_path(database),
        "wal": _sha256_path(wal) if wal.is_file() else None,
        "shm": _sha256_path(shm) if shm.is_file() else None,
    }


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
                f"paired PAPER final evaluation dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"paired PAPER final evaluation dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / SETTLEMENT_POST_AUDIT_TOOL,
            "phase8_recursive_rollover_reentry_paired_paper_final_settlement_audit",
        ),
        _load_module(
            source / CONTINUOUS_PROMOTION_MODULE,
            "meteora_learner.phase8_recursive_rollover_reentry_paired_paper_final_promotion",
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


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    elif is_dataclass(value):
        result = asdict(value)
    elif isinstance(value, dict):
        result = value
    else:
        raise ValueError("paired PAPER final validation is not recordable")
    if not isinstance(result, dict):
        raise ValueError("paired PAPER final validation record is invalid")
    return result


def _position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        SELECT position_id, account_id, pool_address, status,
               policy_source, model_id, opened_at, closed_at,
               entry_capital_quote, realized_pnl_quote
        FROM paper_positions
        WHERE position_id = ?
        LIMIT 1
        """,
        (position_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(
            f"paired PAPER final evaluation position missing: {position_id}"
        )
    names = [item[0] for item in cursor.description or ()]
    value = {name: row[index] for index, name in enumerate(names)}
    value["entry_capital_quote"] = float(value["entry_capital_quote"])
    if value["realized_pnl_quote"] is not None:
        value["realized_pnl_quote"] = float(value["realized_pnl_quote"])
    return value


def validate_phase8_paired_ml_paper_recursive_rollover_reentry_post_settlement_final_evaluation(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("paired PAPER final evaluation must be an object")
    if set(report) != set(REPORT_FIELDS) | {"evaluation_sha256"}:
        raise ValueError("paired PAPER final evaluation schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported paired PAPER final evaluation format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected paired PAPER final evaluation type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("paired PAPER final evaluation lineage mismatch")

    for field in (
        "source_settlement_post_audit_sha256",
        "source_terminal_evaluation_sha256",
        "source_terminal_post_audit_sha256",
        "source_recursive_rollover_reentry_post_audit_sha256",
        "recursive_rollover_reentry_entry_request_sha256",
        "recursive_rollover_reentry_input_verification_sha256",
        "source_prior_final_evaluation_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "continuous_validation_sha256",
        "evaluation_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"paired PAPER final evaluation {field} is invalid"
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
                f"paired PAPER final evaluation {field} is invalid"
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
        "entry_observed_at",
        "terminal_tick_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "final_settlement_observed_at",
        "next_debt_type",
        "next_scope",
        "continuation_route",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"paired PAPER final evaluation {field} is invalid"
            )

    if report["pair_id"] == report["previous_pair_id"]:
        raise ValueError(
            "recursive rollover re-entry paired PAPER final evaluation pair id was not advanced"
        )

    for field in (
        "incumbent_position",
        "challenger_position",
        "continuous_validation",
        "continuous_validation_criteria",
    ):
        if not isinstance(report.get(field), dict):
            raise ValueError(
                f"paired PAPER final evaluation {field} is invalid"
            )
    if not isinstance(report.get("continuous_validation_reasons"), list):
        raise ValueError("paired PAPER final evaluation reasons are invalid")

    for field in (
        "pair_both_closed",
        "pair_policy_model_bindings_verified",
        "separate_next_action_authorization_required",
        "read_only",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"paired PAPER final evaluation requires {field}=true"
            )

    for field in (
        "new_pair_entry_authorized",
        "continuous_promotion_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"paired PAPER final evaluation requires {field}=false"
            )

    if report["next_scope"] != report["challenger_model_id"]:
        raise ValueError("paired PAPER final evaluation next scope mismatch")
    if report["incumbent_closed_trades_remaining"] != max(
        0,
        report["required_incumbent_closed_trades"]
        - report["incumbent_closed_trades"],
    ):
        raise ValueError(
            "paired PAPER final evaluation incumbent remaining mismatch"
        )
    if report["challenger_closed_trades_remaining"] != max(
        0,
        report["required_challenger_closed_trades"]
        - report["challenger_closed_trades"],
    ):
        raise ValueError(
            "paired PAPER final evaluation challenger remaining mismatch"
        )
    expected_floor = (
        report["incumbent_closed_trades"]
        >= report["required_incumbent_closed_trades"]
        and report["challenger_closed_trades"]
        >= report["required_challenger_closed_trades"]
    )
    if report["closed_trade_floor_met"] is not expected_floor:
        raise ValueError(
            "paired PAPER final evaluation closed-trade floor mismatch"
        )

    if not report["closed_trade_floor_met"]:
        if report["next_debt_type"] != DEBT_MORE_EVIDENCE:
            raise ValueError(
                "paired PAPER final evaluation more-evidence debt mismatch"
            )
        if report["continuation_route"] != ROUTE_NEXT_PAIR:
            raise ValueError(
                "paired PAPER final evaluation next-pair route mismatch"
            )
        if report["next_pair_review_ready"] is not True:
            raise ValueError(
                "paired PAPER final evaluation next-pair review not ready"
            )
        if (
            report["promotion_review_ready"]
            or report["validation_review_ready"]
        ):
            raise ValueError(
                "paired PAPER final evaluation mixed next-pair route"
            )
    elif report["continuous_validation_qualified"]:
        if report["next_debt_type"] != DEBT_PROMOTION_REVIEW:
            raise ValueError(
                "paired PAPER final evaluation promotion debt mismatch"
            )
        if report["continuation_route"] != ROUTE_PROMOTION:
            raise ValueError(
                "paired PAPER final evaluation promotion route mismatch"
            )
        if report["promotion_review_ready"] is not True:
            raise ValueError(
                "paired PAPER final evaluation promotion review not ready"
            )
        if (
            report["next_pair_review_ready"]
            or report["validation_review_ready"]
        ):
            raise ValueError(
                "paired PAPER final evaluation mixed promotion route"
            )
    else:
        if report["next_debt_type"] != DEBT_VALIDATION_REVIEW:
            raise ValueError(
                "paired PAPER final evaluation validation debt mismatch"
            )
        if report["continuation_route"] != ROUTE_VALIDATION:
            raise ValueError(
                "paired PAPER final evaluation validation route mismatch"
            )
        if report["validation_review_ready"] is not True:
            raise ValueError(
                "paired PAPER final evaluation validation review not ready"
            )
        if (
            report["next_pair_review_ready"]
            or report["promotion_review_ready"]
        ):
            raise ValueError(
                "paired PAPER final evaluation mixed validation route"
            )

    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError("paired PAPER final evaluation modified database")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("paired PAPER final evaluation modified WAL")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("paired PAPER final evaluation modified SHM")

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["evaluation_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("paired PAPER final evaluation digest mismatch")


def build_phase8_paired_ml_paper_recursive_rollover_reentry_post_settlement_final_evaluation(
    *,
    repository: str | Path,
    source_tree: str | Path,
    settlement_post_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    audit_module, promotion_module = _load_reviewed(source)
    audit = _load_json(
        settlement_post_audit_path,
        label="paired PAPER settlement post-audit",
    )
    audit_module.validate_phase8_paired_ml_paper_recursive_rollover_reentry_terminal_settlement_tick_post_audit(
        audit
    )
    if audit.get("final_pair_evaluation_ready") is not True:
        raise ValueError(
            "paired PAPER settlement post-audit is not final-evaluation ready"
        )
    if audit.get("open_leg_now_closed") is not True:
        raise ValueError(
            "paired PAPER final evaluation requires the remaining leg closed"
        )
    if audit.get("open_leg_still_open") is not False:
        raise ValueError(
            "paired PAPER final evaluation refuses an open remainder"
        )
    if audit.get("receipt_settlement_tick_partial_failure") is not False:
        raise ValueError(
            "paired PAPER final evaluation refuses partial settlement failure"
        )
    if audit.get("next_debt_type") != audit_module.DEBT_FINAL_EVALUATION:
        raise ValueError(
            "paired PAPER settlement final-evaluation debt mismatch"
        )
    if audit.get("continuation_route") != audit_module.ROUTE_FINAL_EVALUATION:
        raise ValueError(
            "paired PAPER settlement final-evaluation route mismatch"
        )
    if audit["pair_id"] == audit["previous_pair_id"]:
        raise ValueError(
            "recursive rollover re-entry paired PAPER final evaluation pair id was not advanced"
        )

    database = (production / "data" / "pio.db").resolve()
    if database.is_symlink() or not database.is_file():
        raise ValueError("production Pio database is invalid")
    if Path(str(audit["pio_database_path"])).resolve() != database:
        raise ValueError(
            "paired PAPER final evaluation database binding mismatch"
        )
    before = _database_state(database)
    expected = {
        "database": audit["audit_database_sha256_after"],
        "wal": audit["audit_wal_sha256_after"],
        "shm": audit["audit_shm_sha256_after"],
    }
    if before != expected:
        raise ValueError(
            "Pio database changed after paired PAPER settlement post-audit"
        )

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        incumbent = _position(
            conn,
            position_id=audit["incumbent_position_id"],
        )
        challenger = _position(
            conn,
            position_id=audit["challenger_position_id"],
        )
    finally:
        conn.close()

    if incumbent["status"] != "CLOSED" or challenger["status"] != "CLOSED":
        raise ValueError(
            "paired PAPER final evaluation requires both positions CLOSED"
        )
    if (
        incumbent["account_id"] != audit["account_id"]
        or challenger["account_id"] != audit["account_id"]
        or incumbent["pool_address"] != audit["pool_address"]
        or challenger["pool_address"] != audit["pool_address"]
        or incumbent["policy_source"] != "ML_CHAMPION"
        or challenger["policy_source"] != "ML_CHALLENGER"
        or incumbent["model_id"] != audit["incumbent_model_id"]
        or challenger["model_id"] != audit["challenger_model_id"]
    ):
        raise ValueError(
            "paired PAPER final position policy/model binding changed"
        )

    criteria = promotion_module.ContinuousChampionCriteria()
    validation = promotion_module.evaluate_continuous_champion(
        promotion_module.Storage(database),
        cycle_id=audit["active_cycle_id"],
        account_id=audit["account_id"],
        criteria=criteria,
    )
    validation_record = _record(validation)
    criteria_record = _record(criteria)
    if validation.policy_actionable is not False:
        raise ValueError(
            "paired PAPER final validation became policy-actionable"
        )
    if validation.incumbent_model_id != audit["incumbent_model_id"]:
        raise ValueError("paired PAPER final incumbent model changed")
    if validation.challenger_model_id != audit["challenger_model_id"]:
        raise ValueError("paired PAPER final challenger model changed")

    incumbent_closed = int(validation.incumbent.closed_trades)
    challenger_closed = int(validation.challenger.closed_trades)
    required_incumbent = int(criteria.min_incumbent_closed_trades)
    required_challenger = int(criteria.min_challenger_closed_trades)
    floor_met = (
        incumbent_closed >= required_incumbent
        and challenger_closed >= required_challenger
    )

    if not floor_met:
        next_debt = DEBT_MORE_EVIDENCE
        route = ROUTE_NEXT_PAIR
        next_pair_ready = True
        promotion_ready = False
        validation_ready = False
    elif validation.qualified:
        next_debt = DEBT_PROMOTION_REVIEW
        route = ROUTE_PROMOTION
        next_pair_ready = False
        promotion_ready = True
        validation_ready = False
    else:
        next_debt = DEBT_VALIDATION_REVIEW
        route = ROUTE_VALIDATION
        next_pair_ready = False
        promotion_ready = False
        validation_ready = True

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "paired PAPER final evaluation changed production database"
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
        "source_settlement_post_audit_sha256": audit["post_audit_sha256"],
        "source_terminal_evaluation_sha256": audit[
            "source_terminal_evaluation_sha256"
        ],
        "source_terminal_post_audit_sha256": audit[
            "source_terminal_post_audit_sha256"
        ],
        "source_recursive_rollover_reentry_post_audit_sha256": audit[
            "source_recursive_rollover_reentry_post_audit_sha256"
        ],
        "recursive_rollover_reentry_entry_request_sha256": audit[
            "recursive_rollover_reentry_entry_request_sha256"
        ],
        "recursive_rollover_reentry_input_verification_sha256": audit[
            "recursive_rollover_reentry_input_verification_sha256"
        ],
        "source_prior_final_evaluation_sha256": audit[
            "source_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "active_cycle_id": audit["active_cycle_id"],
        "incumbent_model_id": audit["incumbent_model_id"],
        "challenger_model_id": audit["challenger_model_id"],
        "account_id": audit["account_id"],
        "previous_pair_id": audit["previous_pair_id"],
        "pair_id": audit["pair_id"],
        "pool_address": audit["pool_address"],
        "entry_observed_at": audit["entry_observed_at"],
        "terminal_tick_observed_at": audit["terminal_tick_observed_at"],
        "incumbent_position_id": audit["incumbent_position_id"],
        "challenger_position_id": audit["challenger_position_id"],
        "final_settlement_observed_at": audit["target_chain_observed_at"],
        "incumbent_position": incumbent,
        "challenger_position": challenger,
        "pair_both_closed": True,
        "pair_policy_model_bindings_verified": True,
        "continuous_validation": validation_record,
        "continuous_validation_sha256": _sha256_bytes(
            _canonical_bytes(validation_record)
        ),
        "continuous_validation_criteria": criteria_record,
        "incumbent_closed_trades": incumbent_closed,
        "challenger_closed_trades": challenger_closed,
        "required_incumbent_closed_trades": required_incumbent,
        "required_challenger_closed_trades": required_challenger,
        "incumbent_closed_trades_remaining": max(
            0, required_incumbent - incumbent_closed
        ),
        "challenger_closed_trades_remaining": max(
            0, required_challenger - challenger_closed
        ),
        "closed_trade_floor_met": floor_met,
        "continuous_validation_qualified": bool(validation.qualified),
        "continuous_validation_reasons": list(validation.reasons),
        "next_debt_type": next_debt,
        "next_scope": audit["challenger_model_id"],
        "continuation_route": route,
        "next_pair_review_ready": next_pair_ready,
        "promotion_review_ready": promotion_ready,
        "validation_review_ready": validation_ready,
        "separate_next_action_authorization_required": True,
        "read_only": True,
        "new_pair_entry_authorized": False,
        "continuous_promotion_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "evaluation_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_recursive_rollover_reentry_post_settlement_final_evaluation(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a fully settled paired Phase 8 PAPER comparison. "
            "Both legs must be CLOSED and preserve their original model/policy "
            "bindings. The tool reuses the existing continuous 20-vs-20 PAPER "
            "criteria and routes to another pair, promotion review, or "
            "validation review. It performs no mutation or promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--settlement-post-audit", required=True)
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_recursive_rollover_reentry_post_settlement_final_evaluation(
        repository=args.repo,
        source_tree=args.source_tree,
        settlement_post_audit_path=args.settlement_post_audit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
