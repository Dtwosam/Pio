from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_ONE_CYCLE_POST_CYCLE_AUDIT_V1"

EXECUTOR_TOOL = Path("deploy/tools/run_manual_market_paper_one_cycle.py")
POST_MUTATION_AUDIT_TOOL = Path(
    "deploy/tools/check_manual_market_paper_preserved_post_mutation.py"
)
PAPER_ACCOUNT_MODULE = Path(
    "python-learner/src/meteora_learner/paper_account.py"
)
PAPER_AUDIT_MODULE = Path(
    "python-learner/src/meteora_learner/paper_audit.py"
)
STORAGE_MODULE = Path(
    "python-learner/src/meteora_learner/storage.py"
)

REVIEWED_SOURCE_BLOBS = {
    EXECUTOR_TOOL: "48d985da37e476c15c39de147b9f2131bcf69021",
    POST_MUTATION_AUDIT_TOOL: "f23bae2b423fe68778c75cb1131ed5822902d5d6",
    PAPER_ACCOUNT_MODULE: "01a77399d385cea0bd0ffb0e059bff6614ca35d7",
    PAPER_AUDIT_MODULE: "f58e27d7a8c629aca1235daf3b49134e3a63ad25",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

INSPECTION_TIMEOUT_SECONDS = 30

PAPER_INSPECTION_HELPER = r"""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from meteora_learner.paper_account import paper_account_snapshot
from meteora_learner.paper_audit import audit_paper_ledger
from meteora_learner.storage import Storage

database = Path(sys.argv[1])
account = sys.argv[2]

with tempfile.TemporaryDirectory(prefix="pio-one-cycle-paper-audit.") as tmp:
    snapshot = Path(tmp) / "pio.db"

    source = sqlite3.connect(
        f"file:{database}?mode=ro",
        uri=True,
    )
    try:
        source.execute("PRAGMA query_only=ON")
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    storage = Storage(snapshot)
    print(json.dumps({
        "account_snapshot": paper_account_snapshot(
            storage,
            account_id=account,
        ).to_record(),
        "ledger_audit": audit_paper_ledger(
            storage,
            account_id=account,
        ).to_record(),
    }, sort_keys=True))
"""

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "one_cycle_receipt_sha256",
    "execution_gate_sha256",
    "authorization_request_sha256",
    "signed_authorization_verification_sha256",
    "saved_post_mutation_audit_sha256",
    "fresh_post_mutation_audit_sha256",
    "production_repository",
    "paper_database_path",
    "account",
    "run_id",
    "cycle_report_sha256",
    "account_snapshot",
    "account_snapshot_sha256",
    "ledger_audit",
    "ledger_audit_sha256",
    "cycle_receipt_safe",
    "fresh_post_mutation_audit_matches_saved",
    "activation_state_unchanged",
    "paper_account_readable",
    "paper_ledger_passing",
    "opened_position_state_consistent",
    "post_cycle_audit_ready",
    "paper_timer_enable_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "paper_database_modified_by_audit",
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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"post-cycle audit dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"post-cycle audit dependency mismatch: {relative}")
    executor_module = _load_module(
        source / EXECUTOR_TOOL,
        "manual_market_paper_post_cycle_executor",
    )
    post_mutation_module = _load_module(
        source / POST_MUTATION_AUDIT_TOOL,
        "manual_market_paper_post_cycle_post_mutation",
    )
    return executor_module, post_mutation_module


def _paper_database(production: Path, expected: str) -> Path:
    expected_path = Path(expected)
    if not expected_path.is_absolute():
        raise ValueError("post-cycle PAPER database path must be absolute")
    database = production / "data" / "pio.db"
    if expected_path.resolve(strict=False) != database.resolve(strict=False):
        raise ValueError("post-cycle PAPER database binding mismatch")
    data_dir = production / "data"
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError("post-cycle PAPER data directory is unsafe")
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError("post-cycle PAPER database is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("post-cycle PAPER database must be a regular file")
    return database


def _inspect_paper_db(
    *,
    source: Path,
    database: Path,
    account: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(source / "python-learner" / "src")
    env["PIO_DATABASE_PATH"] = str(database)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            PAPER_INSPECTION_HELPER,
            str(database),
            account,
        ],
        cwd=source,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=INSPECTION_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise ValueError("post-cycle PAPER database inspection failed")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("post-cycle PAPER inspection returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("post-cycle PAPER inspection result is invalid")
    snapshot = value.get("account_snapshot")
    ledger = value.get("ledger_audit")
    if not isinstance(snapshot, dict) or not isinstance(ledger, dict):
        raise ValueError("post-cycle PAPER inspection fields are invalid")
    return snapshot, ledger


def _validate_account_snapshot(snapshot: dict[str, Any], *, account: str) -> None:
    if snapshot.get("account_id") != account:
        raise ValueError("post-cycle PAPER account snapshot binding mismatch")
    for field in (
        "starting_equity_quote",
        "cash_quote",
        "open_position_mark_quote",
        "open_fee_income_quote",
        "open_reward_income_quote",
        "account_equity_quote",
        "high_water_equity_quote",
        "realized_pnl_quote",
    ):
        value = snapshot.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"post-cycle PAPER account {field} is invalid")
    for field in ("drawdown_bps", "open_positions", "closed_positions"):
        value = snapshot.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"post-cycle PAPER account {field} is invalid")


def _validate_ledger_audit(ledger: dict[str, Any], *, account: str) -> None:
    if ledger.get("account_id") != account:
        raise ValueError("post-cycle PAPER ledger account binding mismatch")
    if ledger.get("passing") is not True:
        raise ValueError("post-cycle PAPER ledger audit is not passing")
    failures = ledger.get("position_failures")
    if not isinstance(failures, int) or isinstance(failures, bool) or failures != 0:
        raise ValueError("post-cycle PAPER ledger position failures detected")
    reasons = ledger.get("reasons")
    if not isinstance(reasons, list) or reasons:
        raise ValueError("post-cycle PAPER ledger audit reasons are non-empty")


def validate_post_cycle_audit(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("post-cycle audit must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"post_cycle_audit_sha256"}:
        raise ValueError("post-cycle audit schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported post-cycle audit format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected post-cycle audit type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("post-cycle audit lineage mismatch")

    for field in (
        "one_cycle_receipt_sha256",
        "execution_gate_sha256",
        "authorization_request_sha256",
        "signed_authorization_verification_sha256",
        "saved_post_mutation_audit_sha256",
        "fresh_post_mutation_audit_sha256",
        "cycle_report_sha256",
        "account_snapshot_sha256",
        "ledger_audit_sha256",
        "post_cycle_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"post-cycle audit {field} is invalid")

    for field in (
        "production_repository",
        "paper_database_path",
        "account",
        "run_id",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"post-cycle audit {field} is invalid")

    snapshot = report.get("account_snapshot")
    ledger = report.get("ledger_audit")
    if not isinstance(snapshot, dict) or not isinstance(ledger, dict):
        raise ValueError("post-cycle audit PAPER inspection is invalid")
    if report["account_snapshot_sha256"] != hashlib.sha256(
        _canonical_bytes(snapshot)
    ).hexdigest():
        raise ValueError("post-cycle audit account snapshot digest mismatch")
    if report["ledger_audit_sha256"] != hashlib.sha256(
        _canonical_bytes(ledger)
    ).hexdigest():
        raise ValueError("post-cycle audit ledger digest mismatch")
    _validate_account_snapshot(snapshot, account=report["account"])
    _validate_ledger_audit(ledger, account=report["account"])

    for field in (
        "cycle_receipt_safe",
        "fresh_post_mutation_audit_matches_saved",
        "activation_state_unchanged",
        "paper_account_readable",
        "paper_ledger_passing",
        "opened_position_state_consistent",
        "post_cycle_audit_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(f"post-cycle audit requires {field}=true")

    for field in (
        "paper_timer_enable_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "paper_database_modified_by_audit",
    ):
        if report.get(field) is not False:
            raise ValueError(f"post-cycle audit requires {field}=false")

    if report["saved_post_mutation_audit_sha256"] != report[
        "fresh_post_mutation_audit_sha256"
    ]:
        raise ValueError("post-cycle activation-state audit digest mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["post_cycle_audit_sha256"] != expected:
        raise ValueError("post-cycle audit digest mismatch")


def build_post_cycle_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    private_bundle_report_path: str | Path,
    pre_mutation_handoff_path: str | Path,
    execution_precheck_path: str | Path,
    mutation_receipt_path: str | Path,
    saved_post_mutation_audit_path: str | Path,
    one_cycle_receipt_path: str | Path,
) -> dict[str, Any]:
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

    executor_module, post_mutation_module = _load_reviewed_modules(source)
    receipt = _load_json(
        one_cycle_receipt_path,
        label="one-cycle PAPER execution receipt",
    )
    saved_post_mutation = _load_json(
        saved_post_mutation_audit_path,
        label="saved preserved post-mutation audit",
    )
    executor_module.validate_execution_receipt(receipt)
    post_mutation_module.validate_post_mutation_audit(saved_post_mutation)

    if receipt.get("one_cycle_execution_completed") is not True:
        raise ValueError("one-cycle PAPER receipt is incomplete")
    if receipt.get("requires_post_cycle_audit") is not True:
        raise ValueError("one-cycle PAPER receipt does not require post-cycle audit")
    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError("post-cycle receipt repository binding mismatch")
    if receipt.get("production_source_file_modified") is not False:
        raise ValueError("post-cycle receipt reports source-file mutation")
    if receipt.get("paper_timer_action_performed") is not False:
        raise ValueError("post-cycle receipt reports timer action")
    if receipt.get("live_capital_used") is not False:
        raise ValueError("post-cycle receipt reports live-capital use")

    fresh_post_mutation = post_mutation_module.build_post_mutation_audit(
        repository=production,
        source_tree=source,
        private_bundle_report_path=private_bundle_report_path,
        pre_mutation_handoff_path=pre_mutation_handoff_path,
        execution_precheck_path=execution_precheck_path,
        mutation_receipt_path=mutation_receipt_path,
    )
    post_mutation_module.validate_post_mutation_audit(fresh_post_mutation)
    if fresh_post_mutation != saved_post_mutation:
        raise ValueError(
            "post-cycle activation-state audit differs from pre-cycle saved audit"
        )
    if fresh_post_mutation.get("activation_state_unchanged") is not True:
        raise ValueError("post-cycle activation state changed")
    if fresh_post_mutation.get("activation_observed") is not False:
        raise ValueError("post-cycle activation was observed")

    database = _paper_database(
        production,
        str(receipt["paper_database_path"]),
    )
    snapshot, ledger = _inspect_paper_db(
        source=source,
        database=database,
        account=str(receipt["account"]),
    )
    _validate_account_snapshot(snapshot, account=str(receipt["account"]))
    _validate_ledger_audit(ledger, account=str(receipt["account"]))

    opened = receipt.get("positions_opened")
    open_positions = snapshot.get("open_positions")
    opened_consistent = bool(
        isinstance(opened, int)
        and not isinstance(opened, bool)
        and isinstance(open_positions, int)
        and not isinstance(open_positions, bool)
        and open_positions >= opened
    )
    if not opened_consistent:
        raise ValueError("post-cycle opened-position state is inconsistent")

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
        "one_cycle_receipt_sha256": receipt["receipt_sha256"],
        "execution_gate_sha256": receipt["execution_gate_sha256"],
        "authorization_request_sha256": receipt[
            "authorization_request_sha256"
        ],
        "signed_authorization_verification_sha256": receipt[
            "signed_authorization_verification_sha256"
        ],
        "saved_post_mutation_audit_sha256": saved_post_mutation[
            "post_mutation_audit_sha256"
        ],
        "fresh_post_mutation_audit_sha256": fresh_post_mutation[
            "post_mutation_audit_sha256"
        ],
        "production_repository": str(production),
        "paper_database_path": str(database),
        "account": receipt["account"],
        "run_id": receipt["run_id"],
        "cycle_report_sha256": receipt["cycle_report_sha256"],
        "account_snapshot": snapshot,
        "account_snapshot_sha256": hashlib.sha256(
            _canonical_bytes(snapshot)
        ).hexdigest(),
        "ledger_audit": ledger,
        "ledger_audit_sha256": hashlib.sha256(
            _canonical_bytes(ledger)
        ).hexdigest(),
        "cycle_receipt_safe": True,
        "fresh_post_mutation_audit_matches_saved": True,
        "activation_state_unchanged": True,
        "paper_account_readable": True,
        "paper_ledger_passing": True,
        "opened_position_state_consistent": True,
        "post_cycle_audit_ready": True,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "paper_database_modified_by_audit": False,
    }
    report = {
        **identity,
        "post_cycle_audit_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_post_cycle_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit one completed bounded manual market/PAPER cycle. The tool "
            "rechecks that activation state remains unchanged, reads the PAPER "
            "account and ledger with exact reviewed code, and emits read-only "
            "evidence. It never starts services/timers, signs/submits "
            "transactions, changes source/Git state, or uses live capital."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    parser.add_argument("--pre-mutation-handoff", required=True)
    parser.add_argument("--execution-precheck", required=True)
    parser.add_argument("--mutation-receipt", required=True)
    parser.add_argument("--saved-post-mutation-audit", required=True)
    parser.add_argument("--one-cycle-receipt", required=True)
    args = parser.parse_args()

    report = build_post_cycle_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        private_bundle_report_path=args.private_bundle_report,
        pre_mutation_handoff_path=args.pre_mutation_handoff,
        execution_precheck_path=args.execution_precheck,
        mutation_receipt_path=args.mutation_receipt,
        saved_post_mutation_audit_path=args.saved_post_mutation_audit,
        one_cycle_receipt_path=args.one_cycle_receipt,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
