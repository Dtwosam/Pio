from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "build_phase6_prelive_promotion_request.py"

SPEC = importlib.util.spec_from_file_location(
    "build_phase6_prelive_promotion_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _status() -> dict:
    return {
        "phase6_evidence_status_ready": True,
        "phase6_evidence_status_sha256": "1" * 64,
        "phase5_post_promotion_audit_sha256": "2" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "execution_database_path": "/var/lib/pio/execution.db",
        "pio_database_sha256_after": "3" * 64,
        "execution_database_sha256_after": "4" * 64,
        "phase6_criteria_sha256": "5" * 64,
        "phase6_report_sha256": "6" * 64,
        "passed_enter_intents": 10,
        "distinct_pools": 2,
        "blocked_intents": 2,
        "postsimulation_intents": 0,
        "invalid_passed_intents": 0,
        "distinct_authorized_wallets": ["wallet-1"],
        "phase5_promoted": True,
        "phase6_promotion_ready": True,
        "phase6_reasons": [],
        "requires_additional_phase6_evidence": False,
        "phase6_promotion_persisted": False,
        "phase6_promotion_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
    }


def _write(root: Path, value: dict) -> Path:
    path = root / "phase6-status.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, status: dict | None = None) -> dict:
    value = copy.deepcopy(status if status is not None else _status())

    class FakeStatus:
        @staticmethod
        def validate_phase6_evidence_status(candidate):
            assert isinstance(candidate, dict)

    monkeypatch.setattr(MODULE, "_load_status_module", lambda source: FakeStatus)

    with tempfile.TemporaryDirectory() as tmp:
        return MODULE.build_phase6_promotion_request(
            source_tree=ROOT,
            phase6_evidence_status_path=_write(Path(tmp), value),
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_status_tool_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_ready_request_binds_exact_corpus_but_authorizes_nothing(monkeypatch):
    request = _build(monkeypatch)

    assert request["phase6_evidence_status_sha256"] == "1" * 64
    assert request["pio_database_sha256"] == "3" * 64
    assert request["execution_database_sha256"] == "4" * 64
    assert request["passed_enter_intents"] == 10
    assert request["distinct_pools"] == 2
    assert request["blocked_intents"] == 2
    assert request["distinct_authorized_wallets"] == ["wallet-1"]
    assert request["promotion_request_ready"] is True
    assert request["phase6_promotion_authorization_present"] is False
    assert request["phase6_promotion_persisted"] is False
    assert request["controlled_live_authorized"] is False
    assert request["live_submit_authorized"] is False
    assert request["live_capital_authorized"] is False


def test_request_rejects_incomplete_evidence(monkeypatch):
    status = _status()
    status["phase6_promotion_ready"] = False
    status["requires_additional_phase6_evidence"] = True
    status["phase6_reasons"] = ["not enough intents"]

    with pytest.raises(ValueError, match="not promotion-ready"):
        _build(monkeypatch, status)


def test_request_rejects_postsimulation_evidence(monkeypatch):
    status = _status()
    status["postsimulation_intents"] = 1

    with pytest.raises(ValueError, match="post-simulation"):
        _build(monkeypatch, status)


def test_request_rejects_invalid_passed_intent(monkeypatch):
    status = _status()
    status["invalid_passed_intents"] = 1

    with pytest.raises(ValueError, match="invalid passed"):
        _build(monkeypatch, status)


def test_request_rejects_multiple_executor_wallets(monkeypatch):
    status = _status()
    status["distinct_authorized_wallets"] = ["wallet-1", "wallet-2"]

    with pytest.raises(ValueError, match="exactly one executor wallet"):
        _build(monkeypatch, status)


def test_resealed_request_cannot_claim_phase6_persisted(monkeypatch):
    request = _build(monkeypatch)
    request["phase6_promotion_persisted"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="phase6_promotion_persisted=false"):
        MODULE.validate_phase6_promotion_request(request)


def test_resealed_request_cannot_authorize_live_submit(monkeypatch):
    request = _build(monkeypatch)
    request["live_submit_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase6_promotion_request(request)


def test_resealed_request_cannot_expand_executor_wallets(monkeypatch):
    request = _build(monkeypatch)
    request["distinct_authorized_wallets"] = ["wallet-1", "wallet-2"]
    _reseal(request)

    with pytest.raises(ValueError, match="exactly one wallet"):
        MODULE.validate_phase6_promotion_request(request)


def test_request_tool_has_no_persistence_or_live_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "subprocess" not in source
    assert "persist_phase6_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
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
