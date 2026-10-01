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
ARTIFACT_TYPE = (
    "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V14_CONTINUATION_SUPERVISION_READINESS_V1"
)

CHECKPOINT_TOOL = Path(
    "deploy/tools/build_phase8_recursive_reentry_continuation_checkpoint_v14.py"
)
REVIEWED_SOURCE_BLOBS = {
    CHECKPOINT_TOOL: "86271e77906e4b82c75ef062af2584f3775e7fd0",
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
    "source_checkpoint_sha256",
    "source_post_audit_sha256",
    "pair_entry_request_sha256",
    "pair_entry_input_verification_sha256",
    "source_execution_receipt_sha256",
    "source_pair_entry_post_audit_sha256",
    "pair_lineage_sha256",
    "latest_previous_tick_post_audit_sha256",
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
    "incumbent_position_id",
    "challenger_position_id",
    "requested_position_ids",
    "previous_tick_observed_at",
    "evaluation_as_of",
    "chain_max_age_seconds",
    "quote_max_age_seconds",
    "latest_chain_observed_at",
    "latest_chain_age_seconds",
    "chain_newer_than_previous_tick",
    "chain_fresh",
    "required_quote_mints",
    "quote_statuses",
    "fresh_quote_map",
    "quotes_ready",
    "pair_positions_open",
    "pair_counterfactual_bindings_present",
    "cycle_status",
    "cycle_active_key",
    "fresh_incumbent_model_status",
    "fresh_challenger_model_status",
    "cycle_model_binding_valid",
    "readiness_status",
    "continuation_tick_request_ready",
    "requires_separate_tick_authorization",
    "requires_fresh_recheck_before_execution",
    "explicit_position_scope_required",
    "derived_pool_safety_required_at_execution",
    "paper_supervisor_tick_authorized",
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
        raise ValueError(
            "paired PAPER continuation timestamps require timezone"
        )
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


def _load_checkpoint(source: Path) -> Any:
    path = source / CHECKPOINT_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed paired PAPER previous-tick checkpoint is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[
        CHECKPOINT_TOOL
    ]:
        raise ValueError(
            "reviewed paired PAPER previous-tick checkpoint blob mismatch"
        )
    return _load_module(
        path,
        "phase8_recursive_reentry_checkpoint_v14_continuation_source",
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
    if not path.is_file() or path.is_symlink():
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
    position_ids: tuple[str, str],
) -> tuple[str, ...]:
    rows = conn.execute(
        """
        SELECT position_id, token_x_mint, token_y_mint,
               reward_mint_0, reward_mint_1
        FROM paper_counterfactual_positions
        WHERE position_id IN (?, ?)
        ORDER BY position_id
        """,
        position_ids,
    ).fetchall()
    if len(rows) != 2:
        raise ValueError(
            "paired PAPER continuation counterfactual bindings are incomplete"
        )

    required: set[str] = set()
    for _, token_x, token_y, reward_0, reward_1 in rows:
        x = str(token_x)
        y = str(token_y)
        if not y:
            raise ValueError(
                "paired PAPER continuation token-Y mint is missing"
            )
        required.add(y)
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
    fresh = age <= max_age_seconds and quote > 0
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


def validate_phase8_recursive_reentry_checkpoint_v14_continuation_supervision_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "paired PAPER continuation readiness must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError(
            "paired PAPER continuation readiness schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported paired PAPER continuation readiness format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected paired PAPER continuation readiness type"
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
            "paired PAPER continuation readiness lineage mismatch"
        )

    for field in (
        "source_checkpoint_sha256",
        "source_post_audit_sha256",
        "pair_entry_request_sha256",
        "pair_entry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "pio_database_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"paired PAPER continuation readiness {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"paired PAPER continuation readiness {field} is invalid"
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
        "incumbent_position_id",
        "challenger_position_id",
        "previous_tick_observed_at",
        "evaluation_as_of",
        "cycle_status",
        "cycle_active_key",
        "fresh_incumbent_model_status",
        "fresh_challenger_model_status",
        "readiness_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"paired PAPER continuation readiness {field} is invalid"
            )

    if report.get("requested_position_ids") != [
        report["incumbent_position_id"],
        report["challenger_position_id"],
    ]:
        raise ValueError(
            "paired PAPER continuation readiness position scope mismatch"
        )
    if report["incumbent_position_id"] == report["challenger_position_id"]:
        raise ValueError(
            "paired PAPER continuation positions must differ"
        )
    if not isinstance(report.get("required_quote_mints"), list):
        raise ValueError(
            "paired PAPER continuation required quote mints are invalid"
        )
    if not isinstance(report.get("quote_statuses"), list):
        raise ValueError(
            "paired PAPER continuation quote statuses are invalid"
        )
    if not isinstance(report.get("fresh_quote_map"), dict):
        raise ValueError(
            "paired PAPER continuation quote map is invalid"
        )

    for field in (
        "pair_positions_open",
        "pair_counterfactual_bindings_present",
        "cycle_model_binding_valid",
        "requires_separate_tick_authorization",
        "requires_fresh_recheck_before_execution",
        "explicit_position_scope_required",
        "derived_pool_safety_required_at_execution",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"paired PAPER continuation readiness requires {field}=true"
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
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"paired PAPER continuation readiness requires {field}=false"
            )

    if report["cycle_status"] != "PAPER_CHALLENGER":
        raise ValueError(
            "paired PAPER continuation cycle status mismatch"
        )
    if report["cycle_active_key"] != "ACTIVE":
        raise ValueError(
            "paired PAPER continuation active cycle mismatch"
        )
    if report["fresh_incumbent_model_status"] != "CHAMPION":
        raise ValueError(
            "paired PAPER continuation incumbent model status mismatch"
        )
    if report["fresh_challenger_model_status"] != "PAPER_CHALLENGER":
        raise ValueError(
            "paired PAPER continuation challenger model status mismatch"
        )

    ready = report["readiness_status"] == STATUS_READY
    if report.get("continuation_tick_request_ready") is not ready:
        raise ValueError(
            "paired PAPER continuation request-ready flag mismatch"
        )
    if ready:
        for field in (
            "chain_newer_than_previous_tick",
            "chain_fresh",
            "quotes_ready",
        ):
            if report.get(field) is not True:
                raise ValueError(
                    f"paired PAPER continuation READY requires {field}=true"
                )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "paired PAPER continuation readiness digest mismatch"
        )


def build_phase8_recursive_reentry_checkpoint_v14_continuation_supervision_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    checkpoint_path: str | Path,
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
        raise ValueError(
            "paired PAPER continuation age limits cannot be negative"
        )

    checkpoint_module = _load_checkpoint(source)
    checkpoint = _load_json(
        checkpoint_path,
        label="recursive re-entry continuation checkpoint v13",
    )
    checkpoint_module.validate_phase8_recursive_reentry_continuation_checkpoint_v14(
        checkpoint
    )
    if checkpoint.get("checkpoint_state") != checkpoint_module.STATE_CONTINUE:
        raise ValueError(
            "recursive re-entry checkpoint does not route to continuation"
        )
    if checkpoint.get("continuation_review_ready") is not True:
        raise ValueError(
            "recursive re-entry checkpoint is not continuation-ready"
        )
    if checkpoint.get("terminal_review_ready") is not False:
        raise ValueError(
            "recursive re-entry continuation refuses terminal checkpoint"
        )
    if checkpoint.get("recovery_review_ready") is not False:
        raise ValueError(
            "recursive re-entry continuation refuses recovery checkpoint"
        )
    if checkpoint.get("pair_both_open") is not True:
        raise ValueError(
            "recursive re-entry continuation requires both positions open"
        )
    if checkpoint.get("pair_any_closed") is not False:
        raise ValueError(
            "recursive re-entry continuation refuses a closed pair leg"
        )
    if checkpoint["pair_id"] == checkpoint["previous_pair_id"]:
        raise ValueError(
            "recursive re-entry continuation pair id was not advanced"
        )

    database = _database_path(production)
    if Path(str(checkpoint["pio_database_path"])).resolve() != database:
        raise ValueError(
            "paired PAPER continuation database binding mismatch"
        )
    before = _database_state(database)

    evaluation = (
        _parse_time(as_of)
        if as_of is not None
        else datetime.now(timezone.utc)
    )
    position_ids = (
        checkpoint["incumbent_position_id"],
        checkpoint["challenger_position_id"],
    )

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        positions = conn.execute(
            """
            SELECT position_id, status, pool_address
            FROM paper_positions
            WHERE position_id IN (?, ?)
            ORDER BY CASE position_id WHEN ? THEN 0 ELSE 1 END
            """,
            (
                position_ids[0],
                position_ids[1],
                position_ids[0],
            ),
        ).fetchall()
        positions_open = (
            len(positions) == 2
            and all(str(row[1]) == "OPEN" for row in positions)
            and all(str(row[2]) == checkpoint["pool_address"] for row in positions)
        )
        if not positions_open:
            raise ValueError(
                "paired PAPER continuation positions are no longer open/bound"
            )

        required_mints = _required_quote_mints(conn, position_ids)

        chain_row = conn.execute(
            """
            SELECT observed_at
            FROM chain_pool_snapshots
            WHERE pool_address = ?
              AND julianday(observed_at) <= julianday(?)
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (checkpoint["pool_address"], _format_time(evaluation)),
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
                checkpoint["latest_tick_observed_at"]
            )
            chain_fresh = chain_age <= chain_max_age_seconds

        statuses = [
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
            (checkpoint["active_cycle_id"],),
        ).fetchone()
        if cycle is None:
            raise ValueError(
                "paired PAPER continuation active cycle is missing"
            )
    finally:
        conn.close()

    cycle_valid = (
        str(cycle[0]) == "PAPER_CHALLENGER"
        and str(cycle[1]) == "ACTIVE"
        and str(cycle[2]) == checkpoint["incumbent_model_id"]
        and str(cycle[3]) == checkpoint["challenger_model_id"]
        and str(cycle[4]) == "CHAMPION"
        and str(cycle[5]) == "PAPER_CHALLENGER"
    )
    if not cycle_valid:
        raise ValueError(
            "paired PAPER continuation cycle/model binding changed"
        )

    quote_map = {
        item["token_mint"]: item["quote_per_atomic"]
        for item in statuses
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
            "paired PAPER continuation readiness changed production database"
        )

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
        "source_checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "source_post_audit_sha256": checkpoint["source_post_audit_sha256"],
        "pair_entry_request_sha256": checkpoint["pair_entry_request_sha256"],
        "pair_entry_input_verification_sha256": checkpoint[
            "pair_entry_input_verification_sha256"
        ],
        "source_execution_receipt_sha256": checkpoint[
            "source_execution_receipt_sha256"
        ],
        "source_pair_entry_post_audit_sha256": checkpoint[
            "source_pair_entry_post_audit_sha256"
        ],
        "pair_lineage_sha256": checkpoint["pair_lineage_sha256"],
        "latest_previous_tick_post_audit_sha256": checkpoint[
            "latest_previous_tick_post_audit_sha256"
        ],
        "source_final_evaluation_sha256": checkpoint[
            "source_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": before["database"],
        "pio_wal_sha256": before["wal"],
        "pio_shm_sha256": before["shm"],
        "active_cycle_id": checkpoint["active_cycle_id"],
        "incumbent_model_id": checkpoint["incumbent_model_id"],
        "challenger_model_id": checkpoint["challenger_model_id"],
        "account_id": checkpoint["account_id"],
        "previous_pair_id": checkpoint["previous_pair_id"],
        "pair_id": checkpoint["pair_id"],
        "pool_address": checkpoint["pool_address"],
        "entry_observed_at": checkpoint["entry_observed_at"],
        "incumbent_position_id": checkpoint["incumbent_position_id"],
        "challenger_position_id": checkpoint["challenger_position_id"],
        "requested_position_ids": [
            checkpoint["incumbent_position_id"],
            checkpoint["challenger_position_id"],
        ],
        "previous_tick_observed_at": checkpoint["latest_tick_observed_at"],
        "evaluation_as_of": _format_time(evaluation),
        "chain_max_age_seconds": chain_max_age_seconds,
        "quote_max_age_seconds": quote_max_age_seconds,
        "latest_chain_observed_at": latest_chain,
        "latest_chain_age_seconds": chain_age,
        "chain_newer_than_previous_tick": chain_newer,
        "chain_fresh": chain_fresh,
        "required_quote_mints": list(required_mints),
        "quote_statuses": statuses,
        "fresh_quote_map": quote_map,
        "quotes_ready": quotes_ready,
        "pair_positions_open": True,
        "pair_counterfactual_bindings_present": True,
        "cycle_status": str(cycle[0]),
        "cycle_active_key": str(cycle[1]),
        "fresh_incumbent_model_status": str(cycle[4]),
        "fresh_challenger_model_status": str(cycle[5]),
        "cycle_model_binding_valid": True,
        "readiness_status": status,
        "continuation_tick_request_ready": status == STATUS_READY,
        "requires_separate_tick_authorization": True,
        "requires_fresh_recheck_before_execution": True,
        "explicit_position_scope_required": True,
        "derived_pool_safety_required_at_execution": True,
        "paper_supervisor_tick_authorized": False,
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
    validate_phase8_recursive_reentry_checkpoint_v14_continuation_supervision_readiness(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build canonical read-only readiness for a subsequent paired Phase 8 PAPER "
            "evidence tick after a clean prior tick checkpoint. Both pair legs must "
            "remain open, the active cycle/model binding must be unchanged, "
            "and a newer fresh chain snapshot plus fresh persisted quotes are "
            "required. This tool does not execute or authorize a tick."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--checkpoint", required=True)
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

    report = (
        build_phase8_recursive_reentry_checkpoint_v14_continuation_supervision_readiness(
            repository=args.repo,
            source_tree=args.source_tree,
            checkpoint_path=args.checkpoint,
            as_of=args.as_of,
            chain_max_age_seconds=args.chain_max_age_seconds,
            quote_max_age_seconds=args.quote_max_age_seconds,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
