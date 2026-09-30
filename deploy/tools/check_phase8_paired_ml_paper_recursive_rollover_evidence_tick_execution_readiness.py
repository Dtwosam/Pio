from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_EVIDENCE_TICK_EXECUTION_READINESS_V1"
)

SUPERVISION_READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_recursive_rollover_supervision_readiness.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_recursive_rollover_evidence_tick_request.py"
)
SIGNER_TOOL = Path(
    "deploy/tools/build_phase8_paired_ml_paper_recursive_rollover_evidence_tick_signed_authorization.py"
)
PAPER_LIVE_MODULE = Path(
    "python-learner/src/meteora_learner/paper_live.py"
)
PAPER_LATEST_MODULE = Path(
    "python-learner/src/meteora_learner/paper_latest.py"
)
REVIEWED_SOURCE_BLOBS = {
    SUPERVISION_READINESS_TOOL: "70081650a667b131550e6852ee47c8ba228c48b3",
    REQUEST_TOOL: "79360d4ec2d1aba8a7d28eb68def4325e3d72e1f",
    SIGNER_TOOL: "96c6484fdb37c535a8530d0a1aa37d22b6a5abc8",
    PAPER_LIVE_MODULE: "97fb2ad8eed46a6e6c9edee4eada3a0042a3a543",
    PAPER_LATEST_MODULE: "6fe32a581870e3bc926b1b316511c3bdc6c7ab0f",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_supervision_readiness_sha256",
    "fresh_supervision_readiness_sha256",
    "saved_request_sha256",
    "fresh_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "source_recursive_rollover_post_audit_sha256",
    "recursive_rollover_entry_request_sha256",
    "recursive_rollover_entry_input_verification_sha256",
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
    "requested_position_ids",
    "incumbent_position_id",
    "challenger_position_id",
    "evidence_cycle_id",
    "expected_run_id",
    "target_chain_observed_at",
    "evaluation_as_of",
    "chain_max_age_seconds",
    "quote_max_age_seconds",
    "required_quote_mints",
    "fresh_quote_map",
    "quote_statuses_sha256",
    "position_token_y_mints",
    "pair_cycle_items",
    "pair_cycle_items_sha256",
    "pool_safety_config",
    "position_management_config",
    "derived_pool_safety",
    "derived_pool_safety_sha256",
    "retry_failed",
    "emergency_exit",
    "estimated_exit_cost_quote",
    "rebalance_cost_quote",
    "existing_target_valuation_count",
    "existing_run_present",
    "fresh_supervision_readiness_matches_saved",
    "fresh_request_matches_saved",
    "fresh_authorization_matches_saved",
    "human_recursive_rollover_pair_evidence_tick_authorization_verified",
    "database_matches_request",
    "exact_chain_snapshot_verified",
    "exact_quote_map_verified",
    "explicit_position_scope_verified",
    "pair_positions_open_verified",
    "single_use_target_clear",
    "derived_pool_safety_verified",
    "idempotent_pair_run_identity_verified",
    "one_pair_evidence_tick_execution_readiness_ready",
    "readiness_only",
    "requires_immediate_pair_scoped_executor",
    "post_tick_audit_required",
    "paper_supervisor_tick_authorized",
    "paper_supervisor_tick_executed",
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
                f"paired PAPER tick execution readiness dependency missing: "
                f"{relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"paired PAPER tick execution readiness dependency mismatch: "
                f"{relative}"
            )
    return (
        _load_module(
            source / SUPERVISION_READINESS_TOOL,
            "phase8_recursive_rollover_pair_tick_exec_ready_supervision",
        ),
        _load_module(
            source / REQUEST_TOOL,
            "phase8_recursive_rollover_pair_tick_exec_ready_request",
        ),
        _load_module(
            source / SIGNER_TOOL,
            "phase8_recursive_rollover_pair_tick_exec_ready_signer",
        ),
        _load_module(
            source / PAPER_LIVE_MODULE,
            "meteora_learner.phase8_pair_tick_exec_ready_live",
        ),
        _load_module(
            source / PAPER_LATEST_MODULE,
            "meteora_learner.phase8_pair_tick_exec_ready_latest",
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
        raise ValueError("paired PAPER tick readiness value is not recordable")
    if not isinstance(result, dict):
        raise ValueError("paired PAPER tick readiness record is invalid")
    return result


def _hash_record(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(_record(value)))


def validate_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_readiness(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "paired PAPER evidence tick execution readiness must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError(
            "paired PAPER evidence tick execution readiness schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported paired PAPER evidence tick execution readiness format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected paired PAPER evidence tick execution readiness type"
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
            "paired PAPER evidence tick execution readiness lineage mismatch"
        )

    for field in (
        "saved_supervision_readiness_sha256",
        "fresh_supervision_readiness_sha256",
        "saved_request_sha256",
        "fresh_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "source_recursive_rollover_post_audit_sha256",
        "recursive_rollover_entry_request_sha256",
        "recursive_rollover_entry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "pio_database_sha256",
        "quote_statuses_sha256",
        "pair_cycle_items_sha256",
        "derived_pool_safety_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"paired PAPER tick execution readiness {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"paired PAPER tick execution readiness {field} is invalid"
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
        "evidence_cycle_id",
        "expected_run_id",
        "target_chain_observed_at",
        "evaluation_as_of",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"paired PAPER tick execution readiness {field} is invalid"
            )

    requested = report.get("requested_position_ids")
    if requested != [
        report["incumbent_position_id"],
        report["challenger_position_id"],
    ]:
        raise ValueError(
            "paired PAPER tick execution readiness position scope mismatch"
        )
    if report["incumbent_position_id"] == report["challenger_position_id"]:
        raise ValueError(
            "paired PAPER tick execution readiness positions must differ"
        )
    if report["incumbent_model_id"] == report["challenger_model_id"]:
        raise ValueError(
            "paired PAPER tick execution readiness models must differ"
        )

    if not isinstance(report.get("pair_cycle_items"), list):
        raise ValueError(
            "paired PAPER tick execution readiness cycle items are invalid"
        )
    items = report["pair_cycle_items"]
    if len(items) != 2:
        raise ValueError(
            "paired PAPER tick execution readiness requires exactly two items"
        )
    if [item.get("position_id") for item in items] != requested:
        raise ValueError(
            "paired PAPER tick execution readiness item scope mismatch"
        )
    if report["pair_cycle_items_sha256"] != _sha256_bytes(
        _canonical_bytes(items)
    ):
        raise ValueError(
            "paired PAPER tick execution readiness item digest mismatch"
        )

    if not isinstance(report.get("position_token_y_mints"), dict):
        raise ValueError(
            "paired PAPER tick execution readiness token-Y map is invalid"
        )
    if set(report["position_token_y_mints"]) != set(requested):
        raise ValueError(
            "paired PAPER tick execution readiness token-Y scope mismatch"
        )
    if not isinstance(report.get("fresh_quote_map"), dict):
        raise ValueError(
            "paired PAPER tick execution readiness quote map is invalid"
        )
    for item in items:
        position_id = item["position_id"]
        mint = report["position_token_y_mints"][position_id]
        quote = report["fresh_quote_map"].get(mint)
        if quote is None or float(quote) <= 0:
            raise ValueError(
                "paired PAPER tick execution readiness item quote is invalid"
            )
        if float(item["token_y_quote_per_atomic"]) != float(quote):
            raise ValueError(
                "paired PAPER tick execution readiness item quote drifted"
            )
        if int(item["quote_max_age_seconds"]) != int(
            report["quote_max_age_seconds"]
        ):
            raise ValueError(
                "paired PAPER tick execution readiness quote-age drifted"
            )
        if item.get("emergency_exit") is not False:
            raise ValueError(
                "paired PAPER tick execution readiness emergency exit drifted"
            )
        if float(item.get("estimated_exit_cost_quote", -1)) != 0.0:
            raise ValueError(
                "paired PAPER tick execution readiness exit cost drifted"
            )
        if item.get("rebalance_cost_quote") is not None:
            raise ValueError(
                "paired PAPER tick execution readiness rebalance cost drifted"
            )

    if not isinstance(report.get("derived_pool_safety"), dict):
        raise ValueError(
            "paired PAPER tick execution readiness pool safety is invalid"
        )
    if report["derived_pool_safety"].get("pool_address") != report[
        "pool_address"
    ]:
        raise ValueError(
            "paired PAPER tick execution readiness safety pool mismatch"
        )
    if report["derived_pool_safety_sha256"] != _sha256_bytes(
        _canonical_bytes(report["derived_pool_safety"])
    ):
        raise ValueError(
            "paired PAPER tick execution readiness safety digest mismatch"
        )

    if report.get("existing_target_valuation_count") != 0:
        raise ValueError(
            "paired PAPER tick execution readiness target already valued"
        )
    if report.get("existing_run_present") is not False:
        raise ValueError(
            "paired PAPER tick execution readiness run already exists"
        )

    for field in (
        "fresh_supervision_readiness_matches_saved",
        "fresh_request_matches_saved",
        "fresh_authorization_matches_saved",
        "human_recursive_rollover_pair_evidence_tick_authorization_verified",
        "database_matches_request",
        "exact_chain_snapshot_verified",
        "exact_quote_map_verified",
        "explicit_position_scope_verified",
        "pair_positions_open_verified",
        "single_use_target_clear",
        "derived_pool_safety_verified",
        "idempotent_pair_run_identity_verified",
        "one_pair_evidence_tick_execution_readiness_ready",
        "readiness_only",
        "requires_immediate_pair_scoped_executor",
        "post_tick_audit_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"paired PAPER tick execution readiness requires {field}=true"
            )

    for field in (
        "retry_failed",
        "emergency_exit",
        "paper_supervisor_tick_authorized",
        "paper_supervisor_tick_executed",
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
                f"paired PAPER tick execution readiness requires {field}=false"
            )
    if float(report.get("estimated_exit_cost_quote", -1)) != 0.0:
        raise ValueError(
            "paired PAPER tick execution readiness requires zero exit cost"
        )
    if report.get("rebalance_cost_quote") is not None:
        raise ValueError(
            "paired PAPER tick execution readiness requires null rebalance cost"
        )

    if report["saved_supervision_readiness_sha256"] != report[
        "fresh_supervision_readiness_sha256"
    ]:
        raise ValueError(
            "paired PAPER tick supervision readiness digest mismatch"
        )
    if report["saved_request_sha256"] != report["fresh_request_sha256"]:
        raise ValueError(
            "paired PAPER tick request digest mismatch"
        )
    if report["saved_signed_authorization_verification_sha256"] != report[
        "fresh_signed_authorization_verification_sha256"
    ]:
        raise ValueError(
            "paired PAPER tick authorization digest mismatch"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["readiness_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "paired PAPER evidence tick execution readiness digest mismatch"
        )


def build_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_readiness(
    *,
    repository: str | Path,
    source_tree: str | Path,
    recursive_rollover_post_audit_path: str | Path,
    saved_supervision_readiness_path: str | Path,
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
        supervision_module,
        request_module,
        signer_module,
        live_module,
        latest_module,
    ) = _load_reviewed(source)

    saved_supervision = _load_json(
        saved_supervision_readiness_path,
        label="saved recursive rollover paired PAPER supervision readiness",
    )
    request = _load_json(
        request_path,
        label="recursive rollover paired PAPER evidence tick request",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved recursive rollover paired PAPER evidence tick signed verification",
    )
    supervision_module.validate_phase8_paired_ml_paper_recursive_rollover_supervision_readiness(
        saved_supervision
    )
    request_module.validate_phase8_paired_ml_paper_recursive_rollover_evidence_tick_request(
        request
    )
    signer_module.validate_verification(saved_verification)

    if request["source_recursive_rollover_supervision_readiness_sha256"] != saved_supervision[
        "readiness_sha256"
    ]:
        raise ValueError(
            "paired PAPER tick request/supervision readiness mismatch"
        )
    if saved_verification["request_sha256"] != request["request_sha256"]:
        raise ValueError(
            "paired PAPER tick signed authorization/request mismatch"
        )

    fresh_supervision = (
        supervision_module.build_phase8_paired_ml_paper_recursive_rollover_supervision_readiness(
            repository=production,
            source_tree=source,
            recursive_rollover_post_audit_path=recursive_rollover_post_audit_path,
            as_of=saved_supervision["evaluation_as_of"],
            chain_max_age_seconds=int(
                saved_supervision["chain_max_age_seconds"]
            ),
            quote_max_age_seconds=int(
                saved_supervision["quote_max_age_seconds"]
            ),
        )
    )
    supervision_module.validate_phase8_paired_ml_paper_recursive_rollover_supervision_readiness(
        fresh_supervision
    )
    if fresh_supervision != saved_supervision:
        raise ValueError(
            "fresh recursive rollover paired PAPER supervision readiness differs from saved"
        )
    if fresh_supervision.get("readiness_status") != supervision_module.STATUS_READY:
        raise ValueError(
            "fresh recursive rollover paired PAPER supervision readiness is not READY"
        )

    with tempfile.TemporaryDirectory(
        prefix="pio-phase8-rollover-paired-paper-tick-readiness-"
    ) as tmp:
        fresh_supervision_path = Path(tmp) / "supervision-readiness.json"
        fresh_supervision_path.write_text(
            json.dumps(fresh_supervision, sort_keys=True),
            encoding="utf-8",
        )
        fresh_request = (
            request_module.build_phase8_paired_ml_paper_recursive_rollover_evidence_tick_request(
                source_tree=source,
                supervision_readiness_path=fresh_supervision_path,
            )
        )
    request_module.validate_phase8_paired_ml_paper_recursive_rollover_evidence_tick_request(
        fresh_request
    )
    if fresh_request != request:
        raise ValueError(
            "fresh recursive rollover paired PAPER evidence tick request differs from saved"
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
    signer_module.validate_verification(fresh_verification)
    if fresh_verification != saved_verification:
        raise ValueError(
            "fresh recursive rollover paired PAPER evidence tick authorization differs from saved"
        )

    _, base_supervision_module = supervision_module._load_reviewed(source)
    database = base_supervision_module._database_path(production)
    database_state = base_supervision_module._database_state(database)
    if Path(str(request["pio_database_path"])).resolve() != database:
        raise ValueError("paired PAPER tick request database binding mismatch")
    expected_state = {
        "database": request["pio_database_sha256"],
        "wal": request["pio_wal_sha256"],
        "shm": request["pio_shm_sha256"],
    }
    if database_state != expected_state:
        raise ValueError(
            "Pio database changed after recursive rollover paired PAPER evidence tick request"
        )

    if request["pair_id"] == request["previous_pair_id"]:
        raise ValueError("recursive rollover paired PAPER tick pair id was not advanced")
    requested = list(request["requested_position_ids"])
    if requested != [
        request["incumbent_position_id"],
        request["challenger_position_id"],
    ]:
        raise ValueError("paired PAPER tick requested position scope changed")

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        positions = conn.execute(
            """
            SELECT p.position_id, p.status, p.pool_address,
                   c.token_y_mint
            FROM paper_positions AS p
            JOIN paper_counterfactual_positions AS c
              ON c.position_id = p.position_id
            WHERE p.position_id IN (?, ?)
            ORDER BY CASE p.position_id WHEN ? THEN 0 ELSE 1 END
            """,
            (requested[0], requested[1], requested[0]),
        ).fetchall()
        if len(positions) != 2:
            raise ValueError("paired PAPER tick position scope is incomplete")
        for row in positions:
            if str(row[1]) != "OPEN":
                raise ValueError("paired PAPER tick position is no longer open")
            if str(row[2]) != request["pool_address"]:
                raise ValueError("paired PAPER tick position pool changed")

        latest_chain = conn.execute(
            """
            SELECT observed_at
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (request["pool_address"],),
        ).fetchone()
        if latest_chain is None:
            raise ValueError("paired PAPER tick target pool has no chain state")
        latest_chain_at = str(latest_chain[0])
        if latest_chain_at != request["target_chain_observed_at"]:
            raise ValueError(
                "paired PAPER tick target is no longer latest chain snapshot"
            )

        valuation_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM paper_chain_valuations
                WHERE position_id IN (?, ?)
                  AND observed_at = ?
                """,
                (
                    requested[0],
                    requested[1],
                    request["target_chain_observed_at"],
                ),
            ).fetchone()[0]
        )

        expected_run_id = latest_module._group_run_id(
            request["evidence_cycle_id"],
            request["target_chain_observed_at"],
        )
        existing_run = conn.execute(
            """
            SELECT 1
            FROM paper_runs
            WHERE run_id = ?
            LIMIT 1
            """,
            (expected_run_id,),
        ).fetchone()
    finally:
        conn.close()

    if valuation_count != 0:
        raise ValueError(
            "paired PAPER tick target already has valuation state"
        )
    if existing_run is not None:
        raise ValueError(
            "paired PAPER tick deterministic run already exists"
        )

    token_y_mints = {
        str(row[0]): str(row[3])
        for row in positions
    }
    fresh_quote_map = request["fresh_quote_map"]
    items: list[dict[str, Any]] = []
    for position_id in requested:
        mint = token_y_mints[position_id]
        quote_raw = fresh_quote_map.get(mint)
        if quote_raw is None or float(quote_raw) <= 0:
            raise ValueError(
                f"paired PAPER tick has no bound quote for token-Y mint {mint}"
            )
        item = latest_module.LatestPaperCycleItem(
            position_id=position_id,
            token_y_quote_per_atomic=float(quote_raw),
            quote_max_age_seconds=int(request["quote_max_age_seconds"]),
            emergency_exit=bool(request["emergency_exit"]),
            estimated_exit_cost_quote=float(
                request["estimated_exit_cost_quote"]
            ),
            rebalance_cost_quote=request["rebalance_cost_quote"],
        )
        items.append(asdict(item))

    safety_config = live_module.PoolSafetyConfig(
        **request["pool_safety_config"]
    )
    derived_safety = live_module.assess_live_pool_safety(
        live_module.Storage(database),
        pool_address=request["pool_address"],
        observed_at=request["target_chain_observed_at"],
        config=safety_config,
    )
    safety_record = _record(derived_safety)
    if safety_record["pool_address"] != request["pool_address"]:
        raise ValueError("paired PAPER tick derived safety pool changed")

    after_state = base_supervision_module._database_state(database)
    if after_state != database_state:
        raise ValueError(
            "recursive rollover paired PAPER tick readiness changed production database"
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
        "saved_supervision_readiness_sha256": saved_supervision[
            "readiness_sha256"
        ],
        "fresh_supervision_readiness_sha256": fresh_supervision[
            "readiness_sha256"
        ],
        "saved_request_sha256": request["request_sha256"],
        "fresh_request_sha256": fresh_request["request_sha256"],
        "saved_signed_authorization_verification_sha256": (
            saved_verification["verification_sha256"]
        ),
        "fresh_signed_authorization_verification_sha256": (
            fresh_verification["verification_sha256"]
        ),
        "source_recursive_rollover_post_audit_sha256": request[
            "source_recursive_rollover_post_audit_sha256"
        ],
        "recursive_rollover_entry_request_sha256": request[
            "recursive_rollover_entry_request_sha256"
        ],
        "recursive_rollover_entry_input_verification_sha256": request[
            "recursive_rollover_entry_input_verification_sha256"
        ],
        "source_final_evaluation_sha256": request[
            "source_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": database_state["database"],
        "pio_wal_sha256": database_state["wal"],
        "pio_shm_sha256": database_state["shm"],
        "active_cycle_id": request["active_cycle_id"],
        "incumbent_model_id": request["incumbent_model_id"],
        "challenger_model_id": request["challenger_model_id"],
        "account_id": request["account_id"],
        "previous_pair_id": request["previous_pair_id"],
        "pair_id": request["pair_id"],
        "pool_address": request["pool_address"],
        "entry_observed_at": request["entry_observed_at"],
        "requested_position_ids": requested,
        "incumbent_position_id": request["incumbent_position_id"],
        "challenger_position_id": request["challenger_position_id"],
        "evidence_cycle_id": request["evidence_cycle_id"],
        "expected_run_id": expected_run_id,
        "target_chain_observed_at": request["target_chain_observed_at"],
        "evaluation_as_of": request["evaluation_as_of"],
        "chain_max_age_seconds": request["chain_max_age_seconds"],
        "quote_max_age_seconds": request["quote_max_age_seconds"],
        "required_quote_mints": request["required_quote_mints"],
        "fresh_quote_map": request["fresh_quote_map"],
        "quote_statuses_sha256": request["quote_statuses_sha256"],
        "position_token_y_mints": token_y_mints,
        "pair_cycle_items": items,
        "pair_cycle_items_sha256": _sha256_bytes(
            _canonical_bytes(items)
        ),
        "pool_safety_config": request["pool_safety_config"],
        "position_management_config": request[
            "position_management_config"
        ],
        "derived_pool_safety": safety_record,
        "derived_pool_safety_sha256": _sha256_bytes(
            _canonical_bytes(safety_record)
        ),
        "retry_failed": request["retry_failed"],
        "emergency_exit": request["emergency_exit"],
        "estimated_exit_cost_quote": request[
            "estimated_exit_cost_quote"
        ],
        "rebalance_cost_quote": request["rebalance_cost_quote"],
        "existing_target_valuation_count": valuation_count,
        "existing_run_present": False,
        "fresh_supervision_readiness_matches_saved": True,
        "fresh_request_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_recursive_rollover_pair_evidence_tick_authorization_verified": True,
        "database_matches_request": True,
        "exact_chain_snapshot_verified": True,
        "exact_quote_map_verified": True,
        "explicit_position_scope_verified": True,
        "pair_positions_open_verified": True,
        "single_use_target_clear": True,
        "derived_pool_safety_verified": True,
        "idempotent_pair_run_identity_verified": True,
        "one_pair_evidence_tick_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_pair_scoped_executor": True,
        "post_tick_audit_required": True,
        "paper_supervisor_tick_authorized": False,
        "paper_supervisor_tick_executed": False,
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
    validate_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_readiness(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshly revalidate exactly one signed pair-scoped Phase 8 PAPER "
            "evidence tick. This tool binds the exact latest chain snapshot, "
            "persisted quote map, explicit two-position scope, deterministic "
            "run identity and derived pool safety. It does not execute PAPER "
            "management or any live transaction."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--recursive-rollover-post-audit", required=True)
    parser.add_argument("--saved-supervision-readiness", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = (
        build_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_readiness(
            repository=args.repo,
            source_tree=args.source_tree,
            recursive_rollover_post_audit_path=args.recursive_rollover_post_audit,
            saved_supervision_readiness_path=(
                args.saved_supervision_readiness
            ),
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
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
