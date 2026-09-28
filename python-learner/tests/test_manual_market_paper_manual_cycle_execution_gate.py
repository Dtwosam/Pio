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
    / "check_manual_market_paper_manual_cycle_execution_gate.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_manual_cycle_execution_gate",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _parameters() -> dict:
    return {
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "capital_per_position_quote": "100.00",
        "network_cost_quote": "0.10",
        "max_new_positions": 1,
        "max_pools_considered": 10,
        "minimum_chain_observations": 12,
        "intake_max_pools": 500,
        "seed_batch_limit": 25,
        "refresh_batch_limit": 10,
        "discovery_page_size": 1000,
        "discovery_max_pages": 100,
        "discovery_sort_by": "tvl:desc",
        "bin_array_radius": 0,
        "timeout_seconds": 120,
        "quote_max_age_seconds": 300,
        "max_share_bps": 500,
        "scheduler_interval_seconds": 300,
        "scheduler_lease_seconds": 900,
        "scheduler_max_positions": 1,
        "observed_at": None,
    }


def _report() -> dict:
    parameters = _parameters()
    parameter_sha = hashlib.sha256(
        MODULE._canonical_bytes(parameters)
    ).hexdigest()
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
        "saved_manual_cycle_readiness_sha256": "1" * 64,
        "fresh_manual_cycle_readiness_sha256": "1" * 64,
        "authorization_request_sha256": "2" * 64,
        "saved_signed_authorization_verification_sha256": "3" * 64,
        "fresh_signed_authorization_verification_sha256": "3" * 64,
        "production_repository": "/opt/pio",
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "approval_expires_at": "2026-09-28T12:05:00Z",
        "authorization_scope": MODULE.AUTHORIZATION_SCOPE,
        "account": parameters["account"],
        "run_id": parameters["run_id"],
        "cycle_parameters": parameters,
        "cycle_parameters_sha256": parameter_sha,
        "fresh_readiness_matches_saved": True,
        "request_binds_readiness": True,
        "fresh_signed_authorization_matches_saved": True,
        "signed_authorization_binds_request": True,
        "human_cycle_authorization_verified": True,
        "approval_not_expired": True,
        "one_cycle_only": True,
        "paper_only": True,
        "manual_cycle_execution_gate_ready": True,
        "requires_immediate_one_shot_executor_recheck": True,
        "manual_cycle_execution_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    return {
        **identity,
        "execution_gate_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["execution_gate_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _saved_artifacts(production: Path):
    params = _parameters()
    parameter_sha = hashlib.sha256(
        MODULE._canonical_bytes(params)
    ).hexdigest()
    readiness = {
        "manual_cycle_readiness_ready": True,
        "manual_cycle_readiness_sha256": "1" * 64,
        "production_repository": str(production),
    }
    request = {
        "authorization_request_ready": True,
        "manual_cycle_readiness_sha256": readiness[
            "manual_cycle_readiness_sha256"
        ],
        "production_repository": str(production),
        "request_sha256": "2" * 64,
        "cycle_parameters_sha256": parameter_sha,
        "cycle_parameters": params,
    }
    signed = {
        "human_cycle_authorization_verified": True,
        "request_sha256": request["request_sha256"],
        "manual_cycle_readiness_sha256": readiness[
            "manual_cycle_readiness_sha256"
        ],
        "cycle_parameters_sha256": parameter_sha,
        "authorization_scope": MODULE.AUTHORIZATION_SCOPE,
        "manual_cycle_execution_authorized": False,
        "verification_sha256": "3" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "expires_at": "2026-09-28T12:05:00Z",
    }
    return readiness, request, signed


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build_with_fakes(
    monkeypatch,
    *,
    mutate_readiness=None,
    mutate_request=None,
    mutate_signed=None,
    mutate_fresh_readiness=None,
    mutate_fresh_signed=None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    production.mkdir()
    readiness, request, signed = _saved_artifacts(production)
    if mutate_readiness:
        mutate_readiness(readiness)
    if mutate_request:
        mutate_request(request)
    if mutate_signed:
        mutate_signed(signed)

    fresh_readiness = copy.deepcopy(readiness)
    fresh_signed = copy.deepcopy(signed)
    if mutate_fresh_readiness:
        mutate_fresh_readiness(fresh_readiness)
    if mutate_fresh_signed:
        mutate_fresh_signed(fresh_signed)

    class FakeReadiness:
        @staticmethod
        def validate_manual_cycle_readiness(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_manual_cycle_readiness(**kwargs):
            return copy.deepcopy(fresh_readiness)

    class FakeRequest:
        @staticmethod
        def validate_manual_cycle_authorization_request(value):
            assert isinstance(value, dict)

    class FakeSigned:
        @staticmethod
        def validate_verification(value):
            assert isinstance(value, dict)

        @staticmethod
        def verify_authorization(**kwargs):
            return copy.deepcopy(fresh_signed)

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_modules",
        lambda source: (FakeReadiness, FakeRequest, FakeSigned),
    )

    readiness_path = _write(root, "readiness.json", readiness)
    request_path = _write(root, "request.json", request)
    signed_path = _write(root, "signed.json", signed)

    def run():
        return MODULE.build_execution_gate(
            repository=production,
            source_tree=ROOT,
            private_bundle_report_path=root / "bundle.json",
            pre_mutation_handoff_path=root / "handoff.json",
            execution_precheck_path=root / "precheck.json",
            mutation_receipt_path=root / "receipt.json",
            post_mutation_audit_path=root / "audit.json",
            manual_cycle_readiness_path=readiness_path,
            authorization_request_path=request_path,
            signed_authorization_verification_path=signed_path,
            signed_payload_path=root / "payload.json",
            signature_path=root / "payload.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="8" * 64,
        )

    return temp, run


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_gate_still_does_not_execute():
    report = _report()
    MODULE.validate_execution_gate(copy.deepcopy(report))

    assert report["manual_cycle_execution_gate_ready"] is True
    assert report["human_cycle_authorization_verified"] is True
    assert report["requires_immediate_one_shot_executor_recheck"] is True
    assert report["manual_cycle_execution_authorized"] is False
    assert report["paper_timer_enable_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_gate_cannot_authorize_execution():
    report = _report()
    report["manual_cycle_execution_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="manual_cycle_execution_authorized=false"):
        MODULE.validate_execution_gate(report)


def test_resealed_gate_cannot_enable_timer():
    report = _report()
    report["paper_timer_enable_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_execution_gate(report)


def test_builder_rechecks_readiness_and_signed_authorization(monkeypatch):
    temp, run = _build_with_fakes(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    MODULE.validate_execution_gate(report)
    assert report["fresh_readiness_matches_saved"] is True
    assert report["fresh_signed_authorization_matches_saved"] is True
    assert report["manual_cycle_execution_gate_ready"] is True
    assert report["manual_cycle_execution_authorized"] is False


def test_builder_fails_on_fresh_readiness_drift(monkeypatch):
    temp, run = _build_with_fakes(
        monkeypatch,
        mutate_fresh_readiness=lambda value: value.update(
            manual_cycle_readiness_sha256="9" * 64
        ),
    )
    try:
        with pytest.raises(ValueError, match="fresh readiness differs"):
            run()
    finally:
        temp.cleanup()


def test_builder_fails_on_fresh_signed_verification_drift(monkeypatch):
    temp, run = _build_with_fakes(
        monkeypatch,
        mutate_fresh_signed=lambda value: value.update(
            verification_sha256="9" * 64
        ),
    )
    try:
        with pytest.raises(ValueError, match="fresh signed authorization differs"):
            run()
    finally:
        temp.cleanup()


def test_builder_fails_on_signed_request_binding_drift(monkeypatch):
    temp, run = _build_with_fakes(
        monkeypatch,
        mutate_signed=lambda value: value.update(request_sha256="9" * 64),
    )
    try:
        with pytest.raises(ValueError, match="signed request binding mismatch"):
            run()
    finally:
        temp.cleanup()


def test_builder_rejects_symlink_production_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        production.mkdir()
        link = root / "production-link"
        link.symlink_to(production, target_is_directory=True)

        with pytest.raises(ValueError, match="must not be a symlink"):
            MODULE.build_execution_gate(
                repository=link,
                source_tree=ROOT,
                private_bundle_report_path=root / "bundle.json",
                pre_mutation_handoff_path=root / "handoff.json",
                execution_precheck_path=root / "precheck.json",
                mutation_receipt_path=root / "receipt.json",
                post_mutation_audit_path=root / "audit.json",
                manual_cycle_readiness_path=root / "readiness.json",
                authorization_request_path=root / "request.json",
                signed_authorization_verification_path=root / "signed.json",
                signed_payload_path=root / "payload.json",
                signature_path=root / "payload.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="8" * 64,
            )


def test_execution_gate_has_no_cycle_or_activation_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert "manual_market_paper_cycle_cli" not in source
    assert '"manual_cycle_execution_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
