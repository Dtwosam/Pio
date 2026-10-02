from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DETECTOR = ROOT / "scripts" / "phase2-add-detector.py"
UNIT = (
    ROOT
    / "deploy"
    / "systemd"
    / "pio-phase2-isolated-add-detector.service"
)


def test_detector_runtime_paths_keep_legacy_defaults_and_allow_isolation():
    source = DETECTOR.read_text(encoding="utf-8")

    assert 'os.getenv("PIO_PHASE2_RUNTIME_ROOT", "/opt/pio")' in source
    assert '"PIO_PHASE2_EXECUTOR"' in source
    assert '"PIO_PHASE2_PIO_CLI"' in source
    assert '"PIO_PHASE2_DETECTOR_STATE_PATH"' in source
    assert '"PIO_PHASE2_PRESTATE_CACHE_DB"' in source
    assert '"/opt/pio/data/phase2-add-detector-state.json"' in source
    assert '"/opt/pio/data/phase2-prestate-cache.db"' in source


def test_isolated_add_detector_unit_uses_isolated_runtime_and_existing_data():
    source = UNIT.read_text(encoding="utf-8")

    assert "Conflicts=pio-phase2-add-detector.service" in source
    assert (
        "After=network-online.target "
        "pio-phase2-isolated-prestate-stream.service"
    ) in source
    assert (
        "WorkingDirectory=/opt/pio-phase2-runtime/current"
        in source
    )
    assert (
        "PIO_PHASE2_EXECUTOR=/opt/pio-phase2-runtime/current/"
        "rust-executor/target/release/meteora-executor"
    ) in source
    assert (
        "PIO_PHASE2_DETECTOR_STATE_PATH=/opt/pio/data/"
        "phase2-add-detector-state.json"
    ) in source
    assert (
        "PIO_PHASE2_PRESTATE_CACHE_DB=/opt/pio/data/"
        "phase2-prestate-cache.db"
    ) in source
    assert (
        "ExecStart=/opt/pio/python-learner/.venv/bin/python "
        "/opt/pio-phase2-runtime/current/scripts/phase2-add-detector.py"
    ) in source
    assert "Restart=on-failure" in source
    assert "Restart=always" not in source
    assert "ReadOnlyPaths=/opt/pio-phase2-runtime/current" in source
    assert "ReadWritePaths=/opt/pio/data" in source


def test_isolated_add_detector_unit_does_not_embed_rpc_or_secret_values():
    source = UNIT.read_text(encoding="utf-8")

    assert "SOLANA_RPC_URL=" not in source
    assert "api-key=" not in source
    assert "EnvironmentFile=/etc/pio/pio.env" in source
