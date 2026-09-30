from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "phase8_paired_ml_paper_recursive_reentry_cycle_handoff_contract.py"
)

SPEC = importlib.util.spec_from_file_location(
    "phase8_paired_ml_paper_recursive_reentry_cycle_handoff_contract",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _handoff() -> dict:
    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "source_final_evaluation_sha256": "a" * 64,
        "source_prior_final_evaluation_sha256": "b" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "c" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-9",
        "previous_pool_address": "pool-9",
        "previous_entry_observed_at": "2026-09-30T17:00:00+00:00",
        "previous_final_settlement_observed_at": "2026-09-30T18:00:00+00:00",
        "previous_incumbent_position_id": "p8-pair-9-incumbent",
        "previous_challenger_position_id": "p8-pair-9-challenger",
        "previous_incumbent_closed_trades": 12,
        "previous_challenger_closed_trades": 12,
        "required_incumbent_closed_trades": 20,
        "required_challenger_closed_trades": 20,
        "pair_both_closed": True,
        "closed_trade_floor_met": False,
        "next_debt_type": MODULE.DEBT_MORE_EVIDENCE,
        "continuation_route": MODULE.ROUTE_NEXT_PAIR,
        "next_pair_review_ready": True,
        "promotion_review_ready": False,
        "validation_review_ready": False,
        "separate_next_action_authorization_required": True,
        "read_only": True,
        "new_pair_entry_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
    }
    return {
        **identity,
        "handoff_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(value: dict) -> None:
    identity = {field: value[field] for field in MODULE.HANDOFF_FIELDS}
    value["handoff_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_valid_handoff_passes_contract():
    MODULE.validate_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
        _handoff()
    )


def test_digest_tamper_fails_closed():
    value = _handoff()
    value["previous_pair_id"] = "pair-10"
    with pytest.raises(ValueError, match="digest mismatch"):
        MODULE.validate_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
            value
        )


def test_pair_must_be_fully_closed():
    value = _handoff()
    value["pair_both_closed"] = False
    _reseal(value)
    with pytest.raises(ValueError, match="both prior legs closed"):
        MODULE.validate_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
            value
        )


def test_handoff_requires_more_evidence():
    value = _handoff()
    value["previous_incumbent_closed_trades"] = 20
    value["previous_challenger_closed_trades"] = 20
    _reseal(value)
    with pytest.raises(ValueError, match="does not require more evidence"):
        MODULE.validate_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
            value
        )


def test_wrong_route_fails_closed():
    value = _handoff()
    value["continuation_route"] = "WRONG_ROUTE"
    _reseal(value)
    with pytest.raises(ValueError, match="route mismatch"):
        MODULE.validate_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
            value
        )


@pytest.mark.parametrize(
    "field",
    [
        "new_pair_entry_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
    ],
)
def test_handoff_cannot_authorize_execution_or_promotion(field: str):
    value = _handoff()
    value[field] = True
    _reseal(value)
    with pytest.raises(ValueError, match=f"{field}=false"):
        MODULE.validate_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
            value
        )


def test_contract_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "sqlite3" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "subprocess" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "promote_continuous_challenger(" not in source
