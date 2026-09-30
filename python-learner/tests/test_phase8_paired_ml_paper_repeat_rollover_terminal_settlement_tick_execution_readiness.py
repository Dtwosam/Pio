from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_tick_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_tick_execution_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _readiness() -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "evaluation_as_of": "2026-09-30T09:36:00+00:00",
        "chain_max_age_seconds": 300,
        "quote_max_age_seconds": 300,
    }


def _request() -> dict:
    return {
        "request_sha256": "b" * 64,
        "source_terminal_evaluation_sha256": "c" * 64,
        "source_terminal_post_audit_sha256": "d" * 64,
        "source_rollover_post_audit_sha256": "1" * 64,
        "rollover_entry_request_sha256": "2" * 64,
        "rollover_entry_input_verification_sha256": "3" * 64,
        "source_final_evaluation_sha256": "4" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "e" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
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
        "settlement_cycle_id": "phase8-rollover-settle:pair-2:abc123",
        "target_chain_observed_at": "2026-09-30T09:35:00+00:00",
        "quote_max_age_seconds": 300,
        "fresh_quote_map": {"y-mint": 0.01},
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
    }


def _verification() -> dict:
    return {
        "verification_sha256": "f" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "expires_at": "2026-09-30T09:45:00Z",
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class _FakeSettlementReadiness:
    @staticmethod
    def validate_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_readiness(value):
        assert isinstance(value, dict)


class _FakeRequest:
    @staticmethod
    def validate_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_tick_request(value):
        assert isinstance(value, dict)


class _FakeSigner:
    @staticmethod
    def validate_verification(value):
        assert isinstance(value, dict)


@dataclass(frozen=True)
class _FakeItem:
    position_id: str
    token_y_quote_per_atomic: float
    quote_max_age_seconds: int
    emergency_exit: bool
    estimated_exit_cost_quote: float
    rebalance_cost_quote: float | None


@dataclass(frozen=True)
class _FakeSafety:
    pool_address: str
    safe: bool
    reason: str | None
    assessment: object | None


def _build(
    monkeypatch,
    *,
    readiness_drift=False,
    request_drift=False,
    verification_drift=False,
    quote_map=None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    import sqlite3
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            """
            CREATE TABLE paper_counterfactual_positions(
                position_id TEXT PRIMARY KEY,
                token_y_mint TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_counterfactual_positions
            VALUES ('p8-pair-2-challenger', 'y-mint')
            """
        )
        conn.commit()
    finally:
        conn.close()

    readiness = _readiness()
    request = _request()
    request["production_repository"] = str(production)
    request["pio_database_path"] = str(database)
    request["pio_database_sha256"] = hashlib.sha256(
        database.read_bytes()
    ).hexdigest()
    if quote_map is not None:
        request["fresh_quote_map"] = quote_map
    verification = _verification()

    readiness_path = _write(root / "readiness.json", readiness)
    request_path = _write(root / "request.json", request)
    verification_path = _write(root / "verification.json", verification)
    for name in ("terminal.json", "payload.json"):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    fresh_readiness = copy.deepcopy(readiness)
    if readiness_drift:
        fresh_readiness["readiness_sha256"] = "9" * 64
    fresh_request = copy.deepcopy(request)
    if request_drift:
        fresh_request["request_sha256"] = "8" * 64
    fresh_verification = copy.deepcopy(verification)
    if verification_drift:
        fresh_verification["verification_sha256"] = "7" * 64

    class Readiness(_FakeSettlementReadiness):
        @staticmethod
        def build_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_readiness(**kwargs):
            return copy.deepcopy(fresh_readiness)

        @staticmethod
        def _database_path(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(path):
            return {
                "database": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                "wal": None,
                "shm": None,
            }

    class Request(_FakeRequest):
        @staticmethod
        def build_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_tick_request(**kwargs):
            return copy.deepcopy(fresh_request)

    class Signer(_FakeSigner):
        @staticmethod
        def verify_authorization(**kwargs):
            return copy.deepcopy(fresh_verification)

    class Storage:
        def __init__(self, path):
            self.path = Path(path)

    class SafetyConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Live:
        @staticmethod
        def assess_live_pool_safety(
            storage,
            *,
            pool_address,
            observed_at,
            config,
        ):
            return _FakeSafety(
                pool_address=pool_address,
                safe=False,
                reason="fixture unsafe",
                assessment=None,
            )

    Live.Storage = Storage
    Live.PoolSafetyConfig = SafetyConfig

    class Latest:
        LatestPaperCycleItem = _FakeItem

        @staticmethod
        def _group_run_id(cycle_id, observed_at):
            digest = hashlib.sha256(observed_at.encode("utf-8")).hexdigest()[:12]
            return f"{cycle_id}:{digest}"

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (Readiness, Request, Signer, Live, Latest),
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_tick_execution_readiness(
            repository=production,
            source_tree=ROOT,
            terminal_evaluation_path=root / "terminal.json",
            saved_settlement_readiness_path=readiness_path,
            request_path=request_path,
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="6" * 64,
            now="2026-09-30T09:42:00Z",
        )

    return temp, run


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_execution_readiness_binds_single_item_and_derived_safety(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["previous_pair_id"] == "pair-1"
    assert report["pair_id"] == "pair-2"
    assert report["source_rollover_post_audit_sha256"] == "1" * 64
    assert report["rollover_entry_request_sha256"] == "2" * 64
    assert report["rollover_entry_input_verification_sha256"] == "3" * 64
    assert report["source_final_evaluation_sha256"] == "4" * 64
    assert report["requested_position_ids"] == ["p8-pair-2-challenger"]
    assert report["single_cycle_item"]["position_id"] == "p8-pair-2-challenger"
    assert report["single_cycle_item"]["token_y_quote_per_atomic"] == 0.01
    assert report["derived_pool_safety"]["safe"] is False
    assert report["derived_pool_safety_bound"] is True
    assert report["expected_run_id"].startswith(
        "phase8-rollover-settle:pair-2:abc123:"
    )
    assert report["human_repeat_rollover_settlement_tick_authorization_verified"] is True
    assert report["settlement_tick_execution_readiness_ready"] is True
    assert report["readiness_only"] is True
    assert report["paper_settlement_tick_authorized"] is False
    assert report["paper_settlement_tick_executed"] is False
    assert report["live_submit_authorized"] is False


def test_fresh_settlement_readiness_drift_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, readiness_drift=True)
    try:
        with pytest.raises(ValueError, match="readiness differs from saved"):
            run()
    finally:
        temp.cleanup()


def test_fresh_request_drift_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, request_drift=True)
    try:
        with pytest.raises(ValueError, match="request differs from saved"):
            run()
    finally:
        temp.cleanup()


def test_fresh_authorization_drift_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, verification_drift=True)
    try:
        with pytest.raises(
            ValueError,
            match="authorization differs from saved",
        ):
            run()
    finally:
        temp.cleanup()


def test_missing_exact_token_y_quote_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, quote_map={})
    try:
        with pytest.raises(ValueError, match="token-Y quote is unavailable"):
            run()
    finally:
        temp.cleanup()


def test_resealed_readiness_rejects_pair_id_regression(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["pair_id"] = report["previous_pair_id"]
    _reseal(report)
    with pytest.raises(ValueError, match="pair id was not advanced"):
        MODULE.validate_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_tick_execution_readiness(
            report
        )


def test_resealed_readiness_cannot_claim_execution(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_settlement_tick_executed"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_settlement_tick_executed=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_tick_execution_readiness(
            report
        )


def test_resealed_readiness_cannot_authorize_promotion(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["continuous_promotion_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="continuous_promotion_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_repeat_rollover_terminal_settlement_tick_execution_readiness(
            report
        )


def test_execution_readiness_has_no_paper_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "apply_paper_chain_valuation(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_settlement_tick_authorized": False' in source
    assert '"paper_settlement_tick_executed": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
