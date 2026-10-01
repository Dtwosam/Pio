from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = (
    ROOT
    / "deploy"
    / "manifests"
    / "phase8-recursive-reentry-checkpoint-v25-continuation.json"
)


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    return hashlib.sha1(
        f"blob {len(payload)}\0".encode() + payload
    ).hexdigest()


def test_phase8_v25_operator_manifest_pins_bundle_provenance_and_single_tick():
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert value["format_version"] == 1
    assert value["checkpoint_version"] == 25
    assert value["prior_evidence_boundary"] == {
        "sealed_v24_evidence_bundle_required": True,
        "matching_v24_post_audit_required": True,
        "prior_bundle_digest_must_survive_all_v25_stages": True,
    }

    steps = value["ordered_steps"]
    assert [item["order"] for item in steps] == list(range(1, 9))
    assert [item["role"] for item in steps] == [
        "bundled-canonical-checkpoint",
        "continuation-readiness",
        "tick-request",
        "detached-authorization",
        "execution-readiness",
        "one-shot-paper-executor",
        "post-execution-audit",
        "evidence-bundle",
    ]

    mutating = [item for item in steps if item["paper_state_mutation"]]
    assert len(mutating) == 1
    assert mutating[0]["role"] == "one-shot-paper-executor"
    assert mutating[0]["maximum_tick_count"] == 1

    for item in steps:
        path = ROOT / item["tool"]
        assert path.is_file()
        assert _git_blob_sha(path) == item["git_blob"]

    boundary = value["execution_boundary"]
    assert boundary["maximum_paper_ticks_per_authorization"] == 1
    assert boundary["prior_bundle_digest_must_match_every_stage"] is True
    assert boundary["fresh_execution_readiness_required"] is True
    assert boundary["post_tick_audit_required"] is True
    assert boundary["final_bundle_requires_database_to_match_post_audit"] is True

    assert all(flag is False for flag in value["safety_boundary"].values())
