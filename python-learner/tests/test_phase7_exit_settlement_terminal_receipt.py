from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_exit_settlement_terminal_receipt.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_settlement_terminal_receipt",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"
OPENED_DECISION_ID = "11111111-2222-4333-8444-555555555555"
EXIT_DECISION_ID = "01234567-89ab-4def-8123-456789abcdef"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
WALLET = "44444444444444444444444444444444"
SIGNATURE = "sig-settlement"


class _FakeObservation:
    @staticmethod
    def validate_post_execution_observation(value):
        assert isinstance(value, dict)


class _FakeRequest:
    @staticmethod
    def validate_exit_settlement_single_execution_request(value):
        assert isinstance(value, dict)


def _observation(*, status: str = "CONFIRMED") -> dict:
    return {
        "observation_sha256": "a" * 64,
        "request_sha256": "b" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": "c" * 64,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "signature": SIGNATURE,
        "observation_status": status,
        "observation_error": (
            "InstructionError(0, Custom(1))"
            if status == "FAILED"
            else None
        ),
        "terminal_settlement_receipt_required": status in {
            "CONFIRMED",
            "FAILED",
        },
        "automatic_resubmission_performed": False,
        "live_capital_movement_confirmed": False,
        "production_pio_database_modified": False,
    }


def _request(*, reward_indices: list[int] | None = None) -> dict:
    reward_indices = [0] if reward_indices is None else reward_indices
    return {
        "request_sha256": "b" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": "c" * 64,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "reward_token_destinations": [
            {
                "reward_index": index,
                "user_token_account": f"reward-destination-{index}",
            }
            for index in reward_indices
        ],
    }


def _event(event_type: str, payload: dict) -> dict:
    return {
        "event": {
            "event_type": event_type,
            "event": payload,
        }
    }


def _claim_fee() -> dict:
    return _event(
        "ClaimFee2",
        {
            "lb_pair": POOL,
            "position": POSITION,
            "owner": WALLET,
            "fee_x": "11",
            "fee_y": "22",
            "active_bin_id": 7,
        },
    )


def _claim_reward(index: int) -> dict:
    return _event(
        "ClaimReward2",
        {
            "lb_pair": POOL,
            "position": POSITION,
            "owner": WALLET,
            "reward_index": index,
            "total_reward": str(100 + index),
            "active_bin_id": 8,
        },
    )


def _close() -> dict:
    return _event(
        "PositionClose",
        {
            "position": POSITION,
            "owner": WALLET,
        },
    )


def _snapshot(
    *,
    succeeded: bool = True,
    events: list[dict] | None = None,
) -> dict:
    if events is None:
        events = [_claim_fee(), _claim_reward(0), _close()]
    return {
        "signature": SIGNATURE,
        "slot": 200,
        "block_time": 1_700_000_000,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 123456,
        "succeeded": succeeded,
        "add_requests": [],
        "rebalance_requests": [],
        "events": events,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    status: str = "CONFIRMED",
    reward_indices: list[int] | None = None,
    snapshot: dict | None = None,
    observation_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        observation = _observation(status=status)
        if observation_override:
            observation.update(observation_override)
        request = _request(reward_indices=reward_indices)
        observation_path = _write(
            root / "observation.json",
            observation,
        )
        request_path = _write(root / "request.json", request)

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeObservation, _FakeRequest),
        )
        observed = {}

        def fake_run_json(*, executor_binary, args, env):
            observed["binary"] = executor_binary
            observed["args"] = args
            observed["env"] = env
            return snapshot or _snapshot(
                succeeded=status == "CONFIRMED"
            )

        monkeypatch.setattr(MODULE, "_run_json", fake_run_json)
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

        report = MODULE.build_exit_settlement_terminal_receipt(
            source_tree=ROOT,
            saved_observation_path=observation_path,
            expected_observation_sha256=observation[
                "observation_sha256"
            ],
            saved_request_path=request_path,
            expected_request_sha256=request["request_sha256"],
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            rpc_url=RPC_URL,
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_confirmed_settlement_receipt_binds_fee_reward_and_close(monkeypatch):
    report, observed = _build(monkeypatch)

    assert observed["args"] == [MODULE.RECEIPT_COMMAND, SIGNATURE]
    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert MODULE.LIVE_SUBMIT_ENV not in observed["env"]
    assert "RPC_URL" not in observed["env"]
    assert observed["env"]["SOLANA_RPC_URL"] == RPC_URL

    assert report["transaction_succeeded"] is True
    assert report["matching_claim_fee_event_count"] == 1
    assert report["claim_fee_x"] == "11"
    assert report["claim_fee_y"] == "22"
    assert report["matching_reward_indices"] == [0]
    assert report["matching_claim_reward_event_count"] == 1
    assert report["matching_position_close_event_count"] == 1
    assert report["settlement_events_match_request"] is True
    assert report["position_account_absence_proof_required"] is True
    assert report["post_exit_state_reconciliation_required"] is True
    assert report["live_capital_effect_reconciled"] is False
    assert report["automatic_resubmission_performed"] is False


def test_settlement_without_rewards_requires_no_reward_event(monkeypatch):
    report, _ = _build(
        monkeypatch,
        reward_indices=[],
        snapshot=_snapshot(
            events=[_claim_fee(), _close()],
        ),
    )

    assert report["reward_indices_expected"] == []
    assert report["matching_reward_indices"] == []
    assert report["matching_claim_reward_event_count"] == 0
    assert report["settlement_events_match_request"] is True


def test_reward_index_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="reward events differ"):
        _build(
            monkeypatch,
            reward_indices=[0],
            snapshot=_snapshot(
                events=[
                    _claim_fee(),
                    _claim_reward(1),
                    _close(),
                ]
            ),
        )


def test_missing_position_close_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="PositionClose"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                events=[_claim_fee(), _claim_reward(0)]
            ),
        )


def test_unexpected_dlmm_event_fails_closed(monkeypatch):
    unexpected = _event(
        "RemoveLiquidity",
        {
            "lb_pair": POOL,
            "from": WALLET,
            "position": POSITION,
            "amount_x": "0",
            "amount_y": "0",
            "active_bin_id": 7,
        },
    )
    with pytest.raises(ValueError, match="unexpected DLMM event"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                events=[
                    _claim_fee(),
                    _claim_reward(0),
                    _close(),
                    unexpected,
                ]
            ),
        )


def test_failed_settlement_receipt_opens_recovery_only(monkeypatch):
    report, _ = _build(
        monkeypatch,
        status="FAILED",
        snapshot=_snapshot(succeeded=False, events=[]),
    )

    assert report["transaction_succeeded"] is False
    assert report["position_account_absence_proof_required"] is False
    assert report["post_exit_state_reconciliation_required"] is False
    assert report["failure_recovery_required"] is True
    assert report["live_capital_effect_reconciled"] is False
    assert report["automatic_resubmission_performed"] is False


def test_pending_observation_cannot_build_terminal_receipt(monkeypatch):
    with pytest.raises(ValueError, match="confirmed or failed"):
        _build(
            monkeypatch,
            status="PENDING",
            observation_override={
                "terminal_settlement_receipt_required": False,
            },
        )


def test_rpc_endpoint_must_match_request(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            observation_override={
                "rpc_endpoint_sha256": "f" * 64,
            },
        )


def test_resealed_receipt_cannot_claim_reconciliation_complete(monkeypatch):
    report, _ = _build(monkeypatch)
    report["live_capital_effect_reconciled"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="live_capital_effect_reconciled=false",
    ):
        MODULE.validate_exit_settlement_terminal_receipt(report)


def test_terminal_receipt_has_no_submission_or_signing_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'RECEIPT_COMMAND = "inspect-transaction-events-env"' in source
    assert "env.pop(KEYPAIR_ENV, None)" in source
    assert "env.pop(LIVE_SUBMIT_ENV, None)" in source

    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"transaction_submission_attempted": False' in source
    assert '"automatic_resubmission_performed": False' in source
    assert '"live_capital_effect_reconciled": False' in source
