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
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_TRANSACTION_EXECUTION_ONCE_V1"

ADMISSION_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_transaction_execution_admission.py"
)
PRESUBMIT_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_presubmit_evidence.py"
)
RUST_WALLET = Path("rust-executor/src/wallet.rs")
RUST_PHASE5_GATE = Path("rust-executor/src/phase5_gate.rs")
RUST_PHASE6_READINESS = Path("rust-executor/src/phase6_readiness.rs")
RUST_PHASE6_GATE = Path("rust-executor/src/phase6_gate.rs")
RUST_CONTROLLED_LIVE = Path("rust-executor/src/controlled_live.rs")
RUST_SIGNER = Path("rust-executor/src/signer.rs")
RUST_SUBMISSION = Path("rust-executor/src/submission.rs")
RUST_EXECUTION_STORE = Path("rust-executor/src/execution_store.rs")
RUST_CONFIRMATION = Path("rust-executor/src/confirmation.rs")
RUST_MAIN = Path("rust-executor/src/main.rs")

REVIEWED_SOURCE_BLOBS = {
    ADMISSION_TOOL: "c3d7b349bc8c33f8d3e0fac5e87c105279e2dd04",
    PRESUBMIT_TOOL: "180980842a673354b024db78195b6bfe0209ab6f",
    RUST_WALLET: "30d183f3fcf49ec4c0f78c9b5a5c2fde0a520967",
    RUST_PHASE5_GATE: "4b77e48d99b89367fafb395a46edd68a61788a5f",
    RUST_PHASE6_READINESS: "81c8320bee7b2263b9e4844510db842516d7c5a0",
    RUST_PHASE6_GATE: "17665cb59b5e5651f0ebb4c49e88f327529587d8",
    RUST_CONTROLLED_LIVE: "5686a9578a3f6191d9fc1106cf75569c966dea69",
    RUST_SIGNER: "1d6569b776e697db2c1cec83fcae4c833c0baaf0",
    RUST_SUBMISSION: "8a8ca557963d2216ca30e9946800e75959f2701c",
    RUST_EXECUTION_STORE: "72bcad76da58e84a3900b59657f1c652a0f35edf",
    RUST_CONFIRMATION: "7bfd3386cf7a99c241073ea24cd529ccab52e8ea",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
}

LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
SUBMIT_COMMAND = "controlled-live-submit-once"
STATUS_COMMAND = "execution-intent-status"

DYNAMIC_ADMISSION_FIELDS = frozenset(
    {
        "fresh_readiness_sha256",
        "fresh_current_block_height",
        "fresh_block_height_remaining",
        "admission_sha256",
    }
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_admission_sha256",
    "expected_saved_admission_sha256",
    "fresh_admission_sha256",
    "admission_static_binding_sha256",
    "decision_id",
    "pool_address",
    "executor_wallet_pubkey",
    "prepared_transaction_sha256",
    "final_simulation_sha256",
    "execution_intent_snapshot_sha256",
    "controlled_live_config_sha256",
    "transaction_guard_config_sha256",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "rpc_endpoint_sha256",
    "pio_database_path",
    "execution_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "execution_database_sha256_before",
    "execution_database_sha256_after",
    "pre_intent_status",
    "pre_signature_absent",
    "pre_error_absent",
    "submit_command",
    "submission_process_returncode",
    "submission_attempted_once",
    "automatic_retry_performed",
    "signature",
    "rpc_accepted",
    "rpc_error",
    "reused_persisted_signature",
    "post_intent_status",
    "post_signature_matches",
    "post_error_absent",
    "execution_database_mutated",
    "pio_database_unchanged",
    "fresh_admission_valid",
    "fresh_admission_static_binding_matches_saved",
    "human_transaction_execution_authorization_verified",
    "transaction_authorization_not_expired",
    "executor_keypair_identity_verified",
    "blockhash_not_expired_at_admission",
    "first_submission_only_enforced",
    "confirmation_receipt_reconciliation_required",
    "confirmation_recovery_required_if_rpc_unaccepted",
    "phase7_promotion_separate",
    "transaction_signing_performed",
    "transaction_submission_attempted",
    "transaction_submission_rpc_accepted",
    "live_capital_movement_confirmed",
    "phase7_promotion_persisted",
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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 execute-once dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"Phase 7 execute-once dependency mismatch: {relative}")
    admission = _load_module(
        source / ADMISSION_TOOL,
        "phase7_execute_once_admission",
    )
    presubmit = _load_module(
        source / PRESUBMIT_TOOL,
        "phase7_execute_once_presubmit",
    )
    return admission, presubmit


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"unsafe file type for execution state: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(path: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(path),
        "wal": _regular_hash_or_none(Path(str(path) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(path) + "-shm")),
    }


def _static_admission(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if key not in DYNAMIC_ADMISSION_FIELDS
    }


def _executor_env(*, rpc_url: str) -> dict[str, str]:
    if os.environ.get(LIVE_SUBMIT_ENV) != "1":
        raise ValueError(f"{LIVE_SUBMIT_ENV}=1 is required for execute-once")
    keypair = os.environ.get(KEYPAIR_ENV)
    if not keypair:
        raise ValueError(f"{KEYPAIR_ENV} is required for execute-once")
    env = dict(os.environ)
    env[LIVE_SUBMIT_ENV] = "1"
    env["SOLANA_RPC_URL"] = rpc_url
    env.pop("RPC_URL", None)
    return env


def _run_json(
    command: list[str],
    *,
    env: dict[str, str],
    allowed_returncodes: set[int],
    timeout: int = 60,
) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode not in allowed_returncodes:
        raise ValueError(
            f"reviewed executor command failed with return code {completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("reviewed executor command returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("reviewed executor command result is invalid")
    return completed.returncode, value


def _intent_status(
    *,
    executor_binary: Path,
    execution_database: Path,
    decision_id: str,
    env: dict[str, str],
) -> dict[str, Any]:
    _, record = _run_json(
        [
            str(executor_binary),
            STATUS_COMMAND,
            str(execution_database),
            decision_id,
        ],
        env=env,
        allowed_returncodes={0},
        timeout=20,
    )
    return record


def _validate_submission_report(
    report: dict[str, Any],
    *,
    decision_id: str,
    returncode: int,
) -> None:
    expected = {
        "decision_id",
        "signature",
        "reused_persisted_signature",
        "rpc_accepted",
        "rpc_error",
        "intent_status",
    }
    if set(report) != expected:
        raise ValueError("Phase 7 execute-once submission report schema mismatch")
    if report.get("decision_id") != decision_id:
        raise ValueError("Phase 7 execute-once decision mismatch")
    if not isinstance(report.get("signature"), str) or not report["signature"]:
        raise ValueError("Phase 7 execute-once signature is missing")
    if report.get("reused_persisted_signature") is not False:
        raise ValueError("Phase 7 execute-once unexpectedly reused a signature")
    if report.get("intent_status") != "SENT":
        raise ValueError("Phase 7 execute-once must persist SENT status")

    accepted = report.get("rpc_accepted")
    if not isinstance(accepted, bool):
        raise ValueError("Phase 7 execute-once RPC accepted flag is invalid")
    if accepted:
        if returncode != 0 or report.get("rpc_error") is not None:
            raise ValueError("Phase 7 accepted submission return semantics mismatch")
    else:
        if returncode != 2:
            raise ValueError("Phase 7 ambiguous submission must return code 2")
        error = report.get("rpc_error")
        if not isinstance(error, str) or not error:
            raise ValueError("Phase 7 ambiguous submission requires RPC error text")


def validate_execution_once_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 7 execute-once report must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"execution_once_sha256"}:
        raise ValueError("Phase 7 execute-once report schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 execute-once format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 execute-once artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 execute-once lineage mismatch")

    for field in (
        "saved_admission_sha256",
        "expected_saved_admission_sha256",
        "fresh_admission_sha256",
        "admission_static_binding_sha256",
        "prepared_transaction_sha256",
        "final_simulation_sha256",
        "execution_intent_snapshot_sha256",
        "controlled_live_config_sha256",
        "transaction_guard_config_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "rpc_endpoint_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "execution_database_sha256_before",
        "execution_database_sha256_after",
        "execution_once_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 7 execute-once {field} is invalid")

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 7 execute-once {field} is invalid")

    for field in (
        "decision_id",
        "pool_address",
        "executor_wallet_pubkey",
        "executor_binary_path",
        "pio_database_path",
        "execution_database_path",
        "signature",
        "post_intent_status",
        "pre_intent_status",
        "submit_command",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 7 execute-once {field} is invalid")

    if report["saved_admission_sha256"] != report["expected_saved_admission_sha256"]:
        raise ValueError("Phase 7 execute-once saved admission digest mismatch")
    if report["executor_binary_sha256"] != report["expected_executor_binary_sha256"]:
        raise ValueError("Phase 7 execute-once executor binary trust-root mismatch")
    if report["submit_command"] != SUBMIT_COMMAND:
        raise ValueError("Phase 7 execute-once command mismatch")
    if report["pre_intent_status"] != "SIMULATION_PASSED":
        raise ValueError("Phase 7 execute-once pre-state must be SIMULATION_PASSED")
    if report["post_intent_status"] != "SENT":
        raise ValueError("Phase 7 execute-once post-state must be SENT")

    if report.get("submission_process_returncode") not in (0, 2):
        raise ValueError("Phase 7 execute-once process return code is invalid")
    accepted = report.get("rpc_accepted")
    if not isinstance(accepted, bool):
        raise ValueError("Phase 7 execute-once RPC accepted flag is invalid")
    if report.get("transaction_submission_rpc_accepted") is not accepted:
        raise ValueError("Phase 7 execute-once RPC accepted binding mismatch")
    if accepted:
        if report["submission_process_returncode"] != 0:
            raise ValueError("Phase 7 accepted execute-once return code mismatch")
        if report.get("rpc_error") is not None:
            raise ValueError("Phase 7 accepted execute-once cannot contain RPC error")
    else:
        if report["submission_process_returncode"] != 2:
            raise ValueError("Phase 7 ambiguous execute-once return code mismatch")
        if not isinstance(report.get("rpc_error"), str) or not report["rpc_error"]:
            raise ValueError("Phase 7 ambiguous execute-once requires RPC error")

    for field in (
        "pre_signature_absent",
        "pre_error_absent",
        "submission_attempted_once",
        "post_signature_matches",
        "post_error_absent",
        "execution_database_mutated",
        "pio_database_unchanged",
        "fresh_admission_valid",
        "fresh_admission_static_binding_matches_saved",
        "human_transaction_execution_authorization_verified",
        "transaction_authorization_not_expired",
        "executor_keypair_identity_verified",
        "blockhash_not_expired_at_admission",
        "first_submission_only_enforced",
        "confirmation_receipt_reconciliation_required",
        "phase7_promotion_separate",
        "transaction_signing_performed",
        "transaction_submission_attempted",
    ):
        if report.get(field) is not True:
            raise ValueError(f"Phase 7 execute-once requires {field}=true")

    if report.get("automatic_retry_performed") is not False:
        raise ValueError("Phase 7 execute-once requires automatic_retry_performed=false")
    if report.get("reused_persisted_signature") is not False:
        raise ValueError("Phase 7 execute-once requires reused_persisted_signature=false")
    if report.get("live_capital_movement_confirmed") is not False:
        raise ValueError(
            "Phase 7 execute-once requires live_capital_movement_confirmed=false"
        )
    if report.get("phase7_promotion_persisted") is not False:
        raise ValueError("Phase 7 execute-once requires phase7_promotion_persisted=false")
    if report.get("production_repository_git_mutated") is not False:
        raise ValueError(
            "Phase 7 execute-once requires production_repository_git_mutated=false"
        )
    if report.get("production_pio_database_modified") is not False:
        raise ValueError(
            "Phase 7 execute-once requires production_pio_database_modified=false"
        )
    if report.get("confirmation_recovery_required_if_rpc_unaccepted") is not (
        not accepted
    ):
        raise ValueError("Phase 7 execute-once confirmation recovery flag mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["execution_once_sha256"] != expected:
        raise ValueError("Phase 7 execute-once digest mismatch")


def execute_once(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_execution_admission_path: str | Path,
    expected_saved_admission_sha256: str,
    saved_transaction_execution_readiness_path: str | Path,
    expected_saved_readiness_sha256: str,
    phase6_post_promotion_audit_path: str | Path,
    saved_phase7_evidence_status_path: str | Path,
    saved_phase7_evidence_plan_path: str | Path,
    saved_input_preflight_path: str | Path,
    controlled_live_config_path: str | Path,
    proposal_path: str | Path,
    executor_wallet_pubkey: str,
    saved_controlled_live_authorization_verification_path: str | Path,
    controlled_live_signed_payload_path: str | Path,
    controlled_live_signature_path: str | Path,
    controlled_live_allowed_signers_path: str | Path,
    expected_controlled_live_allowed_signers_sha256: str,
    saved_authorization_readiness_path: str | Path,
    expected_authorization_readiness_sha256: str,
    execution_database_path: str | Path,
    saved_presubmit_gate_path: str | Path,
    transaction_request_path: str | Path,
    saved_transaction_authorization_verification_path: str | Path,
    transaction_signed_payload_path: str | Path,
    transaction_signature_path: str | Path,
    transaction_allowed_signers_path: str | Path,
    expected_transaction_allowed_signers_sha256: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    transaction_guard_config_path: str | Path,
    rpc_url: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    admission_module, presubmit_module = _load_reviewed_modules(source)

    saved_admission = _load_json(
        saved_execution_admission_path,
        label="saved Phase 7 execution admission",
    )
    admission_module.validate_execution_admission(saved_admission)
    if not _is_hex_digest(expected_saved_admission_sha256, 64):
        raise ValueError("expected Phase 7 admission digest is invalid")
    if saved_admission["admission_sha256"] != expected_saved_admission_sha256:
        raise ValueError("saved Phase 7 admission digest mismatch")
    if saved_admission.get("execution_admission_ready") is not True:
        raise ValueError("saved Phase 7 execution admission is not ready")
    if saved_admission.get("requires_separate_single_shot_submitter") is not True:
        raise ValueError("saved Phase 7 admission does not require one-shot submitter")

    fresh_admission = admission_module.build_execution_admission(
        repository=production,
        source_tree=source,
        saved_transaction_execution_readiness_path=(
            saved_transaction_execution_readiness_path
        ),
        expected_saved_readiness_sha256=expected_saved_readiness_sha256,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
        saved_phase7_evidence_status_path=saved_phase7_evidence_status_path,
        saved_phase7_evidence_plan_path=saved_phase7_evidence_plan_path,
        saved_input_preflight_path=saved_input_preflight_path,
        controlled_live_config_path=controlled_live_config_path,
        proposal_path=proposal_path,
        executor_wallet_pubkey=executor_wallet_pubkey,
        saved_controlled_live_authorization_verification_path=(
            saved_controlled_live_authorization_verification_path
        ),
        controlled_live_signed_payload_path=controlled_live_signed_payload_path,
        controlled_live_signature_path=controlled_live_signature_path,
        controlled_live_allowed_signers_path=controlled_live_allowed_signers_path,
        expected_controlled_live_allowed_signers_sha256=(
            expected_controlled_live_allowed_signers_sha256
        ),
        saved_authorization_readiness_path=saved_authorization_readiness_path,
        expected_authorization_readiness_sha256=(
            expected_authorization_readiness_sha256
        ),
        execution_database_path=execution_database_path,
        saved_presubmit_gate_path=saved_presubmit_gate_path,
        transaction_request_path=transaction_request_path,
        saved_transaction_authorization_verification_path=(
            saved_transaction_authorization_verification_path
        ),
        transaction_signed_payload_path=transaction_signed_payload_path,
        transaction_signature_path=transaction_signature_path,
        transaction_allowed_signers_path=transaction_allowed_signers_path,
        expected_transaction_allowed_signers_sha256=(
            expected_transaction_allowed_signers_sha256
        ),
        executor_binary_path=executor_binary_path,
        expected_executor_binary_sha256=expected_executor_binary_sha256,
        rpc_url=rpc_url,
        now=now,
    )
    admission_module.validate_execution_admission(fresh_admission)

    if _static_admission(fresh_admission) != _static_admission(saved_admission):
        raise ValueError("fresh Phase 7 execution admission static binding drifted")
    if (
        fresh_admission["fresh_current_block_height"]
        < saved_admission["fresh_current_block_height"]
    ):
        raise ValueError("fresh Phase 7 admission block height moved backwards")
    if fresh_admission.get("blockhash_not_expired") is not True:
        raise ValueError("Phase 7 execute-once blockhash is expired")

    saved_presubmit = _load_json(
        saved_presubmit_gate_path,
        label="saved Phase 7 presubmit gate",
    )
    presubmit_module.validate_phase7_presubmit_evidence_gate(saved_presubmit)
    if saved_presubmit.get("presubmit_evidence_ready") is not True:
        raise ValueError("saved Phase 7 presubmit evidence is not ready")
    if saved_presubmit.get("decision_id") != fresh_admission["decision_id"]:
        raise ValueError("presubmit decision differs from execution admission")

    guard_config = _load_json(
        transaction_guard_config_path,
        label="transaction guard config",
    )
    controlled_config = _load_json(
        controlled_live_config_path,
        label="controlled-LIVE config",
    )
    if _sha256_value(guard_config) != saved_presubmit["transaction_guard_config_sha256"]:
        raise ValueError("transaction guard config differs from presubmit evidence")
    if _sha256_value(controlled_config) != saved_presubmit["controlled_live_config_sha256"]:
        raise ValueError("controlled-LIVE config differs from presubmit evidence")

    execution_database = Path(execution_database_path).resolve(strict=True)
    pio_database = Path(str(saved_presubmit["pio_database_path"])).resolve(strict=True)
    if Path(str(saved_presubmit["execution_database_path"])).resolve() != execution_database:
        raise ValueError("execution database differs from presubmit evidence")
    expected_pio = (production / "data" / "pio.db").resolve(strict=False)
    if pio_database != expected_pio:
        raise ValueError("Pio database differs from reviewed production path")

    binary = Path(fresh_admission["executor_binary_path"]).resolve(strict=True)
    if _sha256_bytes(binary.read_bytes()) != fresh_admission["executor_binary_sha256"]:
        raise ValueError("executor binary changed after admission")
    if fresh_admission["executor_binary_sha256"] != expected_executor_binary_sha256:
        raise ValueError("executor binary trust-root mismatch")
    if _sha256_text(rpc_url) != fresh_admission["rpc_endpoint_sha256"]:
        raise ValueError("RPC endpoint differs from execution admission")

    env = _executor_env(rpc_url=rpc_url)

    pre = _intent_status(
        executor_binary=binary,
        execution_database=execution_database,
        decision_id=fresh_admission["decision_id"],
        env=env,
    )
    if pre.get("status") != "SIMULATION_PASSED":
        raise ValueError("execute-once requires SIMULATION_PASSED immediately before submit")
    if pre.get("signature") is not None:
        raise ValueError("execute-once requires no persisted signature before submit")
    if pre.get("error") is not None:
        raise ValueError("execute-once requires no persisted error before submit")

    pio_before = _database_state(pio_database)
    execution_before = _database_state(execution_database)
    if pio_before["database"] is None or execution_before["database"] is None:
        raise ValueError("execution databases are missing")

    returncode, submission = _run_json(
        [
            str(binary),
            SUBMIT_COMMAND,
            str(pio_database),
            str(execution_database),
            fresh_admission["decision_id"],
            str(Path(transaction_guard_config_path).resolve(strict=True)),
            str(Path(controlled_live_config_path).resolve(strict=True)),
        ],
        env=env,
        allowed_returncodes={0, 2},
        timeout=60,
    )
    _validate_submission_report(
        submission,
        decision_id=fresh_admission["decision_id"],
        returncode=returncode,
    )

    post = _intent_status(
        executor_binary=binary,
        execution_database=execution_database,
        decision_id=fresh_admission["decision_id"],
        env=env,
    )
    if post.get("status") != "SENT":
        raise ValueError("execute-once did not persist SENT state")
    if post.get("signature") != submission["signature"]:
        raise ValueError("persisted SENT signature differs from submission report")
    if post.get("error") is not None:
        raise ValueError("execute-once persisted an unexpected execution error")

    pio_after = _database_state(pio_database)
    execution_after = _database_state(execution_database)
    if pio_after != pio_before:
        raise ValueError("Pio database changed during execute-once")
    if execution_after == execution_before:
        raise ValueError("execution database did not record the submission state transition")

    static_digest = _sha256_value(_static_admission(saved_admission))
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
        "saved_admission_sha256": saved_admission["admission_sha256"],
        "expected_saved_admission_sha256": expected_saved_admission_sha256,
        "fresh_admission_sha256": fresh_admission["admission_sha256"],
        "admission_static_binding_sha256": static_digest,
        "decision_id": fresh_admission["decision_id"],
        "pool_address": fresh_admission["pool_address"],
        "executor_wallet_pubkey": fresh_admission["executor_wallet_pubkey"],
        "prepared_transaction_sha256": fresh_admission["prepared_transaction_sha256"],
        "final_simulation_sha256": fresh_admission["final_simulation_sha256"],
        "execution_intent_snapshot_sha256": fresh_admission[
            "execution_intent_snapshot_sha256"
        ],
        "controlled_live_config_sha256": saved_presubmit[
            "controlled_live_config_sha256"
        ],
        "transaction_guard_config_sha256": saved_presubmit[
            "transaction_guard_config_sha256"
        ],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": fresh_admission["executor_binary_sha256"],
        "expected_executor_binary_sha256": expected_executor_binary_sha256,
        "rpc_endpoint_sha256": _sha256_text(rpc_url),
        "pio_database_path": str(pio_database),
        "execution_database_path": str(execution_database),
        "pio_database_sha256_before": pio_before["database"],
        "pio_database_sha256_after": pio_after["database"],
        "pio_wal_sha256_before": pio_before["wal"],
        "pio_wal_sha256_after": pio_after["wal"],
        "pio_shm_sha256_before": pio_before["shm"],
        "pio_shm_sha256_after": pio_after["shm"],
        "execution_database_sha256_before": execution_before["database"],
        "execution_database_sha256_after": execution_after["database"],
        "pre_intent_status": "SIMULATION_PASSED",
        "pre_signature_absent": True,
        "pre_error_absent": True,
        "submit_command": SUBMIT_COMMAND,
        "submission_process_returncode": returncode,
        "submission_attempted_once": True,
        "automatic_retry_performed": False,
        "signature": submission["signature"],
        "rpc_accepted": submission["rpc_accepted"],
        "rpc_error": submission["rpc_error"],
        "reused_persisted_signature": False,
        "post_intent_status": "SENT",
        "post_signature_matches": True,
        "post_error_absent": True,
        "execution_database_mutated": True,
        "pio_database_unchanged": True,
        "fresh_admission_valid": True,
        "fresh_admission_static_binding_matches_saved": True,
        "human_transaction_execution_authorization_verified": True,
        "transaction_authorization_not_expired": True,
        "executor_keypair_identity_verified": True,
        "blockhash_not_expired_at_admission": True,
        "first_submission_only_enforced": True,
        "confirmation_receipt_reconciliation_required": True,
        "confirmation_recovery_required_if_rpc_unaccepted": (
            not submission["rpc_accepted"]
        ),
        "phase7_promotion_separate": True,
        "transaction_signing_performed": True,
        "transaction_submission_attempted": True,
        "transaction_submission_rpc_accepted": submission["rpc_accepted"],
        "live_capital_movement_confirmed": False,
        "phase7_promotion_persisted": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "execution_once_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_execution_once_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one first-attempt Phase 7 controlled-LIVE transaction "
            "after rebuilding the keypair-bound admission gate. This tool invokes "
            "only the reviewed controlled-live-submit-once Rust command and never "
            "automatically retries or resubmits an ambiguous SENT transaction."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-execution-admission", required=True)
    parser.add_argument("--expected-saved-admission-sha256", required=True)
    parser.add_argument("--saved-transaction-execution-readiness", required=True)
    parser.add_argument("--expected-saved-readiness-sha256", required=True)
    parser.add_argument("--phase6-post-promotion-audit", required=True)
    parser.add_argument("--saved-phase7-evidence-status", required=True)
    parser.add_argument("--saved-phase7-evidence-plan", required=True)
    parser.add_argument("--saved-input-preflight", required=True)
    parser.add_argument("--controlled-live-config", required=True)
    parser.add_argument("--transaction-guard-config", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--executor-wallet-pubkey", required=True)
    parser.add_argument(
        "--saved-controlled-live-authorization-verification",
        required=True,
    )
    parser.add_argument("--controlled-live-signed-payload", required=True)
    parser.add_argument("--controlled-live-signature", required=True)
    parser.add_argument("--controlled-live-allowed-signers", required=True)
    parser.add_argument(
        "--expected-controlled-live-allowed-signers-sha256",
        required=True,
    )
    parser.add_argument("--saved-authorization-readiness", required=True)
    parser.add_argument("--expected-authorization-readiness-sha256", required=True)
    parser.add_argument("--execution-db", required=True)
    parser.add_argument("--saved-presubmit-gate", required=True)
    parser.add_argument("--transaction-request", required=True)
    parser.add_argument(
        "--saved-transaction-authorization-verification",
        required=True,
    )
    parser.add_argument("--transaction-signed-payload", required=True)
    parser.add_argument("--transaction-signature", required=True)
    parser.add_argument("--transaction-allowed-signers", required=True)
    parser.add_argument(
        "--expected-transaction-allowed-signers-sha256",
        required=True,
    )
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    report = execute_once(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_execution_admission_path=args.saved_execution_admission,
        expected_saved_admission_sha256=args.expected_saved_admission_sha256,
        saved_transaction_execution_readiness_path=(
            args.saved_transaction_execution_readiness
        ),
        expected_saved_readiness_sha256=args.expected_saved_readiness_sha256,
        phase6_post_promotion_audit_path=args.phase6_post_promotion_audit,
        saved_phase7_evidence_status_path=args.saved_phase7_evidence_status,
        saved_phase7_evidence_plan_path=args.saved_phase7_evidence_plan,
        saved_input_preflight_path=args.saved_input_preflight,
        controlled_live_config_path=args.controlled_live_config,
        transaction_guard_config_path=args.transaction_guard_config,
        proposal_path=args.proposal,
        executor_wallet_pubkey=args.executor_wallet_pubkey,
        saved_controlled_live_authorization_verification_path=(
            args.saved_controlled_live_authorization_verification
        ),
        controlled_live_signed_payload_path=args.controlled_live_signed_payload,
        controlled_live_signature_path=args.controlled_live_signature,
        controlled_live_allowed_signers_path=args.controlled_live_allowed_signers,
        expected_controlled_live_allowed_signers_sha256=(
            args.expected_controlled_live_allowed_signers_sha256
        ),
        saved_authorization_readiness_path=args.saved_authorization_readiness,
        expected_authorization_readiness_sha256=(
            args.expected_authorization_readiness_sha256
        ),
        execution_database_path=args.execution_db,
        saved_presubmit_gate_path=args.saved_presubmit_gate,
        transaction_request_path=args.transaction_request,
        saved_transaction_authorization_verification_path=(
            args.saved_transaction_authorization_verification
        ),
        transaction_signed_payload_path=args.transaction_signed_payload,
        transaction_signature_path=args.transaction_signature,
        transaction_allowed_signers_path=args.transaction_allowed_signers,
        expected_transaction_allowed_signers_sha256=(
            args.expected_transaction_allowed_signers_sha256
        ),
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=args.expected_executor_binary_sha256,
        rpc_url=args.rpc_url,
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["rpc_accepted"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
