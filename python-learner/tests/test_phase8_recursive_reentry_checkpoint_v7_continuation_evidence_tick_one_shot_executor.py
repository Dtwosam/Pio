from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "run_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "run_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_once",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_hash(value) -> str:
    return hashlib.sha256(
        MODULE._canonical_bytes(value)
    ).hexdigest()


def _seed_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE paper_runs(
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                items_total INTEGER NOT NULL,
                items_applied INTEGER NOT NULL,
                items_skipped INTEGER NOT NULL,
                items_failed INTEGER NOT NULL
            );
            CREATE TABLE paper_run_items(
                run_id TEXT NOT NULL,
                position_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _readiness(database: Path) -> dict:
    items = [
        {
            "position_id": "p8-pair-4-incumbent",
            "token_y_quote_per_atomic": 0.000001,
            "quote_max_age_seconds": 300,
            "emergency_exit": False,
            "estimated_exit_cost_quote": 0.0,
            "rebalance_cost_quote": None,
        },
        {
            "position_id": "p8-pair-4-challenger",
            "token_y_quote_per_atomic": 0.000001,
            "quote_max_age_seconds": 300,
            "emergency_exit": False,
            "estimated_exit_cost_quote": 0.0,
            "rebalance_cost_quote": None,
        },
    ]
    return {
        "readiness_sha256": "a" * 64,
        "one_pair_evidence_tick_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_pair_scoped_executor": True,
        "pio_database_path": str(database),
        "pio_database_sha256": _sha(database.read_bytes()),
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "saved_request_sha256": "b" * 64,
        "fresh_signed_authorization_verification_sha256": "c" * 64,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-3",
        "pair_id": "pair-4",
        "pool_address": "pool-4",
        "entry_observed_at": "2026-09-30T09:20:00+00:00",
        "source_checkpoint_sha256": "9" * 64,
        "source_post_audit_sha256": "2" * 64,
        "source_execution_receipt_sha256": "3" * 64,
        "source_pair_entry_post_audit_sha256": "4" * 64,
        "pair_entry_request_sha256": "5" * 64,
        "pair_entry_input_verification_sha256": "6" * 64,
        "pair_lineage_sha256": "7" * 64,
        "latest_previous_tick_post_audit_sha256": "8" * 64,
        "source_final_evaluation_sha256": "a" * 64,
        "previous_tick_observed_at": "2026-09-30T09:25:00+00:00",
        "requested_position_ids": [
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ],
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
        "evidence_cycle_id": "phase8-recursive-reentry-checkpoint-v4-continuation:pair-4:abc123",
        "expected_run_id": "expected-run",
        "target_chain_observed_at": "2026-09-30T09:30:00+00:00",
        "pair_cycle_items": items,
        "pair_cycle_items_sha256": _canonical_hash(items),
        "derived_pool_safety_sha256": "d" * 64,
        "pool_safety_config": {
            "min_tvl_usd": 50_000.0,
        },
        "position_management_config": {
            "stop_loss_bps": 500,
        },
    }


def _verification() -> dict:
    return {
        "approval_payload_sha256": "e" * 64,
        "approval_signature_sha256": "f" * 64,
        "allowed_signers_sha256": "1" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "expires_at": "2026-09-30T01:00:00Z",
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@dataclass(frozen=True)
class _Item:
    position_id: str
    token_y_quote_per_atomic: float
    quote_max_age_seconds: int = 300
    emergency_exit: bool = False
    estimated_exit_cost_quote: float = 0.0
    rebalance_cost_quote: float | None = None


class _Storage:
    def __init__(self, path):
        self.path = Path(path)


class _Config:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _BatchReport:
    def __init__(
        self,
        *,
        status: str,
        applied: int,
        failed: int,
    ):
        self.run_id = "expected-run"
        self.observed_at = "2026-09-30T09:30:00+00:00"
        self.status = status
        self.items_total = 2
        self.items_applied = applied
        self.items_skipped = 0
        self.items_failed = failed
        self.reused_existing_run = False

    def to_record(self):
        return {
            "run_id": self.run_id,
            "observed_at": self.observed_at,
            "status": self.status,
            "items_total": self.items_total,
            "items_applied": self.items_applied,
            "items_skipped": self.items_skipped,
            "items_failed": self.items_failed,
            "reused_existing_run": False,
            "items": [],
        }


class _Group:
    def __init__(self, report):
        self.observed_at = "2026-09-30T09:30:00+00:00"
        self.run_id = "expected-run"
        self.positions = (
            "p8-pair-4-challenger",
            "p8-pair-4-incumbent",
        )
        self.report = report

    def to_record(self):
        return {
            "observed_at": self.observed_at,
            "run_id": self.run_id,
            "positions": list(self.positions),
            "report": self.report.to_record(),
        }


class _Result:
    def __init__(self, *, status: str, applied: int, failed: int):
        report = _BatchReport(
            status=status,
            applied=applied,
            failed=failed,
        )
        self.cycle_id = "phase8-recursive-reentry-checkpoint-v4-continuation:pair-4:abc123"
        self.positions_requested = 2
        self.groups = 1
        self.applied = applied
        self.failed = failed
        self.details = (_Group(report),)

    def to_record(self):
        return {
            "cycle_id": self.cycle_id,
            "positions_requested": self.positions_requested,
            "groups": self.groups,
            "applied": self.applied,
            "failed": self.failed,
            "details": [
                item.to_record()
                for item in self.details
            ],
        }


def _build(
    monkeypatch,
    *,
    partial: bool = False,
    readiness_drift: bool = False,
    item_drift: bool = False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed_database(database)

    readiness = _readiness(database)
    fresh = copy.deepcopy(readiness)
    if readiness_drift:
        fresh["target_chain_observed_at"] = (
            "2026-09-30T09:31:00+00:00"
        )
    if item_drift:
        readiness["pair_cycle_items"][1][
            "token_y_quote_per_atomic"
        ] = 0.000002
        fresh["pair_cycle_items"][1][
            "token_y_quote_per_atomic"
        ] = 0.000002

    readiness_path = _write(root / "readiness.json", readiness)
    verification_path = _write(
        root / "verification.json",
        _verification(),
    )
    for name in (
        "checkpoint.json",
        "continuation.json",
        "request.json",
        "payload.json",
    ):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    calls = {"runs": 0, "items": None}

    class FakeContinuation:
        @staticmethod
        def _database_path(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(db):
            return {
                "database": _sha(Path(db).read_bytes()),
                "wal": None,
                "shm": None,
            }

    class FakeLive:
        PoolSafetyConfig = _Config

    class FakeLatest:
        LatestPaperCycleItem = _Item
        PositionManagementConfig = _Config
        Storage = _Storage

        @staticmethod
        def run_latest_live_paper_cycle(
            storage,
            *,
            cycle_id,
            items,
            safety_config,
            management_config,
            retry_failed,
        ):
            calls["runs"] += 1
            calls["items"] = [asdict(item) for item in items]
            assert cycle_id == "phase8-recursive-reentry-checkpoint-v4-continuation:pair-4:abc123"
            assert retry_failed is False
            status = "FAILED" if partial else "COMPLETE"
            applied = 1 if partial else 2
            failed = 1 if partial else 0
            conn = sqlite3.connect(storage.path)
            try:
                conn.execute(
                    """
                    INSERT INTO paper_runs(
                        run_id, status, items_total,
                        items_applied, items_skipped, items_failed
                    ) VALUES (?, ?, 2, ?, 0, ?)
                    """,
                    ("expected-run", status, applied, failed),
                )
                statuses = (
                    ("p8-pair-4-incumbent", "APPLIED"),
                    (
                        "p8-pair-4-challenger",
                        "FAILED" if partial else "APPLIED",
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO paper_run_items(
                        run_id, position_id, status
                    ) VALUES ('expected-run', ?, ?)
                    """,
                    statuses,
                )
                conn.commit()
            finally:
                conn.close()
            return _Result(
                status=status,
                applied=applied,
                failed=failed,
            )

    class FakeReadiness:
        @staticmethod
        def validate_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_execution_readiness(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_execution_readiness(
            **kwargs,
        ):
            return copy.deepcopy(fresh)

        @staticmethod
        def _load_reviewed(source):
            return (
                FakeContinuation,
                object(),
                object(),
                FakeLive,
                FakeLatest,
            )

    monkeypatch.setattr(
        MODULE,
        "_load_readiness",
        lambda source: FakeReadiness,
    )
    monkeypatch.setattr(
        MODULE,
        "LOCK_PATH",
        root / "paired-paper-tick.lock",
    )

    def run():
        return MODULE.execute_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_once(
            repository=production,
            source_tree=ROOT,
            saved_execution_readiness_path=readiness_path,
            checkpoint_path=root / "checkpoint.json",
            saved_continuation_readiness_path=root / "continuation.json",
            request_path=root / "request.json",
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="1" * 64,
            now="2026-09-30T09:31:30Z",
        )

    return temp, database, calls, run


def _reseal(receipt: dict) -> None:
    identity = {
        field: receipt[field]
        for field in MODULE.RECEIPT_FIELDS
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_readiness_is_exactly_pinned():
    path = ROOT / MODULE.READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.READINESS_TOOL
    ]


def test_complete_pair_tick_uses_exact_two_items(monkeypatch):
    temp, _, calls, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert calls["runs"] == 1
    assert [item["position_id"] for item in calls["items"]] == [
        "p8-pair-4-incumbent",
        "p8-pair-4-challenger",
    ]
    assert receipt["source_checkpoint_sha256"] == "9" * 64
    assert receipt["source_post_audit_sha256"] == "2" * 64
    assert receipt["source_execution_receipt_sha256"] == "3" * 64
    assert receipt["source_pair_entry_post_audit_sha256"] == "4" * 64
    assert receipt["pair_entry_request_sha256"] == "5" * 64
    assert receipt["pair_entry_input_verification_sha256"] == "6" * 64
    assert receipt["pair_lineage_sha256"] == "7" * 64
    assert receipt["latest_previous_tick_post_audit_sha256"] == "8" * 64
    assert receipt["source_final_evaluation_sha256"] == "a" * 64
    assert receipt["previous_pair_id"] == "pair-3"
    assert receipt["pair_id"] == "pair-4"
    assert receipt["previous_tick_observed_at"] == (
        "2026-09-30T09:25:00+00:00"
    )
    assert receipt["tick_status"] == "COMPLETE"
    assert receipt["positions_requested"] == 2
    assert receipt["groups"] == 1
    assert receipt["items_total"] == 2
    assert receipt["items_applied"] == 2
    assert receipt["items_skipped"] == 0
    assert receipt["items_failed"] == 0
    assert receipt["run_reused_existing"] is False
    assert receipt["pair_tick_complete"] is True
    assert receipt["pair_tick_partial_failure"] is False
    assert receipt["paper_supervisor_tick_authorized"] is True
    assert receipt["paper_supervisor_tick_executed"] is True
    assert receipt["requires_post_tick_audit"] is True
    assert receipt["paper_evidence_collection_authorized"] is False
    assert receipt["paper_trading_authorized"] is False
    assert receipt["live_submit_authorized"] is False
    assert receipt["transaction_submission_performed"] is False
    assert receipt["new_live_capital_used"] is False
    assert receipt["continuous_promotion_authorized"] is False
    assert receipt["phase8_promotion_authorized"] is False


def test_partial_pair_tick_still_emits_auditable_receipt(monkeypatch):
    temp, _, calls, run = _build(monkeypatch, partial=True)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert calls["runs"] == 1
    assert receipt["tick_status"] == "FAILED"
    assert receipt["items_applied"] == 1
    assert receipt["items_failed"] == 1
    assert receipt["pair_tick_complete"] is False
    assert receipt["pair_tick_partial_failure"] is True
    assert receipt["requires_post_tick_audit"] is True
    MODULE.validate_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_execution_receipt(
        receipt
    )


def test_fresh_readiness_drift_fails_before_runner(monkeypatch):
    temp, _, calls, run = _build(
        monkeypatch,
        readiness_drift=True,
    )
    try:
        with pytest.raises(ValueError, match="differs from saved"):
            run()
    finally:
        temp.cleanup()

    assert calls["runs"] == 0


def test_item_digest_drift_fails_before_runner(monkeypatch):
    temp, _, calls, run = _build(
        monkeypatch,
        item_drift=True,
    )
    try:
        with pytest.raises(ValueError, match="item digest changed"):
            run()
    finally:
        temp.cleanup()

    assert calls["runs"] == 0


def test_resealed_receipt_cannot_authorize_ongoing_evidence(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["paper_evidence_collection_authorized"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="paper_evidence_collection_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_execution_receipt(
            receipt
        )


def test_resealed_receipt_cannot_claim_live_submission(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["transaction_submission_performed"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="transaction_submission_performed=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_execution_receipt(
            receipt
        )


def test_resealed_receipt_cannot_authorize_promotion(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["continuous_promotion_authorized"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="continuous_promotion_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_execution_receipt(
            receipt
        )


def test_receipt_rejects_inconsistent_completion_flag(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["pair_tick_complete"] = False
    receipt["pair_tick_partial_failure"] = True
    _reseal(receipt)
    with pytest.raises(ValueError, match="completion flag mismatch"):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v7_continuation_evidence_tick_execution_receipt(
            receipt
        )


def test_executor_uses_dedicated_lock():
    assert MODULE.LOCK_PATH == Path(
        "/var/tmp/pio-phase8-recursive-reentry-checkpoint-v5-continuation-evidence-tick-one-shot.lock"
    )


def test_executor_has_no_scheduler_or_live_submit_path():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_scheduled_paper_tick(" not in source
    assert "run_paper_tick(" not in source
    assert "run_paper_supervisor(" not in source
    assert "refresh_paper_market_state(" not in source
    assert "refresh_paper_chain_state(" not in source
    assert "refresh_open_paper_jupiter_quotes(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "run_latest_live_paper_cycle(" in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"transaction_submission_performed": False' in source
