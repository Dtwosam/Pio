import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "snapshot_phase2_production_sources.py"
SPEC = importlib.util.spec_from_file_location(
    "snapshot_phase2_production_sources",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
capture_production_sources = MODULE.capture_production_sources


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_capture_copies_exact_files_and_writes_provenance(tmp_path):
    source_root = tmp_path / "production"
    source_root.mkdir()
    detector = source_root / "phase2-add-detector.py"
    watcher = source_root / "phase2-prestate-watch.py"
    detector_unit = source_root / "pio-phase2-add-detector.service"
    watcher_unit = source_root / "pio-phase2-prestate-watch.service"

    detector.write_text("detector-local-fix\n", encoding="utf-8")
    watcher.write_text("watcher-local-fix\n", encoding="utf-8")
    detector_unit.write_text("[Service]\nExecStart=detector\n", encoding="utf-8")
    watcher_unit.write_text("[Service]\nExecStart=watcher\n", encoding="utf-8")

    before = {
        path: path.read_bytes()
        for path in (detector, watcher, detector_unit, watcher_unit)
    }
    output = tmp_path / "snapshot"
    result = capture_production_sources(
        source_specs=(
            f"detector={detector}",
            f"watcher={watcher}",
            f"detector-unit={detector_unit}",
            f"watcher-unit={watcher_unit}",
        ),
        output_directory=output,
        captured_at="2026-09-26T15:30:00+00:00",
    )

    assert result.format_version == 1
    assert result.captured_at == "2026-09-26T15:30:00+00:00"
    assert len(result.sources) == 4
    by_label = {item.label: item for item in result.sources}
    assert by_label["detector"].sha256 == sha256(detector)
    assert (
        output / by_label["detector"].captured_path
    ).read_bytes() == detector.read_bytes()

    manifest = json.loads(
        (output / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["format_version"] == 1
    assert {
        item["label"] for item in manifest["sources"]
    } == {
        "detector",
        "watcher",
        "detector-unit",
        "watcher-unit",
    }

    for path, original in before.items():
        assert path.read_bytes() == original


def test_capture_refuses_existing_output_directory(tmp_path):
    source = tmp_path / "detector.py"
    source.write_text("x\n", encoding="utf-8")
    output = tmp_path / "snapshot"
    output.mkdir()

    with pytest.raises(ValueError, match="already exists"):
        capture_production_sources(
            source_specs=(f"detector={source}",),
            output_directory=output,
        )


def test_capture_refuses_symlink_source(tmp_path):
    real = tmp_path / "real.py"
    real.write_text("x\n", encoding="utf-8")
    link = tmp_path / "detector.py"
    link.symlink_to(real)

    with pytest.raises(ValueError, match="not a symlink"):
        capture_production_sources(
            source_specs=(f"detector={link}",),
            output_directory=tmp_path / "snapshot",
        )


@pytest.mark.parametrize(
    "source_spec, expected",
    [
        ("detector=relative.py", "must be absolute"),
        ("bad label=/tmp/example", "source label"),
        ("missing-separator", "LABEL=/absolute/path"),
    ],
)
def test_capture_validates_source_spec(tmp_path, source_spec, expected):
    with pytest.raises(ValueError, match=expected):
        capture_production_sources(
            source_specs=(source_spec,),
            output_directory=tmp_path / "snapshot",
        )


def test_capture_refuses_duplicate_labels(tmp_path):
    first = tmp_path / "one.py"
    second = tmp_path / "two.py"
    first.write_text("1\n", encoding="utf-8")
    second.write_text("2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="labels must be unique"):
        capture_production_sources(
            source_specs=(
                f"detector={first}",
                f"detector={second}",
            ),
            output_directory=tmp_path / "snapshot",
        )
