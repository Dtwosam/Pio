import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "verify_phase2_collection_stack.py"


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "verify_phase2_collection_stack",
        TOOL,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VERIFY = load_tool()


def test_collection_stack_verifier_accepts_current_reviewed_tree():
    result = VERIFY.verify_phase2_collection_stack(
        repository=ROOT,
    )

    assert result.verified is True
    assert result.components_verified >= 14
    assert result.critical_files_verified >= 16
    assert result.production_deployment_authorized is False
    assert result.detector_cursor_movement_authorized is False
    assert result.service_restart_authorized is False


def make_minimal_repo(tmp_path):
    repo = tmp_path / "repo"
    target = repo / "rust-executor" / "src" / "state_reader.rs"
    target.parent.mkdir(parents=True)
    target.write_text("state\n", encoding="utf-8")
    manifest = repo / "deploy" / "manifests" / "phase2-collection-integration.json"
    manifest.parent.mkdir(parents=True)
    blob = VERIFY._git_blob_sha(target)
    payload = {
        "format_version": 1,
        "production_deployment_authorized": False,
        "detector_cursor_movement_authorized": False,
        "service_restart_authorized": False,
        "state_reader_target_blob": blob,
        "components": [
            {
                "pr": 7,
                "head": "a" * 40,
                "role": "test component",
            }
        ],
        "critical_file_blobs": {
            "rust-executor/src/state_reader.rs": blob,
        },
    }
    manifest.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return repo, manifest, target


def test_collection_stack_verifier_rejects_modified_critical_file(tmp_path):
    repo, _, target = make_minimal_repo(tmp_path)
    target.write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="blob mismatch"):
        VERIFY.verify_phase2_collection_stack(
            repository=repo,
        )


def test_collection_stack_verifier_rejects_authorization_flip(tmp_path):
    repo, manifest, _ = make_minimal_repo(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["production_deployment_authorized"] = True
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="production_deployment_authorized must remain false",
    ):
        VERIFY.verify_phase2_collection_stack(
            repository=repo,
        )


def test_collection_stack_verifier_rejects_path_traversal(tmp_path):
    repo, manifest, target = make_minimal_repo(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["critical_file_blobs"]["../escape"] = (
        VERIFY._git_blob_sha(target)
    )
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe critical file path"):
        VERIFY.verify_phase2_collection_stack(
            repository=repo,
        )


def test_collection_stack_verifier_rejects_symlinked_critical_file(tmp_path):
    repo, manifest, target = make_minimal_repo(tmp_path)
    real = repo / "real-state-reader.rs"
    shutil.copyfile(target, real)
    target.unlink()
    target.symlink_to(real)

    with pytest.raises(ValueError, match="missing or not regular"):
        VERIFY.verify_phase2_collection_stack(
            repository=repo,
        )


def test_collection_stack_verifier_rejects_duplicate_component_pr(tmp_path):
    repo, manifest, _ = make_minimal_repo(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["components"].append(dict(payload["components"][0]))
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate collection component PR"):
        VERIFY.verify_phase2_collection_stack(
            repository=repo,
        )
