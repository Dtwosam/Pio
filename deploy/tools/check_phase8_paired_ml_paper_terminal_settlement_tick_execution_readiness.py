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
    "PHASE8_PAIRED_ML_PAPER_TERMINAL_SETTLEMENT_TICK_EXECUTION_READINESS_V1"
)

SETTLEMENT_READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_terminal_settlement_readiness.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_terminal_settlement_tick_request.py"
)
SIGNER_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_terminal_settlement_tick_signed_authorization.py"
)
PAPER_LIVE_MODULE = Path(
    "python-learner/src/meteora_learner/paper_live.py"
)
PAPER_LATEST_MODULE = Path(
    "python-learner/src/meteora_learner/paper_latest.py"
)
REVIEWED_SOURCE_BLOBS = {
    SETTLEMENT_READINESS_TOOL: "137ecdb44239eb8b4fd7143cfcc164976120c186",
    REQUEST_TOOL: "c843e1e62e94053f0f36ef3ac1f153968cdb8db9",
    SIGNER_TOOL: "b987c80e269d24f2e9b57c00b829d15dc8a26ac1",
    PAPER_LIVE_MODULE: "97fb2ad8eed46a6e6c9edee4eada3a0042a3a543",
    PAPER_LATEST_MODULE: "6fe32a581870e3bc926b1b316511c3bdc6c7ab0f",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_settlement_readiness_sha256",
    "fresh_settlement_readiness_sha256",
    "saved_request_sha256",
    "fresh_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
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
    "expected_run_id",
    "target_chain_observed_at",
    "token_y_mint",
    "token_y_quote_per_atomic",
    "single_cycle_item",
    "single_cycle_item_sha256",
    "derived_pool_safety",
    "derived_pool_safety_sha256",
    "pool_safety_config",
    "position_management_config",
    "approver_principal",
    "approval_id",
    "authorization_expires_at",
    "fresh_settlement_readiness_matches_saved",
    "fresh_request_matches_saved",
    "fresh_authorization_matches_saved",
    "human_settlement_tick_authorization_verified",
    "exact_single_position_scope_verified",
    "exact_chain_snapshot_verified",
    "exact_quote_map_verified",
    "derived_pool_safety_bound",
    "deterministic_run_id_verified",
    "settlement_tick_execution_readiness_ready",
    "readiness_only",
    "requires_immediate_single_position_executor",
    "post_tick_audit_required",
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


def _load_reviewed(source: Path) -> tuple[Any, Any, Any, Any, Any]:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"paired PAPER settlement execution readiness dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"paired PAPER settlement execution readiness dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / SETTLEMENT_READINESS_TOOL,
            "phase8_terminal_settlement_exec_ready_readiness",
        ),
        _load_module(
            source / REQUEST_TOOL,
            "phase8_terminal_settlement_exec_ready_request",
        ),
        _load_module(
            source / SIGNER_TOOL,
            "phase8_terminal_settlement_exec_ready_signer",
        ),
        _load_module(
            source / PAPER_LIVE_MODULE,
            "meteora_learner.phase8_terminal_settlement_exec_ready_live",
        ),
        _load_module(
            source / PAPER_LATEST_MODULE,
            "meteora_learner.phase8_terminal_settlement_exec_ready_latest",
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
        raise ValueError("paired PAPER settlement readiness value is not recordable")
    if not isinstance(result, dict):
        raise ValueError("paired PAPER settlement readiness record is invalid")
    return result


def validate_phase8_paired_ml_paper_terminal_settlement_tick_execution_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "paired PAPER settlement tick execution readiness must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError(
            "paired PAPER settlement tick execution readiness schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported paired PAPER settlement tick execution readiness format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected paired PAPER settlement tick execution readiness type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "paired PAPER settlement tick execution readiness lineage mismatch"
        )

    for field in (
        "saved_settlement_readiness_sha256",
        "fresh_settlement_readiness_sha256",
        "saved_request_sha256",
        "fresh_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "source_terminal_evaluation_sha256",
        "source_terminal_post_audit_sha256",
        "pio_database_sha256",
        "single_cycle_item_sha256",
        "derived_pool_safety_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"paired PAPER settlement tick readiness {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"paired PAPER settlement tick readiness {field} is invalid"
            )

    if report.get("requested_position_ids") != [report["open_position_id"]]:
        raise ValueError(
            "paired PAPER settlement tick readiness position scope mismatch"
        )
    if not isinstance(report.get("single_cycle_item"), dict):
        raise ValueError(
            "paired PAPER settlement tick readiness cycle item is invalid"
        )
    if not isinstance(report.get("derived_pool_safety"), dict):
        raise ValueError(
            "paired PAPER settlement tick readiness safety record is invalid"
        )

    for field in (
        "fresh_settlement_readiness_matches_saved",
        "fresh_request_matches_saved",
        "fresh_authorization_matches_saved",
        "human_settlement_tick_authorization_verified",
        "exact_single_position_scope_verified",
        "exact_chain_snapshot_verified",
        "exact_quote_map_verified",
        "derived_pool_safety_bound",
        "deterministic_run_id_verified",
        "settlement_tick_execution_readiness_ready",
        "readiness_only",
        "requires_immediate_single_position_executor",
        "post_tick_audit_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"paired PAPER settlement tick readiness requires {field}=true"
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
                f"paired PAPER settlement tick readiness requires {field}=false"
            )

    if report["saved_settlement_readiness_sha256"] != report[
        "fresh_settlement_readiness_sha256"
    ]:
        raise ValueError(
            "paired PAPER settlement tick settlement-readiness digest mismatch"
        )
    if report["saved_request_sha256"] != report["fresh_request_sha256"]:
        raise ValueError("paired PAPER settlement tick request digest mismatch")
    if report[
        "saved_signed_authorization_verification_sha256"
    ] != report["fresh_signed_authorization_verification_sha256"]:
        raise ValueError(
            "paired PAPER settlement tick authorization digest mismatch"
        )
    if report["single_cycle_item"]["position_id"] != report[
        "open_position_id"
    ]:
        raise ValueError(
            "paired PAPER settlement tick cycle item position mismatch"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "paired PAPER settlement tick execution readiness digest mismatch"
        )


def build_phase8_paired_ml_paper_terminal_settlement_tick_execution_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    terminal_evaluation_path: str | Path,
    saved_settlement_readiness_path: str | Path,
    request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    (
        readiness_module,
        request_module,
        signer_module,
        live_module,
        latest_module,
    ) = _load_reviewed(source)

    saved_readiness = _load_json(
        saved_settlement_readiness_path,
        label="saved paired PAPER settlement readiness",
    )
    saved_request = _load_json(
        request_path,
        label="saved paired PAPER settlement tick request",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved paired PAPER settlement signed verification",
    )
    readiness_module.validate_phase8_paired_ml_paper_terminal_settlement_readiness(
        saved_readiness
    )
    request_module.validate_phase8_paired_ml_paper_terminal_settlement_tick_request(
        saved_request
    )
    signer_module.validate_verification(saved_verification)

    fresh_readiness = (
        readiness_module.build_phase8_paired_ml_paper_terminal_settlement_readiness(
            repository=production,
            source_tree=source,
            terminal_evaluation_path=terminal_evaluation_path,
            as_of=saved_readiness["evaluation_as_of"],
            chain_max_age_seconds=saved_readiness["chain_max_age_seconds"],
            quote_max_age_seconds=saved_readiness["quote_max_age_seconds"],
        )
    )
    if fresh_readiness != saved_readiness:
        raise ValueError(
            "fresh paired PAPER settlement readiness differs from saved"
        )

    fresh_request = (
        request_module.build_phase8_paired_ml_paper_terminal_settlement_tick_request(
            source_tree=source,
            settlement_readiness_path=saved_settlement_readiness_path,
        )
    )
    if fresh_request != saved_request:
        raise ValueError(
            "fresh paired PAPER settlement request differs from saved"
        )

    fresh_verification = signer_module.verify_authorization(
        source_tree=source,
        request_path=request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        now=now,
    )
    if fresh_verification != saved_verification:
        raise ValueError(
            "fresh paired PAPER settlement authorization differs from saved"
        )

    database = readiness_module._database_path(production)
    current_state = readiness_module._database_state(database)
    expected_state = {
        "database": saved_request["pio_database_sha256"],
        "wal": saved_request["pio_wal_sha256"],
        "shm": saved_request["pio_shm_sha256"],
    }
    if current_state != expected_state:
        raise ValueError(
            "Pio database changed after paired PAPER settlement request"
        )

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT token_y_mint
            FROM paper_counterfactual_positions
            WHERE position_id = ?
            LIMIT 1
            """,
            (saved_request["open_position_id"],),
        ).fetchone()
        if row is None or not str(row[0]):
            raise ValueError(
                "paired PAPER settlement token-Y binding is missing"
            )
        token_y_mint = str(row[0])
    finally:
        conn.close()

    quote_raw = saved_request["fresh_quote_map"].get(token_y_mint)
    if quote_raw is None or float(quote_raw) <= 0:
        raise ValueError(
            "paired PAPER settlement exact token-Y quote is unavailable"
        )
    token_y_quote = float(quote_raw)

    item = latest_module.LatestPaperCycleItem(
        position_id=saved_request["open_position_id"],
        token_y_quote_per_atomic=token_y_quote,
        quote_max_age_seconds=int(saved_request["quote_max_age_seconds"]),
        emergency_exit=False,
        estimated_exit_cost_quote=0.0,
        rebalance_cost_quote=None,
    )
    item_record = asdict(item)

    safety_config = live_module.PoolSafetyConfig(
        **saved_request["pool_safety_config"]
    )
    safety = live_module.assess_live_pool_safety(
        live_module.Storage(database),
        pool_address=saved_request["pool_address"],
        observed_at=saved_request["target_chain_observed_at"],
        config=safety_config,
    )
    safety_record = _record(safety)
    expected_run_id = latest_module._group_run_id(
        saved_request["settlement_cycle_id"],
        saved_request["target_chain_observed_at"],
    )

    after_state = readiness_module._database_state(database)
    if after_state != current_state:
        raise ValueError(
            "paired PAPER settlement execution readiness changed production"
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
        "saved_settlement_readiness_sha256": saved_readiness[
            "readiness_sha256"
        ],
        "fresh_settlement_readiness_sha256": fresh_readiness[
            "readiness_sha256"
        ],
        "saved_request_sha256": saved_request["request_sha256"],
        "fresh_request_sha256": fresh_request["request_sha256"],
        "saved_signed_authorization_verification_sha256": saved_verification[
            "verification_sha256"
        ],
        "fresh_signed_authorization_verification_sha256": fresh_verification[
            "verification_sha256"
        ],
        "source_terminal_evaluation_sha256": saved_request[
            "source_terminal_evaluation_sha256"
        ],
        "source_terminal_post_audit_sha256": saved_request[
            "source_terminal_post_audit_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": current_state["database"],
        "pio_wal_sha256": current_state["wal"],
        "pio_shm_sha256": current_state["shm"],
        "active_cycle_id": saved_request["active_cycle_id"],
        "incumbent_model_id": saved_request["incumbent_model_id"],
        "challenger_model_id": saved_request["challenger_model_id"],
        "account_id": saved_request["account_id"],
        "pair_id": saved_request["pair_id"],
        "pool_address": saved_request["pool_address"],
        "terminal_tick_observed_at": saved_request[
            "terminal_tick_observed_at"
        ],
        "incumbent_position_id": saved_request["incumbent_position_id"],
        "challenger_position_id": saved_request["challenger_position_id"],
        "open_position_id": saved_request["open_position_id"],
        "closed_position_id": saved_request["closed_position_id"],
        "open_position_policy_source": saved_request[
            "open_position_policy_source"
        ],
        "open_position_model_id": saved_request["open_position_model_id"],
        "requested_position_ids": saved_request["requested_position_ids"],
        "settlement_cycle_id": saved_request["settlement_cycle_id"],
        "expected_run_id": expected_run_id,
        "target_chain_observed_at": saved_request[
            "target_chain_observed_at"
        ],
        "token_y_mint": token_y_mint,
        "token_y_quote_per_atomic": token_y_quote,
        "single_cycle_item": item_record,
        "single_cycle_item_sha256": _sha256_bytes(
            _canonical_bytes(item_record)
        ),
        "derived_pool_safety": safety_record,
        "derived_pool_safety_sha256": _sha256_bytes(
            _canonical_bytes(safety_record)
        ),
        "pool_safety_config": saved_request["pool_safety_config"],
        "position_management_config": saved_request[
            "position_management_config"
        ],
        "approver_principal": fresh_verification["approver_principal"],
        "approval_id": fresh_verification["approval_id"],
        "authorization_expires_at": fresh_verification["expires_at"],
        "fresh_settlement_readiness_matches_saved": True,
        "fresh_request_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_settlement_tick_authorization_verified": True,
        "exact_single_position_scope_verified": True,
        "exact_chain_snapshot_verified": True,
        "exact_quote_map_verified": True,
        "derived_pool_safety_bound": True,
        "deterministic_run_id_verified": True,
        "settlement_tick_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_single_position_executor": True,
        "post_tick_audit_required": True,
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
    validate_phase8_paired_ml_paper_terminal_settlement_tick_execution_readiness(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshly revalidate one signed single-position terminal PAPER "
            "settlement tick. This binds the exact chain snapshot, quote, "
            "derived pool safety and deterministic run id without mutation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--terminal-evaluation", required=True)
    parser.add_argument("--saved-settlement-readiness", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_terminal_settlement_tick_execution_readiness(
        repository=args.repo,
        source_tree=args.source_tree,
        terminal_evaluation_path=args.terminal_evaluation,
        saved_settlement_readiness_path=args.saved_settlement_readiness,
        request_path=args.request,
        saved_signed_authorization_verification_path=(
            args.saved_signed_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=(
            args.expected_allowed_signers_sha256
        ),
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
