from types import SimpleNamespace

import pytest

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_operator_status.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_operator_status",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Report(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def health(**overrides):
    values = dict(
        runtime_ready=True,
        installed_units_exact=True,
        env_ready=True,
        data_ready=True,
        detector_state_ready=True,
        detector_active_enabled=True,
        streams_active=True,
        timer_active=True,
        timer_enabled=True,
        timer_active_enabled=True,
        legacy_collectors_quiescent=True,
        collection_healthy=True,
        pause_recommended=False,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )
    values.update(overrides)
    return Report(**values)


def efficiency(**overrides):
    values = dict(
        repeated_provider_rejection=False,
        latest_cycle_recent=True,
        timer_active=True,
        timer_enabled=True,
        timer_paused=False,
        pause_recommended=False,
        attention_required=False,
        discovery_cache=Report(reusable_now=True),
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )
    values.update(overrides)
    return Report(**values)


def install(monkeypatch, *, health_report=None, efficiency_report=None):
    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_timer_health",
        lambda **kwargs: health_report or health(),
    )
    monkeypatch.setattr(
        MODULE.EFFICIENCY,
        "inspect_rpc_efficiency",
        lambda **kwargs: efficiency_report or efficiency(),
    )


def test_operator_status_reports_healthy_without_provider_credit_guess(
    monkeypatch,
):
    install(monkeypatch)

    report = MODULE.inspect_operator_status()

    assert report.state == "HEALTHY"
    assert report.attention_required is False
    assert report.topology_ready is True
    assert report.collection_running is True
    assert report.discovery_cache_reusable_now is True
    assert report.collector_attempts_are_not_provider_credits is True
    assert report.provider_credit_count_available is False
    assert report.rpc_called is False


def test_operator_status_surfaces_fresh_rate_limit_before_more_timer_cycles(
    monkeypatch,
):
    install(
        monkeypatch,
        health_report=health(
            collection_healthy=False,
            pause_recommended=True,
        ),
        efficiency_report=efficiency(
            repeated_provider_rejection=True,
            latest_cycle_recent=True,
            pause_recommended=True,
            attention_required=True,
        ),
    )

    report = MODULE.inspect_operator_status()

    assert report.state == "RATE_LIMIT_PAUSE_REQUIRED"
    assert report.provider_rate_limit_incident is True
    assert report.provider_rate_limit_paused is False
    assert report.future_timer_cycles_paused is False
    assert report.rate_limit_waste_guard_satisfied is False
    assert report.attention_required is True


def test_operator_status_recognizes_rate_limit_timer_already_paused(monkeypatch):
    install(
        monkeypatch,
        health_report=health(
            timer_active=False,
            timer_enabled=False,
            timer_active_enabled=False,
            collection_healthy=False,
        ),
        efficiency_report=efficiency(
            repeated_provider_rejection=True,
            latest_cycle_recent=True,
            timer_active=False,
            timer_enabled=False,
            timer_paused=True,
            pause_recommended=False,
            attention_required=False,
        ),
    )

    report = MODULE.inspect_operator_status()

    assert report.state == "RATE_LIMIT_PAUSED"
    assert report.provider_rate_limit_paused is True
    assert report.future_timer_cycles_paused is True
    assert report.rate_limit_waste_guard_satisfied is True
    assert report.attention_required is True


def test_operator_status_prioritizes_broken_topology(monkeypatch):
    install(
        monkeypatch,
        health_report=health(
            runtime_ready=False,
            collection_healthy=False,
        ),
    )

    report = MODULE.inspect_operator_status()

    assert report.state == "TOPOLOGY_NOT_READY"
    assert report.topology_ready is False


def test_operator_status_distinguishes_non_rate_limited_stopped_timer(
    monkeypatch,
):
    install(
        monkeypatch,
        health_report=health(
            timer_active=False,
            timer_enabled=False,
            timer_active_enabled=False,
            collection_healthy=False,
        ),
        efficiency_report=efficiency(
            timer_active=False,
            timer_enabled=False,
            timer_paused=True,
        ),
    )

    report = MODULE.inspect_operator_status()

    assert report.state == "TIMER_NOT_RUNNING"
    assert report.provider_rate_limit_incident is False


@pytest.mark.parametrize("which", ("health", "efficiency"))
def test_operator_status_rejects_boundary_crossing(monkeypatch, which):
    h = health()
    e = efficiency()
    if which == "health":
        h.rpc_called = True
    else:
        e.service_control_performed = True
    install(monkeypatch, health_report=h, efficiency_report=e)

    with pytest.raises(ValueError, match="read-only boundary"):
        MODULE.inspect_operator_status()
