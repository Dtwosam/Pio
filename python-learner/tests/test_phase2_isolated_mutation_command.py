from __future__ import annotations

import hashlib
import importlib.util
import shlex
import subprocess
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
    tool = Path(rendered.mutation_argv[1])
    assert rendered.mutation_tool_sha256 == hashlib.sha256(
        tool.read_bytes()
    ).hexdigest()


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
    assert rendered.mutation_tool_sha256 is None
    assert rendered.mutation_rendered is False


def test_fingerprint_uses_canonical_json_ordering():
    left = MODULE._fingerprint(
        "preflight",
        {"b": 2, "a": {"y": 4, "x": 3}},
    )
    right = MODULE._fingerprint(
        "preflight",
        {"a": {"x": 3, "y": 4}, "b": 2},
    )

    assert left == right


def test_renderer_emits_versioned_fingerprint_contract(monkeypatch):
    install(monkeypatch, preflight())

    rendered = MODULE.render_reviewed_mutation_command()

    assert rendered.format_version == MODULE.MUTATION_PREVIEW_FORMAT_VERSION == 2
    assert rendered.fingerprint_schema == MODULE.MUTATION_FINGERPRINT_SCHEMA
    assert rendered.fingerprint_schema == "PHASE2_MUTATION_PREVIEW_V2"
    assert rendered.reviewed_source_commit
    assert len(rendered.deploy_surface_sha256) == 64
    assert rendered.deploy_surface_files > 0


def test_fingerprint_kind_is_domain_separated():
    payload = {"same": "payload"}

    preflight = MODULE._fingerprint("preflight", payload)
    mutation = MODULE._fingerprint("mutation", payload)

    assert preflight != mutation



def test_mutation_fingerprint_changes_when_reviewed_tool_bytes_change(
    monkeypatch,
):
    report = preflight()
    install(monkeypatch, report)

    monkeypatch.setattr(
        MODULE,
        "_mutation_tool_sha256",
        lambda _argv: "1" * 64,
    )
    first = MODULE.render_reviewed_mutation_command()

    monkeypatch.setattr(
        MODULE,
        "_mutation_tool_sha256",
        lambda _argv: "2" * 64,
    )
    second = MODULE.render_reviewed_mutation_command()

    assert first.preflight_fingerprint == second.preflight_fingerprint
    assert first.mutation_tool_sha256 != second.mutation_tool_sha256
    assert first.mutation_fingerprint != second.mutation_fingerprint


def test_mutation_tool_digest_rejects_symlink(tmp_path):
    target = tmp_path / "tool.py"
    target.write_text("print('safe')\n", encoding="utf-8")
    link = tmp_path / "tool-link.py"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="missing or symlinked"):
        MODULE._mutation_tool_sha256(
            (sys.executable, str(link), "--apply")
        )



def _deploy_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Pio Tests"],
        cwd=root,
        check=True,
    )
    tool = root / "deploy" / "tools" / "tool.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("VALUE = 1\n", encoding="utf-8")
    unit = root / "deploy" / "systemd" / "pio.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nType=oneshot\n", encoding="utf-8")
    subprocess.run(["git", "add", "deploy"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "deploy surface"],
        cwd=root,
        check=True,
    )
    return root, tool


def test_deploy_surface_identity_ignores_untracked_runtime_cache(tmp_path):
    root, _tool = _deploy_repo(tmp_path)

    first = MODULE._deploy_surface_identity(root)
    cache = root / "deploy" / "tools" / "__pycache__"
    cache.mkdir()
    (cache / "tool.cpython.pyc").write_bytes(b"runtime-cache")
    second = MODULE._deploy_surface_identity(root)

    assert first == second
    assert first[2] == 2


def test_deploy_surface_identity_rejects_tracked_local_change(tmp_path):
    root, tool = _deploy_repo(tmp_path)
    tool.write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="tracked local changes"):
        MODULE._deploy_surface_identity(root)
