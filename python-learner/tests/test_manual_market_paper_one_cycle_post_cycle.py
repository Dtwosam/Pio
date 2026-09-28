from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest

from meteora_learner.paper_account import create_paper_account
from meteora_learner.storage import Storage


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_manual_market_paper_one_cycle_post_cycle.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_one_cycle_post_cycle",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _snapshot(*, open_positions: int = 1) -> dict:
    return {
        "account_id": "pio-proof-1",
        "starting_equity_quote": 1000.0,
        "cash_quote": 900.0,
        "open_position_mark_quote": 100.0,
        "open_fee_income_quote": 0.0,
        "open_reward_income_quote": 0.0,
        "account_equity_quote": 1000.0,
        "high_water_equity_quote": 1000.0,
        "drawdown_bps": 0,
        "open_positions": open_positions,
        "closed_positions": 0,
        "realized_pnl_quote": 0.0,
    }


def _ledger(*, passing: bool = True) -> dict:
    return {
        "account_id": "pio-proof-1",
        "passing": passing,
        "starting_equity_quote": 1000.0,
        "stored_cash_quote": 900.0,
        "event_derived_cash_quote": 900.0,
        "cash_drift_quote": 0.0,
        "positions_checked": 1,
        "position_failures": 0 if passing else 1,
        "positions": [],
        "reasons": [] if passing else ["DRIFT"],
    }


def _report() -> dict:
    snapshot = _snapshot()
    ledger = _ledger()
    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                MODULE.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "one_cycle_receipt_sha256": "1" * 64,
        "execution_gate_sha256": "2" * 64,
        "authorization_request_sha256": "3" * 64,
        "signed_authorization_verification_sha256": "4" * 64,
        "saved_post_mutation_audit_sha256": "5" * 64,
        "fresh_post_mutation_audit_sha256": "5" * 64,
        "production_repository": "/opt/pio",
        "paper_database_path": "/opt/pio/data/pio.db",
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "cycle_report_sha256": "6" * 64,
        "account_snapshot": snapshot,
        "account_snapshot_sha256": hashlib.sha256(
            MODULE._canonical_bytes(snapshot)
        ).hexdigest(),
        "ledger_audit": ledger,
        "ledger_audit_sha256": hashlib.sha256(
            MODULE._canonical_bytes(ledger)
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
    return {
        **identity,
        "post_cycle_audit_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _receipt(production: Path) -> dict:
    return {
        "receipt_sha256": "1" * 64,
        "execution_gate_sha256": "2" * 64,
        "authorization_request_sha256": "3" * 64,
        "signed_authorization_verification_sha256": "4" * 64,
        "production_repository": str(production),
        "paper_database_path": str(production / "data" / "pio.db"),
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "cycle_report_sha256": "6" * 64,
        "positions_opened": 1,
        "one_cycle_execution_completed": True,
        "requires_post_cycle_audit": True,
        "production_source_file_modified": False,
        "paper_timer_action_performed": False,
        "live_capital_used": False,
    }


def _post_mutation() -> dict:
    return {
        "post_mutation_audit_sha256": "5" * 64,
        "activation_state_unchanged": True,
        "activation_observed": False,
    }


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _production(root: Path) -> Path:
    production = root / "production"
    (production / "data").mkdir(parents=True)
    (production / "data" / "pio.db").write_bytes(b"sqlite-placeholder")
    return production


def _build_with_fakes(
    monkeypatch,
    *,
    fresh_mutator=None,
    snapshot=None,
    ledger=None,
    receipt_mutator=None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = _production(root)
    receipt = _receipt(production)
    if receipt_mutator:
        receipt_mutator(receipt)
    saved_post = _post_mutation()
    fresh_post = copy.deepcopy(saved_post)
    if fresh_mutator:
        fresh_mutator(fresh_post)

    class FakeExecutor:
        @staticmethod
        def validate_execution_receipt(value):
            assert isinstance(value, dict)

    class FakePostMutation:
        @staticmethod
        def validate_post_mutation_audit(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_post_mutation_audit(**kwargs):
            return copy.deepcopy(fresh_post)

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_modules",
        lambda source: (FakeExecutor, FakePostMutation),
    )
    monkeypatch.setattr(
        MODULE,
        "_inspect_paper_db",
        lambda **kwargs: (
            copy.deepcopy(snapshot if snapshot is not None else _snapshot()),
            copy.deepcopy(ledger if ledger is not None else _ledger()),
        ),
    )

    receipt_path = _write(root, "one-cycle-receipt.json", receipt)
    post_path = _write(root, "post-mutation.json", saved_post)

    def run():
        return MODULE.build_post_cycle_audit(
            repository=production,
            source_tree=ROOT,
            private_bundle_report_path=root / "bundle.json",
            pre_mutation_handoff_path=root / "handoff.json",
            execution_precheck_path=root / "precheck.json",
            mutation_receipt_path=root / "mutation-receipt.json",
            saved_post_mutation_audit_path=post_path,
            one_cycle_receipt_path=receipt_path,
        )

    return temp, production, run


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["post_cycle_audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_post_cycle_audit_remains_non_authorizing():
    report = _report()
    MODULE.validate_post_cycle_audit(copy.deepcopy(report))

    assert report["post_cycle_audit_ready"] is True
    assert report["paper_ledger_passing"] is True
    assert report["activation_state_unchanged"] is True
    assert report["paper_timer_enable_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_builder_rechecks_activation_state_and_paper_ledger(monkeypatch):
    temp, _, run = _build_with_fakes(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    MODULE.validate_post_cycle_audit(report)
    assert report["fresh_post_mutation_audit_matches_saved"] is True
    assert report["paper_account_readable"] is True
    assert report["paper_ledger_passing"] is True
    assert report["opened_position_state_consistent"] is True
    assert report["post_cycle_audit_ready"] is True


def test_builder_fails_if_activation_state_audit_drifts(monkeypatch):
    temp, _, run = _build_with_fakes(
        monkeypatch,
        fresh_mutator=lambda value: value.update(
            post_mutation_audit_sha256="9" * 64
        ),
    )
    try:
        with pytest.raises(ValueError, match="differs from pre-cycle saved audit"):
            run()
    finally:
        temp.cleanup()


def test_builder_fails_if_paper_ledger_does_not_pass(monkeypatch):
    temp, _, run = _build_with_fakes(
        monkeypatch,
        ledger=_ledger(passing=False),
    )
    try:
        with pytest.raises(ValueError, match="ledger audit is not passing"):
            run()
    finally:
        temp.cleanup()


def test_builder_fails_if_opened_position_not_reflected(monkeypatch):
    temp, _, run = _build_with_fakes(
        monkeypatch,
        snapshot=_snapshot(open_positions=0),
    )
    try:
        with pytest.raises(ValueError, match="opened-position state is inconsistent"):
            run()
    finally:
        temp.cleanup()


def test_builder_rejects_database_symlink(monkeypatch):
    temp, production, run = _build_with_fakes(monkeypatch)
    root = Path(temp.name)
    database = production / "data" / "pio.db"
    target = root / "other.db"
    target.write_bytes(b"x")
    database.unlink()
    database.symlink_to(target)
    try:
        with pytest.raises(ValueError, match="database must be a regular file"):
            run()
    finally:
        temp.cleanup()


def test_real_paper_inspection_uses_snapshot_without_mutating_source_db():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        storage = Storage(database)
        create_paper_account(
            storage,
            account_id="pio-proof-1",
            starting_cash_quote=1000.0,
        )

        before_db = database.read_bytes()
        wal = Path(str(database) + "-wal")
        before_wal = wal.read_bytes() if wal.exists() else None

        snapshot, ledger = MODULE._inspect_paper_db(
            source=ROOT,
            database=database,
            account="pio-proof-1",
        )

        after_db = database.read_bytes()
        after_wal = wal.read_bytes() if wal.exists() else None

    assert snapshot["account_id"] == "pio-proof-1"
    assert ledger["account_id"] == "pio-proof-1"
    assert ledger["passing"] is True
    assert before_db == after_db
    assert before_wal == after_wal


def test_inspection_helper_initializes_only_private_snapshot():
    helper = MODULE.PAPER_INSPECTION_HELPER

    assert "mode=ro" in helper
    assert "immutable=1" in helper
    assert 'source.execute("PRAGMA query_only=ON")' in helper
    assert "source.backup(destination)" in helper
    assert "TemporaryDirectory" in helper
    assert "Storage(snapshot)" in helper
    assert "Storage(database)" not in helper


def test_resealed_post_cycle_audit_cannot_enable_timer():
    report = _report()
    report["paper_timer_enable_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_post_cycle_audit(report)


def test_post_cycle_audit_has_no_activation_or_write_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "systemctl" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "Storage(database)" not in source
    assert "Storage(snapshot)" in source
    assert "mode=ro" in source
    assert "immutable=1" in source
    assert "source.backup(destination)" in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
    assert '"paper_database_modified_by_audit": False' in source
