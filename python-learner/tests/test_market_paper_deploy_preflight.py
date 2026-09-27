import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "apply_phase2_collection_stack.py"
MANIFEST = ROOT / "deploy" / "manifests" / "market-paper-manual-cycle.json"
PREREQUISITE = (
    ROOT
    / "deploy"
    / "manifests"
    / "market-paper-phase2-prerequisites.json"
)

SPEC = importlib.util.spec_from_file_location(
    "apply_phase2_collection_stack_for_market_paper",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


EXPECTED_RUNTIME_FILES = {
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
}


def _payload():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_market_paper_manifest_is_strictly_preflight_only():
    payload = _payload()

    assert payload["production_deployment_authorized"] is False
    assert payload["deployment_guard_apply_locked"] is True
    assert payload["service_restart_authorized"] is False
    assert payload["detector_cursor_movement_authorized"] is False
    assert payload["paper_live_capital_authorized"] is False
    assert payload["paper_timer_enable_authorized"] is False
    assert payload["deployment_scope"] == (
        "MANUAL_MARKET_PAPER_RUNTIME_SOURCE_ONLY"
    )


def test_market_paper_manifest_has_exact_runtime_scope_and_target_blobs():
    payload = _payload()
    paths = payload["deployment_files"]
    targets = payload["deployment_target_file_blobs"]
    bases = payload["deployment_base_file_blobs"]

    assert set(paths) == EXPECTED_RUNTIME_FILES
    assert len(paths) == len(set(paths))
    assert set(paths) == set(targets) == set(bases)

    assert "python-learner/pyproject.toml" not in paths
    assert "python-learner/src/meteora_learner/cli.py" not in paths
    assert "python-learner/src/meteora_learner/research_store.py" not in paths
    assert "python-learner/src/meteora_learner/storage.py" not in paths
    assert "rust-executor/src/state_reader.rs" not in paths
    assert "rust-executor/src/main.rs" not in paths

    for relative in paths:
        path = ROOT / relative
        assert path.is_file()
        assert targets[relative] == MODULE.git_blob_sha(path)


def test_market_paper_manifest_enforces_phase2_shared_file_prerequisite():
    payload = _payload()
    prerequisite = json.loads(PREREQUISITE.read_text(encoding="utf-8"))

    assert payload["prerequisite_collection_target_ref"] == (
        "7b306f05e842bffdcc6144de39d0346b97e2ab67"
    )
    assert payload["prerequisite_collection_manifest"] == (
        "deploy/manifests/market-paper-phase2-prerequisites.json"
    )
    assert set(prerequisite["deployment_files"]) == {
        "python-learner/src/meteora_learner/research_store.py",
        "python-learner/src/meteora_learner/storage.py",
    }
    assert set(payload["deployment_files"]).isdisjoint(
        prerequisite["deployment_files"]
    )


def test_market_paper_manifest_keeps_state_reader_on_dedicated_guard():
    payload = _payload()

    assert payload["prerequisite_state_reader_target_blob"] == (
        "30d1435af1329bca07f73d6639539b43503e84e9"
    )
    assert "rust-executor/src/state_reader.rs" not in (
        payload["deployment_files"]
    )


def test_custom_market_paper_manifest_preflight_is_read_only_on_target_tree():
    report = MODULE.preflight_collection_stack(
        repository=ROOT,
        source_tree=ROOT,
        manifest=MANIFEST,
    )

    assert report.content_ready is True
    assert report.apply_authorized is False
    assert report.files_changed == 0
    assert all(item.status == "ALREADY_TARGET" for item in report.files)
