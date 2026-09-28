from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

import pytest

from meteora_learner.storage import Storage


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "check_phase6_prelive_evidence_status.py"

SPEC = importlib.util.spec_from_file_location(
    "check_phase6_prelive_evidence_status",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _phase6_result(*, ready: bool = False) -> dict:
    if ready:
        return {
            "phase5_promoted": True,
            "execution_db": "/var/lib/pio/execution.db",
            "passed_enter_intents": 10,
            "distinct_pools": 2,
            "blocked_intents": 2,
            "postsimulation_intents": 0,
            "invalid_passed_intents": 0,
            "distinct_authorized_wallets": ["wallet-1"],
            "criteria": copy.deepcopy(MODULE.EXPECTED_CRITERIA),
            "promotion_ready": True,
            "reasons": [],
        }
    return {
        "phase5_promoted": True,
        "execution_db": "/var/lib/pio/execution.db",
        "passed_enter_intents": 2,
        "distinct_pools": 1,
        "blocked_intents": 0,
        "postsimulation_intents": 0,
        "invalid_passed_intents": 0,
        "distinct_authorized_wallets": ["wallet-1"],
        "criteria": copy.deepcopy(MODULE.EXPECTED_CRITERIA),
        "promotion_ready": False,
        "reasons": [
            "validated LIVE ENTER intents 2 are below 10",
            "distinct validated pools 1 are below 2",
            "blocked-path intents 0 are below 2",
        ],
    }


def _report(*, ready: bool = False) -> dict:
    nested = _phase6_result(ready=ready)
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
        "phase5_post_promotion_audit_sha256": "1" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "execution_database_path": "/var/lib/pio/execution.db",
        "pio_database_sha256_before": "2" * 64,
        "pio_database_sha256_after": "2" * 64,
        "pio_wal_sha256_before": None,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_before": None,
        "pio_shm_sha256_after": None,
        "execution_database_sha256_before": "3" * 64,
        "execution_database_sha256_after": "3" * 64,
        "execution_wal_sha256_before": None,
        "execution_wal_sha256_after": None,
        "execution_shm_sha256_before": None,
        "execution_shm_sha256_after": None,
        "snapshot_only": True,
        "source_databases_unchanged": True,
        "phase5_promotion_confirmed": True,
        "phase6_criteria": copy.deepcopy(MODULE.EXPECTED_CRITERIA),
        "phase6_criteria_sha256": hashlib.sha256(
            MODULE._canonical_bytes(MODULE.EXPECTED_CRITERIA)
        ).hexdigest(),
        "phase6_report": nested,
        "phase6_report_sha256": hashlib.sha256(
            MODULE._canonical_bytes(nested)
        ).hexdigest(),
        "phase5_promoted": nested["phase5_promoted"],
        "passed_enter_intents": nested["passed_enter_intents"],
        "distinct_pools": nested["distinct_pools"],
        "blocked_intents": nested["blocked_intents"],
        "postsimulation_intents": nested["postsimulation_intents"],
        "invalid_passed_intents": nested["invalid_passed_intents"],
        "distinct_authorized_wallets": nested["distinct_authorized_wallets"],
        "phase6_promotion_ready": nested["promotion_ready"],
        "phase6_reasons": nested["reasons"],
        "phase6_evidence_status_ready": True,
        "requires_additional_phase6_evidence": not ready,
        "requires_separate_phase6_promotion_action": True,
        "phase6_promotion_persisted": False,
        "phase6_promotion_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "production_execution_database_modified": False,
    }
    return {
        **identity,
        "phase6_evidence_status_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["phase6_evidence_status_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _create_execution_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE execution_intents (
                decision_id TEXT PRIMARY KEY,
                created_at_unix INTEGER NOT NULL,
                mode TEXT NOT NULL,
                action TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                status TEXT NOT NULL,
                risk_json TEXT,
                transaction_guard_json TEXT,
                wallet_authorization_json TEXT,
                prepared_transaction_json TEXT,
                final_simulation_json TEXT
            );
            """
        )
        wallet = "wallet-1"
        for index in range(10):
            pool = "pool-a" if index < 5 else "pool-b"
            conn.execute(
                """
                INSERT INTO execution_intents(
                    decision_id, created_at_unix, mode, action, pool_address,
                    status, risk_json, transaction_guard_json,
                    wallet_authorization_json, prepared_transaction_json,
                    final_simulation_json
                ) VALUES (?, ?, 'LIVE', 'ENTER', ?, 'SIMULATION_PASSED',
                          ?, ?, ?, ?, ?)
                """,
                (
                    f"passed-{index}",
                    index,
                    pool,
                    json.dumps({"accepted": True}),
                    json.dumps({"accepted": True, "fee_payer": wallet}),
                    json.dumps(
                        {
                            "accepted": True,
                            "wallet_pubkey": wallet,
                            "transaction_fee_payer": wallet,
                        }
                    ),
                    json.dumps({"signatures_all_default": True}),
                    json.dumps({"succeeded": True}),
                ),
            )
        for index in range(2):
            conn.execute(
                """
                INSERT INTO execution_intents(
                    decision_id, created_at_unix, mode, action, pool_address,
                    status, risk_json, transaction_guard_json,
                    wallet_authorization_json, prepared_transaction_json,
                    final_simulation_json
                ) VALUES (?, ?, 'LIVE', 'ENTER', ?, 'REJECTED',
                          NULL, NULL, NULL, NULL, NULL)
                """,
                (f"blocked-{index}", 100 + index, f"pool-blocked-{index}"),
            )
        conn.commit()
    finally:
        conn.close()


def _create_pio_db(path: Path) -> None:
    storage = Storage(path)
    storage.save_phase_promotion_evidence(
        phase_name="PHASE5",
        evidence_type="PHASE5_PROMOTION_V1",
        qualified=True,
        evidence={"promotion_ready": True},
    )


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_not_ready_status_exposes_blockers_without_authorizing_live():
    report = _report(ready=False)

    MODULE.validate_phase6_evidence_status(copy.deepcopy(report))

    assert report["phase6_promotion_ready"] is False
    assert report["requires_additional_phase6_evidence"] is True
    assert report["phase6_reasons"]
    assert report["phase6_promotion_persisted"] is False
    assert report["live_submit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_ready_status_still_does_not_persist_or_authorize_live():
    report = _report(ready=True)

    MODULE.validate_phase6_evidence_status(copy.deepcopy(report))

    assert report["phase6_promotion_ready"] is True
    assert report["requires_additional_phase6_evidence"] is False
    assert report["requires_separate_phase6_promotion_action"] is True
    assert report["phase6_promotion_persisted"] is False
    assert report["phase6_promotion_authorized"] is False
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_resealed_status_cannot_claim_phase6_persisted():
    report = _report(ready=True)
    report["phase6_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase6_promotion_persisted=false"):
        MODULE.validate_phase6_evidence_status(report)


def test_resealed_status_cannot_authorize_live_submit():
    report = _report(ready=True)
    report["live_submit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase6_evidence_status(report)


def test_resealed_status_cannot_redirect_nested_execution_db():
    report = _report(ready=True)
    report["phase6_report"]["execution_db"] = "/tmp/snapshot/execution.db"
    report["phase6_report_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(report["phase6_report"])
    ).hexdigest()
    _reseal(report)

    with pytest.raises(ValueError, match="nested execution database binding mismatch"):
        MODULE.validate_phase6_evidence_status(report)


def test_resealed_status_cannot_lower_phase6_criteria():
    report = _report(ready=False)
    report["phase6_criteria"]["min_passed_enter_intents"] = 1
    report["phase6_criteria_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(report["phase6_criteria"])
    ).hexdigest()
    _reseal(report)

    with pytest.raises(ValueError, match="criteria mismatch"):
        MODULE.validate_phase6_evidence_status(report)


def test_real_phase6_evaluator_ready_corpus_uses_private_snapshots_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pio = root / "pio.db"
        execution = root / "execution.db"
        _create_pio_db(pio)
        _create_execution_db(execution)

        pio_before = pio.read_bytes()
        execution_before = execution.read_bytes()

        snap_root = root / "snapshots"
        snap_root.mkdir()
        pio_snapshot = snap_root / "pio.db"
        execution_snapshot = snap_root / "execution.db"
        MODULE._snapshot_sqlite(pio, pio_snapshot)
        MODULE._snapshot_sqlite(execution, execution_snapshot)

        result = MODULE._evaluate_phase6(
            source=ROOT,
            pio_database=pio_snapshot,
            execution_database=execution_snapshot,
        )

        pio_after = pio.read_bytes()
        execution_after = execution.read_bytes()

    MODULE._validate_phase6_result(result)
    assert result["promotion_ready"] is True
    assert result["passed_enter_intents"] == 10
    assert result["distinct_pools"] == 2
    assert result["blocked_intents"] == 2
    assert result["postsimulation_intents"] == 0
    assert result["invalid_passed_intents"] == 0
    assert result["distinct_authorized_wallets"] == ["wallet-1"]
    assert pio_before == pio_after
    assert execution_before == execution_after


def test_evaluator_rejects_postsimulation_intent():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pio = root / "pio.db"
        execution = root / "execution.db"
        _create_pio_db(pio)
        _create_execution_db(execution)
        conn = sqlite3.connect(execution)
        try:
            conn.execute(
                """
                INSERT INTO execution_intents(
                    decision_id, created_at_unix, mode, action, pool_address,
                    status, risk_json, transaction_guard_json,
                    wallet_authorization_json, prepared_transaction_json,
                    final_simulation_json
                ) VALUES ('sent-1', 999, 'LIVE', 'ENTER', 'pool-a', 'SENT',
                          NULL, NULL, NULL, NULL, NULL)
                """
            )
            conn.commit()
        finally:
            conn.close()

        snap_root = root / "snapshots"
        snap_root.mkdir()
        pio_snapshot = snap_root / "pio.db"
        execution_snapshot = snap_root / "execution.db"
        MODULE._snapshot_sqlite(pio, pio_snapshot)
        MODULE._snapshot_sqlite(execution, execution_snapshot)

        result = MODULE._evaluate_phase6(
            source=ROOT,
            pio_database=pio_snapshot,
            execution_database=execution_snapshot,
        )

    MODULE._validate_phase6_result(result)
    assert result["promotion_ready"] is False
    assert result["postsimulation_intents"] == 1
    assert any("post-simulation" in reason for reason in result["reasons"])


def test_builder_binds_confirmed_phase5_and_source_hashes(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        pio = production / "data" / "pio.db"
        execution = root / "execution.db"
        sqlite3.connect(pio).close()
        sqlite3.connect(execution).close()

        audit = {
            "post_promotion_audit_sha256": "1" * 64,
            "post_promotion_audit_ready": True,
            "phase5_promotion_confirmed": True,
            "phase5_promotion_persisted": True,
            "requires_separate_phase6_workflow": True,
            "production_repository": str(production),
            "paper_database_path": str(pio),
            "paper_timer_enable_authorized": False,
            "paper_collection_start_authorized": False,
            "service_restart_authorized": False,
            "detector_cursor_movement_authorized": False,
            "new_market_entry_authorized": False,
            "transaction_signing_authorized": False,
            "transaction_submission_authorized": False,
            "live_capital_authorized": False,
        }
        audit_path = root / "audit.json"
        audit_path.write_text(json.dumps(audit), encoding="utf-8")

        class FakeAudit:
            @staticmethod
            def validate_post_promotion_audit(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_verify_reviewed_source",
            lambda source: FakeAudit,
        )
        monkeypatch.setattr(
            MODULE,
            "_evaluate_phase6",
            lambda **kwargs: _phase6_result(ready=False),
        )

        report = MODULE.build_phase6_evidence_status(
            repository=production,
            source_tree=ROOT,
            phase5_post_promotion_audit_path=audit_path,
            execution_database_path=execution,
        )

    MODULE.validate_phase6_evidence_status(report)
    assert report["phase5_promotion_confirmed"] is True
    assert report["source_databases_unchanged"] is True
    assert report["phase6_promotion_ready"] is False
    assert (
        report["phase6_report"]["execution_db"]
        == report["execution_database_path"]
    )
    assert report["phase6_promotion_persisted"] is False


def test_phase6_status_tool_has_no_promotion_or_live_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase6_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert "live-submit" not in source
    assert "systemctl" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase6_promotion_persisted": False' in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
