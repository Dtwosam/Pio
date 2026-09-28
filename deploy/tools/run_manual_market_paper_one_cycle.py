from __future__ import annotations

import argparse
import fcntl
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
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_ONE_CYCLE_EXECUTION_RECEIPT_V1"

EXECUTION_GATE_TOOL = Path(
    "deploy/tools/check_manual_market_paper_manual_cycle_execution_gate.py"
)
REVIEWED_SOURCE_BLOBS = {
    EXECUTION_GATE_TOOL: "56d8ff58d027cfb2e9a65b97a4a979236613ccc7",
}

MODULE_NAME = "meteora_learner.manual_market_paper_cycle_cli"
LOCK_PATH = Path("/var/tmp/pio-manual-market-paper-one-cycle.lock")
EXECUTION_TIMEOUT_SECONDS = 900

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "execution_gate_sha256",
    "authorization_request_sha256",
    "signed_authorization_verification_sha256",
    "production_repository",
    "paper_database_path",
    "account",
    "run_id",
    "cycle_parameters_sha256",
    "cycle_report",
    "cycle_report_sha256",
    "status",
    "observed_at",
    "research_status",
    "scheduler_status",
    "exploration_status",
    "positions_opened",
    "new_positions_pending_next_tick",
    "manual_only",
    "paper_only",
    "policy_actionable",
    "live_authorized",
    "one_cycle_execution_completed",
    "paper_database_write_expected",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "paper_timer_action_performed",
    "service_action_performed",
    "detector_cursor_action_performed",
    "transaction_signing_performed",
    "transaction_submission_performed",
    "live_capital_used",
    "requires_post_cycle_audit",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


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


def _load_gate_module(source: Path) -> Any:
    path = source / EXECUTION_GATE_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed one-cycle execution gate tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[EXECUTION_GATE_TOOL]:
        raise ValueError("reviewed one-cycle execution gate blob mismatch")
    return _load_module(
        path,
        "manual_market_paper_one_cycle_executor_gate",
    )


def _paper_database(production: Path) -> Path:
    data_dir = production / "data"
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError("one-cycle PAPER data directory is unsafe")
    database = data_dir / "pio.db"
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError("one-cycle PAPER database is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("one-cycle PAPER database must be a regular file")
    return database


def _python_executable(production: Path) -> Path:
    candidate = production / "python-learner" / ".venv" / "bin" / "python"
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("one-cycle production Python executable is missing") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("one-cycle production Python executable is invalid")
    return candidate


def _command(parameters: dict[str, Any], python_bin: Path) -> list[str]:
    if parameters.get("observed_at") is not None:
        raise ValueError("one-cycle executor refuses synthetic observed_at")
    if parameters.get("max_new_positions") != 1:
        raise ValueError("one-cycle executor requires max_new_positions=1")
    if parameters.get("scheduler_max_positions") != 1:
        raise ValueError("one-cycle executor requires scheduler_max_positions=1")

    return [
        str(python_bin),
        "-m",
        MODULE_NAME,
        "--account",
        str(parameters["account"]),
        "--run-id",
        str(parameters["run_id"]),
        "--capital-per-position",
        str(parameters["capital_per_position_quote"]),
        "--network-cost-quote",
        str(parameters["network_cost_quote"]),
        "--max-new-positions",
        str(parameters["max_new_positions"]),
        "--max-pools-considered",
        str(parameters["max_pools_considered"]),
        "--minimum-chain-observations",
        str(parameters["minimum_chain_observations"]),
        "--intake-max-pools",
        str(parameters["intake_max_pools"]),
        "--seed-batch-limit",
        str(parameters["seed_batch_limit"]),
        "--refresh-batch-limit",
        str(parameters["refresh_batch_limit"]),
        "--discovery-page-size",
        str(parameters["discovery_page_size"]),
        "--discovery-max-pages",
        str(parameters["discovery_max_pages"]),
        "--discovery-sort-by",
        str(parameters["discovery_sort_by"]),
        "--bin-array-radius",
        str(parameters["bin_array_radius"]),
        "--timeout-seconds",
        str(parameters["timeout_seconds"]),
        "--quote-max-age-seconds",
        str(parameters["quote_max_age_seconds"]),
        "--max-share-bps",
        str(parameters["max_share_bps"]),
        "--scheduler-max-positions",
        str(parameters["scheduler_max_positions"]),
    ]


def _execution_environment(production: Path, database: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(production / "python-learner" / "src")
    env["PIO_DATABASE_PATH"] = str(database)
    return env


def _validate_cycle_report(
    report: dict[str, Any],
    *,
    parameters: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("one-cycle CLI report must be a JSON object")
    if report.get("account_id") != parameters["account"]:
        raise ValueError("one-cycle CLI account binding mismatch")
    if report.get("run_id") != parameters["run_id"]:
        raise ValueError("one-cycle CLI run-id binding mismatch")
    if report.get("manual_only") is not True:
        raise ValueError("one-cycle CLI must remain manual-only")
    if report.get("paper_only") is not True:
        raise ValueError("one-cycle CLI must remain PAPER-only")
    if report.get("policy_actionable") is not False:
        raise ValueError("one-cycle CLI must remain non-actionable")
    if report.get("live_authorized") is not False:
        raise ValueError("one-cycle CLI must not authorize live execution")

    pending = report.get("new_positions_pending_next_tick")
    if (
        not isinstance(pending, int)
        or isinstance(pending, bool)
        or pending < 0
        or pending > 1
    ):
        raise ValueError("one-cycle CLI pending-position bound exceeded")

    exploration = report.get("exploration")
    if exploration is not None:
        if not isinstance(exploration, dict):
            raise ValueError("one-cycle CLI exploration report is invalid")
        if exploration.get("max_new_positions") != 1:
            raise ValueError("one-cycle CLI exploration position cap drifted")
        if exploration.get("max_pools_considered") != parameters[
            "max_pools_considered"
        ]:
            raise ValueError("one-cycle CLI exploration pool cap drifted")
        opened = exploration.get("positions_opened")
        if (
            not isinstance(opened, int)
            or isinstance(opened, bool)
            or opened < 0
            or opened > 1
        ):
            raise ValueError("one-cycle CLI opened-position bound exceeded")


def validate_execution_receipt(receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("one-cycle execution receipt must be a JSON object")
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError("one-cycle execution receipt schema mismatch")
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported one-cycle execution receipt format")
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected one-cycle execution receipt type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("one-cycle execution receipt lineage mismatch")

    for field in (
        "execution_gate_sha256",
        "authorization_request_sha256",
        "signed_authorization_verification_sha256",
        "cycle_parameters_sha256",
        "cycle_report_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(receipt.get(field), 64):
            raise ValueError(f"one-cycle execution receipt {field} is invalid")

    for field in (
        "production_repository",
        "paper_database_path",
        "account",
        "run_id",
        "status",
        "observed_at",
    ):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(f"one-cycle execution receipt {field} is invalid")

    report = receipt.get("cycle_report")
    if not isinstance(report, dict):
        raise ValueError("one-cycle execution receipt report is invalid")
    if receipt["cycle_report_sha256"] != hashlib.sha256(
        _canonical_bytes(report)
    ).hexdigest():
        raise ValueError("one-cycle execution receipt report digest mismatch")
    if report.get("account_id") != receipt["account"]:
        raise ValueError("one-cycle execution receipt account mismatch")
    if report.get("run_id") != receipt["run_id"]:
        raise ValueError("one-cycle execution receipt run-id mismatch")
    if report.get("status") != receipt["status"]:
        raise ValueError("one-cycle execution receipt status mismatch")
    if report.get("observed_at") != receipt["observed_at"]:
        raise ValueError("one-cycle execution receipt observed-at mismatch")

    for field in ("manual_only", "paper_only", "one_cycle_execution_completed"):
        if receipt.get(field) is not True:
            raise ValueError(f"one-cycle execution receipt requires {field}=true")
    for field in (
        "policy_actionable",
        "live_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "paper_timer_action_performed",
        "service_action_performed",
        "detector_cursor_action_performed",
        "transaction_signing_performed",
        "transaction_submission_performed",
        "live_capital_used",
    ):
        if receipt.get(field) is not False:
            raise ValueError(f"one-cycle execution receipt requires {field}=false")
    if receipt.get("paper_database_write_expected") is not True:
        raise ValueError("one-cycle execution receipt must acknowledge PAPER DB write")
    if receipt.get("requires_post_cycle_audit") is not True:
        raise ValueError("one-cycle execution receipt requires post-cycle audit")

    for field in ("positions_opened", "new_positions_pending_next_tick"):
        value = receipt.get(field)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > 1
        ):
            raise ValueError(f"one-cycle execution receipt {field} bound exceeded")

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if receipt["receipt_sha256"] != expected_digest:
        raise ValueError("one-cycle execution receipt digest mismatch")


def execute_one_cycle(
    *,
    repository: str | Path,
    source_tree: str | Path,
    expected_execution_gate_sha256: str,
    execution_gate_path: str | Path,
    private_bundle_report_path: str | Path,
    pre_mutation_handoff_path: str | Path,
    execution_precheck_path: str | Path,
    mutation_receipt_path: str | Path,
    post_mutation_audit_path: str | Path,
    manual_cycle_readiness_path: str | Path,
    authorization_request_path: str | Path,
    signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
) -> dict[str, Any]:
    if not _is_hex_digest(expected_execution_gate_sha256, 64):
        raise ValueError("expected execution-gate SHA-256 is invalid")

    source_candidate = Path(source_tree).expanduser()
    production_candidate = Path(repository).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    if production_candidate.is_symlink():
        raise ValueError("production repository root must not be a symlink")
    source = source_candidate.resolve()
    production = production_candidate.resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    gate_module = _load_gate_module(source)
    saved_gate = _load_json(
        execution_gate_path,
        label="saved one-cycle execution gate",
    )
    gate_module.validate_execution_gate(saved_gate)
    if saved_gate.get("execution_gate_sha256") != expected_execution_gate_sha256:
        raise ValueError("saved one-cycle execution gate digest pin mismatch")
    if saved_gate.get("manual_cycle_execution_gate_ready") is not True:
        raise ValueError("saved one-cycle execution gate is not ready")
    if saved_gate.get("requires_immediate_one_shot_executor_recheck") is not True:
        raise ValueError("saved one-cycle execution gate lacks executor boundary")
    if Path(str(saved_gate["production_repository"])).resolve() != production:
        raise ValueError("one-cycle execution gate repository binding mismatch")

    database = _paper_database(production)
    python_bin = _python_executable(production)

    lock_fd = os.open(
        LOCK_PATH,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another manual PAPER one-cycle executor is active") from exc

        fresh_gate = gate_module.build_execution_gate(
            repository=production,
            source_tree=source,
            private_bundle_report_path=private_bundle_report_path,
            pre_mutation_handoff_path=pre_mutation_handoff_path,
            execution_precheck_path=execution_precheck_path,
            mutation_receipt_path=mutation_receipt_path,
            post_mutation_audit_path=post_mutation_audit_path,
            manual_cycle_readiness_path=manual_cycle_readiness_path,
            authorization_request_path=authorization_request_path,
            signed_authorization_verification_path=signed_authorization_verification_path,
            signed_payload_path=signed_payload_path,
            signature_path=signature_path,
            allowed_signers_path=allowed_signers_path,
            expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        )
        gate_module.validate_execution_gate(fresh_gate)
        if fresh_gate != saved_gate:
            raise ValueError(
                "one-cycle executor fresh execution gate differs from saved gate"
            )

        parameters = fresh_gate["cycle_parameters"]
        command = _command(parameters, python_bin)
        completed = subprocess.run(
            command,
            cwd=production,
            env=_execution_environment(production, database),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=EXECUTION_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "one-cycle PAPER CLI failed without retry; "
                f"returncode={completed.returncode}"
            )
        try:
            cycle_report = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("one-cycle PAPER CLI returned invalid JSON") from exc
        _validate_cycle_report(cycle_report, parameters=parameters)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    research = cycle_report.get("research")
    scheduler = cycle_report.get("scheduler")
    exploration = cycle_report.get("exploration")
    positions_opened = (
        exploration.get("positions_opened", 0)
        if isinstance(exploration, dict)
        else 0
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
        "execution_gate_sha256": fresh_gate["execution_gate_sha256"],
        "authorization_request_sha256": fresh_gate[
            "authorization_request_sha256"
        ],
        "signed_authorization_verification_sha256": fresh_gate[
            "fresh_signed_authorization_verification_sha256"
        ],
        "production_repository": str(production),
        "paper_database_path": str(database),
        "account": parameters["account"],
        "run_id": parameters["run_id"],
        "cycle_parameters_sha256": fresh_gate["cycle_parameters_sha256"],
        "cycle_report": cycle_report,
        "cycle_report_sha256": hashlib.sha256(
            _canonical_bytes(cycle_report)
        ).hexdigest(),
        "status": str(cycle_report["status"]),
        "observed_at": str(cycle_report["observed_at"]),
        "research_status": (
            str(research["status"]) if isinstance(research, dict) else None
        ),
        "scheduler_status": (
            str(scheduler["status"]) if isinstance(scheduler, dict) else None
        ),
        "exploration_status": (
            str(exploration["status"]) if isinstance(exploration, dict) else None
        ),
        "positions_opened": positions_opened,
        "new_positions_pending_next_tick": cycle_report[
            "new_positions_pending_next_tick"
        ],
        "manual_only": True,
        "paper_only": True,
        "policy_actionable": False,
        "live_authorized": False,
        "one_cycle_execution_completed": True,
        "paper_database_write_expected": True,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "paper_timer_action_performed": False,
        "service_action_performed": False,
        "detector_cursor_action_performed": False,
        "transaction_signing_performed": False,
        "transaction_submission_performed": False,
        "live_capital_used": False,
        "requires_post_cycle_audit": True,
    }
    receipt = {
        **identity,
        "receipt_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_execution_receipt(receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one human-authorized bounded manual market/PAPER "
            "cycle after an immediate fresh execution-gate recheck. The tool "
            "writes only the virtual PAPER database through the reviewed manual "
            "cycle, never touches systemd/Git/source files, never signs/submits "
            "transactions, and never uses live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--expected-execution-gate-sha256", required=True)
    parser.add_argument("--execution-gate", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    parser.add_argument("--pre-mutation-handoff", required=True)
    parser.add_argument("--execution-precheck", required=True)
    parser.add_argument("--mutation-receipt", required=True)
    parser.add_argument("--post-mutation-audit", required=True)
    parser.add_argument("--manual-cycle-readiness", required=True)
    parser.add_argument("--authorization-request", required=True)
    parser.add_argument("--signed-authorization-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    args = parser.parse_args()

    receipt = execute_one_cycle(
        repository=args.repo,
        source_tree=args.source_tree,
        expected_execution_gate_sha256=args.expected_execution_gate_sha256,
        execution_gate_path=args.execution_gate,
        private_bundle_report_path=args.private_bundle_report,
        pre_mutation_handoff_path=args.pre_mutation_handoff,
        execution_precheck_path=args.execution_precheck,
        mutation_receipt_path=args.mutation_receipt,
        post_mutation_audit_path=args.post_mutation_audit,
        manual_cycle_readiness_path=args.manual_cycle_readiness,
        authorization_request_path=args.authorization_request,
        signed_authorization_verification_path=args.signed_authorization_verification,
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
