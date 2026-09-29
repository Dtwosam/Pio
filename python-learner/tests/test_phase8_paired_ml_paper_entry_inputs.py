from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile

import pytest

from importlib import util


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase8_paired_ml_paper_entry_inputs.py"
)

SPEC = util.spec_from_file_location(
    "build_phase8_paired_ml_paper_entry_inputs",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _paper_verification(*, mode: str = "EXISTING") -> dict:
    return {
        "verification_sha256": "a" * 64,
        "input_sha256": "b" * 64,
        "source_post_audit_sha256": "c" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "model_id": "challenger-1",
        "active_cycle_id": "cycle-1",
        "account_mode": mode,
        "account_id": "paper-1",
        "starting_cash_quote": 10000.0 if mode == "CREATE" else None,
        "paper_evidence_inputs_ready": True,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
    }


class FakeEvidenceInputs:
    verification = _paper_verification()

    @classmethod
    def verify_phase8_paper_evidence_inputs(cls, **kwargs):
        return copy.deepcopy(cls.verification)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _template(monkeypatch, *, mode: str = "EXISTING"):
    FakeEvidenceInputs.verification = _paper_verification(mode=mode)
    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: FakeEvidenceInputs,
    )
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    post = _write(root / "post-audit.json", {"fixture": True})
    paper = _write(root / "paper-inputs.json", {"fixture": True})
    value = MODULE.build_phase8_paired_ml_paper_entry_input_template(
        source_tree=ROOT,
        post_audit_path=post,
        paper_evidence_input_path=paper,
    )
    return temp, root, post, paper, value


def _fill(value: dict) -> dict:
    result = copy.deepcopy(value)
    result["pair_id"] = "pair-001"
    result["pool_address"] = "pool-1"
    result["amount_x"] = 100
    result["amount_y"] = 200
    result["network_cost_y_atomic"] = 3
    result["capital_quote"] = 1000.0
    result["entry_cost_quote"] = 5.0
    return result


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_template_requires_all_pair_economic_inputs(monkeypatch):
    temp, _, _, _, value = _template(monkeypatch)
    try:
        assert value["pair_id"] is None
        assert value["pool_address"] is None
        assert value["amount_x"] is None
        assert value["amount_y"] is None
        assert value["network_cost_y_atomic"] is None
        assert value["capital_quote"] is None
        assert value["entry_cost_quote"] is None
        assert value["as_of"] is None
        assert value["paired_entry_inputs_ready"] is False
        assert value["requires_fresh_account_readiness_check"] is True
        assert value["requires_separate_paired_entry_authorization"] is True
        assert value["paper_pair_entry_authorized"] is False
        assert value["paper_trading_authorized"] is False
        assert value["live_submit_authorized"] is False
    finally:
        temp.cleanup()


def test_create_account_mode_is_preserved_as_separate_dependency(monkeypatch):
    temp, _, _, _, value = _template(monkeypatch, mode="CREATE")
    try:
        assert value["account_mode"] == "CREATE"
        assert value["starting_cash_quote"] == 10000.0
        assert value["requires_paper_account_creation_before_pair"] is True
        assert value["paper_pair_entry_authorized"] is False
    finally:
        temp.cleanup()


def test_valid_pair_inputs_derive_idempotent_ids(monkeypatch):
    temp, root, post, paper, value = _template(monkeypatch)
    try:
        filled = _fill(value)
        path = _write(root / "pair-inputs.json", filled)
        report = MODULE.verify_phase8_paired_ml_paper_entry_inputs(
            source_tree=ROOT,
            post_audit_path=post,
            paper_evidence_input_path=paper,
            input_path=path,
        )

        assert report["pair_id"] == "pair-001"
        assert report["pool_address"] == "pool-1"
        assert report["amount_x"] == 100
        assert report["amount_y"] == 200
        assert report["network_cost_y_atomic"] == 3
        assert report["capital_quote"] == 1000.0
        assert report["entry_cost_quote"] == 5.0
        assert report["incumbent_position_id"] == "p8-pair-001-incumbent"
        assert report["challenger_position_id"] == "p8-pair-001-challenger"
        assert report["incumbent_event_key"] == "p8-pair-001:incumbent"
        assert report["challenger_event_key"] == "p8-pair-001:challenger"
        assert report["economic_inputs_valid"] is True
        assert report["entry_policy_not_weakened"] is True
        assert report["paired_entry_inputs_ready"] is True
        assert report["requires_fresh_account_readiness_check"] is True
        assert report["requires_separate_paired_entry_authorization"] is True
        assert report["paper_pair_entry_authorized"] is False
        assert report["paper_evidence_collection_authorized"] is False
        assert report["paper_trading_authorized"] is False
        assert report["live_submit_authorized"] is False
    finally:
        temp.cleanup()


def test_pair_inputs_reject_zero_notional(monkeypatch):
    temp, root, post, paper, value = _template(monkeypatch)
    try:
        filled = _fill(value)
        filled["amount_x"] = 0
        filled["amount_y"] = 0
        path = _write(root / "pair-inputs.json", filled)
        with pytest.raises(ValueError, match="token amounts"):
            MODULE.verify_phase8_paired_ml_paper_entry_inputs(
                source_tree=ROOT,
                post_audit_path=post,
                paper_evidence_input_path=paper,
                input_path=path,
            )
    finally:
        temp.cleanup()


def test_pair_inputs_require_positive_capital(monkeypatch):
    temp, root, post, paper, value = _template(monkeypatch)
    try:
        filled = _fill(value)
        filled["capital_quote"] = 0.0
        path = _write(root / "pair-inputs.json", filled)
        with pytest.raises(ValueError, match="capital_quote must be positive"):
            MODULE.verify_phase8_paired_ml_paper_entry_inputs(
                source_tree=ROOT,
                post_audit_path=post,
                paper_evidence_input_path=paper,
                input_path=path,
            )
    finally:
        temp.cleanup()


def test_pair_inputs_reject_weakened_entry_policy(monkeypatch):
    temp, root, post, paper, value = _template(monkeypatch)
    try:
        filled = _fill(value)
        filled["min_positive_excess_probability"] = 0.0
        path = _write(root / "pair-inputs.json", filled)
        with pytest.raises(ValueError, match="policy"):
            MODULE.verify_phase8_paired_ml_paper_entry_inputs(
                source_tree=ROOT,
                post_audit_path=post,
                paper_evidence_input_path=paper,
                input_path=path,
            )
    finally:
        temp.cleanup()


def test_pair_inputs_do_not_allow_manual_as_of(monkeypatch):
    temp, root, post, paper, value = _template(monkeypatch)
    try:
        filled = _fill(value)
        filled["as_of"] = "2026-09-29T22:30:00+00:00"
        path = _write(root / "pair-inputs.json", filled)
        with pytest.raises(ValueError, match="as_of must remain null"):
            MODULE.verify_phase8_paired_ml_paper_entry_inputs(
                source_tree=ROOT,
                post_audit_path=post,
                paper_evidence_input_path=paper,
                input_path=path,
            )
    finally:
        temp.cleanup()


def test_pair_inputs_cannot_self_authorize_paper_entry(monkeypatch):
    temp, root, post, paper, value = _template(monkeypatch)
    try:
        filled = _fill(value)
        filled["paper_pair_entry_authorized"] = True
        path = _write(root / "pair-inputs.json", filled)
        with pytest.raises(
            ValueError,
            match="paper_pair_entry_authorized=false",
        ):
            MODULE.verify_phase8_paired_ml_paper_entry_inputs(
                source_tree=ROOT,
                post_audit_path=post,
                paper_evidence_input_path=paper,
                input_path=path,
            )
    finally:
        temp.cleanup()


def test_pair_inputs_cannot_override_derived_position_id(monkeypatch):
    temp, root, post, paper, value = _template(monkeypatch)
    try:
        filled = _fill(value)
        filled["incumbent_position_id"] = "different"
        path = _write(root / "pair-inputs.json", filled)
        with pytest.raises(ValueError, match="does not match pair_id"):
            MODULE.verify_phase8_paired_ml_paper_entry_inputs(
                source_tree=ROOT,
                post_audit_path=post,
                paper_evidence_input_path=paper,
                input_path=path,
            )
    finally:
        temp.cleanup()


def test_pair_input_tool_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paired_ml_paper_entries(" not in source
    assert "create_paper_account(" not in source
    assert "run_paper_supervisor(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_pair_entry_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
