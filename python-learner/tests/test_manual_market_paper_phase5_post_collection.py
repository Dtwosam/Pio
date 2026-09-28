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
    / "check_manual_market_paper_phase5_post_collection.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_phase5_post_collection",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _status(
    *,
    digest: str,
    runtime_hours: float,
    terminal_ticks: int,
    valuations: int,
    positions_valued: int,
    closed_positions: int,
    pools: int,
    ready: bool = False,
) -> dict:
    return {
        "phase5_evidence_status_sha256": digest,
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "endurance": {
            "runtime_hours": runtime_hours,
            "terminal_ticks": terminal_ticks,
            "applied_chain_valuations": valuations,
            "distinct_positions_valued": positions_valued,
        },
        "closed_positions": closed_positions,
        "distinct_valued_pools": pools,
        "phase5_promotion_ready": ready,
        "phase5_reasons": [] if ready else ["more PAPER evidence required"],
        "requires_additional_paper_evidence": not ready,
    }


def _pre_status() -> dict:
    return _status(
        digest="2" * 64,
        runtime_hours=12.0,
        terminal_ticks=100,
        valuations=20,
        positions_valued=1,
        closed_positions=1,
        pools=1,
        ready=False,
    )


def _fresh_status(*, ready: bool = False, advanced: bool = True) -> dict:
    return _status(
        digest="3" * 64,
        runtime_hours=13.0 if advanced else 12.0,
        terminal_ticks=112 if advanced else 100,
        valuations=25 if advanced else 20,
        positions_valued=1,
        closed_positions=1,
        pools=1,
        ready=ready,
    )


def _receipt(production: Path) -> dict:
    return {
        "receipt_sha256": "1" * 64,
        "collection_readiness_sha256": "4" * 64,
        "phase5_evidence_status_sha256": "2" * 64,
        "production_repository": str(production),
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "target_timer_unit": "pio-paper@pio-proof-1.timer",
        "target_service_unit": "pio-paper@pio-proof-1.service",
        "requested_collection_seconds": 3600,
        "started_at": "2026-09-28T12:00:00Z",
        "collection_deadline": "2026-09-28T13:00:00Z",
        "bounded_collection_started": True,
        "requires_post_collection_audit": True,
    }


def _unit(
    *,
    unit: str,
    active_state: str,
    unit_file_state: str,
    fragment_matches: bool = True,
    no_drop_ins: bool = True,
) -> dict:
    return {
        "unit": unit,
        "load_state": "loaded",
        "active_state": active_state,
        "unit_file_state": unit_file_state,
        "fragment_path": f"/etc/systemd/system/{unit}",
        "fragment_sha256": "5" * 64,
        "fragment_git_blob": "6" * 40,
        "drop_in_paths": "" if no_drop_ins else "/etc/systemd/system/drop.conf",
        "fragment_matches_reviewed": fragment_matches,
        "no_drop_ins": no_drop_ins,
    }


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _run_with_fakes(
    monkeypatch,
    *,
    fresh: dict | None = None,
    service_active: str = "inactive",
    timer_active: str = "inactive",
    timer_unit_file_state: str = "disabled",
    fragment_matches: bool = True,
    no_drop_ins: bool = True,
    now: str = "2026-09-28T13:00:01Z",
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    production.mkdir()
    pre = _pre_status()
    receipt = _receipt(production)
    fresh_status = copy.deepcopy(
        fresh if fresh is not None else _fresh_status()
    )

    class FakeStarter:
        @staticmethod
        def validate_activation_receipt(value):
            assert isinstance(value, dict)

    class FakeReadiness:
        PAPER_SERVICE_UNIT = Path("deploy/systemd/pio-paper@.service")
        PAPER_TIMER_UNIT = Path("deploy/systemd/pio-paper@.timer")

        @staticmethod
        def _unit_snapshot(*, source, unit, reviewed_relative):
            if unit.endswith(".service"):
                return _unit(
                    unit=unit,
                    active_state=service_active,
                    unit_file_state="static",
                    fragment_matches=fragment_matches,
                    no_drop_ins=no_drop_ins,
                )
            return _unit(
                unit=unit,
                active_state=timer_active,
                unit_file_state=timer_unit_file_state,
                fragment_matches=fragment_matches,
                no_drop_ins=no_drop_ins,
            )

        @staticmethod
        def _validate_unit_snapshot(value):
            assert isinstance(value, dict)

    class FakeStatus:
        @staticmethod
        def validate_phase5_evidence_status(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_phase5_evidence_status(**kwargs):
            return copy.deepcopy(fresh_status)

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_modules",
        lambda source: (FakeStarter, FakeReadiness, FakeStatus),
    )

    receipt_path = _write(root, "activation.json", receipt)
    pre_path = _write(root, "pre-status.json", pre)

    def execute():
        return MODULE.build_post_collection_audit(
            repository=production,
            source_tree=ROOT,
            post_cycle_audit_path=root / "post-cycle.json",
            pre_collection_phase5_evidence_status_path=pre_path,
            activation_receipt_path=receipt_path,
            now=now,
        )

    return temp, execute


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["post_collection_audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_completed_window_rebuilds_phase5_status_and_stays_non_authorizing(
    monkeypatch,
):
    temp, execute = _run_with_fakes(monkeypatch)
    try:
        report = execute()
    finally:
        temp.cleanup()

    MODULE.validate_post_collection_audit(report)
    assert report["deadline_reached"] is True
    assert report["service_inactive"] is True
    assert report["timer_inactive"] is True
    assert report["timer_disabled"] is True
    assert report["bounded_window_ended"] is True
    assert report["evidence_advanced"] is True
    assert report["phase5_promotion_ready"] is False
    assert report["requires_additional_paper_evidence"] is True
    assert report["requires_new_collection_plan"] is True
    assert report["phase5_promotion_persisted"] is False
    assert report["paper_timer_enable_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_ready_phase5_evidence_still_requires_separate_promotion(monkeypatch):
    temp, execute = _run_with_fakes(
        monkeypatch,
        fresh=_fresh_status(ready=True),
    )
    try:
        report = execute()
    finally:
        temp.cleanup()

    MODULE.validate_post_collection_audit(report)
    assert report["phase5_promotion_ready"] is True
    assert report["requires_additional_paper_evidence"] is False
    assert report["requires_new_collection_plan"] is False
    assert report["requires_separate_phase5_promotion_action"] is True
    assert report["phase5_promotion_persisted"] is False
    assert report["phase5_promotion_authorized"] is False


def test_no_progress_is_reported_without_faking_failure(monkeypatch):
    temp, execute = _run_with_fakes(
        monkeypatch,
        fresh=_fresh_status(advanced=False),
    )
    try:
        report = execute()
    finally:
        temp.cleanup()

    MODULE.validate_post_collection_audit(report)
    assert report["evidence_advanced"] is False
    assert report["post_collection_audit_ready"] is True
    assert report["requires_new_collection_plan"] is True


def test_audit_cannot_run_before_collection_deadline(monkeypatch):
    temp, execute = _run_with_fakes(
        monkeypatch,
        now="2026-09-28T12:59:59Z",
    )
    try:
        with pytest.raises(ValueError, match="before collection deadline"):
            execute()
    finally:
        temp.cleanup()


def test_audit_fails_if_service_is_still_active(monkeypatch):
    temp, execute = _run_with_fakes(
        monkeypatch,
        service_active="active",
    )
    try:
        with pytest.raises(ValueError, match="service is still active"):
            execute()
    finally:
        temp.cleanup()


def test_audit_fails_if_timer_is_still_active(monkeypatch):
    temp, execute = _run_with_fakes(
        monkeypatch,
        timer_active="active",
    )
    try:
        with pytest.raises(ValueError, match="timer is still active"):
            execute()
    finally:
        temp.cleanup()


def test_audit_fails_if_timer_became_persistent(monkeypatch):
    temp, execute = _run_with_fakes(
        monkeypatch,
        timer_unit_file_state="enabled",
    )
    try:
        with pytest.raises(ValueError, match="timer became persistent"):
            execute()
    finally:
        temp.cleanup()


def test_audit_fails_on_unit_byte_drift(monkeypatch):
    temp, execute = _run_with_fakes(
        monkeypatch,
        fragment_matches=False,
    )
    try:
        with pytest.raises(ValueError, match="unit bytes drifted"):
            execute()
    finally:
        temp.cleanup()


def test_audit_fails_on_unit_drop_ins(monkeypatch):
    temp, execute = _run_with_fakes(
        monkeypatch,
        no_drop_ins=False,
    )
    try:
        with pytest.raises(ValueError, match="drop-ins detected"):
            execute()
    finally:
        temp.cleanup()


def test_resealed_report_cannot_persist_phase5_promotion(monkeypatch):
    temp, execute = _run_with_fakes(
        monkeypatch,
        fresh=_fresh_status(ready=True),
    )
    try:
        report = execute()
    finally:
        temp.cleanup()

    report["phase5_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase5_promotion_persisted=false"):
        MODULE.validate_post_collection_audit(report)


def test_resealed_report_cannot_enable_timer(monkeypatch):
    temp, execute = _run_with_fakes(monkeypatch)
    try:
        report = execute()
    finally:
        temp.cleanup()

    report["paper_timer_enable_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_post_collection_audit(report)


def test_post_collection_audit_has_no_runtime_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "systemctl" not in source
    assert "subprocess" not in source
    assert "persist_phase5_promotion" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase5_promotion_persisted": False' in source
    assert '"persistent_recurring_paper_automation_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
