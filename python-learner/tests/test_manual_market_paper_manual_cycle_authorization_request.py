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
    / "build_manual_market_paper_manual_cycle_authorization_request.py"
)
READINESS_TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_manual_market_paper_manual_cycle_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_manual_cycle_authorization_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

READINESS_SPEC = importlib.util.spec_from_file_location(
    "manual_market_paper_cycle_authorization_request_readiness_test",
    READINESS_TOOL,
)
assert READINESS_SPEC is not None and READINESS_SPEC.loader is not None
READINESS = importlib.util.module_from_spec(READINESS_SPEC)
sys.modules[READINESS_SPEC.name] = READINESS
READINESS_SPEC.loader.exec_module(READINESS)


def _readiness() -> dict:
    identity = {
        "format_version": READINESS.FORMAT_VERSION,
        "artifact_type": READINESS.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                READINESS.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "saved_post_mutation_audit_sha256": "1" * 64,
        "fresh_post_mutation_audit_sha256": "1" * 64,
        "production_repository": "/opt/pio",
        "mutation_receipt_sha256": "2" * 64,
        "execution_precheck_sha256": "3" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "01234567-89ab-4def-8123-456789abcdef",
        "runtime_manifest_git_blob": READINESS.REVIEWED_SOURCE_BLOBS[
            READINESS.RUNTIME_MANIFEST
        ],
        "manual_cycle_cli_git_blob": READINESS.REVIEWED_SOURCE_BLOBS[
            READINESS.MANUAL_CYCLE_CLI
        ],
        "fresh_post_mutation_audit_matches_saved": True,
        "post_mutation_audit_ready": True,
        "runtime_files_deployed": True,
        "manual_paper_runtime_ready": True,
        "manual_mode_safe": True,
        "operational_services_healthy": True,
        "activation_state_unchanged": True,
        "activation_observed": False,
        "runtime_manifest_manual_only": True,
        "manual_cycle_cli_reviewed": True,
        "manual_cycle_readiness_ready": True,
        "requires_separate_manual_cycle_authorization": True,
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
    report = {
        **identity,
        "manual_cycle_readiness_sha256": hashlib.sha256(
            READINESS._canonical_bytes(identity)
        ).hexdigest(),
    }
    READINESS.validate_manual_cycle_readiness(report)
    return report


def _write_readiness(root: Path, readiness: dict) -> Path:
    path = root / "manual-cycle-readiness.json"
    path.write_text(json.dumps(readiness), encoding="utf-8")
    return path


def _build(root: Path, **overrides):
    readiness = _readiness()
    readiness_path = _write_readiness(root, readiness)
    kwargs = {
        "source_tree": ROOT,
        "readiness_path": readiness_path,
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "capital_per_position_quote": "100.00",
        "network_cost_quote": "0.10",
        "max_pools_considered": 10,
        "minimum_chain_observations": 12,
        "intake_max_pools": 500,
        "seed_batch_limit": 25,
        "refresh_batch_limit": 10,
        "discovery_page_size": 1000,
        "discovery_max_pages": 100,
        "timeout_seconds": 120,
        "quote_max_age_seconds": 300,
        "max_share_bps": 500,
    }
    kwargs.update(overrides)
    return (
        MODULE.build_manual_cycle_authorization_request(**kwargs),
        readiness,
    )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_request_binds_readiness_and_all_cycle_parameters_without_authorizing():
    with tempfile.TemporaryDirectory() as tmp:
        request, readiness = _build(Path(tmp))

    assert (
        request["manual_cycle_readiness_sha256"]
        == readiness["manual_cycle_readiness_sha256"]
    )
    assert (
        request["post_mutation_audit_sha256"]
        == readiness["fresh_post_mutation_audit_sha256"]
    )
    assert request["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert request["authorization_request_ready"] is True
    assert request["one_cycle_only"] is True
    assert request["paper_only"] is True
    assert request["manual_cycle_authorization_present"] is False
    assert request["manual_cycle_execution_authorized"] is False
    assert request["paper_timer_enable_authorized"] is False
    assert request["transaction_signing_authorized"] is False
    assert request["transaction_submission_authorized"] is False
    assert request["live_capital_authorized"] is False

    parameters = request["cycle_parameters"]
    assert parameters["max_new_positions"] == 1
    assert parameters["max_pools_considered"] == 10
    assert parameters["scheduler_interval_seconds"] == 300
    assert parameters["scheduler_lease_seconds"] == 900
    assert parameters["scheduler_max_positions"] == 1
    assert parameters["observed_at"] is None


def test_resealed_request_cannot_authorize_manual_cycle():
    with tempfile.TemporaryDirectory() as tmp:
        request, _ = _build(Path(tmp))

    request["manual_cycle_execution_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="manual_cycle_execution_authorized=false"):
        MODULE.validate_manual_cycle_authorization_request(request)


def test_resealed_request_cannot_enable_recurring_timer():
    with tempfile.TemporaryDirectory() as tmp:
        request, _ = _build(Path(tmp))

    request["paper_timer_enable_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_manual_cycle_authorization_request(request)


def test_resealed_request_cannot_expand_first_proof_to_multiple_new_positions():
    with tempfile.TemporaryDirectory() as tmp:
        request, _ = _build(Path(tmp))

    request["cycle_parameters"]["max_new_positions"] = 2
    request["cycle_parameters_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(request["cycle_parameters"])
    ).hexdigest()
    _reseal(request)

    with pytest.raises(ValueError, match="max_new_positions at 1"):
        MODULE.validate_manual_cycle_authorization_request(request)


def test_resealed_request_cannot_remove_scheduler_position_cap():
    with tempfile.TemporaryDirectory() as tmp:
        request, _ = _build(Path(tmp))

    request["cycle_parameters"]["scheduler_max_positions"] = None
    request["cycle_parameters_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(request["cycle_parameters"])
    ).hexdigest()
    _reseal(request)

    with pytest.raises(ValueError, match="scheduler_max_positions=1"):
        MODULE.validate_manual_cycle_authorization_request(request)


@pytest.mark.parametrize(
    "value",
    ["01", "1e3", "+1", "-1", "NaN", "Infinity", "1."],
)
def test_capital_decimal_text_must_be_canonical(value):
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="canonical decimal text"):
            _build(
                Path(tmp),
                capital_per_position_quote=value,
            )


def test_network_cost_may_be_zero_but_not_negative():
    with tempfile.TemporaryDirectory() as tmp:
        request, _ = _build(
            Path(tmp),
            network_cost_quote="0",
        )
    assert request["cycle_parameters"]["network_cost_quote"] == "0"

    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="canonical decimal text"):
            _build(
                Path(tmp),
                network_cost_quote="-0.1",
            )


def test_first_proof_pool_bound_cannot_exceed_ten():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="max_pools_considered"):
            _build(Path(tmp), max_pools_considered=11)


def test_resealed_request_cannot_inject_observed_at():
    with tempfile.TemporaryDirectory() as tmp:
        request, _ = _build(Path(tmp))

    request["cycle_parameters"]["observed_at"] = "2026-09-28T12:00:00Z"
    request["cycle_parameters_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(request["cycle_parameters"])
    ).hexdigest()
    _reseal(request)

    with pytest.raises(ValueError, match="fresh runtime time"):
        MODULE.validate_manual_cycle_authorization_request(request)


def test_builder_rejects_readiness_that_claims_execution_authorized():
    readiness = _readiness()
    readiness["manual_cycle_execution_authorized"] = True
    identity = {
        field: readiness[field]
        for field in READINESS.REPORT_FIELDS
    }
    readiness["manual_cycle_readiness_sha256"] = hashlib.sha256(
        READINESS._canonical_bytes(identity)
    ).hexdigest()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _write_readiness(root, readiness)
        with pytest.raises(ValueError):
            MODULE.build_manual_cycle_authorization_request(
                source_tree=ROOT,
                readiness_path=path,
                account="pio-proof-1",
                run_id="manual-proof-20260928-1",
                capital_per_position_quote="100",
                network_cost_quote="0.1",
            )


def test_authorization_request_tool_has_no_execution_or_production_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"manual_cycle_authorization_present": False' in source
    assert '"manual_cycle_execution_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
