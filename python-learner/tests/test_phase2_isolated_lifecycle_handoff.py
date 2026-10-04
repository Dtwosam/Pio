from types import SimpleNamespace
import hashlib

import pytest

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_lifecycle_handoff.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_lifecycle_handoff",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Result(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def activation(
    *,
    runtime_ready=True,
    installed_units_exact=True,
    activation_ready=False,
    read_only=True,
    rpc_called=False,
):
    return Result(
        runtime_ready=runtime_ready,
        runtime_current="/opt/pio-phase2-runtime/current",
        installed_units_exact=installed_units_exact,
        activation_ready=activation_ready,
        env_file_regular=True,
        env_keys=(Result(configured=True),),
        position_pool_matches_detector_topology=True,
        data_files=(
            Result(exists=True, regular_file=True, symlink=False),
        ),
        detector_state_valid=True,
        detector_cursors_complete=True,
        unit_states=(Result(ready=True),),
        read_only=read_only,
        rpc_called=rpc_called,
        daemon_reload_performed=False,
        service_control_performed=False,
    )


def smoke(*, smoke_ready=False):
    return Result(
        runtime_ready=True,
        installed_units_exact=True,
        env_ready=True,
        data_ready=True,
        detector_state_ready=True,
        legacy_collectors_quiescent=True,
        streams_active=smoke_ready,
        detector_active=smoke_ready,
        detector_enabled=smoke_ready,
        evidence_service_inactive=True,
        evidence_timer_inactive=True,
        evidence_timer_disabled=True,
        smoke_ready=smoke_ready,
        read_only=True,
        rpc_called=False,
        service_control_performed=False,
    )


def operator(
    *,
    state="TIMER_NOT_RUNNING",
    collection_running=False,
    incident=False,
    paused=False,
):
    return Result(
        state=state,
        collection_running=collection_running,
        provider_rate_limit_incident=incident,
        provider_rate_limit_paused=paused,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def timer(*, ready=False):
    return Result(
        smoke_readiness_current=True,
        receipt_regular=ready,
        receipt_valid=ready,
        receipt_runtime_matches=ready,
        receipt_fresh=ready,
        receipt_stage_statuses_valid=ready,
        evidence_row_present=ready,
        evidence_row_matches_receipt=ready,
        receipt_is_latest_for_pool=ready,
        evidence_row_non_qualified=ready,
        evidence_row_no_promotion=ready,
        timer_ready=ready,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def source_bootstrap(
    *,
    status="READY_CREATE",
    read_only=True,
    repository_url="https://github.com/Dtwosam/Pio.git",
):
    return Result(
        status=status,
        repository_url=repository_url,
        read_only=read_only,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        daemon_reload_performed=False,
    )


def source_runtime(*, ready=False, production_modified=False):
    return Result(
        runtime_ready=ready,
        production_tree_modified=production_modified,
        rpc_called=False,
        service_control_performed=False,
    )


def unit_upgrade(
    *,
    ready=True,
    upgrade_needed=False,
    installer_needed=True,
    statuses=("NOT_INSTALLED", "NOT_INSTALLED"),
):
    names = (
        "pio-phase2-isolated-evidence-cycle.service",
        "pio-phase2-isolated-evidence-cycle.timer",
    )
    return Result(
        ready=ready,
        upgrade_needed=upgrade_needed,
        installer_needed=installer_needed,
        applied=False,
        files_updated=0,
        backup_root=None,
        daemon_reload_performed=False,
        service_control_performed=False,
        rpc_called=False,
        units=tuple(
            Result(name=name, status=status)
            for name, status in zip(names, statuses)
        ),
    )


def install(
    monkeypatch,
    *,
    activation_report,
    smoke_report=None,
    operator_report=None,
    timer_report=None,
    source_bootstrap_report=None,
    source_runtime_report=None,
    unit_upgrade_report=None,
):
    monkeypatch.setattr(
        MODULE.ACTIVATION,
        "inspect_activation",
        lambda **kwargs: activation_report,
    )
    if smoke_report is not None:
        monkeypatch.setattr(
            MODULE.SMOKE,
            "inspect_smoke_readiness",
            lambda **kwargs: smoke_report,
        )
    if operator_report is not None:
        monkeypatch.setattr(
            MODULE.OPERATOR,
            "inspect_operator_status",
            lambda **kwargs: operator_report,
        )
    if timer_report is not None:
        monkeypatch.setattr(
            MODULE.TIMER,
            "inspect_timer_readiness",
            lambda **kwargs: timer_report,
        )
    if source_bootstrap_report is not None:
        monkeypatch.setattr(
            MODULE.BOOTSTRAP,
            "inspect_pinned_source",
            lambda **kwargs: source_bootstrap_report,
        )
    if source_runtime_report is not None:
        monkeypatch.setattr(
            MODULE.RUNTIME_CHECK,
            "inspect_runtime",
            lambda *args, **kwargs: source_runtime_report,
        )
    monkeypatch.setattr(
        MODULE.UNIT_UPGRADE,
        "inspect_upgrade",
        lambda **kwargs: (
            unit_upgrade_report
            if unit_upgrade_report is not None
            else unit_upgrade()
        ),
    )


def test_handoff_bootstraps_missing_pinned_source_before_runtime_staging(
    monkeypatch,
):
    install(
        monkeypatch,
        activation_report=activation(runtime_ready=False),
        source_runtime_report=source_runtime(ready=False),
        source_bootstrap_report=source_bootstrap(status="READY_CREATE"),
    )

    report = MODULE.inspect_lifecycle_handoff(
        source_tree="/tmp/pio-phase2-build/pinned"
    )

    assert report.state == "SOURCE_BOOTSTRAP_REQUIRED"
    assert report.next_action == "BOOTSTRAP_PINNED_SOURCE"
    assert report.next_tool == "bootstrap_phase2_isolated_source.py"
    assert report.source_status == "READY_CREATE"
    assert report.source_runtime_ready is False
    assert report.blockers == ("PINNED_SOURCE_MISSING",)
    assert report.next_parameters == {
        "destination": "/tmp/pio-phase2-build/pinned",
        "repository_url": "https://github.com/Dtwosam/Pio.git",
    }
    assert report.next_mutation_flag == "--apply"
    assert report.smoke_readiness is None
    assert report.operator_status is None


def test_handoff_prepares_clean_pinned_source_before_staging(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(runtime_ready=False),
        source_runtime_report=source_runtime(ready=False),
        source_bootstrap_report=source_bootstrap(status="ALREADY_PINNED"),
    )

    report = MODULE.inspect_lifecycle_handoff(
        source_tree="/tmp/pio-phase2-build/pinned"
    )

    assert report.state == "SOURCE_PREPARATION_REQUIRED"
    assert report.next_action == "PREPARE_PINNED_RUNTIME"
    assert report.next_tool == "prepare_phase2_isolated_runtime.py"
    assert report.source_status == "ALREADY_PINNED"
    assert report.blockers == ("PINNED_SOURCE_NOT_PREPARED",)
    assert report.next_parameters == {
        "source_tree": "/tmp/pio-phase2-build/pinned",
    }
    assert report.next_mutation_flag == "--prepare"


def test_handoff_stages_fully_prepared_source(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(runtime_ready=False),
        source_runtime_report=source_runtime(ready=True),
    )

    report = MODULE.inspect_lifecycle_handoff(
        source_tree="/tmp/pio-phase2-build/pinned"
    )

    assert report.state == "RUNTIME_STAGING_READY"
    assert report.next_action == "STAGE_REVIEWED_RUNTIME"
    assert report.next_tool == "stage_phase2_isolated_runtime.py"
    assert report.source_status == "PREPARED_RUNTIME_READY"
    assert report.source_runtime_ready is True
    assert report.blockers == ()
    assert report.next_parameters == {
        "source_tree": "/tmp/pio-phase2-build/pinned",
        "destination_root": "/opt/pio-phase2-runtime",
    }
    assert report.next_mutation_flag == "--apply"


def test_handoff_surfaces_pinned_source_conflict(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(runtime_ready=False),
        source_runtime_report=source_runtime(ready=False),
        source_bootstrap_report=source_bootstrap(
            status="CONFLICT_SOURCE_DRIFT"
        ),
    )

    report = MODULE.inspect_lifecycle_handoff(
        source_tree="/tmp/pio-phase2-build/pinned"
    )

    assert report.state == "SOURCE_CONFLICT_REVIEW_REQUIRED"
    assert report.next_action == "REVIEW_PINNED_SOURCE_CONFLICT"
    assert report.next_tool == "bootstrap_phase2_isolated_source.py"
    assert report.blockers == ("CONFLICT_SOURCE_DRIFT",)
    assert report.next_parameters["destination"] == (
        "/tmp/pio-phase2-build/pinned"
    )
    assert report.next_mutation_flag is None


def test_handoff_installs_units_after_runtime_is_ready(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(installed_units_exact=False),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "SYSTEMD_UNITS_NOT_READY"
    assert report.next_action == "INSTALL_REVIEWED_UNITS"
    assert report.next_tool == "install_phase2_isolated_systemd_units.py"
    assert report.next_parameters == {
        "source_tree": "/opt/pio-phase2-runtime/current",
        "runtime_root": "/opt/pio-phase2-runtime",
        "destination": "/etc/systemd/system",
    }
    assert report.next_mutation_flag == "--apply"
    assert report.blockers == ("SYSTEMD_UNITS_MISSING",)
    assert report.systemd_unit_upgrade["installer_needed"] is True


def test_handoff_routes_exact_predecessor_units_to_upgrader(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(installed_units_exact=False),
        unit_upgrade_report=unit_upgrade(
            upgrade_needed=True,
            installer_needed=False,
            statuses=("READY_UPDATE", "READY_UPDATE"),
        ),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "SYSTEMD_UNIT_UPGRADE_READY"
    assert report.next_action == "UPGRADE_REVIEWED_UNITS"
    assert report.next_tool == "upgrade_phase2_isolated_systemd_units.py"
    assert report.next_parameters == {
        "source_tree": "/opt/pio-phase2-runtime/current",
        "destination": "/etc/systemd/system",
    }
    assert report.next_mutation_flag == "--apply"
    assert report.blockers == ()
    assert report.systemd_unit_upgrade["upgrade_needed"] is True


def test_handoff_upgrades_predecessor_before_installing_missing_peer(
    monkeypatch,
):
    install(
        monkeypatch,
        activation_report=activation(installed_units_exact=False),
        unit_upgrade_report=unit_upgrade(
            upgrade_needed=True,
            installer_needed=True,
            statuses=("READY_UPDATE", "NOT_INSTALLED"),
        ),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "SYSTEMD_UNIT_UPGRADE_READY"
    assert report.next_tool == "upgrade_phase2_isolated_systemd_units.py"
    assert report.next_mutation_flag == "--apply"


def test_handoff_surfaces_modified_unit_conflict_without_mutation(
    monkeypatch,
):
    install(
        monkeypatch,
        activation_report=activation(installed_units_exact=False),
        unit_upgrade_report=unit_upgrade(
            ready=False,
            upgrade_needed=False,
            installer_needed=False,
            statuses=("CONFLICT_MODIFIED", "ALREADY_TARGET"),
        ),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "SYSTEMD_UNIT_CONFLICT_REVIEW_REQUIRED"
    assert report.next_action == "REVIEW_SYSTEMD_UNIT_CONFLICT"
    assert report.next_tool == "upgrade_phase2_isolated_systemd_units.py"
    assert report.next_mutation_flag is None
    assert report.blockers == (
        "pio-phase2-isolated-evidence-cycle.service:CONFLICT_MODIFIED",
    )


def test_handoff_rejects_mutating_unit_upgrade_inspection(monkeypatch):
    bad = unit_upgrade(
        upgrade_needed=True,
        installer_needed=False,
        statuses=("READY_UPDATE", "READY_UPDATE"),
    )
    bad.applied = True
    install(
        monkeypatch,
        activation_report=activation(installed_units_exact=False),
        unit_upgrade_report=bad,
    )

    with pytest.raises(ValueError, match="read-only boundary"):
        MODULE.inspect_lifecycle_handoff()


def test_handoff_activates_detector_topology_when_preflight_is_ready(
    monkeypatch,
):
    install(
        monkeypatch,
        activation_report=activation(activation_ready=True),
        smoke_report=smoke(smoke_ready=False),
        operator_report=operator(),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "DETECTOR_ACTIVATION_READY"
    assert report.next_action == "ACTIVATE_PRESTATE_STREAMS_AND_DETECTOR"
    assert report.blockers == ()
    assert report.next_parameters == {
        "runtime_root": "/opt/pio-phase2-runtime",
        "unit_destination": "/etc/systemd/system",
        "env_file": "/etc/pio/pio.env",
        "data_root": "/opt/pio/data",
    }
    assert report.next_mutation_flag == "--apply"


def test_handoff_requires_one_shot_smoke_before_timer(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(),
        smoke_report=smoke(smoke_ready=True),
        operator_report=operator(),
        timer_report=timer(ready=False),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "SMOKE_REQUIRED"
    assert report.next_action == "RUN_ONE_SHOT_SMOKE"
    assert report.next_tool == "run_phase2_isolated_smoke.py"
    assert "SMOKE_RECEIPT_INVALID" in report.blockers
    assert report.next_parameters["receipt"] == (
        "/opt/pio/data/phase2-isolated-smoke-receipt.json"
    )
    assert report.next_mutation_flag == "--apply"


def test_handoff_activates_timer_only_after_fresh_smoke(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(),
        smoke_report=smoke(smoke_ready=True),
        operator_report=operator(),
        timer_report=timer(ready=True),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "TIMER_ACTIVATION_READY"
    assert report.next_action == "ACTIVATE_EVIDENCE_TIMER"
    assert report.next_tool == "activate_phase2_isolated_timer.py"
    assert report.timer_ready is True
    assert report.blockers == ()
    assert report.next_parameters["max_receipt_age_seconds"] == 1800
    assert report.next_mutation_flag == "--apply"


def test_handoff_reports_healthy_running_system(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(),
        smoke_report=smoke(smoke_ready=False),
        operator_report=operator(
            state="HEALTHY",
            collection_running=True,
        ),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "RUNNING_HEALTHY"
    assert report.next_action == "MONITOR_ZERO_RPC_STATUS"
    assert report.attention_required is False
    assert report.collection_running is True
    assert report.next_parameters["env_file"] == "/etc/pio/pio.env"
    assert report.next_mutation_flag is None


def test_handoff_keeps_timer_paused_during_active_provider_incident(
    monkeypatch,
):
    install(
        monkeypatch,
        activation_report=activation(),
        smoke_report=smoke(smoke_ready=True),
        operator_report=operator(
            state="RATE_LIMIT_PAUSED",
            incident=True,
            paused=True,
        ),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "RATE_LIMIT_PAUSED"
    assert report.next_action == (
        "KEEP_TIMER_PAUSED_UNTIL_PROVIDER_RECOVERS"
    )
    assert report.next_tool == "check_phase2_isolated_operator_status.py"
    assert report.blockers == ("ACTIVE_PROVIDER_RATE_LIMIT_INCIDENT",)
    assert report.next_mutation_flag is None


def test_handoff_surfaces_manual_autopause_when_repeated_rejection_is_live(
    monkeypatch,
):
    install(
        monkeypatch,
        activation_report=activation(),
        smoke_report=smoke(smoke_ready=False),
        operator_report=operator(
            state="RATE_LIMIT_PAUSE_REQUIRED",
            collection_running=True,
            incident=True,
            paused=False,
        ),
    )

    report = MODULE.inspect_lifecycle_handoff()

    assert report.state == "RATE_LIMIT_PAUSE_REQUIRED"
    assert report.next_action == "RUN_RATE_LIMIT_AUTOPAUSE"
    assert report.next_tool == "autopause_phase2_isolated_timer.py"
    assert report.next_parameters == {
        "database": "/opt/pio/data/pio.db",
    }
    assert report.next_mutation_flag == "--apply"


def test_handoff_rejects_underlying_boundary_crossing(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(rpc_called=True),
    )

    with pytest.raises(ValueError, match="read-only boundary"):
        MODULE.inspect_lifecycle_handoff()



def test_handoff_rejects_source_bootstrap_boundary_crossing(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(runtime_ready=False),
        source_runtime_report=source_runtime(ready=False),
        source_bootstrap_report=source_bootstrap(
            status="READY_CREATE",
            read_only=False,
        ),
    )

    with pytest.raises(ValueError, match="read-only boundary"):
        MODULE.inspect_lifecycle_handoff(
            source_tree="/tmp/pio-phase2-build/pinned"
        )


def test_handoff_rejects_source_runtime_boundary_crossing(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(runtime_ready=False),
        source_runtime_report=source_runtime(
            ready=True,
            production_modified=True,
        ),
    )

    with pytest.raises(ValueError, match="read-only boundary"):
        MODULE.inspect_lifecycle_handoff(
            source_tree="/tmp/pio-phase2-build/pinned"
        )



def test_handoff_next_parameters_never_expose_env_values(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(activation_ready=True),
        smoke_report=smoke(smoke_ready=False),
        operator_report=operator(),
    )
    secret = "https://rpc.invalid/?api-key=super-secret"

    report = MODULE.inspect_lifecycle_handoff(
        env_file="/etc/pio/pio.env",
    )

    encoded = str(report.next_parameters)
    assert "SOLANA_RPC_URL" not in encoded
    assert "super-secret" not in encoded
    assert secret not in encoded
    assert report.next_parameters["env_file"] == "/etc/pio/pio.env"



def test_handoff_dependency_identity_is_one_reviewed_snapshot():
    commit, dependency_sha = MODULE._dependency_source_identity()

    assert len(commit) >= 40
    assert len(dependency_sha) == 64
    assert len(MODULE._DEPENDENCY_SNAPSHOTS) == 7

    for (
        _current,
        _relative,
        _loaded_path,
        encoded,
        _opened,
        _label,
    ) in MODULE._DEPENDENCY_SNAPSHOTS:
        assert encoded
        assert len(hashlib.sha256(encoded).hexdigest()) == 64


def test_handoff_dependencies_are_descriptor_captured_and_executed():
    source = TOOL.read_text(encoding="utf-8")

    assert "def _capture_dependency(" in source
    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "compile(encoded" in source
    assert "exec(code, module.__dict__)" in source
    assert "_DEPENDENCY_SNAPSHOTS" in source
    assert "spec.loader.exec_module(module)" not in source


def test_handoff_dependency_identity_does_not_reread_loaded_paths(monkeypatch):
    loaded_paths = {
        snapshot[2] for snapshot in MODULE._DEPENDENCY_SNAPSHOTS
    }
    real_read_bytes = Path.read_bytes

    def reject_dependency_reread(path):
        if path.resolve() in loaded_paths:
            raise AssertionError(
                "loaded lifecycle dependency path must not be reread"
            )
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_dependency_reread)

    commit, dependency_sha = MODULE._dependency_source_identity()

    assert len(commit) >= 40
    assert len(dependency_sha) == 64


def test_handoff_rejects_dependency_change_during_inspection(monkeypatch):
    install(
        monkeypatch,
        activation_report=activation(runtime_ready=False),
        source_runtime_report=source_runtime(ready=False),
        source_bootstrap_report=source_bootstrap(status="READY_CREATE"),
    )
    identities = iter(
        (
            ("a" * 40, "1" * 64),
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_dependency_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(
        ValueError,
        match="dependencies changed during inspection",
    ):
        MODULE.inspect_lifecycle_handoff(
            source_tree="/tmp/pio-phase2-build/pinned"
        )



def test_lifecycle_runbooks_cover_unit_upgrade_and_conflict_states():
    handoff_doc = (
        ROOT / "deploy/phase2-isolated-lifecycle-handoff.md"
    ).read_text(encoding="utf-8")
    command_doc = (
        ROOT / "deploy/phase2-isolated-next-command.md"
    ).read_text(encoding="utf-8")

    for value in (
        "SYSTEMD_UNIT_UPGRADE_READY",
        "SYSTEMD_UNIT_CONFLICT_REVIEW_REQUIRED",
        "upgrade_phase2_isolated_systemd_units.py",
    ):
        assert value in handoff_doc
        assert value in command_doc

    assert "UPGRADE_REVIEWED_UNITS" in handoff_doc
    assert "daemon-reload" in handoff_doc
    assert "without `--apply`" in command_doc
