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
    / "build_phase7_controlled_live_exit_terminal_receipt.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_terminal_receipt",
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
SIGNATURE = (
    "5NfR2yR4tXbFp7YqZ3wDqM7K4k8Qm9vY6YV7A7A7A7"
    "A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A7A"
)


class _FakeObservationModule:
    @staticmethod
    def validate_post_execution_observation(value):
        assert isinstance(value, dict)


def _observation(status: str = "CONFIRMED") -> dict:
    confirmed = status == "CONFIRMED"
    failed = status == "FAILED"
    return {
        "observation_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "signature": SIGNATURE,
        "observation_status": status,
        "observation_error": (
            "InstructionError(0, Custom(1))" if failed else None
        ),
        "terminal_exit_receipt_required": confirmed or failed,
        "automatic_resubmission_performed": False,
        "live_capital_movement_confirmed": False,
        "production_pio_database_modified": False,
    }


def _remove_event(
    *,
    pool: str = POOL,
    position: str = POSITION,
    owner: str = WALLET,
) -> dict:
    return {
        "event_index": 0,
        "parent_ix_index": 0,
        "event": {
            "event_type": "RemoveLiquidity",
            "event": {
                "lb_pair": pool,
                "from": owner,
                "position": position,
                "amount_x": "1000",
                "amount_y": "2000",
                "active_bin_id": 7,
            },
        },
    }


def _snapshot(
    *,
    succeeded: bool = True,
    events: list | None = None,
) -> dict:
    return {
        "signature": SIGNATURE,
        "slot": 123456,
        "block_time": 1_800_000_000,
        "network_fee_lamports": 5000,
        "compute_units_consumed": 100000,
        "succeeded": succeeded,
        "add_requests": [],
        "rebalance_requests": [],
        "events": (
            [_remove_event()] if events is None and succeeded else (
                [] if events is None else events
            )
        ),
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    status: str = "CONFIRMED",
    snapshot: dict | None = None,
    observation_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        observation = _observation(status)
        if observation_override:
            observation.update(observation_override)
        observation_path = _write(
            root / "observation.json",
            observation,
        )

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_observation_module",
            lambda source: _FakeObservationModule,
        )
        observed = {}

        def fake_run_json(*, executor_binary, args, env):
            observed["binary"] = executor_binary
            observed["args"] = args
            observed["env"] = env
            return snapshot or _snapshot(
                succeeded=(status == "CONFIRMED"),
            )

        monkeypatch.setattr(MODULE, "_run_json", fake_run_json)

        report = MODULE.build_exit_terminal_receipt(
            source_tree=ROOT,
            saved_observation_path=observation_path,
            expected_observation_sha256=observation[
                "observation_sha256"
            ],
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


def test_confirmed_receipt_binds_remove_liquidity_event(monkeypatch):
    report, observed = _build(monkeypatch)

    assert observed["args"] == [
        MODULE.RECEIPT_COMMAND,
        SIGNATURE,
    ]
    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert MODULE.LIVE_SUBMIT_ENV not in observed["env"]
    assert observed["env"]["SOLANA_RPC_URL"] == RPC_URL
    assert report["transaction_succeeded"] is True
    assert report["remove_liquidity_event_count"] == 1
    assert report["matching_remove_liquidity_event_count"] == 1
    assert report["remove_liquidity_lb_pair"] == POOL
    assert report["remove_liquidity_position"] == POSITION
    assert report["remove_liquidity_from"] == WALLET
    assert report["remove_liquidity_amount_x"] == "1000"
    assert report["remove_liquidity_amount_y"] == "2000"
    assert report["terminal_receipt_ready"] is True
    assert report["position_zero_liquidity_proof_required"] is True
    assert report["failure_recovery_required"] is False
    assert report["settlement_authorized"] is False


def test_failed_receipt_opens_recovery_only(monkeypatch):
    report, _ = _build(
        monkeypatch,
        status="FAILED",
        snapshot=_snapshot(succeeded=False, events=[]),
    )

    assert report["transaction_succeeded"] is False
    assert report["matching_remove_liquidity_event_count"] == 0
    assert report["remove_liquidity_event_matches_execution"] is False
    assert report["position_zero_liquidity_proof_required"] is False
    assert report["failure_recovery_required"] is True
    assert report["settlement_authorized"] is False


def test_pending_observation_cannot_build_terminal_receipt(monkeypatch):
    with pytest.raises(ValueError, match="confirmed or failed"):
        _build(
            monkeypatch,
            status="PENDING",
        )


def test_confirmed_receipt_requires_matching_remove_event(monkeypatch):
    with pytest.raises(
        ValueError,
        match="exactly one matching remove-liquidity event",
    ):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                succeeded=True,
                events=[
                    _remove_event(
                        position="77777777777777777777777777777777"
                    )
                ],
            ),
        )


def test_snapshot_outcome_must_match_confirmation(monkeypatch):
    with pytest.raises(
        ValueError,
        match="outcome differs from chain observation",
    ):
        _build(
            monkeypatch,
            status="CONFIRMED",
            snapshot=_snapshot(succeeded=False, events=[]),
        )


def test_add_request_in_exit_snapshot_fails_closed(monkeypatch):
    snapshot = _snapshot()
    snapshot["add_requests"] = [{"unexpected": True}]

    with pytest.raises(ValueError, match="unexpectedly contains add request"):
        _build(
            monkeypatch,
            snapshot=snapshot,
        )


def test_rpc_endpoint_must_match_observation(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            observation_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_resealed_receipt_cannot_authorize_settlement(monkeypatch):
    report, _ = _build(monkeypatch)
    report["settlement_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="settlement_authorized=false"):
        MODULE.validate_exit_terminal_receipt(report)


def test_resealed_receipt_cannot_claim_reconciliation(monkeypatch):
    report, _ = _build(monkeypatch)
    report["live_capital_effect_reconciled"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="live_capital_effect_reconciled=false",
    ):
        MODULE.validate_exit_terminal_receipt(report)


def test_terminal_receipt_tool_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'RECEIPT_COMMAND = "inspect-transaction-events-env"' in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "while True" not in source
    assert "for attempt" not in source
    assert '"settlement_authorized": False' in source
    assert '"live_capital_effect_reconciled": False' in source
