import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_TOOL = (
    ROOT / "deploy" / "tools" / "snapshot_phase2_production_sources.py"
)
GATE_TOOL = (
    ROOT / "deploy" / "tools" / "check_phase2_production_import_ready.py"
)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SNAPSHOT = load(SNAPSHOT_TOOL, "snapshot_phase2_sources_for_gate")
GATE = load(GATE_TOOL, "check_phase2_production_import_ready")


REQUIRED_TESTS = (
    "test_detector_has_resolved_run_wrapper",
    "test_detector_cursor_is_fail_closed",
    "test_detector_pagination_reaches_retained_cursor_without_skipping",
    "test_detector_retries_rpc_failures_with_backoff",
    "test_detector_supports_1000_signature_pages",
    "test_watcher_promotes_verified_prestate",
    "test_detector_failure_does_not_stop_watcher",
    "test_rpc_url_not_exposed_in_process_arguments",
)


def make_fixture(tmp_path):
    production = tmp_path / "production"
    production.mkdir()
    source_bytes = {
        "detector": b"detector-production\n",
        "watcher": b"watcher-production\n",
        "detector_unit": b"[Service]\nExecStart=/detector\n",
        "watcher_unit": b"[Service]\nExecStart=/watcher\n",
    }
    source_paths = {}
    for label, payload in source_bytes.items():
        path = production / f"{label}.txt"
        path.write_bytes(payload)
        source_paths[label] = path

    snapshot = tmp_path / "snapshot"
    SNAPSHOT.capture_production_sources(
        source_specs=tuple(
            f"{label}={path}"
            for label, path in source_paths.items()
        ),
        output_directory=snapshot,
        captured_at="2026-09-26T20:00:00+00:00",
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    imports = {
        "detector": "scripts/phase2-add-detector.py",
        "watcher": "scripts/phase2-prestate-watch.py",
        "detector_unit": "deploy/systemd/pio-phase2-add-detector.service",
        "watcher_unit": "deploy/systemd/pio-phase2-prestate-watch.service",
    }
    for label, relative in imports.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source_bytes[label])

    test_files = (
        "python-learner/tests/test_phase2_add_detector.py",
        "python-learner/tests/test_phase2_prestate_watch.py",
        "python-learner/tests/test_phase2_detector_watcher_systemd.py",
    )
    distributed = {
        test_files[0]: REQUIRED_TESTS[:5],
        test_files[1]: REQUIRED_TESTS[5:6],
        test_files[2]: REQUIRED_TESTS[6:],
    }
    for relative, names in distributed.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n\n".join(
                f"def {name}():\n    assert True"
                for name in names
            )
            + "\n",
            encoding="utf-8",
        )

    runbook = repo / "deploy/phase2-detector-watcher-update.md"
    runbook.parent.mkdir(parents=True, exist_ok=True)
    runbook.write_text("reviewed import procedure\n", encoding="utf-8")

    contract = {
        "format_version": 1,
        "issue": 6,
        "production_import_ready_requires_snapshot_match": True,
        "required_sources": [
            {"label": label, "repo_path": relative}
            for label, relative in imports.items()
        ],
        "required_regression_tests": list(REQUIRED_TESTS),
        "required_test_files": list(test_files),
        "required_runbook": "deploy/phase2-detector-watcher-update.md",
    }
    contract_path = repo / "deploy/manifests/phase2-production-import-contract.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(contract),
        encoding="utf-8",
    )
    return repo, snapshot, contract_path


def test_import_gate_accepts_exact_snapshot_and_complete_regression_contract(
    tmp_path,
):
    repo, snapshot, contract = make_fixture(tmp_path)

    result = GATE.evaluate_phase2_production_import_gate(
        repo_root=repo,
        snapshot_directory=snapshot,
        contract_path=contract,
    )

    assert result.issue == 6
    assert result.required_sources == 4
    assert result.snapshot_imports_verified == 4
    assert result.required_test_files == 3
    assert result.test_files_verified == 3
    assert result.required_regression_tests == len(REQUIRED_TESTS)
    assert result.test_functions_verified == len(REQUIRED_TESTS)
    assert result.runbook_verified is True
    assert result.ready is True


def test_import_gate_rejects_repo_file_that_drifted_from_production(
    tmp_path,
):
    repo, snapshot, contract = make_fixture(tmp_path)
    (repo / "scripts/phase2-add-detector.py").write_text(
        "reconstructed\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match production snapshot"):
        GATE.evaluate_phase2_production_import_gate(
            repo_root=repo,
            snapshot_directory=snapshot,
            contract_path=contract,
        )


def test_import_gate_rejects_missing_required_regression_test(tmp_path):
    repo, snapshot, contract = make_fixture(tmp_path)
    path = repo / "python-learner/tests/test_phase2_add_detector.py"
    source = path.read_text(encoding="utf-8")
    path.write_text(
        source.replace(
            "def test_detector_cursor_is_fail_closed():",
            "def test_different_name():",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="required regression tests missing"):
        GATE.evaluate_phase2_production_import_gate(
            repo_root=repo,
            snapshot_directory=snapshot,
            contract_path=contract,
        )


def test_import_gate_rejects_missing_required_import(tmp_path):
    repo, snapshot, contract = make_fixture(tmp_path)
    (repo / "scripts/phase2-prestate-watch.py").unlink()

    with pytest.raises(ValueError, match="required repo import missing"):
        GATE.evaluate_phase2_production_import_gate(
            repo_root=repo,
            snapshot_directory=snapshot,
            contract_path=contract,
        )


def test_import_gate_rejects_unsafe_repo_path(tmp_path):
    repo, snapshot, contract = make_fixture(tmp_path)
    payload = json.loads(contract.read_text(encoding="utf-8"))
    payload["required_sources"][0]["repo_path"] = "../escape.py"
    contract.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe repo-relative path"):
        GATE.evaluate_phase2_production_import_gate(
            repo_root=repo,
            snapshot_directory=snapshot,
            contract_path=contract,
        )


def test_import_gate_rejects_empty_runbook(tmp_path):
    repo, snapshot, contract = make_fixture(tmp_path)
    (repo / "deploy/phase2-detector-watcher-update.md").write_text(
        "",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runbook cannot be empty"):
        GATE.evaluate_phase2_production_import_gate(
            repo_root=repo,
            snapshot_directory=snapshot,
            contract_path=contract,
        )
