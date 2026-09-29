from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "build_phase8_paper_evidence_inputs.py"

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paper_evidence_inputs",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _audit() -> dict:
    return {
        "post_audit_sha256": "a" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "model_id": "challenger-1",
        "active_cycle_id": "cycle-1",
        "next_debt_type": "PAPER_CHALLENGER_EVIDENCE_REQUIRED",
        "continuation_route": "PHASE8_PAPER_CHALLENGER_EVIDENCE_REVIEW",
        "paper_challenger_active": True,
        "paper_account_required": True,
        "challenger_closed_trade_evidence_required": True,
        "incumbent_closed_trade_evidence_required": True,
        "requires_manual_paper_evidence_inputs": True,
        "requires_separate_paper_evidence_action": True,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
    }


class _FakeAudit:
    @staticmethod
    def validate_phase8_paper_challenger_transition_post_audit(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _template(monkeypatch, *, audit: dict | None = None):
    monkeypatch.setattr(
        MODULE,
        "_load_post_audit_module",
        lambda source: _FakeAudit,
    )
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    audit_path = _write(
        root / "post-audit.json",
        copy.deepcopy(audit if audit is not None else _audit()),
    )
    value = MODULE.build_phase8_paper_evidence_input_template(
        source_tree=ROOT,
        post_audit_path=audit_path,
    )
    return temp, root, audit_path, value


def test_reviewed_post_audit_is_exactly_pinned():
    path = ROOT / MODULE.POST_AUDIT_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.POST_AUDIT_TOOL
    ]


def test_template_stops_at_explicit_manual_economic_inputs(monkeypatch):
    temp, _, _, value = _template(monkeypatch)
    try:
        assert value["account_mode"] is None
        assert value["account_id"] is None
        assert value["starting_cash_quote"] is None
        assert value["min_challenger_closed_trades"] == 20
        assert value["min_incumbent_closed_trades"] == 20
        assert value["paper_account_create_command"] is None
        assert value["paper_supervisor_command"] is None
        assert value["continuous_validation_command"] is None
        assert value["paper_evidence_inputs_ready"] is False
        assert value[
            "requires_separate_paper_evidence_execution_authorization"
        ] is True
        assert value["paper_evidence_collection_authorized"] is False
        assert value["paper_trading_authorized"] is False
        assert value["live_submit_authorized"] is False
        assert value["new_live_capital_authorized"] is False
        assert value["phase8_promotion_authorized"] is False
    finally:
        temp.cleanup()


def test_create_mode_verifies_account_and_starting_cash(monkeypatch):
    temp, root, audit_path, value = _template(monkeypatch)
    try:
        value["account_mode"] = "CREATE"
        value["account_id"] = "phase8-paper-cycle-1"
        value["starting_cash_quote"] = 10000.0
        input_path = _write(root / "inputs.json", value)

        result = MODULE.verify_phase8_paper_evidence_inputs(
            source_tree=ROOT,
            post_audit_path=audit_path,
            input_path=input_path,
        )

        assert result["account_mode"] == "CREATE"
        assert result["account_id"] == "phase8-paper-cycle-1"
        assert result["starting_cash_quote"] == 10000.0
        assert result["account_input_valid"] is True
        assert result["economic_input_valid"] is True
        assert result["criteria_not_weakened"] is True
        assert result["paper_evidence_inputs_ready"] is True
        assert result["requires_separate_paper_account_action"] is True
        assert result[
            "paper_account_create_command"
        ] == (
            "pio paper-create-account --account "
            "phase8-paper-cycle-1 --cash 10000"
        )
        assert result[
            "paper_supervisor_command"
        ] == (
            "pio paper-supervise --account phase8-paper-cycle-1 "
            "--cycle-id cycle-1"
        )
        assert result["paper_evidence_collection_authorized"] is False
        assert result["paper_trading_authorized"] is False
    finally:
        temp.cleanup()


def test_existing_mode_does_not_invent_starting_cash(monkeypatch):
    temp, root, audit_path, value = _template(monkeypatch)
    try:
        value["account_mode"] = "EXISTING"
        value["account_id"] = "existing-paper"
        value["starting_cash_quote"] = None
        input_path = _write(root / "inputs.json", value)

        result = MODULE.verify_phase8_paper_evidence_inputs(
            source_tree=ROOT,
            post_audit_path=audit_path,
            input_path=input_path,
        )

        assert result["account_mode"] == "EXISTING"
        assert result["paper_account_create_command"] is None
        assert result["requires_separate_paper_account_action"] is False
        assert result["paper_supervisor_command"] == (
            "pio paper-supervise --account existing-paper "
            "--cycle-id cycle-1"
        )
    finally:
        temp.cleanup()


def test_create_mode_requires_positive_starting_cash(monkeypatch):
    temp, root, audit_path, value = _template(monkeypatch)
    try:
        value["account_mode"] = "CREATE"
        value["account_id"] = "paper-1"
        value["starting_cash_quote"] = None
        input_path = _write(root / "inputs.json", value)

        with pytest.raises(ValueError, match="positive starting_cash_quote"):
            MODULE.verify_phase8_paper_evidence_inputs(
                source_tree=ROOT,
                post_audit_path=audit_path,
                input_path=input_path,
            )
    finally:
        temp.cleanup()


def test_existing_mode_rejects_invented_starting_cash(monkeypatch):
    temp, root, audit_path, value = _template(monkeypatch)
    try:
        value["account_mode"] = "EXISTING"
        value["account_id"] = "paper-1"
        value["starting_cash_quote"] = 1000.0
        input_path = _write(root / "inputs.json", value)

        with pytest.raises(ValueError, match="starting_cash_quote=null"):
            MODULE.verify_phase8_paper_evidence_inputs(
                source_tree=ROOT,
                post_audit_path=audit_path,
                input_path=input_path,
            )
    finally:
        temp.cleanup()


def test_qualification_criteria_cannot_be_weakened(monkeypatch):
    temp, root, audit_path, value = _template(monkeypatch)
    try:
        value["account_mode"] = "EXISTING"
        value["account_id"] = "paper-1"
        value["min_challenger_closed_trades"] = 1
        input_path = _write(root / "inputs.json", value)

        with pytest.raises(ValueError, match="criterion"):
            MODULE.verify_phase8_paper_evidence_inputs(
                source_tree=ROOT,
                post_audit_path=audit_path,
                input_path=input_path,
            )
    finally:
        temp.cleanup()


def test_filled_template_cannot_self_authorize_paper_trading(monkeypatch):
    temp, root, audit_path, value = _template(monkeypatch)
    try:
        value["account_mode"] = "EXISTING"
        value["account_id"] = "paper-1"
        value["paper_trading_authorized"] = True
        input_path = _write(root / "inputs.json", value)

        with pytest.raises(
            ValueError,
            match="paper_trading_authorized=false",
        ):
            MODULE.verify_phase8_paper_evidence_inputs(
                source_tree=ROOT,
                post_audit_path=audit_path,
                input_path=input_path,
            )
    finally:
        temp.cleanup()


def test_wrong_post_audit_route_fails_closed(monkeypatch):
    audit = _audit()
    audit["continuation_route"] = "PHASE8_PROMOTION_REVIEW"
    monkeypatch.setattr(
        MODULE,
        "_load_post_audit_module",
        lambda source: _FakeAudit,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "audit.json", audit)
        with pytest.raises(ValueError, match="not routed"):
            MODULE.build_phase8_paper_evidence_input_template(
                source_tree=ROOT,
                post_audit_path=path,
            )


def test_input_tool_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "create_paper_account(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
