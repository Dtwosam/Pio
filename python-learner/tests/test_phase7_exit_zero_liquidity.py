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
    / "check_phase7_controlled_live_exit_zero_liquidity.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_zero_liquidity",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
WALLET = "44444444444444444444444444444444"


class _FakeConfirmation:
    @staticmethod
    def validate_exit_submission_confirmation(value):
        assert isinstance(value, dict)


def _confirmation() -> dict:
    return {
        "confirmation_sha256": "a" * 64,
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "exit_decision_id": "01234567-89ab-4def-8123-456789abcdef",
        "signature": "sig-exit",
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "observation_status": "CONFIRMED",
        "transaction_chain_confirmation_observed": True,
        "zero_liquidity_proof_required": True,
        "automatic_resubmission_performed": False,
    }


def _snapshot(
    *,
    total_x: str = "0",
    total_y: str = "0",
    fee_x: str = "5",
    fee_y: str = "0",
    reward_one: str = "7",
    reward_two: str = "0",
    liquidities: tuple[str, ...] = ("0", "0"),
    pool: str = POOL,
    owner: str = WALLET,
) -> dict:
    bins = [
        {
            "bin_id": index,
            "position_liquidity": liquidity,
        }
        for index, liquidity in enumerate(liquidities)
    ]
    return {
        "position_address": POSITION,
        "capture_slot_start": 200,
        "capture_slot_end": 200,
        "pool_address": pool,
        "owner": owner,
        "fee_owner": owner,
        "lower_bin_id": 0,
        "upper_bin_id": max(0, len(bins) - 1),
        "total_x_amount": total_x,
        "total_y_amount": total_y,
        "fee_x": fee_x,
        "fee_y": fee_y,
        "reward_one": reward_one,
        "reward_two": reward_two,
        "last_updated_at": 1,
        "total_claimed_fee_x_amount": "0",
        "total_claimed_fee_y_amount": "0",
        "supports_limit_order": False,
        "reward_mints": [
            "11111111111111111111111111111111",
            "11111111111111111111111111111111",
        ],
        "bins": bins,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    snapshot: dict | None = None,
    confirmation_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        confirmation = _confirmation()
        if confirmation_override:
            confirmation.update(confirmation_override)
        confirmation_path = _write(
            root / "confirmation.json",
            confirmation,
        )

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_confirmation_module",
            lambda source: _FakeConfirmation,
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

        report = MODULE.build_exit_zero_liquidity_proof(
            source_tree=ROOT,
            saved_confirmation_path=confirmation_path,
            expected_confirmation_sha256=confirmation[
                "confirmation_sha256"
            ],
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


def test_confirmed_exit_proves_zero_principal_and_liquidity(monkeypatch):
    report, observed = _build(monkeypatch)

    assert observed["position"] == POSITION
    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert MODULE.LIVE_SUBMIT_ENV not in observed["env"]
    assert observed["env"]["SOLANA_RPC_URL"] == RPC_URL

    assert report["position_account_present"] is True
    assert report["principal_x_zero"] is True
    assert report["principal_y_zero"] is True
    assert report["all_position_liquidity_zero"] is True
    assert report["zero_liquidity_proven"] is True
    assert report["nonzero_position_liquidity_bins"] == 0
    assert report["claimable_fee_or_reward_may_remain"] is True
    assert report["settlement_or_claim_step_required"] is True
    assert report["separate_position_close_required"] is True
    assert report["post_close_account_absence_proof_required"] is True


def test_zero_liquidity_can_hold_with_no_claimable_fees_or_rewards(monkeypatch):
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
    assert report["separate_position_close_required"] is True


def test_nonzero_principal_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="still has principal token amounts",
    ):
        _build(
            monkeypatch,
            snapshot=_snapshot(total_x="1"),
        )


def test_nonzero_bin_liquidity_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="still has nonzero liquidity shares",
    ):
        _build(
            monkeypatch,
            snapshot=_snapshot(liquidities=("0", "10")),
        )


def test_pool_identity_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="pool mismatch"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                pool="77777777777777777777777777777777"
            ),
        )


def test_owner_identity_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="owner mismatch"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                owner="77777777777777777777777777777777"
            ),
        )


def test_unconfirmed_submission_cannot_get_zero_liquidity_proof(monkeypatch):
    with pytest.raises(
        ValueError,
        match="requires CONFIRMED submission",
    ):
        _build(
            monkeypatch,
            confirmation_override={
                "observation_status": "PENDING",
                "transaction_chain_confirmation_observed": False,
                "zero_liquidity_proof_required": False,
            },
        )


def test_rpc_endpoint_must_match_confirmation(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            confirmation_override={
                "rpc_endpoint_sha256": "f" * 64
            },
        )


def test_resealed_proof_cannot_claim_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_attempted"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_attempted=false",
    ):
        MODULE.validate_exit_zero_liquidity_proof(report)


def test_zero_liquidity_tool_is_read_only_inspection_only():
    source = TOOL.read_text(encoding="utf-8")

    assert 'INSPECT_COMMAND = "inspect-position-env"' in source
    assert "send_transaction" not in source
    assert "controlled-live-submit" not in source
    assert "sign_message" not in source
    assert "PIO_EXECUTOR_KEYPAIR" in source
    assert "env.pop(KEYPAIR_ENV, None)" in source
    assert '"transaction_signing_performed": False' in source
    assert '"transaction_submission_attempted": False' in source
