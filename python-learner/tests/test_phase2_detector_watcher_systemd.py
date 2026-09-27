import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

DETECTOR = ROOT / "scripts" / "phase2-add-detector.py"
WATCHER = ROOT / "scripts" / "phase2-prestate-watch.py"

DETECTOR_UNIT = (
    ROOT
    / "deploy"
    / "systemd"
    / "pio-phase2-add-detector.service"
)
WATCHER_UNIT = (
    ROOT
    / "deploy"
    / "systemd"
    / "pio-phase2-prestate-watch.service"
)


def _subprocess_argument_fragments(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    fragments = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        direct_subprocess = (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "run"
        )

        wrapper_run = (
            isinstance(node.func, ast.Name)
            and node.func.id == "run"
        )

        if not direct_subprocess and not wrapper_run:
            continue

        fragment = ast.get_source_segment(source, node)
        if fragment:
            fragments.append(fragment)

    return fragments


def test_detector_failure_does_not_stop_watcher():
    detector = DETECTOR_UNIT.read_text(encoding="utf-8")
    watcher = WATCHER_UNIT.read_text(encoding="utf-8")

    assert (
        "ExecStart=/opt/pio/python-learner/.venv/bin/python "
        "/opt/pio/scripts/phase2-add-detector.py"
        in detector
    )
    assert (
        "ExecStart=/opt/pio/python-learner/.venv/bin/python "
        "/opt/pio/scripts/phase2-prestate-watch.py"
        in watcher
    )

    assert "Restart=always" in detector
    assert "Restart=always" in watcher

    # Ordering the detector after the watcher is allowed. Coupling their
    # lifecycle is not: detector failure must not stop or restart the watcher.
    assert "Requires=pio-phase2-prestate-watch.service" not in detector
    assert "PartOf=pio-phase2-prestate-watch.service" not in detector
    assert "BindsTo=pio-phase2-prestate-watch.service" not in detector

    assert "pio-phase2-add-detector.service" not in watcher


def test_rpc_url_not_exposed_in_process_arguments():
    detector_source = DETECTOR.read_text(encoding="utf-8")
    watcher_source = WATCHER.read_text(encoding="utf-8")

    # Production Rust commands intentionally use the env-only variants.
    assert '"inspect-transaction-events-env"' in detector_source
    assert '"inspect-pool-env"' in watcher_source

    for fragment in (
        _subprocess_argument_fragments(DETECTOR)
        + _subprocess_argument_fragments(WATCHER)
    ):
        assert "SOLANA_RPC_URL" not in fragment
        assert "RPC," not in fragment
        assert "RPC)" not in fragment

    detector_unit = DETECTOR_UNIT.read_text(encoding="utf-8")
    watcher_unit = WATCHER_UNIT.read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/pio/pio.env" in detector_unit
    assert "EnvironmentFile=/etc/pio/pio.env" in watcher_unit

    for unit in (detector_unit, watcher_unit):
        exec_lines = [
            line
            for line in unit.splitlines()
            if line.startswith("ExecStart=")
        ]
        assert len(exec_lines) == 1

        exec_line = exec_lines[0]

        assert "SOLANA_RPC_URL" not in exec_line
        assert "http://" not in exec_line
        assert "https://" not in exec_line
