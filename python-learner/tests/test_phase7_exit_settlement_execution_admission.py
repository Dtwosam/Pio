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
    / "check_phase7_controlled_live_exit_settlement_execution_admission.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_settlement_execution_admission",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


OPENED_DECISION_ID = "11111111-2222-4333-8444-555555555555"
EXIT_DECISION_ID = "01234567-89ab-4def-8123-456789abcdef"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
WALLET = "44444444444444444444444444444444"


def _false_boundaries() -> dict:
    return {
        "settlement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


def _saved_readiness() -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "exit_signature": "sig-confirmed-exit",
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": "b" * 64,
        "final_settlement_transaction_sha256": "c" * 64,
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "current_block_height": 950,
        "block_height_remaining": 49,
        "rpc_endpoint_sha256": "d" * 64,
        "transaction_authorization_expires_at": "2026-09-29T15:31:10Z",
        "readiness_checked_at": "2026-09-29T15:30:30Z",
        "human_exact_settlement_transaction_authorization_verified": True,
        "exact_settlement_authorization_not_expired": True,
        "final_transaction_unsigned": True,
        "final_exact_simulation_succeeded": True,
        "settlement_execution_readiness_ready": True,
        "requires_keypair_identity_admission": True,
        "requires_immediate_execution_after_readiness": True,
        "requires_fresh_blockhash_recheck_at_execution": True,
        "requires_single_submission_only": True,
        **_false_boundaries(),
    }


def _fresh_readiness(
    *,
    height: int = 951,
    static_override: dict | None = None,
) -> dict:
    value = _saved_readiness()
    value.update(
        {
            "readiness_sha256": "e" * 64,
            "current_block_height": height,
            "block_height_remaining": 999 - height,
            "readiness_checked_at": "2026-09-29T15:30:31Z",
        }
    )
    if static_override:
        value.update(static_override)
    return value


class _FakeReadiness:
    fresh = None

    @staticmethod
    def validate_exit_settlement_execution_readiness(value):
        assert isinstance(value, dict)

    @classmethod
    def build_exit_settlement_execution_readiness(cls, **kwargs):
        assert cls.fresh is not None
        return dict(cls.fresh)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh: dict | None = None,
    wallet: str = WALLET,
    live_submit: bool = True,
    keypair_mode: int = 0o600,
    expected_binary_sha: str | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        saved = _saved_readiness()
        saved_path = _write(root / "readiness.json", saved)

        keypair = root / "executor-keypair.json"
        keypair.write_text("[1,2,3]", encoding="utf-8")
        keypair.chmod(keypair_mode)

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        dummy = root / "dummy.json"
        dummy.write_text("{}", encoding="utf-8")

        monkeypatch.setattr(
            MODULE,
            "_load_readiness_module",
            lambda source: _FakeReadiness,
        )
        _FakeReadiness.fresh = fresh or _fresh_readiness()

        observed = {}

        def fake_wallet_status(*, executor_binary, keypair_path):
            observed["binary"] = executor_binary
            observed["keypair_path"] = keypair_path
            return {
                "pubkey": wallet,
                "keypair_source": MODULE.KEYPAIR_ENV,
                "permissions_verified": True,
            }

        monkeypatch.setattr(MODULE, "_wallet_status", fake_wallet_status)
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, str(keypair))
        if live_submit:
            monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        else:
            monkeypatch.delenv(MODULE.LIVE_SUBMIT_ENV, raising=False)

        report = MODULE.build_exit_settlement_execution_admission(
            source_tree=ROOT,
            saved_readiness_path=saved_path,
            expected_saved_readiness_sha256=saved["readiness_sha256"],
            saved_finalization_path=dummy,
            expected_finalization_sha256="1" * 64,
            saved_authorization_verification_path=dummy,
            expected_saved_authorization_verification_sha256="2" * 64,
            authorization_request_path=dummy,
            authorization_payload_path=dummy,
            authorization_signature_path=dummy,
            authorization_allowed_signers_path=dummy,
            expected_authorization_allowed_signers_sha256="3" * 64,
            rpc_url="https://rpc.example.invalid",
            executor_binary_path=binary,
            expected_executor_binary_sha256=(
                expected_binary_sha or binary_sha
            ),
            now="2026-09-29T15:30:31Z",
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["admission_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_keypair_identity_admission_is_non_signing(monkeypatch):
    report, observed = _build(monkeypatch)

    assert observed["keypair_path"].is_absolute()
    assert report["fresh_current_block_height"] == 951
    assert report["saved_current_block_height"] == 950
    assert report["block_height_monotonic"] is True
    assert report["runtime_live_submit_opt_in_present"] is True
    assert report["keypair_path_absolute"] is True
    assert report["keypair_path_not_symlink"] is True
    assert report["keypair_path_regular_file"] is True
    assert report["keypair_owner_only_permissions"] is True
    assert report["wallet_identity_matches_readiness"] is True
    assert report["executor_keypair_identity_verified"] is True
    assert report["wrapper_read_private_key_bytes"] is False
    assert report["settlement_execution_admission_ready"] is True
    assert report["settlement_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_live_submit_runtime_opt_in_is_required(monkeypatch):
    with pytest.raises(ValueError, match="PIO_LIVE_SUBMIT_ENABLED=1"):
        _build(monkeypatch, live_submit=False)


def test_wallet_mismatch_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="wallet differs"):
        _build(
            monkeypatch,
            wallet="77777777777777777777777777777777",
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_broad_keypair_permissions_fail_closed(monkeypatch):
    with pytest.raises(ValueError, match="permissions are too broad"):
        _build(monkeypatch, keypair_mode=0o644)


def test_fresh_readiness_static_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="static binding differs"):
        _build(
            monkeypatch,
            fresh=_fresh_readiness(
                static_override={"destination_config_sha256": "f" * 64}
            ),
        )


def test_block_height_regression_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="block height regressed"):
        _build(
            monkeypatch,
            fresh=_fresh_readiness(height=949),
        )


def test_executor_binary_trust_root_is_external(monkeypatch):
    with pytest.raises(ValueError, match="external trust root"):
        _build(monkeypatch, expected_binary_sha="f" * 64)


def test_resealed_admission_cannot_authorize_signing(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_exit_settlement_execution_admission(report)


def test_resealed_admission_cannot_authorize_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_settlement_execution_admission(report)


def test_admission_wrapper_never_signs_or_submits():
    source = TOOL.read_text(encoding="utf-8")

    assert 'WALLET_STATUS_COMMAND = "wallet-status"' in source
    assert "env.pop(LIVE_SUBMIT_ENV, None)" in source
    assert "env.pop(\"SOLANA_RPC_URL\", None)" in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"wrapper_read_private_key_bytes": False' in source
    assert '"settlement_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
