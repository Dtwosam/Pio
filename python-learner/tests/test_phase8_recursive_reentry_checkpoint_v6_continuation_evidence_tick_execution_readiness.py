from __future__ import annotations

import copy
from dataclasses import dataclass
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
    / "check_phase8_recursive_reentry_checkpoint_v6_continuation_evidence_tick_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_recursive_reentry_checkpoint_v6_continuation_evidence_tick_execution_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _seed_database(
    path: Path,
    *,
    latest_chain: str = "2026-09-30T09:30:00+00:00",
    valuation: bool = False,
    existing_run: bool = False,
) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE paper_positions(
                position_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                pool_address TEXT NOT NULL
            );
            CREATE TABLE paper_counterfactual_positions(
                position_id TEXT PRIMARY KEY,
                token_y_mint TEXT NOT NULL
            );
            CREATE TABLE chain_pool_snapshots(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE TABLE paper_chain_valuations(
                position_id TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE TABLE paper_runs(
                run_id TEXT PRIMARY KEY
            );
            """
        )
        for position_id in (
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ):
            conn.execute(
                """
                INSERT INTO paper_positions(
                    position_id, status, pool_address
                ) VALUES (?, 'OPEN', 'pool-4')
                """,
                (position_id,),
            )
            conn.execute(
                """
                INSERT INTO paper_counterfactual_positions(
                    position_id, token_y_mint
                ) VALUES (?, 'y-mint')
                """,
                (position_id,),
            )
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(pool_address, observed_at)
            VALUES ('pool-4', ?)
            """,
            (latest_chain,),
        )
        if valuation:
            conn.execute(
                """
                INSERT INTO paper_chain_valuations(position_id, observed_at)
                VALUES (
                    'p8-pair-4-incumbent',
                    '2026-09-30T09:30:00+00:00'
                )
                """
            )
        if existing_run:
            conn.execute(
                "INSERT INTO paper_runs(run_id) VALUES ('expected-run')"
            )
        conn.commit()
    finally:
        conn.close()


def _continuation(production: Path, database: Path) -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "evaluation_as_of": "2026-09-30T09:31:00+00:00",
        "chain_max_age_seconds": 300,
        "quote_max_age_seconds": 300,
        "readiness_status": "READY",
        "continuation_tick_request_ready": True,
        "production_repository": str(production),
        "pio_database_path": str(database),
    }


def _request(database: Path) -> dict:
    return {
        "request_sha256": "b" * 64,
        "source_continuation_supervision_readiness_sha256": "a" * 64,
        "source_checkpoint_sha256": "c" * 64,
        "source_post_audit_sha256": "d" * 64,
        "source_execution_receipt_sha256": "e" * 64,
        "source_pair_entry_post_audit_sha256": "f" * 64,
        "pair_entry_request_sha256": "1" * 64,
        "pair_entry_input_verification_sha256": "2" * 64,
        "pair_lineage_sha256": "3" * 64,
        "latest_previous_tick_post_audit_sha256": "4" * 64,
        "source_final_evaluation_sha256": "5" * 64,
        "pio_database_path": str(database),
        "pio_database_sha256": _sha(database.read_bytes()),
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-3",
        "pair_id": "pair-4",
        "pool_address": "pool-4",
        "entry_observed_at": "2026-09-30T09:20:00+00:00",
        "previous_tick_observed_at": "2026-09-30T09:25:00+00:00",
        "incumbent_position_id": "p8-pair-4-incumbent",
        "challenger_position_id": "p8-pair-4-challenger",
        "requested_position_ids": [
            "p8-pair-4-incumbent",
            "p8-pair-4-challenger",
        ],
        "evidence_cycle_id": "phase8-recursive-reentry-checkpoint-v4-continuation:pair-4:abc123",
        "target_chain_observed_at": "2026-09-30T09:30:00+00:00",
        "evaluation_as_of": "2026-09-30T09:31:00+00:00",
        "chain_max_age_seconds": 300,
        "quote_max_age_seconds": 300,
        "required_quote_mints": ["y-mint"],
        "fresh_quote_map": {"y-mint": 0.000001},
        "quote_statuses_sha256": "d" * 64,
        "pool_safety_config": {
            "min_tvl_usd": 50_000.0,
            "min_volume_24h_usd": 10_000.0,
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
        "retry_failed": False,
        "emergency_exit": False,
        "estimated_exit_cost_quote": 0.0,
        "rebalance_cost_quote": None,
    }


def _verification(request: dict) -> dict:
    return {
        "request_sha256": request["request_sha256"],
        "verification_sha256": "e" * 64,
    }


@dataclass(frozen=True)
class _Item:
    position_id: str
    token_y_quote_per_atomic: float
    quote_max_age_seconds: int = 300
    emergency_exit: bool = False
    estimated_exit_cost_quote: float = 0.0
    rebalance_cost_quote: float | None = None


class _Safety:
    def __init__(self, *, safe: bool):
        self.safe = safe

    def to_record(self):
        return {
            "pool_address": "pool-4",
            "safe": self.safe,
            "reason": None if self.safe else "derived safety rejection",
            "assessment": {
                "pool_address": "pool-4",
                "accepted": self.safe,
            },
        }


def _build(
    monkeypatch,
    *,
    latest_chain: str = "2026-09-30T09:30:00+00:00",
    valuation: bool = False,
    existing_run: bool = False,
    safety_safe: bool = True,
    continuation_drift: bool = False,
    database_drift_during_readiness: bool = False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    _seed_database(
        database,
        latest_chain=latest_chain,
        valuation=valuation,
        existing_run=existing_run,
    )

    continuation = _continuation(production, database)
    fresh_continuation = copy.deepcopy(continuation)
    if continuation_drift:
        fresh_continuation["evaluation_as_of"] = (
            "2026-09-30T09:32:00+00:00"
        )
    request = _request(database)
    verification = _verification(request)

    continuation_path = _write(root / "continuation.json", continuation)
    request_path = _write(root / "request.json", request)
    verification_path = _write(root / "verification.json", verification)
    for name in ("checkpoint.json", "payload.json"):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    class FakeContinuation:
        STATUS_READY = "READY"
        state_calls = 0

        @staticmethod
        def validate_phase8_recursive_reentry_checkpoint_v6_continuation_supervision_readiness(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_recursive_reentry_checkpoint_v6_continuation_supervision_readiness(**kwargs):
            return copy.deepcopy(fresh_continuation)

        @staticmethod
        def _database_path(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(db):
            FakeContinuation.state_calls += 1
            if (
                database_drift_during_readiness
                and FakeContinuation.state_calls > 1
            ):
                return {
                    "database": "9" * 64,
                    "wal": None,
                    "shm": None,
                }
            return {
                "database": _sha(Path(db).read_bytes()),
                "wal": None,
                "shm": None,
            }

    class FakeRequest:
        @staticmethod
        def validate_phase8_recursive_reentry_checkpoint_v6_continuation_evidence_tick_request(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_recursive_reentry_checkpoint_v6_continuation_evidence_tick_request(**kwargs):
            return copy.deepcopy(request)

    class FakeSigner:
        @staticmethod
        def validate_verification(value):
            assert isinstance(value, dict)

        @staticmethod
        def verify_authorization(**kwargs):
            return copy.deepcopy(verification)

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

    class FakeSafetyConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeLive:
        Storage = FakeStorage
        PoolSafetyConfig = FakeSafetyConfig

        @staticmethod
        def assess_live_pool_safety(
            storage,
            *,
            pool_address,
            observed_at,
            config,
        ):
            assert pool_address == "pool-4"
            assert observed_at == "2026-09-30T09:30:00+00:00"
            return _Safety(safe=safety_safe)

    class FakeLatest:
        LatestPaperCycleItem = _Item

        @staticmethod
        def _group_run_id(cycle_id, observed_at):
            return "expected-run"

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (
            FakeContinuation,
            FakeRequest,
            FakeSigner,
            FakeLive,
            FakeLatest,
        ),
    )

    def run():
        return MODULE.build_phase8_recursive_reentry_checkpoint_v6_continuation_evidence_tick_execution_readiness(
            repository=production,
            source_tree=ROOT,
            checkpoint_path=root / "checkpoint.json",
            saved_continuation_readiness_path=continuation_path,
            request_path=request_path,
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="f" * 64,
            now="2026-09-30T00:51:30Z",
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


def test_execution_readiness_binds_exact_pair_and_target(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["previous_pair_id"] == "pair-3"
    assert report["pair_id"] == "pair-4"
    assert report["source_checkpoint_sha256"] == "c" * 64
    assert report["source_post_audit_sha256"] == "d" * 64
    assert report["source_execution_receipt_sha256"] == "e" * 64
    assert report["source_pair_entry_post_audit_sha256"] == "f" * 64
    assert report["pair_entry_request_sha256"] == "1" * 64
    assert report["pair_entry_input_verification_sha256"] == "2" * 64
    assert report["pair_lineage_sha256"] == "3" * 64
    assert report["latest_previous_tick_post_audit_sha256"] == "4" * 64
    assert report["source_final_evaluation_sha256"] == "5" * 64
    assert report["requested_position_ids"] == [
        "p8-pair-4-incumbent",
        "p8-pair-4-challenger",
    ]
    assert [item["position_id"] for item in report["pair_cycle_items"]] == [
        "p8-pair-4-incumbent",
        "p8-pair-4-challenger",
    ]
    assert all(
        item["token_y_quote_per_atomic"] == 0.000001
        for item in report["pair_cycle_items"]
    )
    assert report["expected_run_id"] == "expected-run"
    assert report["existing_target_valuation_count"] == 0
    assert report["existing_run_present"] is False
    assert report["exact_chain_snapshot_verified"] is True
    assert report["exact_quote_map_verified"] is True
    assert report["explicit_position_scope_verified"] is True
    assert report["single_use_target_clear"] is True
    assert report["derived_pool_safety_verified"] is True
    assert report[
        "one_pair_evidence_tick_execution_readiness_ready"
    ] is True
    assert report["readiness_only"] is True
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["paper_supervisor_tick_executed"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_unsafe_derived_pool_still_allows_policy_tick(monkeypatch):
    temp, run = _build(monkeypatch, safety_safe=False)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["derived_pool_safety"]["safe"] is False
    assert report["derived_pool_safety_verified"] is True
    assert report[
        "one_pair_evidence_tick_execution_readiness_ready"
    ] is True


def test_fresh_checkpoint_continuation_drift_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, continuation_drift=True)
    try:
        with pytest.raises(ValueError, match="differs from saved"):
            run()
    finally:
        temp.cleanup()


def test_target_chain_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        latest_chain="2026-09-30T09:31:00+00:00",
    )
    try:
        with pytest.raises(ValueError, match="no longer latest"):
            run()
    finally:
        temp.cleanup()


def test_existing_target_valuation_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, valuation=True)
    try:
        with pytest.raises(ValueError, match="already has valuation"):
            run()
    finally:
        temp.cleanup()


def test_existing_deterministic_run_fails_closed(monkeypatch):
    temp, run = _build(monkeypatch, existing_run=True)
    try:
        with pytest.raises(ValueError, match="run already exists"):
            run()
    finally:
        temp.cleanup()


def test_readiness_database_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        database_drift_during_readiness=True,
    )
    try:
        with pytest.raises(
            ValueError,
            match="readiness changed production database",
        ):
            run()
    finally:
        temp.cleanup()


def test_resealed_readiness_cannot_authorize_tick(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_supervisor_tick_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_supervisor_tick_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v6_continuation_evidence_tick_execution_readiness(
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
        MODULE.validate_phase8_recursive_reentry_checkpoint_v6_continuation_evidence_tick_execution_readiness(
            report
        )


def test_resealed_readiness_cannot_authorize_live_submit(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["live_submit_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="live_submit_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v6_continuation_evidence_tick_execution_readiness(
            report
        )


def test_readiness_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_latest_live_paper_cycle(" not in source
    assert "run_live_chain_paper_batch(" not in source
    assert "apply_paper_chain_valuation(" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_supervisor_tick_executed": False' in source
    assert '"continuous_promotion_authorized": False' in source
    assert '"live_submit_authorized": False' in source
