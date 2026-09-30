from __future__ import annotations

import argparse
from decimal import Decimal
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_CYCLE_ENTRY_ONE_SHOT_EXECUTION_RECEIPT_V1"
)

READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_execution_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "a5e2c6d4d32e4f1885db71e61d2bd60ecdef4afd",
}

LOCK_PATH = Path(
    "/var/tmp/pio-phase8-recursive-reentry-cycle-paired-paper-entry-one-shot.lock"
)

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_readiness_sha256",
    "fresh_readiness_sha256",
    "recursive_rollover_reentry_cycle_entry_request_sha256",
    "signed_authorization_verification_sha256",
    "recursive_rollover_reentry_cycle_input_verification_sha256",
    "source_final_evaluation_sha256",
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
    "amount_x",
    "amount_y",
    "network_cost_y_atomic",
    "capital_quote",
    "entry_cost_quote",
    "required_pair_cash_quote",
    "decision_observed_at",
    "incumbent_position_id",
    "challenger_position_id",
    "incumbent_event_key",
    "challenger_event_key",
    "candidate_frame_sha256",
    "incumbent_inference_sha256",
    "challenger_inference_sha256",
    "incumbent_choice_sha256",
    "challenger_choice_sha256",
    "incumbent_position",
    "challenger_position",
    "account_cash_quote_before",
    "account_cash_quote_after",
    "account_open_positions_before",
    "account_open_positions_after",
    "fresh_readiness_matches_saved",
    "human_recursive_reentry_cycle_paired_paper_entry_authorization_verified",
    "zero_open_positions_verified_at_commit",
    "same_candidate_frame_verified",
    "same_decision_snapshot_verified",
    "equal_capital_verified",
    "atomic_pair_open_verified",
    "counterfactual_preflights_reverified",
    "chain_bound_verified",
    "no_lookahead_verified",
    "one_shot_only",
    "paper_pair_entry_authorized",
    "paper_pair_entry_executed",
    "paper_pair_entry_completed",
    "requires_post_pair_audit",
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
            "reviewed recursive re-entry cycle paired PAPER execution readiness is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[READINESS_TOOL]:
        raise ValueError(
            "reviewed recursive re-entry cycle paired PAPER execution readiness blob mismatch"
        )
    return _load_module(
        path,
        "phase8_recursive_reentry_cycle_paired_paper_one_shot_readiness",
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


def validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_execution_receipt(
    receipt: dict[str, Any],
) -> None:
    if not isinstance(receipt, dict):
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution receipt must be an object"
        )
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution receipt schema mismatch"
        )
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported recursive re-entry cycle paired PAPER execution receipt format"
        )
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected recursive re-entry cycle paired PAPER execution receipt type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution receipt lineage mismatch"
        )

    for field in (
        "saved_readiness_sha256",
        "fresh_readiness_sha256",
        "recursive_rollover_reentry_cycle_entry_request_sha256",
        "signed_authorization_verification_sha256",
        "recursive_rollover_reentry_cycle_input_verification_sha256",
        "source_final_evaluation_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "candidate_frame_sha256",
        "incumbent_inference_sha256",
        "challenger_inference_sha256",
        "incumbent_choice_sha256",
        "challenger_choice_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(receipt.get(field), 64):
            raise ValueError(
                f"recursive re-entry cycle paired PAPER execution receipt {field} is invalid"
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
                f"recursive re-entry cycle paired PAPER execution receipt {field} is invalid"
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
        "decision_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "incumbent_event_key",
        "challenger_event_key",
    ):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(
                f"recursive re-entry cycle paired PAPER execution receipt {field} is invalid"
            )

    if receipt["pair_id"] == receipt["previous_pair_id"]:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution receipt pair id was not advanced"
        )
    if receipt["incumbent_model_id"] == receipt["challenger_model_id"]:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution model identities must differ"
        )
    if receipt["incumbent_position_id"] == receipt["challenger_position_id"]:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution position ids must differ"
        )

    for field in ("incumbent_position", "challenger_position"):
        if not isinstance(receipt.get(field), dict):
            raise ValueError(
                f"recursive re-entry cycle paired PAPER execution receipt {field} is invalid"
            )

    for field in (
        "fresh_readiness_matches_saved",
        "human_recursive_reentry_cycle_paired_paper_entry_authorization_verified",
        "zero_open_positions_verified_at_commit",
        "same_candidate_frame_verified",
        "same_decision_snapshot_verified",
        "equal_capital_verified",
        "atomic_pair_open_verified",
        "counterfactual_preflights_reverified",
        "chain_bound_verified",
        "no_lookahead_verified",
        "one_shot_only",
        "paper_pair_entry_authorized",
        "paper_pair_entry_executed",
        "paper_pair_entry_completed",
        "requires_post_pair_audit",
        "production_pio_database_modified",
    ):
        if receipt.get(field) is not True:
            raise ValueError(
                f"recursive re-entry cycle paired PAPER execution receipt requires {field}=true"
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
                f"recursive re-entry cycle paired PAPER execution receipt requires {field}=false"
            )

    if receipt["saved_readiness_sha256"] != receipt[
        "fresh_readiness_sha256"
    ]:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution readiness digest mismatch"
        )

    expected_debit = 2.0 * (
        float(receipt["capital_quote"])
        + float(receipt["entry_cost_quote"])
    )
    if not math.isclose(
        float(receipt["required_pair_cash_quote"]),
        expected_debit,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution pair cash binding mismatch"
        )
    actual_debit = (
        float(receipt["account_cash_quote_before"])
        - float(receipt["account_cash_quote_after"])
    )
    if not math.isclose(
        actual_debit,
        expected_debit,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution account cash delta mismatch"
        )
    if int(receipt["account_open_positions_before"]) != 0:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution did not start from zero open positions"
        )
    if int(receipt["account_open_positions_after"]) != 2:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution did not finish with two open positions"
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
            "recursive re-entry cycle paired PAPER execution receipt shows no DB mutation"
        )

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    if receipt["receipt_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution receipt digest mismatch"
        )


def execute_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_once(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_execution_readiness_path: str | Path,
    final_evaluation_path: str | Path,
    recursive_rollover_reentry_cycle_input_path: str | Path,
    saved_account_readiness_path: str | Path,
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
        label="saved recursive re-entry cycle paired PAPER execution readiness",
    )
    saved_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="saved recursive re-entry cycle paired PAPER signed authorization verification",
    )
    readiness_module.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_execution_readiness(
        saved_readiness
    )
    if saved_readiness.get(
        "paper_pair_entry_execution_readiness_ready"
    ) is not True:
        raise ValueError(
            "saved recursive re-entry cycle paired PAPER readiness is not ready"
        )
    if saved_readiness.get("readiness_only") is not True:
        raise ValueError(
            "saved recursive re-entry cycle paired PAPER artifact is not readiness-only"
        )
    if saved_readiness.get(
        "requires_immediate_one_shot_pair_executor"
    ) is not True:
        raise ValueError(
            "saved recursive re-entry cycle paired PAPER readiness lacks executor boundary"
        )

    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    lock_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(LOCK_PATH, lock_flags, 0o600)
    except OSError as exc:
        raise ValueError(
            "recursive re-entry cycle paired PAPER executor lock path is unsafe"
        ) from exc
    if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
        os.close(lock_fd)
        raise ValueError(
            "recursive re-entry cycle paired PAPER executor lock must be a regular file"
        )

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "another recursive re-entry cycle paired PAPER entry executor is active"
            ) from exc

        fresh_readiness = (
            readiness_module.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_execution_readiness(
                repository=production,
                source_tree=source,
                final_evaluation_path=final_evaluation_path,
                recursive_rollover_reentry_cycle_input_path=recursive_rollover_reentry_cycle_input_path,
                saved_account_readiness_path=saved_account_readiness_path,
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
        readiness_module.validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_execution_readiness(
            fresh_readiness
        )
        if fresh_readiness != saved_readiness:
            raise ValueError(
                "fresh recursive re-entry cycle paired PAPER execution readiness differs from saved"
            )

        (
            _,
            rollover_account_module,
            _,
            _,
            base_readiness_module,
        ) = readiness_module._load_reviewed(source)
        base_account_module, _, _, pair_module = (
            base_readiness_module._load_reviewed(source)
        )
        database = base_account_module._production_database(production)
        if Path(str(saved_readiness["pio_database_path"])).resolve() != database:
            raise ValueError(
                "recursive re-entry cycle paired PAPER readiness database mismatch"
            )

        before_state = base_account_module._database_state(database)
        expected_before = {
            "database": saved_readiness["pio_database_sha256"],
            "wal": saved_readiness["pio_wal_sha256"],
            "shm": saved_readiness["pio_shm_sha256"],
        }
        if before_state != expected_before:
            raise ValueError(
                "Pio database changed after recursive re-entry cycle paired PAPER readiness"
            )

        storage = pair_module.Storage(database)
        account_before_snapshot = pair_module.paper_account_snapshot(
            storage,
            account_id=saved_readiness["account_id"],
        )
        account_before = account_before_snapshot.to_record()
        if int(account_before["open_positions"]) != 0:
            raise ValueError(
                "recursive re-entry cycle paired PAPER executor requires zero open positions"
            )
        if float(account_before["cash_quote"]) < float(
            saved_readiness["required_pair_cash_quote"]
        ):
            raise ValueError(
                "recursive re-entry cycle paired PAPER account cash became insufficient"
            )

        incumbent_choice = pair_module.PairedMLPaperChoice(
            **saved_readiness["incumbent_choice"]
        )
        challenger_choice = pair_module.PairedMLPaperChoice(
            **saved_readiness["challenger_choice"]
        )
        incumbent_preflight = pair_module._preflight_choice(
            storage,
            choice=incumbent_choice,
            amount_x=int(saved_readiness["amount_x"]),
            amount_y=int(saved_readiness["amount_y"]),
            max_share_bps=int(saved_readiness["max_share_bps"]),
            favor_x_in_active_bin=bool(
                saved_readiness["favor_x_in_active_bin"]
            ),
        )
        challenger_preflight = pair_module._preflight_choice(
            storage,
            choice=challenger_choice,
            amount_x=int(saved_readiness["amount_x"]),
            amount_y=int(saved_readiness["amount_y"]),
            max_share_bps=int(saved_readiness["max_share_bps"]),
            favor_x_in_active_bin=bool(
                saved_readiness["favor_x_in_active_bin"]
            ),
        )
        if readiness_module._hash_record(
            incumbent_preflight
        ) != saved_readiness["incumbent_preflight_sha256"]:
            raise ValueError(
                "recursive re-entry cycle paired PAPER incumbent preflight drifted"
            )
        if readiness_module._hash_record(
            challenger_preflight
        ) != saved_readiness["challenger_preflight_sha256"]:
            raise ValueError(
                "recursive re-entry cycle paired PAPER challenger preflight drifted"
            )

        capital = Decimal(str(saved_readiness["capital_quote"]))
        cost = Decimal(str(saved_readiness["entry_cost_quote"]))
        with storage.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")

            open_row = conn.execute(
                """
                SELECT COUNT(*)
                FROM paper_positions
                WHERE account_id = ? AND status = 'OPEN'
                """,
                (saved_readiness["account_id"],),
            ).fetchone()
            if open_row is None or int(open_row[0]) != 0:
                raise ValueError(
                    "recursive re-entry cycle paired PAPER account gained an open position before commit"
                )

            pair_module._verify_pair_transaction_state(
                conn,
                cycle_id=saved_readiness["active_cycle_id"],
                incumbent_model_id=saved_readiness["incumbent_model_id"],
                challenger_model_id=saved_readiness[
                    "challenger_model_id"
                ],
            )
            pair_module._open_paper_position_in_conn(
                conn,
                event_key=saved_readiness["incumbent_event_key"],
                account_id=saved_readiness["account_id"],
                position_id=saved_readiness["incumbent_position_id"],
                pool_address=saved_readiness["pool_address"],
                policy_source="ML_CHAMPION",
                strategy=incumbent_choice.strategy,
                min_bin_id=incumbent_choice.min_bin_id,
                max_bin_id=incumbent_choice.max_bin_id,
                capital=capital,
                cost=cost,
                model_id=saved_readiness["incumbent_model_id"],
                event_time=saved_readiness["decision_observed_at"],
            )
            pair_module._insert_counterfactual_preview(
                conn,
                position_id=saved_readiness["incumbent_position_id"],
                capital_quote=float(capital),
                preview=incumbent_preflight,
            )
            pair_module._open_paper_position_in_conn(
                conn,
                event_key=saved_readiness["challenger_event_key"],
                account_id=saved_readiness["account_id"],
                position_id=saved_readiness["challenger_position_id"],
                pool_address=saved_readiness["pool_address"],
                policy_source="ML_CHALLENGER",
                strategy=challenger_choice.strategy,
                min_bin_id=challenger_choice.min_bin_id,
                max_bin_id=challenger_choice.max_bin_id,
                capital=capital,
                cost=cost,
                model_id=saved_readiness["challenger_model_id"],
                event_time=saved_readiness["decision_observed_at"],
            )
            pair_module._insert_counterfactual_preview(
                conn,
                position_id=saved_readiness["challenger_position_id"],
                capital_quote=float(capital),
                preview=challenger_preflight,
            )

        incumbent_position = pair_module.paper_position_snapshot(
            storage,
            position_id=saved_readiness["incumbent_position_id"],
        ).to_record()
        challenger_position = pair_module.paper_position_snapshot(
            storage,
            position_id=saved_readiness["challenger_position_id"],
        ).to_record()
        account_after = pair_module.paper_account_snapshot(
            storage,
            account_id=saved_readiness["account_id"],
        ).to_record()
        after_state = base_account_module._database_state(database)

        if after_state == before_state:
            raise ValueError(
                "recursive re-entry cycle paired PAPER execution did not mutate database"
            )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    expected_debit = Decimal(
        str(saved_readiness["required_pair_cash_quote"])
    )
    actual_debit = Decimal(str(account_before["cash_quote"])) - Decimal(
        str(account_after["cash_quote"])
    )
    if actual_debit != expected_debit:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution cash debit is not exact"
        )
    if int(account_after["open_positions"]) != 2:
        raise ValueError(
            "recursive re-entry cycle paired PAPER execution did not create exactly two open positions"
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
        "recursive_rollover_reentry_cycle_entry_request_sha256": saved_readiness[
            "recursive_rollover_reentry_cycle_entry_request_sha256"
        ],
        "signed_authorization_verification_sha256": saved_readiness[
            "fresh_signed_authorization_verification_sha256"
        ],
        "recursive_rollover_reentry_cycle_input_verification_sha256": saved_readiness[
            "recursive_rollover_reentry_cycle_input_verification_sha256"
        ],
        "source_final_evaluation_sha256": saved_readiness[
            "source_final_evaluation_sha256"
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
        "previous_pair_id": saved_readiness["previous_pair_id"],
        "pair_id": saved_readiness["pair_id"],
        "pool_address": saved_readiness["pool_address"],
        "amount_x": saved_readiness["amount_x"],
        "amount_y": saved_readiness["amount_y"],
        "network_cost_y_atomic": saved_readiness[
            "network_cost_y_atomic"
        ],
        "capital_quote": saved_readiness["capital_quote"],
        "entry_cost_quote": saved_readiness["entry_cost_quote"],
        "required_pair_cash_quote": saved_readiness[
            "required_pair_cash_quote"
        ],
        "decision_observed_at": saved_readiness[
            "decision_observed_at"
        ],
        "incumbent_position_id": saved_readiness[
            "incumbent_position_id"
        ],
        "challenger_position_id": saved_readiness[
            "challenger_position_id"
        ],
        "incumbent_event_key": saved_readiness["incumbent_event_key"],
        "challenger_event_key": saved_readiness[
            "challenger_event_key"
        ],
        "candidate_frame_sha256": saved_readiness[
            "candidate_frame_sha256"
        ],
        "incumbent_inference_sha256": saved_readiness[
            "incumbent_inference_sha256"
        ],
        "challenger_inference_sha256": saved_readiness[
            "challenger_inference_sha256"
        ],
        "incumbent_choice_sha256": saved_readiness[
            "incumbent_choice_sha256"
        ],
        "challenger_choice_sha256": saved_readiness[
            "challenger_choice_sha256"
        ],
        "incumbent_position": incumbent_position,
        "challenger_position": challenger_position,
        "account_cash_quote_before": float(account_before["cash_quote"]),
        "account_cash_quote_after": float(account_after["cash_quote"]),
        "account_open_positions_before": int(
            account_before["open_positions"]
        ),
        "account_open_positions_after": int(
            account_after["open_positions"]
        ),
        "fresh_readiness_matches_saved": True,
        "human_recursive_reentry_cycle_paired_paper_entry_authorization_verified": True,
        "zero_open_positions_verified_at_commit": True,
        "same_candidate_frame_verified": True,
        "same_decision_snapshot_verified": True,
        "equal_capital_verified": True,
        "atomic_pair_open_verified": True,
        "counterfactual_preflights_reverified": True,
        "chain_bound_verified": True,
        "no_lookahead_verified": True,
        "one_shot_only": True,
        "paper_pair_entry_authorized": True,
        "paper_pair_entry_executed": True,
        "paper_pair_entry_completed": True,
        "requires_post_pair_audit": True,
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
    validate_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_execution_receipt(
        receipt
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one subsequent equal-capital ML_CHAMPION vs "
            "ML_CHALLENGER PAPER pair from signed repeat-entry readiness. "
            "The exact readiness choices are re-preflighted and both positions "
            "open atomically. This does not authorize future PAPER ticks, "
            "live transactions, or promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--final-evaluation", required=True)
    parser.add_argument("--recursive-rollover-reentry-cycle-inputs", required=True)
    parser.add_argument("--saved-account-readiness", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    receipt = execute_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_entry_once(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_execution_readiness_path=args.saved_readiness,
        final_evaluation_path=args.final_evaluation,
        recursive_rollover_reentry_cycle_input_path=args.recursive_rollover_reentry_cycle_inputs,
        saved_account_readiness_path=args.saved_account_readiness,
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
