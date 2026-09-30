from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "run_phase8_paired_ml_paper_recursive_rollover_terminal_settlement_tick_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "run_phase8_paired_ml_paper_recursive_rollover_terminal_settlement_tick_once",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _item() -> dict:
    return {
        "position_id": "p8-pair-2-challenger",
        "token_y_quote_per_atomic": 0.01,
        "quote_max_age_seconds": 300,
        "emergency_exit": False,
        "estimated_exit_cost_quote": 0.0,
        "rebalance_cost_quote": None,
    }


def _readiness(database: Path) -> dict:
    item = _item()
    return {
        "readiness_sha256": "a" * 64,
        "settlement_tick_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_single_position_executor": True,
        "pio_database_path": str(database),
        "pio_database_sha256": _sha(database.read_bytes()),
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "source_terminal_evaluation_sha256": "c" * 64,
        "source_terminal_post_audit_sha256": "d" * 64,
        "source_recursive_rollover_post_audit_sha256": "e" * 64,
        "recursive_rollover_entry_request_sha256": "1" * 64,
        "recursive_rollover_entry_input_verification_sha256": "2" * 64,
        "source_final_evaluation_sha256": "f" * 64,
        "previous_pair_id": "pair-1",
        "pair_id": "pair-2",
        "pool_address": "pool-2",
        "entry_observed_at": "2026-09-30T09:20:00+00:00",
        "terminal_tick_observed_at": "2026-09-30T09:30:00+00:00",
        "incumbent_position_id": "p8-pair-2-incumbent",
        "challenger_position_id": "p8-pair-2-challenger",
        "open_position_id": "p8-pair-2-challenger",
        "closed_position_id": "p8-pair-2-incumbent",
        "open_position_policy_source": "ML_CHALLENGER",
        "open_position_model_id": "challenger-1",
        "requested_position_ids": ["p8-pair-2-challenger"],
        "settlement_cycle_id": "phase8-repeat-rollover-settle:pair-2:abc123",
        "expected_run_id": "phase8-repeat-rollover-settle:pair-2:abc123:def456",
        "target_chain_observed_at": "2026-09-30T09:35:00+00:00",
        "single_cycle_item": item,
        "single_cycle_item_sha256": hashlib.sha256(
            MODULE._canonical_bytes(item)
        ).hexdigest(),
        "derived_pool_safety_sha256": "b" * 64,
        "pool_safety_config": {
            "min_tvl_usd": 50000.0,
            "min_volume_24h_usd": 10000.0,
            "min_pool_age_hours": 24.0,
            "min_chain_observations": 12,
            "max_dynamic_fee_pct": 5.0,
            "max_pool_snapshot_age_seconds": 900,
            "require_standard_spl": True,
            "require_not_blacklisted": True,
        },
        "position_management_config": {
            "stop_loss_bps": 500,
            "take_profit_bps": None,
            "max_rebalances": 3,
            "max_holding_observations": None,
            "proactive_rebalance_buffer_bins": 0,
        },
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "authorization_expires_at": "2026-09-30T09:45:00Z",
    }


def _verification() -> dict:
    return {
        "verification_sha256": "c" * 64,
        "approval_payload_sha256": "d" * 64,
        "approval_signature_sha256": "e" * 64,
        "allowed_signers_sha256": "f" * 64,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@dataclass(frozen=True)
class _LiveItem:
    position_id: str
    token_y_quote_per_atomic: float
    quote_max_age_seconds: int
    emergency_exit: bool
    estimated_exit_cost_quote: float
    rebalance_cost_quote: float | None


@dataclass(frozen=True)
class _RunItem:
    position_id: str
    status: str


class _Result:
    def __init__(
        self,
        *,
        run_id: str,
        observed_at: str,
        reused: bool = False,
        failed: int = 0,
    ):
        self.run_id = run_id
        self.observed_at = observed_at
        self.status = "FAILED" if failed else "COMPLETED"
        self.items_total = 1
        self.items_applied = 0 if failed else 1
        self.items_skipped = 0
        self.items_failed = failed
        self.reused_existing_run = reused
        self.items = (
            _RunItem(
                position_id="p8-pair-2-challenger",
                status="FAILED" if failed else "APPLIED",
            ),
        )

    def to_record(self):
        return {
            "run_id": self.run_id,
            "observed_at": self.observed_at,
            "status": self.status,
            "items_total": self.items_total,
            "items_applied": self.items_applied,
            "items_skipped": self.items_skipped,
            "items_failed": self.items_failed,
            "reused_existing_run": self.reused_existing_run,
            "items": [
                {
                    "position_id": item.position_id,
                    "status": item.status,
                }
                for item in self.items
            ],
        }


def _build(
    monkeypatch,
    *,
    readiness_drift: bool = False,
    reused: bool = False,
    failed: int = 0,
    pair_regression: bool = False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"before")

    readiness = _readiness(database)
    if pair_regression:
        readiness["pair_id"] = readiness["previous_pair_id"]
    fresh = copy.deepcopy(readiness)
    if readiness_drift:
        fresh["expected_run_id"] = "different-run"

    readiness_path = _write(root / "readiness.json", readiness)
    verification_path = _write(
        root / "verification.json",
        _verification(),
    )
    request_path = _write(
        root / "request.json",
        {"request_sha256": "9" * 64},
    )
    for name in (
        "terminal.json",
        "settlement-readiness.json",
        "payload.json",
    ):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    calls = {"runner": 0, "run_id": None, "observed_at": None, "items": None}

    class SettlementReadiness:
        @staticmethod
        def _database_path(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(path):
            path = Path(path)
            return {
                "database": _sha(path.read_bytes()),
                "wal": None,
                "shm": None,
            }

    class Live:
        class Storage:
            def __init__(self, path):
                self.path = Path(path)

        class PoolSafetyConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        LivePaperChainBatchItem = _LiveItem

        @staticmethod
        def run_live_chain_paper_batch(
            storage,
            *,
            run_id,
            observed_at,
            items,
            safety_config,
            management_config,
            retry_failed,
        ):
            calls["runner"] += 1
            calls["run_id"] = run_id
            calls["observed_at"] = observed_at
            calls["items"] = tuple(item.position_id for item in items)
            assert retry_failed is False
            storage.path.write_bytes(b"after")
            return _Result(
                run_id=run_id,
                observed_at=observed_at,
                reused=reused,
                failed=failed,
            )

    class Latest:
        class PositionManagementConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

    class FakeReadiness:
        @staticmethod
        def validate_phase8_paired_ml_paper_recursive_rollover_terminal_settlement_tick_execution_readiness(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paired_ml_paper_recursive_rollover_terminal_settlement_tick_execution_readiness(
            **kwargs,
        ):
            return copy.deepcopy(fresh)

        @staticmethod
        def _load_reviewed(source):
            return (
                SettlementReadiness,
                object(),
                object(),
                Live,
                Latest,
            )

    monkeypatch.setattr(
        MODULE,
        "_load_readiness",
        lambda source: FakeReadiness,
    )
    monkeypatch.setattr(
        MODULE,
        "LOCK_PATH",
        root / "settlement-one-shot.lock",
    )

    def run():
        return MODULE.execute_phase8_paired_ml_paper_recursive_rollover_terminal_settlement_tick_once(
            repository=production,
            source_tree=ROOT,
            saved_execution_readiness_path=readiness_path,
            terminal_evaluation_path=root / "terminal.json",
            saved_settlement_readiness_path=(
                root / "settlement-readiness.json"
            ),
            request_path=request_path,
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="f" * 64,
            now="2026-09-30T09:42:00Z",
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


def test_reviewed_execution_readiness_is_exactly_pinned():
    path = ROOT / MODULE.READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.READINESS_TOOL
    ]


def test_one_shot_executes_exact_single_position_exact_snapshot(monkeypatch):
    temp, _, calls, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert calls["runner"] == 1
    assert receipt["previous_pair_id"] == "pair-1"
    assert receipt["pair_id"] == "pair-2"
    assert receipt["source_recursive_rollover_post_audit_sha256"] == "e" * 64
    assert receipt["recursive_rollover_entry_request_sha256"] == "1" * 64
    assert receipt["recursive_rollover_entry_input_verification_sha256"] == "2" * 64
    assert receipt["source_final_evaluation_sha256"] == "f" * 64
    assert receipt["entry_observed_at"] == "2026-09-30T09:20:00+00:00"
    assert calls["run_id"] == "phase8-repeat-rollover-settle:pair-2:abc123:def456"
    assert calls["observed_at"] == "2026-09-30T09:35:00+00:00"
    assert calls["items"] == ("p8-pair-2-challenger",)
    assert receipt["positions_requested"] == 1
    assert receipt["groups"] == 1
    assert receipt["items_total"] == 1
    assert receipt["items_applied"] == 1
    assert receipt["items_failed"] == 0
    assert receipt["paper_settlement_tick_authorized"] is True
    assert receipt["paper_settlement_tick_executed"] is True
    assert receipt["settlement_tick_complete"] is True
    assert receipt["settlement_tick_partial_failure"] is False
    assert receipt["requires_post_tick_audit"] is True
    assert receipt["paper_trading_authorized"] is False
    assert receipt["live_submit_authorized"] is False
    assert receipt["continuous_promotion_authorized"] is False
    assert receipt["transaction_submission_performed"] is False
    assert receipt["new_live_capital_used"] is False


def test_pair_lineage_regression_fails_before_execution(monkeypatch):
    temp, database, calls, run = _build(
        monkeypatch,
        pair_regression=True,
    )
    try:
        before = database.read_bytes()
        with pytest.raises(ValueError, match="pair id was not advanced"):
            run()
        after = database.read_bytes()
    finally:
        temp.cleanup()

    assert calls["runner"] == 0
    assert before == after


def test_fresh_readiness_drift_fails_before_execution(monkeypatch):
    temp, database, calls, run = _build(
        monkeypatch,
        readiness_drift=True,
    )
    try:
        before = database.read_bytes()
        with pytest.raises(ValueError, match="differs from saved"):
            run()
        after = database.read_bytes()
    finally:
        temp.cleanup()

    assert calls["runner"] == 0
    assert before == after


def test_reused_existing_run_fails_closed(monkeypatch):
    temp, _, calls, run = _build(monkeypatch, reused=True)
    try:
        with pytest.raises(ValueError, match="unexpectedly reused"):
            run()
    finally:
        temp.cleanup()

    assert calls["runner"] == 1


def test_failed_item_is_recorded_as_partial_failure(monkeypatch):
    temp, _, _, run = _build(monkeypatch, failed=1)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert receipt["items_failed"] == 1
    assert receipt["settlement_tick_complete"] is False
    assert receipt["settlement_tick_partial_failure"] is True
    assert receipt["run_item_status"] == "FAILED"


def test_resealed_receipt_cannot_authorize_ongoing_paper(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["paper_trading_authorized"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_terminal_settlement_tick_execution_receipt(
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
        MODULE.validate_phase8_paired_ml_paper_recursive_rollover_terminal_settlement_tick_execution_receipt(
            receipt
        )


def test_executor_uses_dedicated_one_shot_lock():
    assert MODULE.LOCK_PATH == Path(
        "/var/tmp/pio-phase8-recursive-rollover-paired-paper-terminal-settlement-tick-one-shot.lock"
    )


def test_executor_uses_exact_snapshot_batch_not_latest_runner():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_live_chain_paper_batch(" in source
    assert "run_latest_live_paper_cycle(" not in source
    assert 'observed_at=saved_readiness["target_chain_observed_at"]' in source
    assert "send_transaction" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
