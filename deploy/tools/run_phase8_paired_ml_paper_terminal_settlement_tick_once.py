from __future__ import annotations

import argparse
from dataclasses import asdict
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_TERMINAL_SETTLEMENT_TICK_ONE_SHOT_EXECUTION_RECEIPT_V1"
)

READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_terminal_settlement_tick_execution_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "73f4eb8bdf75da628596e1d2d47ade8caeefa9a2",
}

LOCK_PATH = Path(
    "/var/tmp/pio-phase8-paired-paper-terminal-settlement-tick-one-shot.lock"
)

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_readiness_sha256",
    "fresh_readiness_sha256",
    "saved_request_sha256",
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
    "single_cycle_item_sha256",
    "derived_pool_safety_sha256",
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
    "run_item_status",
    "fresh_readiness_matches_saved",
    "human_settlement_tick_authorization_verified",
    "exact_single_position_scope_verified",
    "exact_chain_snapshot_verified",
    "exact_quote_map_verified",
    "derived_pool_safety_bound",
    "deterministic_run_id_verified",
    "one_tick_only",
    "paper_settlement_tick_authorized",
    "paper_settlement_tick_executed",
    "settlement_tick_complete",
    "settlement_tick_partial_failure",
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
            "reviewed paired PAPER settlement execution readiness is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[READINESS_TOOL]:
        raise ValueError(
            "reviewed paired PAPER settlement execution readiness blob mismatch"
        )
    return _load_module(
        path,
        "phase8_terminal_settlement_one_shot_readiness",
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


def validate_phase8_paired_ml_paper_terminal_settlement_tick_execution_receipt(
    receipt: dict[str, Any],
) -> None:
    if not isinstance(receipt, dict):
        raise ValueError(
            "paired PAPER settlement tick execution receipt must be an object"
        )
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError(
            "paired PAPER settlement tick execution receipt schema mismatch"
        )
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported paired PAPER settlement tick execution receipt format"
        )
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected paired PAPER settlement tick execution receipt type"
        )
    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "paired PAPER settlement tick execution receipt lineage mismatch"
        )

    for field in (
        "saved_readiness_sha256",
        "fresh_readiness_sha256",
        "saved_request_sha256",
        "signed_authorization_verification_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "single_cycle_item_sha256",
        "derived_pool_safety_sha256",
        "tick_result_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(receipt.get(field), 64):
            raise ValueError(
                f"paired PAPER settlement tick receipt {field} is invalid"
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
                f"paired PAPER settlement tick receipt {field} is invalid"
            )

    if receipt.get("requested_position_ids") != [receipt["open_position_id"]]:
        raise ValueError(
            "paired PAPER settlement tick receipt position scope mismatch"
        )
    if not isinstance(receipt.get("tick_result"), dict):
        raise ValueError(
            "paired PAPER settlement tick result is invalid"
        )
    if not isinstance(receipt.get("run_item_status"), str) or not receipt[
        "run_item_status"
    ]:
        raise ValueError(
            "paired PAPER settlement tick item status is invalid"
        )

    for field in (
        "fresh_readiness_matches_saved",
        "human_settlement_tick_authorization_verified",
        "exact_single_position_scope_verified",
        "exact_chain_snapshot_verified",
        "exact_quote_map_verified",
        "derived_pool_safety_bound",
        "deterministic_run_id_verified",
        "one_tick_only",
        "paper_settlement_tick_authorized",
        "paper_settlement_tick_executed",
        "requires_post_tick_audit",
        "production_pio_database_modified",
    ):
        if receipt.get(field) is not True:
            raise ValueError(
                f"paired PAPER settlement tick receipt requires {field}=true"
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
                f"paired PAPER settlement tick receipt requires {field}=false"
            )

    if receipt["saved_readiness_sha256"] != receipt[
        "fresh_readiness_sha256"
    ]:
        raise ValueError(
            "paired PAPER settlement tick readiness digest mismatch"
        )
    if receipt["positions_requested"] != 1 or receipt["groups"] != 1:
        raise ValueError(
            "paired PAPER settlement tick escaped single-position scope"
        )
    if receipt["items_total"] != 1:
        raise ValueError(
            "paired PAPER settlement tick run item count mismatch"
        )
    if (
        receipt["items_applied"]
        + receipt["items_skipped"]
        + receipt["items_failed"]
        != 1
    ):
        raise ValueError(
            "paired PAPER settlement tick run count accounting mismatch"
        )
    if receipt["run_reused_existing"] is not False:
        raise ValueError(
            "paired PAPER settlement tick unexpectedly reused an existing run"
        )

    complete = receipt["items_failed"] == 0
    partial = receipt["items_failed"] > 0
    if receipt["settlement_tick_complete"] is not complete:
        raise ValueError(
            "paired PAPER settlement tick complete flag mismatch"
        )
    if receipt["settlement_tick_partial_failure"] is not partial:
        raise ValueError(
            "paired PAPER settlement tick partial-failure flag mismatch"
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
            "paired PAPER settlement tick receipt shows no DB mutation"
        )

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    if receipt["receipt_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "paired PAPER settlement tick execution receipt digest mismatch"
        )


def execute_phase8_paired_ml_paper_terminal_settlement_tick_once(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_execution_readiness_path: str | Path,
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

    readiness_module = _load_readiness(source)
    saved_readiness = _load_json(
        saved_execution_readiness_path,
        label="saved paired PAPER settlement tick execution readiness",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved paired PAPER settlement signed verification",
    )
    readiness_module.validate_phase8_paired_ml_paper_terminal_settlement_tick_execution_readiness(
        saved_readiness
    )
    if saved_readiness.get(
        "settlement_tick_execution_readiness_ready"
    ) is not True:
        raise ValueError(
            "saved paired PAPER settlement tick readiness is not ready"
        )
    if saved_readiness.get("readiness_only") is not True:
        raise ValueError(
            "saved paired PAPER settlement artifact is not readiness-only"
        )
    if saved_readiness.get(
        "requires_immediate_single_position_executor"
    ) is not True:
        raise ValueError(
            "saved paired PAPER settlement readiness lacks executor boundary"
        )

    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    lock_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(LOCK_PATH, lock_flags, 0o600)
    except OSError as exc:
        raise ValueError(
            "paired PAPER settlement executor lock path is unsafe"
        ) from exc
    if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
        os.close(lock_fd)
        raise ValueError(
            "paired PAPER settlement executor lock must be a regular file"
        )

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "another paired PAPER settlement executor is active"
            ) from exc

        fresh_readiness = (
            readiness_module.build_phase8_paired_ml_paper_terminal_settlement_tick_execution_readiness(
                repository=production,
                source_tree=source,
                terminal_evaluation_path=terminal_evaluation_path,
                saved_settlement_readiness_path=(
                    saved_settlement_readiness_path
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
        readiness_module.validate_phase8_paired_ml_paper_terminal_settlement_tick_execution_readiness(
            fresh_readiness
        )
        if fresh_readiness != saved_readiness:
            raise ValueError(
                "fresh paired PAPER settlement tick readiness differs from saved"
            )

        (
            settlement_readiness_module,
            _,
            _,
            live_module,
            latest_module,
        ) = readiness_module._load_reviewed(source)
        database = settlement_readiness_module._database_path(production)
        before_state = settlement_readiness_module._database_state(database)
        expected_before = {
            "database": saved_readiness["pio_database_sha256"],
            "wal": saved_readiness["pio_wal_sha256"],
            "shm": saved_readiness["pio_shm_sha256"],
        }
        if before_state != expected_before:
            raise ValueError(
                "Pio database changed after paired PAPER settlement readiness"
            )

        item = latest_module.LatestPaperCycleItem(
            **saved_readiness["single_cycle_item"]
        )
        if _sha256_bytes(
            _canonical_bytes(asdict(item))
        ) != saved_readiness["single_cycle_item_sha256"]:
            raise ValueError(
                "paired PAPER settlement execution item digest changed"
            )
        if item.position_id != saved_readiness["open_position_id"]:
            raise ValueError(
                "paired PAPER settlement execution position scope changed"
            )

        safety_config = live_module.PoolSafetyConfig(
            **saved_readiness["pool_safety_config"]
        )
        management_config = latest_module.PositionManagementConfig(
            **saved_readiness["position_management_config"]
        )
        result = latest_module.run_latest_live_paper_cycle(
            latest_module.Storage(database),
            cycle_id=saved_readiness["settlement_cycle_id"],
            items=(item,),
            safety_config=safety_config,
            management_config=management_config,
            retry_failed=False,
        )
        result_record = result.to_record()

        if result.positions_requested != 1 or result.groups != 1:
            raise ValueError(
                "paired PAPER settlement tick escaped single-position scope"
            )
        if len(result.details) != 1:
            raise ValueError(
                "paired PAPER settlement tick returned unexpected group count"
            )
        detail = result.details[0]
        if detail.observed_at != saved_readiness["target_chain_observed_at"]:
            raise ValueError(
                "paired PAPER settlement tick used a different chain snapshot"
            )
        if detail.run_id != saved_readiness["expected_run_id"]:
            raise ValueError(
                "paired PAPER settlement tick deterministic run id changed"
            )
        if list(detail.positions) != [saved_readiness["open_position_id"]]:
            raise ValueError(
                "paired PAPER settlement tick position scope changed"
            )
        if detail.report.reused_existing_run is not False:
            raise ValueError(
                "paired PAPER settlement tick unexpectedly reused an existing run"
            )
        if detail.report.items_total != 1:
            raise ValueError(
                "paired PAPER settlement tick persisted item count changed"
            )

        after_state = settlement_readiness_module._database_state(database)
        if after_state == before_state:
            raise ValueError(
                "paired PAPER settlement tick did not mutate production database"
            )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    request = _load_json(
        request_path,
        label="paired PAPER settlement tick request",
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
        "saved_readiness_sha256": saved_readiness["readiness_sha256"],
        "fresh_readiness_sha256": fresh_readiness["readiness_sha256"],
        "saved_request_sha256": request["request_sha256"],
        "signed_authorization_verification_sha256": saved_verification[
            "verification_sha256"
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
        "approver_principal": saved_readiness["approver_principal"],
        "approval_id": saved_readiness["approval_id"],
        "authorization_expires_at": saved_readiness[
            "authorization_expires_at"
        ],
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
        "pair_id": saved_readiness["pair_id"],
        "pool_address": saved_readiness["pool_address"],
        "terminal_tick_observed_at": saved_readiness[
            "terminal_tick_observed_at"
        ],
        "incumbent_position_id": saved_readiness[
            "incumbent_position_id"
        ],
        "challenger_position_id": saved_readiness[
            "challenger_position_id"
        ],
        "open_position_id": saved_readiness["open_position_id"],
        "closed_position_id": saved_readiness["closed_position_id"],
        "open_position_policy_source": saved_readiness[
            "open_position_policy_source"
        ],
        "open_position_model_id": saved_readiness[
            "open_position_model_id"
        ],
        "requested_position_ids": saved_readiness[
            "requested_position_ids"
        ],
        "settlement_cycle_id": saved_readiness["settlement_cycle_id"],
        "expected_run_id": saved_readiness["expected_run_id"],
        "target_chain_observed_at": saved_readiness[
            "target_chain_observed_at"
        ],
        "single_cycle_item_sha256": saved_readiness[
            "single_cycle_item_sha256"
        ],
        "derived_pool_safety_sha256": saved_readiness[
            "derived_pool_safety_sha256"
        ],
        "tick_result": result_record,
        "tick_result_sha256": _sha256_bytes(
            _canonical_bytes(result_record)
        ),
        "tick_status": detail.report.status,
        "positions_requested": result.positions_requested,
        "groups": result.groups,
        "items_total": detail.report.items_total,
        "items_applied": detail.report.items_applied,
        "items_skipped": detail.report.items_skipped,
        "items_failed": detail.report.items_failed,
        "run_reused_existing": detail.report.reused_existing_run,
        "run_item_status": detail.report.items[0].status,
        "fresh_readiness_matches_saved": True,
        "human_settlement_tick_authorization_verified": True,
        "exact_single_position_scope_verified": True,
        "exact_chain_snapshot_verified": True,
        "exact_quote_map_verified": True,
        "derived_pool_safety_bound": True,
        "deterministic_run_id_verified": True,
        "one_tick_only": True,
        "paper_settlement_tick_authorized": True,
        "paper_settlement_tick_executed": True,
        "settlement_tick_complete": detail.report.items_failed == 0,
        "settlement_tick_partial_failure": detail.report.items_failed > 0,
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
    validate_phase8_paired_ml_paper_terminal_settlement_tick_execution_receipt(
        receipt
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one signed single-position terminal PAPER "
            "settlement tick after fresh readiness. The existing PAPER policy "
            "may HOLD, REBALANCE or EXIT; this tool does not force an exit and "
            "does not authorize any live or promotion action."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
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

    receipt = execute_phase8_paired_ml_paper_terminal_settlement_tick_once(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_execution_readiness_path=args.saved_readiness,
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
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
