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
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_SUPERVISION_READINESS_V1"
)

REENTRY_POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_recursive_rollover_reentry_entry_post_audit.py"
)
BASE_SUPERVISION_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_supervision_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    REENTRY_POST_AUDIT_TOOL: "89f436b34bd77f0a2e235a3c07eaabd51de383a5",
    BASE_SUPERVISION_TOOL: "e7e911e26ebf8c244cfc8d63d1037c6c1162df45",
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
    "source_recursive_rollover_reentry_post_audit_sha256",
    "recursive_rollover_reentry_entry_request_sha256",
    "recursive_rollover_reentry_input_verification_sha256",
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
    "incumbent_position_id",
    "challenger_position_id",
    "requested_position_ids",
    "entry_observed_at",
    "evaluation_as_of",
    "chain_max_age_seconds",
    "quote_max_age_seconds",
    "latest_chain_observed_at",
    "latest_chain_age_seconds",
    "chain_newer_than_entry",
    "chain_fresh",
    "required_quote_mints",
    "quote_statuses",
    "fresh_quote_map",
    "quotes_ready",
    "pair_positions_open",
    "pair_counterfactual_bindings_present",
    "paper_ledger_audit_passing",
    "cycle_status",
    "cycle_active_key",
    "fresh_incumbent_model_status",
    "fresh_challenger_model_status",
    "cycle_model_binding_valid",
    "readiness_status",
    "recursive_rollover_reentry_evidence_tick_request_ready",
    "requires_separate_tick_authorization",
    "requires_fresh_recheck_before_execution",
    "explicit_position_scope_required",
    "derived_pool_safety_required_at_execution",
    "read_only",
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
                f"recursive rollover re-entry paired PAPER supervision dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"recursive rollover re-entry paired PAPER supervision dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / REENTRY_POST_AUDIT_TOOL,
            "phase8_recursive_rollover_reentry_pair_supervision_post_audit",
        ),
        _load_module(
            source / BASE_SUPERVISION_TOOL,
            "phase8_repeat_pair_supervision_base",
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


def validate_phase8_paired_ml_paper_recursive_rollover_reentry_supervision_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision readiness must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision readiness schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported recursive rollover re-entry paired PAPER supervision readiness format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected recursive rollover re-entry paired PAPER supervision readiness type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision readiness lineage mismatch"
        )

    for field in (
        "source_recursive_rollover_reentry_post_audit_sha256",
        "recursive_rollover_reentry_entry_request_sha256",
        "recursive_rollover_reentry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "pio_database_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"recursive rollover re-entry paired PAPER supervision readiness {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"recursive rollover re-entry paired PAPER supervision readiness {field} is invalid"
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
        "incumbent_position_id",
        "challenger_position_id",
        "entry_observed_at",
        "evaluation_as_of",
        "cycle_status",
        "cycle_active_key",
        "fresh_incumbent_model_status",
        "fresh_challenger_model_status",
        "readiness_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"recursive rollover re-entry paired PAPER supervision readiness {field} is invalid"
            )

    if report["pair_id"] == report["previous_pair_id"]:
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision pair id was not advanced"
        )
    if report.get("requested_position_ids") != [
        report["incumbent_position_id"],
        report["challenger_position_id"],
    ]:
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision position scope mismatch"
        )
    if not isinstance(report.get("required_quote_mints"), list):
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision required quote mints are invalid"
        )
    if not isinstance(report.get("quote_statuses"), list):
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision quote statuses are invalid"
        )
    if not isinstance(report.get("fresh_quote_map"), dict):
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision quote map is invalid"
        )

    for field in (
        "pair_positions_open",
        "pair_counterfactual_bindings_present",
        "paper_ledger_audit_passing",
        "cycle_model_binding_valid",
        "requires_separate_tick_authorization",
        "requires_fresh_recheck_before_execution",
        "explicit_position_scope_required",
        "derived_pool_safety_required_at_execution",
        "read_only",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"recursive rollover re-entry paired PAPER supervision readiness requires {field}=true"
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
                f"recursive rollover re-entry paired PAPER supervision readiness requires {field}=false"
            )

    ready = report["readiness_status"] == STATUS_READY
    if report["recursive_rollover_reentry_evidence_tick_request_ready"] is not ready:
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision request-ready mismatch"
        )
    if ready:
        if report["chain_newer_than_entry"] is not True:
            raise ValueError(
                "recursive rollover re-entry paired PAPER supervision READY requires newer chain"
            )
        if report["chain_fresh"] is not True:
            raise ValueError(
                "recursive rollover re-entry paired PAPER supervision READY requires fresh chain"
            )
        if report["quotes_ready"] is not True:
            raise ValueError(
                "recursive rollover re-entry paired PAPER supervision READY requires fresh quotes"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision readiness digest mismatch"
        )


def build_phase8_paired_ml_paper_recursive_rollover_reentry_supervision_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    recursive_rollover_reentry_post_audit_path: str | Path,
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
            "recursive rollover re-entry paired PAPER supervision age limits cannot be negative"
        )

    audit_module, base_module = _load_reviewed(source)
    audit = _load_json(
        recursive_rollover_reentry_post_audit_path,
        label="recursive rollover re-entry paired PAPER entry post-audit",
    )
    audit_module.validate_phase8_paired_ml_paper_recursive_rollover_reentry_entry_post_audit(
        audit
    )
    if audit.get("post_pair_audit_ready") is not True:
        raise ValueError(
            "recursive rollover re-entry paired PAPER post-audit is not ready"
        )
    if audit.get("recursive_rollover_reentry_pair_seed_ready_for_supervision") is not True:
        raise ValueError(
            "recursive rollover re-entry paired PAPER seed is not ready for supervision"
        )
    if audit.get("paper_ledger_audit_passing") is not True:
        raise ValueError(
            "recursive rollover re-entry paired PAPER ledger is not passing"
        )

    database = base_module._database_path(production)
    if Path(str(audit["pio_database_path"])).resolve() != database:
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision database binding mismatch"
        )
    before = base_module._database_state(database)
    expected = {
        "database": audit["audit_database_sha256_after"],
        "wal": audit["audit_wal_sha256_after"],
        "shm": audit["audit_shm_sha256_after"],
    }
    if before != expected:
        raise ValueError(
            "Pio database changed after recursive rollover re-entry paired PAPER post-audit"
        )

    evaluation = (
        base_module._parse_time(as_of)
        if as_of is not None
        else datetime.now(timezone.utc)
    )
    position_ids = (
        audit["incumbent_position_id"],
        audit["challenger_position_id"],
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
            and all(str(row[2]) == audit["pool_address"] for row in positions)
        )
        if not positions_open:
            raise ValueError(
                "recursive rollover re-entry paired PAPER positions are no longer open/bound"
            )

        required_mints = base_module._required_quote_mints(
            conn,
            position_ids,
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
            (
                audit["pool_address"],
                base_module._format_time(evaluation),
            ),
        ).fetchone()
        latest_chain = (
            str(chain_row[0]) if chain_row is not None else None
        )
        if latest_chain is None:
            chain_age = None
            chain_newer = False
            chain_fresh = False
        else:
            chain_time = base_module._parse_time(latest_chain)
            chain_age = max(
                0,
                int((evaluation - chain_time).total_seconds()),
            )
            chain_newer = chain_time > base_module._parse_time(
                audit["decision_observed_at"]
            )
            chain_fresh = chain_age <= chain_max_age_seconds

        statuses = [
            base_module._quote_status(
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
            (audit["active_cycle_id"],),
        ).fetchone()
        if cycle is None:
            raise ValueError(
                "recursive rollover re-entry paired PAPER active cycle is missing"
            )
    finally:
        conn.close()

    cycle_valid = (
        str(cycle[0]) == "PAPER_CHALLENGER"
        and str(cycle[1]) == "ACTIVE"
        and str(cycle[2]) == audit["incumbent_model_id"]
        and str(cycle[3]) == audit["challenger_model_id"]
        and str(cycle[4]) == "CHAMPION"
        and str(cycle[5]) == "PAPER_CHALLENGER"
    )
    if not cycle_valid:
        raise ValueError(
            "recursive rollover re-entry paired PAPER cycle/model binding changed"
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

    after = base_module._database_state(database)
    if after != before:
        raise ValueError(
            "recursive rollover re-entry paired PAPER supervision readiness changed production database"
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
        "source_recursive_rollover_reentry_post_audit_sha256": audit["post_audit_sha256"],
        "recursive_rollover_reentry_entry_request_sha256": audit[
            "recursive_rollover_reentry_entry_request_sha256"
        ],
        "recursive_rollover_reentry_input_verification_sha256": audit[
            "recursive_rollover_reentry_input_verification_sha256"
        ],
        "source_final_evaluation_sha256": audit[
            "source_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": before["database"],
        "pio_wal_sha256": before["wal"],
        "pio_shm_sha256": before["shm"],
        "active_cycle_id": audit["active_cycle_id"],
        "incumbent_model_id": audit["incumbent_model_id"],
        "challenger_model_id": audit["challenger_model_id"],
        "account_id": audit["account_id"],
        "previous_pair_id": audit["previous_pair_id"],
        "pair_id": audit["pair_id"],
        "pool_address": audit["pool_address"],
        "incumbent_position_id": audit["incumbent_position_id"],
        "challenger_position_id": audit["challenger_position_id"],
        "requested_position_ids": [
            audit["incumbent_position_id"],
            audit["challenger_position_id"],
        ],
        "entry_observed_at": audit["decision_observed_at"],
        "evaluation_as_of": base_module._format_time(evaluation),
        "chain_max_age_seconds": chain_max_age_seconds,
        "quote_max_age_seconds": quote_max_age_seconds,
        "latest_chain_observed_at": latest_chain,
        "latest_chain_age_seconds": chain_age,
        "chain_newer_than_entry": chain_newer,
        "chain_fresh": chain_fresh,
        "required_quote_mints": list(required_mints),
        "quote_statuses": statuses,
        "fresh_quote_map": quote_map,
        "quotes_ready": quotes_ready,
        "pair_positions_open": True,
        "pair_counterfactual_bindings_present": True,
        "paper_ledger_audit_passing": True,
        "cycle_status": str(cycle[0]),
        "cycle_active_key": str(cycle[1]),
        "fresh_incumbent_model_status": str(cycle[4]),
        "fresh_challenger_model_status": str(cycle[5]),
        "cycle_model_binding_valid": True,
        "readiness_status": status,
        "recursive_rollover_reentry_evidence_tick_request_ready": status == STATUS_READY,
        "requires_separate_tick_authorization": True,
        "requires_fresh_recheck_before_execution": True,
        "explicit_position_scope_required": True,
        "derived_pool_safety_required_at_execution": True,
        "read_only": True,
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
        "readiness_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_paired_ml_paper_recursive_rollover_reentry_supervision_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build read-only supervision readiness for a newly seeded recursive rollover "
            "paired Phase 8 PAPER comparison. Both legs must remain open, the "
            "ledger and cycle/model bindings must remain valid, and a newer "
            "fresh chain snapshot plus fresh required quotes are needed before "
            "a separately authorized pair-scoped evidence tick can be requested."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--recursive-rollover-reentry-post-audit", required=True)
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

    report = build_phase8_paired_ml_paper_recursive_rollover_reentry_supervision_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        recursive_rollover_reentry_post_audit_path=args.recursive_rollover_reentry_post_audit,
        as_of=args.as_of,
        chain_max_age_seconds=args.chain_max_age_seconds,
        quote_max_age_seconds=args.quote_max_age_seconds,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
