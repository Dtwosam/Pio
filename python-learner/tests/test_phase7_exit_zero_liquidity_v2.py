from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_exit_zero_liquidity_v2.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_zero_liquidity_v2",
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
SIGNATURE = "sig-confirmed-exit"


class _FakeReceiptModule:
    @staticmethod
    def validate_exit_terminal_receipt(value):
        assert isinstance(value, dict)


def _receipt() -> dict:
    return {
        "receipt_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "signature": SIGNATURE,
        "transaction_slot": 120,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "terminal_receipt_status": "CONFIRMED",
        "transaction_succeeded": True,
        "remove_liquidity_event_matches_execution": True,
        "matching_remove_liquidity_event_count": 1,
        "position_zero_liquidity_proof_required": True,
        "settlement_authorized": False,
        "automatic_resubmission_performed": False,
        "live_capital_effect_reconciled": False,
    }


def _bin(
    *,
    liquidity: str = "0",
    amount_x: str = "0",
    amount_y: str = "0",
) -> dict:
    return {
        "bin_id": 7,
        "price": "1",
        "bin_x_amount": "100",
        "bin_y_amount": "200",
        "bin_liquidity": "300",
        "bin_fee_x_per_token_stored": "0",
        "bin_fee_y_per_token_stored": "0",
        "bin_reward_per_token_stored": ["0", "0"],
        "position_liquidity": liquidity,
        "position_x_amount": amount_x,
        "position_y_amount": amount_y,
        "position_fee_x_amount": "5",
        "position_fee_y_amount": "0",
        "position_reward_amounts": ["1", "0"],
    }


def _snapshot(
    *,
    capture_slot_start: int = 121,
    capture_slot_end: int = 121,
    pool: str = POOL,
    owner: str = WALLET,
    fee_owner: str = MODULE.DEFAULT_PUBKEY,
    total_x: str = "0",
    total_y: str = "0",
    fee_x: str = "5",
    fee_y: str = "0",
    reward_one: str = "1",
    reward_two: str = "0",
    bins: list[dict] | None = None,
) -> dict:
    return {
        "position_address": POSITION,
        "capture_slot_start": capture_slot_start,
        "capture_slot_end": capture_slot_end,
        "pool_address": pool,
        "owner": owner,
        "fee_owner": fee_owner,
        "lower_bin_id": -10,
        "upper_bin_id": 10,
        "total_x_amount": total_x,
        "total_y_amount": total_y,
        "fee_x": fee_x,
        "fee_y": fee_y,
        "reward_one": reward_one,
        "reward_two": reward_two,
        "last_updated_at": 1_800_000_000,
        "total_claimed_fee_x_amount": "0",
        "total_claimed_fee_y_amount": "0",
        "supports_limit_order": False,
        "reward_mints": [MODULE.DEFAULT_PUBKEY, MODULE.DEFAULT_PUBKEY],
        "bins": [_bin()] if bins is None else bins,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    snapshot: dict | None = None,
    receipt_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        receipt = _receipt()
        if receipt_override:
            receipt.update(receipt_override)
        receipt_path = _write(root / "receipt.json", receipt)

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_receipt_module",
            lambda source: _FakeReceiptModule,
        )
        observed = {}

        def fake_inspect_position(
            *,
            executor_binary,
            position_address,
            env,
        ):
            observed["binary"] = executor_binary
            observed["position"] = position_address
            observed["env"] = env
            return snapshot or _snapshot()

        monkeypatch.setattr(
            MODULE,
            "_inspect_position",
            fake_inspect_position,
        )
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

        report = MODULE.build_exit_zero_liquidity_proof(
            source_tree=ROOT,
            saved_receipt_path=receipt_path,
            expected_receipt_sha256=receipt["receipt_sha256"],
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            rpc_url=RPC_URL,
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["proof_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_confirmed_exit_proves_zero_liquidity_after_transaction(monkeypatch):
    report, observed = _build(monkeypatch)

    assert observed["position"] == POSITION
    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert MODULE.LIVE_SUBMIT_ENV not in observed["env"]
    assert "RPC_URL" not in observed["env"]
    assert observed["env"]["SOLANA_RPC_URL"] == RPC_URL

    assert report["transaction_slot"] == 120
    assert report["capture_slot_start"] == 121
    assert report["post_transaction_snapshot_order_valid"] is True
    assert report["principal_x_zero"] is True
    assert report["principal_y_zero"] is True
    assert report["all_position_liquidity_zero"] is True
    assert report["all_position_x_zero"] is True
    assert report["all_position_y_zero"] is True
    assert report["zero_liquidity_proven"] is True
    assert report["claimable_fee_or_reward_may_remain"] is True
    assert report["settlement_preparation_required"] is True
    assert report["position_account_close_required"] is True
    assert report["settlement_authorized"] is False
    assert report["transaction_submission_attempted"] is False


def test_zero_liquidity_can_have_no_claimables(monkeypatch):
    report, _ = _build(
        monkeypatch,
        snapshot=_snapshot(
            fee_x="0",
            fee_y="0",
            reward_one="0",
            reward_two="0",
        ),
    )

    assert report["zero_liquidity_proven"] is True
    assert report["claimable_fee_or_reward_may_remain"] is False
    assert report["settlement_preparation_required"] is True


def test_snapshot_cannot_precede_confirmed_transaction(monkeypatch):
    with pytest.raises(ValueError, match="predates confirmed transaction"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                capture_slot_start=119,
                capture_slot_end=119,
            ),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("total_x", "1", "principal X"),
        ("total_y", "1", "principal Y"),
    ],
)
def test_nonzero_position_principal_fails_closed(
    monkeypatch,
    field,
    value,
    message,
):
    kwargs = {field: value}
    with pytest.raises(ValueError, match=message):
        _build(
            monkeypatch,
            snapshot=_snapshot(**kwargs),
        )


def test_nonzero_bin_liquidity_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="nonzero liquidity shares"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                bins=[_bin(liquidity="1")],
            ),
        )


@pytest.mark.parametrize(
    "bins",
    [
        [_bin(amount_x="1")],
        [_bin(amount_y="1")],
    ],
)
def test_nonzero_bin_principal_fails_closed(monkeypatch, bins):
    with pytest.raises(
        ValueError,
        match="bins still contain principal token amounts",
    ):
        _build(
            monkeypatch,
            snapshot=_snapshot(bins=bins),
        )


def test_pool_identity_mismatch_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="snapshot pool mismatch"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                pool="77777777777777777777777777777777",
            ),
        )


def test_owner_identity_mismatch_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="snapshot owner mismatch"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                owner="77777777777777777777777777777777",
            ),
        )


def test_unsupported_fee_owner_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="fee owner is unsupported"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                fee_owner="77777777777777777777777777777777",
            ),
        )


def test_executor_fee_owner_is_supported(monkeypatch):
    report, _ = _build(
        monkeypatch,
        snapshot=_snapshot(fee_owner=WALLET),
    )

    assert report["fee_owner_supported_for_settlement"] is True


def test_rpc_endpoint_must_match_receipt(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            receipt_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_unconfirmed_receipt_cannot_enter_zero_liquidity_proof(monkeypatch):
    with pytest.raises(ValueError, match="requires CONFIRMED receipt"):
        _build(
            monkeypatch,
            receipt_override={
                "terminal_receipt_status": "FAILED",
                "transaction_succeeded": False,
                "position_zero_liquidity_proof_required": False,
            },
        )


def test_resealed_proof_cannot_authorize_settlement(monkeypatch):
    report, _ = _build(monkeypatch)
    report["settlement_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="settlement_authorized=false"):
        MODULE.validate_exit_zero_liquidity_proof(report)


def test_resealed_proof_cannot_claim_reconciliation(monkeypatch):
    report, _ = _build(monkeypatch)
    report["live_capital_effect_reconciled"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="live_capital_effect_reconciled=false",
    ):
        MODULE.validate_exit_zero_liquidity_proof(report)


def test_zero_liquidity_tool_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'INSPECT_COMMAND = "inspect-position-env"' in source
    assert "env.pop(KEYPAIR_ENV, None)" in source
    assert "env.pop(LIVE_SUBMIT_ENV, None)" in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "controlled-live-submit" not in source
    assert "while True" not in source
    assert "for attempt" not in source
    assert '"settlement_authorized": False' in source
    assert '"transaction_submission_attempted": False' in source
    assert '"automatic_resubmission_performed": False' in source
