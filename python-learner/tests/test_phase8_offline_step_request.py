from __future__ import annotations

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
    / "build_phase8_offline_step_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_offline_step_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakeHandoff:
    @staticmethod
    def validate_phase8_post_phase7_handoff(value):
        assert isinstance(value, dict)


def _handoff() -> dict:
    return {
        "handoff_sha256": "a" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256_after": "b" * 64,
        "phase7_promotion_confirmed": True,
        "phase7_promotion_persisted": True,
        "phase8_handoff_ready": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "phase8_operator_handoff": {
            "research_only": True,
            "read_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "status": "AUTOMATIC_ACTION",
            "debt_type": "RETRAIN_OFFLINE_TRAIN_READY",
            "scope": "cycle-1",
            "reason": "private offline training step is ready",
            "automatic_action_available": True,
            "operator_action_required": False,
            "manual_input_required": False,
            "suggested_command": "pio phase8-evidence-run --max-steps 4",
        },
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_policy_action_authorized": False,
        "phase8_execution_authorized": False,
        "phase8_promotion_authorized": False,
        "production_pio_database_modified": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, override: dict | None = None):
    value = copy.deepcopy(_handoff())
    if override:
        for key, item in override.items():
            if key == "phase8_operator_handoff":
                value[key].update(item)
            else:
                value[key] = item

    monkeypatch.setattr(
        MODULE,
        "_load_handoff_module",
        lambda source: _FakeHandoff,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "handoff.json", value)
        return MODULE.build_phase8_offline_step_request(
            source_tree=ROOT,
            phase8_handoff_path=path,
            expected_phase8_handoff_sha256="a" * 64,
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_handoff_tool_is_exactly_pinned():
    path = ROOT / MODULE.HANDOFF_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.HANDOFF_TOOL
    ]


@pytest.mark.parametrize(
    "debt_type",
    sorted(MODULE.ALLOWED_DEBT_TYPES),
)
def test_allowed_automatic_offline_debt_builds_request(
    monkeypatch,
    debt_type,
):
    request = _build(
        monkeypatch,
        {"phase8_operator_handoff": {"debt_type": debt_type}},
    )

    assert request["debt_type"] == debt_type
    assert request["request_ready"] is True
    assert request["automatic_action_available"] is True
    assert request["one_step_only"] is True
    assert request["explicit_human_authorization_required"] is True
    assert request["offline_step_authorization_present"] is False
    assert request["offline_step_execution_authorized"] is False
    assert request["paper_challenger_transition_authorized"] is False
    assert request["paper_trading_authorized"] is False
    assert request["live_submit_authorized"] is False
    assert request["new_live_capital_authorized"] is False


def test_manual_required_state_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="not automatic"):
        _build(
            monkeypatch,
            {
                "phase8_operator_handoff": {
                    "status": "MANUAL_REQUIRED",
                    "automatic_action_available": False,
                    "operator_action_required": True,
                    "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
                }
            },
        )


def test_paper_transition_debt_is_not_an_allowed_offline_step(monkeypatch):
    with pytest.raises(ValueError, match="not allowed"):
        _build(
            monkeypatch,
            {
                "phase8_operator_handoff": {
                    "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
                }
            },
        )


def test_manual_input_requirement_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="requires manual input"):
        _build(
            monkeypatch,
            {
                "phase8_operator_handoff": {
                    "manual_input_required": True,
                }
            },
        )


def test_handoff_digest_mismatch_is_rejected(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_load_handoff_module",
        lambda source: _FakeHandoff,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "handoff.json", _handoff())
        with pytest.raises(ValueError, match="handoff digest mismatch"):
            MODULE.build_phase8_offline_step_request(
                source_tree=ROOT,
                phase8_handoff_path=path,
                expected_phase8_handoff_sha256="f" * 64,
            )


def test_resealed_request_cannot_authorize_paper_transition(monkeypatch):
    request = _build(monkeypatch)
    request["paper_challenger_transition_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="paper_challenger_transition_authorized=false",
    ):
        MODULE.validate_phase8_offline_step_request(request)


def test_resealed_request_cannot_authorize_live_submit(monkeypatch):
    request = _build(monkeypatch)
    request["live_submit_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase8_offline_step_request(request)


def test_request_has_no_mutation_or_transaction_execution():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_phase8_evidence_step(" not in source
    assert "start_paper_challenger(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"offline_step_execution_authorized": False' in source
    assert '"paper_challenger_transition_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
