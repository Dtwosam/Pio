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
    / "start_manual_market_paper_phase5_evidence_collection.py"
)

SPEC = importlib.util.spec_from_file_location(
    "start_manual_market_paper_phase5_evidence_collection",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _readiness() -> dict:
    return {
        "collection_readiness_sha256": "1" * 64,
        "phase5_evidence_status_sha256": "2" * 64,
        "collection_plan_sha256": "3" * 64,
        "collection_request_sha256": "4" * 64,
        "saved_signed_authorization_verification_sha256": "5" * 64,
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "requested_collection_seconds": 3600,
        "scheduler_extra_args_text": "--max-positions 3 --refresh-jupiter-quotes",
        "collection_readiness_ready": True,
        "requires_bounded_activation_executor": True,
    }


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _run_with_fakes(
    monkeypatch,
    *,
    fresh=None,
    target_active="active",
    target_unit_file_state="disabled",
    schedule_error: Exception | None = None,
    start_error: Exception | None = None,
):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    production.mkdir()
    saved = _readiness()
    readiness_path = _write(root, "readiness.json", saved)
    calls: list[tuple] = []

    class FakeReadinessModule:
        @staticmethod
        def validate_collection_readiness(value):
            assert isinstance(value, dict)

        @staticmethod
        def build_collection_readiness(**kwargs):
            calls.append(("fresh-readiness",))
            return copy.deepcopy(fresh if fresh is not None else saved)

    monkeypatch.setattr(
        MODULE,
        "_load_readiness_module",
        lambda source: FakeReadinessModule,
    )

    def fake_schedule(*, guard_base, target_timer_unit, seconds):
        calls.append(("schedule-guard", target_timer_unit, seconds))
        if schedule_error is not None:
            raise schedule_error
        return f"{guard_base}.timer", f"{guard_base}.service"

    def fake_start(unit):
        calls.append(("start-timer", unit))
        if start_error is not None:
            raise start_error

    def fake_property(unit, prop):
        calls.append(("show", unit, prop))
        if prop == "ActiveState":
            return target_active
        if prop == "UnitFileState":
            return target_unit_file_state
        raise AssertionError(prop)

    def fake_stop(*units):
        calls.append(("stop", *units))

    monkeypatch.setattr(MODULE, "_schedule_stop_guard", fake_schedule)
    monkeypatch.setattr(MODULE, "_systemctl_start", fake_start)
    monkeypatch.setattr(MODULE, "_systemctl_property", fake_property)
    monkeypatch.setattr(MODULE, "_systemctl_stop", fake_stop)

    def execute():
        return MODULE.activate_phase5_evidence_collection(
            repository=production,
            source_tree=ROOT,
            post_cycle_audit_path=root / "post-cycle.json",
            collection_plan_path=root / "plan.json",
            collection_request_path=root / "request.json",
            signed_authorization_verification_path=root / "verification.json",
            signed_payload_path=root / "payload.json",
            signature_path=root / "payload.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="6" * 64,
            collection_readiness_path=readiness_path,
            expected_collection_readiness_sha256="1" * 64,
            lock_path=root / "collection.lock",
        )

    return temp, calls, execute


def _reseal(receipt: dict) -> None:
    identity = {field: receipt[field] for field in MODULE.RECEIPT_FIELDS}
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_readiness_tool_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_stop_guard_is_scheduled_before_timer_start(monkeypatch):
    temp, calls, execute = _run_with_fakes(monkeypatch)
    try:
        receipt = execute()
    finally:
        temp.cleanup()

    schedule_index = next(
        i for i, call in enumerate(calls) if call[0] == "schedule-guard"
    )
    start_index = next(
        i for i, call in enumerate(calls) if call[0] == "start-timer"
    )
    assert schedule_index < start_index

    MODULE.validate_activation_receipt(receipt)
    assert receipt["paper_timer_start_authorized"] is True
    assert receipt["paper_timer_started"] is True
    assert receipt["paper_timer_active"] is True
    assert receipt["paper_timer_unit_file_state"] == "disabled"
    assert receipt["paper_timer_remains_disabled"] is True
    assert receipt["auto_stop_guard_scheduled"] is True
    assert receipt["reboot_fail_closed"] is True
    assert receipt["bounded_collection_started"] is True
    assert receipt["persistent_recurring_paper_automation_authorized"] is False
    assert receipt["paper_timer_enable_authorized"] is False
    assert receipt["live_capital_authorized"] is False


def test_fresh_readiness_drift_fails_before_systemd_action(monkeypatch):
    fresh = _readiness()
    fresh["run_id"] = "drifted"
    temp, calls, execute = _run_with_fakes(monkeypatch, fresh=fresh)
    try:
        with pytest.raises(ValueError, match="differs from saved"):
            execute()
    finally:
        temp.cleanup()

    assert not any(call[0] == "schedule-guard" for call in calls)
    assert not any(call[0] == "start-timer" for call in calls)


def test_guard_failure_prevents_timer_start(monkeypatch):
    temp, calls, execute = _run_with_fakes(
        monkeypatch,
        schedule_error=ValueError("guard failed"),
    )
    try:
        with pytest.raises(ValueError, match="guard failed"):
            execute()
    finally:
        temp.cleanup()

    assert any(call[0] == "schedule-guard" for call in calls)
    assert not any(call[0] == "start-timer" for call in calls)


def test_timer_start_failure_triggers_cleanup(monkeypatch):
    temp, calls, execute = _run_with_fakes(
        monkeypatch,
        start_error=ValueError("start failed"),
    )
    try:
        with pytest.raises(ValueError, match="start failed"):
            execute()
    finally:
        temp.cleanup()

    assert any(call[0] == "schedule-guard" for call in calls)
    assert any(call[0] == "start-timer" for call in calls)
    stop_calls = [call for call in calls if call[0] == "stop"]
    assert stop_calls
    assert any("pio-paper@pio-proof-1.timer" in call for call in stop_calls)


def test_timer_that_became_enabled_is_immediately_stopped(monkeypatch):
    temp, calls, execute = _run_with_fakes(
        monkeypatch,
        target_unit_file_state="enabled",
    )
    try:
        with pytest.raises(ValueError, match="became persistent"):
            execute()
    finally:
        temp.cleanup()

    stop_calls = [call for call in calls if call[0] == "stop"]
    assert stop_calls
    assert any("pio-paper@pio-proof-1.timer" in call for call in stop_calls)


def test_timer_that_did_not_become_active_is_stopped_and_fails(monkeypatch):
    temp, calls, execute = _run_with_fakes(
        monkeypatch,
        target_active="inactive",
    )
    try:
        with pytest.raises(ValueError, match="did not become active"):
            execute()
    finally:
        temp.cleanup()

    assert any(call[0] == "stop" for call in calls)


def test_schedule_guard_uses_transient_timer_and_stop_only(monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.setattr(MODULE, "_systemctl_path", lambda: Path("/usr/bin/systemctl"))
    monkeypatch.setattr(MODULE, "_systemd_run_path", lambda: Path("/usr/bin/systemd-run"))

    def fake_run(args, *, label):
        calls.append(list(args))

        class Result:
            stdout = ""

        return Result()

    monkeypatch.setattr(MODULE, "_run", fake_run)
    monkeypatch.setattr(
        MODULE,
        "_systemctl_property",
        lambda unit, prop: "active",
    )

    timer, service = MODULE._schedule_stop_guard(
        guard_base="pio-phase5-evidence-stop-pio-proof-1-aaaaaaaaaaaa",
        target_timer_unit="pio-paper@pio-proof-1.timer",
        seconds=3600,
    )

    assert timer.endswith(".timer")
    assert service.endswith(".service")
    command = calls[0]
    assert command[:2] == ["/usr/bin/systemd-run", "--quiet"]
    assert "--on-active=3600s" in command
    assert "--timer-property=AccuracySec=1s" in command
    assert command[-3:] == [
        "/usr/bin/systemctl",
        "stop",
        "pio-paper@pio-proof-1.timer",
    ]
    assert "enable" not in command
    assert "disable" not in command


def test_resealed_receipt_cannot_claim_persistent_timer_enable(monkeypatch):
    temp, _, execute = _run_with_fakes(monkeypatch)
    try:
        receipt = execute()
    finally:
        temp.cleanup()

    receipt["paper_timer_enable_authorized"] = True
    _reseal(receipt)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_activation_receipt(receipt)


def test_resealed_receipt_cannot_authorize_live_capital(monkeypatch):
    temp, _, execute = _run_with_fakes(monkeypatch)
    try:
        receipt = execute()
    finally:
        temp.cleanup()

    receipt["live_capital_authorized"] = True
    _reseal(receipt)

    with pytest.raises(ValueError, match="live_capital_authorized=false"):
        MODULE.validate_activation_receipt(receipt)


def test_activation_tool_never_enables_timer_or_touches_git():
    source = TOOL.read_text(encoding="utf-8")

    assert '["enable"' not in source
    assert '"enable",' not in source
    assert '["disable"' not in source
    assert '"disable",' not in source
    assert "daemon-reload" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert "shell=True" not in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"persistent_recurring_paper_automation_authorized": False' in source
    assert '"new_market_entry_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
