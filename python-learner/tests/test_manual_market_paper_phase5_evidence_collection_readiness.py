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
    / "check_manual_market_paper_phase5_evidence_collection_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_phase5_evidence_collection_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _plan() -> dict:
    return {
        "collection_plan_sha256": "1" * 64,
        "phase5_evidence_status_sha256": "2" * 64,
    }


def _request() -> dict:
    return {
        "collection_plan_sha256": "1" * 64,
        "request_sha256": "3" * 64,
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "requested_collection_seconds": 3600,
        "scheduler_extra_args_text": "--max-positions 3 --refresh-jupiter-quotes",
    }


def _verification() -> dict:
    return {
        "request_sha256": "3" * 64,
        "verification_sha256": "4" * 64,
    }


def _status() -> dict:
    return {
        "phase5_evidence_status_sha256": "2" * 64,
    }


def _unit(
    *,
    unit: str,
    git_blob: str,
    active_state: str = "inactive",
    unit_file_state: str = "disabled",
    drop_in_paths: str = "",
) -> dict:
    return {
        "unit": unit,
        "load_state": "loaded",
        "active_state": active_state,
        "unit_file_state": unit_file_state,
        "fragment_path": f"/etc/systemd/system/{unit}",
        "fragment_sha256": "5" * 64,
        "fragment_git_blob": git_blob,
        "drop_in_paths": drop_in_paths,
        "fragment_matches_reviewed": True,
        "no_drop_ins": drop_in_paths == "",
    }


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    fresh_status=None,
    fresh_verification=None,
    service=None,
    timer=None,
    env_value="--max-positions 3 --refresh-jupiter-quotes",
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    production.mkdir()

    plan = _plan()
    request = _request()
    saved_verification = _verification()

    class FakeStatusModule:
        @staticmethod
        def build_phase5_evidence_status(**kwargs):
            return copy.deepcopy(fresh_status or _status())

        @staticmethod
        def validate_phase5_evidence_status(value):
            assert isinstance(value, dict)

    class FakePlanModule:
        @staticmethod
        def validate_phase5_evidence_collection_plan(value):
            assert isinstance(value, dict)

    class FakeRequestModule:
        @staticmethod
        def validate_phase5_evidence_collection_request(value):
            assert isinstance(value, dict)

    class FakeSignedModule:
        @staticmethod
        def validate_verification(value):
            assert isinstance(value, dict)

        @staticmethod
        def verify_authorization(**kwargs):
            return copy.deepcopy(fresh_verification or saved_verification)

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_modules",
        lambda source: (
            FakeStatusModule,
            FakePlanModule,
            FakeRequestModule,
            FakeSignedModule,
        ),
    )

    service_value = service or _unit(
        unit="pio-paper@pio-proof-1.service",
        git_blob=MODULE.REVIEWED_SOURCE_BLOBS[MODULE.PAPER_SERVICE_UNIT],
        unit_file_state="static",
    )
    timer_value = timer or _unit(
        unit="pio-paper@pio-proof-1.timer",
        git_blob=MODULE.REVIEWED_SOURCE_BLOBS[MODULE.PAPER_TIMER_UNIT],
    )

    def fake_unit_snapshot(*, source, unit, reviewed_relative):
        if reviewed_relative == MODULE.PAPER_SERVICE_UNIT:
            return copy.deepcopy(service_value)
        return copy.deepcopy(timer_value)

    monkeypatch.setattr(MODULE, "_unit_snapshot", fake_unit_snapshot)

    env_file = root / "pio.env"
    env_file.write_text(
        f'PIO_PAPER_SCHEDULER_EXTRA_ARGS="{env_value}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "ENVIRONMENT_FILE", env_file)

    plan_path = _write(root, "plan.json", plan)
    request_path = _write(root, "request.json", request)
    verification_path = _write(root, "verification.json", saved_verification)

    def run():
        return MODULE.build_collection_readiness(
            repository=production,
            source_tree=ROOT,
            post_cycle_audit_path=root / "post-cycle.json",
            collection_plan_path=plan_path,
            collection_request_path=request_path,
            saved_signed_verification_path=verification_path,
            signed_payload_path=root / "payload.json",
            signature_path=root / "payload.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="6" * 64,
            environment_file=env_file,
            now="2026-09-28T14:00:00Z",
        )

    return temp, run


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["collection_readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_canonical_environment_assignment_is_read_without_sourcing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pio.env"
        path.write_text(
            '# comment\nPIO_OTHER=x\n'
            'PIO_PAPER_SCHEDULER_EXTRA_ARGS="--max-positions 3 '
            '--refresh-jupiter-quotes"\n',
            encoding="utf-8",
        )
        value, digest = MODULE._parse_environment_scheduler_args(path)

    assert value == "--max-positions 3 --refresh-jupiter-quotes"
    assert len(digest) == 64


def test_duplicate_scheduler_environment_assignment_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pio.env"
        path.write_text(
            "PIO_PAPER_SCHEDULER_EXTRA_ARGS=a\n"
            "PIO_PAPER_SCHEDULER_EXTRA_ARGS=b\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="exactly one"):
            MODULE._parse_environment_scheduler_args(path)


def test_ready_gate_rechecks_status_signature_units_and_environment(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    MODULE.validate_collection_readiness(report)
    assert report["fresh_phase5_status_matches_plan"] is True
    assert report["fresh_signed_authorization_matches_saved"] is True
    assert report["environment_args_match_request"] is True
    assert report["service_unit_matches_reviewed"] is True
    assert report["timer_unit_matches_reviewed"] is True
    assert report["timer_disabled"] is True
    assert report["collection_readiness_ready"] is True
    assert report["requires_bounded_activation_executor"] is True
    assert report["collection_execution_authorized"] is False
    assert report["paper_timer_enable_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_status_drift_fails_closed(monkeypatch):
    fresh = _status()
    fresh["phase5_evidence_status_sha256"] = "9" * 64
    temp, run = _build(monkeypatch, fresh_status=fresh)
    try:
        with pytest.raises(ValueError, match="differs from the signed collection plan"):
            run()
    finally:
        temp.cleanup()


def test_signature_verification_drift_fails_closed(monkeypatch):
    fresh = _verification()
    fresh["verification_sha256"] = "9" * 64
    temp, run = _build(monkeypatch, fresh_verification=fresh)
    try:
        with pytest.raises(ValueError, match="differs from saved"):
            run()
    finally:
        temp.cleanup()


def test_environment_args_drift_fails_closed(monkeypatch):
    temp, run = _build(
        monkeypatch,
        env_value="--max-positions 99 --refresh-jupiter-quotes",
    )
    try:
        with pytest.raises(ValueError, match="extra args differ"):
            run()
    finally:
        temp.cleanup()


def test_active_timer_fails_closed(monkeypatch):
    timer = _unit(
        unit="pio-paper@pio-proof-1.timer",
        git_blob=MODULE.REVIEWED_SOURCE_BLOBS[MODULE.PAPER_TIMER_UNIT],
        active_state="active",
        unit_file_state="enabled",
    )
    temp, run = _build(monkeypatch, timer=timer)
    try:
        with pytest.raises(ValueError, match="readiness failed closed"):
            run()
    finally:
        temp.cleanup()


def test_systemd_drop_in_fails_closed(monkeypatch):
    service = _unit(
        unit="pio-paper@pio-proof-1.service",
        git_blob=MODULE.REVIEWED_SOURCE_BLOBS[MODULE.PAPER_SERVICE_UNIT],
        unit_file_state="static",
        drop_in_paths="/etc/systemd/system/pio-paper@.service.d/override.conf",
    )
    temp, run = _build(monkeypatch, service=service)
    try:
        with pytest.raises(ValueError, match="readiness failed closed"):
            run()
    finally:
        temp.cleanup()


def test_resealed_gate_cannot_enable_timer(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_timer_enable_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_collection_readiness(report)


def test_resealed_gate_cannot_authorize_collection_execution(monkeypatch):
    temp, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["collection_execution_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="collection_execution_authorized=false"):
        MODULE.validate_collection_readiness(report)


def test_collection_readiness_uses_only_read_only_systemctl_show():
    source = TOOL.read_text(encoding="utf-8")

    assert '"show",' in source
    assert '"enable"' not in source
    assert '"disable"' not in source
    assert '"start"' not in source
    assert '"stop"' not in source
    assert '"restart"' not in source
    assert '"daemon-reload"' not in source
    assert "shell=True" not in source
    assert "source /etc/pio/pio.env" not in source
    assert '"collection_execution_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
