from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_mutation_freshness.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_mutation_freshness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Current(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def current(
    *,
    state="SOURCE_BOOTSTRAP_REQUIRED",
    action="BOOTSTRAP_PINNED_SOURCE",
    tool="bootstrap_phase2_isolated_source.py",
    preflight_fp="a" * 64,
    mutation_fp="b" * 64,
    tool_sha="c" * 64,
    source_commit="d" * 40,
    surface_sha="e" * 64,
    surface_files=10,
    argv=None,
    ready=True,
    rpc_called=False,
):
    if argv is None:
        argv = (
            sys.executable,
            str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
            "--destination",
            "/tmp/pinned",
            "--apply",
        )
    return Current(
        format_version=MODULE.RENDER.MUTATION_PREVIEW_FORMAT_VERSION,
        fingerprint_schema=MODULE.RENDER.MUTATION_FINGERPRINT_SCHEMA,
        reviewed_source_commit=source_commit,
        deploy_surface_sha256=surface_sha,
        deploy_surface_files=surface_files,
        state=state,
        next_action=action,
        next_tool=tool,
        preflight_succeeded=ready,
        mutation_rendered=ready,
        mutation_argv=argv if ready else None,
        mutation_command="cmd" if ready else None,
        mutation_executed=False,
        mutation_tool_sha256=tool_sha if ready else None,
        preflight_fingerprint=preflight_fp,
        mutation_fingerprint=mutation_fp if ready else None,
        read_only=True,
        rpc_called=rpc_called,
        database_write_performed=False,
        service_control_performed=False,
        daemon_reload_performed=False,
        production_tree_modified=False,
    )


def prior_payload(**overrides):
    payload = {
        "format_version": MODULE.RENDER.MUTATION_PREVIEW_FORMAT_VERSION,
        "fingerprint_schema": MODULE.RENDER.MUTATION_FINGERPRINT_SCHEMA,
        "reviewed_source_commit": "d" * 40,
        "deploy_surface_sha256": "e" * 64,
        "deploy_surface_files": 10,
        "state": "SOURCE_BOOTSTRAP_REQUIRED",
        "next_action": "BOOTSTRAP_PINNED_SOURCE",
        "next_tool": "bootstrap_phase2_isolated_source.py",
        "preflight_succeeded": True,
        "mutation_rendered": True,
        "mutation_executed": False,
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "daemon_reload_performed": False,
        "production_tree_modified": False,
        "preflight_fingerprint": "a" * 64,
        "mutation_fingerprint": "b" * 64,
        "mutation_tool_sha256": "c" * 64,
        "mutation_argv": [
            sys.executable,
            str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
            "--destination",
            "/tmp/pinned",
            "--apply",
        ],
    }
    payload.update(overrides)
    return payload


def write_preview(tmp_path, payload=None):
    path = tmp_path / "preview.json"
    path.write_text(
        json.dumps(prior_payload() if payload is None else payload),
        encoding="utf-8",
    )
    return path


def install(monkeypatch, report):
    monkeypatch.setattr(
        MODULE.RENDER,
        "render_reviewed_mutation_command",
        lambda **kwargs: report,
    )


def test_freshness_accepts_exact_current_preview(tmp_path, monkeypatch):
    path = write_preview(tmp_path)
    install(monkeypatch, current())

    report = MODULE.check_mutation_preview_freshness(preview_path=path)

    assert report.status == "CURRENT"
    assert report.preview_current is True
    assert report.state_matches is True
    assert report.action_matches is True
    assert report.tool_matches is True
    assert report.mutation_argv_matches is True
    assert report.mutation_tool_sha256_matches is True
    assert report.preflight_fingerprint_matches is True
    assert report.mutation_fingerprint_matches is True
    assert report.mutation_executed is False


def test_freshness_rejects_changed_preflight_fingerprint(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    install(monkeypatch, current(preflight_fp="c" * 64))

    report = MODULE.check_mutation_preview_freshness(preview_path=path)

    assert report.status == "STALE"
    assert report.preview_current is False
    assert report.preflight_fingerprint_matches is False


def test_freshness_rejects_changed_mutation_argv(tmp_path, monkeypatch):
    path = write_preview(tmp_path)
    install(
        monkeypatch,
        current(
            argv=(
                sys.executable,
                str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
                "--destination",
                "/tmp/other",
                "--apply",
            )
        ),
    )

    report = MODULE.check_mutation_preview_freshness(preview_path=path)

    assert report.status == "STALE"
    assert report.mutation_argv_matches is False


def test_freshness_reports_current_preflight_not_ready(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    install(monkeypatch, current(ready=False))

    report = MODULE.check_mutation_preview_freshness(preview_path=path)

    assert report.status == "CURRENT_PREFLIGHT_NOT_MUTATION_READY"
    assert report.preview_current is False
    assert report.current_preflight_succeeded is False
    assert report.current_mutation_rendered is False


def test_freshness_rejects_symlinked_prior_preview(tmp_path, monkeypatch):
    target = write_preview(tmp_path)
    link = tmp_path / "preview-link.json"
    link.symlink_to(target)
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="must not be a symlink"):
        MODULE.check_mutation_preview_freshness(preview_path=link)


def test_freshness_rejects_invalid_prior_fingerprint(tmp_path, monkeypatch):
    path = write_preview(
        tmp_path,
        prior_payload(preflight_fingerprint="not-a-fingerprint"),
    )
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="preflight fingerprint is invalid"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_rejects_prior_that_reports_execution(tmp_path, monkeypatch):
    path = write_preview(
        tmp_path,
        prior_payload(mutation_executed=True),
    )
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="reports mutation execution"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_rejects_current_boundary_crossing(tmp_path, monkeypatch):
    path = write_preview(tmp_path)
    install(monkeypatch, current(rpc_called=True))

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_rejects_oversized_preview(tmp_path, monkeypatch):
    path = tmp_path / "preview.json"
    path.write_bytes(b"{" + b" " * MODULE._MAX_PREVIEW_BYTES + b"}")
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="size is invalid"):
        MODULE.check_mutation_preview_freshness(preview_path=path)



def test_freshness_rejects_unsupported_prior_format_version(
    tmp_path,
    monkeypatch,
):
    path = write_preview(
        tmp_path,
        prior_payload(format_version=999),
    )
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="format version is unsupported"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_rejects_unsupported_prior_fingerprint_schema(
    tmp_path,
    monkeypatch,
):
    path = write_preview(
        tmp_path,
        prior_payload(fingerprint_schema="UNKNOWN"),
    )
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="fingerprint schema is unsupported"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_rejects_prior_boundary_crossing(tmp_path, monkeypatch):
    path = write_preview(
        tmp_path,
        prior_payload(rpc_called=True),
    )
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_rejects_current_format_contract_mismatch(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    report = current()
    report.format_version = 999
    install(monkeypatch, report)

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_report_exposes_current_format_contract(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    install(monkeypatch, current())

    report = MODULE.check_mutation_preview_freshness(preview_path=path)

    assert report.format_version == 2
    assert report.fingerprint_schema == "PHASE2_MUTATION_PREVIEW_V2"
    assert report.reviewed_source_commit_matches is True
    assert report.deploy_surface_sha256_matches is True
    assert report.deploy_surface_files_match is True



def test_freshness_rejects_changed_mutation_tool_bytes(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    install(monkeypatch, current(tool_sha="d" * 64))

    report = MODULE.check_mutation_preview_freshness(preview_path=path)

    assert report.status == "STALE"
    assert report.preview_current is False
    assert report.mutation_tool_sha256_matches is False


def test_freshness_rejects_invalid_prior_mutation_tool_sha(
    tmp_path,
    monkeypatch,
):
    path = write_preview(
        tmp_path,
        prior_payload(mutation_tool_sha256="not-a-sha"),
    )
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="mutation tool SHA256 is invalid"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_rejects_rendered_current_without_tool_sha(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    install(monkeypatch, current(tool_sha=None))

    with pytest.raises(ValueError, match="crossed the read-only boundary"):
        MODULE.check_mutation_preview_freshness(preview_path=path)



def test_freshness_rejects_changed_reviewed_source_commit(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    install(monkeypatch, current(source_commit="f" * 40))

    report = MODULE.check_mutation_preview_freshness(preview_path=path)

    assert report.status == "STALE"
    assert report.preview_current is False
    assert report.reviewed_source_commit_matches is False


def test_freshness_rejects_changed_deploy_surface_digest(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    install(monkeypatch, current(surface_sha="f" * 64))

    report = MODULE.check_mutation_preview_freshness(preview_path=path)

    assert report.status == "STALE"
    assert report.preview_current is False
    assert report.deploy_surface_sha256_matches is False


def test_freshness_rejects_invalid_prior_source_identity(
    tmp_path,
    monkeypatch,
):
    path = write_preview(
        tmp_path,
        prior_payload(reviewed_source_commit="bad"),
    )
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="reviewed source commit is invalid"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_rejects_invalid_prior_surface_file_count(
    tmp_path,
    monkeypatch,
):
    path = write_preview(
        tmp_path,
        prior_payload(deploy_surface_files=0),
    )
    install(monkeypatch, current())

    with pytest.raises(ValueError, match="surface file count is invalid"):
        MODULE.check_mutation_preview_freshness(preview_path=path)



def test_freshness_renderer_source_matches_exact_executed_bytes():
    commit, renderer_sha = MODULE._renderer_source_identity()

    assert len(commit) >= 40
    assert renderer_sha == MODULE._RENDER_TOOL_SHA256_AT_LOAD
    assert renderer_sha == hashlib.sha256(
        MODULE._RENDER_TOOL_BYTES_AT_LOAD
    ).hexdigest()


def test_freshness_never_rereads_loaded_renderer_tool_bytes(monkeypatch):
    real_read_bytes = Path.read_bytes

    def reject_renderer_reread(path):
        if path.resolve() == MODULE._RENDER_TOOL_PATH_AT_LOAD:
            raise AssertionError(
                "executed freshness renderer path must not be reread"
            )
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_renderer_reread)

    commit, renderer_sha = MODULE._renderer_source_identity()

    assert len(commit) >= 40
    assert renderer_sha == MODULE._RENDER_TOOL_SHA256_AT_LOAD


def test_freshness_renderer_is_executed_from_descriptor_captured_bytes():
    source = TOOL.read_text(encoding="utf-8")

    assert "def _capture_regular_file(" in source
    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "compile(encoded" in source
    assert "exec(code, module.__dict__)" in source
    assert "_RENDER_TOOL_BYTES_AT_LOAD" in source
    assert "RENDER_TOOL.read_bytes()" not in source


def test_freshness_rejects_renderer_change_during_render(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    install(monkeypatch, current())
    identities = iter(
        (
            ("a" * 40, "1" * 64),
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_renderer_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(ValueError, match="renderer changed during render"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_rejects_preview_path_replacement_during_render(
    tmp_path,
    monkeypatch,
):
    path = write_preview(tmp_path)
    report = current()

    def replace_preview(**kwargs):
        path.unlink()
        path.write_text(
            json.dumps(prior_payload(state="REPLACED")),
            encoding="utf-8",
        )
        return report

    monkeypatch.setattr(
        MODULE.RENDER,
        "render_reviewed_mutation_command",
        replace_preview,
    )

    with pytest.raises(ValueError, match="preview path changed after capture"):
        MODULE.check_mutation_preview_freshness(preview_path=path)


def test_freshness_parses_preview_from_captured_bytes_not_path_read():
    source = TOOL.read_text(encoding="utf-8")

    assert "json.loads(encoded.decode(" in source
    assert "path.read_text(" not in source
