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


def install(
    monkeypatch,
    *,
    health_report=None,
    efficiency_report=None,
    autopause_failed=False,
):
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
    monkeypatch.setattr(
        MODULE,
        "_unit_failed",
        lambda *args, **kwargs: autopause_failed,
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
    assert report.autopause_service_failed is False
    assert report.autopause_failure_relevant is False
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



def test_operator_status_surfaces_failed_autopause_hook_during_active_incident(
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
        autopause_failed=True,
    )

    report = MODULE.inspect_operator_status()

    assert report.state == "RATE_LIMIT_PAUSE_REQUIRED"
    assert report.provider_rate_limit_incident is True
    assert report.autopause_service_failed is True
    assert report.autopause_failure_relevant is True
    assert report.future_timer_cycles_paused is False
    assert report.attention_required is True


def test_operator_status_does_not_treat_stale_autopause_failure_as_incident(
    monkeypatch,
):
    install(
        monkeypatch,
        autopause_failed=True,
    )

    report = MODULE.inspect_operator_status()

    assert report.state == "HEALTHY"
    assert report.provider_rate_limit_incident is False
    assert report.autopause_service_failed is True
    assert report.autopause_failure_relevant is False
    assert report.attention_required is False



def test_operator_status_rejects_timer_health_drift_between_snapshots(
    monkeypatch,
):
    health_reports = iter(
        (
            health(),
            health(
                timer_active=False,
                timer_enabled=False,
                timer_active_enabled=False,
                collection_healthy=False,
            ),
        )
    )
    stable_efficiency = efficiency()
    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_timer_health",
        lambda **kwargs: next(health_reports),
    )
    monkeypatch.setattr(
        MODULE.EFFICIENCY,
        "inspect_rpc_efficiency",
        lambda **kwargs: stable_efficiency,
    )
    monkeypatch.setattr(
        MODULE,
        "_unit_failed",
        lambda *args, **kwargs: False,
    )

    with pytest.raises(
        ValueError,
        match="operator inputs changed during status inspection",
    ):
        MODULE.inspect_operator_status()


def test_operator_status_rejects_rpc_efficiency_drift_between_snapshots(
    monkeypatch,
):
    stable_health = health()
    efficiency_reports = iter(
        (
            efficiency(),
            efficiency(
                repeated_provider_rejection=True,
                latest_cycle_recent=True,
                pause_recommended=True,
                attention_required=True,
            ),
        )
    )
    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_timer_health",
        lambda **kwargs: stable_health,
    )
    monkeypatch.setattr(
        MODULE.EFFICIENCY,
        "inspect_rpc_efficiency",
        lambda **kwargs: next(efficiency_reports),
    )
    monkeypatch.setattr(
        MODULE,
        "_unit_failed",
        lambda *args, **kwargs: False,
    )

    with pytest.raises(
        ValueError,
        match="operator inputs changed during status inspection",
    ):
        MODULE.inspect_operator_status()


def test_operator_status_rejects_autopause_state_drift(
    monkeypatch,
):
    stable_health = health()
    stable_efficiency = efficiency()
    unit_states = iter((False, True))
    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_timer_health",
        lambda **kwargs: stable_health,
    )
    monkeypatch.setattr(
        MODULE.EFFICIENCY,
        "inspect_rpc_efficiency",
        lambda **kwargs: stable_efficiency,
    )
    monkeypatch.setattr(
        MODULE,
        "_unit_failed",
        lambda *args, **kwargs: next(unit_states),
    )

    with pytest.raises(
        ValueError,
        match="operator inputs changed during status inspection",
    ):
        MODULE.inspect_operator_status()


def test_operator_status_allows_age_only_progress_between_snapshots(
    monkeypatch,
):
    health_reports = iter(
        (
            health(latest_cycle_age_seconds=10.0),
            health(latest_cycle_age_seconds=10.2),
        )
    )
    efficiency_reports = iter(
        (
            efficiency(latest_cycle_age_seconds=10.0),
            efficiency(latest_cycle_age_seconds=10.2),
        )
    )
    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_timer_health",
        lambda **kwargs: next(health_reports),
    )
    monkeypatch.setattr(
        MODULE.EFFICIENCY,
        "inspect_rpc_efficiency",
        lambda **kwargs: next(efficiency_reports),
    )
    monkeypatch.setattr(
        MODULE,
        "_unit_failed",
        lambda *args, **kwargs: False,
    )

    report = MODULE.inspect_operator_status()

    assert report.state == "HEALTHY"
    assert report.attention_required is False



def test_operator_status_allows_discovery_cache_age_to_advance(
    monkeypatch,
):
    stable_health = health()
    first_cache = Report(
        path="/opt/pio/data/phase2-position-discovery-cache.json",
        exists=True,
        regular_file=True,
        symlink=False,
        format_valid=True,
        pool_matches=True,
        complete=True,
        captured_at="2026-10-04T10:00:00+00:00",
        age_seconds=100.0,
        max_age_seconds=3600,
        reusable_now=True,
        positions_found=10,
        positions_returned=10,
        positions_cached=10,
    )
    second_cache = Report(
        path="/opt/pio/data/phase2-position-discovery-cache.json",
        exists=True,
        regular_file=True,
        symlink=False,
        format_valid=True,
        pool_matches=True,
        complete=True,
        captured_at="2026-10-04T10:00:00+00:00",
        age_seconds=100.4,
        max_age_seconds=3600,
        reusable_now=True,
        positions_found=10,
        positions_returned=10,
        positions_cached=10,
    )
    efficiency_reports = iter(
        (
            efficiency(discovery_cache=first_cache),
            efficiency(discovery_cache=second_cache),
        )
    )
    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_timer_health",
        lambda **kwargs: stable_health,
    )
    monkeypatch.setattr(
        MODULE.EFFICIENCY,
        "inspect_rpc_efficiency",
        lambda **kwargs: next(efficiency_reports),
    )
    monkeypatch.setattr(
        MODULE,
        "_unit_failed",
        lambda *args, **kwargs: False,
    )

    report = MODULE.inspect_operator_status()

    assert report.state == "HEALTHY"
    assert report.discovery_cache_reusable_now is True
