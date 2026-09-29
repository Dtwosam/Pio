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
    / "check_phase7_controlled_live_open_position_lifecycle.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_open_position_lifecycle",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"
DECISION_ID = "11111111-2222-4333-8444-555555555555"
SIGNATURE = "sig-1"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"


def _handoff() -> dict:
    return {
        "handoff_sha256": "a" * 64,
        "continuation_route": MODULE._load_reviewed.__name__ and "OPEN_POSITION_LIFECYCLE",
        "position_status": "OPEN",
        "position_address": POSITION,
        "new_entry_evidence_candidate": False,
        "decision_id": DECISION_ID,
        "signature": SIGNATURE,
        "pool_address": POOL,
    }


def _receipt(binary_sha: str) -> dict:
    return {
        "receipt_artifact_sha256": "b" * 64,
        "decision_id": DECISION_ID,
        "signature": SIGNATURE,
        "pool_address": POOL,
        "transaction_chain_confirmation_observed": True,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "executor_binary_sha256": binary_sha,
    }


def _snapshot(
    *,
    position: str = POSITION,
    pool: str = POOL,
) -> dict:
    return {
        "position_address": position,
        "capture_slot_start": 100,
        "capture_slot_end": 101,
        "pool_address": pool,
        "owner": "44444444444444444444444444444444",
        "fee_owner": "55555555555555555555555555555555",
        "lower_bin_id": -10,
        "upper_bin_id": 10,
        "total_x_amount": "1000",
        "total_y_amount": "2000",
        "fee_x": "5",
        "fee_y": "6",
        "reward_one": "7",
        "reward_two": "8",
        "last_updated_at": 123456,
        "total_claimed_fee_x_amount": "0",
        "total_claimed_fee_y_amount": "0",
        "supports_limit_order": False,
        "reward_mints": [
            "66666666666666666666666666666666",
            "77777777777777777777777777777777",
        ],
        "bins": [{}, {}],
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    snapshot: dict | None = None,
    handoff_override: dict | None = None,
    receipt_override: dict | None = None,
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        handoff = _handoff()
        if handoff_override:
            handoff.update(handoff_override)
        receipt = _receipt(binary_sha)
        if receipt_override:
            receipt.update(receipt_override)

        handoff_path = _write(root / "handoff.json", handoff)
        receipt_path = _write(root / "receipt.json", receipt)

        class FakeHandoff:
            ROUTE_OPEN = "OPEN_POSITION_LIFECYCLE"

            @staticmethod
            def validate_post_reconciliation_evidence_handoff(value):
                assert isinstance(value, dict)

        class FakeReceipt:
            @staticmethod
            def validate_receipt_artifact(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (FakeHandoff, FakeReceipt),
        )
        monkeypatch.setattr(
            MODULE,
            "_inspect_position",
            lambda **kwargs: copy.deepcopy(snapshot or _snapshot()),
        )

        return MODULE.build_open_position_lifecycle_status(
            source_tree=ROOT,
            saved_handoff_path=handoff_path,
            expected_handoff_sha256=handoff["handoff_sha256"],
            saved_execution_receipt_path=receipt_path,
            expected_execution_receipt_sha256=receipt["receipt_artifact_sha256"],
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            rpc_url=RPC_URL,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["lifecycle_status_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_open_position_snapshot_is_read_only_and_non_authorizing(monkeypatch):
    report = _build(monkeypatch)

    assert report["decision_id"] == DECISION_ID
    assert report["signature"] == SIGNATURE
    assert report["pool_address"] == POOL
    assert report["position_address"] == POSITION
    assert report["position_account_present"] is True
    assert report["position_closed_proven"] is False
    assert report["open_position_snapshot_ready"] is True
    assert report["lifecycle_route"] == "OPEN_POSITION_OBSERVATION"
    assert report["bin_count"] == 2
    assert report["requires_separate_exit_decision"] is True
    assert report["requires_separate_exit_authorization"] is True
    assert report["requires_separate_exit_transaction"] is True
    assert report["exit_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False


def test_handoff_must_be_routed_to_open_position_lifecycle(monkeypatch):
    with pytest.raises(ValueError, match="not routed to open-position lifecycle"):
        _build(
            monkeypatch,
            handoff_override={"continuation_route": "EVIDENCE_REVIEW"},
        )


def test_execution_receipt_identity_must_match_handoff(monkeypatch):
    with pytest.raises(ValueError, match="decision differs"):
        _build(
            monkeypatch,
            receipt_override={
                "decision_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
            },
        )


def test_snapshot_position_must_match_handoff(monkeypatch):
    with pytest.raises(ValueError, match="address differs"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                position="88888888888888888888888888888888"
            ),
        )


def test_snapshot_pool_must_match_handoff(monkeypatch):
    with pytest.raises(ValueError, match="pool differs"):
        _build(
            monkeypatch,
            snapshot=_snapshot(
                pool="99999999999999999999999999999999"
            ),
        )


def test_rpc_endpoint_is_bound_to_execution_receipt(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint differs"):
        _build(
            monkeypatch,
            receipt_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_resealed_status_cannot_authorize_exit(monkeypatch):
    report = _build(monkeypatch)
    report["exit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="exit_authorized=false"):
        MODULE.validate_open_position_lifecycle_status(report)


def test_resealed_status_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="transaction_submission_authorized=false"):
        MODULE.validate_open_position_lifecycle_status(report)


def test_resealed_status_cannot_authorize_new_capital(monkeypatch):
    report = _build(monkeypatch)
    report["new_live_capital_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="new_live_capital_authorized=false"):
        MODULE.validate_open_position_lifecycle_status(report)


def test_inspect_environment_strips_signing_and_live_submit(monkeypatch):
    monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
    monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
    monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

    env = MODULE._inspect_env(rpc_url=RPC_URL)

    assert MODULE.KEYPAIR_ENV not in env
    assert MODULE.LIVE_SUBMIT_ENV not in env
    assert "RPC_URL" not in env
    assert env["SOLANA_RPC_URL"] == RPC_URL


def test_lifecycle_tool_only_uses_read_only_position_inspection():
    source = TOOL.read_text(encoding="utf-8")

    assert 'INSPECT_COMMAND = "inspect-position-env"' in source
    assert '"controlled-live-submit-once"' not in source
    assert '"controlled-live-submit"' not in source
    assert '"execution-confirmation"' not in source
    assert '"execution-recovery"' not in source
    assert '"verify-position-closed"' not in source
    assert '"exit_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
