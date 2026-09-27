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


def test_preservation_bundle_v2_identity_has_no_stale_v1_source_pin():
    assert MODULE.FORMAT_VERSION == 2
    assert (
        MODULE.ARTIFACT_TYPE
        == "MANUAL_MARKET_PAPER_PRESERVATION_BUNDLE_V2"
    )

    source = TOOL.read_text(encoding="utf-8")
    assert "EXPECTED_VALIDATION_SOURCE_HEAD" not in source
    assert "bundle_ready_for_mutation_review" not in source
    assert "requires_fresh_production_recheck" not in source


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
    source_head="1" * 40,
    ready=True,
    blocker=None,
):
    digit = lambda offset: str((seed + offset) % 10 or 1)
    candidate_sha = digit(0) * 64
    candidate_report_sha = digit(1) * 64
    patch_sha = digit(2) * 64
    patch_report_sha = digit(3) * 64
    validation_report_sha = digit(4) * 64

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
        "reviewed_source_head": source_head,
        "validation_ready": ready,
        "validation_blocker": blocker,
    }
    return candidate, patch, validation


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build_with_fake_modules(
    tmp_path,
    monkeypatch,
    *,
    research_ready=True,
    state_ready=True,
    research_head="1" * 40,
    state_head="1" * 40,
):
    research = _chain_reports(
        path=MODULE.RESEARCH_STORE_PATH,
        candidate_blob=MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
        current_blob=MODULE.EXPECTED_RESEARCH_STORE_CURRENT_BLOB,
        target_blob=MODULE.EXPECTED_RESEARCH_STORE_TARGET_BLOB,
        seed=1,
        source_head=research_head,
        ready=research_ready,
        blocker=None if research_ready else "PYTEST_NOT_AVAILABLE",
    )
    state = _chain_reports(
        path=MODULE.STATE_READER_PATH,
        candidate_blob=MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
        current_blob=MODULE.EXPECTED_STATE_READER_CURRENT_BLOB,
        target_blob=MODULE.EXPECTED_STATE_READER_TARGET_BLOB,
        seed=5,
        source_head=state_head,
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


def test_ready_bundle_seals_both_chains_for_preservation_review(
    tmp_path,
    monkeypatch,
):
    bundle = _build_with_fake_modules(tmp_path, monkeypatch)

    MODULE.validate_preservation_bundle(
        json.loads(json.dumps(bundle))
    )

    assert bundle["blockers"] == []
    assert bundle["all_candidates_validated"] is True
    assert bundle["validation_source_heads_match"] is True
    assert bundle["common_validation_source_head"] == "1" * 40
    assert bundle["bundle_ready_for_preservation_review"] is True
    assert bundle["requires_preservation_review"] is True
    assert bundle["requires_reviewed_source_rebase"] is True
    assert bundle["requires_new_readiness_cycle"] is True
    assert bundle["mutation_authorized"] is False
    assert [item["preserved_candidate_blob"] for item in bundle["files"]] == [
        MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
        MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
    ]


def test_nonready_candidate_is_sealed_as_bundle_blocker(
    tmp_path,
    monkeypatch,
):
    bundle = _build_with_fake_modules(
        tmp_path,
        monkeypatch,
        state_ready=False,
    )

    MODULE.validate_preservation_bundle(bundle)

    assert bundle["all_candidates_validated"] is False
    assert bundle["bundle_ready_for_preservation_review"] is False
    assert bundle["blockers"] == [
        f"{MODULE.STATE_READER_PATH}:CARGO_NOT_FOUND"
    ]


def test_source_head_mismatch_blocks_bundle_even_when_both_validated(
    tmp_path,
    monkeypatch,
):
    bundle = _build_with_fake_modules(
        tmp_path,
        monkeypatch,
        research_head="1" * 40,
        state_head="2" * 40,
    )

    MODULE.validate_preservation_bundle(bundle)

    assert bundle["all_candidates_validated"] is True
    assert bundle["validation_source_heads_match"] is False
    assert bundle["common_validation_source_head"] is None
    assert bundle["bundle_ready_for_preservation_review"] is False
    assert bundle["blockers"] == ["VALIDATION_SOURCE_HEAD_MISMATCH"]


def test_json_artifact_symlink_is_rejected(tmp_path):
    target = tmp_path / "report.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "report-link.json"
    link.symlink_to(target)

    try:
        MODULE._load_json(link)
    except ValueError as exc:
        assert "must not be a symlink" in str(exc)
    else:
        raise AssertionError("symlink JSON artifact must fail closed")


def test_chain_rejects_wrong_production_current_blob():
    candidate, patch, validation = _chain_reports(
        path=MODULE.STATE_READER_PATH,
        candidate_blob=MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
        current_blob="3" * 40,
        target_blob=MODULE.EXPECTED_STATE_READER_TARGET_BLOB,
        seed=5,
    )
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
            expected_current_blob=MODULE.EXPECTED_STATE_READER_CURRENT_BLOB,
            expected_target_blob=MODULE.EXPECTED_STATE_READER_TARGET_BLOB,
            expected_candidate_blob=MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
        )
    except ValueError as exc:
        assert "production current blob" in str(exc)
    else:
        raise AssertionError("unexpected current blob must fail closed")


def test_candidate_to_patch_digest_mismatch_fails_closed():
    candidate, patch, validation = _chain_reports(
        path=MODULE.RESEARCH_STORE_PATH,
        candidate_blob=MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
        current_blob=MODULE.EXPECTED_RESEARCH_STORE_CURRENT_BLOB,
        target_blob=MODULE.EXPECTED_RESEARCH_STORE_TARGET_BLOB,
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
            expected_current_blob=MODULE.EXPECTED_RESEARCH_STORE_CURRENT_BLOB,
            expected_target_blob=MODULE.EXPECTED_RESEARCH_STORE_TARGET_BLOB,
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
        current_blob=MODULE.EXPECTED_STATE_READER_CURRENT_BLOB,
        target_blob=MODULE.EXPECTED_STATE_READER_TARGET_BLOB,
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
            expected_current_blob=MODULE.EXPECTED_STATE_READER_CURRENT_BLOB,
            expected_target_blob=MODULE.EXPECTED_STATE_READER_TARGET_BLOB,
            expected_candidate_blob=MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
        )
    except ValueError as exc:
        assert "patch -> validation report" in str(exc)
    else:
        raise AssertionError("patch/validation lineage mismatch must fail closed")


def test_tampered_bundle_digest_is_rejected(tmp_path, monkeypatch):
    bundle = _build_with_fake_modules(tmp_path, monkeypatch)
    tampered = copy.deepcopy(bundle)
    tampered["files"][0]["patch_size"] += 1

    try:
        MODULE.validate_preservation_bundle(tampered)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered preservation bundle must fail closed")


def test_rehashed_bundle_cannot_authorize_mutation(tmp_path, monkeypatch):
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


def test_rehashed_bundle_cannot_substitute_candidate(tmp_path, monkeypatch):
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


def test_bundle_tool_is_digest_only_and_production_blind():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "/opt/pio" not in source
    assert "systemctl" not in source
    assert ".write_text(" not in source
    assert ".write_bytes(" not in source
    assert "shutil" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
