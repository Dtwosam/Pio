import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = (
    ROOT
    / "deploy"
    / "phase8-recursive-reentry-checkpoint-v25-operator-runbook.md"
)
MANIFEST = (
    ROOT
    / "deploy"
    / "manifests"
    / "phase8-recursive-reentry-checkpoint-v25-continuation.json"
)


def test_phase8_v25_operator_runbook_keeps_single_tick_boundary_explicit():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "check_phase8_recursive_reentry_checkpoint_v25_operator_preflight.py" in text
    assert "check_phase8_recursive_reentry_checkpoint_v25_artifact_status.py" in text
    assert "check_phase8_recursive_reentry_checkpoint_v25_evidence_handoff.py" in text
    assert "build_phase8_recursive_reentry_continuation_checkpoint_v25.py" in text
    assert "continuation_supervision_readiness.py" in text
    assert "continuation_evidence_tick_request.py" in text
    assert "continuation_evidence_tick_signed_authorization.py" in text
    assert "continuation_evidence_tick_execution_readiness.py" in text
    assert "continuation_evidence_tick_once.py" in text
    assert "continuation_evidence_tick_post_audit.py" in text
    assert "continuation_evidence_bundle.py" in text

    assert "This is the only mutation-capable step in the manifest." in text
    assert "V24_EVIDENCE_BUNDLE" in text
    assert "V24_POST_AUDIT" in text
    assert "--evidence-bundle" in text
    assert "Do not loop this command." in text
    assert "authorizes no next action" in text
    assert "MUTATION_BOUNDARY_REVIEW_REQUIRED" in text
    assert "is not authorization to execute" in text
    assert "evidence_handoff_ready=true" in text
    assert "future_checkpoint_refresh_authorized=false" in text
    assert "$ARTIFACT_DIR/evidence-handoff-v25.json" in text
    assert "does not reverify the detached SSH signature" in text
    assert "does not" in text and "production database" in text
    assert "recurring PAPER" in text
    assert "live submission" in text
    assert "new capital" in text
    assert "promotion" in text


def test_phase8_v25_operator_runbook_uses_manifest_artifact_names():
    text = RUNBOOK.read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    outputs = [
        step["output_artifact"]
        for step in manifest["ordered_steps"]
    ]
    assert len(outputs) == 8
    assert len(set(outputs)) == 8
    for output in outputs:
        assert f"$ARTIFACT_DIR/{output}" in text

    legacy_names = (
        "$ARTIFACT_DIR/checkpoint.json",
        "$ARTIFACT_DIR/continuation-readiness.json",
        "$ARTIFACT_DIR/request.json",
        "$ARTIFACT_DIR/signed-authorization-verification.json",
        "$ARTIFACT_DIR/execution-readiness.json",
        "$ARTIFACT_DIR/execution-receipt.json",
        "$ARTIFACT_DIR/post-audit.json",
        "$ARTIFACT_DIR/evidence-bundle.json",
    )
    assert all(name not in text for name in legacy_names)
