from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/save_phase2_mutation_post_audit_catalog.py"
SPEC = importlib.util.spec_from_file_location(
    "save_phase2_mutation_post_audit_catalog",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Catalog(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def good_catalog(tmp_path: Path, **overrides):
    values = {
        "artifact_directory": str(tmp_path / "artifacts"),
        "pattern": "*.post-audit.json",
        "artifacts_seen": 2,
        "artifacts_verified": 2,
        "artifacts_failed": 0,
        "duplicate_artifact_hashes": 0,
        "duplicate_receipt_hashes": 0,
        "entries": (
            {
                "artifact_path": str(tmp_path / "artifacts" / "a.post-audit.json"),
                "artifact_sha256": "1" * 64,
                "verification_status": "VERIFIED",
                "execution_receipt_sha256": "2" * 64,
            },
            {
                "artifact_path": str(tmp_path / "artifacts" / "b.post-audit.json"),
                "artifact_sha256": "3" * 64,
                "verification_status": "VERIFIED",
                "execution_receipt_sha256": "4" * 64,
            },
        ),
        "catalog_stable_during_scan": True,
        "all_verified": True,
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
    }
    values.update(overrides)
    return Catalog(**values)


def test_save_catalog_snapshot_is_private_atomic_and_bound_to_source(
    tmp_path,
    monkeypatch,
):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    catalog = good_catalog(tmp_path)
    monkeypatch.setattr(
        MODULE.CATALOG,
        "build_phase2_post_audit_catalog",
        lambda **kwargs: catalog,
    )
    output = tmp_path / "catalog.json"

    report = MODULE.save_phase2_post_audit_catalog_snapshot(
        artifact_directory=artifact_dir,
        output_path=output,
    )

    assert report.artifact_write_performed is True
    assert report.artifacts_seen == 2
    assert report.artifacts_verified == 2
    assert report.file_mode == "0600"
    assert report.read_only_catalog is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["format_version"] == 1
    assert payload["artifact_type"] == (
        "PHASE2_MUTATION_POST_AUDIT_CATALOG_SNAPSHOT_V1"
    )
    assert payload["catalog"]["all_verified"] is True
    assert payload["catalog"]["catalog_stable_during_scan"] is True
    assert len(payload["catalog_source_commit"]) >= 40
    assert len(payload["catalog_tool_sha256"]) == 64
    assert len(payload["catalog_payload_sha256"]) == 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("artifacts_seen", 0),
        ("artifacts_verified", 1),
        ("artifacts_failed", 1),
        ("duplicate_artifact_hashes", 1),
        ("duplicate_receipt_hashes", 1),
        ("catalog_stable_during_scan", False),
        ("all_verified", False),
        ("read_only", False),
        ("rpc_called", True),
        ("database_write_performed", True),
        ("service_control_performed", True),
        ("mutation_executed", True),
    ],
)
def test_save_catalog_snapshot_fails_closed_on_invalid_catalog(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    catalog = good_catalog(tmp_path, **{field: value})
    monkeypatch.setattr(
        MODULE.CATALOG,
        "build_phase2_post_audit_catalog",
        lambda **kwargs: catalog,
    )

    with pytest.raises(ValueError, match="not uniquely and fully verified"):
        MODULE.save_phase2_post_audit_catalog_snapshot(
            artifact_directory=artifact_dir,
            output_path=tmp_path / "catalog.json",
        )


def test_save_catalog_snapshot_refuses_overwrite(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setattr(
        MODULE.CATALOG,
        "build_phase2_post_audit_catalog",
        lambda **kwargs: good_catalog(tmp_path),
    )
    output = tmp_path / "catalog.json"
    output.write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        MODULE.save_phase2_post_audit_catalog_snapshot(
            artifact_directory=artifact_dir,
            output_path=output,
        )


def test_save_catalog_snapshot_rejects_sensitive_catalog_text(
    tmp_path,
    monkeypatch,
):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    catalog = good_catalog(
        tmp_path,
        artifact_directory="/tmp/api-key=secret",
    )
    monkeypatch.setattr(
        MODULE.CATALOG,
        "build_phase2_post_audit_catalog",
        lambda **kwargs: catalog,
    )

    with pytest.raises(ValueError, match="sensitive text"):
        MODULE.save_phase2_post_audit_catalog_snapshot(
            artifact_directory=artifact_dir,
            output_path=tmp_path / "catalog.json",
        )


def test_catalog_source_identity_matches_reviewed_git_head():
    commit, tool_sha = MODULE._catalog_source_identity()

    assert len(commit) >= 40
    assert len(tool_sha) == 64
    assert tool_sha == MODULE.hashlib.sha256(
        MODULE.CATALOG_TOOL.read_bytes()
    ).hexdigest()



def test_save_catalog_snapshot_rejects_source_change_during_build(
    tmp_path,
    monkeypatch,
):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setattr(
        MODULE.CATALOG,
        "build_phase2_post_audit_catalog",
        lambda **kwargs: good_catalog(tmp_path),
    )
    identities = iter(
        (
            ("a" * 40, "1" * 64),
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_catalog_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(ValueError, match="source changed during snapshot build"):
        MODULE.save_phase2_post_audit_catalog_snapshot(
            artifact_directory=artifact_dir,
            output_path=tmp_path / "catalog.json",
        )

    assert not (tmp_path / "catalog.json").exists()



def test_save_catalog_snapshot_rejects_self_including_output(
    tmp_path,
    monkeypatch,
):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setattr(
        MODULE.CATALOG,
        "build_phase2_post_audit_catalog",
        lambda **kwargs: good_catalog(tmp_path),
    )

    with pytest.raises(ValueError, match="must not match the catalog pattern"):
        MODULE.save_phase2_post_audit_catalog_snapshot(
            artifact_directory=artifact_dir,
            output_path=artifact_dir / "catalog.post-audit.json",
        )



def test_save_catalog_snapshot_publish_race_never_overwrites_destination(
    tmp_path,
    monkeypatch,
):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setattr(
        MODULE.CATALOG,
        "build_phase2_post_audit_catalog",
        lambda **kwargs: good_catalog(tmp_path),
    )

    output = tmp_path / "catalog.json"
    competitor = b"preexisting concurrent catalog evidence\n"
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
        MODULE.save_phase2_post_audit_catalog_snapshot(
            artifact_directory=artifact_dir,
            output_path=output,
        )

    assert output.read_bytes() == competitor
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".catalog.json.*.tmp"))



def test_save_catalog_snapshot_digest_comes_from_exact_payload_not_path_reread(
    tmp_path,
    monkeypatch,
):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setattr(
        MODULE.CATALOG,
        "build_phase2_post_audit_catalog",
        lambda **kwargs: good_catalog(tmp_path),
    )
    output = tmp_path / "catalog.json"

    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(path):
        if path == output:
            raise AssertionError(
                "published catalog destination must not be reopened for digesting"
            )
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    saved = MODULE.save_phase2_post_audit_catalog_snapshot(
        artifact_directory=artifact_dir,
        output_path=output,
    )

    payload = output.read_text(encoding="utf-8").encode("utf-8")
    assert saved.artifact_sha256 == MODULE.hashlib.sha256(payload).hexdigest()
    assert saved.bytes_written == len(payload)
