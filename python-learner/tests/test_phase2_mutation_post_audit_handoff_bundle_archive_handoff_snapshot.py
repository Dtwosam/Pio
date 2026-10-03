from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SAVE_TOOL = (
    ROOT
    / "deploy/tools/save_phase2_mutation_post_audit_handoff_bundle_archive_handoff.py"
)
VERIFY_TOOL = (
    ROOT
    / "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive_handoff_snapshot.py"
)
HANDOFF_RELATIVE = (
    "deploy/tools/"
    "check_phase2_mutation_post_audit_handoff_bundle_archive_handoff.py"
)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SAVE = load(SAVE_TOOL, "save_phase2_portable_archive_handoff_snapshot_test")
VERIFY = load(VERIFY_TOOL, "verify_phase2_portable_archive_handoff_snapshot_test")


class Report(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def good_handoff(**overrides):
    values = {
        "archive_path": "/archive/phase2-handoff.tar",
        "archive_sha256": "a" * 64,
        "archive_size": 4096,
        "archive_verified": True,
        "source_bundle_sha256": "b" * 64,
        "source_bundle_verified": True,
        "historical_archive_only": True,
        "historical_authorizes_next_action": False,
        "current_state": "TIMER_ACTIVATION_READY",
        "current_next_action": "ACTIVATE_EVIDENCE_TIMER",
        "current_next_tool": "activate_phase2_isolated_timer.py",
        "current_next_parameters": {
            "runtime_root": "/opt/pio-phase2-runtime",
            "receipt": "/opt/pio/data/phase2-isolated-smoke-receipt.json",
        },
        "current_next_mutation_flag": "--apply",
        "current_attention_required": False,
        "current_blockers": (),
        "provider_rate_limit_incident": False,
        "provider_rate_limit_paused": False,
        "evidence_lineage_verified": True,
        "archive_influenced_current_action": False,
        "next_action_source": "CURRENT_LIFECYCLE_HANDOFF",
        "attention_required": False,
        "blockers": (),
        "authorizes_next_action": False,
        "requires_fresh_separate_mutation_authorization": True,
        "read_only": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
    }
    values.update(overrides)
    return Report(**values)


def handoff_record(**overrides):
    report = good_handoff(**overrides)
    record = report.to_record()
    record["current_blockers"] = list(record["current_blockers"])
    record["blockers"] = list(record["blockers"])
    return record


def git(*args: str) -> bytes:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return proc.stdout


def test_saver_writes_private_non_authorizing_snapshot(tmp_path, monkeypatch):
    report = good_handoff()
    monkeypatch.setattr(
        SAVE.HANDOFF,
        "inspect_phase2_portable_archive_current_handoff",
        lambda **kwargs: report,
    )
    monkeypatch.setattr(
        SAVE,
        "_handoff_source_identity",
        lambda: ("c" * 40, "d" * 64),
    )
    output = tmp_path / "archive-handoff.snapshot.json"

    result = SAVE.save_phase2_portable_archive_current_handoff_snapshot(
        archive_path="/archive/phase2-handoff.tar",
        output_path=output,
        repository_root=ROOT,
    )

    assert result.artifact_write_performed is True
    assert result.authorizes_next_action is False
    assert result.requires_fresh_separate_mutation_authorization is True
    assert result.archive_sha256 == "a" * 64
    assert result.source_bundle_sha256 == "b" * 64
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["artifact_type"] == SAVE.ARTIFACT_TYPE
    assert payload["handoff"]["authorizes_next_action"] is False
    assert (
        payload["handoff"]["archive_influenced_current_action"]
        is False
    )


def test_saver_refuses_protected_output(tmp_path, monkeypatch):
    monkeypatch.setattr(
        SAVE.HANDOFF,
        "inspect_phase2_portable_archive_current_handoff",
        lambda **kwargs: good_handoff(),
    )
    with pytest.raises(ValueError, match="protected production path"):
        SAVE.save_phase2_portable_archive_current_handoff_snapshot(
            archive_path="/archive/phase2-handoff.tar",
            output_path="/opt/pio/data/archive-handoff.snapshot.json",
            repository_root=ROOT,
        )


def test_saver_rejects_sensitive_handoff_content(tmp_path, monkeypatch):
    monkeypatch.setattr(
        SAVE.HANDOFF,
        "inspect_phase2_portable_archive_current_handoff",
        lambda **kwargs: good_handoff(
            current_next_parameters={
                "SOLANA_RPC_URL": "https://rpc.invalid/?api-key=secret",
            }
        ),
    )
    monkeypatch.setattr(
        SAVE,
        "_handoff_source_identity",
        lambda: ("c" * 40, "d" * 64),
    )

    with pytest.raises(ValueError, match="sensitive"):
        SAVE.save_phase2_portable_archive_current_handoff_snapshot(
            archive_path="/archive/phase2-handoff.tar",
            output_path=tmp_path / "snapshot.json",
            repository_root=ROOT,
        )


def test_saver_rejects_source_identity_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(
        SAVE.HANDOFF,
        "inspect_phase2_portable_archive_current_handoff",
        lambda **kwargs: good_handoff(),
    )
    identities = iter(
        (
            ("c" * 40, "d" * 64),
            ("e" * 40, "f" * 64),
        )
    )
    monkeypatch.setattr(
        SAVE,
        "_handoff_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(ValueError, match="source changed"):
        SAVE.save_phase2_portable_archive_current_handoff_snapshot(
            archive_path="/archive/phase2-handoff.tar",
            output_path=tmp_path / "snapshot.json",
            repository_root=ROOT,
        )


def make_verified_snapshot(tmp_path: Path, *, handoff=None) -> Path:
    record = handoff or handoff_record()
    commit = git("rev-parse", "HEAD").decode().strip()
    historical_tool = git("show", f"{commit}:{HANDOFF_RELATIVE}")
    tool_sha = hashlib.sha256(historical_tool).hexdigest()
    payload = {
        "format_version": 1,
        "artifact_type": VERIFY.ARTIFACT_TYPE,
        "handoff_payload_sha256": VERIFY._canonical_sha256(record),
        "handoff_source_commit": commit,
        "handoff_tool_sha256": tool_sha,
        "handoff": record,
    }
    path = tmp_path / "archive-handoff.snapshot.json"
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_static_verifier_checks_historical_tool_and_payload(tmp_path):
    path = make_verified_snapshot(tmp_path)

    report = VERIFY.verify_phase2_portable_archive_current_handoff_snapshot(
        snapshot_path=path,
        repository_root=ROOT,
    )

    assert report.handoff_snapshot_verified is True
    assert report.handoff_payload_sha256_matches is True
    assert report.handoff_source_commit_present is True
    assert report.handoff_source_is_ancestor_of_current_head is True
    assert report.handoff_tool_sha256_matches is True
    assert report.evidence_lineage_verified is True
    assert report.non_authorizing_boundary_valid is True
    assert report.recorded_current_state == "TIMER_ACTIVATION_READY"
    assert report.recorded_current_next_action == "ACTIVATE_EVIDENCE_TIMER"
    assert report.historical_handoff_only is True
    assert report.current_lifecycle_rechecked is False
    assert report.authorizes_next_action is False
    assert report.requires_fresh_separate_mutation_authorization is True


def test_static_verifier_detects_payload_tampering(tmp_path):
    path = make_verified_snapshot(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["handoff"]["current_next_action"] = "RUN_ONE_SHOT_SMOKE"
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    report = VERIFY.verify_phase2_portable_archive_current_handoff_snapshot(
        snapshot_path=path,
        repository_root=ROOT,
    )

    assert report.handoff_payload_sha256_matches is False
    assert report.handoff_snapshot_verified is False


def test_static_verifier_rejects_authority_escalation(tmp_path):
    record = handoff_record(authorizes_next_action=True)
    path = make_verified_snapshot(tmp_path, handoff=record)

    with pytest.raises(ValueError, match="non-authorizing boundary"):
        VERIFY.verify_phase2_portable_archive_current_handoff_snapshot(
            snapshot_path=path,
            repository_root=ROOT,
        )


def test_static_verifier_rejects_sensitive_content(tmp_path):
    record = handoff_record(
        current_next_parameters={
            "rpc_url": "https://rpc.invalid/?api-key=secret",
        }
    )
    path = make_verified_snapshot(tmp_path, handoff=record)

    with pytest.raises(ValueError, match="sensitive"):
        VERIFY.verify_phase2_portable_archive_current_handoff_snapshot(
            snapshot_path=path,
            repository_root=ROOT,
        )


def test_static_verifier_requires_private_file_mode(tmp_path):
    path = make_verified_snapshot(tmp_path)
    path.chmod(0o644)

    with pytest.raises(ValueError, match="permissions must be 0600"):
        VERIFY.verify_phase2_portable_archive_current_handoff_snapshot(
            snapshot_path=path,
            repository_root=ROOT,
        )



def test_static_verifier_hashes_the_same_snapshot_bytes_it_parses(
    tmp_path,
    monkeypatch,
):
    path = make_verified_snapshot(tmp_path)
    expected_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    original_read_bytes = Path.read_bytes

    def forbid_second_snapshot_read(self):
        if self == path:
            raise AssertionError("snapshot path must not be reread after capture")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", forbid_second_snapshot_read)

    report = VERIFY.verify_phase2_portable_archive_current_handoff_snapshot(
        snapshot_path=path,
        repository_root=ROOT,
    )

    assert report.handoff_snapshot_verified is True
    assert report.snapshot_sha256 == expected_sha


def test_static_verifier_fails_closed_if_snapshot_path_is_replaced_after_read(
    tmp_path,
    monkeypatch,
):
    path = make_verified_snapshot(tmp_path)
    original_snapshot = VERIFY._read_snapshot_bytes

    def snapshot_then_replace(snapshot_path):
        payload, opened = original_snapshot(snapshot_path)
        replacement = tmp_path / "replacement.snapshot.json"
        replacement.write_bytes(payload)
        replacement.chmod(0o600)
        replacement.replace(snapshot_path)
        return payload, opened

    monkeypatch.setattr(
        VERIFY,
        "_read_snapshot_bytes",
        snapshot_then_replace,
    )

    with pytest.raises(
        ValueError,
        match="snapshot path changed during verification",
    ):
        VERIFY.verify_phase2_portable_archive_current_handoff_snapshot(
            snapshot_path=path,
            repository_root=ROOT,
        )
