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
ARTIFACT_TYPE = "PHASE8_PAIRED_ML_PAPER_SUPERVISION_READINESS_V1"

POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_entry_post_audit.py"
)
REVIEWED_SOURCE_BLOBS = {
    POST_AUDIT_TOOL: "69ce33e4492c8bbf22ee3480d5f2601a50562bda",
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
    "source_post_audit_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "pair_id",
    "pool_address",
    "entry_decision_observed_at",
    "incumbent_position_id",
    "challenger_position_id",
    "requested_position_ids",
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
    "readiness_status",
    "one_pair_evidence_tick_request_ready",
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


def _parse_time(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("paired PAPER supervision timestamps require timezone")
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


def _load_post_audit(source: Path) -> Any:
    path = source / POST_AUDIT_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed paired PAPER post-audit is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[POST_AUDIT_TOOL]:
        raise ValueError("reviewed paired PAPER post-audit blob mismatch")
    return _load_module(
        path,
        "phase8_paired_paper_supervision_readiness_audit",
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


def _database_state(module: Any, source: Path, production: Path) -> tuple[Path, dict[str, Any]]:
    executor = module._load_executor(source)
    readiness = executor._load_readiness_module(source)
    account_module, _, _, _ = readiness._load_reviewed(source)
    database = account_module._production_database(production)
    return database, account_module._database_state(database)


def _required_quote_mints(
    conn: sqlite3.Connection,
    position_ids: tuple[str, str],
) -> tuple[str, ...]:
    placeholders = ",".join("?" for _ in position_ids)
    rows = conn.execute(
        f"""
        SELECT token_x_mint, token_y_mint, reward_mint_0, reward_mint_1
        FROM paper_counterfactual_positions
        WHERE position_id IN ({placeholders})
        ORDER BY position_id
        """,
        position_ids,
    ).fetchall()
    if len(rows) != 2:
        raise ValueError("paired PAPER counterfactual bindings are incomplete")

    required: set[str] = set()
    for token_x, token_y, reward_0, reward_1 in rows:
        x = str(token_x)
        y = str(token_y)
        if not y:
            raise ValueError("paired PAPER token-Y mint is missing")
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


def validate_phase8_paired_ml_paper_supervision_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("paired PAPER supervision readiness must be an object")
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError("paired PAPER supervision readiness schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported paired PAPER supervision readiness format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected paired PAPER supervision readiness type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("paired PAPER supervision readiness lineage mismatch")

    for field in (
        "source_post_audit_sha256",
        "pio_database_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"paired PAPER supervision readiness {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"paired PAPER supervision readiness {field} is invalid"
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
        "entry_decision_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "evaluation_as_of",
        "readiness_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"paired PAPER supervision readiness {field} is invalid"
            )

    if report.get("requested_position_ids") != [
        report["incumbent_position_id"],
        report["challenger_position_id"],
    ]:
        raise ValueError("paired PAPER supervision position scope mismatch")

    for field in (
        "chain_max_age_seconds",
        "quote_max_age_seconds",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"paired PAPER supervision readiness {field} is invalid"
            )

    for field in (
        "required_quote_mints",
        "quote_statuses",
    ):
        if not isinstance(report.get(field), list):
            raise ValueError(
                f"paired PAPER supervision readiness {field} is invalid"
            )
    if not isinstance(report.get("fresh_quote_map"), dict):
        raise ValueError("paired PAPER supervision quote map is invalid")

    ready = report["readiness_status"] == STATUS_READY
    if report["one_pair_evidence_tick_request_ready"] is not ready:
        raise ValueError("paired PAPER supervision readiness status mismatch")

    for field in (
        "pair_positions_open",
        "pair_counterfactual_bindings_present",
        "requires_separate_tick_authorization",
        "requires_fresh_recheck_before_execution",
        "explicit_position_scope_required",
        "derived_pool_safety_required_at_execution",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"paired PAPER supervision readiness requires {field}=true"
            )

    for field in (
        "paper_supervisor_tick_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"paired PAPER supervision readiness requires {field}=false"
            )

    if ready:
        if report.get("chain_newer_than_entry") is not True:
            raise ValueError("READY supervision lacks a newer chain snapshot")
        if report.get("chain_fresh") is not True:
            raise ValueError("READY supervision chain state is not fresh")
        if report.get("quotes_ready") is not True:
            raise ValueError("READY supervision quotes are not fresh")
        if not isinstance(report.get("latest_chain_observed_at"), str):
            raise ValueError("READY supervision latest chain time is missing")
        if not isinstance(report.get("latest_chain_age_seconds"), int):
            raise ValueError("READY supervision chain age is missing")

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError("paired PAPER supervision readiness digest mismatch")


def build_phase8_paired_ml_paper_supervision_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    post_audit_path: str | Path,
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
        raise ValueError("paired PAPER supervision age limits cannot be negative")

    audit_module = _load_post_audit(source)
    audit = _load_json(post_audit_path, label="paired PAPER post-entry audit")
    audit_module.validate_phase8_paired_ml_paper_entry_post_audit(audit)
    if audit.get("post_pair_audit_ready") is not True:
        raise ValueError("paired PAPER post-entry audit is not ready")
    if audit.get("pair_seed_ready_for_supervision") is not True:
        raise ValueError("paired PAPER pair is not ready for supervision review")
    if audit.get("paper_supervisor_tick_authorized") is not False:
        raise ValueError("paired PAPER post-audit already authorizes supervision")

    database, before = _database_state(audit_module, source, production)
    if Path(str(audit["pio_database_path"])).resolve() != database:
        raise ValueError("paired PAPER supervision database binding mismatch")

    evaluation = (
        _parse_time(as_of)
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
            raise ValueError("paired PAPER positions are no longer open/bound")

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
            (audit["pool_address"], _format_time(evaluation)),
        ).fetchone()
        latest_chain = str(chain_row[0]) if chain_row is not None else None
        if latest_chain is None:
            chain_age = None
            chain_newer = False
            chain_fresh = False
        else:
            chain_time = _parse_time(latest_chain)
            chain_age = max(0, int((evaluation - chain_time).total_seconds()))
            chain_newer = chain_time > _parse_time(
                audit["decision_observed_at"]
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
    finally:
        conn.close()

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

    after_database, after = _database_state(audit_module, source, production)
    if after_database != database or after != before:
        raise ValueError("paired PAPER supervision readiness changed production")

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        "source_post_audit_sha256": audit["post_audit_sha256"],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": before["database"],
        "pio_wal_sha256": before["wal"],
        "pio_shm_sha256": before["shm"],
        "active_cycle_id": audit["active_cycle_id"],
        "incumbent_model_id": audit["incumbent_model_id"],
        "challenger_model_id": audit["challenger_model_id"],
        "account_id": audit["account_id"],
        "pair_id": audit["pair_id"],
        "pool_address": audit["pool_address"],
        "entry_decision_observed_at": audit["decision_observed_at"],
        "incumbent_position_id": audit["incumbent_position_id"],
        "challenger_position_id": audit["challenger_position_id"],
        "requested_position_ids": [
            audit["incumbent_position_id"],
            audit["challenger_position_id"],
        ],
        "evaluation_as_of": _format_time(evaluation),
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
        "readiness_status": status,
        "one_pair_evidence_tick_request_ready": status == STATUS_READY,
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
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_supervision_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only readiness for one pair-scoped PAPER evidence tick. "
            "Requires a chain snapshot newer than entry and fresh persisted "
            "quotes for the exact pair. It does not fetch quotes, refresh "
            "chain state, run PAPER management, or authorize live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-audit", required=True)
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

    report = build_phase8_paired_ml_paper_supervision_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        post_audit_path=args.post_audit,
        as_of=args.as_of,
        chain_max_age_seconds=args.chain_max_age_seconds,
        quote_max_age_seconds=args.quote_max_age_seconds,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
