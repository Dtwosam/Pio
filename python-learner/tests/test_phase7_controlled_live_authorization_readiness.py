from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_authorization_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_authorization_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


POOL = "11111111111111111111111111111111"
WALLET = "22222222222222222222222222222222"


def _report() -> dict:
    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                MODULE.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "saved_phase7_evidence_status_sha256": "1" * 64,
        "fresh_phase7_evidence_status_sha256": "1" * 64,
        "saved_phase7_evidence_plan_sha256": "2" * 64,
        "fresh_phase7_evidence_plan_sha256": "2" * 64,
        "saved_input_preflight_sha256": "3" * 64,
        "fresh_input_preflight_sha256": "3" * 64,
        "saved_signed_authorization_verification_sha256": "4" * 64,
        "fresh_signed_authorization_verification_sha256": "4" * 64,
        "phase6_post_promotion_audit_sha256": "5" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "controlled_live_config_sha256": "6" * 64,
        "proposal_sha256": "7" * 64,
        "input_manifest_sha256": "8" * 64,
        "executor_wallet_pubkey": WALLET,
        "approval_payload_sha256": "9" * 64,
        "approval_signature_sha256": "a" * 64,
        "allowed_signers_sha256": "b" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
        "fresh_status_matches_saved": True,
        "fresh_plan_matches_saved": True,
        "fresh_preflight_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_controlled_live_authorization_verified": True,
        "approval_not_expired": True,
        "phase6_dependency_current": True,
        "ledger_clean": True,
        "failed_receipts_zero": True,
        "open_positions_zero": True,
        "existing_closed_positions_fully_valued": True,
        "existing_closed_positions_fully_labeled": True,
        "new_entry_evidence_candidate": True,
        "single_pool_scope": True,
        "single_position_scope": True,
        "single_daily_entry_scope": True,
        "rebalance_disabled": True,
        "exit_enabled": True,
        "authorization_readiness_ready": True,
        "requires_rust_controlled_live_check": True,
        "requires_rust_risk_gate": True,
        "requires_exact_presign_evidence": True,
        "requires_proposal_transaction_account_binding": True,
        "requires_fresh_blockhash": True,
        "requires_final_simulation": True,
        "requires_wallet_authorization": True,
        "requires_live_submit_feature": True,
        "requires_runtime_live_submit_opt_in": True,
        "requires_separate_transaction_execution_gate": True,
        "requires_confirmation_receipt_reconciliation": True,
        "requires_separate_phase7_promotion_action": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    return {
        **identity,
        "readiness_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _artifacts(production: Path) -> tuple[dict, dict, dict, dict, dict]:
    phase6 = {
        "post_promotion_audit_sha256": "5" * 64,
        "production_repository": str(production),
    }
    status = {
        "phase7_evidence_status_sha256": "1" * 64,
        "phase6_post_promotion_audit_sha256": "5" * 64,
        "pio_database_path": str(production / "data" / "pio.db"),
        "phase6_promoted": True,
        "ledger_audit": {"clean": True},
        "failed_receipts": 0,
        "open_positions": 0,
        "closed_positions": 0,
        "valued_closed_positions": 0,
        "labeled_closed_positions": 0,
    }
    plan = {
        "plan_sha256": "2" * 64,
        "phase7_evidence_status_sha256": "1" * 64,
        "new_entry_evidence_candidate": True,
    }
    preflight = {
        "input_preflight_sha256": "3" * 64,
        "phase7_evidence_plan_sha256": "2" * 64,
        "controlled_live_config_sha256": "6" * 64,
        "proposal_sha256": "7" * 64,
        "input_manifest_sha256": "8" * 64,
        "executor_wallet_pubkey": WALLET,
    }
    verification = {
        "verification_sha256": "4" * 64,
        "input_preflight_sha256": "3" * 64,
        "approval_payload_sha256": "9" * 64,
        "approval_signature_sha256": "a" * 64,
        "allowed_signers_sha256": "b" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
    }
    return phase6, status, plan, preflight, verification


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_report_still_authorizes_no_transaction_execution():
    report = _report()

    MODULE.validate_phase7_authorization_readiness(copy.deepcopy(report))

    assert report["authorization_readiness_ready"] is True
    assert report["requires_separate_transaction_execution_gate"] is True
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_report_cannot_authorize_signing():
    report = _report()
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="transaction_signing_authorized=false"):
        MODULE.validate_phase7_authorization_readiness(report)


def test_resealed_report_cannot_authorize_submission():
    report = _report()
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_phase7_authorization_readiness(report)


def test_resealed_report_cannot_hide_saved_fresh_status_mismatch():
    report = _report()
    report["fresh_phase7_evidence_status_sha256"] = "f" * 64
    _reseal(report)

    with pytest.raises(ValueError, match="status.*mismatch"):
        MODULE.validate_phase7_authorization_readiness(report)


def test_builder_requires_exact_fresh_reproduction(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        (production / "data" / "pio.db").write_bytes(b"unused")

        phase6, status, plan, preflight, verification = _artifacts(production)

        class FakeStatus:
            @staticmethod
            def validate_phase7_evidence_status(value):
                pass

            @staticmethod
            def build_phase7_evidence_status(**kwargs):
                return copy.deepcopy(status)

        class FakePlan:
            @staticmethod
            def validate_phase7_evidence_plan(value):
                pass

            @staticmethod
            def build_phase7_evidence_plan(**kwargs):
                return copy.deepcopy(plan)

        class FakePreflight:
            @staticmethod
            def validate_phase7_input_preflight(value):
                pass

            @staticmethod
            def build_phase7_input_preflight(**kwargs):
                return copy.deepcopy(preflight)

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                pass

            @staticmethod
            def verify_authorization(**kwargs):
                return copy.deepcopy(verification)

        class FakePhase6:
            @staticmethod
            def validate_post_promotion_audit(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (
                FakeStatus,
                FakePlan,
                FakePreflight,
                FakeSigner,
                FakePhase6,
            ),
        )

        report = MODULE.build_phase7_authorization_readiness(
            repository=production,
            source_tree=ROOT,
            phase6_post_promotion_audit_path=_write(
                root, "phase6.json", phase6
            ),
            saved_phase7_evidence_status_path=_write(
                root, "status.json", status
            ),
            saved_phase7_evidence_plan_path=_write(
                root, "plan.json", plan
            ),
            saved_input_preflight_path=_write(
                root, "preflight.json", preflight
            ),
            controlled_live_config_path=root / "config.json",
            proposal_path=root / "proposal.json",
            executor_wallet_pubkey=WALLET,
            saved_signed_authorization_verification_path=_write(
                root, "verification.json", verification
            ),
            signed_payload_path=root / "payload.json",
            signature_path=root / "signature.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="b" * 64,
            now="2026-09-28T19:02:00Z",
        )

    MODULE.validate_phase7_authorization_readiness(report)
    assert report["fresh_status_matches_saved"] is True
    assert report["fresh_plan_matches_saved"] is True
    assert report["fresh_preflight_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True


def test_builder_rejects_fresh_status_drift(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)

        phase6, status, plan, preflight, verification = _artifacts(production)
        fresh = copy.deepcopy(status)
        fresh["failed_receipts"] = 1

        class FakeStatus:
            @staticmethod
            def validate_phase7_evidence_status(value):
                pass

            @staticmethod
            def build_phase7_evidence_status(**kwargs):
                return copy.deepcopy(fresh)

        class FakePlan:
            @staticmethod
            def validate_phase7_evidence_plan(value):
                pass

        class FakePreflight:
            @staticmethod
            def validate_phase7_input_preflight(value):
                pass

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                pass

        class FakePhase6:
            @staticmethod
            def validate_post_promotion_audit(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (
                FakeStatus,
                FakePlan,
                FakePreflight,
                FakeSigner,
                FakePhase6,
            ),
        )

        with pytest.raises(ValueError, match="differs from saved status"):
            MODULE.build_phase7_authorization_readiness(
                repository=production,
                source_tree=ROOT,
                phase6_post_promotion_audit_path=_write(
                    root, "phase6.json", phase6
                ),
                saved_phase7_evidence_status_path=_write(
                    root, "status.json", status
                ),
                saved_phase7_evidence_plan_path=_write(
                    root, "plan.json", plan
                ),
                saved_input_preflight_path=_write(
                    root, "preflight.json", preflight
                ),
                controlled_live_config_path=root / "config.json",
                proposal_path=root / "proposal.json",
                executor_wallet_pubkey=WALLET,
                saved_signed_authorization_verification_path=_write(
                    root, "verification.json", verification
                ),
                signed_payload_path=root / "payload.json",
                signature_path=root / "signature.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="b" * 64,
            )


def test_builder_rejects_dirty_ledger_even_if_artifacts_match(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)

        phase6, status, plan, preflight, verification = _artifacts(production)
        status["ledger_audit"] = {"clean": False}

        class FakeStatus:
            @staticmethod
            def validate_phase7_evidence_status(value):
                pass

            @staticmethod
            def build_phase7_evidence_status(**kwargs):
                return copy.deepcopy(status)

        class FakePlan:
            @staticmethod
            def validate_phase7_evidence_plan(value):
                pass

            @staticmethod
            def build_phase7_evidence_plan(**kwargs):
                return copy.deepcopy(plan)

        class FakePreflight:
            @staticmethod
            def validate_phase7_input_preflight(value):
                pass

            @staticmethod
            def build_phase7_input_preflight(**kwargs):
                return copy.deepcopy(preflight)

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                pass

            @staticmethod
            def verify_authorization(**kwargs):
                return copy.deepcopy(verification)

        class FakePhase6:
            @staticmethod
            def validate_post_promotion_audit(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (
                FakeStatus,
                FakePlan,
                FakePreflight,
                FakeSigner,
                FakePhase6,
            ),
        )

        with pytest.raises(ValueError, match="clean live ledger"):
            MODULE.build_phase7_authorization_readiness(
                repository=production,
                source_tree=ROOT,
                phase6_post_promotion_audit_path=_write(
                    root, "phase6.json", phase6
                ),
                saved_phase7_evidence_status_path=_write(
                    root, "status.json", status
                ),
                saved_phase7_evidence_plan_path=_write(
                    root, "plan.json", plan
                ),
                saved_input_preflight_path=_write(
                    root, "preflight.json", preflight
                ),
                controlled_live_config_path=root / "config.json",
                proposal_path=root / "proposal.json",
                executor_wallet_pubkey=WALLET,
                saved_signed_authorization_verification_path=_write(
                    root, "verification.json", verification
                ),
                signed_payload_path=root / "payload.json",
                signature_path=root / "signature.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="b" * 64,
            )


def test_readiness_tool_has_no_transaction_executor():
    source = TOOL.read_text(encoding="utf-8")

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
