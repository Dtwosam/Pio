from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE8_PAIRED_ML_PAPER_TERMINAL_SETTLEMENT_TICK_REQUEST_V1"

READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_terminal_settlement_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "137ecdb44239eb8b4fd7143cfcc164976120c186",
}

AUTHORIZATION_SCOPE = "RUN_ONE_PHASE8_PAIRED_ML_PAPER_SETTLEMENT_TICK_ONLY"
DECISION = "AUTHORIZE_ONE_PHASE8_PAIRED_ML_PAPER_SETTLEMENT_TICK"

SAFETY_CONFIG = {
    "min_tvl_usd": 50_000.0,
    "min_volume_24h_usd": 10_000.0,
    "min_pool_age_hours": 24.0,
    "min_chain_observations": 12,
    "max_dynamic_fee_pct": 5.0,
    "max_pool_snapshot_age_seconds": 900,
    "require_standard_spl": True,
    "require_not_blacklisted": True,
}
MANAGEMENT_CONFIG = {
    "stop_loss_bps": 500,
    "take_profit_bps": None,
    "max_rebalances": 3,
    "max_holding_observations": None,
    "proactive_rebalance_buffer_bins": 0,
}

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "decision",
    "source_settlement_readiness_sha256",
    "source_terminal_evaluation_sha256",
    "source_terminal_post_audit_sha256",
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
    "terminal_tick_observed_at",
    "incumbent_position_id",
    "challenger_position_id",
    "open_position_id",
    "closed_position_id",
    "open_position_policy_source",
    "open_position_model_id",
    "requested_position_ids",
    "settlement_cycle_id",
    "target_chain_observed_at",
    "evaluation_as_of",
    "chain_max_age_seconds",
    "quote_max_age_seconds",
    "required_quote_mints",
    "fresh_quote_map",
    "quote_statuses_sha256",
    "pool_safety_config",
    "position_management_config",
    "retry_failed",
    "emergency_exit",
    "estimated_exit_cost_quote",
    "rebalance_cost_quote",
    "request_ready",
    "explicit_human_authorization_required",
    "fresh_settlement_readiness_recheck_required",
    "exact_chain_snapshot_required",
    "exact_quote_map_required",
    "explicit_single_position_scope_required",
    "derived_pool_safety_required",
    "idempotent_single_position_run_required",
    "post_tick_audit_required",
    "paper_settlement_tick_authorization_present",
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
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
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


def _load_readiness(source: Path) -> Any:
    path = source / READINESS_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed paired PAPER settlement readiness is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[READINESS_TOOL]:
        raise ValueError("reviewed paired PAPER settlement readiness blob mismatch")
    return _load_module(
        path,
        "phase8_paired_paper_terminal_settlement_request_readiness",
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


def validate_phase8_paired_ml_paper_terminal_settlement_tick_request(
    request: dict[str, Any],
) -> None:
    if not isinstance(request, dict):
        raise ValueError("paired PAPER settlement tick request must be an object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("paired PAPER settlement tick request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported paired PAPER settlement request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected paired PAPER settlement request type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("paired PAPER settlement request lineage mismatch")
    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("paired PAPER settlement authorization scope mismatch")
    if request.get("decision") != DECISION:
        raise ValueError("paired PAPER settlement decision mismatch")

    for field in (
        "source_settlement_readiness_sha256",
        "source_terminal_evaluation_sha256",
        "source_terminal_post_audit_sha256",
        "pio_database_sha256",
        "quote_statuses_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(f"paired PAPER settlement request {field} is invalid")
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = request.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"paired PAPER settlement request {field} is invalid")

    if request.get("requested_position_ids") != [request["open_position_id"]]:
        raise ValueError("paired PAPER settlement request position scope mismatch")
    if request["open_position_id"] == request["closed_position_id"]:
        raise ValueError("paired PAPER settlement request position ids collapsed")
    if request.get("pool_safety_config") != SAFETY_CONFIG:
        raise ValueError("paired PAPER settlement safety config changed")
    if request.get("position_management_config") != MANAGEMENT_CONFIG:
        raise ValueError("paired PAPER settlement management config changed")
    if request.get("retry_failed") is not False:
        raise ValueError("paired PAPER settlement request requires retry_failed=false")
    if request.get("emergency_exit") is not False:
        raise ValueError("paired PAPER settlement request requires emergency_exit=false")
    if request.get("estimated_exit_cost_quote") != 0.0:
        raise ValueError("paired PAPER settlement exit-cost input changed")
    if request.get("rebalance_cost_quote") is not None:
        raise ValueError("paired PAPER settlement rebalance cost must remain null")

    for field in (
        "request_ready",
        "explicit_human_authorization_required",
        "fresh_settlement_readiness_recheck_required",
        "exact_chain_snapshot_required",
        "exact_quote_map_required",
        "explicit_single_position_scope_required",
        "derived_pool_safety_required",
        "idempotent_single_position_run_required",
        "post_tick_audit_required",
    ):
        if request.get(field) is not True:
            raise ValueError(f"paired PAPER settlement request requires {field}=true")

    for field in (
        "paper_settlement_tick_authorization_present",
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
        if request.get(field) is not False:
            raise ValueError(f"paired PAPER settlement request requires {field}=false")

    identity = {field: request[field] for field in REQUEST_FIELDS}
    if request["request_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError("paired PAPER settlement request digest mismatch")


def build_phase8_paired_ml_paper_terminal_settlement_tick_request(
    *,
    source_tree: str | Path,
    settlement_readiness_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    readiness_module = _load_readiness(source)
    readiness = _load_json(
        settlement_readiness_path,
        label="paired PAPER terminal settlement readiness",
    )
    readiness_module.validate_phase8_paired_ml_paper_terminal_settlement_readiness(
        readiness
    )
    if readiness.get("readiness_status") != readiness_module.STATUS_READY:
        raise ValueError("paired PAPER terminal settlement readiness is not READY")
    if readiness.get("settlement_tick_request_ready") is not True:
        raise ValueError("paired PAPER terminal settlement cannot request a tick")
    for field in (
        "chain_newer_than_terminal_tick",
        "chain_fresh",
        "quotes_ready",
        "open_position_still_open",
        "closed_position_still_closed",
        "open_counterfactual_binding_present",
        "cycle_model_binding_valid",
    ):
        if readiness.get(field) is not True:
            raise ValueError(
                f"paired PAPER settlement readiness requires {field}=true"
            )

    settlement_cycle_id = (
        "phase8-settle:"
        + readiness["pair_id"]
        + ":"
        + hashlib.sha256(
            readiness["latest_chain_observed_at"].encode("utf-8")
        ).hexdigest()[:12]
    )
    quote_statuses_sha = _sha256_bytes(
        _canonical_bytes(readiness["quote_statuses"])
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
        "authorization_scope": AUTHORIZATION_SCOPE,
        "decision": DECISION,
        "source_settlement_readiness_sha256": readiness["readiness_sha256"],
        "source_terminal_evaluation_sha256": readiness[
            "source_terminal_evaluation_sha256"
        ],
        "source_terminal_post_audit_sha256": readiness[
            "source_terminal_post_audit_sha256"
        ],
        "production_repository": readiness["production_repository"],
        "pio_database_path": readiness["pio_database_path"],
        "pio_database_sha256": readiness["pio_database_sha256"],
        "pio_wal_sha256": readiness["pio_wal_sha256"],
        "pio_shm_sha256": readiness["pio_shm_sha256"],
        "active_cycle_id": readiness["active_cycle_id"],
        "incumbent_model_id": readiness["incumbent_model_id"],
        "challenger_model_id": readiness["challenger_model_id"],
        "account_id": readiness["account_id"],
        "pair_id": readiness["pair_id"],
        "pool_address": readiness["pool_address"],
        "terminal_tick_observed_at": readiness["terminal_tick_observed_at"],
        "incumbent_position_id": readiness["incumbent_position_id"],
        "challenger_position_id": readiness["challenger_position_id"],
        "open_position_id": readiness["open_position_id"],
        "closed_position_id": readiness["closed_position_id"],
        "open_position_policy_source": readiness[
            "open_position_policy_source"
        ],
        "open_position_model_id": readiness["open_position_model_id"],
        "requested_position_ids": [readiness["open_position_id"]],
        "settlement_cycle_id": settlement_cycle_id,
        "target_chain_observed_at": readiness["latest_chain_observed_at"],
        "evaluation_as_of": readiness["evaluation_as_of"],
        "chain_max_age_seconds": readiness["chain_max_age_seconds"],
        "quote_max_age_seconds": readiness["quote_max_age_seconds"],
        "required_quote_mints": readiness["required_quote_mints"],
        "fresh_quote_map": readiness["fresh_quote_map"],
        "quote_statuses_sha256": quote_statuses_sha,
        "pool_safety_config": SAFETY_CONFIG,
        "position_management_config": MANAGEMENT_CONFIG,
        "retry_failed": False,
        "emergency_exit": False,
        "estimated_exit_cost_quote": 0.0,
        "rebalance_cost_quote": None,
        "request_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_settlement_readiness_recheck_required": True,
        "exact_chain_snapshot_required": True,
        "exact_quote_map_required": True,
        "explicit_single_position_scope_required": True,
        "derived_pool_safety_required": True,
        "idempotent_single_position_run_required": True,
        "post_tick_audit_required": True,
        "paper_settlement_tick_authorization_present": False,
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
    request = {
        **identity,
        "request_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_terminal_settlement_tick_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request for exactly one single-position "
            "terminal PAPER settlement tick. It binds the remaining OPEN leg "
            "to fresh persisted chain/quote state and never forces an exit."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--settlement-readiness", required=True)
    args = parser.parse_args()

    request = build_phase8_paired_ml_paper_terminal_settlement_tick_request(
        source_tree=args.source_tree,
        settlement_readiness_path=args.settlement_readiness,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
