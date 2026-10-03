from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import stat
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/save_phase2_isolated_mutation_preview.py"
SPEC = importlib.util.spec_from_file_location(
    "save_phase2_isolated_mutation_preview",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Preview(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def preview(*, ready=True, rpc_called=False):
    argv = (
        sys.executable,
        str(ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"),
        "--destination",
        "/tmp/pio-phase2-build",
        "--apply",
    )
    return Preview(
        format_version=MODULE.RENDER.MUTATION_PREVIEW_FORMAT_VERSION,
        fingerprint_schema=MODULE.RENDER.MUTATION_FINGERPRINT_SCHEMA,
        reviewed_source_commit="d" * 40,
        deploy_surface_sha256="e" * 64,
        deploy_surface_files=10,
        state="SOURCE_BOOTSTRAP_REQUIRED",
        next_action="BOOTSTRAP_PINNED_SOURCE",
        next_tool="bootstrap_phase2_isolated_source.py",
        preflight_executed=True,
        preflight_exit_code=0 if ready else 2,
        preflight_json_valid=True,
        preflight_succeeded=ready,
        mutation_flag="--apply",
        mutation_argv=argv if ready else None,
        mutation_command="cmd" if ready else None,
        mutation_rendered=ready,
        mutation_executed=False,
        mutation_tool_sha256="c" * 64 if ready else None,
        preflight_fingerprint="a" * 64,
        mutation_fingerprint="b" * 64 if ready else None,
        preflight={"status": "READY_CREATE"},
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
        MODULE.RENDER,
        "render_reviewed_mutation_command",
        lambda **kwargs: report,
    )


def test_saver_writes_exact_preview_atomically_with_private_mode(
    tmp_path,
    monkeypatch,
):
    report = preview()
    install(monkeypatch, report)
    output = tmp_path / "preview.json"

    saved = MODULE.save_mutation_preview(output_path=output)

    assert saved.preview_saved is True
    assert saved.artifact_write_performed is True
    assert saved.read_only_preflight is True
    assert saved.mutation_executed is False
    assert saved.replaced_existing is False
    assert saved.file_mode == "0600"
    assert saved.reviewed_source_commit == "d" * 40
    assert saved.deploy_surface_sha256 == "e" * 64
    assert saved.deploy_surface_files == 10
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    expected = json.loads(json.dumps(report.to_record()))
    assert json.loads(output.read_text(encoding="utf-8")) == expected
    assert not list(tmp_path.glob(".preview.json.*.tmp"))


def test_saver_refuses_existing_preview_without_replace(tmp_path, monkeypatch):
    install(monkeypatch, preview())
    output = tmp_path / "preview.json"
    output.write_text('{"old": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        MODULE.save_mutation_preview(output_path=output)

    assert json.loads(output.read_text(encoding="utf-8")) == {"old": True}


def test_saver_replaces_existing_preview_only_when_explicit(
    tmp_path,
    monkeypatch,
):
    report = preview()
    install(monkeypatch, report)
    output = tmp_path / "preview.json"
    output.write_text('{"old": true}\n', encoding="utf-8")
    output.chmod(0o644)

    saved = MODULE.save_mutation_preview(
        output_path=output,
        replace=True,
    )

    assert saved.replaced_existing is True
    assert saved.file_mode == "0600"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    expected = json.loads(json.dumps(report.to_record()))
    assert json.loads(output.read_text(encoding="utf-8")) == expected


def test_saver_refuses_symlink_output(tmp_path, monkeypatch):
    install(monkeypatch, preview())
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "preview.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        MODULE.save_mutation_preview(output_path=link)

    assert target.read_text(encoding="utf-8") == "{}\n"


def test_saver_refuses_non_mutation_ready_preview(tmp_path, monkeypatch):
    install(monkeypatch, preview(ready=False))
    output = tmp_path / "preview.json"

    with pytest.raises(ValueError, match="not safe and mutation-ready"):
        MODULE.save_mutation_preview(output_path=output)

    assert not output.exists()


def test_saver_refuses_preview_that_crossed_read_only_boundary(
    tmp_path,
    monkeypatch,
):
    install(monkeypatch, preview(rpc_called=True))
    output = tmp_path / "preview.json"

    with pytest.raises(ValueError, match="not safe and mutation-ready"):
        MODULE.save_mutation_preview(output_path=output)

    assert not output.exists()


@pytest.mark.parametrize(
    "path",
    (
        "/opt/pio/preview.json",
        "/opt/pio/data/preview.json",
        "/opt/pio-phase2-runtime/preview.json",
        "/etc/pio/preview.json",
        "/etc/systemd/system/preview.json",
    ),
)
def test_saver_refuses_protected_production_paths(path):
    with pytest.raises(ValueError, match="protected production path"):
        MODULE._output_path(path)



def test_saver_rejects_non_hex_digest(tmp_path, monkeypatch):
    report = preview()
    report.mutation_tool_sha256 = "z" * 64
    install(monkeypatch, report)
    output = tmp_path / "preview.json"

    with pytest.raises(ValueError, match="not safe and mutation-ready"):
        MODULE.save_mutation_preview(output_path=output)

    assert not output.exists()



def test_saver_rejects_invalid_deploy_surface_identity(tmp_path, monkeypatch):
    report = preview()
    report.deploy_surface_sha256 = "invalid"
    install(monkeypatch, report)
    output = tmp_path / "preview.json"

    with pytest.raises(ValueError, match="not safe and mutation-ready"):
        MODULE.save_mutation_preview(output_path=output)

    assert not output.exists()



def test_saver_default_publish_race_never_overwrites_preview_destination(
    tmp_path,
    monkeypatch,
):
    install(monkeypatch, preview())
    output = tmp_path / "preview.json"
    competitor = b'{"concurrent": true}\n'
    real_link = MODULE.os.link
    raced = False

    def competing_publish(source, destination, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            destination = Path(destination)
            destination.write_bytes(competitor)
            destination.chmod(0o600)
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(MODULE.os, "link", competing_publish)

    with pytest.raises(
        ValueError,
        match="output appeared before publish",
    ):
        MODULE.save_mutation_preview(output_path=output)

    assert output.read_bytes() == competitor
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".preview.json.*.tmp"))


def test_saver_explicit_replace_does_not_use_no_clobber_link(
    tmp_path,
    monkeypatch,
):
    report = preview()
    install(monkeypatch, report)
    output = tmp_path / "preview.json"
    output.write_text('{"old": true}\n', encoding="utf-8")

    def forbidden_link(*args, **kwargs):
        raise AssertionError("explicit replace must not use no-clobber link")

    monkeypatch.setattr(MODULE.os, "link", forbidden_link)

    saved = MODULE.save_mutation_preview(
        output_path=output,
        replace=True,
    )

    assert saved.replaced_existing is True
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(
        json.dumps(report.to_record())
    )



def test_saver_digest_comes_from_exact_preview_payload_not_path_reread(
    tmp_path,
    monkeypatch,
):
    report = preview()
    install(monkeypatch, report)
    output = tmp_path / "preview.json"

    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(path):
        if path == output:
            raise AssertionError(
                "published preview destination must not be reopened for digesting"
            )
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    saved = MODULE.save_mutation_preview(output_path=output)

    payload = output.read_text(encoding="utf-8").encode("utf-8")
    assert saved.preview_sha256 == MODULE.hashlib.sha256(payload).hexdigest()
    assert saved.bytes_written == len(payload)
