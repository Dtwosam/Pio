from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/apply_phase2_add_index_compat_patch.py"
SPEC = importlib.util.spec_from_file_location(
    "apply_phase2_add_index_compat_patch",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    for relative in MODULE.FILE_CONTRACT:
        src = ROOT / relative
        dst = source / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return source


def test_compat_patch_preflight_matches_reviewed_source(tmp_path):
    source = make_source(tmp_path)
    report = MODULE.evaluate(source_tree=source)

    assert report.ready is True
    assert report.applied is False
    assert report.status == "READY_APPLY"
    assert {item.status for item in report.files} == {"READY_APPLY"}
    assert report.production_tree_modified is False
    assert report.service_control_performed is False
    assert report.rpc_called is False


def test_compat_patch_applies_and_verifies_exact_target_blobs(tmp_path):
    source = make_source(tmp_path)
    report = MODULE.evaluate(source_tree=source, apply=True)

    assert report.ready is True
    assert report.applied is True
    assert report.status == "APPLIED"
    assert {item.status for item in report.files} == {"ALREADY_TARGET"}

    for relative, (_, expected_target) in MODULE.FILE_CONTRACT.items():
        path = source / relative
        assert MODULE.git_blob_sha(path) == expected_target
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    calibration = (
        source
        / "python-learner/src/meteora_learner/calibration_queue.py"
    ).read_text(encoding="utf-8")
    composition = (
        source
        / "python-learner/src/meteora_learner/composition_prestate.py"
    ).read_text(encoding="utf-8")
    store = (
        source
        / "python-learner/src/meteora_learner/research_store.py"
    ).read_text(encoding="utf-8")

    assert "resolve_add_instruction_index" in store
    assert "preferred_instruction_index=ix" in calibration
    assert "instruction_index=chain_ix" in calibration
    assert 'item["event_type"] == "Rebalancing"' in calibration
    assert "preferred_instruction_index=history_ix_index" in composition
    assert 'item["event_type"] == "Rebalancing"' in composition


def test_compat_patch_is_idempotent_after_apply(tmp_path):
    source = make_source(tmp_path)
    MODULE.evaluate(source_tree=source, apply=True)

    report = MODULE.evaluate(source_tree=source, apply=True)

    assert report.ready is True
    assert report.applied is True
    assert report.status == "ALREADY_TARGET"


def test_compat_patch_fails_closed_on_source_drift(tmp_path):
    source = make_source(tmp_path)
    path = source / next(iter(MODULE.FILE_CONTRACT))
    path.write_text(path.read_text(encoding="utf-8") + "\n# drift\n")

    report = MODULE.evaluate(source_tree=source)

    assert report.ready is False
    assert report.status == "SOURCE_DRIFT"


def test_compat_patch_refuses_live_production_tree():
    with pytest.raises(ValueError, match="must not be applied directly"):
        MODULE.evaluate(source_tree="/opt/pio")
