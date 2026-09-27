from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "collect_manual_market_paper_state_reader_conflict_hunks.py"
)

SPEC = importlib.util.spec_from_file_location(
    "collect_manual_market_paper_state_reader_conflict_hunks",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reviewed_state_reader_conflict_hunk_source_is_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_parse_exactly_two_diff3_conflict_blocks():
    merged = b"""prefix
<<<<<<< production-local
local one
local two
||||||| reviewed-base
base one
=======
target one
>>>>>>> reviewed-target
middle
<<<<<<< production-local
local three
||||||| reviewed-base
base three
base four
=======
target three
target four
>>>>>>> reviewed-target
suffix
"""

    parsed = MODULE._parse_diff3_conflicts(merged)

    assert parsed == [
        {
            "production_local": ["local one", "local two"],
            "reviewed_base": ["base one"],
            "reviewed_target": ["target one"],
        },
        {
            "production_local": ["local three"],
            "reviewed_base": ["base three", "base four"],
            "reviewed_target": ["target three", "target four"],
        },
    ]


def test_merge_file_returncode_two_is_valid_conflict_result():
    merged = b"""<<<<<<< production-local
local
||||||| reviewed-base
base
=======
target
>>>>>>> reviewed-target
"""

    def runner(args, **kwargs):
        assert args[:3] == ["git", "merge-file", "-p"]
        assert "--diff3" in args
        return subprocess.CompletedProcess(
            args=args,
            returncode=2,
            stdout=merged,
            stderr=b"",
        )

    returncode, payload = MODULE._merge_file_diff3(
        current=b"local\n",
        base=b"base\n",
        target=b"target\n",
        runner=runner,
    )

    assert returncode == 2
    assert payload == merged


def test_merge_file_clean_or_execution_error_fails_closed():
    def clean_runner(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=b"clean\n",
            stderr=b"",
        )

    try:
        MODULE._merge_file_diff3(
            current=b"local\n",
            base=b"base\n",
            target=b"target\n",
            runner=clean_runner,
        )
    except ValueError as exc:
        assert "unexpectedly became clean" in str(exc)
    else:
        raise AssertionError("unexpected clean merge must fail closed")

    def bad_runner(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=128,
            stdout=b"",
            stderr=b"fatal",
        )

    try:
        MODULE._merge_file_diff3(
            current=b"local\n",
            base=b"base\n",
            target=b"target\n",
            runner=bad_runner,
        )
    except ValueError as exc:
        assert "return code 128" in str(exc)
    else:
        raise AssertionError("merge-file execution error must fail closed")


def test_conflict_records_map_exact_ranges_and_emit_only_conflict_lines():
    current = b"""head
local one
local two
between
local three
tail
"""
    base = b"""head
base one
between
base three
base four
tail
"""
    target = b"""head
target one
between
target three
target four
tail
"""
    parsed = [
        {
            "production_local": ["local one", "local two"],
            "reviewed_base": ["base one"],
            "reviewed_target": ["target one"],
        },
        {
            "production_local": ["local three"],
            "reviewed_base": ["base three", "base four"],
            "reviewed_target": ["target three", "target four"],
        },
    ]

    records = MODULE._conflict_records(
        current=current,
        base=base,
        target=target,
        parsed=parsed,
    )

    assert len(records) == 2
    assert records[0]["production_local"]["line_range"] == {
        "start": 2,
        "end": 3,
    }
    assert records[0]["reviewed_base"]["line_range"] == {
        "start": 2,
        "end": 2,
    }
    assert records[0]["reviewed_target"]["line_range"] == {
        "start": 2,
        "end": 2,
    }
    assert records[1]["production_local"]["line_range"] == {
        "start": 5,
        "end": 5,
    }
    assert records[1]["reviewed_base"]["line_range"] == {
        "start": 4,
        "end": 5,
    }
    assert records[1]["reviewed_target"]["line_range"] == {
        "start": 4,
        "end": 5,
    }

    serialized = json.dumps(records)
    assert "head" not in serialized
    assert "between" not in serialized
    assert "tail" not in serialized
    assert "local one" in serialized
    assert "target four" in serialized


def test_empty_conflict_side_has_null_range():
    record = MODULE._section_record(
        lines=[],
        line_range={"start": None, "end": None},
    )

    assert record["line_count"] == 0
    assert record["line_range"] == {"start": None, "end": None}
    assert record["lines"] == []
    assert record["sha256"] == hashlib.sha256(b"").hexdigest()


def test_conflict_content_bounds_fail_closed():
    too_many = ["line"] * (MODULE.MAX_LINES_PER_SIDE + 1)
    try:
        MODULE._conflict_records(
            current=("\n".join(too_many) + "\n").encode(),
            base=b"base\n",
            target=b"target\n",
            parsed=[
                {
                    "production_local": too_many,
                    "reviewed_base": ["base"],
                    "reviewed_target": ["target"],
                }
            ],
        )
    except ValueError as exc:
        assert "bounded line limit" in str(exc)
    else:
        raise AssertionError("oversized conflict side must fail closed")


def _section(lines, start):
    payload = ("\n".join(lines) + ("\n" if lines else "")).encode()
    return {
        "line_range": (
            {"start": start, "end": start + len(lines) - 1}
            if lines
            else {"start": None, "end": None}
        ),
        "line_count": len(lines),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "lines": lines,
    }


def _synthetic_evidence():
    conflicts = []
    for index, offset in ((1, 10), (2, 30)):
        current = _section([f"local-{index}"], offset)
        base = _section([f"base-{index}"], offset)
        target = _section([f"target-{index}"], offset)
        identity = {
            "index": index,
            "production_local": current,
            "reviewed_base": base,
            "reviewed_target": target,
        }
        conflicts.append(
            {
                **identity,
                "conflict_sha256": hashlib.sha256(
                    MODULE._canonical_bytes(identity)
                ).hexdigest(),
            }
        )

    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                MODULE.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "repository": "/opt/pio",
        "production_head": MODULE.EXPECTED_PRODUCTION_HEAD,
        "path": str(MODULE.STATE_READER_PATH),
        "expected_base_blob": "0" * 40,
        "current_blob": MODULE.EXPECTED_CURRENT_BLOB,
        "expected_current_blob": MODULE.EXPECTED_CURRENT_BLOB,
        "target_blob": "1" * 40,
        "merge_file_returncode": 2,
        "conflict_count": 2,
        "conflicts": conflicts,
        "scoped_to_conflicts_only": True,
        "production_file_modified": False,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    return {
        **identity,
        "evidence_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def test_sealed_conflict_hunk_evidence_validates():
    evidence = _synthetic_evidence()

    MODULE.validate_conflict_hunk_evidence(
        json.loads(json.dumps(evidence))
    )

    assert evidence["conflict_count"] == 2
    assert evidence["scoped_to_conflicts_only"] is True
    assert evidence["production_file_modified"] is False
    assert evidence["mutation_authorized"] is False


def test_tampered_conflict_lines_fail_digest_validation():
    evidence = _synthetic_evidence()
    tampered = copy.deepcopy(evidence)
    tampered["conflicts"][0]["production_local"]["lines"][0] = "changed"

    try:
        MODULE.validate_conflict_hunk_evidence(tampered)
    except ValueError as exc:
        assert "section digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered conflict content must fail closed")


def test_rehashed_evidence_cannot_authorize_mutation():
    evidence = _synthetic_evidence()
    evidence["mutation_authorized"] = True
    identity = {
        field: evidence[field]
        for field in MODULE.IDENTITY_FIELDS
    }
    evidence["evidence_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_conflict_hunk_evidence(evidence)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_source_has_only_temp_file_writes_and_no_production_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert source.count(".write_bytes(") == 3
    assert "current_path.write_bytes(current)" in source
    assert "base_path.write_bytes(base)" in source
    assert "target_path.write_bytes(target)" in source
    assert "TemporaryDirectory" in source

    assert "write_text(" not in source
    assert "shutil" not in source
    assert "copy2(" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert "systemctl" not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
