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
    / "check_phase8_paired_ml_paper_repeat_entry_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_repeat_entry_execution_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _Record:
    def __init__(self, value: dict):
        self.value = copy.deepcopy(value)
        for key, item in value.items():
            setattr(self, key, item)

    def to_record(self):
        return copy.deepcopy(self.value)


class _Candidate(_Record):
    no_lookahead = True

    def to_frame(self):
        return {"shared": "frame"}


def _input() -> dict:
    return {
        "verification_sha256": "1" * 64,
        "amount_x": 10,
        "amount_y": 20,
        "network_cost_y_atomic": 5,
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
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
    }


def _account() -> dict:
    return {
        "readiness_sha256": "2" * 64,
        "readiness_status": "READY",
        "zero_open_positions_verified": True,
        "previous_pair_positions_closed": True,
    }


def _request(production: Path, database: Path) -> dict:
    return {
        "request_sha256": "3" * 64,
        "source_repeat_account_readiness_sha256": "2" * 64,
        "repeat_entry_input_verification_sha256": "1" * 64,
        "source_final_evaluation_sha256": "4" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": "5" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "previous_pair_id": "pair-1",
        "pair_id": "pair-2",
        "pool_address": "pool-2",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "incumbent_position_id": "p8-pair-2-incumbent",
        "challenger_position_id": "p8-pair-2-challenger",
        "incumbent_event_key": "p8-pair-2:incumbent",
        "challenger_event_key": "p8-pair-2:challenger",
    }


def _verification() -> dict:
    return {
        "verification_sha256": "6" * 64,
        "request_sha256": "3" * 64,
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "expires_at": "2026-09-30T10:00:00Z",
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, account_drift: bool = False):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"db")

    saved_account = _account()
    request = _request(production, database)
    verification = _verification()
    input_record = _input()

    account_path = _write(root / "account.json", saved_account)
    request_path = _write(root / "request.json", request)
    verification_path = _write(root / "verification.json", verification)
    final_path = _write(root / "final.json", {"fixture": True})
    input_path = _write(root / "input.json", {"fixture": True})
    payload_path = _write(root / "payload.json", {"fixture": True})
    signature_path = root / "signature"
    signature_path.write_bytes(b"sig")
    allowed_path = root / "allowed"
    allowed_path.write_bytes(b"allowed")

    calls = {"scores": [], "frames": 0, "preflights": []}

    class FakeInput:
        @staticmethod
        def verify_phase8_paired_ml_paper_repeat_entry_inputs(**kwargs):
            return copy.deepcopy(input_record)

    class FakeAccount:
        STATUS_READY = "READY"

        @staticmethod
        def validate_phase8_paired_ml_paper_repeat_account_readiness(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paired_ml_paper_repeat_account_readiness(**kwargs):
            value = copy.deepcopy(saved_account)
            if account_drift:
                value["readiness_sha256"] = "9" * 64
            return value

    class FakeRequest:
        @staticmethod
        def validate_phase8_paired_ml_paper_repeat_entry_request(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paired_ml_paper_repeat_entry_request(**kwargs):
            return copy.deepcopy(request)

    class FakeSigner:
        @staticmethod
        def validate_verification(value):
            assert isinstance(value, dict)

        @staticmethod
        def verify_authorization(**kwargs):
            return copy.deepcopy(verification)

    class FakeBaseAccount:
        @staticmethod
        def _production_database(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(path):
            return {
                "database": "5" * 64,
                "wal": None,
                "shm": None,
            }

    class FakeInferenceConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

    class FakePair:
        Storage = FakeStorage
        MLInferenceConfig = FakeInferenceConfig
        StrategyType = staticmethod(lambda value: value)

        @staticmethod
        def retraining_cycle(storage, *, cycle_id):
            return SimpleNamespace(
                status="PAPER_CHALLENGER",
                champion_model_id="champion-1",
                challenger_model_id="challenger-1",
                target_dataset_version="dataset-2",
            )

        @staticmethod
        def _model_status_record(storage, *, model_id, required_status):
            return {
                "model_id": model_id,
                "status": required_status,
                "dataset_version": "dataset-2",
            }

        @staticmethod
        def build_current_ml_candidate_frame(*args, **kwargs):
            calls["frames"] += 1
            return _Candidate(
                {
                    "pool_address": "pool-2",
                    "decision_observed_at": (
                        "2026-09-30T09:10:00+00:00"
                    ),
                    "no_lookahead": True,
                    "rows": [{"row_index": 0}],
                }
            )

        @staticmethod
        def load_registered_ml_v1(storage, *, model_id):
            return model_id

        @staticmethod
        def score_ml_candidates(bundle, frame, *, config):
            assert frame == {"shared": "frame"}
            calls["scores"].append(bundle)
            return _Record(
                {
                    "model_id": bundle,
                    "policy_actionable": False,
                    "research_choice": {"row_index": 0},
                }
            )

        @staticmethod
        def _choice(*, model_id, policy_source, prediction, frame):
            return _Record(
                {
                    "model_id": model_id,
                    "policy_source": policy_source,
                    "pool_address": "pool-2",
                    "decision_observed_at": (
                        "2026-09-30T09:10:00+00:00"
                    ),
                    "row_index": 0,
                    "strategy": "SPOT",
                    "half_width": 2,
                    "center_offset": 0,
                    "min_bin_id": 98,
                    "max_bin_id": 102,
                }
            )

        @staticmethod
        def _preflight_choice(storage, *, choice, **kwargs):
            calls["preflights"].append(choice.policy_source)
            return _Record(
                {
                    "policy_source": choice.policy_source,
                    "pool_address": "pool-2",
                    "chain_bound": True,
                }
            )

    class FakeBaseReadiness:
        @staticmethod
        def _load_reviewed(source):
            return FakeBaseAccount, object(), object(), FakePair

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (
            FakeInput,
            FakeAccount,
            FakeRequest,
            FakeSigner,
            FakeBaseReadiness,
        ),
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_repeat_entry_execution_readiness(
            repository=production,
            source_tree=ROOT,
            final_evaluation_path=final_path,
            repeat_entry_input_path=input_path,
            saved_account_readiness_path=account_path,
            request_path=request_path,
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=payload_path,
            signature_path=signature_path,
            allowed_signers_path=allowed_path,
            expected_allowed_signers_sha256="7" * 64,
            now="2026-09-30T09:12:00Z",
        )

    return temp, calls, run


def _reseal(report: dict) -> None:
    identity = {
        field: report[field]
        for field in MODULE.REPORT_FIELDS
    }
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_repeat_readiness_scores_both_models_on_one_shared_frame(monkeypatch):
    temp, calls, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert calls["frames"] == 1
    assert calls["scores"] == ["champion-1", "challenger-1"]
    assert calls["preflights"] == ["ML_CHAMPION", "ML_CHALLENGER"]
    assert report["previous_pair_id"] == "pair-1"
    assert report["pair_id"] == "pair-2"
    assert report["fresh_account_readiness_matches_saved"] is True
    assert report["fresh_request_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True
    assert report[
        "human_repeat_paired_paper_entry_authorization_verified"
    ] is True
    assert report["zero_open_positions_verified"] is True
    assert report["previous_pair_closed_verified"] is True
    assert report["same_candidate_frame_verified"] is True
    assert report["same_decision_snapshot_verified"] is True
    assert report["equal_capital_verified"] is True
    assert report["counterfactual_preflight_verified"] is True
    assert report["no_lookahead_verified"] is True
    assert report["paper_pair_entry_execution_readiness_ready"] is True
    assert report["paper_pair_entry_authorized"] is False
    assert report["paper_pair_entry_executed"] is False
    assert report["live_submit_authorized"] is False


def test_fresh_account_drift_fails_before_model_scoring(monkeypatch):
    temp, calls, run = _build(monkeypatch, account_drift=True)
    try:
        with pytest.raises(ValueError, match="differs from saved"):
            run()
    finally:
        temp.cleanup()

    assert calls["frames"] == 0
    assert calls["scores"] == []
    assert calls["preflights"] == []


def test_resealed_readiness_cannot_reuse_previous_pair(monkeypatch):
    temp, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["pair_id"] = report["previous_pair_id"]
    _reseal(report)
    with pytest.raises(ValueError, match="pair id was not advanced"):
        MODULE.validate_phase8_paired_ml_paper_repeat_entry_execution_readiness(
            report
        )


def test_resealed_readiness_cannot_authorize_pair_open(monkeypatch):
    temp, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_pair_entry_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_pair_entry_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_repeat_entry_execution_readiness(
            report
        )


def test_resealed_readiness_rejects_cash_binding_drift(monkeypatch):
    temp, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["required_pair_cash_quote"] = 2000.0
    _reseal(report)
    with pytest.raises(ValueError, match="cash binding mismatch"):
        MODULE.validate_phase8_paired_ml_paper_repeat_entry_execution_readiness(
            report
        )


def test_repeat_execution_readiness_has_no_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paired_ml_paper_entries(" not in source
    assert "_open_paper_position_in_conn(" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "promote_continuous_challenger(" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_pair_entry_authorized": False' in source
    assert '"paper_pair_entry_executed": False' in source
    assert '"live_submit_authorized": False' in source
