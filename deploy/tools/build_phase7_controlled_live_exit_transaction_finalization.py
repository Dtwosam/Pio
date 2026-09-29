from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_TRANSACTION_FINALIZATION_V1"
)

PREPARATION_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_transaction_preparation.py"
)
READINESS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_transaction_preparation_readiness.py"
)
FINALIZER_SOURCE = Path(
    "rust-executor/src/bin/phase7-exit-finalizer.rs"
)
REVIEWED_SOURCE_BLOBS = {
    PREPARATION_TOOL: "aa612f6dd376ea9761aa481d43c36bde40ff00c9",
    READINESS_TOOL: "0e5ef3f98569e197303708430c61371f9cc16703",
    FINALIZER_SOURCE: "aded09c930f78cec8dc02f1d3853909e369d8b71",
}

DLMM_PROGRAM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
REMOVE_LIQUIDITY2_PREFIX = "e6d7527ff165e392"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"
MAX_CLOCK_SKEW_SECONDS = 30

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_preparation_sha256",
    "expected_preparation_sha256",
    "saved_readiness_sha256",
    "expected_readiness_sha256",
    "opened_decision_id",
    "opening_signature",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "user_token_x",
    "user_token_y",
    "preparation_transaction_sha256",
    "finalizer_binary_path",
    "finalizer_binary_sha256",
    "expected_finalizer_binary_sha256",
    "decision_expires_at",
    "authorization_expires_at",
    "finalized_at",
    "finalizer_report_sha256",
    "risk_report_sha256",
    "final_transaction_base64",
    "final_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "prepared_rpc_context_slot",
    "final_guard_sha256",
    "final_wallet_authorization_sha256",
    "exact_simulation_sha256",
    "exact_simulation_rpc_context_slot",
    "authorization_not_expired",
    "decision_not_expired",
    "final_risk_accepted",
    "final_transaction_guard_accepted",
    "final_guard_fee_payer_matches_executor",
    "final_guard_pool_account_present",
    "final_guard_required_accounts_present",
    "final_guard_single_instruction",
    "final_guard_single_signature",
    "final_transaction_unsigned",
    "expected_wallet_verified",
    "exact_simulation_succeeded",
    "exact_simulation_at_or_after_prepared_slot",
    "exit_transaction_finalized",
    "exact_exit_transaction_authorization_required",
    "fresh_presubmit_recheck_required",
    "fresh_blockhash_expiry_recheck_required",
    "separate_exit_execution_gate_required",
    "post_exit_confirmation_required",
    "post_exit_closure_proof_required",
    "post_exit_state_reconciliation_required",
    "exit_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_entry_authorized",
    "new_live_capital_authorized",
    "phase7_promotion_authorized",
    "phase7_promotion_persisted",
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


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


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


def _regular_executable(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if not (st.st_mode & stat.S_IXUSR):
        raise ValueError(f"{label} must be executable")
    return resolved


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT finalization dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT finalization dependency mismatch: {relative}"
            )
    preparation = _load_module(
        source / PREPARATION_TOOL,
        "phase7_exit_finalization_preparation",
    )
    readiness = _load_module(
        source / READINESS_TOOL,
        "phase7_exit_finalization_readiness",
    )
    return preparation, readiness


def _canonical_utc(raw: Any, *, label: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError(f"{label} must use UTC Z form")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            f"{label} must use YYYY-MM-DDTHH:MM:SSZ"
        ) from exc


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is invalid")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} is invalid")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    value = _nonnegative_int(value, label=label)
    if value <= 0:
        raise ValueError(f"{label} must be positive")
    return value


def _execution_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("RPC_URL", None)
    env["SOLANA_RPC_URL"] = rpc_url
    return env


def _run_finalizer(
    *,
    finalizer_binary: Path,
    request_path: Path,
    risk_config_path: Path,
    guard_config_path: Path,
    expected_wallet_pubkey: str,
    env: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(finalizer_binary),
            str(request_path),
            str(risk_config_path),
            str(guard_config_path),
            expected_wallet_pubkey,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=45,
    )
    if completed.returncode != 0:
        raise ValueError(
            "Phase 7 EXIT finalizer failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Phase 7 EXIT finalizer returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 7 EXIT finalizer returned invalid result"
        )
    return value


def validate_exit_transaction_finalization(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT transaction finalization must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"finalization_sha256"}:
        raise ValueError(
            "Phase 7 EXIT transaction finalization schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT transaction finalization format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT transaction finalization type"
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
            "Phase 7 EXIT transaction finalization lineage mismatch"
        )

    for field in (
        "saved_preparation_sha256",
        "expected_preparation_sha256",
        "saved_readiness_sha256",
        "expected_readiness_sha256",
        "rpc_endpoint_sha256",
        "preparation_transaction_sha256",
        "finalizer_binary_sha256",
        "expected_finalizer_binary_sha256",
        "finalizer_report_sha256",
        "risk_report_sha256",
        "final_transaction_sha256",
        "final_guard_sha256",
        "final_wallet_authorization_sha256",
        "exact_simulation_sha256",
        "finalization_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT transaction finalization {field} is invalid"
            )

    if (
        report["saved_preparation_sha256"]
        != report["expected_preparation_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT transaction finalization preparation digest mismatch"
        )
    if report["saved_readiness_sha256"] != report["expected_readiness_sha256"]:
        raise ValueError(
            "Phase 7 EXIT transaction finalization readiness digest mismatch"
        )
    if (
        report["finalizer_binary_sha256"]
        != report["expected_finalizer_binary_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT transaction finalizer trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "opening_signature",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "user_token_x",
        "user_token_y",
        "finalizer_binary_path",
        "decision_expires_at",
        "authorization_expires_at",
        "finalized_at",
        "final_transaction_base64",
        "recent_blockhash",
    ):
        _nonempty_string(
            report.get(field),
            label=f"Phase 7 EXIT transaction finalization {field}",
        )

    authorization_expires = _canonical_utc(
        report["authorization_expires_at"],
        label="Phase 7 EXIT authorization expires_at",
    )
    decision_expires = _canonical_utc(
        report["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    finalized = _canonical_utc(
        report["finalized_at"],
        label="Phase 7 EXIT transaction finalized_at",
    )
    if authorization_expires > decision_expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization outlives decision"
        )
    if finalized > authorization_expires:
        raise ValueError(
            "Phase 7 EXIT transaction finalization authorization has expired"
        )
    if finalized > decision_expires:
        raise ValueError(
            "Phase 7 EXIT transaction finalization decision has expired"
        )

    _positive_int(
        report.get("last_valid_block_height"),
        label="Phase 7 EXIT transaction last_valid_block_height",
    )
    prepared_slot = _nonnegative_int(
        report.get("prepared_rpc_context_slot"),
        label="Phase 7 EXIT transaction prepared_rpc_context_slot",
    )
    simulation_slot = _nonnegative_int(
        report.get("exact_simulation_rpc_context_slot"),
        label="Phase 7 EXIT transaction exact_simulation_rpc_context_slot",
    )
    if simulation_slot < prepared_slot:
        raise ValueError(
            "Phase 7 EXIT exact simulation predates prepared blockhash"
        )

    for field in (
        "authorization_not_expired",
        "decision_not_expired",
        "final_risk_accepted",
        "final_transaction_guard_accepted",
        "final_guard_fee_payer_matches_executor",
        "final_guard_pool_account_present",
        "final_guard_required_accounts_present",
        "final_guard_single_instruction",
        "final_guard_single_signature",
        "final_transaction_unsigned",
        "expected_wallet_verified",
        "exact_simulation_succeeded",
        "exact_simulation_at_or_after_prepared_slot",
        "exit_transaction_finalized",
        "exact_exit_transaction_authorization_required",
        "fresh_presubmit_recheck_required",
        "fresh_blockhash_expiry_recheck_required",
        "separate_exit_execution_gate_required",
        "post_exit_confirmation_required",
        "post_exit_closure_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT transaction finalization requires {field}=true"
            )

    for field in (
        "exit_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT transaction finalization requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["finalization_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT transaction finalization digest mismatch"
        )


def build_exit_transaction_finalization(
    *,
    source_tree: str | Path,
    saved_preparation_path: str | Path,
    expected_preparation_sha256: str,
    saved_readiness_path: str | Path,
    expected_readiness_sha256: str,
    finalizer_binary_path: str | Path,
    expected_finalizer_binary_sha256: str,
    rpc_url: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    preparation_module, readiness_module = _load_reviewed(source)

    preparation = _load_json(
        saved_preparation_path,
        label="saved Phase 7 EXIT transaction preparation",
    )
    preparation_module.validate_exit_transaction_preparation(preparation)
    if not _is_hex_digest(expected_preparation_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT transaction preparation digest is invalid"
        )
    if preparation["preparation_sha256"] != expected_preparation_sha256:
        raise ValueError(
            "saved Phase 7 EXIT transaction preparation digest mismatch"
        )

    readiness = _load_json(
        saved_readiness_path,
        label="saved Phase 7 EXIT transaction-preparation readiness",
    )
    readiness_module.validate_exit_transaction_preparation_readiness(
        readiness
    )
    if not _is_hex_digest(expected_readiness_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT transaction-preparation readiness digest is invalid"
        )
    if readiness["readiness_sha256"] != expected_readiness_sha256:
        raise ValueError(
            "saved Phase 7 EXIT transaction-preparation readiness digest mismatch"
        )
    if preparation["saved_readiness_sha256"] != readiness["readiness_sha256"]:
        raise ValueError(
            "Phase 7 EXIT preparation does not bind supplied readiness"
        )

    for prep_field, readiness_field in (
        ("opened_decision_id", "opened_decision_id"),
        ("opening_signature", "opening_signature"),
        ("exit_decision_id", "decision_record_id"),
        ("pool_address", "pool_address"),
        ("position_address", "position_address"),
        ("executor_wallet_pubkey", "executor_wallet_pubkey"),
        ("rpc_endpoint_sha256", "rpc_endpoint_sha256"),
    ):
        if preparation.get(prep_field) != readiness.get(readiness_field):
            raise ValueError(
                f"Phase 7 EXIT finalization {prep_field} binding mismatch"
            )

    if _sha256_text(rpc_url) != preparation["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT transaction finalization RPC endpoint mismatch"
        )

    issued = _canonical_utc(
        readiness["authorization_issued_at"],
        label="Phase 7 EXIT authorization issued_at",
    )
    authorization_expires = _canonical_utc(
        readiness["authorization_expires_at"],
        label="Phase 7 EXIT authorization expires_at",
    )
    decision_expires = _canonical_utc(
        readiness["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    now_dt = (
        _canonical_utc(
            now,
            label="Phase 7 EXIT transaction finalization now",
        )
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    if now_dt < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError("Phase 7 EXIT authorization is not yet valid")
    if now_dt > authorization_expires:
        raise ValueError("Phase 7 EXIT authorization has expired")
    if now_dt > decision_expires:
        raise ValueError("Phase 7 EXIT decision has expired")
    if authorization_expires > decision_expires:
        raise ValueError(
            "Phase 7 EXIT authorization outlives signed EXIT decision"
        )

    for artifact, label in (
        (preparation, "EXIT transaction preparation"),
        (readiness, "EXIT transaction-preparation readiness"),
    ):
        for field in (
            "exit_authorized",
            "controlled_live_authorized",
            "live_submit_authorized",
            "transaction_signing_authorized",
            "transaction_submission_authorized",
            "automatic_resubmission_authorized",
            "new_live_entry_authorized",
            "new_live_capital_authorized",
            "phase7_promotion_authorized",
            "phase7_promotion_persisted",
            "production_file_modified",
            "production_repository_git_mutated",
            "production_pio_database_modified",
        ):
            if artifact.get(field) is not False:
                raise ValueError(
                    f"{label} unexpectedly authorizes {field}"
                )

    finalizer = _regular_executable(
        finalizer_binary_path,
        label="Phase 7 EXIT finalizer binary",
    )
    finalizer_sha = _sha256_bytes(finalizer.read_bytes())
    if not _is_hex_digest(expected_finalizer_binary_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT finalizer binary SHA-256 is invalid"
        )
    if finalizer_sha != expected_finalizer_binary_sha256:
        raise ValueError(
            "Phase 7 EXIT finalizer binary differs from external trust root"
        )

    proposal = {
        "decision_id": preparation["exit_decision_id"],
        "mode": "LIVE",
        "action": "EXIT",
        "pool_address": preparation["pool_address"],
        "capital_quote": 0.0,
        "account_equity_quote": 0.0,
        "portfolio_deployed_quote": 0.0,
        "daily_drawdown_pct": 0.0,
        "min_bin_id": 0,
        "max_bin_id": 0,
        "strategy": "PHASE7_CONTROLLED_LIVE_EXIT",
        "expected_net_return_pct": 0.0,
        "expected_downside_pct": 0.0,
        "model_version": "human-authorized-exit-v1",
        "data_age_seconds": 0,
    }
    request = {
        "proposal": proposal,
        "transaction_base64": preparation["transaction_base64"],
    }
    risk_config = {
        "max_capital_per_position_pct": 1.0,
        "max_total_deployed_pct": 1.0,
        "max_daily_drawdown_pct": 0.0,
        "min_expected_edge_pct": 0.0,
        "max_expected_downside_pct": 0.0,
        "max_data_age_seconds": 1,
    }
    guard_config = {
        "expected_fee_payer": preparation["executor_wallet_pubkey"],
        "allowed_program_ids": [DLMM_PROGRAM_ID],
        "max_instructions": 1,
        "max_static_accounts": 64,
        "allow_address_lookup_tables": False,
        "require_unsigned": True,
        "require_proposal_pool_account": True,
        "required_account_pubkeys": [
            preparation["position_address"],
            preparation["user_token_x"],
            preparation["user_token_y"],
        ],
        "require_instruction_policy": True,
        "instruction_policies": [
            {
                "program_id": DLMM_PROGRAM_ID,
                "allowed_actions": ["EXIT"],
                "allowed_data_prefixes_hex": [
                    REMOVE_LIQUIDITY2_PREFIX
                ],
            }
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_path = root / "request.json"
        risk_path = root / "risk.json"
        guard_path = root / "guard.json"
        request_path.write_text(
            json.dumps(request, sort_keys=True),
            encoding="utf-8",
        )
        risk_path.write_text(
            json.dumps(risk_config, sort_keys=True),
            encoding="utf-8",
        )
        guard_path.write_text(
            json.dumps(guard_config, sort_keys=True),
            encoding="utf-8",
        )
        finalizer_report = _run_finalizer(
            finalizer_binary=finalizer,
            request_path=request_path,
            risk_config_path=risk_path,
            guard_config_path=guard_path,
            expected_wallet_pubkey=preparation["executor_wallet_pubkey"],
            env=_execution_env(rpc_url=rpc_url),
        )

    if finalizer_report.get("accepted") is not True:
        raise ValueError("Phase 7 EXIT finalizer did not accept transaction")
    if finalizer_report.get("stage") != "PRESIGN_READY":
        raise ValueError("Phase 7 EXIT finalizer stage is not PRESIGN_READY")

    risk = finalizer_report.get("risk")
    prepared = finalizer_report.get("prepared")
    transaction = finalizer_report.get("transaction")
    wallet = finalizer_report.get("wallet")
    simulation = finalizer_report.get("simulation")
    for label, value in (
        ("risk", risk),
        ("prepared transaction", prepared),
        ("transaction guard", transaction),
        ("wallet authorization", wallet),
        ("exact simulation", simulation),
    ):
        if not isinstance(value, dict):
            raise ValueError(
                f"Phase 7 EXIT finalizer missing {label}"
            )

    if risk.get("accepted") is not True:
        raise ValueError("Phase 7 EXIT final risk check failed")
    if risk.get("action") != "EXIT" or risk.get("mode") != "LIVE":
        raise ValueError("Phase 7 EXIT final risk identity mismatch")
    if risk.get("decision_id") != preparation["exit_decision_id"]:
        raise ValueError("Phase 7 EXIT final risk decision mismatch")

    final_transaction = _nonempty_string(
        prepared.get("transaction_base64"),
        label="Phase 7 EXIT finalized transaction_base64",
    )
    recent_blockhash = _nonempty_string(
        prepared.get("recent_blockhash"),
        label="Phase 7 EXIT finalized recent_blockhash",
    )
    last_valid_block_height = _positive_int(
        prepared.get("last_valid_block_height"),
        label="Phase 7 EXIT finalized last_valid_block_height",
    )
    prepared_slot = _nonnegative_int(
        prepared.get("rpc_context_slot"),
        label="Phase 7 EXIT finalized rpc_context_slot",
    )
    if prepared.get("signatures_all_default") is not True:
        raise ValueError(
            "Phase 7 EXIT finalized transaction unexpectedly contains signatures"
        )
    if final_transaction == preparation["transaction_base64"]:
        raise ValueError(
            "Phase 7 EXIT finalizer did not refresh transaction blockhash"
        )

    if transaction.get("accepted") is not True:
        raise ValueError("Phase 7 EXIT final transaction guard rejected")
    if (
        transaction.get("fee_payer")
        != preparation["executor_wallet_pubkey"]
    ):
        raise ValueError("Phase 7 EXIT final transaction fee payer mismatch")
    if transaction.get("pool_account_present") is not True:
        raise ValueError("Phase 7 EXIT final transaction lost pool binding")
    if transaction.get("required_accounts_present") is not True:
        raise ValueError(
            "Phase 7 EXIT final transaction lost required accounts"
        )
    if transaction.get("instruction_count") != 1:
        raise ValueError(
            "Phase 7 EXIT final transaction must contain one instruction"
        )
    if transaction.get("required_signatures") != 1:
        raise ValueError(
            "Phase 7 EXIT final transaction must require one signature"
        )
    if transaction.get("signatures_all_default") is not True:
        raise ValueError(
            "Phase 7 EXIT final transaction guard found signatures"
        )
    if transaction.get("address_lookup_table_count") != 0:
        raise ValueError(
            "Phase 7 EXIT final transaction uses address lookup tables"
        )
    if transaction.get("program_ids") != [DLMM_PROGRAM_ID]:
        raise ValueError(
            "Phase 7 EXIT final transaction program set mismatch"
        )
    fingerprints = transaction.get("instruction_fingerprints")
    if (
        not isinstance(fingerprints, list)
        or len(fingerprints) != 1
        or not isinstance(fingerprints[0], dict)
        or fingerprints[0].get("program_id") != DLMM_PROGRAM_ID
        or fingerprints[0].get("data_prefix_hex")
        != REMOVE_LIQUIDITY2_PREFIX
    ):
        raise ValueError(
            "Phase 7 EXIT final transaction instruction fingerprint mismatch"
        )

    if wallet.get("accepted") is not True:
        raise ValueError("Phase 7 EXIT final expected-wallet check failed")
    if wallet.get("wallet_pubkey") != preparation["executor_wallet_pubkey"]:
        raise ValueError("Phase 7 EXIT final expected-wallet identity mismatch")
    if (
        wallet.get("transaction_fee_payer")
        != preparation["executor_wallet_pubkey"]
    ):
        raise ValueError(
            "Phase 7 EXIT final wallet fee-payer identity mismatch"
        )

    if simulation.get("succeeded") is not True:
        raise ValueError("Phase 7 EXIT exact simulation failed")
    simulation_slot = _nonnegative_int(
        simulation.get("rpc_context_slot"),
        label="Phase 7 EXIT exact simulation rpc_context_slot",
    )
    if simulation_slot < prepared_slot:
        raise ValueError(
            "Phase 7 EXIT exact simulation predates prepared blockhash"
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
        "saved_preparation_sha256": preparation["preparation_sha256"],
        "expected_preparation_sha256": expected_preparation_sha256,
        "saved_readiness_sha256": readiness["readiness_sha256"],
        "expected_readiness_sha256": expected_readiness_sha256,
        "opened_decision_id": preparation["opened_decision_id"],
        "opening_signature": preparation["opening_signature"],
        "exit_decision_id": preparation["exit_decision_id"],
        "pool_address": preparation["pool_address"],
        "position_address": preparation["position_address"],
        "executor_wallet_pubkey": preparation["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": preparation["rpc_endpoint_sha256"],
        "user_token_x": preparation["user_token_x"],
        "user_token_y": preparation["user_token_y"],
        "preparation_transaction_sha256": preparation[
            "transaction_sha256"
        ],
        "finalizer_binary_path": str(finalizer),
        "finalizer_binary_sha256": finalizer_sha,
        "expected_finalizer_binary_sha256": (
            expected_finalizer_binary_sha256
        ),
        "decision_expires_at": readiness["decision_expires_at"],
        "authorization_expires_at": readiness[
            "authorization_expires_at"
        ],
        "finalized_at": _format_utc(now_dt),
        "finalizer_report_sha256": _sha256_value(finalizer_report),
        "risk_report_sha256": _sha256_value(risk),
        "final_transaction_base64": final_transaction,
        "final_transaction_sha256": _sha256_text(final_transaction),
        "recent_blockhash": recent_blockhash,
        "last_valid_block_height": last_valid_block_height,
        "prepared_rpc_context_slot": prepared_slot,
        "final_guard_sha256": _sha256_value(transaction),
        "final_wallet_authorization_sha256": _sha256_value(wallet),
        "exact_simulation_sha256": _sha256_value(simulation),
        "exact_simulation_rpc_context_slot": simulation_slot,
        "authorization_not_expired": True,
        "decision_not_expired": True,
        "final_risk_accepted": True,
        "final_transaction_guard_accepted": True,
        "final_guard_fee_payer_matches_executor": True,
        "final_guard_pool_account_present": True,
        "final_guard_required_accounts_present": True,
        "final_guard_single_instruction": True,
        "final_guard_single_signature": True,
        "final_transaction_unsigned": True,
        "expected_wallet_verified": True,
        "exact_simulation_succeeded": True,
        "exact_simulation_at_or_after_prepared_slot": True,
        "exit_transaction_finalized": True,
        "exact_exit_transaction_authorization_required": True,
        "fresh_presubmit_recheck_required": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "separate_exit_execution_gate_required": True,
        "post_exit_confirmation_required": True,
        "post_exit_closure_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exit_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "finalization_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_transaction_finalization(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh one prepared Phase 7 EXIT transaction with a current "
            "blockhash, re-run the exact unsigned transaction and expected-"
            "wallet guards, and exact-simulate the refreshed bytes through "
            "the dedicated non-keypair finalizer. No signing, submission, "
            "retry, execution-store mutation, or production mutation occurs."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-preparation", required=True)
    parser.add_argument("--expected-preparation-sha256", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--expected-readiness-sha256", required=True)
    parser.add_argument("--finalizer-binary", required=True)
    parser.add_argument("--expected-finalizer-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_exit_transaction_finalization(
        source_tree=args.source_tree,
        saved_preparation_path=args.saved_preparation,
        expected_preparation_sha256=args.expected_preparation_sha256,
        saved_readiness_path=args.saved_readiness,
        expected_readiness_sha256=args.expected_readiness_sha256,
        finalizer_binary_path=args.finalizer_binary,
        expected_finalizer_binary_sha256=(
            args.expected_finalizer_binary_sha256
        ),
        rpc_url=args.rpc_url,
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
