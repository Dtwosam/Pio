from __future__ import annotations

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
        state=state,
        next_action=action,
        next_tool=tool,
        preflight_succeeded=ready,
        mutation_rendered=ready,
        mutation_argv=argv if ready else None,
        mutation_command="cmd" if ready else None,
        mutation_executed=False,
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

    with pytest.raises(ValueError, match="too large"):
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

    assert report.format_version == 1
    assert report.fingerprint_schema == "PHASE2_MUTATION_PREVIEW_V1"
