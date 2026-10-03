from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tarfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_TOOL = (
    ROOT / "deploy/tools/archive_phase2_mutation_post_audit_handoff_bundle.py"
)
VERIFY_TOOL = (
    ROOT / "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive.py"
)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ARCHIVE = load(ARCHIVE_TOOL, "archive_phase2_portable_bundle_test")
VERIFY = load(VERIFY_TOOL, "verify_phase2_portable_bundle_archive_test")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def make_bundle(tmp_path: Path):
    root = tmp_path / "bundle"
    root.mkdir(mode=0o700)
    audits = root / "post-audits"
    audits.mkdir(mode=0o700)

    handoff = b'{"handoff":"snapshot"}\n'
    catalog = b'{"catalog":"snapshot"}\n'
    audit = b'{"post_audit":"verified"}\n'
    audit_sha = sha(audit)
    bundle_sha = "b" * 64

    manifest = {
        "format_version": 1,
        "artifact_type": "PHASE2_MUTATION_POST_AUDIT_HANDOFF_BUNDLE_V1",
        "handoff_snapshot": {
            "path": "handoff.snapshot.json",
            "sha256": sha(handoff),
        },
        "catalog_snapshot": {
            "path": "catalog.snapshot.json",
            "sha256": sha(catalog),
        },
        "post_audits": [
            {
                "path": f"post-audits/{audit_sha}.post-audit.json",
                "sha256": audit_sha,
            }
        ],
        "artifacts_seen": 1,
        "bundle_sha256": bundle_sha,
    }

    write_file(root / "manifest.json", json.dumps(manifest).encode())
    write_file(root / "handoff.snapshot.json", handoff)
    write_file(root / "catalog.snapshot.json", catalog)
    write_file(audits / f"{audit_sha}.post-audit.json", audit)
    return root, bundle_sha


def bundle_report(root: Path, bundle_sha: str):
    return SimpleNamespace(
        bundle_directory=str(root),
        bundle_sha256=bundle_sha,
        bundle_verified=True,
        historical_handoff_only=True,
        current_lifecycle_rechecked=False,
        authorizes_next_action=False,
        requires_fresh_separate_mutation_authorization=True,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def test_archive_is_deterministic_private_and_non_authorizing(tmp_path, monkeypatch):
    root, bundle_sha = make_bundle(tmp_path)
    monkeypatch.setattr(
        ARCHIVE.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(root, bundle_sha),
    )

    first = tmp_path / "first.tar"
    second = tmp_path / "second.tar"
    one = ARCHIVE.build_phase2_portable_bundle_archive(
        bundle_directory=root,
        output_path=first,
        repository_root=ROOT,
    )
    two = ARCHIVE.build_phase2_portable_bundle_archive(
        bundle_directory=root,
        output_path=second,
        repository_root=ROOT,
    )

    assert one.archive_sha256 == two.archive_sha256
    assert first.read_bytes() == second.read_bytes()
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert one.source_bundle_sha256 == bundle_sha
    assert one.deterministic_metadata is True
    assert one.authorizes_next_action is False
    assert one.requires_fresh_separate_mutation_authorization is True
    assert one.production_file_modified is False
    assert one.rpc_called is False
    assert one.database_write_performed is False
    assert one.service_control_performed is False
    assert one.mutation_executed is False


def test_archive_verifier_rechecks_reconstructed_bundle(tmp_path, monkeypatch):
    root, bundle_sha = make_bundle(tmp_path)
    monkeypatch.setattr(
        ARCHIVE.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(root, bundle_sha),
    )
    archive = tmp_path / "bundle.tar"
    ARCHIVE.build_phase2_portable_bundle_archive(
        bundle_directory=root,
        output_path=archive,
        repository_root=ROOT,
    )

    seen = {}

    def verify_bundle(**kwargs):
        extracted = Path(kwargs["bundle_directory"])
        seen["mode"] = stat.S_IMODE(extracted.stat().st_mode)
        seen["manifest"] = (extracted / "manifest.json").is_file()
        return bundle_report(extracted, bundle_sha)

    monkeypatch.setattr(
        VERIFY.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        verify_bundle,
    )
    report = VERIFY.verify_phase2_portable_bundle_archive(
        archive_path=archive,
        repository_root=ROOT,
    )

    assert report.archive_verified is True
    assert report.source_bundle_verified is True
    assert report.source_bundle_sha256 == bundle_sha
    assert report.deterministic_metadata_valid is True
    assert report.temporary_extraction_performed is True
    assert report.authorizes_next_action is False
    assert report.requires_fresh_separate_mutation_authorization is True
    assert report.production_file_modified is False
    assert seen == {"mode": 0o700, "manifest": True}


def test_archive_verifier_rejects_path_traversal_before_extraction(tmp_path):
    path = tmp_path / "malicious.tar"
    with tarfile.open(path, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("../escape")
        info.mode = 0o600
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        payload = b"x"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    path.chmod(0o600)

    with pytest.raises(ValueError, match="member path is invalid"):
        VERIFY.verify_phase2_portable_bundle_archive(
            archive_path=path,
            repository_root=ROOT,
        )


def test_archive_verifier_rejects_member_mode_drift(tmp_path, monkeypatch):
    root, bundle_sha = make_bundle(tmp_path)
    monkeypatch.setattr(
        ARCHIVE.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(root, bundle_sha),
    )
    valid = tmp_path / "valid.tar"
    ARCHIVE.build_phase2_portable_bundle_archive(
        bundle_directory=root,
        output_path=valid,
        repository_root=ROOT,
    )

    bad = tmp_path / "bad.tar"
    with tarfile.open(valid, mode="r:") as source, tarfile.open(
        bad,
        mode="w",
        format=tarfile.USTAR_FORMAT,
    ) as target:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            copied = tarfile.TarInfo(member.name)
            copied.type = member.type
            copied.mode = 0o644 if member.name == "manifest.json" else member.mode
            copied.uid = 0
            copied.gid = 0
            copied.uname = ""
            copied.gname = ""
            copied.mtime = 0
            copied.size = len(data) if data is not None else 0
            target.addfile(
                copied,
                io.BytesIO(data) if data is not None else None,
            )
    bad.chmod(0o600)

    with pytest.raises(ValueError, match="member mode is invalid"):
        VERIFY.verify_phase2_portable_bundle_archive(
            archive_path=bad,
            repository_root=ROOT,
        )


def test_archive_builder_refuses_protected_output(tmp_path, monkeypatch):
    root, bundle_sha = make_bundle(tmp_path)
    monkeypatch.setattr(
        ARCHIVE.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(root, bundle_sha),
    )

    with pytest.raises(ValueError, match="protected production path"):
        ARCHIVE.build_phase2_portable_bundle_archive(
            bundle_directory=root,
            output_path="/opt/pio/data/phase2-bundle.tar",
            repository_root=ROOT,
        )



def test_archive_verifier_hashes_and_parses_the_same_byte_snapshot(
    tmp_path,
    monkeypatch,
):
    root, bundle_sha = make_bundle(tmp_path)
    monkeypatch.setattr(
        ARCHIVE.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(root, bundle_sha),
    )
    archive_path = tmp_path / "bundle.tar"
    ARCHIVE.build_phase2_portable_bundle_archive(
        bundle_directory=root,
        output_path=archive_path,
        repository_root=ROOT,
    )
    expected_sha = sha(archive_path.read_bytes())

    monkeypatch.setattr(
        VERIFY.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(
            Path(kwargs["bundle_directory"]),
            bundle_sha,
        ),
    )
    real_open = VERIFY.tarfile.open
    seen = {}

    def snapshot_only_open(*args, **kwargs):
        seen["fileobj"] = kwargs.get("fileobj")
        seen["name"] = kwargs.get("name")
        if args:
            seen["positional_name"] = args[0]
        assert kwargs.get("fileobj") is not None
        return real_open(*args, **kwargs)

    monkeypatch.setattr(VERIFY.tarfile, "open", snapshot_only_open)

    report = VERIFY.verify_phase2_portable_bundle_archive(
        archive_path=archive_path,
        repository_root=ROOT,
    )

    assert report.archive_sha256 == expected_sha
    assert seen["fileobj"] is not None
    assert seen.get("name") is None
    assert "positional_name" not in seen


def test_archive_verifier_fails_closed_if_path_is_replaced_after_snapshot(
    tmp_path,
    monkeypatch,
):
    root, bundle_sha = make_bundle(tmp_path)
    monkeypatch.setattr(
        ARCHIVE.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(root, bundle_sha),
    )
    archive_path = tmp_path / "bundle.tar"
    ARCHIVE.build_phase2_portable_bundle_archive(
        bundle_directory=root,
        output_path=archive_path,
        repository_root=ROOT,
    )

    monkeypatch.setattr(
        VERIFY.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(
            Path(kwargs["bundle_directory"]),
            bundle_sha,
        ),
    )
    original_snapshot = VERIFY._read_archive_snapshot

    def snapshot_then_replace(path):
        payload, opened = original_snapshot(path)
        replacement = tmp_path / "replacement.tar"
        replacement.write_bytes(payload)
        replacement.chmod(0o600)
        replacement.replace(path)
        return payload, opened

    monkeypatch.setattr(
        VERIFY,
        "_read_archive_snapshot",
        snapshot_then_replace,
    )

    with pytest.raises(
        ValueError,
        match="archive path changed during verification",
    ):
        VERIFY.verify_phase2_portable_bundle_archive(
            archive_path=archive_path,
            repository_root=ROOT,
        )



def test_archive_builder_publish_race_never_overwrites_destination(
    tmp_path,
    monkeypatch,
):
    root, bundle_sha = make_bundle(tmp_path)
    monkeypatch.setattr(
        ARCHIVE.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(root, bundle_sha),
    )
    output = tmp_path / "portable.tar"
    competitor = b"concurrent archive evidence\n"
    real_link = ARCHIVE.os.link
    raced = False

    def competing_publish(source, destination, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            destination = Path(destination)
            destination.write_bytes(competitor)
            destination.chmod(0o600)
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(ARCHIVE.os, "link", competing_publish)

    with pytest.raises(
        ValueError,
        match="output appeared before publish",
    ):
        ARCHIVE.build_phase2_portable_bundle_archive(
            bundle_directory=root,
            output_path=output,
            repository_root=ROOT,
        )

    assert output.read_bytes() == competitor
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".portable.tar.*.tmp"))


def test_archive_builder_digest_uses_exact_prepublished_tar_bytes(
    tmp_path,
    monkeypatch,
):
    root, bundle_sha = make_bundle(tmp_path)
    monkeypatch.setattr(
        ARCHIVE.BUNDLE,
        "verify_phase2_portable_handoff_bundle",
        lambda **kwargs: bundle_report(root, bundle_sha),
    )
    output = tmp_path / "portable.tar"
    original_read_bytes = Path.read_bytes

    def forbid_output_reread(self):
        if self == output:
            raise AssertionError("published archive path must not be reread for digest")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", forbid_output_reread)

    report = ARCHIVE.build_phase2_portable_bundle_archive(
        bundle_directory=root,
        output_path=output,
        repository_root=ROOT,
    )

    published = original_read_bytes(output)
    assert report.archive_sha256 == hashlib.sha256(published).hexdigest()
    assert report.archive_size == len(published)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
