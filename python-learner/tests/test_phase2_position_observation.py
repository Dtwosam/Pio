import json
import subprocess

import pytest

from meteora_learner.phase2_position_observation import (
    collect_phase2_position_observations,
)
from meteora_learner.storage import Storage


POOL = "pool"
POSITIONS = ("position-a", "position-b")


def _snapshot(position, pool=POOL, capture_slot=450700000):
    return {
        "position_address": position,
        "capture_slot_start": capture_slot,
        "capture_slot_end": capture_slot,
        "pool_address": pool,
        "owner": "owner",
        "fee_owner": "fee-owner",
        "lower_bin_id": -1,
        "upper_bin_id": 1,
        "total_x_amount": "10",
        "total_y_amount": "20",
        "fee_x": "1",
        "fee_y": "2",
        "reward_one": "3",
        "reward_two": "4",
        "last_updated_at": 100,
        "total_claimed_fee_x_amount": "0",
        "total_claimed_fee_y_amount": "0",
        "supports_limit_order": False,
        "reward_mints": ["reward-a", "reward-b"],
        "bins": [
            {
                "bin_id": 0,
                "price": "1",
                "bin_x_amount": "10",
                "bin_y_amount": "20",
                "bin_liquidity": "30",
                "bin_fee_x_per_token_stored": "4",
                "bin_fee_y_per_token_stored": "5",
                "bin_reward_per_token_stored": ["6", "7"],
                "position_liquidity": "8",
                "position_x_amount": "9",
                "position_y_amount": "10",
                "position_fee_x_amount": "11",
                "position_fee_y_amount": "12",
                "position_reward_amounts": ["13", "14"],
            }
        ],
    }


def test_observer_uses_env_only_executor_commands_and_saves_all_positions(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        assert "https://secret-rpc.example" not in command
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 2,
                "positions_returned": 2,
                "truncated": False,
                "positions": [
                    {"position_address": POSITIONS[0]},
                    {"position_address": POSITIONS[1]},
                ],
            }
        else:
            payload = _snapshot(command[2])
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    result = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        observed_at="2026-09-26T15:00:00+00:00",
        runner=runner,
    )

    assert result.positions_selected == 2
    assert result.snapshots_saved == 2
    assert result.bins_saved == 2
    assert result.failures == 0
    assert result.reconciliation_progress is not None
    assert result.reconciliation_progress.positions_seen == 2
    assert result.reconciliation_progress.amount_bins_checked >= 0
    assert result.reconciliation_progress.fee_intervals_seen == 0
    assert result.reconciliation_progress.reward_intervals_seen == 0
    assert commands[0][1] == "discover-pool-positions-env"
    assert commands[0][3] == "5000"
    assert [item[1] for item in commands[1:]] == [
        "inspect-position-env",
        "inspect-position-env",
    ]
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT position_address, observed_at FROM chain_position_snapshots ORDER BY position_address"
        ).fetchall()
    assert rows == [
        (POSITIONS[0], "2026-09-26T15:00:00+00:00"),
        (POSITIONS[1], "2026-09-26T15:00:00+00:00"),
    ]


def test_observer_keeps_successful_samples_visible_when_one_position_fails(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "positions_found": 2,
                "positions_returned": 2,
                "truncated": False,
                "positions": [
                    {"position_address": POSITIONS[0]},
                    {"position_address": POSITIONS[1]},
                ],
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[2] == POSITIONS[1]:
            return subprocess.CompletedProcess(command, 1, "", "rpc timeout")
        return subprocess.CompletedProcess(command, 0, json.dumps(_snapshot(command[2])), "")

    result = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        runner=runner,
    )

    assert result.snapshots_saved == 1
    assert result.failures == 1
    assert result.failed_positions == (POSITIONS[1],)
    assert result.failure_details[0].category == "EXECUTOR_FAILED"


def test_observer_refuses_cross_pool_snapshot(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "positions": [{"position_address": POSITIONS[0]}],
                "positions_found": 1,
                "positions_returned": 1,
                "truncated": False,
            }
        else:
            payload = _snapshot(POSITIONS[0], pool="other-pool")
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    result = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        runner=runner,
    )

    assert result.snapshots_saved == 0
    assert result.failed_positions == (POSITIONS[0],)
    assert result.failure_details[0].category == "POOL_MISMATCH"


def test_observer_rotates_to_least_recently_seen_position(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    inspected = []

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 2,
                "positions_returned": 2,
                "truncated": False,
                "positions": [
                    {"position_address": POSITIONS[0]},
                    {"position_address": POSITIONS[1]},
                ],
            }
        else:
            inspected.append(command[2])
            payload = _snapshot(command[2])
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload), stderr=""
        )

    first = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=1,
        observed_at="2026-09-26T15:00:00+00:00",
        runner=runner,
    )
    second = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=1,
        observed_at="2026-09-26T15:15:00+00:00",
        runner=runner,
    )

    assert first.positions_selected == 1
    assert second.positions_selected == 1
    assert inspected == [POSITIONS[0], POSITIONS[1]]


def test_observer_surfaces_discovery_truncation(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 5001,
                "positions_returned": 1,
                "truncated": True,
                "positions": [{"position_address": POSITIONS[0]}],
            }
        else:
            payload = _snapshot(command[2])
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload), stderr=""
        )

    result = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=1,
        runner=runner,
    )

    assert result.discovery_truncated is True
    assert result.positions_found == 5001
    assert result.positions_returned == 1
    assert result.positions_selected == 1



def test_observer_progress_surfaces_repeated_position_intervals(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    inspection_count = 0

    def runner(command, **kwargs):
        nonlocal inspection_count
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 1,
                "positions_returned": 1,
                "truncated": False,
                "positions": [{"position_address": POSITIONS[0]}],
            }
        else:
            inspection_count += 1
            payload = _snapshot(
                POSITIONS[0],
                capture_slot=450700000 + inspection_count,
            )
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload), stderr=""
        )

    first = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=1,
        observed_at="2026-09-26T15:00:00+00:00",
        runner=runner,
    )
    second = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=1,
        observed_at="2026-09-26T15:15:00+00:00",
        runner=runner,
    )

    assert first.reconciliation_progress is not None
    assert first.reconciliation_progress.fee_intervals_seen == 0
    assert second.reconciliation_progress is not None
    assert second.reconciliation_progress.positions_seen == 1
    assert second.reconciliation_progress.fee_intervals_seen == 1
    assert second.reconciliation_progress.reward_intervals_seen == 1



def test_observer_rejects_position_snapshot_without_capture_slots(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 1,
                "positions_returned": 1,
                "truncated": False,
                "positions": [{"position_address": POSITIONS[0]}],
            }
        else:
            payload = _snapshot(POSITIONS[0])
            payload.pop("capture_slot_start")
            payload.pop("capture_slot_end")
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload), stderr=""
        )

    result = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        runner=runner,
    )

    assert result.snapshots_saved == 0
    assert result.failures == 1
    assert result.failed_positions == (POSITIONS[0],)
    assert result.failure_details[0].category == "CAPTURE_PROVENANCE"


def test_observer_rejects_mixed_context_position_snapshot(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 1,
                "positions_returned": 1,
                "truncated": False,
                "positions": [{"position_address": POSITIONS[0]}],
            }
        else:
            payload = _snapshot(POSITIONS[0])
            payload["capture_slot_end"] += 1
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload), stderr=""
        )

    result = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        runner=runner,
    )

    assert result.snapshots_saved == 0
    assert result.failures == 1
    assert result.failed_positions == (POSITIONS[0],)
    assert result.failure_details[0].category == "CAPTURE_PROVENANCE"



def test_observer_failure_details_do_not_echo_executor_stderr(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    secret = "https://user:secret@example.invalid/rpc"

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 1,
                "positions_returned": 1,
                "truncated": False,
                "positions": [{"position_address": POSITIONS[0]}],
            }
            return subprocess.CompletedProcess(
                command, 0, json.dumps(payload), ""
            )
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            f"transport failed for {secret}",
        )

    result = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        runner=runner,
    )

    payload = json.dumps(result.to_record())
    assert result.failure_details[0].category == "EXECUTOR_FAILED"
    assert secret not in payload
    assert "transport failed" not in payload


def test_observer_classifies_executor_timeout(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 1,
                "positions_returned": 1,
                "truncated": False,
                "positions": [{"position_address": POSITIONS[0]}],
            }
            return subprocess.CompletedProcess(
                command, 0, json.dumps(payload), ""
            )
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    result = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        runner=runner,
    )

    assert result.failures == 1
    assert result.failure_details[0].category == "EXECUTOR_TIMEOUT"



def test_failed_never_seen_position_does_not_starve_rotation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    inspected = []
    first_attempt = True

    def runner(command, **kwargs):
        nonlocal first_attempt
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 2,
                "positions_returned": 2,
                "truncated": False,
                "positions": [
                    {"position_address": POSITIONS[0]},
                    {"position_address": POSITIONS[1]},
                ],
            }
            return subprocess.CompletedProcess(
                command, 0, json.dumps(payload), ""
            )

        inspected.append(command[2])
        if command[2] == POSITIONS[0] and first_attempt:
            first_attempt = False
            return subprocess.CompletedProcess(
                command, 1, "", "persistent failure"
            )
        return subprocess.CompletedProcess(
            command, 0, json.dumps(_snapshot(command[2])), ""
        )

    first = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=1,
        observed_at="2026-09-26T15:00:00+00:00",
        runner=runner,
    )
    second = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=1,
        observed_at="2026-09-26T15:15:00+00:00",
        runner=runner,
    )

    assert first.failure_details[0].category == "EXECUTOR_FAILED"
    assert second.snapshots_saved == 1
    assert inspected == [POSITIONS[0], POSITIONS[1]]

    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT position_address, succeeded, failure_category, capture_slot
            FROM phase2_position_observation_attempts
            ORDER BY id
            """
        ).fetchall()
    assert rows == [
        (POSITIONS[0], 0, "EXECUTOR_FAILED", None),
        (POSITIONS[1], 1, None, 450700000),
    ]


def test_attempt_ledger_uses_prior_snapshot_history_for_initial_rotation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    inspected = []

    ingest_payload = _snapshot(POSITIONS[0])
    from meteora_learner.position_ingest import ingest_position_snapshot
    ingest_position_snapshot(
        storage,
        ingest_payload,
        observed_at="2026-09-26T14:45:00+00:00",
    )

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 2,
                "positions_returned": 2,
                "truncated": False,
                "positions": [
                    {"position_address": POSITIONS[0]},
                    {"position_address": POSITIONS[1]},
                ],
            }
        else:
            inspected.append(command[2])
            payload = _snapshot(command[2])
        return subprocess.CompletedProcess(
            command, 0, json.dumps(payload), ""
        )

    result = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=1,
        observed_at="2026-09-26T15:00:00+00:00",
        runner=runner,
    )

    assert result.snapshots_saved == 1
    assert inspected == [POSITIONS[1]]



def test_observer_reserves_revisit_slot_while_exploring_new_positions(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    third = "position-c"
    inspected = []

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 3,
                "positions_returned": 3,
                "truncated": False,
                "positions": [
                    {"position_address": POSITIONS[0]},
                    {"position_address": POSITIONS[1]},
                    {"position_address": third},
                ],
            }
        else:
            inspected.append(command[2])
            payload = _snapshot(command[2])
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload), stderr=""
        )

    first = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=2,
        min_revisit_per_run=1,
        observed_at="2026-09-26T15:00:00+00:00",
        runner=runner,
    )
    second = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=2,
        min_revisit_per_run=1,
        observed_at="2026-09-26T15:15:00+00:00",
        runner=runner,
    )

    assert first.exploration_positions_selected == 2
    assert first.revisit_positions_selected == 0
    assert second.exploration_positions_selected == 1
    assert second.revisit_positions_selected == 1
    assert inspected == [
        POSITIONS[0],
        POSITIONS[1],
        third,
        POSITIONS[0],
    ]
    assert second.reconciliation_progress is not None
    assert second.reconciliation_progress.fee_intervals_seen >= 1
    assert second.reconciliation_progress.reward_intervals_seen >= 1


def test_observer_single_slot_budget_preserves_exploration(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    storage.save_phase2_position_observation_attempt(
        pool_address=POOL,
        position_address=POSITIONS[0],
        attempted_at="2026-09-26T14:45:00+00:00",
        succeeded=True,
        capture_slot=450699999,
    )
    from meteora_learner.position_ingest import ingest_position_snapshot
    ingest_position_snapshot(
        storage,
        _snapshot(POSITIONS[0]),
        observed_at="2026-09-26T14:45:00+00:00",
    )
    inspected = []

    def runner(command, **kwargs):
        if command[1] == "discover-pool-positions-env":
            payload = {
                "pool_address": POOL,
                "positions_found": 2,
                "positions_returned": 2,
                "truncated": False,
                "positions": [
                    {"position_address": POSITIONS[0]},
                    {"position_address": POSITIONS[1]},
                ],
            }
        else:
            inspected.append(command[2])
            payload = _snapshot(command[2])
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload), stderr=""
        )

    report = collect_phase2_position_observations(
        storage,
        pool_address=POOL,
        executor_path="/executor",
        max_positions_per_run=1,
        min_revisit_per_run=1,
        observed_at="2026-09-26T15:00:00+00:00",
        runner=runner,
    )

    assert inspected == [POSITIONS[1]]
    assert report.exploration_positions_selected == 1
    assert report.revisit_positions_selected == 0


def test_observer_revisit_budget_cannot_be_negative(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with pytest.raises(ValueError, match="cannot be negative"):
        collect_phase2_position_observations(
            storage,
            pool_address=POOL,
            executor_path="/executor",
            min_revisit_per_run=-1,
        )
