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
    / "build_phase7_controlled_live_exit_settlement_signed_transaction_verification.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_settlement_signed_transaction_verification",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


WALLET = "44444444444444444444444444444444"
DEST_SHA = "b" * 64
TX_SHA = "c" * 64
BLOCKHASH = "11111111111111111111111111111111"


class _FakeRequest:
    @staticmethod
    def validate_exit_settlement_single_execution_request(value):
        assert isinstance(value, dict)


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "destination_config_sha256": DEST_SHA,
        "final_settlement_transaction_sha256": TX_SHA,
        "executor_wallet_pubkey": WALLET,
        "recent_blockhash": BLOCKHASH,
    }


def _nested(**override) -> dict:
    value = {
        "request_sha256": "a" * 64,
        "destination_config_sha256": DEST_SHA,
        "final_settlement_transaction_sha256": TX_SHA,
        "signed_transaction_sha256": "d" * 64,
        "signature": "verified-signature",
        "executor_wallet_pubkey": WALLET,
        "recent_blockhash": BLOCKHASH,
        "unsigned_message_matches_signed_message": True,
        "fee_payer_matches_executor": True,
        "blockhash_matches_request": True,
        "single_required_signature": True,
        "signed_transaction_has_one_signature": True,
        "signature_non_default": True,
        "signature_verified": True,
        "exact_signed_settlement_transaction_verified": True,
    }
    value.update(override)
    return value


def _write(path: Path, value: str | dict) -> Path:
    if isinstance(value, dict):
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    nested: dict | None = None,
    expected_binary_sha: str | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request = _request()
        request_path = _write(root / "request.json", request)
        signed_path = _write(root / "signed.txt", "signed-base64")
        verifier = root / "verifier"
        verifier.write_bytes(b"reviewed-verifier")
        verifier.chmod(verifier.stat().st_mode | stat.S_IXUSR)
        verifier_sha = hashlib.sha256(verifier.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_request_module",
            lambda source: _FakeRequest,
        )
        observed = {}

        def fake_run_verifier(
            *,
            verifier_binary,
            request_path,
            signed_transaction_path,
            env,
        ):
            observed["env"] = env
            observed["binary"] = verifier_binary
            return nested or _nested()

        monkeypatch.setattr(MODULE, "_run_verifier", fake_run_verifier)
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.invalid")
        monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

        report = MODULE.build_settlement_signed_transaction_verification(
            source_tree=ROOT,
            saved_request_path=request_path,
            expected_request_sha256=request["request_sha256"],
            signed_transaction_path=signed_path,
            verifier_binary_path=verifier,
            expected_verifier_binary_sha256=(
                expected_binary_sha or verifier_sha
            ),
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["verification_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_exact_signed_settlement_is_sealed_without_execution(monkeypatch):
    report, observed = _build(monkeypatch)

    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert MODULE.LIVE_SUBMIT_ENV not in observed["env"]
    assert "SOLANA_RPC_URL" not in observed["env"]
    assert "RPC_URL" not in observed["env"]
    assert report["destination_config_sha256"] == DEST_SHA
    assert report["request_final_settlement_transaction_sha256"] == TX_SHA
    assert report["signature_verified"] is True
    assert report["exact_signed_settlement_transaction_verified"] is True
    assert report["verification_only"] is True
    assert report["transaction_signing_performed"] is False
    assert report["transaction_submission_attempted"] is False


def test_destination_binding_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="destination_config_sha256 binding mismatch",
    ):
        _build(
            monkeypatch,
            nested=_nested(destination_config_sha256="f" * 64),
        )


def test_transaction_binding_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="final_settlement_transaction_sha256 binding mismatch",
    ):
        _build(
            monkeypatch,
            nested=_nested(
                final_settlement_transaction_sha256="f" * 64
            ),
        )


def test_unverified_signature_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="signature_verified=true",
    ):
        _build(
            monkeypatch,
            nested=_nested(signature_verified=False),
        )


def test_verifier_binary_trust_root_is_external(monkeypatch):
    with pytest.raises(ValueError, match="external trust root"):
        _build(monkeypatch, expected_binary_sha="f" * 64)


def test_resealed_verification_cannot_claim_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_attempted"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_attempted=false",
    ):
        MODULE.validate_settlement_signed_transaction_verification(report)


def test_wrapper_has_no_keypair_signing_or_submission_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "env.pop(KEYPAIR_ENV, None)" in source
    assert "env.pop(LIVE_SUBMIT_ENV, None)" in source
    assert 'env.pop("SOLANA_RPC_URL", None)' in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"transaction_signing_performed": False' in source
    assert '"transaction_submission_attempted": False' in source
