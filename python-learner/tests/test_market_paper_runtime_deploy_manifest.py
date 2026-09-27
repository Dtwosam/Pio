from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy" / "manifests" / "market-paper-runtime.json"
PHASE2 = (
    ROOT
    / "deploy"
    / "manifests"
    / "phase2-collection-integration.json"
)
TOOL = ROOT / "deploy" / "tools" / "apply_phase2_collection_stack.py"

SPEC = importlib.util.spec_from_file_location(
    "market_paper_deploy_preflight",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


EXPECTED_RUNTIME = {
    "python-learner/src/meteora_learner/jupiter_quotes.py",
    "python-learner/src/meteora_learner/manual_market_paper_cycle.py",
    "python-learner/src/meteora_learner/manual_market_paper_cycle_cli.py",
    "python-learner/src/meteora_learner/market_chain_context.py",
    "python-learner/src/meteora_learner/market_chain_history_cycle.py",
    "python-learner/src/meteora_learner/market_chain_refresh.py",
    "python-learner/src/meteora_learner/market_paper_exploration.py",
    "python-learner/src/meteora_learner/market_paper_intake.py",
    "python-learner/src/meteora_learner/market_pool_universe.py",
    "python-learner/src/meteora_learner/market_research_cycle.py",
    "python-learner/src/meteora_learner/paper_account.py",
    "python-learner/src/meteora_learner/paper_candidate_cycle.py",
    "python-learner/src/meteora_learner/paper_candidate_policy.py",
    "python-learner/src/meteora_learner/paper_chain_valuation.py",
    "python-learner/src/meteora_learner/paper_empirical_entry.py",
    "python-learner/src/meteora_learner/paper_empirical_entry_workflow.py",
    "python-learner/src/meteora_learner/research_store.py",
    "python-learner/src/meteora_learner/storage.py",
}


def test_market_paper_manifest_is_locked_and_runtime_only():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["production_deployment_authorized"] is False
    assert payload["deployment_guard_apply_locked"] is True
    assert payload["service_restart_authorized"] is False
    assert payload["detector_cursor_movement_authorized"] is False
    assert payload["live_capital_authorized"] is False
    assert payload["paper_only"] is True
    assert payload["activation_mode"] == "MANUAL_ONLY"
    assert payload["phase2_collection_prerequisite_manifest"] == (
        "deploy/manifests/market-paper-phase2-prerequisites.json"
    )

    paths = set(payload["deployment_files"])
    assert paths == EXPECTED_RUNTIME
    assert "python-learner/pyproject.toml" not in paths
    assert not any(path.startswith("rust-executor/") for path in paths)
    assert not any(path.startswith("deploy/systemd/") for path in paths)


def test_market_paper_manifest_target_blobs_match_source_tree():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = payload["deployment_files"]
    targets = payload["deployment_target_file_blobs"]
    bases = payload["deployment_base_file_blobs"]

    assert len(paths) == len(set(paths))
    assert set(paths) == set(targets) == set(bases)

    for relative in paths:
        path = ROOT / relative
        assert path.is_file()
        assert targets[relative] == MODULE.git_blob_sha(path)
        base = bases[relative]
        assert base is None or (
            isinstance(base, str)
            and len(base) == 40
            and all(ch in "0123456789abcdef" for ch in base)
        )


def test_shared_store_bases_require_reviewed_phase2_targets():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    phase2 = json.loads(PHASE2.read_text(encoding="utf-8"))

    for relative in payload["phase2_prerequisite_files"]:
        assert relative in {
            "python-learner/src/meteora_learner/research_store.py",
            "python-learner/src/meteora_learner/storage.py",
        }
        assert payload["deployment_base_file_blobs"][relative] == (
            phase2["deployment_target_file_blobs"][relative]
        )
        assert payload["deployment_target_file_blobs"][relative] == (
            phase2["deployment_target_file_blobs"][relative]
        )


def test_market_paper_manifest_is_preflight_only_with_existing_guard():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["deployment_guard_apply_locked"] is True
    assert MANIFEST.resolve() != MODULE.DEFAULT_MANIFEST.resolve()
