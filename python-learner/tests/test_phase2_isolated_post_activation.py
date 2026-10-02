from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_post_activation.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_post_activation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


POOLS = (
    "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ",
    "DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf",
)


def install_units(tmp_path: Path) -> Path:
    destination = tmp_path / "systemd"
    destination.mkdir()
    for name in MODULE.INSTALL.UNIT_CONTRACT:
        shutil.copy2(
            ROOT / "deploy" / "systemd" / name,
            destination / name,
        )
    return destination


def data_root(tmp_path: Path, *, include_second=True) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    (root / "phase2-add-detector-state.json").write_text(
        json.dumps(
            {
                "cursors": {POOLS[0]: "sig-a", POOLS[1]: "sig-b"},
                "processed": [],
                "pending": {"pending-id": {"pool": POOLS[0]}},
            }
        ),
        encoding="utf-8",
    )
    cache = root / "phase2-prestate-cache.db"
    with sqlite3.connect(cache) as conn:
        conn.execute(
            """
            CREATE TABLE prestate_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                capture_slot_start INTEGER NOT NULL,
                capture_slot_end INTEGER NOT NULL,
                active_bin_id INTEGER NOT NULL,
                bin_array_address TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        pools = POOLS if include_second else POOLS[:1]
        for index, pool in enumerate(pools):
            conn.execute(
                """
                INSERT INTO prestate_snapshots(
                    observed_at, pool_address,
                    capture_slot_start, capture_slot_end,
                    active_bin_id, bin_array_address, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, '{}')
                """,
                (
                    "2026-10-02T12:00:00+00:00",
                    pool,
                    100 + index,
                    100 + index,
                    10 + index,
                    f"array-{index}",
                ),
            )
    return root


def bypass_runtime(monkeypatch, tmp_path: Path):
    current = tmp_path / "runtime" / "current"
    current.parent.mkdir()
    monkeypatch.setattr(
        MODULE.INSTALL,
        "_validate_runtime",
        lambda _root: current,
    )


def systemctl_runner(*, active=(), enabled=()):
    active = set(active)
    enabled = set(enabled)

    def runner(command, **kwargs):
        action, unit = command[1], command[2]
        if action == "is-active":
            value = "active" if unit in active else "inactive"
            code = 0 if unit in active else 3
        elif action == "is-enabled":
            value = "enabled" if unit in enabled else "disabled"
            code = 0 if unit in enabled else 1
        else:
            raise AssertionError(action)
        return subprocess.CompletedProcess(
            command,
            code,
            stdout=value + "\n",
            stderr="",
        )
    return runner


def ready_states():
    streams = {
        f"pio-phase2-isolated-prestate-stream@{pool}.service"
        for pool in POOLS
    }
    active = {MODULE.DETECTOR_UNIT, *streams}
    enabled = {MODULE.DETECTOR_UNIT}
    return active, enabled


def test_post_activation_health_requires_live_topology_and_prestates(
    tmp_path,
    monkeypatch,
):
    destination = install_units(tmp_path)
    root = data_root(tmp_path)
    bypass_runtime(monkeypatch, tmp_path)
    active, enabled = ready_states()

    report = MODULE.inspect_post_activation(
        runtime_root=tmp_path / "runtime",
        unit_destination=destination,
        data_root=root,
        runner=systemctl_runner(active=active, enabled=enabled),
    )

    assert report.health_ready_for_evidence_timer is True
    assert report.runtime_ready is True
    assert report.installed_units_exact is True
    assert report.detector_pools == tuple(sorted(POOLS))
    assert report.detector_active_enabled is True
    assert report.streams_active is True
    assert report.detector_pending_count == 1
    assert report.evidence_timer_inactive_disabled is True
    assert report.legacy_exclusive_units_inactive_disabled is True
    assert all(row.row_present for row in report.prestates)
    assert all(row.single_context for row in report.prestates)
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.service_control_performed is False


def test_post_activation_health_rejects_missing_pool_baseline(
    tmp_path,
    monkeypatch,
):
    destination = install_units(tmp_path)
    root = data_root(tmp_path, include_second=False)
    bypass_runtime(monkeypatch, tmp_path)
    active, enabled = ready_states()

    report = MODULE.inspect_post_activation(
        runtime_root=tmp_path / "runtime",
        unit_destination=destination,
        data_root=root,
        runner=systemctl_runner(active=active, enabled=enabled),
    )

    assert report.health_ready_for_evidence_timer is False
    missing = [row for row in report.prestates if not row.row_present]
    assert [row.pool_address for row in missing] == [POOLS[1]]


def test_post_activation_health_rejects_legacy_competing_timer(
    tmp_path,
    monkeypatch,
):
    destination = install_units(tmp_path)
    root = data_root(tmp_path)
    bypass_runtime(monkeypatch, tmp_path)
    active, enabled = ready_states()
    active.add("pio-phase2-position-observer.timer")
    enabled.add("pio-phase2-position-observer.timer")

    report = MODULE.inspect_post_activation(
        runtime_root=tmp_path / "runtime",
        unit_destination=destination,
        data_root=root,
        runner=systemctl_runner(active=active, enabled=enabled),
    )

    assert report.health_ready_for_evidence_timer is False
    assert report.legacy_exclusive_units_inactive_disabled is False


def test_post_activation_health_requires_evidence_timer_still_off(
    tmp_path,
    monkeypatch,
):
    destination = install_units(tmp_path)
    root = data_root(tmp_path)
    bypass_runtime(monkeypatch, tmp_path)
    active, enabled = ready_states()
    active.add(MODULE.EVIDENCE_TIMER)
    enabled.add(MODULE.EVIDENCE_TIMER)

    report = MODULE.inspect_post_activation(
        runtime_root=tmp_path / "runtime",
        unit_destination=destination,
        data_root=root,
        runner=systemctl_runner(active=active, enabled=enabled),
    )

    assert report.health_ready_for_evidence_timer is False
    assert report.evidence_timer_inactive_disabled is False
