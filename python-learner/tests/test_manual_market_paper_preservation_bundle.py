from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_preservation_bundle.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_preservation_bundle",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reviewed_preservation_bundle_dependencies_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def _fake_module():
    def validate(_report):
        return None

    return SimpleNamespace(
        validate_candidate_report=validate,
        validate_portable_patch_report=validate,
        validate_portable_validation_report=validate,
    )


def _chain_reports(
    *,
    path,
    candidate_blob,
    current_blob,
    target_blob,
    seed,
    ready=True,
    blocker=None,
):
    candidate_sha = (str(seed % 10) or "1") * 64
    candidate_report_sha = str((seed + 1) % 10) * 64
    patch_sha = str((seed + 2) % 10) * 64
    patch_report_sha = str((seed + 3) % 10) * 64
    validation_report_sha = str((seed + 4) % 10) * 64

    candidate = {
        "production_head": MODULE.EXPECTED_PRODUCTION_HEAD,
        "path": path,
        "current_blob": current_blob,
        "target_blob": target_blob,
        "candidate_git_blob": candidate_blob,
        "candidate_sha256": candidate_sha,
        "candidate_size": 32000 + seed,
        "report_sha256": candidate_report_sha,
    }
    patch = {
        "candidate_report_sha256": candidate_report_sha,
        "reviewed_target_blob": target_blob,
        "candidate_git_blob": candidate_blob,
        "candidate_sha256": candidate_sha,
        "candidate_size": candidate["candidate_size"],
        "patch_sha256": patch_sha,
        "patch_size": 2048 + seed,
        "report_sha256": patch_report_sha,
    }
    validation = {
        "patch_report_sha256": patch_report_sha,
        "patch_sha256": patch_sha,
        "patch_size": patch["patch_size"],
        "candidate_git_blob": candidate_blob,
        "candidate_sha256": candidate_sha,
        "candidate_size": candidate["candidate_size"],
        "report_sha256": validation_report_sha,
        "reviewed_source_head": "a" * 40,
        "validation_ready": ready,
        "validation_blocker": blocker,
    }
    return candidate, patch, validation


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build_with_fake_modules(tmp_path, monkeypatch, *, research_ready=True, state_ready=True):
    research = _chain_reports(
        path=MODULE.RESEARCH_STORE_PATH,
        candidate_blob=MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
        current_blob="1" * 40,
        target_blob="2" * 40,
        seed=1,
        ready=research_ready,
        blocker=None if research_ready else "PYTEST_NOT_AVAILABLE",
    )
    state = _chain_reports(
        path=MODULE.STATE_READER_PATH,
        candidate_blob=MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
        current_blob="3" * 40,
        target_blob="4" * 40,
        seed=5,
        ready=state_ready,
        blocker=None if state_ready else "CARGO_NOT_FOUND",
    )

    fake = _fake_module()
    monkeypatch.setattr(MODULE, "_verify_reviewed_source", lambda _source: None)
    monkeypatch.setattr(MODULE, "_load_module", lambda _path, _name: fake)

    paths = []
    for prefix, reports in (("research", research), ("state", state)):
        for kind, report in zip(
            ("candidate", "patch", "validation"),
            reports,
            strict=True,
        ):
            paths.append(
                _write_json(
                    tmp_path / f"{prefix}-{kind}.json",
                    report,
                )
            )

    return MODULE.build_preservation_bundle(
        source_tree=tmp_path,
        research_candidate_report_path=paths[0],
        research_patch_report_path=paths[1],
        research_validation_report_path=paths[2],
        state_candidate_report_path=paths[3],
        state_patch_report_path=paths[4],
        state_validation_report_path=paths[5],
    )


def test_ready_preservation_bundle_seals_both_candidate_chains(tmp_path, monkeypatch):
    bundle = _build_with_fake_modules(tmp_path, monkeypatch)

    MODULE.validate_preservation_bundle(
        json.loads(json.dumps(bundle))
    )

    assert bundle["production_head"] == MODULE.EXPECTED_PRODUCTION_HEAD
    assert bundle["blockers"] == []
    assert bundle["all_candidates_validated"] is True
    assert bundle["bundle_ready_for_mutation_review"] is True
    assert bundle["mutation_authorized"] is False
    assert [item["preserved_candidate_blob"] for item in bundle["files"]] == [
        MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
        MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
    ]


def test_nonready_candidate_is_sealed_as_bundle_blocker(tmp_path, monkeypatch):
    bundle = _build_with_fake_modules(
        tmp_path,
        monkeypatch,
        state_ready=False,
    )

    MODULE.validate_preservation_bundle(bundle)

    assert bundle["all_candidates_validated"] is False
    assert bundle["bundle_ready_for_mutation_review"] is False
    assert bundle["blockers"] == [
        f"{MODULE.STATE_READER_PATH}:CARGO_NOT_FOUND"
    ]


def test_candidate_to_patch_digest_mismatch_fails_closed():
    candidate, patch, validation = _chain_reports(
        path=MODULE.RESEARCH_STORE_PATH,
        candidate_blob=MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
        current_blob="1" * 40,
        target_blob="2" * 40,
        seed=1,
    )
    patch["candidate_report_sha256"] = "f" * 64
    fake = _fake_module()

    try:
        MODULE._build_chain_entry(
            candidate_module=fake,
            patch_module=fake,
            validation_module=fake,
            candidate_report=candidate,
            patch_report=patch,
            validation_report=validation,
            expected_path=MODULE.RESEARCH_STORE_PATH,
            expected_candidate_blob=MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
        )
    except ValueError as exc:
        assert "candidate -> patch report" in str(exc)
    else:
        raise AssertionError("candidate/patch lineage mismatch must fail closed")


def test_patch_to_validation_digest_mismatch_fails_closed():
    candidate, patch, validation = _chain_reports(
        path=MODULE.STATE_READER_PATH,
        candidate_blob=MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
        current_blob="3" * 40,
        target_blob="4" * 40,
        seed=5,
    )
    validation["patch_report_sha256"] = "f" * 64
    fake = _fake_module()

    try:
        MODULE._build_chain_entry(
            candidate_module=fake,
            patch_module=fake,
            validation_module=fake,
            candidate_report=candidate,
            patch_report=patch,
            validation_report=validation,
            expected_path=MODULE.STATE_READER_PATH,
            expected_candidate_blob=MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
        )
    except ValueError as exc:
        assert "patch -> validation report" in str(exc)
    else:
        raise AssertionError("patch/validation lineage mismatch must fail closed")


def test_tampered_preservation_bundle_digest_is_rejected(tmp_path, monkeypatch):
    bundle = _build_with_fake_modules(tmp_path, monkeypatch)
    tampered = copy.deepcopy(bundle)
    tampered["files"][0]["patch_size"] += 1

    try:
        MODULE.validate_preservation_bundle(tampered)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered preservation bundle must fail closed")


def test_rehashed_preservation_bundle_cannot_authorize_mutation(tmp_path, monkeypatch):
    bundle = _build_with_fake_modules(tmp_path, monkeypatch)
    bundle["mutation_authorized"] = True
    identity = {
        field: bundle[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    bundle["bundle_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preservation_bundle(bundle)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_rehashed_preservation_bundle_cannot_substitute_candidate(tmp_path, monkeypatch):
    bundle = _build_with_fake_modules(tmp_path, monkeypatch)
    bundle["files"][0]["preserved_candidate_blob"] = "f" * 40
    identity = {
        field: bundle[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    bundle["bundle_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_preservation_bundle(bundle)
    except ValueError as exc:
        assert "candidate blob mismatch" in str(exc)
    else:
        raise AssertionError("rehashed candidate substitution must fail closed")


def test_preservation_bundle_tool_is_digest_only_and_production_blind():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "systemctl" not in source
    assert ".write_text(" not in source
    assert ".write_bytes(" not in source
    assert "shutil" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
