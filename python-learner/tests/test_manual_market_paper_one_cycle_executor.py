from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "run_manual_market_paper_one_cycle.py"

SPEC = importlib.util.spec_from_file_location(
    "run_manual_market_paper_one_cycle",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _parameters() -> dict:
    return {
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "capital_per_position_quote": "100.00",
        "network_cost_quote": "0.10",
        "max_new_positions": 1,
        "max_pools_considered": 10,
        "minimum_chain_observations": 12,
        "intake_max_pools": 500,
        "seed_batch_limit": 25,
        "refresh_batch_limit": 10,
        "discovery_page_size": 1000,
        "discovery_max_pages": 100,
        "discovery_sort_by": "tvl:desc",
        "bin_array_radius": 0,
        "timeout_seconds": 120,
        "quote_max_age_seconds": 300,
        "max_share_bps": 500,
        "scheduler_interval_seconds": 300,
        "scheduler_lease_seconds": 900,
        "scheduler_max_positions": 1,
        "observed_at": None,
    }


def _gate(production: Path) -> dict:
    params = _parameters()
    params_sha = hashlib.sha256(MODULE._canonical_bytes(params)).hexdigest()
    return {
        "execution_gate_sha256": "1" * 64,
        "manual_cycle_execution_gate_ready": True,
        "requires_immediate_one_shot_executor_recheck": True,
        "production_repository": str(production),
        "authorization_request_sha256": "2" * 64,
        "fresh_signed_authorization_verification_sha256": "3" * 64,
        "cycle_parameters": params,
        "cycle_parameters_sha256": params_sha,
    }


def _cycle_report(**updates) -> dict:
    value = {
        "account_id": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "observed_at": "2026-09-28T12:30:00Z",
        "status": "COMPLETE",
        "manual_only": True,
        "paper_only": True,
        "policy_actionable": False,
        "live_authorized": False,
        "research": {"status": "COMPLETE", "pools_ready": 10, "stages": []},
        "scheduler": {
            "status": "COMPLETE",
            "tick_id": "tick-1",
            "lease_acquired": True,
            "recovered_stale_lease": False,
        },
        "exploration": {
            "status": "COMPLETE",
            "pools_ready": 10,
            "pools_considered": 10,
            "positions_opened": 1,
            "positions_already_applied": 0,
            "max_new_positions": 1,
            "max_pools_considered": 10,
            "items": [],
        },
        "new_entries_skipped_reason": None,
        "new_positions_pending_next_tick": 1,
    }
    value.update(updates)
    return value


def _production(root: Path) -> Path:
    production = root / "production"
    (production / "data").mkdir(parents=True)
    (production / "data" / "pio.db").write_bytes(b"sqlite-placeholder")
    python_bin = production / "python-learner" / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    python_bin.chmod(0o755)
    return production


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _run_with_fakes(
    monkeypatch,
    *,
    gate_mutator=None,
    fresh_mutator=None,
    cycle_report=None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = _production(root)
    gate = _gate(production)
    if gate_mutator:
        gate_mutator(gate)
    fresh = copy.deepcopy(gate)
    if fresh_mutator:
        fresh_mutator(fresh)
    gate_path = _write(root, "gate.json", gate)

    class FakeGateModule:
        @staticmethod
        def validate_execution_gate(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_execution_gate(**kwargs):
            return copy.deepcopy(fresh)

    monkeypatch.setattr(MODULE, "_load_gate_module", lambda source: FakeGateModule)
    monkeypatch.setattr(MODULE, "LOCK_PATH", root / "one-cycle.lock")

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                cycle_report if cycle_report is not None else _cycle_report()
            ),
            stderr="",
        )

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    def run():
        return MODULE.execute_one_cycle(
            repository=production,
            source_tree=ROOT,
            expected_execution_gate_sha256=gate["execution_gate_sha256"],
            execution_gate_path=gate_path,
            private_bundle_report_path=root / "bundle.json",
            pre_mutation_handoff_path=root / "handoff.json",
            execution_precheck_path=root / "precheck.json",
            mutation_receipt_path=root / "mutation-receipt.json",
            post_mutation_audit_path=root / "audit.json",
            manual_cycle_readiness_path=root / "readiness.json",
            authorization_request_path=root / "request.json",
            signed_authorization_verification_path=root / "signed.json",
            signed_payload_path=root / "payload.json",
            signature_path=root / "payload.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="8" * 64,
        )

    return temp, production, gate, captured, run


def _reseal(receipt: dict) -> None:
    identity = {field: receipt[field] for field in MODULE.RECEIPT_FIELDS}
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_execution_gate_tool_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_command_is_exactly_bounded_and_has_no_observed_at():
    command = MODULE._command(_parameters(), Path("/fixed/python"))

    assert command[:3] == [
        "/fixed/python",
        "-m",
        MODULE.MODULE_NAME,
    ]
    assert "--max-new-positions" in command
    assert command[command.index("--max-new-positions") + 1] == "1"
    assert "--max-pools-considered" in command
    assert command[command.index("--max-pools-considered") + 1] == "10"
    assert "--scheduler-max-positions" in command
    assert command[command.index("--scheduler-max-positions") + 1] == "1"
    assert "--observed-at" not in command


def test_one_shot_executor_rechecks_gate_and_emits_safe_receipt(monkeypatch):
    temp, production, gate, captured, run = _run_with_fakes(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    MODULE.validate_execution_receipt(receipt)
    assert receipt["execution_gate_sha256"] == gate["execution_gate_sha256"]
    assert receipt["one_cycle_execution_completed"] is True
    assert receipt["paper_database_write_expected"] is True
    assert receipt["production_source_file_modified"] is False
    assert receipt["paper_timer_action_performed"] is False
    assert receipt["transaction_signing_performed"] is False
    assert receipt["transaction_submission_performed"] is False
    assert receipt["live_capital_used"] is False
    assert receipt["requires_post_cycle_audit"] is True
    assert receipt["positions_opened"] == 1

    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == production
    assert kwargs["shell"] if "shell" in kwargs else True
    assert kwargs["env"]["PIO_DATABASE_PATH"] == str(
        production / "data" / "pio.db"
    )
    assert kwargs["env"]["PYTHONPATH"] == str(
        production / "python-learner" / "src"
    )


def test_executor_fails_if_fresh_gate_differs(monkeypatch):
    temp, _, _, _, run = _run_with_fakes(
        monkeypatch,
        fresh_mutator=lambda value: value.update(
            execution_gate_sha256="9" * 64
        ),
    )
    try:
        with pytest.raises(ValueError, match="fresh execution gate differs"):
            run()
    finally:
        temp.cleanup()


def test_executor_rejects_live_authorized_cycle_report(monkeypatch):
    report = _cycle_report(live_authorized=True)
    temp, _, _, _, run = _run_with_fakes(
        monkeypatch,
        cycle_report=report,
    )
    try:
        with pytest.raises(ValueError, match="must not authorize live execution"):
            run()
    finally:
        temp.cleanup()


def test_executor_rejects_more_than_one_opened_position(monkeypatch):
    report = _cycle_report()
    report["exploration"]["positions_opened"] = 2
    temp, _, _, _, run = _run_with_fakes(
        monkeypatch,
        cycle_report=report,
    )
    try:
        with pytest.raises(ValueError, match="opened-position bound exceeded"):
            run()
    finally:
        temp.cleanup()


def test_executor_rejects_paper_database_symlink(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = _production(root)
        database = production / "data" / "pio.db"
        target = root / "other.db"
        target.write_bytes(b"x")
        database.unlink()
        database.symlink_to(target)

        gate = _gate(production)
        gate_path = _write(root, "gate.json", gate)

        class FakeGateModule:
            @staticmethod
            def validate_execution_gate(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_gate_module",
            lambda source: FakeGateModule,
        )

        with pytest.raises(ValueError, match="database must be a regular file"):
            MODULE.execute_one_cycle(
                repository=production,
                source_tree=ROOT,
                expected_execution_gate_sha256=gate["execution_gate_sha256"],
                execution_gate_path=gate_path,
                private_bundle_report_path=root / "bundle.json",
                pre_mutation_handoff_path=root / "handoff.json",
                execution_precheck_path=root / "precheck.json",
                mutation_receipt_path=root / "mutation-receipt.json",
                post_mutation_audit_path=root / "audit.json",
                manual_cycle_readiness_path=root / "readiness.json",
                authorization_request_path=root / "request.json",
                signed_authorization_verification_path=root / "signed.json",
                signed_payload_path=root / "payload.json",
                signature_path=root / "payload.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="8" * 64,
            )


def test_resealed_receipt_cannot_claim_timer_action():
    report = _cycle_report()
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
        "execution_gate_sha256": "1" * 64,
        "authorization_request_sha256": "2" * 64,
        "signed_authorization_verification_sha256": "3" * 64,
        "production_repository": "/opt/pio",
        "paper_database_path": "/opt/pio/data/pio.db",
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "cycle_parameters_sha256": "4" * 64,
        "cycle_report": report,
        "cycle_report_sha256": hashlib.sha256(
            MODULE._canonical_bytes(report)
        ).hexdigest(),
        "status": "COMPLETE",
        "observed_at": "2026-09-28T12:30:00Z",
        "research_status": "COMPLETE",
        "scheduler_status": "COMPLETE",
        "exploration_status": "COMPLETE",
        "positions_opened": 1,
        "new_positions_pending_next_tick": 1,
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
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }
    MODULE.validate_execution_receipt(copy.deepcopy(receipt))

    receipt["paper_timer_action_performed"] = True
    _reseal(receipt)
    with pytest.raises(ValueError, match="paper_timer_action_performed=false"):
        MODULE.validate_execution_receipt(receipt)


def test_executor_has_no_service_git_or_live_submit_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "systemctl" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert "live-submit" not in source
    assert "paper_timer_action_performed\": False" in source
    assert "transaction_signing_performed\": False" in source
    assert "transaction_submission_performed\": False" in source
    assert "live_capital_used\": False" in source
