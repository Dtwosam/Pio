from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = (
    ROOT
    / "deploy"
    / "manifests"
    / "phase8-recursive-reentry-checkpoint-v24-continuation.json"
)


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def test_phase8_v24_operator_manifest_is_exact_and_non_authorizing():
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert value["format_version"] == 1
    assert value["artifact_type"] == (
        "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V24_CONTINUATION_OPERATOR_MANIFEST_V1"
    )
    assert value["checkpoint_version"] == 24

    steps = value["ordered_steps"]
    assert [item["order"] for item in steps] == list(range(1, 9))
    assert [item["role"] for item in steps] == [
        "canonical-checkpoint",
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

    signed = next(
        item for item in steps if item["role"] == "detached-authorization"
    )
    assert signed["human_signature_required"] is True

    for item in steps:
        path = ROOT / item["tool"]
        assert path.is_file()
        assert _git_blob_sha(path) == item["git_blob"]

    boundary = value["execution_boundary"]
    assert boundary["only_mutating_step"] == "one-shot-paper-executor"
    assert boundary["maximum_paper_ticks_per_authorization"] == 1
    assert boundary["fresh_execution_readiness_required"] is True
    assert boundary["post_tick_audit_required"] is True
    assert boundary["final_bundle_requires_database_to_match_post_audit"] is True

    assert value["required_external_authorization_inputs"] == [
        "signed authorization payload",
        "detached SSH signature",
        "allowed_signers trust-root file",
        "expected allowed_signers SHA-256",
    ]

    safety = value["safety_boundary"]
    assert safety == {
        "automatic_paper_execution_authorized": False,
        "recurring_paper_collection_authorized": False,
        "scheduler_execution_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
    }
