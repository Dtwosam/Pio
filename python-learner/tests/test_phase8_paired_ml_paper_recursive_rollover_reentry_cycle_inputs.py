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
    / "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_inputs.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_inputs",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _FakeFinal:
    DEBT_MORE_EVIDENCE = "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
    ROUTE_NEXT_PAIR = "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_NEXT_PAIR_REVIEW"

    @staticmethod
    def validate_phase8_paired_ml_paper_recursive_rollover_reentry_post_settlement_final_evaluation(
        value,
    ):
        assert isinstance(value, dict)


def _final(*, next_pair: bool = True) -> dict:
    return {
        "evaluation_sha256": "a" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256_after": "b" * 64,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_after": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-4",
        "incumbent_closed_trades": 9,
        "challenger_closed_trades": 9,
        "required_incumbent_closed_trades": 20,
        "required_challenger_closed_trades": 20,
        "next_pair_review_ready": next_pair,
        "promotion_review_ready": False,
        "validation_review_ready": False,
        "closed_trade_floor_met": False,
        "next_debt_type": "PAPER_CHALLENGER_EVIDENCE_REQUIRED",
        "continuation_route": "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_NEXT_PAIR_REVIEW",
        "pair_both_closed": True,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, final: dict | None = None):
    monkeypatch.setattr(
        MODULE,
        "_load_final_evaluation",
        lambda source: _FakeFinal,
    )
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    final_path = _write(
        root / "final.json",
        copy.deepcopy(final if final is not None else _final()),
    )
    return temp, root, final_path


def _filled(template: dict, *, pair_id: str = "pair-5") -> dict:
    value = copy.deepcopy(template)
    value.update(
        {
            "pair_id": pair_id,
            "pool_address": "pool-5",
            "amount_x": 10,
            "amount_y": 20,
            "network_cost_y_atomic": 5,
            "capital_quote": 1000.0,
            "entry_cost_quote": 5.0,
        }
    )
    return value


def test_reviewed_final_evaluation_is_exactly_pinned():
    path = ROOT / MODULE.FINAL_EVALUATION_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.FINAL_EVALUATION_TOOL
    ]


def test_template_binds_to_more_evidence_route(monkeypatch):
    temp, _, final_path = _build(monkeypatch)
    try:
        template = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_input_template(
                source_tree=ROOT,
                final_evaluation_path=final_path,
            )
        )
    finally:
        temp.cleanup()

    assert template["previous_pair_id"] == "pair-5"
    assert template["account_mode"] == "EXISTING"
    assert template["previous_incumbent_closed_trades"] == 9
    assert template["previous_challenger_closed_trades"] == 9
    assert template["pair_id"] is None
    assert template["as_of"] is None
    assert template["reentry_cycle_inputs_ready"] is False
    assert template["requires_separate_paired_entry_authorization"] is True
    assert template["paper_pair_entry_authorized"] is False
    assert template["live_submit_authorized"] is False


def test_verify_accepts_new_pair_and_derives_ids(monkeypatch):
    temp, root, final_path = _build(monkeypatch)
    try:
        template = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_input_template(
                source_tree=ROOT,
                final_evaluation_path=final_path,
            )
        )
        input_path = _write(root / "input.json", _filled(template))
        result = MODULE.verify_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_inputs(
            source_tree=ROOT,
            final_evaluation_path=final_path,
            input_path=input_path,
        )
    finally:
        temp.cleanup()

    assert result["pair_id"] == "pair-5"
    assert result["incumbent_position_id"] == "p8-pair-5-incumbent"
    assert result["challenger_position_id"] == "p8-pair-5-challenger"
    assert result["incumbent_event_key"] == "p8-pair-5:incumbent"
    assert result["challenger_event_key"] == "p8-pair-5:challenger"
    assert result["reentry_cycle_inputs_ready"] is True
    assert result["new_pair_id_verified"] is True
    assert result["entry_policy_not_weakened"] is True
    assert result["paper_pair_entry_authorized"] is False
    assert result["paper_trading_authorized"] is False


def test_cycle_pair_id_must_differ_from_previous(monkeypatch):
    temp, root, final_path = _build(monkeypatch)
    try:
        template = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_input_template(
                source_tree=ROOT,
                final_evaluation_path=final_path,
            )
        )
        input_path = _write(
            root / "input.json",
            _filled(template, pair_id="pair-3"),
        )
        with pytest.raises(ValueError, match="pair_id must be new"):
            MODULE.verify_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_inputs(
                source_tree=ROOT,
                final_evaluation_path=final_path,
                input_path=input_path,
            )
    finally:
        temp.cleanup()


def test_cycle_entry_refuses_non_next_pair_route(monkeypatch):
    final = _final(next_pair=False)
    final["promotion_review_ready"] = True
    final["closed_trade_floor_met"] = True
    final["next_debt_type"] = "PAPER_CHALLENGER_PROMOTION_REVIEW_REQUIRED"
    final["continuation_route"] = (
        "PHASE8_CONTINUOUS_CHALLENGER_PROMOTION_REVIEW"
    )
    temp, _, final_path = _build(monkeypatch, final=final)
    try:
        with pytest.raises(
            ValueError,
            match="does not route to another pair",
        ):
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_input_template(
                source_tree=ROOT,
                final_evaluation_path=final_path,
            )
    finally:
        temp.cleanup()


def test_fixed_risk_policy_cannot_be_weakened(monkeypatch):
    temp, root, final_path = _build(monkeypatch)
    try:
        template = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_input_template(
                source_tree=ROOT,
                final_evaluation_path=final_path,
            )
        )
        value = _filled(template)
        value["max_share_bps"] = 5000
        input_path = _write(root / "input.json", value)
        with pytest.raises(ValueError, match="policy max_share_bps changed"):
            MODULE.verify_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_inputs(
                source_tree=ROOT,
                final_evaluation_path=final_path,
                input_path=input_path,
            )
    finally:
        temp.cleanup()


def test_input_cannot_carry_pair_authorization(monkeypatch):
    temp, root, final_path = _build(monkeypatch)
    try:
        template = (
            MODULE.build_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_input_template(
                source_tree=ROOT,
                final_evaluation_path=final_path,
            )
        )
        value = _filled(template)
        value["paper_pair_entry_authorized"] = True
        input_path = _write(root / "input.json", value)
        with pytest.raises(
            ValueError,
            match="paper_pair_entry_authorized=false",
        ):
            MODULE.verify_phase8_paired_ml_paper_recursive_rollover_reentry_cycle_inputs(
                source_tree=ROOT,
                final_evaluation_path=final_path,
                input_path=input_path,
            )
    finally:
        temp.cleanup()


def test_reentry_cycle_inputs_have_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paired_ml_paper_entries(" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "promote_continuous_challenger(" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_pair_entry_authorized": False' in source
    assert '"live_submit_authorized": False' in source
