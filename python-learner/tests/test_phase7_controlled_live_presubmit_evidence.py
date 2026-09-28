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


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_presubmit_evidence.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_presubmit_evidence",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


POOL = "11111111111111111111111111111111"
WALLET = "22222222222222222222222222222222"
DECISION = "11111111-2222-4333-8444-555555555555"


def _proposal() -> dict:
    return {
        "decision_id": DECISION,
        "mode": "LIVE",
        "action": "ENTER",
        "pool_address": POOL,
        "capital_quote": 5.0,
        "account_equity_quote": 1000.0,
        "portfolio_deployed_quote": 0.0,
        "daily_drawdown_pct": 0.25,
        "min_bin_id": -10,
        "max_bin_id": 10,
        "strategy": "SPOT",
        "expected_net_return_pct": 1.0,
        "expected_downside_pct": 0.5,
        "model_version": "baseline-v1",
        "data_age_seconds": 5,
    }


def _saved_readiness(production: Path) -> dict:
    return {
        "readiness_sha256": "1" * 64,
        "authorization_readiness_ready": True,
        "pio_database_path": str(production / "data" / "pio.db"),
        "saved_phase7_evidence_status_sha256": "2" * 64,
        "saved_phase7_evidence_plan_sha256": "3" * 64,
        "saved_input_preflight_sha256": "4" * 64,
        "saved_signed_authorization_verification_sha256": "5" * 64,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
    }


def _preflight() -> dict:
    return {
        "input_preflight_sha256": "4" * 64,
        "proposal": _proposal(),
        "executor_wallet_pubkey": WALLET,
        "controlled_live_config_sha256": "6" * 64,
        "proposal_sha256": "7" * 64,
        "input_manifest_sha256": "8" * 64,
    }


def _create_execution_db(
    path: Path,
    *,
    status: str = "SIMULATION_PASSED",
    signature: str | None = None,
    error: str | None = None,
    risk_accepted: bool = True,
    guard_accepted: bool = True,
    wallet_accepted: bool = True,
    prepared_unsigned: bool = True,
    final_simulation_succeeded: bool = True,
    proposal: dict | None = None,
) -> None:
    proposal_value = copy.deepcopy(proposal if proposal is not None else _proposal())
    request = {
        "request": {
            "proposal": proposal_value,
            "transaction_base64": "dW5zaWduZWQtdHJhbnNhY3Rpb24=",
        },
        "risk_config": {
            "max_capital_per_position_pct": 10.0,
            "max_total_deployed_pct": 50.0,
        },
        "transaction_guard_config": {
            "expected_fee_payer": WALLET,
            "allowed_program_ids": ["11111111111111111111111111111111"],
            "required_accounts": [],
            "require_unsigned": True,
            "require_proposal_pool_account": True,
            "max_instructions": 8,
            "allowed_actions_by_program": {},
        },
    }
    risk = {
        "decision_id": DECISION,
        "mode": "LIVE",
        "action": "ENTER",
        "accepted": risk_accepted,
        "reason": "approved" if risk_accepted else "blocked",
    }
    simulation = {
        "succeeded": True,
        "rpc_context_slot": 100,
        "result": {"err": None},
    }
    guard = {
        "accepted": guard_accepted,
        "reason": "approved" if guard_accepted else "blocked",
        "fee_payer": WALLET,
        "program_ids": [],
        "account_keys": [POOL, WALLET],
        "required_signatures": 1,
        "signatures_all_default": True,
        "instruction_fingerprints": [],
    }
    wallet = {
        "accepted": wallet_accepted,
        "reason": "approved" if wallet_accepted else "blocked",
        "wallet_pubkey": WALLET,
        "transaction_fee_payer": WALLET,
    }
    prepared = {
        "transaction_base64": "cHJlcGFyZWQtdW5zaWduZWQ=",
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 123,
        "rpc_context_slot": 101,
        "signatures_all_default": prepared_unsigned,
    }
    final_simulation = {
        "succeeded": final_simulation_succeeded,
        "rpc_context_slot": 102,
        "result": {"err": None if final_simulation_succeeded else "boom"},
    }

    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE execution_intents (
                decision_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                action TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL,
                risk_json TEXT,
                simulation_json TEXT,
                transaction_guard_json TEXT,
                wallet_authorization_json TEXT,
                prepared_transaction_json TEXT,
                final_simulation_json TEXT,
                signature TEXT,
                error TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO execution_intents(
                decision_id, mode, action, pool_address, request_json, status,
                created_at_unix, updated_at_unix, risk_json, simulation_json,
                transaction_guard_json, wallet_authorization_json,
                prepared_transaction_json, final_simulation_json, signature, error
            ) VALUES (?, 'LIVE', 'ENTER', ?, ?, ?, 1, 2, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                DECISION,
                POOL,
                json.dumps(request, sort_keys=True),
                status,
                json.dumps(risk, sort_keys=True),
                json.dumps(simulation, sort_keys=True),
                json.dumps(guard, sort_keys=True),
                json.dumps(wallet, sort_keys=True),
                json.dumps(prepared, sort_keys=True),
                json.dumps(final_simulation, sort_keys=True),
                signature,
                error,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _report() -> dict:
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
        "saved_authorization_readiness_sha256": "1" * 64,
        "fresh_authorization_readiness_sha256": "1" * 64,
        "expected_authorization_readiness_sha256": "1" * 64,
        "fresh_authorization_readiness_matches_saved": True,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "execution_database_path": "/var/lib/pio/execution.db",
        "execution_database_sha256_before": "a" * 64,
        "execution_database_sha256_after": "a" * 64,
        "execution_wal_sha256_before": None,
        "execution_wal_sha256_after": None,
        "execution_shm_sha256_before": None,
        "execution_shm_sha256_after": None,
        "execution_database_snapshot_only": True,
        "execution_database_unchanged": True,
        "decision_id": DECISION,
        "mode": "LIVE",
        "action": "ENTER",
        "pool_address": POOL,
        "executor_wallet_pubkey": WALLET,
        "phase7_evidence_status_sha256": "2" * 64,
        "phase7_evidence_plan_sha256": "3" * 64,
        "input_preflight_sha256": "4" * 64,
        "signed_authorization_verification_sha256": "5" * 64,
        "controlled_live_config_sha256": "6" * 64,
        "proposal_sha256": "7" * 64,
        "input_manifest_sha256": "8" * 64,
        "execution_request_sha256": "9" * 64,
        "risk_config_sha256": "b" * 64,
        "transaction_guard_config_sha256": "c" * 64,
        "risk_report_sha256": "d" * 64,
        "initial_simulation_sha256": "e" * 64,
        "transaction_guard_sha256": "f" * 64,
        "wallet_authorization_sha256": "0" * 64,
        "prepared_transaction_sha256": "1" * 64,
        "final_simulation_sha256": "2" * 64,
        "execution_intent_snapshot_sha256": "3" * 64,
        "execution_intent_status": "SIMULATION_PASSED",
        "execution_request_matches_preflight_proposal": True,
        "risk_accepted": True,
        "risk_identity_matches_proposal": True,
        "initial_simulation_succeeded": True,
        "transaction_guard_accepted": True,
        "transaction_guard_requires_unsigned": True,
        "transaction_guard_requires_proposal_pool": True,
        "transaction_guard_fee_payer_matches_wallet": True,
        "transaction_guard_single_signature": True,
        "transaction_guard_transaction_unsigned": True,
        "wallet_authorization_accepted": True,
        "wallet_authorization_matches_executor": True,
        "wallet_authorization_fee_payer_matches_executor": True,
        "prepared_transaction_unsigned": True,
        "prepared_transaction_present": True,
        "prepared_recent_blockhash_present": True,
        "prepared_last_valid_block_height": 123,
        "prepared_rpc_context_slot": 101,
        "final_simulation_succeeded": True,
        "final_simulation_rpc_context_slot": 102,
        "signature_absent": True,
        "error_absent": True,
        "presubmit_evidence_ready": True,
        "requires_fresh_blockhash_expiry_recheck": True,
        "requires_live_submit_feature": True,
        "requires_runtime_live_submit_opt_in": True,
        "requires_separate_transaction_execution_gate": True,
        "requires_confirmation_receipt_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "production_execution_database_modified": False,
    }
    return {
        **identity,
        "presubmit_gate_sha256": MODULE._sha256_value(identity),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["presubmit_gate_sha256"] = MODULE._sha256_value(identity)


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_report_still_authorizes_no_signing_submission_or_capital():
    report = _report()

    MODULE.validate_phase7_presubmit_evidence_gate(copy.deepcopy(report))

    assert report["presubmit_evidence_ready"] is True
    assert report["requires_separate_transaction_execution_gate"] is True
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_report_cannot_authorize_signing():
    report = _report()
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="transaction_signing_authorized=false"):
        MODULE.validate_phase7_presubmit_evidence_gate(report)


def test_resealed_report_cannot_authorize_submission():
    report = _report()
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="transaction_submission_authorized=false"):
        MODULE.validate_phase7_presubmit_evidence_gate(report)


def test_resealed_report_cannot_hide_signature_presence():
    report = _report()
    report["signature_absent"] = False
    _reseal(report)

    with pytest.raises(ValueError, match="signature_absent=true"):
        MODULE.validate_phase7_presubmit_evidence_gate(report)


def test_real_intent_snapshot_accepts_only_unsigned_presubmit_state():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "execution.db"
        _create_execution_db(database)

        snapshot = MODULE._load_intent_snapshot(
            database=database,
            decision_id=DECISION,
            expected_proposal=_proposal(),
            executor_wallet_pubkey=WALLET,
        )

    assert snapshot["status"] == "SIMULATION_PASSED"
    assert snapshot["risk"]["accepted"] is True
    assert snapshot["transaction_guard"]["accepted"] is True
    assert snapshot["wallet_authorization"]["accepted"] is True
    assert snapshot["prepared_transaction"]["signatures_all_default"] is True
    assert snapshot["final_simulation"]["succeeded"] is True
    assert snapshot["signature"] is None
    assert snapshot["error"] is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"status": "SIGNING"}, "must remain SIMULATION_PASSED"),
        ({"signature": "sig"}, "already has a transaction signature"),
        ({"error": "boom"}, "already has an error"),
        ({"risk_accepted": False}, "risk report is not accepted"),
        ({"guard_accepted": False}, "transaction guard is not accepted"),
        ({"wallet_accepted": False}, "wallet authorization is not accepted"),
        ({"prepared_unsigned": False}, "prepared transaction is already signed"),
        (
            {"final_simulation_succeeded": False},
            "exact final simulation is not successful",
        ),
    ],
)
def test_real_intent_snapshot_fails_closed_on_execution_drift(kwargs, message):
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "execution.db"
        _create_execution_db(database, **kwargs)

        with pytest.raises(ValueError, match=message):
            MODULE._load_intent_snapshot(
                database=database,
                decision_id=DECISION,
                expected_proposal=_proposal(),
                executor_wallet_pubkey=WALLET,
            )


def test_real_intent_snapshot_rejects_proposal_mismatch():
    changed = _proposal()
    changed["capital_quote"] = 6.0

    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "execution.db"
        _create_execution_db(database, proposal=changed)

        with pytest.raises(ValueError, match="proposal differs from preflight"):
            MODULE._load_intent_snapshot(
                database=database,
                decision_id=DECISION,
                expected_proposal=_proposal(),
                executor_wallet_pubkey=WALLET,
            )


def test_builder_replays_fresh_readiness_and_preserves_execution_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        sqlite3.connect(production / "data" / "pio.db").close()
        execution = root / "execution.db"
        _create_execution_db(execution)

        readiness = _saved_readiness(production)
        preflight = _preflight()

        class FakeReadiness:
            @staticmethod
            def validate_phase7_authorization_readiness(value):
                pass

            @staticmethod
            def build_phase7_authorization_readiness(**kwargs):
                return copy.deepcopy(readiness)

        class FakePreflight:
            @staticmethod
            def validate_phase7_input_preflight(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_verify_reviewed_source",
            lambda source: (FakeReadiness, FakePreflight),
        )

        readiness_path = _write(root, "readiness.json", readiness)
        preflight_path = _write(root, "preflight.json", preflight)
        before = execution.read_bytes()

        report = MODULE.build_phase7_presubmit_evidence_gate(
            repository=production,
            source_tree=ROOT,
            phase6_post_promotion_audit_path=root / "phase6.json",
            saved_phase7_evidence_status_path=root / "status.json",
            saved_phase7_evidence_plan_path=root / "plan.json",
            saved_input_preflight_path=preflight_path,
            controlled_live_config_path=root / "config.json",
            proposal_path=root / "proposal.json",
            executor_wallet_pubkey=WALLET,
            saved_signed_authorization_verification_path=root / "verify.json",
            signed_payload_path=root / "payload.json",
            signature_path=root / "signature.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="a" * 64,
            saved_authorization_readiness_path=readiness_path,
            expected_authorization_readiness_sha256="1" * 64,
            execution_database_path=execution,
            now="2026-09-28T19:02:00Z",
        )

        after = execution.read_bytes()

    MODULE.validate_phase7_presubmit_evidence_gate(report)
    assert before == after
    assert report["fresh_authorization_readiness_matches_saved"] is True
    assert report["execution_database_unchanged"] is True
    assert report["presubmit_evidence_ready"] is True
    assert report["signature_absent"] is True


def test_builder_rejects_fresh_readiness_drift(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        sqlite3.connect(production / "data" / "pio.db").close()
        execution = root / "execution.db"
        _create_execution_db(execution)

        saved = _saved_readiness(production)
        fresh = copy.deepcopy(saved)
        fresh["readiness_sha256"] = "f" * 64
        preflight = _preflight()

        class FakeReadiness:
            @staticmethod
            def validate_phase7_authorization_readiness(value):
                pass

            @staticmethod
            def build_phase7_authorization_readiness(**kwargs):
                return copy.deepcopy(fresh)

        class FakePreflight:
            @staticmethod
            def validate_phase7_input_preflight(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_verify_reviewed_source",
            lambda source: (FakeReadiness, FakePreflight),
        )

        with pytest.raises(ValueError, match="differs from saved readiness"):
            MODULE.build_phase7_presubmit_evidence_gate(
                repository=production,
                source_tree=ROOT,
                phase6_post_promotion_audit_path=root / "phase6.json",
                saved_phase7_evidence_status_path=root / "status.json",
                saved_phase7_evidence_plan_path=root / "plan.json",
                saved_input_preflight_path=_write(
                    root, "preflight.json", preflight
                ),
                controlled_live_config_path=root / "config.json",
                proposal_path=root / "proposal.json",
                executor_wallet_pubkey=WALLET,
                saved_signed_authorization_verification_path=root / "verify.json",
                signed_payload_path=root / "payload.json",
                signature_path=root / "signature.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="a" * 64,
                saved_authorization_readiness_path=_write(
                    root, "readiness.json", saved
                ),
                expected_authorization_readiness_sha256="1" * 64,
                execution_database_path=execution,
            )


def test_presubmit_tool_has_no_signing_submission_or_key_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "load_executor_keypair" not in source
    assert "submit_execution_intent" not in source
    assert "begin_signing(" not in source
    assert "record_sent(" not in source
    assert "systemctl" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source
    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
