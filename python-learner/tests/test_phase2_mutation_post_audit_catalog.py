from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/catalog_phase2_mutation_post_audits.py"
SPEC = importlib.util.spec_from_file_location(
    "catalog_phase2_mutation_post_audits",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def private_file(path: Path, payload: str = "{}") -> Path:
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)
    return path


def verified_report(path: Path, *, artifact_sha: str, receipt_sha: str):
    return SimpleNamespace(
        static_audit_verified=True,
        artifact_path=str(path),
        artifact_sha256=artifact_sha,
        audit_source_commit="a" * 40,
        execution_receipt_sha256=receipt_sha,
        prior_state="SMOKE_REQUIRED",
        current_state="TIMER_ACTIVATION_READY",
        current_next_action="ACTIVATE_EVIDENCE_TIMER",
        current_next_tool="activate_phase2_isolated_timer.py",
        current_next_mutation_flag="--apply",
    )


def test_catalog_is_deterministic_and_read_only(tmp_path, monkeypatch):
    a = private_file(tmp_path / "b.post-audit.json")
    b = private_file(tmp_path / "a.post-audit.json")
    private_file(tmp_path / "ignored.json")

    reports = {
        str(a.resolve()): verified_report(
            a, artifact_sha="1" * 64, receipt_sha="2" * 64
        ),
        str(b.resolve()): verified_report(
            b, artifact_sha="3" * 64, receipt_sha="4" * 64
        ),
    }
    monkeypatch.setattr(
        MODULE.VERIFY,
        "verify_saved_mutation_post_audit",
        lambda *, artifact_path, repository_root: reports[str(Path(artifact_path).resolve())],
    )

    report = MODULE.build_phase2_post_audit_catalog(
        artifact_directory=tmp_path,
    )

    assert [Path(item.artifact_path).name for item in report.entries] == [
        "a.post-audit.json",
        "b.post-audit.json",
    ]
    assert report.artifacts_seen == 2
    assert report.artifacts_verified == 2
    assert report.artifacts_failed == 0
    assert report.catalog_stable_during_scan is True
    assert report.all_verified is True
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False


def test_catalog_failure_is_secret_safe(tmp_path, monkeypatch):
    artifact = private_file(tmp_path / "bad.post-audit.json")
    secret = "https://rpc.invalid/?api-key=secret"

    def fail(**kwargs):
        raise ValueError(f"verification failed at {secret}")

    monkeypatch.setattr(
        MODULE.VERIFY,
        "verify_saved_mutation_post_audit",
        fail,
    )

    report = MODULE.build_phase2_post_audit_catalog(
        artifact_directory=tmp_path,
    )

    assert report.artifacts_failed == 1
    assert report.entries[0].failure_category == "ARTIFACT_VERIFICATION_FAILED"
    assert secret not in str(report.to_record())


def test_catalog_reports_duplicate_evidence_identity(tmp_path, monkeypatch):
    a = private_file(tmp_path / "a.post-audit.json")
    b = private_file(tmp_path / "b.post-audit.json")

    monkeypatch.setattr(
        MODULE.VERIFY,
        "verify_saved_mutation_post_audit",
        lambda *, artifact_path, repository_root: verified_report(
            Path(artifact_path),
            artifact_sha="1" * 64,
            receipt_sha="2" * 64,
        ),
    )

    report = MODULE.build_phase2_post_audit_catalog(
        artifact_directory=tmp_path,
    )

    assert report.duplicate_artifact_hashes == 1
    assert report.duplicate_receipt_hashes == 1


def test_catalog_accounts_for_non_private_candidates(tmp_path, monkeypatch):
    private_file(tmp_path / "private.post-audit.json")
    public = tmp_path / "public.post-audit.json"
    public.write_text("{}", encoding="utf-8")
    public.chmod(0o644)

    def verify(*, artifact_path, repository_root):
        path = Path(artifact_path)
        if path.name == "public.post-audit.json":
            raise ValueError("saved mutation post-audit permissions must be 0600")
        return verified_report(
            path,
            artifact_sha="1" * 64,
            receipt_sha="2" * 64,
        )

    monkeypatch.setattr(
        MODULE.VERIFY,
        "verify_saved_mutation_post_audit",
        verify,
    )

    report = MODULE.build_phase2_post_audit_catalog(
        artifact_directory=tmp_path,
    )

    assert report.artifacts_seen == 2
    assert report.artifacts_verified == 1
    assert report.artifacts_failed == 1
    assert report.all_verified is False
    assert report.entries[1].failure_category == "ARTIFACT_VERIFICATION_FAILED"


def test_catalog_rejects_symlink_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        MODULE.build_phase2_post_audit_catalog(
            artifact_directory=alias,
        )


def test_catalog_requires_simple_pattern(tmp_path):
    with pytest.raises(ValueError, match="simple filename glob"):
        MODULE.build_phase2_post_audit_catalog(
            artifact_directory=tmp_path,
            pattern="../*.json",
        )



def test_empty_catalog_is_not_verified(tmp_path):
    report = MODULE.build_phase2_post_audit_catalog(
        artifact_directory=tmp_path,
    )

    assert report.artifacts_seen == 0
    assert report.artifacts_verified == 0
    assert report.artifacts_failed == 0
    assert report.all_verified is False



def test_catalog_rejects_directory_change_during_verification(tmp_path, monkeypatch):
    artifact = private_file(tmp_path / "a.post-audit.json")
    changed = False

    def verify(*, artifact_path, repository_root):
        nonlocal changed
        if not changed:
            changed = True
            private_file(tmp_path / "b.post-audit.json")
        return verified_report(
            Path(artifact_path),
            artifact_sha="1" * 64,
            receipt_sha="2" * 64,
        )

    monkeypatch.setattr(
        MODULE.VERIFY,
        "verify_saved_mutation_post_audit",
        verify,
    )

    with pytest.raises(ValueError, match="changed during verification"):
        MODULE.build_phase2_post_audit_catalog(
            artifact_directory=tmp_path,
        )

def test_catalog_verifier_source_identity_matches_exact_executed_bytes():
    commit, verifier_sha = MODULE._verifier_source_identity()

    assert len(commit) >= 40
    assert verifier_sha == MODULE._VERIFY_TOOL_SHA256_AT_LOAD
    assert verifier_sha == hashlib.sha256(
        MODULE._VERIFY_TOOL_BYTES_AT_LOAD
    ).hexdigest()


def test_catalog_never_rereads_loaded_verifier_tool_bytes(monkeypatch):
    real_read_bytes = Path.read_bytes

    def reject_verifier_reread(path):
        if path.resolve() == MODULE._VERIFY_TOOL_PATH_AT_LOAD:
            raise AssertionError(
                "catalog verifier identity must use captured dependency bytes"
            )
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_verifier_reread)

    commit, verifier_sha = MODULE._verifier_source_identity()

    assert len(commit) >= 40
    assert verifier_sha == MODULE._VERIFY_TOOL_SHA256_AT_LOAD


def test_catalog_verifier_is_descriptor_captured_and_executed():
    source = TOOL.read_text(encoding="utf-8")

    assert "def _capture_tool(" in source
    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "compile(encoded" in source
    assert "exec(code, module.__dict__)" in source
    assert "_VERIFY_TOOL_BYTES_AT_LOAD" in source
    assert "VERIFY_TOOL.read_bytes()" not in source


def test_catalog_rejects_symlinked_verifier_path(tmp_path, monkeypatch):
    real_tool = tmp_path / "verify.py"
    real_tool.write_text("# reviewed\n", encoding="utf-8")
    link = tmp_path / "verify-link.py"
    link.symlink_to(real_tool)
    monkeypatch.setattr(MODULE, "VERIFY_TOOL", link)

    with pytest.raises(ValueError, match="missing or symlinked"):
        MODULE.build_phase2_post_audit_catalog(
            artifact_directory=tmp_path,
        )


def test_catalog_rejects_verifier_source_change_during_scan(
    tmp_path,
    monkeypatch,
):
    artifact = private_file(tmp_path / "a.post-audit.json")
    monkeypatch.setattr(
        MODULE.VERIFY,
        "verify_saved_mutation_post_audit",
        lambda *, artifact_path, repository_root: verified_report(
            Path(artifact_path),
            artifact_sha="1" * 64,
            receipt_sha="2" * 64,
        ),
    )
    identities = iter(
        (
            ("a" * 40, "1" * 64),
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_verifier_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(ValueError, match="source changed during catalog"):
        MODULE.build_phase2_post_audit_catalog(
            artifact_directory=tmp_path,
        )

    assert artifact.exists()

