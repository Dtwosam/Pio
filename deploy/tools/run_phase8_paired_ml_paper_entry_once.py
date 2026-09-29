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
ARTIFACT_TYPE = "PHASE8_PAIRED_ML_PAPER_ENTRY_ONE_SHOT_EXECUTION_RECEIPT_V1"

READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_entry_execution_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "acfeb1eb595695751773b191e3cf9844074bb2b9",
}

LOCK_PATH = Path("/var/tmp/pio-phase8-paired-paper-entry-one-shot.lock")

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_readiness_sha256",
    "fresh_readiness_sha256",
    "paired_entry_request_sha256",
    "signed_authorization_verification_sha256",
    "paired_entry_input_verification_sha256",
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
    "human_paired_paper_entry_authorization_verified",
    "same_candidate_frame_verified",
    "same_decision_snapshot_verified",
    "equal_capital_verified",
    "atomic_pair_open_verified",
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


def _load_readiness_module(source: Path) -> Any:
    path = source / READINESS_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed paired PAPER execution readiness is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[READINESS_TOOL]:
        raise ValueError("reviewed paired PAPER execution readiness blob mismatch")
    return _load_module(
        path,
        "phase8_paired_paper_one_shot_readiness",
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


def _hash_record(value: Any) -> str:
    if not isinstance(value, dict):
        raise ValueError("paired PAPER execution record must be an object")
    return _sha256_bytes(_canonical_bytes(value))


def validate_phase8_paired_ml_paper_entry_execution_receipt(
    receipt: dict[str, Any],
) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("paired PAPER execution receipt must be an object")
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError("paired PAPER execution receipt schema mismatch")
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported paired PAPER execution receipt format")
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected paired PAPER execution receipt type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("paired PAPER execution receipt lineage mismatch")

    for field in (
        "saved_readiness_sha256",
        "fresh_readiness_sha256",
        "paired_entry_request_sha256",
        "signed_authorization_verification_sha256",
        "paired_entry_input_verification_sha256",
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
                f"paired PAPER execution receipt {field} is invalid"
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
                f"paired PAPER execution receipt {field} is invalid"
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
        "decision_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "incumbent_event_key",
        "challenger_event_key",
        "approver_principal",
        "approval_id",
        "authorization_expires_at",
    ):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(
                f"paired PAPER execution receipt {field} is invalid"
            )

    for field in ("incumbent_position", "challenger_position"):
        if not isinstance(receipt.get(field), dict):
            raise ValueError(
                f"paired PAPER execution receipt {field} is invalid"
            )

    for field in (
        "fresh_readiness_matches_saved",
        "human_paired_paper_entry_authorization_verified",
        "same_candidate_frame_verified",
        "same_decision_snapshot_verified",
        "equal_capital_verified",
        "atomic_pair_open_verified",
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
                f"paired PAPER execution receipt requires {field}=true"
            )

    for field in (
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_performed",
        "new_live_capital_used",
        "phase8_promotion_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
    ):
        if receipt.get(field) is not False:
            raise ValueError(
                f"paired PAPER execution receipt requires {field}=false"
            )

    if receipt["saved_readiness_sha256"] != receipt["fresh_readiness_sha256"]:
        raise ValueError("paired PAPER execution readiness digest mismatch")
    if receipt["incumbent_model_id"] == receipt["challenger_model_id"]:
        raise ValueError("paired PAPER execution model identities must differ")
    if receipt["incumbent_position_id"] == receipt["challenger_position_id"]:
        raise ValueError("paired PAPER execution position ids must differ")

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
        raise ValueError("paired PAPER execution pair cash binding mismatch")
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
        raise ValueError("paired PAPER execution account cash delta mismatch")
    if (
        int(receipt["account_open_positions_after"])
        != int(receipt["account_open_positions_before"]) + 2
    ):
        raise ValueError("paired PAPER execution open-position delta mismatch")

    if (
        receipt["pio_database_sha256_before"]
        == receipt["pio_database_sha256_after"]
        and receipt["pio_wal_sha256_before"]
        == receipt["pio_wal_sha256_after"]
        and receipt["pio_shm_sha256_before"]
        == receipt["pio_shm_sha256_after"]
    ):
        raise ValueError("paired PAPER execution receipt shows no DB mutation")

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    if receipt["receipt_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError("paired PAPER execution receipt digest mismatch")


def execute_phase8_paired_ml_paper_entry_once(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_readiness_path: str | Path,
    saved_account_readiness_path: str | Path,
    post_audit_path: str | Path,
    paper_evidence_input_path: str | Path,
    paired_entry_input_path: str | Path,
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

    readiness_module = _load_readiness_module(source)
    saved_readiness = _load_json(
        saved_readiness_path,
        label="saved paired PAPER execution readiness",
    )
    saved_account = _load_json(
        saved_account_readiness_path,
        label="saved paired PAPER account readiness",
    )
    readiness_module.validate_phase8_paired_ml_paper_entry_execution_readiness(
        saved_readiness
    )
    if saved_readiness.get(
        "paper_pair_entry_execution_readiness_ready"
    ) is not True:
        raise ValueError("saved paired PAPER readiness is not ready")
    if saved_readiness.get("readiness_only") is not True:
        raise ValueError("saved paired PAPER artifact is not readiness-only")
    if saved_readiness.get(
        "requires_immediate_one_shot_pair_executor"
    ) is not True:
        raise ValueError("saved paired PAPER readiness lacks executor boundary")

    account_module, _, _, pair_module = readiness_module._load_reviewed(source)
    database = account_module._production_database(production)
    if Path(str(saved_readiness["pio_database_path"])).resolve() != database:
        raise ValueError("paired PAPER readiness database mismatch")

    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    lock_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(LOCK_PATH, lock_flags, 0o600)
    except OSError as exc:
        raise ValueError("paired PAPER executor lock path is unsafe") from exc
    if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
        os.close(lock_fd)
        raise ValueError("paired PAPER executor lock must be a regular file")

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "another paired PAPER entry executor is active"
            ) from exc

        fresh_readiness = (
            readiness_module.build_phase8_paired_ml_paper_entry_execution_readiness(
                repository=production,
                source_tree=source,
                saved_account_readiness_path=saved_account_readiness_path,
                post_audit_path=post_audit_path,
                paper_evidence_input_path=paper_evidence_input_path,
                paired_entry_input_path=paired_entry_input_path,
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
        readiness_module.validate_phase8_paired_ml_paper_entry_execution_readiness(
            fresh_readiness
        )
        if fresh_readiness != saved_readiness:
            raise ValueError(
                "fresh paired PAPER execution readiness differs from saved"
            )

        before_state = account_module._database_state(database)
        expected_before = {
            "database": saved_readiness["pio_database_sha256"],
            "wal": saved_readiness["pio_wal_sha256"],
            "shm": saved_readiness["pio_shm_sha256"],
        }
        if before_state != expected_before:
            raise ValueError("Pio database changed after paired PAPER readiness")

        account_cash_before = float(saved_account["account_cash_quote"])
        account_open_before = int(saved_account["account_open_positions"])

        inference_config = pair_module.MLInferenceConfig(
            risk_lambda=float(saved_readiness["risk_lambda"]),
            min_positive_excess_probability=float(
                saved_readiness["min_positive_excess_probability"]
            ),
            min_range_survival_probability=float(
                saved_readiness["min_range_survival_probability"]
            ),
            min_score_bps=float(saved_readiness["min_score_bps"]),
        )
        result = pair_module.open_paired_ml_paper_entries(
            pair_module.Storage(database),
            account_id=saved_readiness["account_id"],
            cycle_id=saved_readiness["active_cycle_id"],
            pool_address=saved_readiness["pool_address"],
            amount_x=int(saved_readiness["amount_x"]),
            amount_y=int(saved_readiness["amount_y"]),
            network_cost_y_atomic=int(
                saved_readiness["network_cost_y_atomic"]
            ),
            capital_quote=float(saved_readiness["capital_quote"]),
            incumbent_position_id=saved_readiness[
                "incumbent_position_id"
            ],
            challenger_position_id=saved_readiness[
                "challenger_position_id"
            ],
            incumbent_event_key=saved_readiness[
                "incumbent_event_key"
            ],
            challenger_event_key=saved_readiness[
                "challenger_event_key"
            ],
            entry_cost_quote=float(saved_readiness["entry_cost_quote"]),
            lookback_observations=int(
                saved_readiness["lookback_observations"]
            ),
            half_widths=tuple(
                int(x) for x in saved_readiness["half_widths"]
            ),
            center_offsets=tuple(
                int(x) for x in saved_readiness["center_offsets"]
            ),
            strategies=tuple(
                pair_module.StrategyType(value)
                for value in saved_readiness["strategies"]
            ),
            max_share_bps=int(saved_readiness["max_share_bps"]),
            favor_x_in_active_bin=bool(
                saved_readiness["favor_x_in_active_bin"]
            ),
            near_liquidity_radius=int(
                saved_readiness["near_liquidity_radius"]
            ),
            inference_config=inference_config,
            as_of=saved_readiness["decision_observed_at"],
        )
        record = result.to_record()

        if record.get("paper_only") is not True:
            raise ValueError("paired PAPER executor result is not paper-only")
        if record.get("policy_actionable") is not False:
            raise ValueError("paired PAPER executor became policy-actionable")
        if record.get("live_authorized") is not False:
            raise ValueError("paired PAPER executor authorized live capital")
        for field in (
            "same_candidate_frame",
            "same_decision_snapshot",
            "equal_capital",
            "atomic_pair_open",
            "chain_bound",
            "no_lookahead",
        ):
            if record.get(field) is not True:
                raise ValueError(
                    f"paired PAPER executor result requires {field}=true"
                )

        if record["decision_observed_at"] != saved_readiness[
            "decision_observed_at"
        ]:
            raise ValueError("paired PAPER execution decision snapshot drifted")
        if record["incumbent_model_id"] != saved_readiness[
            "incumbent_model_id"
        ]:
            raise ValueError("paired PAPER incumbent model drifted")
        if record["challenger_model_id"] != saved_readiness[
            "challenger_model_id"
        ]:
            raise ValueError("paired PAPER challenger model drifted")

        hash_pairs = (
            (
                "candidate_frame",
                "candidate_frame_sha256",
            ),
            (
                "incumbent_inference",
                "incumbent_inference_sha256",
            ),
            (
                "challenger_inference",
                "challenger_inference_sha256",
            ),
            (
                "incumbent_choice",
                "incumbent_choice_sha256",
            ),
            (
                "challenger_choice",
                "challenger_choice_sha256",
            ),
        )
        for result_field, readiness_field in hash_pairs:
            if _hash_record(record[result_field]) != saved_readiness[
                readiness_field
            ]:
                raise ValueError(
                    f"paired PAPER execution {result_field} drifted"
                )

        after_state = account_module._database_state(database)
        if after_state == before_state:
            raise ValueError("paired PAPER execution did not mutate database")
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    account_after = record["account"]
    account_cash_after = float(account_after["cash_quote"])
    account_open_after = int(account_after["open_positions"])
    expected_debit = Decimal(str(saved_readiness["required_pair_cash_quote"]))
    actual_debit = Decimal(str(account_cash_before)) - Decimal(
        str(account_cash_after)
    )
    if actual_debit != expected_debit:
        raise ValueError("paired PAPER execution cash debit is not exact")
    if account_open_after != account_open_before + 2:
        raise ValueError("paired PAPER execution did not add exactly two positions")

    signed_verification = _load_json(
        saved_signed_authorization_verification_path,
        label="paired PAPER signed authorization verification",
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
        "paired_entry_request_sha256": saved_readiness[
            "paired_entry_request_sha256"
        ],
        "signed_authorization_verification_sha256": saved_readiness[
            "fresh_signed_authorization_verification_sha256"
        ],
        "paired_entry_input_verification_sha256": saved_readiness[
            "paired_entry_input_verification_sha256"
        ],
        "approval_payload_sha256": signed_verification[
            "approval_payload_sha256"
        ],
        "approval_signature_sha256": signed_verification[
            "approval_signature_sha256"
        ],
        "allowed_signers_sha256": signed_verification[
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
        "incumbent_position": record["incumbent_position"],
        "challenger_position": record["challenger_position"],
        "account_cash_quote_before": account_cash_before,
        "account_cash_quote_after": account_cash_after,
        "account_open_positions_before": account_open_before,
        "account_open_positions_after": account_open_after,
        "fresh_readiness_matches_saved": True,
        "human_paired_paper_entry_authorization_verified": True,
        "same_candidate_frame_verified": True,
        "same_decision_snapshot_verified": True,
        "equal_capital_verified": True,
        "atomic_pair_open_verified": True,
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
        "phase8_promotion_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": True,
    }
    receipt = {
        **identity,
        "receipt_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_entry_execution_receipt(receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one signed equal-capital ML_CHAMPION vs "
            "ML_CHALLENGER PAPER pair after fresh readiness. The pair opens "
            "atomically. This does not authorize the PAPER scheduler, future "
            "paper observations, live transactions, or Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--saved-account-readiness", required=True)
    parser.add_argument("--post-audit", required=True)
    parser.add_argument("--paper-evidence-input", required=True)
    parser.add_argument("--paired-entry-input", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    receipt = execute_phase8_paired_ml_paper_entry_once(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_readiness_path=args.saved_readiness,
        saved_account_readiness_path=args.saved_account_readiness,
        post_audit_path=args.post_audit,
        paper_evidence_input_path=args.paper_evidence_input,
        paired_entry_input_path=args.paired_entry_input,
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
