import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "apply_phase2_collection_stack.py"
MANIFEST = (
    ROOT
    / "deploy"
    / "manifests"
    / "phase2-collection-integration.json"
)

SPEC = importlib.util.spec_from_file_location(
    "apply_phase2_collection_stack",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def fixture_tree(tmp_path, *, authorized=False):
    repo = tmp_path / "repo"
    source = tmp_path / "source"
    existing = "python/existing.py"
    created = "deploy/new.service"

    write(repo / existing, "base\n")
    write(repo / "keep.txt", "production-local\n")
    write(source / existing, "target\n")
    write(source / created, "new\n")

    payload = {
        "production_deployment_authorized": authorized,
        "deployment_guard_apply_locked": not authorized,
        "deployment_files": [existing, created],
        "deployment_target_file_blobs": {
            existing: MODULE.git_blob_sha(source / existing),
            created: MODULE.git_blob_sha(source / created),
        },
        "deployment_base_file_blobs": {
            existing: MODULE.git_blob_sha(repo / existing),
            created: None,
        },
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    return repo, source, manifest, existing, created


def status_map(report):
    return {item.path: item.status for item in report.files}


def test_preflight_accepts_exact_base_and_new_files(tmp_path):
    repo, source, manifest, existing, created = fixture_tree(tmp_path)

    report = MODULE.preflight_collection_stack(
        repository=repo,
        source_tree=source,
        manifest=manifest,
    )

    assert report.content_ready is True
    assert report.apply_authorized is False
    assert report.files_changed == 2
    assert status_map(report) == {
        existing: "READY_UPDATE",
        created: "READY_CREATE",
    }
    assert (repo / existing).read_text(encoding="utf-8") == "base\n"
    assert not (repo / created).exists()
    assert (repo / "keep.txt").read_text(encoding="utf-8") == (
        "production-local\n"
    )


def test_apply_refuses_while_manifest_authorization_is_locked(tmp_path):
    repo, source, manifest, existing, created = fixture_tree(tmp_path)

    with pytest.raises(ValueError, match="not authorized"):
        MODULE.apply_guarded_collection_stack(
            repository=repo,
            source_tree=source,
            manifest=manifest,
            apply=True,
            backup_dir=tmp_path / "backups",
        )

    assert (repo / existing).read_text(encoding="utf-8") == "base\n"
    assert not (repo / created).exists()


def test_authorized_apply_backs_up_updates_and_preserves_unrelated_files(
    tmp_path,
):
    repo, source, manifest, existing, created = fixture_tree(
        tmp_path,
        authorized=True,
    )

    report = MODULE.apply_guarded_collection_stack(
        repository=repo,
        source_tree=source,
        manifest=manifest,
        apply=True,
        backup_dir=tmp_path / "backups",
    )

    assert report.applied is True
    assert report.apply_authorized is True
    assert report.backup_root is not None
    assert (repo / existing).read_text(encoding="utf-8") == "target\n"
    assert (repo / created).read_text(encoding="utf-8") == "new\n"
    assert (repo / "keep.txt").read_text(encoding="utf-8") == (
        "production-local\n"
    )
    backup = Path(report.backup_root) / existing
    assert backup.read_text(encoding="utf-8") == "base\n"


def test_preflight_fails_closed_on_local_target_modification(tmp_path):
    repo, source, manifest, existing, _ = fixture_tree(tmp_path)
    write(repo / existing, "production-local-change\n")

    report = MODULE.preflight_collection_stack(
        repository=repo,
        source_tree=source,
        manifest=manifest,
    )

    assert report.content_ready is False
    assert status_map(report)[existing] == "CONFLICT_MODIFIED"


def test_preflight_fails_closed_on_unreviewed_source_bytes(tmp_path):
    repo, source, manifest, existing, _ = fixture_tree(tmp_path)
    write(source / existing, "different-source\n")

    report = MODULE.preflight_collection_stack(
        repository=repo,
        source_tree=source,
        manifest=manifest,
    )

    assert report.content_ready is False
    assert status_map(report)[existing] == "SOURCE_MISMATCH"


def test_preflight_recognizes_already_target_file(tmp_path):
    repo, source, manifest, existing, _ = fixture_tree(tmp_path)
    write(repo / existing, "target\n")

    report = MODULE.preflight_collection_stack(
        repository=repo,
        source_tree=source,
        manifest=manifest,
    )

    assert status_map(report)[existing] == "ALREADY_TARGET"


def test_reviewed_manifest_is_apply_locked_and_matches_source_tree():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["production_deployment_authorized"] is False
    assert payload["deployment_guard_apply_locked"] is True

    paths = payload["deployment_files"]
    targets = payload["deployment_target_file_blobs"]
    bases = payload["deployment_base_file_blobs"]
    assert paths
    assert len(paths) == len(set(paths))
    assert set(paths) == set(targets) == set(bases)

    for relative in paths:
        path = ROOT / relative
        assert path.is_file()
        assert targets[relative] == MODULE.git_blob_sha(path)
        base = bases[relative]
        assert base is None or (
            isinstance(base, str) and len(base) == 40
        )



def test_preflight_rejects_symlinked_source_file(tmp_path):
    repo, source, manifest, existing, _ = fixture_tree(tmp_path)
    external = tmp_path / "external.py"
    external.write_text("target\n", encoding="utf-8")
    (source / existing).unlink()
    (source / existing).symlink_to(external)

    report = MODULE.preflight_collection_stack(
        repository=repo,
        source_tree=source,
        manifest=manifest,
    )

    assert report.content_ready is False
    assert status_map(report)[existing] in {
        "SOURCE_SYMLINK",
        "SOURCE_OUTSIDE_TREE",
    }


def test_manifest_rejects_non_hex_blob_hash(tmp_path):
    repo, source, manifest, existing, _ = fixture_tree(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["deployment_target_file_blobs"][existing] = "z" * 40
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid target blob"):
        MODULE.preflight_collection_stack(
            repository=repo,
            source_tree=source,
            manifest=manifest,
        )
