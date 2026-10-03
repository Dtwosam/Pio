from __future__ import annotations

import importlib.util
import shlex
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/render_phase2_isolated_mutation_command.py"
SPEC = importlib.util.spec_from_file_location(
    "render_phase2_isolated_mutation_command",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Result(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def preflight(
    *,
    mutation_flag="--apply",
    executed=True,
    exit_code=0,
    json_valid=True,
    failure_category=None,
    argv=None,
    rpc_called=False,
):
    if argv is None:
        argv = (
            sys.executable,
            str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
            "--destination",
            "/tmp/build path",
        )
    return Result(
        state="SOURCE_BOOTSTRAP_REQUIRED",
        next_action="BOOTSTRAP_PINNED_SOURCE",
        next_tool="bootstrap_phase2_isolated_source.py",
        command_rendered=True,
        command_executed=executed,
        preflight_argv=argv,
        mutation_flag=mutation_flag,
        mutation_flag_appended=False,
        exit_code=exit_code,
        result_json_valid=json_valid,
        result={"status": "READY_CREATE"},
        failure_category=failure_category,
        lifecycle={"state": "SOURCE_BOOTSTRAP_REQUIRED"},
        read_only=True,
        rpc_called=rpc_called,
        database_write_performed=False,
        service_control_performed=False,
        daemon_reload_performed=False,
        production_tree_modified=False,
    )


def install(monkeypatch, report):
    monkeypatch.setattr(
        MODULE.RUNNER,
        "run_next_read_only_preflight",
        lambda **kwargs: report,
    )


def test_renderer_appends_apply_only_after_successful_preflight(monkeypatch):
    report = preflight(mutation_flag="--apply")
    install(monkeypatch, report)

    rendered = MODULE.render_reviewed_mutation_command()

    assert rendered.preflight_succeeded is True
    assert rendered.mutation_rendered is True
    assert rendered.mutation_executed is False
    assert rendered.mutation_argv[-1] == "--apply"
    assert "--apply" not in report.preflight_argv
    assert shlex.split(rendered.mutation_command) == list(
        rendered.mutation_argv
    )


def test_renderer_supports_prepare_boundary(monkeypatch):
    report = preflight(mutation_flag="--prepare")
    install(monkeypatch, report)

    rendered = MODULE.render_reviewed_mutation_command()

    assert rendered.mutation_argv[-1] == "--prepare"
    assert rendered.mutation_executed is False


def test_renderer_never_renders_mutation_after_failed_preflight(monkeypatch):
    install(
        monkeypatch,
        preflight(
            exit_code=2,
            failure_category="PREFLIGHT_NONZERO_EXIT",
        ),
    )

    rendered = MODULE.render_reviewed_mutation_command()

    assert rendered.preflight_succeeded is False
    assert rendered.mutation_rendered is False
    assert rendered.mutation_argv is None
    assert rendered.mutation_command is None


def test_renderer_returns_no_mutation_for_monitoring_state(monkeypatch):
    report = preflight(mutation_flag=None)
    report.state = "RUNNING_HEALTHY"
    report.next_action = "MONITOR_ZERO_RPC_STATUS"
    install(monkeypatch, report)

    rendered = MODULE.render_reviewed_mutation_command()

    assert rendered.preflight_succeeded is True
    assert rendered.mutation_flag is None
    assert rendered.mutation_rendered is False
    assert rendered.mutation_executed is False


def test_renderer_rejects_unexpected_mutation_flag(monkeypatch):
    install(monkeypatch, preflight(mutation_flag="--force"))

    with pytest.raises(ValueError, match="unexpected lifecycle mutation"):
        MODULE.render_reviewed_mutation_command()


def test_renderer_rejects_preflight_argv_with_mutation_already_present(
    monkeypatch,
):
    argv = (
        sys.executable,
        str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
        "--apply",
    )
    install(monkeypatch, preflight(argv=argv, mutation_flag="--apply"))

    with pytest.raises(ValueError, match="already contains"):
        MODULE.render_reviewed_mutation_command()


def test_renderer_rejects_preflight_boundary_crossing(monkeypatch):
    install(monkeypatch, preflight(rpc_called=True))

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.render_reviewed_mutation_command()


def test_renderer_preserves_shell_safe_parameter_boundaries(monkeypatch):
    dangerous = "/tmp/$(touch should-not-run) path"
    argv = (
        sys.executable,
        str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
        "--destination",
        dangerous,
    )
    install(monkeypatch, preflight(argv=argv))

    rendered = MODULE.render_reviewed_mutation_command()

    assert dangerous in rendered.mutation_argv
    assert shlex.split(rendered.mutation_command) == list(
        rendered.mutation_argv
    )


def test_renderer_does_not_render_when_preflight_was_not_executed(
    monkeypatch,
):
    install(
        monkeypatch,
        preflight(
            executed=False,
            exit_code=None,
            json_valid=False,
        ),
    )

    rendered = MODULE.render_reviewed_mutation_command()

    assert rendered.preflight_succeeded is False
    assert rendered.mutation_argv is None
    assert rendered.mutation_executed is False



def test_renderer_fingerprints_are_stable_for_same_preflight(monkeypatch):
    report = preflight()
    install(monkeypatch, report)

    first = MODULE.render_reviewed_mutation_command()
    second = MODULE.render_reviewed_mutation_command()

    assert first.preflight_fingerprint == second.preflight_fingerprint
    assert first.mutation_fingerprint == second.mutation_fingerprint
    assert len(first.preflight_fingerprint) == 64
    assert len(first.mutation_fingerprint) == 64
    assert first.preflight_fingerprint == first.preflight_fingerprint.lower()
    assert first.mutation_fingerprint == first.mutation_fingerprint.lower()


def test_renderer_preflight_fingerprint_changes_with_result(monkeypatch):
    first_report = preflight()
    install(monkeypatch, first_report)
    first = MODULE.render_reviewed_mutation_command()

    second_report = preflight()
    second_report.result = {"status": "ALREADY_PINNED"}
    install(monkeypatch, second_report)
    second = MODULE.render_reviewed_mutation_command()

    assert first.preflight_fingerprint != second.preflight_fingerprint
    assert first.mutation_fingerprint != second.mutation_fingerprint


def test_renderer_mutation_fingerprint_changes_with_argv(monkeypatch):
    first_report = preflight()
    install(monkeypatch, first_report)
    first = MODULE.render_reviewed_mutation_command()

    second_report = preflight(
        argv=(
            sys.executable,
            str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
            "--destination",
            "/tmp/other-build",
        )
    )
    install(monkeypatch, second_report)
    second = MODULE.render_reviewed_mutation_command()

    assert first.preflight_fingerprint != second.preflight_fingerprint
    assert first.mutation_fingerprint != second.mutation_fingerprint


def test_renderer_has_no_mutation_fingerprint_when_no_command(monkeypatch):
    install(
        monkeypatch,
        preflight(
            exit_code=2,
            failure_category="PREFLIGHT_NONZERO_EXIT",
        ),
    )

    rendered = MODULE.render_reviewed_mutation_command()

    assert rendered.preflight_fingerprint
    assert rendered.mutation_fingerprint is None
    assert rendered.mutation_rendered is False


def test_fingerprint_uses_canonical_json_ordering():
    left = MODULE._fingerprint(
        {"b": 2, "a": {"y": 4, "x": 3}}
    )
    right = MODULE._fingerprint(
        {"a": {"x": 3, "y": 4}, "b": 2}
    )

    assert left == right
