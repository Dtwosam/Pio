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
    / "build_phase7_controlled_live_exit_signed_transaction_verification.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_signed_transaction_verification",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


WALLET = "44444444444444444444444444444444"
BLOCKHASH = "11111111111111111111111111111111"


class _FakeRequestModule:
    @staticmethod
    def validate_exit_single_execution_request(value):
        assert isinstance(value, dict)


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "final_transaction_sha256": "b" * 64,
        "executor_wallet_pubkey": WALLET,
        "recent_blockhash": BLOCKHASH,
    }


def _nested() -> dict:
    return {
        "request_sha256": "a" * 64,
        "final_transaction_sha256": "b" * 64,
        "signed_transaction_sha256": "c" * 64,
        "signature": "sig-external",
        "executor_wallet_pubkey": WALLET,
        "recent_blockhash": BLOCKHASH,
        "unsigned_message_matches_signed_message": True,
        "fee_payer_matches_executor": True,
        "blockhash_matches_request": True,
        "single_required_signature": True,
        "signed_transaction_has_one_signature": True,
        "signature_non_default": True,
        "signature_verified": True,
        "exact_signed_exit_transaction_verified": True,
    }


def _write(path: Path, value: str | dict) -> Path:
    if isinstance(value, dict):
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    nested_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request = _request()
        request_path = _write(root / "request.json", request)
        signed_path = _write(root / "signed.txt", "signed-base64")

        verifier = root / "phase7-exit-signed-transaction-verifier"
        verifier.write_bytes(b"reviewed-verifier")
        verifier.chmod(verifier.stat().st_mode | stat.S_IXUSR)
        verifier_sha = hashlib.sha256(verifier.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_request_module",
            lambda source: _FakeRequestModule,
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
            observed["verifier"] = verifier_binary
            observed["request"] = request_path
            observed["signed"] = signed_transaction_path
            nested = _nested()
            if nested_override:
                nested.update(nested_override)
            return nested

        monkeypatch.setattr(MODULE, "_run_verifier", fake_run_verifier)

        report = MODULE.build_signed_transaction_verification(
            source_tree=ROOT,
            saved_request_path=request_path,
            expected_request_sha256=request["request_sha256"],
            signed_transaction_path=signed_path,
            verifier_binary_path=verifier,
            expected_verifier_binary_sha256=verifier_sha,
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


def test_signed_transaction_verification_is_bound_and_read_only(monkeypatch):
    report, observed = _build(monkeypatch)

    assert report["saved_request_sha256"] == "a" * 64
    assert report["request_final_transaction_sha256"] == "b" * 64
    assert report["signed_transaction_sha256"] == "c" * 64
    assert report["signature"] == "sig-external"
    assert report["signature_verified"] is True
    assert report["exact_signed_exit_transaction_verified"] is True
    assert report["verification_only"] is True
    assert report["transaction_signing_performed"] is False
    assert report["transaction_submission_attempted"] is False
    assert report["automatic_retry_performed"] is False

    env = observed["env"]
    assert MODULE.KEYPAIR_ENV not in env
    assert MODULE.LIVE_SUBMIT_ENV not in env
    assert "SOLANA_RPC_URL" not in env
    assert "RPC_URL" not in env


def test_wrong_wallet_binding_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="executor_wallet_pubkey binding mismatch"):
        _build(
            monkeypatch,
            nested_override={
                "executor_wallet_pubkey":
                    "77777777777777777777777777777777"
            },
        )


def test_wrong_blockhash_binding_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="recent_blockhash binding mismatch"):
        _build(
            monkeypatch,
            nested_override={
                "recent_blockhash":
                    "22222222222222222222222222222222"
            },
        )


def test_failed_signature_verification_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="signature_verified=true"):
        _build(
            monkeypatch,
            nested_override={"signature_verified": False},
        )


def test_resealed_artifact_cannot_claim_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_attempted"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_attempted=false",
    ):
        MODULE.validate_signed_transaction_verification(report)


def test_verification_tool_has_no_signing_or_network_send():
    source = TOOL.read_text(encoding="utf-8")

    assert "load_executor_keypair" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "controlled-live-submit" not in source
    assert '"transaction_signing_performed": False' in source
    assert '"transaction_submission_attempted": False' in source
