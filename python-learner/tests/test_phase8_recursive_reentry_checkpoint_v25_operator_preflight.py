from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase8_recursive_reentry_checkpoint_v25_operator_preflight.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_phase8_recursive_reentry_checkpoint_v25_operator_preflight",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _build(monkeypatch):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    database = data / "pio.db"
    database.write_bytes(b"stable-production-db")
    allowed = root / "allowed_signers"
    allowed.write_text("operator namespaces ssh-ed25519 AAAATEST\n", encoding="utf-8")
    monkeypatch.setattr(
        MODULE,
        "_ssh_keygen_path",
        lambda: Path("/usr/bin/ssh-keygen"),
    )

    def run(expected=None):
        return MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_preflight(
            repository=production,
            source_tree=ROOT,
            allowed_signers_path=allowed,
            expected_allowed_signers_sha256=(
                expected if expected is not None else _sha(allowed.read_bytes())
            ),
        )

    return temp, database, allowed, run


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["preflight_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_manifest_and_reviewed_tools_are_exactly_pinned():
    manifest, manifest_sha = MODULE._load_operator_manifest(ROOT)
    assert len(manifest_sha) == 64
    blobs = MODULE._verify_source_tools(ROOT, manifest)
    assert len(blobs) == 8
    assert all(len(value) == 40 for value in blobs.values())


def test_manifest_rejects_prior_bundle_boundary_drift():
    manifest, _ = MODULE._load_operator_manifest(ROOT)
    tampered = json.loads(json.dumps(manifest))
    tampered["prior_evidence_boundary"][
        "prior_bundle_digest_must_survive_all_v25_stages"
    ] = False

    with pytest.raises(
        ValueError,
        match="prior evidence boundary mismatch",
    ):
        MODULE._validate_operator_manifest(tampered)


def test_manifest_rejects_missing_every_stage_bundle_binding():
    manifest, _ = MODULE._load_operator_manifest(ROOT)
    tampered = json.loads(json.dumps(manifest))
    tampered["execution_boundary"][
        "prior_bundle_digest_must_match_every_stage"
    ] = False

    with pytest.raises(
        ValueError,
        match="requires prior bundle lineage",
    ):
        MODULE._validate_operator_manifest(tampered)


def test_builds_read_only_operator_preflight(monkeypatch):
    temp, _, allowed, run = _build(monkeypatch)
    expected_allowed_sha = _sha(allowed.read_bytes())
    try:
        report = run()
    finally:
        temp.cleanup()

    assert report["allowed_signers_sha256"] == expected_allowed_sha
    assert report["manifest_valid"] is True
    assert report["reviewed_source_tools_verified"] is True
    assert report["source_tree_stable_during_preflight"] is True
    assert report["trust_root_verified"] is True
    assert report["ssh_signature_verifier_available"] is True
    assert report["database_stable_during_preflight"] is True
    assert report["one_shot_executor_only_mutating_step"] is True
    assert report["external_human_signature_required"] is True
    assert report["preflight_ready"] is True
    assert report["paper_supervisor_tick_authorized"] is False
    assert report["paper_trading_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["phase8_promotion_authorized"] is False
    MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_preflight(
        report
    )


def test_wrong_trust_root_digest_fails_closed(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        with pytest.raises(
            ValueError,
            match="allowed_signers trust-root digest mismatch",
        ):
            run("0" * 64)
    finally:
        temp.cleanup()


def test_symlinked_trust_root_is_rejected(monkeypatch):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    (data / "pio.db").write_bytes(b"stable-production-db")
    actual = root / "actual-allowed"
    actual.write_text("operator namespaces ssh-ed25519 AAAATEST\n", encoding="utf-8")
    alias = root / "allowed_signers"
    alias.symlink_to(actual)
    monkeypatch.setattr(
        MODULE,
        "_ssh_keygen_path",
        lambda: Path("/usr/bin/ssh-keygen"),
    )
    try:
        with pytest.raises(ValueError, match="must not be a symlink"):
            MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_preflight(
                repository=production,
                source_tree=ROOT,
                allowed_signers_path=alias,
                expected_allowed_signers_sha256=_sha(actual.read_bytes()),
            )
    finally:
        temp.cleanup()


def test_symlinked_database_is_rejected(monkeypatch):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    production = root / "production"
    data = production / "data"
    data.mkdir(parents=True)
    actual = root / "real.db"
    actual.write_bytes(b"stable-production-db")
    (data / "pio.db").symlink_to(actual)
    allowed = root / "allowed_signers"
    allowed.write_text("operator namespaces ssh-ed25519 AAAATEST\n", encoding="utf-8")
    monkeypatch.setattr(
        MODULE,
        "_ssh_keygen_path",
        lambda: Path("/usr/bin/ssh-keygen"),
    )
    try:
        with pytest.raises(ValueError, match="unsafe Pio database state file"):
            MODULE.build_phase8_recursive_reentry_checkpoint_v25_operator_preflight(
                repository=production,
                source_tree=ROOT,
                allowed_signers_path=allowed,
                expected_allowed_signers_sha256=_sha(allowed.read_bytes()),
            )
    finally:
        temp.cleanup()


def test_resealed_preflight_cannot_authorize_live_submit(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["live_submit_authorized"] = True
    _reseal(report)
    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_preflight(
            report
        )


def test_resealed_preflight_cannot_authorize_paper_tick(monkeypatch):
    temp, _, _, run = _build(monkeypatch)
    try:
        report = run()
    finally:
        temp.cleanup()

    report["paper_supervisor_tick_authorized"] = True
    _reseal(report)
    with pytest.raises(
        ValueError,
        match="paper_supervisor_tick_authorized=false",
    ):
        MODULE.validate_phase8_recursive_reentry_checkpoint_v25_operator_preflight(
            report
        )


def test_preflight_has_no_execution_or_database_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "sqlite3.connect" not in source
    assert "UPDATE paper_" not in source
    assert "INSERT INTO paper_" not in source
    assert "run_latest_live_paper_cycle(" not in source
    assert "run_paper_supervisor(" not in source
    assert "run_scheduled_paper_tick(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_supervisor_tick_authorized": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"phase8_promotion_authorized": False' in source
