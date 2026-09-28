from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_signed_authorization.py"
)
PREFLIGHT_TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_input_preflight.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load(TOOL, "phase7_controlled_live_signed_authorization_test")
PREFLIGHT = _load(PREFLIGHT_TOOL, "phase7_controlled_live_preflight_for_signed_test")

POOL = "11111111111111111111111111111111"
WALLET = "22222222222222222222222222222222"


def _preflight() -> dict:
    config = {
        "enabled": True,
        "allowed_pool_addresses": [POOL],
        "max_open_positions": 1,
        "max_rebalances_per_position": 1,
        "max_capital_quote_per_entry": 5.0,
        "max_daily_entry_capital_quote": 5.0,
        "max_daily_entry_submissions": 1,
        "max_daily_realized_loss_quote": 2.0,
        "max_daily_drawdown_pct": 1.0,
        "allow_rebalance": False,
        "allow_exit": True,
    }
    proposal = {
        "decision_id": "11111111-2222-4333-8444-555555555555",
        "mode": "LIVE",
        "action": "ENTER",
        "pool_address": POOL,
        "capital_quote": 5.0,
        "account_equity_quote": 1000.0,
        "portfolio_deployed_quote": 0.0,
        "daily_drawdown_pct": 0.25,
        "min_bin_id": -10,
        "max_bin_id": 10,
        "strategy": "SPOT",
        "expected_net_return_pct": 1.0,
        "expected_downside_pct": 0.5,
        "model_version": "baseline-v1",
        "data_age_seconds": 5,
    }
    manifest = {
        "controlled_live_config": config,
        "proposal": proposal,
        "executor_wallet_pubkey": WALLET,
    }
    identity = {
        "format_version": PREFLIGHT.FORMAT_VERSION,
        "artifact_type": PREFLIGHT.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                PREFLIGHT.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "phase7_evidence_plan_sha256": "1" * 64,
        "phase7_evidence_status_sha256": "2" * 64,
        "phase6_post_promotion_audit_sha256": "3" * 64,
        "controlled_live_config": config,
        "controlled_live_config_sha256": PREFLIGHT._sha256(config),
        "proposal": proposal,
        "proposal_sha256": PREFLIGHT._sha256(proposal),
        "executor_wallet_pubkey": WALLET,
        "input_manifest_sha256": PREFLIGHT._sha256(manifest),
        "single_pool_scope": True,
        "single_position_scope": True,
        "single_daily_entry_scope": True,
        "rebalance_disabled": True,
        "exit_enabled": True,
        "proposal_capital_equals_entry_cap": True,
        "daily_entry_cap_equals_proposal_capital": True,
        "loss_budget_within_proposal_capital": True,
        "proposal_drawdown_within_config": True,
        "input_preflight_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_phase6_readiness_required": True,
        "rust_controlled_live_check_required": True,
        "rust_risk_gate_required": True,
        "exact_presign_evidence_required": True,
        "proposal_transaction_account_binding_required": True,
        "fresh_blockhash_required": True,
        "final_simulation_required": True,
        "wallet_authorization_required": True,
        "live_submit_feature_required": True,
        "runtime_live_submit_opt_in_required": True,
        "confirmation_receipt_reconciliation_required": True,
        "phase7_promotion_separate": True,
        "controlled_live_authorization_present": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    report = {
        **identity,
        "input_preflight_sha256": PREFLIGHT._sha256(identity),
    }
    PREFLIGHT.validate_phase7_input_preflight(report)
    return report


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _payload(root: Path) -> tuple[dict, Path, Path]:
    preflight = _preflight()
    preflight_path = _write(root, "preflight.json", preflight)
    payload = MODULE.build_payload(
        source_tree=ROOT,
        input_preflight_path=preflight_path,
        approver_principal="wyck@example.com",
        issued_at="2026-09-28T19:00:00Z",
        ttl_seconds=300,
        approval_id="fedcba98-7654-4cba-8123-456789abcdef",
    )
    payload_path = _write(root, "payload.json", payload)
    return payload, payload_path, preflight_path


def _sign(
    root: Path,
    payload: dict,
    *,
    allowed_principal: str = "wyck@example.com",
) -> tuple[Path, Path, str]:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen unavailable")

    key = root / "approval_ed25519"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    public_key = (root / "approval_ed25519.pub").read_text(
        encoding="utf-8"
    ).strip()
    allowed = root / "allowed_signers"
    allowed.write_text(
        f"{allowed_principal} {public_key}\n",
        encoding="utf-8",
    )

    signing = root / "payload.canonical"
    signing.write_bytes(MODULE._canonical_bytes(payload))
    subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            MODULE.SIGNATURE_NAMESPACE,
            str(signing),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    signature = Path(str(signing) + ".sig")
    return signature, allowed, hashlib.sha256(allowed.read_bytes()).hexdigest()


def _reseal_payload(payload: dict) -> None:
    identity = {field: payload[field] for field in MODULE.PAYLOAD_FIELDS}
    payload["payload_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_preflight_tool_is_exactly_pinned():
    assert MODULE._git_blob_sha(ROOT / MODULE.INPUT_PREFLIGHT_TOOL) == (
        MODULE.REVIEWED_SOURCE_BLOBS[MODULE.INPUT_PREFLIGHT_TOOL]
    )


def test_namespace_is_phase7_controlled_live_specific():
    assert MODULE.SIGNATURE_NAMESPACE == (
        "pio-phase7-controlled-live-evidence-authorization-v1"
    )


def test_payload_binds_exact_preflight_without_authorizing_execution():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, preflight_path = _payload(root)
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))

    assert payload["input_preflight_sha256"] == preflight["input_preflight_sha256"]
    assert payload["input_manifest_sha256"] == preflight["input_manifest_sha256"]
    assert payload["executor_wallet_pubkey"] == WALLET
    assert payload["human_authorization_intent"] is True
    assert payload["fresh_phase7_controlled_live_recheck_required"] is True
    assert payload["controlled_live_authorized"] is False
    assert payload["live_submit_authorized"] is False
    assert payload["transaction_signing_authorized"] is False
    assert payload["transaction_submission_authorized"] is False
    assert payload["live_capital_authorized"] is False


def test_emit_signing_bytes_matches_verifier_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, preflight_path = _payload(root)
        emitted = MODULE.emit_signing_bytes(
            source_tree=ROOT,
            input_preflight_path=preflight_path,
            payload_path=payload_path,
        )

    assert emitted == MODULE._canonical_bytes(payload)
    assert not emitted.endswith(b"\n")


def test_resealed_payload_cannot_authorize_live_submit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, preflight_path = _payload(root)
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))

    payload["live_submit_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_payload(payload, preflight=preflight)


def test_resealed_payload_cannot_authorize_signing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, _, preflight_path = _payload(root)
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))

    payload["transaction_signing_authorized"] = True
    _reseal_payload(payload)

    with pytest.raises(ValueError, match="transaction_signing_authorized=false"):
        MODULE.validate_payload(payload, preflight=preflight)


def test_real_ed25519_signature_round_trip_verifies():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, preflight_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        report = MODULE.verify_authorization(
            source_tree=ROOT,
            input_preflight_path=preflight_path,
            payload_path=payload_path,
            signature_path=signature,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256=allowed_sha,
            now="2026-09-28T19:02:00Z",
        )

    assert report["signature_verified"] is True
    assert report["trust_root_digest_matches"] is True
    assert report["human_controlled_live_authorization_verified"] is True
    assert report["fresh_phase7_controlled_live_recheck_required"] is True
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_wrong_trust_root_digest_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, preflight_path = _payload(root)
        signature, allowed, _ = _sign(root, payload)

        with pytest.raises(ValueError, match="trust-root digest mismatch"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                input_preflight_path=preflight_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256="f" * 64,
                now="2026-09-28T19:02:00Z",
            )


def test_untrusted_principal_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, preflight_path = _payload(root)
        signature, allowed, allowed_sha = _sign(
            root,
            payload,
            allowed_principal="somebody-else@example.com",
        )

        with pytest.raises(ValueError, match="signature verification failed"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                input_preflight_path=preflight_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T19:02:00Z",
            )


def test_expired_authorization_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, preflight_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        with pytest.raises(ValueError, match="has expired"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                input_preflight_path=preflight_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T19:06:00Z",
            )


def test_signature_does_not_survive_resealed_payload_tampering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload, payload_path, preflight_path = _payload(root)
        signature, allowed, allowed_sha = _sign(root, payload)

        tampered = copy.deepcopy(payload)
        tampered["approval_id"] = "11111111-2222-4333-8444-555555555555"
        _reseal_payload(tampered)
        payload_path.write_text(json.dumps(tampered), encoding="utf-8")

        with pytest.raises(ValueError, match="signature verification failed"):
            MODULE.verify_authorization(
                source_tree=ROOT,
                input_preflight_path=preflight_path,
                payload_path=payload_path,
                signature_path=signature,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=allowed_sha,
                now="2026-09-28T19:02:00Z",
            )


def test_signer_has_no_production_or_live_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "systemctl" not in source
    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
