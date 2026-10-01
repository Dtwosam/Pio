from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = (
    ROOT
    / "deploy"
    / "phase8-recursive-reentry-checkpoint-v24-operator-runbook.md"
)


def test_phase8_v24_operator_runbook_keeps_single_tick_boundary_explicit():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "check_phase8_recursive_reentry_checkpoint_v24_operator_preflight.py" in text
    assert "build_phase8_recursive_reentry_continuation_checkpoint_v24.py" in text
    assert "continuation_supervision_readiness.py" in text
    assert "continuation_evidence_tick_request.py" in text
    assert "continuation_evidence_tick_signed_authorization.py" in text
    assert "continuation_evidence_tick_execution_readiness.py" in text
    assert "continuation_evidence_tick_once.py" in text
    assert "continuation_evidence_tick_post_audit.py" in text
    assert "continuation_evidence_bundle.py" in text

    assert "This is the only mutation-capable step in the manifest." in text
    assert "Do not loop this command." in text
    assert "authorizes no next action" in text
    assert "recurring PAPER" in text
    assert "live submission" in text
    assert "new capital" in text
    assert "promotion" in text
