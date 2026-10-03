from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
BUILD_TOOL = (
    ROOT / "deploy/tools/build_phase2_mutation_post_audit_handoff_bundle.py"
)
VERIFY_TOOL = (
    ROOT / "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle.py"
)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUILD = load(BUILD_TOOL, "build_phase2_portable_handoff_bundle_test")
VERIFY = load(VERIFY_TOOL, "verify_phase2_portable_handoff_bundle_test")


class Report(SimpleNamespace):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_private_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def inputs(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    first = artifacts / "a.post-audit.json"
    second = artifacts / "b.post-audit.json"
    write_private_json(first, {"kind": "post-audit", "n": 1})
    write_private_json(second, {"kind": "post-audit", "n": 2})

    hashes = sorted((sha256(first), sha256(second)))
    catalog = tmp_path / "catalog.json"
    write_private_json(
        catalog,
        {
            "catalog": {
                "entries": [
                    {"artifact_sha256": digest}
                    for digest in hashes
                ]
            }
        },
    )
    catalog_sha = sha256(catalog)

    handoff = tmp_path / "handoff.json"
    write_private_json(
        handoff,
        {
            "handoff": {
                "snapshot_sha256": catalog_sha,
                "historical_artifacts_seen": 2,
            }
        },
    )
    return handoff, catalog, artifacts, hashes


def valid_boundary(**overrides):
    values = {
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
        "authorizes_next_action": False,
    }
    values.update(overrides)
    return values


def install_build_verifiers(monkeypatch):
    def snapshot_sha(kwargs):
        return hashlib.sha256(
            Path(kwargs["snapshot_path"]).read_bytes()
        ).hexdigest()

    monkeypatch.setattr(
        BUILD.HANDOFF_VERIFY,
        "verify_phase2_post_audit_catalog_handoff_snapshot",
        lambda **kwargs: Report(
            handoff_snapshot_verified=True,
            snapshot_sha256=snapshot_sha(kwargs),
            **valid_boundary(),
        ),
    )
    monkeypatch.setattr(
        BUILD.CATALOG_VERIFY,
        "verify_phase2_post_audit_catalog_snapshot",
        lambda **kwargs: Report(
            snapshot_verified=True,
            snapshot_sha256=snapshot_sha(kwargs),
            artifacts_seen=2,
            **valid_boundary(),
        ),
    )
    monkeypatch.setattr(
        BUILD.FRESH_VERIFY,
        "freshly_reverify_phase2_post_audit_catalog",
        lambda **kwargs: Report(
            fresh_reverification_verified=True,
            snapshot_sha256=snapshot_sha(kwargs),
            **valid_boundary(),
        ),
    )


def install_verify_verifiers(monkeypatch):
    def snapshot_sha(kwargs):
        return hashlib.sha256(
            Path(kwargs["snapshot_path"]).read_bytes()
        ).hexdigest()

    monkeypatch.setattr(
        VERIFY.HANDOFF_VERIFY,
        "verify_phase2_post_audit_catalog_handoff_snapshot",
        lambda **kwargs: Report(
            handoff_snapshot_verified=True,
            snapshot_sha256=snapshot_sha(kwargs),
            **valid_boundary(),
        ),
    )
    monkeypatch.setattr(
        VERIFY.CATALOG_VERIFY,
        "verify_phase2_post_audit_catalog_snapshot",
        lambda **kwargs: Report(
            snapshot_verified=True,
            snapshot_sha256=snapshot_sha(kwargs),
            **valid_boundary(),
        ),
    )
    monkeypatch.setattr(
        VERIFY.FRESH_VERIFY,
        "freshly_reverify_phase2_post_audit_catalog",
        lambda **kwargs: Report(
            fresh_reverification_verified=True,
            snapshot_sha256=snapshot_sha(kwargs),
            **valid_boundary(),
        ),
    )


def build_bundle(tmp_path, monkeypatch):
    handoff, catalog, artifacts, hashes = inputs(tmp_path)
    install_build_verifiers(monkeypatch)
    output = tmp_path / "bundle"
    report = BUILD.build_phase2_portable_handoff_bundle(
        handoff_snapshot_path=handoff,
        catalog_snapshot_path=catalog,
        artifact_directory=artifacts,
        output_directory=output,
        repository_root=ROOT,
    )
    return output, report, hashes


def test_builder_publishes_private_non_authorizing_bundle(tmp_path, monkeypatch):
    output, report, hashes = build_bundle(tmp_path, monkeypatch)

    assert report.bundle_write_performed is True
    assert report.post_audits_copied == 2
    assert report.evidence_lineage_verified is True
    assert report.fresh_reverification_verified is True
    assert report.historical_handoff_only is True
    assert report.current_lifecycle_rechecked is False
    assert report.authorizes_next_action is False
    assert report.requires_fresh_separate_mutation_authorization is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False
    assert stat.S_IMODE(output.stat().st_mode) == 0o700

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_type"] == (
        "PHASE2_MUTATION_POST_AUDIT_HANDOFF_BUNDLE_V1"
    )
    assert manifest["authorizes_next_action"] is False
    assert manifest["requires_fresh_separate_mutation_authorization"] is True
    assert [entry["sha256"] for entry in manifest["post_audits"]] == hashes

    for name in (
        "manifest.json",
        "handoff.snapshot.json",
        "catalog.snapshot.json",
    ):
        assert stat.S_IMODE((output / name).stat().st_mode) == 0o600
    assert stat.S_IMODE((output / "post-audits").stat().st_mode) == 0o700


def test_verifier_accepts_exact_bundle_and_stays_read_only(tmp_path, monkeypatch):
    output, built, _ = build_bundle(tmp_path, monkeypatch)
    install_verify_verifiers(monkeypatch)

    report = VERIFY.verify_phase2_portable_handoff_bundle(
        bundle_directory=output,
        repository_root=ROOT,
    )

    assert report.bundle_sha256 == built.bundle_sha256
    assert report.bundle_verified is True
    assert report.handoff_snapshot_verified is True
    assert report.catalog_snapshot_verified is True
    assert report.post_audits_expected == 2
    assert report.post_audits_verified == 2
    assert report.artifact_hash_set_matches is True
    assert report.current_lifecycle_rechecked is False
    assert report.authorizes_next_action is False
    assert report.requires_fresh_separate_mutation_authorization is True
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False


def test_verifier_detects_post_audit_tamper(tmp_path, monkeypatch):
    output, _, hashes = build_bundle(tmp_path, monkeypatch)
    install_verify_verifiers(monkeypatch)
    target = output / "post-audits" / f"{hashes[0]}.post-audit.json"
    target.write_text('{"tampered":true}\n', encoding="utf-8")
    target.chmod(0o600)

    with pytest.raises(ValueError, match="post-audit hash mismatch"):
        VERIFY.verify_phase2_portable_handoff_bundle(
            bundle_directory=output,
            repository_root=ROOT,
        )


def test_verifier_rejects_unmanifested_file(tmp_path, monkeypatch):
    output, _, _ = build_bundle(tmp_path, monkeypatch)
    install_verify_verifiers(monkeypatch)
    extra = output / "unexpected.txt"
    extra.write_text("unexpected", encoding="utf-8")
    extra.chmod(0o600)

    with pytest.raises(ValueError, match="unexpected top-level entries"):
        VERIFY.verify_phase2_portable_handoff_bundle(
            bundle_directory=output,
            repository_root=ROOT,
        )


def test_builder_rejects_handoff_catalog_linkage_mismatch(tmp_path, monkeypatch):
    handoff, catalog, artifacts, _ = inputs(tmp_path)
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    payload["handoff"]["snapshot_sha256"] = "0" * 64
    write_private_json(handoff, payload)
    install_build_verifiers(monkeypatch)

    with pytest.raises(ValueError, match="does not reference"):
        BUILD.build_phase2_portable_handoff_bundle(
            handoff_snapshot_path=handoff,
            catalog_snapshot_path=catalog,
            artifact_directory=artifacts,
            output_directory=tmp_path / "bundle",
            repository_root=ROOT,
        )


def test_builder_rejects_overwrite(tmp_path, monkeypatch):
    handoff, catalog, artifacts, _ = inputs(tmp_path)
    install_build_verifiers(monkeypatch)
    output = tmp_path / "bundle"
    output.mkdir()

    with pytest.raises(ValueError, match="already exists"):
        BUILD.build_phase2_portable_handoff_bundle(
            handoff_snapshot_path=handoff,
            catalog_snapshot_path=catalog,
            artifact_directory=artifacts,
            output_directory=output,
            repository_root=ROOT,
        )


def test_builder_rejects_protected_output(tmp_path, monkeypatch):
    handoff, catalog, artifacts, _ = inputs(tmp_path)
    install_build_verifiers(monkeypatch)

    with pytest.raises(ValueError, match="protected production path"):
        BUILD.build_phase2_portable_handoff_bundle(
            handoff_snapshot_path=handoff,
            catalog_snapshot_path=catalog,
            artifact_directory=artifacts,
            output_directory="/opt/pio/data/phase2-handoff-bundle",
            repository_root=ROOT,
        )



def test_directory_publish_noreplace_preserves_source_inode(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "published"
    source.mkdir(mode=0o700)
    marker = source / "marker.txt"
    marker.write_text("complete", encoding="utf-8")
    source_stat = source.stat()

    BUILD._publish_directory_noreplace(source, destination)

    assert not source.exists()
    assert destination.is_dir()
    published_stat = destination.stat()
    assert published_stat.st_dev == source_stat.st_dev
    assert published_stat.st_ino == source_stat.st_ino
    assert stat.S_IMODE(published_stat.st_mode) == 0o700
    assert (destination / "marker.txt").read_text(encoding="utf-8") == "complete"


def test_builder_does_not_clobber_destination_created_during_publish(
    tmp_path,
    monkeypatch,
):
    handoff, catalog, artifacts, _ = inputs(tmp_path)
    install_build_verifiers(monkeypatch)
    output = tmp_path / "bundle"
    original_publish = BUILD._publish_directory_noreplace

    def raced_publish(source, destination):
        destination.mkdir(mode=0o700)
        sentinel = destination / "sentinel.txt"
        sentinel.write_text("concurrent-owner", encoding="utf-8")
        sentinel.chmod(0o600)
        return original_publish(source, destination)

    monkeypatch.setattr(
        BUILD,
        "_publish_directory_noreplace",
        raced_publish,
    )

    with pytest.raises(ValueError, match="appeared before publish"):
        BUILD.build_phase2_portable_handoff_bundle(
            handoff_snapshot_path=handoff,
            catalog_snapshot_path=catalog,
            artifact_directory=artifacts,
            output_directory=output,
            repository_root=ROOT,
        )

    assert output.is_dir()
    assert (output / "sentinel.txt").read_text(encoding="utf-8") == (
        "concurrent-owner"
    )
    assert {item.name for item in output.iterdir()} == {"sentinel.txt"}



def test_verifier_rejects_child_snapshot_identity_drift(
    tmp_path,
    monkeypatch,
):
    output, _, _ = build_bundle(tmp_path, monkeypatch)
    install_verify_verifiers(monkeypatch)

    original = (
        VERIFY.HANDOFF_VERIFY
        .verify_phase2_post_audit_catalog_handoff_snapshot
    )

    def mismatched_handoff(**kwargs):
        report = original(**kwargs)
        return Report(
            **{
                **report.__dict__,
                "snapshot_sha256": "0" * 64,
            }
        )

    monkeypatch.setattr(
        VERIFY.HANDOFF_VERIFY,
        "verify_phase2_post_audit_catalog_handoff_snapshot",
        mismatched_handoff,
    )

    with pytest.raises(
        ValueError,
        match="handoff snapshot changed during verification",
    ):
        VERIFY.verify_phase2_portable_handoff_bundle(
            bundle_directory=output,
            repository_root=ROOT,
        )


def test_verifier_rejects_snapshot_path_swap_during_nested_verification(
    tmp_path,
    monkeypatch,
):
    output, _, _ = build_bundle(tmp_path, monkeypatch)
    install_verify_verifiers(monkeypatch)
    handoff = output / "handoff.snapshot.json"
    original = (
        VERIFY.HANDOFF_VERIFY
        .verify_phase2_post_audit_catalog_handoff_snapshot
    )

    def swap_handoff(**kwargs):
        report = original(**kwargs)
        replacement = output / "replacement.json"
        replacement.write_bytes(handoff.read_bytes())
        replacement.chmod(0o600)
        handoff.unlink()
        replacement.rename(handoff)
        return report

    monkeypatch.setattr(
        VERIFY.HANDOFF_VERIFY,
        "verify_phase2_post_audit_catalog_handoff_snapshot",
        swap_handoff,
    )

    with pytest.raises(
        ValueError,
        match="handoff snapshot path changed after read",
    ):
        VERIFY.verify_phase2_portable_handoff_bundle(
            bundle_directory=output,
            repository_root=ROOT,
        )


def test_bundle_verifier_source_has_no_split_path_content_reads():
    source = VERIFY_TOOL.read_text(encoding="utf-8")

    assert ".read_text(" not in source
    assert ".read_bytes(" not in source



def test_builder_rejects_child_handoff_snapshot_identity_drift(
    tmp_path,
    monkeypatch,
):
    handoff, catalog, artifacts, _ = inputs(tmp_path)
    install_build_verifiers(monkeypatch)

    monkeypatch.setattr(
        BUILD.HANDOFF_VERIFY,
        "verify_phase2_post_audit_catalog_handoff_snapshot",
        lambda **kwargs: Report(
            handoff_snapshot_verified=True,
            snapshot_sha256="0" * 64,
            **valid_boundary(),
        ),
    )

    with pytest.raises(
        ValueError,
        match="handoff snapshot changed during nested verification",
    ):
        BUILD.build_phase2_portable_handoff_bundle(
            handoff_snapshot_path=handoff,
            catalog_snapshot_path=catalog,
            artifact_directory=artifacts,
            output_directory=tmp_path / "bundle",
            repository_root=ROOT,
        )


def test_builder_rejects_source_path_swap_during_nested_verification(
    tmp_path,
    monkeypatch,
):
    handoff, catalog, artifacts, _ = inputs(tmp_path)
    install_build_verifiers(monkeypatch)
    original = (
        BUILD.HANDOFF_VERIFY
        .verify_phase2_post_audit_catalog_handoff_snapshot
    )

    def swap_handoff(**kwargs):
        report = original(**kwargs)
        replacement = tmp_path / "replacement-handoff.json"
        replacement.write_bytes(handoff.read_bytes())
        replacement.chmod(0o600)
        handoff.unlink()
        replacement.rename(handoff)
        return report

    monkeypatch.setattr(
        BUILD.HANDOFF_VERIFY,
        "verify_phase2_post_audit_catalog_handoff_snapshot",
        swap_handoff,
    )

    with pytest.raises(
        ValueError,
        match="handoff snapshot path changed during bundle build",
    ):
        BUILD.build_phase2_portable_handoff_bundle(
            handoff_snapshot_path=handoff,
            catalog_snapshot_path=catalog,
            artifact_directory=artifacts,
            output_directory=tmp_path / "bundle",
            repository_root=ROOT,
        )


def test_builder_writes_exact_preverification_snapshot_bytes(
    tmp_path,
    monkeypatch,
):
    handoff, catalog, artifacts, _ = inputs(tmp_path)
    handoff_before = handoff.read_bytes()
    catalog_before = catalog.read_bytes()
    install_build_verifiers(monkeypatch)
    output = tmp_path / "bundle"

    report = BUILD.build_phase2_portable_handoff_bundle(
        handoff_snapshot_path=handoff,
        catalog_snapshot_path=catalog,
        artifact_directory=artifacts,
        output_directory=output,
        repository_root=ROOT,
    )

    assert (output / "handoff.snapshot.json").read_bytes() == handoff_before
    assert (output / "catalog.snapshot.json").read_bytes() == catalog_before
    assert report.handoff_snapshot_sha256 == hashlib.sha256(
        handoff_before
    ).hexdigest()
    assert report.catalog_snapshot_sha256 == hashlib.sha256(
        catalog_before
    ).hexdigest()


def test_bundle_builder_snapshot_inputs_use_descriptor_bound_io():
    source = BUILD_TOOL.read_text(encoding="utf-8")

    assert "def _private_json_snapshot(" in source
    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "handoff_bytes" in source
    assert "catalog_bytes" in source
    assert "snapshot_sha256" in source
    assert "shutil.copyfile(handoff_path" not in source
    assert "shutil.copyfile(catalog_path" not in source
