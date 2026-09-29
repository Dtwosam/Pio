from __future__ import annotations

import argparse
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
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_FINALIZATION_V1"

PREPARATION_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_settlement_preparation.py"
)
FINALIZER_SOURCE = Path(
    "rust-executor/src/bin/phase7-exit-finalizer.rs"
)
RUST_BLOCKHASH = Path("rust-executor/src/blockhash.rs")
RUST_PRESIGN = Path("rust-executor/src/presign.rs")
RUST_SIMULATION = Path("rust-executor/src/simulation.rs")
RUST_TRANSACTION_GUARD = Path("rust-executor/src/transaction_guard.rs")
RUST_WALLET_GUARD = Path("rust-executor/src/wallet_guard.rs")
REVIEWED_SOURCE_BLOBS = {
    PREPARATION_TOOL: "7cde66709653480989e9a2cb61b0ea2ecbf93a2e",
    FINALIZER_SOURCE: "aded09c930f78cec8dc02f1d3853909e369d8b71",
    RUST_BLOCKHASH: "792153a0bb1527b16d7f7fabf40fed3752225df4",
    RUST_PRESIGN: "7353bf6512947bc207a416c8a365d6a21038dc40",
    RUST_SIMULATION: "d3224a83b463962b21d8671649755afaad70870b",
    RUST_TRANSACTION_GUARD: "ca8189735003d4e2f2f97e4c498c4d1d67c0f878",
    RUST_WALLET_GUARD: "4f3b8c061263a5937a216357130d9fb5bbf5bfb2",
}

DLMM_PROGRAM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
CLAIM_FEE2_PREFIX = "70bf65ab1c907fbb"
CLAIM_REWARD2_PREFIX = "be037f77b2579db7"
CLOSE_POSITION2_PREFIX = "ae5a2373ba2893e2"

KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_preparation_sha256",
    "expected_preparation_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "exit_signature",
    "zero_liquidity_snapshot_sha256",
    "zero_liquidity_capture_slot_start",
    "zero_liquidity_capture_slot_end",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "destination_config_sha256",
    "user_token_x",
    "user_token_y",
    "reward_token_destinations",
    "prepared_settlement_transaction_sha256",
    "finalizer_binary_path",
    "finalizer_binary_sha256",
    "expected_finalizer_binary_sha256",
    "finalizer_report_sha256",
    "risk_report_sha256",
    "final_settlement_transaction_base64",
    "final_settlement_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "prepared_rpc_context_slot",
    "final_guard_sha256",
    "final_wallet_authorization_sha256",
    "exact_simulation_sha256",
    "exact_simulation_rpc_context_slot",
    "final_risk_accepted",
    "final_transaction_guard_accepted",
    "final_guard_fee_payer_matches_executor",
    "final_guard_pool_account_present",
    "final_guard_required_accounts_present",
    "final_guard_instruction_sequence_valid",
    "final_guard_single_signature",
    "final_transaction_unsigned",
    "expected_wallet_verified",
    "exact_simulation_succeeded",
    "exact_simulation_at_or_after_prepared_slot",
    "settlement_transaction_finalized",
    "exact_settlement_transaction_authorization_required",
    "fresh_blockhash_expiry_recheck_required",
    "single_submission_only_required",
    "post_settlement_confirmation_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "settlement_authorized",
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
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if not (st.st_mode & stat.S_IXUSR):
        raise ValueError(f"{label} must be executable")
    return resolved


def _load_preparation_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT settlement finalization dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT settlement finalization dependency mismatch: {relative}"
            )
    return _load_module(
        source / PREPARATION_TOOL,
        "phase7_exit_settlement_finalization_preparation",
    )


def _execution_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("RPC_URL", None)
    env["SOLANA_RPC_URL"] = rpc_url
    return env


def _run_finalizer(
    *,
    binary: Path,
    request_path: Path,
    risk_path: Path,
    guard_path: Path,
    expected_wallet: str,
    env: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(binary),
            str(request_path),
            str(risk_path),
            str(guard_path),
            expected_wallet,
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
            "Phase 7 EXIT settlement finalizer failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Phase 7 EXIT settlement finalizer returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 7 EXIT settlement finalizer returned invalid result"
        )
    return value


def _expected_prefixes(reward_indices: list[int]) -> list[str]:
    return [
        CLAIM_FEE2_PREFIX,
        *[CLAIM_REWARD2_PREFIX for _ in reward_indices],
        CLOSE_POSITION2_PREFIX,
    ]


def validate_exit_settlement_finalization(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT settlement finalization must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"finalization_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement finalization schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement finalization format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement finalization type"
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
            "Phase 7 EXIT settlement finalization lineage mismatch"
        )

    for field in (
        "saved_preparation_sha256",
        "expected_preparation_sha256",
        "rpc_endpoint_sha256",
        "destination_config_sha256",
        "zero_liquidity_snapshot_sha256",
        "prepared_settlement_transaction_sha256",
        "finalizer_binary_sha256",
        "expected_finalizer_binary_sha256",
        "finalizer_report_sha256",
        "risk_report_sha256",
        "final_settlement_transaction_sha256",
        "final_guard_sha256",
        "final_wallet_authorization_sha256",
        "exact_simulation_sha256",
        "finalization_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement finalization {field} is invalid"
            )

    if report["saved_preparation_sha256"] != report[
        "expected_preparation_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement finalization preparation digest mismatch"
        )
    if report["finalizer_binary_sha256"] != report[
        "expected_finalizer_binary_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement finalization binary trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "exit_signature",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "user_token_x",
        "user_token_y",
        "finalizer_binary_path",
        "final_settlement_transaction_base64",
        "recent_blockhash",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT settlement finalization {field} is invalid"
            )

    for field in (
        "last_valid_block_height",
        "zero_liquidity_capture_slot_start",
        "zero_liquidity_capture_slot_end",
        "prepared_rpc_context_slot",
        "exact_simulation_rpc_context_slot",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT settlement finalization {field} is invalid"
            )
    if report["last_valid_block_height"] <= 0:
        raise ValueError(
            "Phase 7 EXIT settlement finalization block-height ceiling invalid"
        )
    if report["zero_liquidity_capture_slot_end"] < report["zero_liquidity_capture_slot_start"]:
        raise ValueError(
            "Phase 7 EXIT settlement finalization zero-liquidity slot range invalid"
        )
    if report["prepared_rpc_context_slot"] < report["zero_liquidity_capture_slot_start"]:
        raise ValueError(
            "Phase 7 EXIT settlement finalization blockhash refresh predates zero-liquidity snapshot"
        )
    if (
        report["exact_simulation_rpc_context_slot"]
        < report["prepared_rpc_context_slot"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement exact simulation predates prepared blockhash"
        )

    if not isinstance(report.get("reward_token_destinations"), list):
        raise ValueError(
            "Phase 7 EXIT settlement finalization reward destinations invalid"
        )
    normalized_destination_config = {
        "user_token_x": report["user_token_x"],
        "user_token_y": report["user_token_y"],
        "reward_token_destinations": report["reward_token_destinations"],
    }
    if report["destination_config_sha256"] != _sha256_value(
        normalized_destination_config
    ):
        raise ValueError(
            "Phase 7 EXIT settlement finalization destination-config digest mismatch"
        )
    if report["final_settlement_transaction_sha256"] != _sha256_text(
        report["final_settlement_transaction_base64"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement finalization transaction digest mismatch"
        )

    for field in (
        "final_risk_accepted",
        "final_transaction_guard_accepted",
        "final_guard_fee_payer_matches_executor",
        "final_guard_pool_account_present",
        "final_guard_required_accounts_present",
        "final_guard_instruction_sequence_valid",
        "final_guard_single_signature",
        "final_transaction_unsigned",
        "expected_wallet_verified",
        "exact_simulation_succeeded",
        "exact_simulation_at_or_after_prepared_slot",
        "settlement_transaction_finalized",
        "exact_settlement_transaction_authorization_required",
        "fresh_blockhash_expiry_recheck_required",
        "single_submission_only_required",
        "post_settlement_confirmation_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement finalization requires {field}=true"
            )

    for field in (
        "settlement_authorized",
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
                f"Phase 7 EXIT settlement finalization requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["finalization_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT settlement finalization digest mismatch"
        )


def build_exit_settlement_finalization(
    *,
    source_tree: str | Path,
    saved_preparation_path: str | Path,
    expected_preparation_sha256: str,
    finalizer_binary_path: str | Path,
    expected_finalizer_binary_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    preparation_module = _load_preparation_module(source)

    preparation = _load_json(
        saved_preparation_path,
        label="saved Phase 7 EXIT settlement preparation",
    )
    preparation_module.validate_exit_settlement_preparation(preparation)
    if not _is_hex_digest(expected_preparation_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT settlement preparation digest is invalid"
        )
    if preparation["preparation_sha256"] != expected_preparation_sha256:
        raise ValueError(
            "saved Phase 7 EXIT settlement preparation digest mismatch"
        )

    for field in (
        "settlement_prepared",
        "fresh_blockhash_exact_finalization_required",
        "exact_settlement_transaction_authorization_required",
        "single_submission_only_required",
        "post_settlement_confirmation_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if preparation.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement finalization requires preparation {field}=true"
            )
    for field in (
        "settlement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_pio_database_modified",
    ):
        if preparation.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement finalization refuses preparation {field}=true"
            )

    if _sha256_text(rpc_url) != preparation["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement finalization RPC endpoint mismatch"
        )

    finalizer = _regular_executable(
        finalizer_binary_path,
        label="Phase 7 EXIT non-keypair finalizer",
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

    reward_destinations = preparation["reward_token_destinations"]
    required_accounts = [
        preparation["position_address"],
        preparation["user_token_x"],
        preparation["user_token_y"],
        *[
            item["user_token_account"]
            for item in reward_destinations
        ],
    ]
    reward_indices = preparation["reward_indices_claimed"]
    guard_config = {
        "expected_fee_payer": preparation["executor_wallet_pubkey"],
        "allowed_program_ids": [DLMM_PROGRAM_ID],
        "max_instructions": 4,
        "max_static_accounts": 96,
        "allow_address_lookup_tables": False,
        "require_unsigned": True,
        "require_proposal_pool_account": True,
        "required_account_pubkeys": required_accounts,
        "require_instruction_policy": True,
        "instruction_policies": [
            {
                "program_id": DLMM_PROGRAM_ID,
                "allowed_actions": ["EXIT"],
                "allowed_data_prefixes_hex": [
                    CLAIM_FEE2_PREFIX,
                    CLAIM_REWARD2_PREFIX,
                    CLOSE_POSITION2_PREFIX,
                ],
            }
        ],
    }
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
        "strategy": "PHASE7_EXIT_SETTLEMENT_FINALIZATION",
        "expected_net_return_pct": 0.0,
        "expected_downside_pct": 0.0,
        "model_version": "human-authorized-exit-settlement-v1",
        "data_age_seconds": 0,
    }
    request = {
        "proposal": proposal,
        "transaction_base64": preparation[
            "settlement_transaction_base64"
        ],
    }
    risk_config = {
        "max_capital_per_position_pct": 1.0,
        "max_total_deployed_pct": 1.0,
        "max_daily_drawdown_pct": 0.0,
        "min_expected_edge_pct": 0.0,
        "max_expected_downside_pct": 0.0,
        "max_data_age_seconds": 1,
    }

    with tempfile.TemporaryDirectory(prefix="pio-phase7-settlement-final-") as tmp:
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
            binary=finalizer,
            request_path=request_path,
            risk_path=risk_path,
            guard_path=guard_path,
            expected_wallet=preparation["executor_wallet_pubkey"],
            env=_execution_env(rpc_url=rpc_url),
        )

    if finalizer_report.get("accepted") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement finalizer did not accept transaction"
        )
    if finalizer_report.get("stage") != "PRESIGN_READY":
        raise ValueError(
            "Phase 7 EXIT settlement finalizer stage is not PRESIGN_READY"
        )

    risk = finalizer_report.get("risk")
    prepared = finalizer_report.get("prepared")
    guard = finalizer_report.get("transaction")
    wallet = finalizer_report.get("wallet")
    simulation = finalizer_report.get("simulation")
    for label, value in (
        ("risk", risk),
        ("prepared transaction", prepared),
        ("transaction guard", guard),
        ("wallet authorization", wallet),
        ("exact simulation", simulation),
    ):
        if not isinstance(value, dict):
            raise ValueError(
                f"Phase 7 EXIT settlement finalizer missing {label}"
            )

    if (
        risk.get("accepted") is not True
        or risk.get("action") != "EXIT"
        or risk.get("mode") != "LIVE"
        or risk.get("decision_id") != preparation["exit_decision_id"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement final risk binding mismatch"
        )

    final_transaction = prepared.get("transaction_base64")
    if not isinstance(final_transaction, str) or not final_transaction:
        raise ValueError(
            "Phase 7 EXIT finalized settlement transaction is invalid"
        )
    if final_transaction == preparation["settlement_transaction_base64"]:
        raise ValueError(
            "Phase 7 EXIT settlement finalizer did not refresh blockhash"
        )
    if prepared.get("signatures_all_default") is not True:
        raise ValueError(
            "Phase 7 EXIT finalized settlement unexpectedly contains signatures"
        )
    blockhash = prepared.get("recent_blockhash")
    if not isinstance(blockhash, str) or not blockhash:
        raise ValueError(
            "Phase 7 EXIT settlement final blockhash is invalid"
        )
    last_valid = prepared.get("last_valid_block_height")
    prepared_slot = prepared.get("rpc_context_slot")
    if (
        not isinstance(last_valid, int)
        or isinstance(last_valid, bool)
        or last_valid <= 0
    ):
        raise ValueError(
            "Phase 7 EXIT settlement last-valid block height is invalid"
        )
    if (
        not isinstance(prepared_slot, int)
        or isinstance(prepared_slot, bool)
        or prepared_slot < 0
    ):
        raise ValueError(
            "Phase 7 EXIT settlement prepared slot is invalid"
        )

    if guard.get("accepted") is not True:
        raise ValueError(
            "Phase 7 EXIT final settlement transaction guard rejected"
        )
    if guard.get("fee_payer") != preparation["executor_wallet_pubkey"]:
        raise ValueError(
            "Phase 7 EXIT final settlement fee payer mismatch"
        )
    if guard.get("pool_account_present") is not True:
        raise ValueError(
            "Phase 7 EXIT final settlement lost pool binding"
        )
    if guard.get("required_accounts_present") is not True:
        raise ValueError(
            "Phase 7 EXIT final settlement lost required accounts"
        )
    if guard.get("required_signatures") != 1:
        raise ValueError(
            "Phase 7 EXIT final settlement must require one signature"
        )
    if guard.get("signatures_all_default") is not True:
        raise ValueError(
            "Phase 7 EXIT final settlement guard found signatures"
        )
    if guard.get("address_lookup_table_count") != 0:
        raise ValueError(
            "Phase 7 EXIT final settlement uses lookup tables"
        )
    if guard.get("program_ids") != [DLMM_PROGRAM_ID]:
        raise ValueError(
            "Phase 7 EXIT final settlement program set mismatch"
        )
    fingerprints = guard.get("instruction_fingerprints")
    expected_prefixes = _expected_prefixes(reward_indices)
    if (
        not isinstance(fingerprints, list)
        or len(fingerprints) != len(expected_prefixes)
        or [
            item.get("data_prefix_hex")
            for item in fingerprints
            if isinstance(item, dict)
        ] != expected_prefixes
        or any(
            not isinstance(item, dict)
            or item.get("program_id") != DLMM_PROGRAM_ID
            for item in fingerprints
        )
    ):
        raise ValueError(
            "Phase 7 EXIT final settlement instruction sequence mismatch"
        )

    if wallet.get("accepted") is not True:
        raise ValueError(
            "Phase 7 EXIT final settlement expected-wallet check failed"
        )
    if wallet.get("wallet_pubkey") != preparation["executor_wallet_pubkey"]:
        raise ValueError(
            "Phase 7 EXIT final settlement wallet identity mismatch"
        )
    if wallet.get("transaction_fee_payer") != preparation[
        "executor_wallet_pubkey"
    ]:
        raise ValueError(
            "Phase 7 EXIT final settlement wallet/fee-payer mismatch"
        )

    if simulation.get("succeeded") is not True:
        raise ValueError(
            "Phase 7 EXIT final settlement exact simulation failed"
        )
    simulation_slot = simulation.get("rpc_context_slot")
    if (
        not isinstance(simulation_slot, int)
        or isinstance(simulation_slot, bool)
        or simulation_slot < prepared_slot
    ):
        raise ValueError(
            "Phase 7 EXIT final settlement simulation slot is invalid"
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
        "opened_decision_id": preparation["opened_decision_id"],
        "exit_decision_id": preparation["exit_decision_id"],
        "exit_signature": preparation["exit_signature"],
        "zero_liquidity_snapshot_sha256": preparation["zero_liquidity_snapshot_sha256"],
        "zero_liquidity_capture_slot_start": preparation["zero_liquidity_capture_slot_start"],
        "zero_liquidity_capture_slot_end": preparation["zero_liquidity_capture_slot_end"],
        "pool_address": preparation["pool_address"],
        "position_address": preparation["position_address"],
        "executor_wallet_pubkey": preparation["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": preparation["rpc_endpoint_sha256"],
        "destination_config_sha256": preparation[
            "destination_config_sha256"
        ],
        "user_token_x": preparation["user_token_x"],
        "user_token_y": preparation["user_token_y"],
        "reward_token_destinations": reward_destinations,
        "prepared_settlement_transaction_sha256": preparation[
            "settlement_transaction_sha256"
        ],
        "finalizer_binary_path": str(finalizer),
        "finalizer_binary_sha256": finalizer_sha,
        "expected_finalizer_binary_sha256": (
            expected_finalizer_binary_sha256
        ),
        "finalizer_report_sha256": _sha256_value(finalizer_report),
        "risk_report_sha256": _sha256_value(risk),
        "final_settlement_transaction_base64": final_transaction,
        "final_settlement_transaction_sha256": _sha256_text(
            final_transaction
        ),
        "recent_blockhash": blockhash,
        "last_valid_block_height": last_valid,
        "prepared_rpc_context_slot": prepared_slot,
        "final_guard_sha256": _sha256_value(guard),
        "final_wallet_authorization_sha256": _sha256_value(wallet),
        "exact_simulation_sha256": _sha256_value(simulation),
        "exact_simulation_rpc_context_slot": simulation_slot,
        "final_risk_accepted": True,
        "final_transaction_guard_accepted": True,
        "final_guard_fee_payer_matches_executor": True,
        "final_guard_pool_account_present": True,
        "final_guard_required_accounts_present": True,
        "final_guard_instruction_sequence_valid": True,
        "final_guard_single_signature": True,
        "final_transaction_unsigned": True,
        "expected_wallet_verified": True,
        "exact_simulation_succeeded": True,
        "exact_simulation_at_or_after_prepared_slot": True,
        "settlement_transaction_finalized": True,
        "exact_settlement_transaction_authorization_required": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "single_submission_only_required": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "settlement_authorized": False,
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
    validate_exit_settlement_finalization(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh one prepared Phase 7 EXIT settlement transaction with a "
            "current blockhash, rerun its exact guard and expected-wallet "
            "checks, and exact-simulate the refreshed unsigned bytes through "
            "the non-keypair finalizer. No signing or submission occurs."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-preparation", required=True)
    parser.add_argument("--expected-preparation-sha256", required=True)
    parser.add_argument("--finalizer-binary", required=True)
    parser.add_argument("--expected-finalizer-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = build_exit_settlement_finalization(
        source_tree=args.source_tree,
        saved_preparation_path=args.saved_preparation,
        expected_preparation_sha256=args.expected_preparation_sha256,
        finalizer_binary_path=args.finalizer_binary,
        expected_finalizer_binary_sha256=(
            args.expected_finalizer_binary_sha256
        ),
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
