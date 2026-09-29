from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "run_phase8_paired_ml_paper_entry_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "run_phase8_paired_ml_paper_entry_once",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record_hash(value: dict) -> str:
    return hashlib.sha256(
        MODULE._canonical_bytes(value)
    ).hexdigest()


def _candidate_frame() -> dict:
    return {
        "pool_address": "pool-1",
        "decision_observed_at": "2026-09-30T00:40:00+00:00",
        "previous_observed_at": "2026-09-30T00:35:00+00:00",
        "lookback_observations": 12,
        "candidates_seen": 2,
        "candidates_built": 2,
        "candidates_dropped": 0,
        "drop_reasons": [],
        "rows": [
            {
                "pool_address": "pool-1",
                "decision_observed_at": "2026-09-30T00:40:00+00:00",
                "strategy": "SPOT",
                "half_width": 2,
                "center_offset": 0,
            }
        ],
        "no_lookahead": True,
    }


def _inference(model_id: str) -> dict:
    return {
        "model_id": model_id,
        "candidates_seen": 2,
        "candidates_eligible": 1,
        "research_choice": {
            "row_index": 0,
            "pool_address": "pool-1",
            "decision_observed_at": "2026-09-30T00:40:00+00:00",
            "strategy": "SPOT",
            "half_width": 2,
            "center_offset": 0,
        },
        "policy_actionable": False,
        "ranking_rule": "test",
        "predictions": [],
    }


def _choice(model_id: str, policy_source: str) -> dict:
    return {
        "model_id": model_id,
        "policy_source": policy_source,
        "row_index": 0,
        "pool_address": "pool-1",
        "decision_observed_at": "2026-09-30T00:40:00+00:00",
        "strategy": "SPOT",
        "half_width": 2,
        "center_offset": 0,
        "min_bin_id": 98,
        "max_bin_id": 102,
        "risk_adjusted_score_bps": 10.0,
        "predicted_positive_excess_probability": 0.7,
        "predicted_range_survival": 0.8,
    }


def _readiness(production: Path, database: Path) -> dict:
    frame = _candidate_frame()
    inc_inf = _inference("champion-1")
    chal_inf = _inference("challenger-1")
    inc_choice = _choice("champion-1", "ML_CHAMPION")
    chal_choice = _choice("challenger-1", "ML_CHALLENGER")
    return {
        "readiness_sha256": "a" * 64,
        "paper_pair_entry_execution_readiness_ready": True,
        "readiness_only": True,
        "requires_immediate_one_shot_pair_executor": True,
        "pio_database_path": str(database),
        "pio_database_sha256": _sha(database.read_bytes()),
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "paired_entry_request_sha256": "b" * 64,
        "fresh_signed_authorization_verification_sha256": "c" * 64,
        "paired_entry_input_verification_sha256": "d" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "authorization_expires_at": "2026-09-30T01:00:00Z",
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-1",
        "pool_address": "pool-1",
        "amount_x": 10,
        "amount_y": 20,
        "network_cost_y_atomic": 5,
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "as_of": None,
        "lookback_observations": 12,
        "half_widths": [0, 1, 2, 5, 10],
        "center_offsets": [0],
        "strategies": ["SPOT", "CURVE", "BID_ASK"],
        "max_share_bps": 500,
        "favor_x_in_active_bin": False,
        "near_liquidity_radius": 5,
        "risk_lambda": 1.5,
        "min_positive_excess_probability": 0.55,
        "min_range_survival_probability": 0.5,
        "min_score_bps": 0.0,
        "decision_observed_at": "2026-09-30T00:40:00+00:00",
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "incumbent_event_key": "pair-1-incumbent-enter",
        "challenger_event_key": "pair-1-challenger-enter",
        "candidate_frame_sha256": _record_hash(frame),
        "incumbent_inference_sha256": _record_hash(inc_inf),
        "challenger_inference_sha256": _record_hash(chal_inf),
        "incumbent_choice_sha256": _record_hash(inc_choice),
        "challenger_choice_sha256": _record_hash(chal_choice),
    }


def _account_readiness() -> dict:
    return {
        "account_cash_quote": 5000.0,
        "account_open_positions": 0,
    }


def _signed_verification() -> dict:
    return {
        "approval_payload_sha256": "e" * 64,
        "approval_signature_sha256": "f" * 64,
        "allowed_signers_sha256": "1" * 64,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class _Result:
    def __init__(self, value: dict):
        self.value = value

    def to_record(self) -> dict:
        return copy.deepcopy(self.value)


def _pair_result(*, decision=None, hash_drift=False) -> dict:
    frame = _candidate_frame()
    inc_inf = _inference("champion-1")
    chal_inf = _inference("challenger-1")
    inc_choice = _choice("champion-1", "ML_CHAMPION")
    chal_choice = _choice("challenger-1", "ML_CHALLENGER")
    if decision is not None:
        frame["decision_observed_at"] = decision
        inc_inf["research_choice"]["decision_observed_at"] = decision
        chal_inf["research_choice"]["decision_observed_at"] = decision
        inc_choice["decision_observed_at"] = decision
        chal_choice["decision_observed_at"] = decision
    if hash_drift:
        chal_choice["half_width"] = 5
    return {
        "account_id": "paper-1",
        "cycle_id": "cycle-1",
        "pool_address": "pool-1",
        "decision_observed_at": (
            decision or "2026-09-30T00:40:00+00:00"
        ),
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "incumbent_choice": inc_choice,
        "challenger_choice": chal_choice,
        "incumbent_position": {
            "position_id": "pair-1-incumbent",
            "account_id": "paper-1",
            "status": "OPEN",
            "policy_source": "ML_CHAMPION",
            "model_id": "champion-1",
            "entry_capital_quote": 1000.0,
        },
        "challenger_position": {
            "position_id": "pair-1-challenger",
            "account_id": "paper-1",
            "status": "OPEN",
            "policy_source": "ML_CHALLENGER",
            "model_id": "challenger-1",
            "entry_capital_quote": 1000.0,
        },
        "account": {
            "cash_quote": 2990.0,
            "open_positions": 2,
        },
        "candidate_frame": frame,
        "incumbent_inference": inc_inf,
        "challenger_inference": chal_inf,
        "equal_capital_quote": 1000.0,
        "equal_entry_cost_quote": 5.0,
        "same_candidate_frame": True,
        "same_decision_snapshot": True,
        "equal_capital": True,
        "atomic_pair_open": True,
        "chain_bound": True,
        "no_lookahead": True,
        "paper_only": True,
        "policy_actionable": False,
        "live_authorized": False,
    }


def _build(
    monkeypatch,
    *,
    fresh_readiness_mutator=None,
    result_decision=None,
    result_hash_drift=False,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"before")

    readiness = _readiness(production, database)
    fresh = copy.deepcopy(readiness)
    if fresh_readiness_mutator:
        fresh_readiness_mutator(fresh)

    saved_readiness_path = _write(root / "readiness.json", readiness)
    saved_account_path = _write(
        root / "account-readiness.json",
        _account_readiness(),
    )
    signed_verification_path = _write(
        root / "verification.json",
        _signed_verification(),
    )
    for name in (
        "post-audit.json",
        "paper-evidence-input.json",
        "pair-input.json",
        "request.json",
        "payload.json",
    ):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    state = {"mutated": False}
    call_args = {}

    class FakeAccount:
        @staticmethod
        def _production_database(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(path):
            p = Path(path)
            return {
                "database": _sha(p.read_bytes()),
                "wal": None,
                "shm": None,
            }

    class FakeReadiness:
        @staticmethod
        def validate_phase8_paired_ml_paper_entry_execution_readiness(
            value,
        ):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paired_ml_paper_entry_execution_readiness(
            **kwargs,
        ):
            return copy.deepcopy(fresh)

        @staticmethod
        def _load_reviewed(source):
            return FakeAccount, object(), object(), FakePair

    class FakeInferenceConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

    class FakePair:
        MLInferenceConfig = FakeInferenceConfig
        Storage = FakeStorage
        StrategyType = staticmethod(lambda value: value)

        @staticmethod
        def open_paired_ml_paper_entries(storage, **kwargs):
            call_args.update(kwargs)
            storage.path.write_bytes(b"after-pair-open")
            state["mutated"] = True
            return _Result(
                _pair_result(
                    decision=result_decision,
                    hash_drift=result_hash_drift,
                )
            )

    monkeypatch.setattr(
        MODULE,
        "_load_readiness_module",
        lambda source: FakeReadiness,
    )
    monkeypatch.setattr(
        MODULE,
        "LOCK_PATH",
        root / "paired-paper-one-shot.lock",
    )

    def run():
        return MODULE.execute_phase8_paired_ml_paper_entry_once(
            repository=production,
            source_tree=ROOT,
            saved_readiness_path=saved_readiness_path,
            saved_account_readiness_path=saved_account_path,
            post_audit_path=root / "post-audit.json",
            paper_evidence_input_path=root / "paper-evidence-input.json",
            paired_entry_input_path=root / "pair-input.json",
            request_path=root / "request.json",
            saved_signed_authorization_verification_path=(
                signed_verification_path
            ),
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="1" * 64,
            now="2026-09-30T00:45:00Z",
        )

    return temp, database, state, call_args, run


def _reseal(receipt: dict) -> None:
    identity = {
        field: receipt[field]
        for field in MODULE.RECEIPT_FIELDS
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_readiness_is_exactly_pinned():
    path = ROOT / MODULE.READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.READINESS_TOOL
    ]


def test_one_shot_opens_exact_pair_and_stops_before_scheduler(
    monkeypatch,
):
    temp, _, state, call_args, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    assert state["mutated"] is True
    assert call_args["account_id"] == "paper-1"
    assert call_args["cycle_id"] == "cycle-1"
    assert call_args["pool_address"] == "pool-1"
    assert call_args["capital_quote"] == 1000.0
    assert call_args["entry_cost_quote"] == 5.0
    assert call_args["incumbent_position_id"] == "pair-1-incumbent"
    assert call_args["challenger_position_id"] == "pair-1-challenger"
    assert call_args["as_of"] == "2026-09-30T00:40:00+00:00"

    assert receipt["paper_pair_entry_authorized"] is True
    assert receipt["paper_pair_entry_executed"] is True
    assert receipt["paper_pair_entry_completed"] is True
    assert receipt["atomic_pair_open_verified"] is True
    assert receipt["same_candidate_frame_verified"] is True
    assert receipt["same_decision_snapshot_verified"] is True
    assert receipt["equal_capital_verified"] is True
    assert receipt["chain_bound_verified"] is True
    assert receipt["no_lookahead_verified"] is True
    assert receipt["account_cash_quote_before"] == 5000.0
    assert receipt["account_cash_quote_after"] == 2990.0
    assert receipt["account_open_positions_before"] == 0
    assert receipt["account_open_positions_after"] == 2
    assert receipt["requires_post_pair_audit"] is True
    assert receipt["paper_evidence_collection_authorized"] is False
    assert receipt["paper_trading_authorized"] is False
    assert receipt["live_submit_authorized"] is False
    assert receipt["transaction_submission_performed"] is False
    assert receipt["new_live_capital_used"] is False
    assert receipt["phase8_promotion_authorized"] is False


def test_fresh_readiness_drift_fails_before_pair_open(monkeypatch):
    temp, _, state, _, run = _build(
        monkeypatch,
        fresh_readiness_mutator=lambda value: value.update(
            decision_observed_at="2026-09-30T00:41:00+00:00"
        ),
    )
    try:
        with pytest.raises(ValueError, match="differs from saved"):
            run()
    finally:
        temp.cleanup()

    assert state["mutated"] is False


def test_execution_is_pinned_to_authorized_decision_snapshot(monkeypatch):
    temp, _, _, call_args, run = _build(monkeypatch)
    try:
        run()
    finally:
        temp.cleanup()

    assert call_args["as_of"] == (
        "2026-09-30T00:40:00+00:00"
    )


def test_result_decision_drift_is_detected(monkeypatch):
    temp, _, state, _, run = _build(
        monkeypatch,
        result_decision="2026-09-30T00:41:00+00:00",
    )
    try:
        with pytest.raises(ValueError, match="decision snapshot drifted"):
            run()
    finally:
        temp.cleanup()

    assert state["mutated"] is True


def test_result_choice_hash_drift_is_detected(monkeypatch):
    temp, _, state, _, run = _build(
        monkeypatch,
        result_hash_drift=True,
    )
    try:
        with pytest.raises(ValueError, match="challenger_choice drifted"):
            run()
    finally:
        temp.cleanup()

    assert state["mutated"] is True


def test_resealed_receipt_cannot_authorize_ongoing_paper_trading(
    monkeypatch,
):
    temp, _, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["paper_trading_authorized"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_entry_execution_receipt(
            receipt
        )


def test_resealed_receipt_cannot_claim_live_submission(monkeypatch):
    temp, _, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["transaction_submission_performed"] = True
    _reseal(receipt)
    with pytest.raises(
        ValueError,
        match="transaction_submission_performed=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_entry_execution_receipt(
            receipt
        )


def test_receipt_rejects_wrong_cash_delta(monkeypatch):
    temp, _, _, _, run = _build(monkeypatch)
    try:
        receipt = run()
    finally:
        temp.cleanup()

    receipt["account_cash_quote_after"] = 3000.0
    _reseal(receipt)
    with pytest.raises(ValueError, match="cash delta mismatch"):
        MODULE.validate_phase8_paired_ml_paper_entry_execution_receipt(
            receipt
        )


def test_executor_uses_dedicated_one_shot_lock():
    assert MODULE.LOCK_PATH == Path(
        "/var/tmp/pio-phase8-paired-paper-entry-one-shot.lock"
    )


def test_executor_has_no_scheduler_or_live_submit_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "paper_scheduler" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert '"paper_evidence_collection_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"transaction_submission_performed": False' in source
    assert '"new_live_capital_used": False' in source
