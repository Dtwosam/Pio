from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE8_PAIRED_ML_PAPER_REPEAT_TERMINAL_SETTLEMENT_READINESS_V1"

TERMINAL_EVALUATION_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_repeat_terminal_evaluation.py"
)
REVIEWED_SOURCE_BLOBS = {
    TERMINAL_EVALUATION_TOOL: "d771a7daa0a73154442012309bc5ded98edd4303",
}

STATUS_READY = "READY"
STATUS_WAITING_NEW_CHAIN = "WAITING_NEW_CHAIN"
STATUS_WAITING_CHAIN_FRESHNESS = "WAITING_CHAIN_FRESHNESS"
STATUS_WAITING_QUOTES = "WAITING_QUOTES"

DEFAULT_CHAIN_MAX_AGE_SECONDS = 300
DEFAULT_QUOTE_MAX_AGE_SECONDS = 300

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_terminal_evaluation_sha256",
    "source_terminal_post_audit_sha256",
    "source_repeat_post_audit_sha256",
    "source_final_evaluation_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
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
    "evaluation_as_of",
    "chain_max_age_seconds",
    "quote_max_age_seconds",
    "latest_chain_observed_at",
    "latest_chain_age_seconds",
    "chain_newer_than_terminal_tick",
    "chain_fresh",
    "required_quote_mints",
    "quote_statuses",
    "fresh_quote_map",
    "quotes_ready",
    "open_position_still_open",
    "closed_position_still_closed",
    "open_counterfactual_binding_present",
    "cycle_status",
    "cycle_active_key",
    "fresh_incumbent_model_status",
    "fresh_challenger_model_status",
    "cycle_model_binding_valid",
    "readiness_status",
    "settlement_tick_request_ready",
    "requires_separate_settlement_tick_authorization",
    "requires_fresh_recheck_before_execution",
    "explicit_single_position_scope_required",
    "derived_pool_safety_required_at_execution",
    "read_only",
    "paper_settlement_tick_authorized",
    "paper_settlement_tick_executed",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
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


def _parse_time(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("paired PAPER settlement timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_terminal_evaluation(source: Path) -> Any:
    path = source / TERMINAL_EVALUATION_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed paired PAPER terminal evaluation is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[
        TERMINAL_EVALUATION_TOOL
    ]:
        raise ValueError("reviewed paired PAPER terminal evaluation blob mismatch")
    return _load_module(
        path,
        "phase8_paired_paper_repeat_terminal_settlement_evaluation",
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


def _database_path(production: Path) -> Path:
    path = (production / "data" / "pio.db").resolve()
    if path.is_symlink() or not path.is_file():
        raise ValueError("production Pio database is invalid")
    return path


def _database_state(database: Path) -> dict[str, str | None]:
    wal = Path(str(database) + "-wal")
    shm = Path(str(database) + "-shm")
    return {
        "database": _sha256_path(database),
        "wal": _sha256_path(wal) if wal.is_file() else None,
        "shm": _sha256_path(shm) if shm.is_file() else None,
    }


def _required_quote_mints(
    conn: sqlite3.Connection,
    *,
    position_id: str,
) -> tuple[str, ...]:
    row = conn.execute(
        """
        SELECT token_x_mint, token_y_mint, reward_mint_0, reward_mint_1
        FROM paper_counterfactual_positions
        WHERE position_id = ?
        LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            "paired PAPER settlement counterfactual binding is missing"
        )
    token_x, token_y, reward_0, reward_1 = row
    x = str(token_x)
    y = str(token_y)
    if not y:
        raise ValueError("paired PAPER settlement token-Y mint is missing")

    required = {y}
    for reward in (reward_0, reward_1):
        if reward is None:
            continue
        mint = str(reward)
        if mint and mint not in {x, y}:
            required.add(mint)
    return tuple(sorted(required))


def _quote_status(
    conn: sqlite3.Connection,
    *,
    token_mint: str,
    as_of: datetime,
    max_age_seconds: int,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT quote_per_atomic, source, observed_at
        FROM token_quote_observations
        WHERE token_mint = ?
          AND quote_unit = 'USD'
          AND julianday(observed_at) <= julianday(?)
        ORDER BY julianday(observed_at) DESC, id DESC
        LIMIT 1
        """,
        (token_mint, _format_time(as_of)),
    ).fetchone()
    if row is None:
        return {
            "token_mint": token_mint,
            "available": False,
            "fresh": False,
            "quote_per_atomic": None,
            "source": None,
            "observed_at": None,
            "age_seconds": None,
            "reason": "no persisted quote observation",
        }

    observed = _parse_time(str(row[2]))
    age = max(0, int((as_of - observed).total_seconds()))
    quote = float(row[0])
    fresh = quote > 0 and age <= max_age_seconds
    reason = None
    if quote <= 0:
        reason = "persisted quote must be positive"
    elif not fresh:
        reason = f"quote age {age} seconds exceeds limit"
    return {
        "token_mint": token_mint,
        "available": True,
        "fresh": fresh,
        "quote_per_atomic": quote,
        "source": str(row[1]),
        "observed_at": str(row[2]),
        "age_seconds": age,
        "reason": reason,
    }


def validate_phase8_paired_ml_paper_repeat_terminal_settlement_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("paired PAPER settlement readiness must be an object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("paired PAPER settlement readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported paired PAPER settlement readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected paired PAPER settlement readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("paired PAPER settlement readiness lineage mismatch")

    for field in (
        "source_terminal_evaluation_sha256",
        "source_terminal_post_audit_sha256",
        "source_repeat_post_audit_sha256",
        "source_final_evaluation_sha256",
        "pio_database_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"paired PAPER settlement readiness {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"paired PAPER settlement readiness {field} is invalid"
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
        "open_position_id",
        "closed_position_id",
        "open_position_policy_source",
        "open_position_model_id",
        "evaluation_as_of",
        "cycle_status",
        "cycle_active_key",
        "fresh_incumbent_model_status",
        "fresh_challenger_model_status",
        "readiness_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"paired PAPER settlement readiness {field} is invalid"
            )

    if report["pair_id"] == report["previous_pair_id"]:
        raise ValueError("repeat paired PAPER settlement pair id was not advanced")
    if report["open_position_id"] == report["closed_position_id"]:
        raise ValueError("paired PAPER settlement position scope collapsed")
    if {
        report["open_position_id"],
        report["closed_position_id"],
    } != {
        report["incumbent_position_id"],
        report["challenger_position_id"],
    }:
        raise ValueError("paired PAPER settlement position scope mismatch")
    if report["open_position_policy_source"] not in {
        "ML_CHAMPION",
        "ML_CHALLENGER",
    }:
        raise ValueError("paired PAPER settlement open policy source is invalid")
    if not isinstance(report.get("required_quote_mints"), list):
        raise ValueError("paired PAPER settlement required quote mints invalid")
    if not isinstance(report.get("quote_statuses"), list):
        raise ValueError("paired PAPER settlement quote statuses invalid")
    if not isinstance(report.get("fresh_quote_map"), dict):
        raise ValueError("paired PAPER settlement quote map invalid")

    for field in (
        "open_position_still_open",
        "closed_position_still_closed",
        "open_counterfactual_binding_present",
        "cycle_model_binding_valid",
        "requires_separate_settlement_tick_authorization",
        "requires_fresh_recheck_before_execution",
        "explicit_single_position_scope_required",
        "derived_pool_safety_required_at_execution",
        "read_only",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"paired PAPER settlement readiness requires {field}=true"
            )

    for field in (
        "paper_settlement_tick_authorized",
        "paper_settlement_tick_executed",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"paired PAPER settlement readiness requires {field}=false"
            )

    ready = report["readiness_status"] == STATUS_READY
    if report["settlement_tick_request_ready"] is not ready:
        raise ValueError(
            "paired PAPER settlement readiness request-ready mismatch"
        )
    if ready:
        if report["chain_newer_than_terminal_tick"] is not True:
            raise ValueError(
                "paired PAPER settlement READY state requires newer chain"
            )
        if report["chain_fresh"] is not True:
            raise ValueError(
                "paired PAPER settlement READY state requires fresh chain"
            )
        if report["quotes_ready"] is not True:
            raise ValueError(
                "paired PAPER settlement READY state requires fresh quotes"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("paired PAPER settlement readiness digest mismatch")


def build_phase8_paired_ml_paper_repeat_terminal_settlement_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    terminal_evaluation_path: str | Path,
    as_of: str | None = None,
    chain_max_age_seconds: int = DEFAULT_CHAIN_MAX_AGE_SECONDS,
    quote_max_age_seconds: int = DEFAULT_QUOTE_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")
    if chain_max_age_seconds < 0 or quote_max_age_seconds < 0:
        raise ValueError("paired PAPER settlement age limits cannot be negative")

    terminal_module = _load_terminal_evaluation(source)
    terminal = _load_json(
        terminal_evaluation_path,
        label="paired PAPER terminal evaluation",
    )
    terminal_module.validate_phase8_paired_ml_paper_repeat_terminal_evaluation(
        terminal
    )
    if terminal.get("pair_settlement_review_ready") is not True:
        raise ValueError(
            "paired PAPER terminal evaluation does not route to settlement"
        )
    if terminal.get("pair_has_open_remainder") is not True:
        raise ValueError(
            "paired PAPER settlement requires exactly one open pair leg"
        )
    if terminal.get("next_debt_type") != terminal_module.DEBT_PAIR_SETTLEMENT:
        raise ValueError("paired PAPER terminal settlement debt mismatch")
    if terminal.get("paper_pair_settlement_authorized") is not False:
        raise ValueError(
            "paired PAPER terminal evaluation already authorizes settlement"
        )
    if terminal["pair_id"] == terminal["previous_pair_id"]:
        raise ValueError(
            "repeat paired PAPER terminal settlement pair id was not advanced"
        )

    database = _database_path(production)
    if Path(str(terminal["pio_database_path"])).resolve() != database:
        raise ValueError("paired PAPER settlement database binding mismatch")
    before = _database_state(database)
    expected = {
        "database": terminal["pio_database_sha256_after"],
        "wal": terminal["pio_wal_sha256_after"],
        "shm": terminal["pio_shm_sha256_after"],
    }
    if before != expected:
        raise ValueError(
            "Pio database changed after paired PAPER terminal evaluation"
        )

    evaluation = (
        _parse_time(as_of)
        if as_of is not None
        else datetime.now(timezone.utc)
    )
    positions = (
        terminal["incumbent_position_id"],
        terminal["challenger_position_id"],
    )

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT position_id, status, pool_address, policy_source, model_id
            FROM paper_positions
            WHERE position_id IN (?, ?)
            """,
            positions,
        ).fetchall()
        if len(rows) != 2:
            raise ValueError("paired PAPER settlement positions are incomplete")
        by_id = {
            str(row[0]): {
                "status": str(row[1]),
                "pool_address": str(row[2]),
                "policy_source": str(row[3]),
                "model_id": str(row[4]),
            }
            for row in rows
        }
        open_ids = [
            position_id
            for position_id, item in by_id.items()
            if item["status"] == "OPEN"
        ]
        closed_ids = [
            position_id
            for position_id, item in by_id.items()
            if item["status"] == "CLOSED"
        ]
        if len(open_ids) != 1 or len(closed_ids) != 1:
            raise ValueError(
                "paired PAPER settlement requires one OPEN and one CLOSED leg"
            )
        open_id = open_ids[0]
        closed_id = closed_ids[0]
        open_row = by_id[open_id]
        if open_row["pool_address"] != terminal["pool_address"]:
            raise ValueError("paired PAPER settlement open pool binding changed")

        expected_open_policy = (
            "ML_CHAMPION"
            if open_id == terminal["incumbent_position_id"]
            else "ML_CHALLENGER"
        )
        expected_open_model = (
            terminal["incumbent_model_id"]
            if open_id == terminal["incumbent_position_id"]
            else terminal["challenger_model_id"]
        )
        if open_row["policy_source"] != expected_open_policy:
            raise ValueError(
                "paired PAPER settlement open policy binding changed"
            )
        if open_row["model_id"] != expected_open_model:
            raise ValueError(
                "paired PAPER settlement open model binding changed"
            )

        required_mints = _required_quote_mints(
            conn,
            position_id=open_id,
        )

        chain_row = conn.execute(
            """
            SELECT observed_at
            FROM chain_pool_snapshots
            WHERE pool_address = ?
              AND julianday(observed_at) <= julianday(?)
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (terminal["pool_address"], _format_time(evaluation)),
        ).fetchone()
        latest_chain = str(chain_row[0]) if chain_row is not None else None
        if latest_chain is None:
            chain_age = None
            chain_newer = False
            chain_fresh = False
        else:
            chain_time = _parse_time(latest_chain)
            chain_age = max(
                0,
                int((evaluation - chain_time).total_seconds()),
            )
            chain_newer = chain_time > _parse_time(
                terminal["terminal_tick_observed_at"]
            )
            chain_fresh = chain_age <= chain_max_age_seconds

        quote_statuses = [
            _quote_status(
                conn,
                token_mint=mint,
                as_of=evaluation,
                max_age_seconds=quote_max_age_seconds,
            )
            for mint in required_mints
        ]

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
            (terminal["active_cycle_id"],),
        ).fetchone()
        if cycle is None:
            raise ValueError("paired PAPER settlement active cycle is missing")
    finally:
        conn.close()

    cycle_valid = (
        str(cycle[0]) == "PAPER_CHALLENGER"
        and str(cycle[1]) == "ACTIVE"
        and str(cycle[2]) == terminal["incumbent_model_id"]
        and str(cycle[3]) == terminal["challenger_model_id"]
        and str(cycle[4]) == "CHAMPION"
        and str(cycle[5]) == "PAPER_CHALLENGER"
    )
    if not cycle_valid:
        raise ValueError("paired PAPER settlement cycle/model binding changed")

    quote_map = {
        item["token_mint"]: item["quote_per_atomic"]
        for item in quote_statuses
        if item["fresh"] and item["quote_per_atomic"] is not None
    }
    quotes_ready = len(quote_map) == len(required_mints)

    if not chain_newer:
        status = STATUS_WAITING_NEW_CHAIN
    elif not chain_fresh:
        status = STATUS_WAITING_CHAIN_FRESHNESS
    elif not quotes_ready:
        status = STATUS_WAITING_QUOTES
    else:
        status = STATUS_READY

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "paired PAPER settlement readiness changed production database"
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
        "source_terminal_evaluation_sha256": terminal["evaluation_sha256"],
        "source_terminal_post_audit_sha256": terminal[
            "source_terminal_post_audit_sha256"
        ],
        "source_repeat_post_audit_sha256": terminal[
            "source_repeat_post_audit_sha256"
        ],
        "source_final_evaluation_sha256": terminal[
            "source_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": before["database"],
        "pio_wal_sha256": before["wal"],
        "pio_shm_sha256": before["shm"],
        "active_cycle_id": terminal["active_cycle_id"],
        "incumbent_model_id": terminal["incumbent_model_id"],
        "challenger_model_id": terminal["challenger_model_id"],
        "account_id": terminal["account_id"],
        "previous_pair_id": terminal["previous_pair_id"],
        "pair_id": terminal["pair_id"],
        "pool_address": terminal["pool_address"],
        "entry_observed_at": terminal["entry_observed_at"],
        "terminal_tick_observed_at": terminal["terminal_tick_observed_at"],
        "incumbent_position_id": terminal["incumbent_position_id"],
        "challenger_position_id": terminal["challenger_position_id"],
        "open_position_id": open_id,
        "closed_position_id": closed_id,
        "open_position_policy_source": expected_open_policy,
        "open_position_model_id": expected_open_model,
        "evaluation_as_of": _format_time(evaluation),
        "chain_max_age_seconds": chain_max_age_seconds,
        "quote_max_age_seconds": quote_max_age_seconds,
        "latest_chain_observed_at": latest_chain,
        "latest_chain_age_seconds": chain_age,
        "chain_newer_than_terminal_tick": chain_newer,
        "chain_fresh": chain_fresh,
        "required_quote_mints": list(required_mints),
        "quote_statuses": quote_statuses,
        "fresh_quote_map": quote_map,
        "quotes_ready": quotes_ready,
        "open_position_still_open": True,
        "closed_position_still_closed": True,
        "open_counterfactual_binding_present": True,
        "cycle_status": str(cycle[0]),
        "cycle_active_key": str(cycle[1]),
        "fresh_incumbent_model_status": str(cycle[4]),
        "fresh_challenger_model_status": str(cycle[5]),
        "cycle_model_binding_valid": True,
        "readiness_status": status,
        "settlement_tick_request_ready": status == STATUS_READY,
        "requires_separate_settlement_tick_authorization": True,
        "requires_fresh_recheck_before_execution": True,
        "explicit_single_position_scope_required": True,
        "derived_pool_safety_required_at_execution": True,
        "read_only": True,
        "paper_settlement_tick_authorized": False,
        "paper_settlement_tick_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_repeat_terminal_settlement_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build read-only readiness for one remaining OPEN leg of a "
            "terminal repeat paired Phase 8 PAPER comparison. The tool requires a "
            "newer fresh chain snapshot, fresh required quotes and unchanged "
            "cycle/model bindings. It authorizes no PAPER mutation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--terminal-evaluation", required=True)
    parser.add_argument("--as-of")
    parser.add_argument(
        "--chain-max-age-seconds",
        type=int,
        default=DEFAULT_CHAIN_MAX_AGE_SECONDS,
    )
    parser.add_argument(
        "--quote-max-age-seconds",
        type=int,
        default=DEFAULT_QUOTE_MAX_AGE_SECONDS,
    )
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_repeat_terminal_settlement_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        terminal_evaluation_path=args.terminal_evaluation,
        as_of=args.as_of,
        chain_max_age_seconds=args.chain_max_age_seconds,
        quote_max_age_seconds=args.quote_max_age_seconds,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
