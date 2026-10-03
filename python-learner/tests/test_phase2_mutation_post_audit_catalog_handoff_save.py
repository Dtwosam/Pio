from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/save_phase2_mutation_post_audit_catalog_handoff.py"
SPEC = importlib.util.spec_from_file_location(
    "save_phase2_mutation_post_audit_catalog_handoff",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Handoff(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def good_handoff(**overrides):
    values = {
        "snapshot_path": "/archive/catalog.json",
        "snapshot_sha256": "1" * 64,
        "snapshot_verified": True,
        "historical_artifacts_seen": 2,
        "historical_artifacts_verified": 2,
        "historical_catalog_source_commit": "a" * 40,
        "historical_snapshot_only": True,
        "historical_authorizes_next_action": False,
        "fresh_reverification_requested": True,
        "artifacts_reverified": True,
        "fresh_reverification_verified": True,
        "fresh_snapshot_identity_matches": True,
        "fresh_artifact_directory": "/archive/post-audits",
        "current_state": "TIMER_ACTIVATION_READY",
        "current_next_action": "ACTIVATE_EVIDENCE_TIMER",
        "current_next_tool": "activate_phase2_isolated_timer.py",
        "current_next_parameters": {
            "runtime_root": "/opt/pio-phase2-runtime",
            "max_receipt_age_seconds": 1800,
        },
        "current_next_mutation_flag": "--apply",
        "current_attention_required": False,
        "current_blockers": (),
        "provider_rate_limit_incident": False,
        "provider_rate_limit_paused": False,
        "evidence_lineage_verified": True,
        "snapshot_influenced_current_action": False,
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
    return Handoff(**values)


def test_save_handoff_snapshot_is_private_create_only_and_non_authorizing(
    tmp_path,
    monkeypatch,
):
    handoff = good_handoff()
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: handoff,
    )
    output = tmp_path / "handoff.json"

    report = MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
        snapshot_path=tmp_path / "catalog.json",
        artifact_directory=tmp_path / "archive",
        output_path=output,
    )

    assert report.artifact_write_performed is True
    assert report.evidence_lineage_verified is True
    assert report.fresh_reverification_requested is True
    assert report.current_state == "TIMER_ACTIVATION_READY"
    assert report.current_next_action == "ACTIVATE_EVIDENCE_TIMER"
    assert report.current_next_mutation_flag == "--apply"
    assert report.authorizes_next_action is False
    assert report.requires_fresh_separate_mutation_authorization is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False
    assert report.mutation_executed is False
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["format_version"] == 1
    assert payload["artifact_type"] == (
        "PHASE2_MUTATION_POST_AUDIT_CATALOG_HANDOFF_SNAPSHOT_V1"
    )
    assert payload["handoff"]["authorizes_next_action"] is False
    assert payload["handoff"]["snapshot_influenced_current_action"] is False
    assert payload["handoff"]["next_action_source"] == (
        "CURRENT_LIFECYCLE_HANDOFF"
    )
    assert len(payload["handoff_source_commit"]) >= 40
    assert len(payload["handoff_tool_sha256"]) == 64
    assert len(payload["handoff_payload_sha256"]) == 64


def test_save_handoff_snapshot_allows_attention_state_as_evidence(
    tmp_path,
    monkeypatch,
):
    handoff = good_handoff(
        attention_required=True,
        current_attention_required=True,
        blockers=("RATE_LIMIT_PAUSED",),
        current_blockers=("RATE_LIMIT_PAUSED",),
        current_state="RATE_LIMIT_PAUSED",
        current_next_action="WAIT_FOR_PROVIDER_CAPACITY",
        current_next_tool=None,
        current_next_mutation_flag=None,
    )
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: handoff,
    )

    report = MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
        snapshot_path=tmp_path / "catalog.json",
        output_path=tmp_path / "handoff.json",
    )

    assert report.attention_required is True
    assert report.current_state == "RATE_LIMIT_PAUSED"
    assert report.authorizes_next_action is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_lineage_verified", False),
        ("snapshot_influenced_current_action", True),
        ("next_action_source", "HISTORICAL_SNAPSHOT"),
        ("authorizes_next_action", True),
        ("requires_fresh_separate_mutation_authorization", False),
        ("read_only", False),
        ("rpc_called", True),
        ("database_write_performed", True),
        ("service_control_performed", True),
        ("mutation_executed", True),
    ],
)
def test_save_handoff_snapshot_rejects_boundary_crossing(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: good_handoff(**{field: value}),
    )

    with pytest.raises(
        ValueError,
        match="not verified and safely non-authorizing",
    ):
        MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=tmp_path / "catalog.json",
            output_path=tmp_path / "handoff.json",
        )


def test_save_handoff_snapshot_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: good_handoff(),
    )
    output = tmp_path / "handoff.json"
    output.write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=tmp_path / "catalog.json",
            output_path=output,
        )


def test_save_handoff_snapshot_rejects_sensitive_report(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: good_handoff(
            current_next_parameters={
                "note": "https://rpc.invalid/?api-key=secret",
            }
        ),
    )

    with pytest.raises(ValueError, match="sensitive text"):
        MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=tmp_path / "catalog.json",
            output_path=tmp_path / "handoff.json",
        )


def test_handoff_source_identity_matches_reviewed_git_head():
    commit, tool_sha = MODULE._handoff_source_identity()

    assert len(commit) >= 40
    assert len(tool_sha) == 64
    assert tool_sha == MODULE.hashlib.sha256(
        MODULE.HANDOFF_TOOL.read_bytes()
    ).hexdigest()


def test_save_handoff_snapshot_rejects_source_change_during_build(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: good_handoff(),
    )
    identities = iter(
        (
            ("a" * 40, "1" * 64),
            ("b" * 40, "2" * 64),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_handoff_source_identity",
        lambda: next(identities),
    )

    output = tmp_path / "handoff.json"
    with pytest.raises(ValueError, match="source changed during snapshot build"):
        MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=tmp_path / "catalog.json",
            output_path=output,
        )

    assert not output.exists()


def test_save_handoff_snapshot_rejects_protected_output(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: good_handoff(),
    )

    with pytest.raises(ValueError, match="protected production path"):
        MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=tmp_path / "catalog.json",
            output_path="/opt/pio/data/handoff.json",
        )



def test_save_catalog_handoff_publish_race_never_overwrites_destination(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: good_handoff(),
    )
    monkeypatch.setattr(
        MODULE,
        "_handoff_source_identity",
        lambda: ("c" * 40, "d" * 64),
    )
    output = tmp_path / "handoff.json"
    competitor = b"concurrent catalog evidence\n"
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
        MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=tmp_path / "catalog.json",
            output_path=output,
        )

    assert output.read_bytes() == competitor
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".handoff.json.*.tmp"))



def test_save_catalog_handoff_digest_uses_exact_payload_not_path_reread(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: good_handoff(),
    )
    monkeypatch.setattr(
        MODULE,
        "_handoff_source_identity",
        lambda: ("c" * 40, "d" * 64),
    )
    output = tmp_path / "handoff.json"

    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(path):
        if path == output:
            raise AssertionError(
                "published catalog handoff must not be reopened for digesting"
            )
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    saved = MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
        snapshot_path=tmp_path / "catalog.json",
        output_path=output,
    )

    payload = output.read_text(encoding="utf-8").encode("utf-8")
    assert saved.artifact_sha256 == MODULE.hashlib.sha256(payload).hexdigest()
    assert saved.bytes_written == len(payload)



def test_save_catalog_handoff_detects_path_replacement_after_publish(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        MODULE.HANDOFF,
        "inspect_phase2_post_audit_catalog_handoff",
        lambda **kwargs: good_handoff(),
    )
    monkeypatch.setattr(
        MODULE,
        "_handoff_source_identity",
        lambda: ("c" * 40, "d" * 64),
    )
    output = tmp_path / "handoff.json"
    replacement_bytes = b"replacement catalog handoff\n"
    real_fsync = MODULE.os.fsync
    replaced = False

    def fsync_then_replace(fd):
        nonlocal replaced
        real_fsync(fd)
        if not replaced and stat.S_ISDIR(MODULE.os.fstat(fd).st_mode):
            replaced = True
            replacement = tmp_path / "replacement-handoff.json"
            replacement.write_bytes(replacement_bytes)
            replacement.chmod(0o600)
            MODULE.os.replace(replacement, output)

    monkeypatch.setattr(MODULE.os, "fsync", fsync_then_replace)

    with pytest.raises(ValueError, match="path changed after publish"):
        MODULE.save_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=tmp_path / "catalog.json",
            output_path=output,
        )

    assert output.read_bytes() == replacement_bytes
