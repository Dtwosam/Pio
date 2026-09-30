from __future__ import annotations

import argparse
from dataclasses import asdict
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_EVIDENCE_TICK_ONE_SHOT_EXECUTION_RECEIPT_V1"
)

READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "9888343d312b964c35776d63a5b1bcc26197f3d8",
}

LOCK_PATH = Path(
    "/var/tmp/pio-phase8-recursive-rollover-paired-paper-evidence-tick-one-shot.lock"
)

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_readiness_sha256",
    "fresh_readiness_sha256",
    "saved_request_sha256",
    "source_recursive_rollover_post_audit_sha256",
    "recursive_rollover_entry_request_sha256",
    "recursive_rollover_entry_input_verification_sha256",
    "source_final_evaluation_sha256",
    "signed_authorization_verification_sha256",
    "approval_payload_sha256",
    "approval_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "authorization_expires_at",
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
    "requested_position_ids",
    "incumbent_position_id",
    "challenger_position_id",
    "evidence_cycle_id",
    "expected_run_id",
    "target_chain_observed_at",
    "pair_cycle_items_sha256",
    "derived_pool_safety_sha256",
    "pool_safety_config",
    "position_management_config",
    "tick_result",
    "tick_result_sha256",
    "tick_status",
    "positions_requested",
    "groups",
    "items_total",
    "items_applied",
    "items_skipped",
    "items_failed",
    "run_reused_existing",
    "run_item_statuses",
    "fresh_readiness_matches_saved",
    "human_recursive_rollover_pair_evidence_tick_authorization_verified",
    "exact_position_scope_verified",
    "exact_chain_snapshot_verified",
    "exact_quote_map_verified",
    "derived_pool_safety_bound",
    "deterministic_run_id_verified",
    "one_tick_only",
    "paper_supervisor_tick_authorized",
    "paper_supervisor_tick_executed",
    "pair_tick_complete",
    "pair_tick_partial_failure",
    "requires_post_tick_audit",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_performed",
    "new_live_capital_used",
    "continuous_promotion_authorized",
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
        raise ValueError(
            "reviewed paired PAPER evidence tick execution readiness is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[READINESS_TOOL]:
        raise ValueError(
            "reviewed paired PAPER evidence tick execution readiness blob mismatch"
        )
    return _load_module(
        path,
        "phase8_recursive_rollover_paired_paper_tick_one_shot_readiness",
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


def validate_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_receipt(
    receipt: dict[str, Any],
) -> None:
    if not isinstance(receipt, dict):
        raise ValueError(
            "paired PAPER evidence tick execution receipt must be an object"
        )
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError(
            "paired PAPER evidence tick execution receipt schema mismatch"
        )
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported paired PAPER evidence tick execution receipt format"
        )
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected paired PAPER evidence tick execution receipt type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "paired PAPER evidence tick execution receipt lineage mismatch"
        )

    for field in (
        "saved_readiness_sha256",
        "fresh_readiness_sha256",
        "saved_request_sha256",
        "source_recursive_rollover_post_audit_sha256",
        "recursive_rollover_entry_request_sha256",
        "recursive_rollover_entry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "signed_authorization_verification_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "pair_cycle_items_sha256",
        "derived_pool_safety_sha256",
        "tick_result_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(receipt.get(field), 64):
            raise ValueError(
                f"paired PAPER tick execution receipt {field} is invalid"
            )
    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = receipt.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"paired PAPER tick execution receipt {field} is invalid"
            )

    for field in (
        "approver_principal",
        "approval_id",
        "authorization_expires_at",
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
        "tick_status",
    ):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(
                f"paired PAPER tick execution receipt {field} is invalid"
            )

    requested = receipt.get("requested_position_ids")
    if receipt["pair_id"] == receipt["previous_pair_id"]:
        raise ValueError(
            "recursive rollover paired PAPER tick receipt pair id was not advanced"
        )
    if requested != [
        receipt["incumbent_position_id"],
        receipt["challenger_position_id"],
    ]:
        raise ValueError(
            "paired PAPER tick execution receipt position scope mismatch"
        )
    if not isinstance(receipt.get("tick_result"), dict):
        raise ValueError(
            "paired PAPER tick execution receipt result is invalid"
        )
    if receipt["tick_result_sha256"] != _sha256_bytes(
        _canonical_bytes(receipt["tick_result"])
    ):
        raise ValueError(
            "paired PAPER tick execution receipt result digest mismatch"
        )
    if not isinstance(receipt.get("run_item_statuses"), dict):
        raise ValueError(
            "paired PAPER tick execution receipt item statuses are invalid"
        )
    if set(receipt["run_item_statuses"]) != set(requested):
        raise ValueError(
            "paired PAPER tick execution receipt item status scope mismatch"
        )

    if receipt.get("positions_requested") != 2:
        raise ValueError(
            "paired PAPER tick execution receipt requires two positions"
        )
    if receipt.get("groups") != 1:
        raise ValueError(
            "paired PAPER tick execution receipt requires one chain group"
        )
    if receipt.get("items_total") != 2:
        raise ValueError(
            "paired PAPER tick execution receipt requires two run items"
        )
    counts = (
        int(receipt["items_applied"])
        + int(receipt["items_skipped"])
        + int(receipt["items_failed"])
    )
    if counts != 2:
        raise ValueError(
            "paired PAPER tick execution receipt item counts do not total two"
        )

    complete = (
        receipt["tick_status"] == "COMPLETE"
        and receipt["items_applied"] == 2
        and receipt["items_skipped"] == 0
        and receipt["items_failed"] == 0
    )
    if receipt.get("pair_tick_complete") is not complete:
        raise ValueError(
            "paired PAPER tick execution receipt completion flag mismatch"
        )
    if receipt.get("pair_tick_partial_failure") is not (not complete):
        raise ValueError(
            "paired PAPER tick execution receipt partial flag mismatch"
        )
    if receipt.get("run_reused_existing") is not False:
        raise ValueError(
            "paired PAPER tick execution receipt reused an existing run"
        )

    for field in (
        "fresh_readiness_matches_saved",
        "human_recursive_rollover_pair_evidence_tick_authorization_verified",
        "exact_position_scope_verified",
        "exact_chain_snapshot_verified",
        "exact_quote_map_verified",
        "derived_pool_safety_bound",
        "deterministic_run_id_verified",
        "one_tick_only",
        "paper_supervisor_tick_authorized",
        "paper_supervisor_tick_executed",
        "requires_post_tick_audit",
        "production_pio_database_modified",
    ):
        if receipt.get(field) is not True:
            raise ValueError(
                f"paired PAPER tick execution receipt requires {field}=true"
            )

    for field in (
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_performed",
        "new_live_capital_used",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
    ):
        if receipt.get(field) is not False:
            raise ValueError(
                f"paired PAPER tick execution receipt requires {field}=false"
            )

    if receipt["saved_readiness_sha256"] != receipt[
        "fresh_readiness_sha256"
    ]:
        raise ValueError(
            "paired PAPER tick execution readiness digest mismatch"
        )
    if (
        receipt["pio_database_sha256_before"]
        == receipt["pio_database_sha256_after"]
        and receipt["pio_wal_sha256_before"]
        == receipt["pio_wal_sha256_after"]
        and receipt["pio_shm_sha256_before"]
        == receipt["pio_shm_sha256_after"]
    ):
        raise ValueError(
            "paired PAPER tick execution receipt shows no DB mutation"
        )

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    if receipt["receipt_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "paired PAPER evidence tick execution receipt digest mismatch"
        )


def execute_phase8_paired_ml_paper_recursive_rollover_evidence_tick_once(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_execution_readiness_path: str | Path,
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

    readiness_module = _load_readiness(source)
    saved_readiness = _load_json(
        saved_execution_readiness_path,
        label="saved paired PAPER evidence tick execution readiness",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved paired PAPER evidence tick signed verification",
    )
    readiness_module.validate_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_readiness(
        saved_readiness
    )
    if saved_readiness.get(
        "one_pair_evidence_tick_execution_readiness_ready"
    ) is not True:
        raise ValueError("saved paired PAPER evidence tick readiness is not ready")
    if saved_readiness.get("readiness_only") is not True:
        raise ValueError("saved paired PAPER evidence tick artifact is not readiness-only")
    if saved_readiness.get(
        "requires_immediate_pair_scoped_executor"
    ) is not True:
        raise ValueError(
            "saved paired PAPER evidence tick readiness lacks executor boundary"
        )

    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    lock_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(LOCK_PATH, lock_flags, 0o600)
    except OSError as exc:
        raise ValueError(
            "paired PAPER evidence tick executor lock path is unsafe"
        ) from exc
    if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
        os.close(lock_fd)
        raise ValueError(
            "paired PAPER evidence tick executor lock must be a regular file"
        )

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "another paired PAPER evidence tick executor is active"
            ) from exc

        fresh_readiness = (
            readiness_module.build_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_readiness(
                repository=production,
                source_tree=source,
                recursive_rollover_post_audit_path=recursive_rollover_post_audit_path,
                saved_supervision_readiness_path=(
                    saved_supervision_readiness_path
                ),
                request_path=request_path,
                saved_signed_authorization_verification_path=(
                    saved_signed_authorization_verification_path
                ),
                signed_payload_path=signed_payload_path,
                signature_path=signature_path,
                allowed_signers_path=allowed_signers_path,
                expected_allowed_signers_sha256=(
                    expected_allowed_signers_sha256
                ),
                now=now,
            )
        )
        readiness_module.validate_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_readiness(
            fresh_readiness
        )
        if fresh_readiness != saved_readiness:
            raise ValueError(
                "fresh paired PAPER evidence tick readiness differs from saved"
            )

        (
            supervision_module,
            _,
            _,
            live_module,
            latest_module,
        ) = readiness_module._load_reviewed(source)
        _, base_supervision_module = supervision_module._load_reviewed(source)
        database = base_supervision_module._database_path(production)
        before_state = base_supervision_module._database_state(database)
        expected_before = {
            "database": saved_readiness["pio_database_sha256"],
            "wal": saved_readiness["pio_wal_sha256"],
            "shm": saved_readiness["pio_shm_sha256"],
        }
        if before_state != expected_before:
            raise ValueError(
                "Pio database changed after paired PAPER tick readiness"
            )

        if saved_readiness["pair_id"] == saved_readiness["previous_pair_id"]:
            raise ValueError("recursive rollover paired PAPER tick pair id was not advanced")

        items = tuple(
            latest_module.LatestPaperCycleItem(**item)
            for item in saved_readiness["pair_cycle_items"]
        )
        if _sha256_bytes(
            _canonical_bytes([asdict(item) for item in items])
        ) != saved_readiness["pair_cycle_items_sha256"]:
            raise ValueError(
                "paired PAPER tick execution item digest changed"
            )

        safety_config = live_module.PoolSafetyConfig(
            **saved_readiness["pool_safety_config"]
        )
        management_config = latest_module.PositionManagementConfig(
            **saved_readiness["position_management_config"]
        )
        result = latest_module.run_latest_live_paper_cycle(
            latest_module.Storage(database),
            cycle_id=saved_readiness["evidence_cycle_id"],
            items=items,
            safety_config=safety_config,
            management_config=management_config,
            retry_failed=False,
        )
        result_record = result.to_record()

        if result.positions_requested != 2 or result.groups != 1:
            raise ValueError(
                "paired PAPER evidence tick escaped two-position one-group scope"
            )
        if len(result.details) != 1:
            raise ValueError(
                "paired PAPER evidence tick returned unexpected group count"
            )
        detail = result.details[0]
        if detail.observed_at != saved_readiness["target_chain_observed_at"]:
            raise ValueError(
                "paired PAPER evidence tick used a different chain snapshot"
            )
        if detail.run_id != saved_readiness["expected_run_id"]:
            raise ValueError(
                "paired PAPER evidence tick deterministic run id changed"
            )
        if set(detail.positions) != set(
            saved_readiness["requested_position_ids"]
        ):
            raise ValueError(
                "paired PAPER evidence tick position scope changed"
            )
        if detail.report.reused_existing_run is not False:
            raise ValueError(
                "paired PAPER evidence tick unexpectedly reused an existing run"
            )

        after_database = base_supervision_module._database_path(production)
        after_state = base_supervision_module._database_state(database)
        if after_database != database:
            raise ValueError(
                "paired PAPER evidence tick database path changed"
            )
        if after_state == before_state:
            raise ValueError(
                "paired PAPER evidence tick did not mutate production database"
            )

        conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            run = conn.execute(
                """
                SELECT status, items_total, items_applied,
                       items_skipped, items_failed
                FROM paper_runs
                WHERE run_id = ?
                LIMIT 1
                """,
                (saved_readiness["expected_run_id"],),
            ).fetchone()
            if run is None:
                raise ValueError(
                    "paired PAPER evidence tick run record is missing"
                )
            rows = conn.execute(
                """
                SELECT position_id, status
                FROM paper_run_items
                WHERE run_id = ?
                ORDER BY position_id
                """,
                (saved_readiness["expected_run_id"],),
            ).fetchall()
        finally:
            conn.close()
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    run_status = str(run[0])
    items_total = int(run[1])
    items_applied = int(run[2])
    items_skipped = int(run[3])
    items_failed = int(run[4])
    item_statuses = {str(row[0]): str(row[1]) for row in rows}
    if set(item_statuses) != set(saved_readiness["requested_position_ids"]):
        raise ValueError(
            "paired PAPER evidence tick persisted item scope changed"
        )
    if (
        result.applied != items_applied
        or result.failed != items_failed
        or result.details[0].report.items_total != items_total
        or result.details[0].report.items_skipped != items_skipped
    ):
        raise ValueError(
            "paired PAPER evidence tick result/persisted run mismatch"
        )

    complete = (
        run_status == "COMPLETE"
        and items_total == 2
        and items_applied == 2
        and items_skipped == 0
        and items_failed == 0
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
        "saved_readiness_sha256": saved_readiness["readiness_sha256"],
        "fresh_readiness_sha256": fresh_readiness["readiness_sha256"],
        "saved_request_sha256": saved_readiness["saved_request_sha256"],
        "source_recursive_rollover_post_audit_sha256": saved_readiness[
            "source_recursive_rollover_post_audit_sha256"
        ],
        "recursive_rollover_entry_request_sha256": saved_readiness[
            "recursive_rollover_entry_request_sha256"
        ],
        "recursive_rollover_entry_input_verification_sha256": saved_readiness[
            "recursive_rollover_entry_input_verification_sha256"
        ],
        "source_final_evaluation_sha256": saved_readiness[
            "source_final_evaluation_sha256"
        ],
        "signed_authorization_verification_sha256": saved_readiness[
            "fresh_signed_authorization_verification_sha256"
        ],
        "approval_payload_sha256": saved_verification[
            "approval_payload_sha256"
        ],
        "approval_signature_sha256": saved_verification[
            "approval_signature_sha256"
        ],
        "allowed_signers_sha256": saved_verification[
            "allowed_signers_sha256"
        ],
        "approver_principal": saved_verification["approver_principal"],
        "approval_id": saved_verification["approval_id"],
        "authorization_expires_at": saved_verification["expires_at"],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before_state["database"],
        "pio_database_sha256_after": after_state["database"],
        "pio_wal_sha256_before": before_state["wal"],
        "pio_wal_sha256_after": after_state["wal"],
        "pio_shm_sha256_before": before_state["shm"],
        "pio_shm_sha256_after": after_state["shm"],
        "active_cycle_id": saved_readiness["active_cycle_id"],
        "incumbent_model_id": saved_readiness["incumbent_model_id"],
        "challenger_model_id": saved_readiness["challenger_model_id"],
        "account_id": saved_readiness["account_id"],
        "previous_pair_id": saved_readiness["previous_pair_id"],
        "pair_id": saved_readiness["pair_id"],
        "pool_address": saved_readiness["pool_address"],
        "entry_observed_at": saved_readiness["entry_observed_at"],
        "requested_position_ids": saved_readiness[
            "requested_position_ids"
        ],
        "incumbent_position_id": saved_readiness[
            "incumbent_position_id"
        ],
        "challenger_position_id": saved_readiness[
            "challenger_position_id"
        ],
        "evidence_cycle_id": saved_readiness["evidence_cycle_id"],
        "expected_run_id": saved_readiness["expected_run_id"],
        "target_chain_observed_at": saved_readiness[
            "target_chain_observed_at"
        ],
        "pair_cycle_items_sha256": saved_readiness[
            "pair_cycle_items_sha256"
        ],
        "derived_pool_safety_sha256": saved_readiness[
            "derived_pool_safety_sha256"
        ],
        "pool_safety_config": saved_readiness["pool_safety_config"],
        "position_management_config": saved_readiness[
            "position_management_config"
        ],
        "tick_result": result_record,
        "tick_result_sha256": _sha256_bytes(
            _canonical_bytes(result_record)
        ),
        "tick_status": run_status,
        "positions_requested": result.positions_requested,
        "groups": result.groups,
        "items_total": items_total,
        "items_applied": items_applied,
        "items_skipped": items_skipped,
        "items_failed": items_failed,
        "run_reused_existing": False,
        "run_item_statuses": item_statuses,
        "fresh_readiness_matches_saved": True,
        "human_recursive_rollover_pair_evidence_tick_authorization_verified": True,
        "exact_position_scope_verified": True,
        "exact_chain_snapshot_verified": True,
        "exact_quote_map_verified": True,
        "derived_pool_safety_bound": True,
        "deterministic_run_id_verified": True,
        "one_tick_only": True,
        "paper_supervisor_tick_authorized": True,
        "paper_supervisor_tick_executed": True,
        "pair_tick_complete": complete,
        "pair_tick_partial_failure": not complete,
        "requires_post_tick_audit": True,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_performed": False,
        "new_live_capital_used": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": True,
    }
    receipt = {
        **identity,
        "receipt_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_recursive_rollover_evidence_tick_execution_receipt(
        receipt
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one signed pair-scoped Phase 8 PAPER evidence "
            "tick through the existing idempotent latest-chain runner. Only "
            "the two readiness-bound positions are processed. This does not "
            "authorize the scheduler, ongoing PAPER collection, live capital "
            "or Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-execution-readiness", required=True)
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

    receipt = execute_phase8_paired_ml_paper_recursive_rollover_evidence_tick_once(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_execution_readiness_path=args.saved_execution_readiness,
        recursive_rollover_post_audit_path=args.recursive_rollover_post_audit,
        saved_supervision_readiness_path=args.saved_supervision_readiness,
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
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
