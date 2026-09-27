from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = (
    ROOT
    / "deploy"
    / "manifests"
    / "market-paper-phase2-prerequisites.json"
)
PHASE2 = (
    ROOT
    / "deploy"
    / "manifests"
    / "phase2-collection-integration.json"
)
TOOL = ROOT / "deploy" / "tools" / "apply_phase2_collection_stack.py"

SPEC = importlib.util.spec_from_file_location(
    "market_paper_phase2_prerequisite_preflight",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


EXPECTED_FILES = {
    "python-learner/src/meteora_learner/research_store.py",
    "python-learner/src/meteora_learner/storage.py",
}


def _payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_prerequisite_manifest_is_locked_and_exactly_scoped():
    payload = _payload(MANIFEST)

    assert payload["production_deployment_authorized"] is False
    assert payload["deployment_guard_apply_locked"] is True
    assert payload["service_restart_authorized"] is False
    assert payload["detector_cursor_movement_authorized"] is False
    assert payload["paper_live_capital_authorized"] is False
    assert payload["paper_timer_enable_authorized"] is False
    assert payload["deployment_scope"] == (
        "MANUAL_MARKET_PAPER_PHASE2_SHARED_PREREQUISITES"
    )
    assert payload["source_manifest"] == (
        "deploy/manifests/phase2-collection-integration.json"
    )

    files = payload["deployment_files"]
    assert set(files) == EXPECTED_FILES
    assert len(files) == len(set(files))
    assert "python-learner/src/meteora_learner/cli.py" not in files
    assert "python-learner/src/meteora_learner/composition_automation.py" not in files
    assert "rust-executor/src/main.rs" not in files
    assert not any(path.startswith("scripts/") for path in files)
    assert not any(path.startswith("deploy/systemd/") for path in files)


def test_prerequisite_manifest_preserves_reviewed_phase2_lineage():
    payload = _payload(MANIFEST)
    phase2 = _payload(PHASE2)

    assert payload["source_manifest_target_ref"] == (
        "7b306f05e842bffdcc6144de39d0346b97e2ab67"
    )
    assert payload["deployment_target_ref"] == (
        "7b306f05e842bffdcc6144de39d0346b97e2ab67"
    )

    for relative in EXPECTED_FILES:
        assert payload["deployment_base_file_blobs"][relative] == (
            phase2["deployment_base_file_blobs"][relative]
        )
        assert payload["deployment_target_file_blobs"][relative] == (
            phase2["deployment_target_file_blobs"][relative]
        )
        assert payload["deployment_target_file_blobs"][relative] == (
            MODULE.git_blob_sha(ROOT / relative)
        )


def test_prerequisite_manifest_preflight_is_read_only_on_reviewed_tree():
    report = MODULE.preflight_collection_stack(
        repository=ROOT,
        source_tree=ROOT,
        manifest=MANIFEST,
    )

    assert report.content_ready is True
    assert report.apply_authorized is False
    assert report.applied is False
    assert report.files_changed == 0
    assert {item.path for item in report.files} == EXPECTED_FILES
    assert all(item.status == "ALREADY_TARGET" for item in report.files)
