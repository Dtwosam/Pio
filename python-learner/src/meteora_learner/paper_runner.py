from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Sequence

from .paper_account import (
    close_paper_position,
    mark_paper_position,
    paper_account_snapshot,
    paper_position_snapshot,
    rebalance_paper_position,
)
from .paper_cycle import apply_paper_observation
from .paper_policy import evaluate_paper_position_policy
from .position_policy import PositionManagementConfig
from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class PaperBatchObservation:
    position_id: str
    active_bin_id: int
    holding_observations: int
    mark_quote: float
    fee_delta_quote: float = 0.0
    reward_delta_quote: float = 0.0
    pool_safe: bool = True
    emergency_exit: bool = False
    estimated_exit_cost_quote: float = 0.0
    rebalance_cost_quote: float | None = None

    def __post_init__(self) -> None:
        if not self.position_id.strip():
            raise ValueError("position_id is required")
        if self.holding_observations < 0:
            raise ValueError("holding_observations cannot be negative")
        for name in (
            "mark_quote",
            "fee_delta_quote",
            "reward_delta_quote",
            "estimated_exit_cost_quote",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.rebalance_cost_quote is not None and self.rebalance_cost_quote < 0:
            raise ValueError("rebalance_cost_quote cannot be negative")


@dataclass(frozen=True)
class PaperBatchItemResult:
    position_id: str
    status: str
    executed_action: str | None
    recovered: bool
    error: str | None
    result: dict[str, Any] | None


@dataclass(frozen=True)
class PaperBatchRunReport:
    run_id: str
    observed_at: str
    status: str
    items_total: int
    items_applied: int
    items_skipped: int
    items_failed: int
    reused_existing_run: bool
    items: tuple[PaperBatchItemResult, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _payload(
    observation: PaperBatchObservation,
    config: PositionManagementConfig,
) -> str:
    return json.dumps(
        {
            "observation": asdict(observation),
            "config": asdict(config),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _recenter_same_width(
    *,
    active_bin_id: int,
    min_bin_id: int,
    max_bin_id: int,
) -> tuple[int, int]:
    width = max_bin_id - min_bin_id
    left = width // 2
    new_min = active_bin_id - left
    return new_min, new_min + width


def _event_types(storage: Storage, prefix: str) -> set[str]:
    keys = (
        f"{prefix}:mark",
        f"{prefix}:rebalance",
        f"{prefix}:exit",
    )
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT event_type
            FROM paper_events
            WHERE event_key IN (?, ?, ?)
            """,
            keys,
        ).fetchall()
    return {str(row[0]) for row in rows}


def _recovered_summary(
    storage: Storage,
    *,
    observation: PaperBatchObservation,
    action: str,
) -> dict[str, Any]:
    position = paper_position_snapshot(
        storage,
        position_id=observation.position_id,
    )
    account = paper_account_snapshot(
        storage,
        account_id=position.account_id,
    )
    return {
        "position_id": observation.position_id,
        "executed_action": action,
        "position_after": position.to_record(),
        "account_after": account.to_record(),
    }


def _continue_after_existing_mark(
    storage: Storage,
    *,
    prefix: str,
    observed_at: str,
    observation: PaperBatchObservation,
    config: PositionManagementConfig,
) -> tuple[str, dict[str, Any]]:
    position = paper_position_snapshot(
        storage,
        position_id=observation.position_id,
    )
    if position.status != "OPEN":
        return "EXIT_RECOVERED", _recovered_summary(
            storage,
            observation=observation,
            action="EXIT_RECOVERED",
        )

    policy = evaluate_paper_position_policy(
        storage,
        position_id=observation.position_id,
        active_bin_id=observation.active_bin_id,
        holding_observations=observation.holding_observations,
        pool_safe=observation.pool_safe,
        estimated_exit_cost_quote=observation.estimated_exit_cost_quote,
        emergency_exit=observation.emergency_exit,
        config=config,
    )

    action = policy.decision.action
    if action == "EXIT":
        close_paper_position(
            storage,
            event_key=f"{prefix}:exit",
            position_id=observation.position_id,
            final_mark_quote=observation.mark_quote,
            exit_cost_quote=observation.estimated_exit_cost_quote,
            event_time=observed_at,
        )
        executed = "EXIT_RECOVERED"
    elif action == "REBALANCE":
        if observation.rebalance_cost_quote is None:
            executed = "REBALANCE_PENDING_COST"
        else:
            new_min, new_max = _recenter_same_width(
                active_bin_id=observation.active_bin_id,
                min_bin_id=position.min_bin_id,
                max_bin_id=position.max_bin_id,
            )
            rebalance_paper_position(
                storage,
                event_key=f"{prefix}:rebalance",
                position_id=observation.position_id,
                new_min_bin_id=new_min,
                new_max_bin_id=new_max,
                new_mark_quote=observation.mark_quote,
                rebalance_cost_quote=observation.rebalance_cost_quote,
                event_time=observed_at,
            )
            executed = "REBALANCE_RECOVERED"
    else:
        executed = "HOLD_RECOVERED"

    return executed, _recovered_summary(
        storage,
        observation=observation,
        action=executed,
    )


def _process_one(
    storage: Storage,
    *,
    run_id: str,
    observed_at: str,
    observation: PaperBatchObservation,
    config: PositionManagementConfig,
) -> tuple[str, bool, dict[str, Any]]:
    prefix = f"paper-run:{run_id}:{observation.position_id}"
    events = _event_types(storage, prefix)

    if "EXIT" in events:
        return (
            "EXIT_RECOVERED",
            True,
            _recovered_summary(
                storage,
                observation=observation,
                action="EXIT_RECOVERED",
            ),
        )
    if "REBALANCE" in events:
        return (
            "REBALANCE_RECOVERED",
            True,
            _recovered_summary(
                storage,
                observation=observation,
                action="REBALANCE_RECOVERED",
            ),
        )
    if "MARK" in events:
        action, result = _continue_after_existing_mark(
            storage,
            prefix=prefix,
            observed_at=observed_at,
            observation=observation,
            config=config,
        )
        return action, True, result

    result = apply_paper_observation(
        storage,
        event_key_prefix=prefix,
        position_id=observation.position_id,
        active_bin_id=observation.active_bin_id,
        holding_observations=observation.holding_observations,
        mark_quote=observation.mark_quote,
        fee_delta_quote=observation.fee_delta_quote,
        reward_delta_quote=observation.reward_delta_quote,
        pool_safe=observation.pool_safe,
        emergency_exit=observation.emergency_exit,
        estimated_exit_cost_quote=observation.estimated_exit_cost_quote,
        rebalance_cost_quote=observation.rebalance_cost_quote,
        event_time=observed_at,
        config=config,
    )
    return result.executed_action, False, result.to_record()


def _load_report(
    storage: Storage,
    *,
    run_id: str,
    reused_existing_run: bool,
) -> PaperBatchRunReport:
    with storage.connect() as conn:
        run = conn.execute(
            """
            SELECT observed_at, status, items_total, items_applied,
                   items_skipped, items_failed
            FROM paper_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if run is None:
            raise RuntimeError("paper run disappeared")
        rows = conn.execute(
            """
            SELECT position_id, status, result_json, error
            FROM paper_run_items
            WHERE run_id = ?
            ORDER BY position_id ASC
            """,
            (run_id,),
        ).fetchall()

    items: list[PaperBatchItemResult] = []
    for row in rows:
        result = json.loads(row[2]) if row[2] else None
        items.append(
            PaperBatchItemResult(
                position_id=str(row[0]),
                status=str(row[1]),
                executed_action=(
                    str(result.get("executed_action"))
                    if isinstance(result, dict)
                    and result.get("executed_action") is not None
                    else None
                ),
                recovered=(
                    bool(result.get("recovered", False))
                    if isinstance(result, dict)
                    else False
                ),
                error=str(row[3]) if row[3] is not None else None,
                result=result,
            )
        )

    return PaperBatchRunReport(
        run_id=run_id,
        observed_at=str(run[0]),
        status=str(run[1]),
        items_total=int(run[2]),
        items_applied=int(run[3]),
        items_skipped=int(run[4]),
        items_failed=int(run[5]),
        reused_existing_run=reused_existing_run,
        items=tuple(items),
    )


def run_paper_observation_batch(
    storage: Storage,
    *,
    run_id: str,
    observed_at: str,
    observations: Sequence[PaperBatchObservation],
    config: PositionManagementConfig = PositionManagementConfig(),
    retry_failed: bool = False,
) -> PaperBatchRunReport:
    """
    Apply one deterministic observation batch across paper positions.

    A repeated run_id with identical inputs is idempotent. Pending items recover
    from already-written paper events before applying any mutation again.
    """
    if not run_id.strip():
        raise ValueError("run_id is required")
    if not observed_at.strip():
        raise ValueError("observed_at is required")
    if not observations:
        raise ValueError("at least one paper observation is required")

    ordered = tuple(sorted(observations, key=lambda item: item.position_id))
    if len({item.position_id for item in ordered}) != len(ordered):
        raise ValueError("paper batch contains duplicate position_id values")

    reused = False
    with storage.connect() as conn:
        run = conn.execute(
            """
            SELECT observed_at, status, items_total
            FROM paper_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

        if run is None:
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO paper_runs(
                    run_id, observed_at, started_at, status, items_total
                ) VALUES (?, ?, ?, 'RUNNING', ?)
                """,
                (run_id, observed_at, now, len(ordered)),
            )
            for item in ordered:
                prefix = f"paper-run:{run_id}:{item.position_id}"
                conn.execute(
                    """
                    INSERT INTO paper_run_items(
                        run_id, position_id, event_key_prefix,
                        status, input_json
                    ) VALUES (?, ?, ?, 'PENDING', ?)
                    """,
                    (
                        run_id,
                        item.position_id,
                        prefix,
                        _payload(item, config),
                    ),
                )
        else:
            reused = True
            if str(run[0]) != observed_at:
                raise ValueError("existing paper run observed_at does not match")
            if int(run[2]) != len(ordered):
                raise ValueError("existing paper run item count does not match")
            stored = conn.execute(
                """
                SELECT position_id, input_json
                FROM paper_run_items
                WHERE run_id = ?
                ORDER BY position_id ASC
                """,
                (run_id,),
            ).fetchall()
            expected = [
                (item.position_id, _payload(item, config))
                for item in ordered
            ]
            actual = [(str(row[0]), str(row[1])) for row in stored]
            if actual != expected:
                raise ValueError("existing paper run inputs do not match")
            if str(run[1]) == "COMPLETE":
                return _load_report(
                    storage,
                    run_id=run_id,
                    reused_existing_run=True,
                )

    by_id = {item.position_id: item for item in ordered}
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT position_id, status
            FROM paper_run_items
            WHERE run_id = ?
            ORDER BY position_id ASC
            """,
            (run_id,),
        ).fetchall()

    for position_id, status in rows:
        position_id = str(position_id)
        status = str(status)
        if status in {"APPLIED", "SKIPPED"}:
            continue
        if status == "FAILED" and not retry_failed:
            continue

        item = by_id[position_id]
        try:
            action, recovered, result = _process_one(
                storage,
                run_id=run_id,
                observed_at=observed_at,
                observation=item,
                config=config,
            )
            stored_result = {
                "executed_action": action,
                "recovered": recovered,
                "detail": result,
            }
            with storage.connect() as conn:
                conn.execute(
                    """
                    UPDATE paper_run_items
                    SET status = 'APPLIED', result_json = ?, error = NULL
                    WHERE run_id = ? AND position_id = ?
                    """,
                    (
                        json.dumps(stored_result, separators=(",", ":")),
                        run_id,
                        position_id,
                    ),
                )
        except Exception as exc:
            with storage.connect() as conn:
                conn.execute(
                    """
                    UPDATE paper_run_items
                    SET status = 'FAILED', error = ?
                    WHERE run_id = ? AND position_id = ?
                    """,
                    (str(exc)[:2000], run_id, position_id),
                )

    with storage.connect() as conn:
        counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'APPLIED' THEN 1 ELSE 0 END),
                SUM(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END),
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END),
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END)
            FROM paper_run_items
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        applied = int(counts[0] or 0)
        skipped = int(counts[1] or 0)
        failed = int(counts[2] or 0)
        pending = int(counts[3] or 0)
        final_status = "COMPLETE" if failed == 0 and pending == 0 else "FAILED"
        conn.execute(
            """
            UPDATE paper_runs
            SET finished_at = ?, status = ?, items_applied = ?,
                items_skipped = ?, items_failed = ?,
                error = ?
            WHERE run_id = ?
            """,
            (
                utc_now_iso(),
                final_status,
                applied,
                skipped,
                failed,
                None if final_status == "COMPLETE" else "one or more items failed",
                run_id,
            ),
        )

    return _load_report(
        storage,
        run_id=run_id,
        reused_existing_run=reused,
    )
