import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_TOOL = (
    ROOT / "deploy" / "tools" / "snapshot_phase2_production_sources.py"
)
VERIFY_TOOL = (
    ROOT / "deploy" / "tools" / "verify_phase2_production_sources.py"
)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SNAPSHOT = load(SNAPSHOT_TOOL, "snapshot_phase2_sources_for_verify")
VERIFY = load(VERIFY_TOOL, "verify_phase2_production_sources")


def make_snapshot(tmp_path):
    production = tmp_path / "production"
    production.mkdir()
    detector = production / "phase2-add-detector.py"
    watcher = production / "phase2-prestate-watch.py"
    detector.write_bytes(b"detector-local-fix\n")
    watcher.write_bytes(b"watcher-local-fix\n")
    snapshot = tmp_path / "snapshot"
    SNAPSHOT.capture_production_sources(
        source_specs=(
            f"detector={detector}",
            f"watcher={watcher}",
        ),
        output_directory=snapshot,
        captured_at="2026-09-26T16:00:00+00:00",
    )
    return snapshot, detector, watcher


def test_verify_accepts_unchanged_snapshot_and_exact_repo_import(tmp_path):
    snapshot, detector, watcher = make_snapshot(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    imported_detector = repo / "phase2-add-detector.py"
    imported_watcher = repo / "phase2-prestate-watch.py"
    imported_detector.write_bytes(detector.read_bytes())
    imported_watcher.write_bytes(watcher.read_bytes())

    result = VERIFY.verify_production_source_snapshot(
        snapshot_directory=snapshot,
        repo_source_specs=(
            f"detector={imported_detector}",
            f"watcher={imported_watcher}",
        ),
    )

    assert result.verified is True
    assert result.sources_verified == 2
    assert result.imports_verified == 2
    assert all(item.imported_path for item in result.sources)


def test_verify_rejects_modified_captured_source(tmp_path):
    snapshot, _, _ = make_snapshot(tmp_path)
    captured = next(snapshot.glob("detector__*"))
    captured.write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        VERIFY.verify_production_source_snapshot(
            snapshot_directory=snapshot,
        )


def test_verify_rejects_repo_import_that_differs_from_capture(tmp_path):
    snapshot, _, _ = make_snapshot(tmp_path)
    imported = tmp_path / "phase2-add-detector.py"
    imported.write_text("reconstructed-from-memory\n", encoding="utf-8")

    with pytest.raises(ValueError, match="repo source hash mismatch"):
        VERIFY.verify_production_source_snapshot(
            snapshot_directory=snapshot,
            repo_source_specs=(f"detector={imported}",),
        )


def test_verify_rejects_unknown_or_duplicate_repo_labels(tmp_path):
    snapshot, detector, _ = make_snapshot(tmp_path)

    with pytest.raises(ValueError, match="not present in snapshot"):
        VERIFY.verify_production_source_snapshot(
            snapshot_directory=snapshot,
            repo_source_specs=(f"unknown={detector}",),
        )

    with pytest.raises(ValueError, match="duplicate repo source label"):
        VERIFY.verify_production_source_snapshot(
            snapshot_directory=snapshot,
            repo_source_specs=(
                f"detector={detector}",
                f"detector={detector}",
            ),
        )


def test_verify_rejects_unsafe_captured_path(tmp_path):
    snapshot, _, _ = make_snapshot(tmp_path)
    manifest = snapshot / "manifest.json"
    import json

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sources"][0]["captured_path"] = "../escape.py"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe captured path"):
        VERIFY.verify_production_source_snapshot(
            snapshot_directory=snapshot,
        )
