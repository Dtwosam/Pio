from types import SimpleNamespace
import hashlib
import importlib.util
from pathlib import Path
import shlex
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/render_phase2_isolated_next_command.py"
SPEC = importlib.util.spec_from_file_location(
    "render_phase2_isolated_next_command",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Result(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def handoff(
    *,
    next_tool="bootstrap_phase2_isolated_source.py",
    next_parameters=None,
    mutation_flag="--apply",
    read_only=True,
    rpc_called=False,
):
    return Result(
        state="SOURCE_BOOTSTRAP_REQUIRED",
        next_action="BOOTSTRAP_PINNED_SOURCE",
        next_tool=next_tool,
        next_parameters=(
            {
                "destination": "/opt/pio-phase2-build/pinned",
                "repository_url": "https://github.com/Dtwosam/Pio.git",
            }
            if next_parameters is None
            else next_parameters
        ),
        next_mutation_flag=mutation_flag,
        read_only=read_only,
        rpc_called=rpc_called,
        database_write_performed=False,
        service_control_performed=False,
    )


def install(monkeypatch, report):
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_lifecycle_handoff",
        lambda **kwargs: report,
    )


def test_renderer_builds_preflight_without_mutation_flag(monkeypatch):
    install(monkeypatch, handoff())

    report = MODULE.render_lifecycle_command(
        python_executable="python3",
    )

    assert report.preflight_argv is not None
    assert report.preflight_argv[0] == "python3"
    assert report.preflight_argv[1].endswith(
        "/deploy/tools/bootstrap_phase2_isolated_source.py"
    )
    assert "--destination" in report.preflight_argv
    assert "--repository-url" in report.preflight_argv
    assert "--apply" not in report.preflight_argv
    assert report.mutation_flag == "--apply"
    assert report.mutation_flag_appended is False
    assert shlex.split(report.preflight_command) == list(
        report.preflight_argv
    )
    assert report.read_only is True
    assert report.rpc_called is False


def test_renderer_shell_quotes_parameter_values(monkeypatch):
    dangerous = "/tmp/build path/$(touch should-not-run)"
    install(
        monkeypatch,
        handoff(
            next_parameters={
                "destination": dangerous,
                "repository_url": "https://github.com/Dtwosam/Pio.git",
            },
        ),
    )

    report = MODULE.render_lifecycle_command()

    assert report.preflight_argv is not None
    assert dangerous in report.preflight_argv
    assert shlex.split(report.preflight_command) == list(
        report.preflight_argv
    )
    assert "$(touch should-not-run)" in report.preflight_command


def test_renderer_keeps_prepare_boundary_separate(monkeypatch):
    install(
        monkeypatch,
        handoff(
            next_tool="prepare_phase2_isolated_runtime.py",
            next_parameters={"source_tree": "/tmp/pinned"},
            mutation_flag="--prepare",
        ),
    )

    report = MODULE.render_lifecycle_command()

    assert "--prepare" not in report.preflight_argv
    assert report.mutation_flag == "--prepare"


def test_renderer_rejects_tool_path_traversal(monkeypatch):
    install(
        monkeypatch,
        handoff(next_tool="../../tmp/unreviewed.py"),
    )

    with pytest.raises(ValueError, match="local basename"):
        MODULE.render_lifecycle_command()


def test_renderer_rejects_unexpected_mutation_flag(monkeypatch):
    install(
        monkeypatch,
        handoff(mutation_flag="--force"),
    )

    with pytest.raises(ValueError, match="unexpected lifecycle mutation"):
        MODULE.render_lifecycle_command()


def test_renderer_rejects_unsupported_parameter_value(monkeypatch):
    install(
        monkeypatch,
        handoff(next_parameters={"apply": True}),
    )

    with pytest.raises(ValueError, match="unsupported next-step"):
        MODULE.render_lifecycle_command()


def test_renderer_rejects_unsafe_parameter_name(monkeypatch):
    install(
        monkeypatch,
        handoff(next_parameters={"bad;name": "value"}),
    )

    with pytest.raises(ValueError, match="unsafe next-step parameter"):
        MODULE.render_lifecycle_command()


def test_renderer_rejects_underlying_boundary_crossing(monkeypatch):
    install(
        monkeypatch,
        handoff(rpc_called=True),
    )

    with pytest.raises(ValueError, match="read-only boundary"):
        MODULE.render_lifecycle_command()


def test_renderer_returns_no_command_when_handoff_has_no_tool(monkeypatch):
    install(
        monkeypatch,
        handoff(
            next_tool=None,
            next_parameters={},
            mutation_flag=None,
        ),
    )

    report = MODULE.render_lifecycle_command()

    assert report.preflight_argv is None
    assert report.preflight_command is None
    assert report.mutation_flag is None



def test_renderer_handoff_source_identity_matches_exact_executed_bytes():
    commit, handoff_sha = MODULE._handoff_source_identity()

    assert len(commit) >= 40
    assert handoff_sha == MODULE._HANDOFF_TOOL_SHA256_AT_LOAD
    assert handoff_sha == hashlib.sha256(
        MODULE._HANDOFF_TOOL_BYTES_AT_LOAD
    ).hexdigest()


def test_renderer_handoff_is_descriptor_captured_and_executed():
    source = TOOL.read_text(encoding="utf-8")

    assert "def _capture_handoff(" in source
    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "compile(encoded" in source
    assert "exec(code, module.__dict__)" in source
    assert "_HANDOFF_TOOL_BYTES_AT_LOAD" in source
    assert "spec.loader.exec_module(module)" not in source


def test_renderer_rejects_handoff_identity_change_during_inspection(
    monkeypatch,
):
    install(monkeypatch, handoff())
    identities = iter(
        (
            ("a" * 40, "1" * 64),
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_handoff_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(ValueError, match="handoff changed during inspection"):
        MODULE.render_lifecycle_command()
