from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_paired_ml_paper_entry_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase8_paired_ml_paper_entry_execution_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _saved_account(production: Path, database: Path) -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "readiness_status": "READY",
        "paired_entry_input_verification_sha256": "b" * 64,
        "source_post_audit_sha256": "c" * 64,
        "source_paper_evidence_input_verification_sha256": "d" * 64,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": _sha(database.read_bytes()),
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-1",
        "pool_address": "pool-1",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "incumbent_event_key": "pair-1-incumbent-enter",
        "challenger_event_key": "pair-1-challenger-enter",
    }


def _request(account: dict) -> dict:
    return {
        "request_sha256": "e" * 64,
        "source_account_readiness_sha256": account["readiness_sha256"],
        "paired_entry_input_verification_sha256": account[
            "paired_entry_input_verification_sha256"
        ],
        "source_post_audit_sha256": account["source_post_audit_sha256"],
        "source_paper_evidence_input_verification_sha256": account[
            "source_paper_evidence_input_verification_sha256"
        ],
        "production_repository": account["production_repository"],
        "pio_database_path": account["pio_database_path"],
        "pio_database_sha256": account["pio_database_sha256"],
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-1",
        "pool_address": "pool-1",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "incumbent_position_id": "pair-1-incumbent",
        "challenger_position_id": "pair-1-challenger",
        "incumbent_event_key": "pair-1-incumbent-enter",
        "challenger_event_key": "pair-1-challenger-enter",
    }


def _verification(request: dict) -> dict:
    return {
        "verification_sha256": "f" * 64,
        "request_sha256": request["request_sha256"],
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "expires_at": "2026-09-30T01:00:00Z",
    }


def _input_verification() -> dict:
    return {
        "verification_sha256": "b" * 64,
        "amount_x": 10,
        "amount_y": 20,
        "network_cost_y_atomic": 5,
        "as_of": "2026-09-30T00:40:00+00:00",
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


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class _Record:
    def __init__(self, value: dict):
        self.value = value

    def to_record(self):
        return copy.deepcopy(self.value)


class _CandidateReport:
    no_lookahead = True
    decision_observed_at = "2026-09-30T00:40:00+00:00"

    def to_frame(self):
        return pd.DataFrame(
            [
                {
                    "pool_address": "pool-1",
                    "decision_observed_at": self.decision_observed_at,
                    "strategy": "SPOT",
                    "half_width": 2,
                    "center_offset": 0,
                    "active_bin_id": 100,
                }
            ]
        )

    def to_record(self):
        return {
            "pool_address": "pool-1",
            "decision_observed_at": self.decision_observed_at,
            "no_lookahead": True,
            "rows": self.to_frame().to_dict(orient="records"),
        }


def _choice(policy: str, model_id: str, *, decision=None):
    return _Record(
        {
            "model_id": model_id,
            "policy_source": policy,
            "row_index": 0,
            "pool_address": "pool-1",
            "decision_observed_at": (
                decision or "2026-09-30T00:40:00+00:00"
            ),
            "strategy": "SPOT",
            "half_width": 2,
            "center_offset": 0,
            "min_bin_id": 98,
            "max_bin_id": 102,
            "risk_adjusted_score_bps": 10.0,
            "predicted_positive_excess_probability": 0.7,
            "predicted_range_survival": 0.8,
        }
    )


def _build(
    monkeypatch,
    *,
    account_mutator=None,
    verification_mutator=None,
    cycle_mutator=None,
    choice_decision=None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"paired-ready")

    account = _saved_account(production, database)
    request = _request(account)
    verification = _verification(request)
    input_verification = _input_verification()

    fresh_account = copy.deepcopy(account)
    if account_mutator:
        account_mutator(fresh_account)
    fresh_verification = copy.deepcopy(verification)
    if verification_mutator:
        verification_mutator(fresh_verification)

    saved_account_path = _write(root / "account.json", account)
    request_path = _write(root / "request.json", request)
    verification_path = _write(root / "verification.json", verification)
    for name in (
        "post-audit.json",
        "paper-evidence-input.json",
        "pair-input.json",
        "payload.json",
    ):
        _write(root / name, {"fixture": True})
    signature = root / "signature"
    signature.write_bytes(b"sig")
    allowed = root / "allowed"
    allowed.write_bytes(b"allowed")

    class FakeInput:
        @staticmethod
        def verify_phase8_paired_ml_paper_entry_inputs(**kwargs):
            return copy.deepcopy(input_verification)

    class FakeAccount:
        STATUS_READY = "READY"

        @staticmethod
        def validate_phase8_paired_ml_paper_account_readiness(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paired_ml_paper_account_readiness(**kwargs):
            return copy.deepcopy(fresh_account)

        @staticmethod
        def _load_pair_input_module(source):
            return FakeInput

        @staticmethod
        def _production_database(repo):
            return (Path(repo) / "data" / "pio.db").resolve()

        @staticmethod
        def _database_state(path):
            return {
                "database": _sha(Path(path).read_bytes()),
                "wal": None,
                "shm": None,
            }

    class FakeRequest:
        @staticmethod
        def validate_phase8_paired_ml_paper_entry_request(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase8_paired_ml_paper_entry_request(**kwargs):
            return copy.deepcopy(request)

    class FakeSigner:
        @staticmethod
        def validate_verification(value):
            assert isinstance(value, dict)

        @staticmethod
        def verify_authorization(**kwargs):
            return copy.deepcopy(fresh_verification)

    cycle = {
        "status": "PAPER_CHALLENGER",
        "champion_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "target_dataset_version": "dataset-v2",
    }
    if cycle_mutator:
        cycle_mutator(cycle)

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

    class FakeInferenceConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakePair:
        Storage = FakeStorage
        MLInferenceConfig = FakeInferenceConfig
        StrategyType = staticmethod(lambda value: value)

        @staticmethod
        def retraining_cycle(storage, *, cycle_id):
            assert cycle_id == "cycle-1"
            return SimpleNamespace(**cycle)

        @staticmethod
        def _model_status_record(storage, *, model_id, required_status):
            return {
                "status": required_status,
                "dataset_version": (
                    "dataset-v2"
                    if model_id == "challenger-1"
                    else "dataset-v1"
                ),
            }

        @staticmethod
        def build_current_ml_candidate_frame(*args, **kwargs):
            return _CandidateReport()

        @staticmethod
        def load_registered_ml_v1(storage, *, model_id):
            return SimpleNamespace(model_id=model_id)

        @staticmethod
        def score_ml_candidates(bundle, frame, *, config):
            prediction = SimpleNamespace(
                row_index=0,
                pool_address="pool-1",
                decision_observed_at=(
                    "2026-09-30T00:40:00+00:00"
                ),
                strategy="SPOT",
                half_width=2,
                center_offset=0,
            )
            return _Record(
                {
                    "model_id": bundle.model_id,
                    "policy_actionable": False,
                    "research_choice": {
                        "row_index": 0,
                        "pool_address": "pool-1",
                        "decision_observed_at": prediction.decision_observed_at,
                    },
                }
            )

        @staticmethod
        def _choice(
            *,
            model_id,
            policy_source,
            prediction,
            frame,
        ):
            return _choice(
                policy_source,
                model_id,
                decision=choice_decision,
            )

        @staticmethod
        def _preflight_choice(storage, *, choice, **kwargs):
            record = choice.to_record()
            return _Record(
                {
                    "pool_address": record["pool_address"],
                    "entry_observed_at": record[
                        "decision_observed_at"
                    ],
                    "strategy": record["strategy"],
                    "min_bin_id": record["min_bin_id"],
                    "max_bin_id": record["max_bin_id"],
                    "chain_bound": True,
                }
            )

    # score result needs attribute access in the readiness tool.
    def score(bundle, frame, *, config):
        prediction = SimpleNamespace(
            row_index=0,
            pool_address="pool-1",
            decision_observed_at="2026-09-30T00:40:00+00:00",
            strategy="SPOT",
            half_width=2,
            center_offset=0,
        )
        return SimpleNamespace(
            policy_actionable=False,
            research_choice=prediction,
            to_record=lambda: {
                "model_id": bundle.model_id,
                "policy_actionable": False,
                "research_choice": {
                    "row_index": 0,
                    "pool_address": "pool-1",
                    "decision_observed_at": (
                        "2026-09-30T00:40:00+00:00"
                    ),
                    "strategy": "SPOT",
                    "half_width": 2,
                    "center_offset": 0,
                },
            },
        )

    FakePair.score_ml_candidates = staticmethod(score)

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed",
        lambda source: (
            FakeAccount,
            FakeRequest,
            FakeSigner,
            FakePair,
        ),
    )

    def run():
        return MODULE.build_phase8_paired_ml_paper_entry_execution_readiness(
            repository=production,
            source_tree=ROOT,
            saved_account_readiness_path=saved_account_path,
            post_audit_path=root / "post-audit.json",
            paper_evidence_input_path=root / "paper-evidence-input.json",
            paired_entry_input_path=root / "pair-input.json",
            request_path=request_path,
            saved_signed_authorization_verification_path=verification_path,
            signed_payload_path=root / "payload.json",
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256="1" * 64,
            now="2026-09-30T00:45:00Z",
        )

    return temp, run


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


def test_readiness_rechecks_same_frame_models_and_preflights(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["fresh_account_readiness_matches_saved"] is True
    assert report["fresh_request_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True
    assert report[
        "human_paired_paper_entry_authorization_verified"
    ] is True
    assert report["account_ready"] is True
    assert report["cycle_model_binding_valid"] is True
    assert report["model_inference_readiness_verified"] is True
    assert report["same_candidate_frame_verified"] is True
    assert report["same_decision_snapshot_verified"] is True
    assert report["equal_capital_verified"] is True
    assert report["counterfactual_preflight_verified"] is True
    assert report["no_lookahead_verified"] is True
    assert report["paper_pair_entry_execution_readiness_ready"] is True
    assert report["readiness_only"] is True
    assert report["paper_pair_entry_authorized"] is False
    assert report["paper_pair_entry_executed"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["decision_observed_at"] == (
        "2026-09-30T00:40:00+00:00"
    )
    assert report["incumbent_choice"]["policy_source"] == "ML_CHAMPION"
    assert report["challenger_choice"]["policy_source"] == "ML_CHALLENGER"


def test_account_readiness_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        account_mutator=lambda value: value.update(
            pio_database_sha256="9" * 64
        ),
    )
    try:
        with pytest.raises(ValueError, match="differs from saved"):
            run()
    finally:
        temp.cleanup()


def test_signed_authorization_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        verification_mutator=lambda value: value.update(
            verification_sha256="9" * 64
        ),
    )
    try:
        with pytest.raises(ValueError, match="differs from saved"):
            run()
    finally:
        temp.cleanup()


def test_cycle_model_binding_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        cycle_mutator=lambda value: value.update(
            challenger_model_id="challenger-2"
        ),
    )
    try:
        with pytest.raises(ValueError, match="cycle/model binding changed"):
            run()
    finally:
        temp.cleanup()


def test_choice_decision_snapshot_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        choice_decision="2026-09-30T00:41:00+00:00",
    )
    try:
        with pytest.raises(
            ValueError,
            match="decision snapshot changed",
        ):
            run()
    finally:
        temp.cleanup()


def test_resealed_readiness_cannot_claim_pair_execution(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_pair_entry_executed"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_pair_entry_executed=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_entry_execution_readiness(
            report
        )


def test_resealed_readiness_cannot_authorize_paper_trading(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_trading_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_entry_execution_readiness(
            report
        )


def test_readiness_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paired_ml_paper_entries(" not in source
    assert "_open_paper_position_in_conn(" not in source
    assert "create_paper_account(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_pair_entry_executed": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
