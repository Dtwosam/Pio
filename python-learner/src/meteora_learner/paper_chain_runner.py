from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Sequence

from .paper_chain import apply_chain_paper_observation
from .paper_runner import PaperBatchItemResult, PaperBatchRunReport
from .position_policy import PositionManagementConfig
from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class PaperChainBatchItem:
    position_id: str
    token_y_quote_per_atomic: float
    quote_max_age_seconds: int = 300
    pool_safe: bool = True
    emergency_exit: bool = False
    estimated_exit_cost_quote: float = 0.0
    rebalance_cost_quote: float | None = None

    def __post_init__(self) -> None:
        if not self.position_id.strip():
            raise ValueError("position_id is required")
        if self.token_y_quote_per_atomic <= 0:
            raise ValueError("token_y_quote_per_atomic must be positive")
        if self.quote_max_age_seconds < 0:
            raise ValueError("quote_max_age_seconds cannot be negative")
        if self.estimated_exit_cost_quote < 0:
            raise ValueError("estimated_exit_cost_quote cannot be negative")
        if self.rebalance_cost_quote is not None and self.rebalance_cost_quote < 0:
            raise ValueError("rebalance_cost_quote cannot be negative")


def _payload(
    item: PaperChainBatchItem,
    config: PositionManagementConfig,
) -> str:
    return json.dumps(
        {
            "source": "CHAIN_VALUATION_V1",
            "item": asdict(item),
            "config": asdict(config),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


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
            raise RuntimeError("paper chain run disappeared")
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
        result = json.loads(str(row[2])) if row[2] is not None else None
        items.append(
            PaperBatchItemResult(
                position_id=str(row[0]),
                status=str(row[1]),
                executed_action=(
                    str(result["executed_action"])
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


def run_chain_paper_batch(
    storage: Storage,
    *,
    run_id: str,
    observed_at: str,
    items: Sequence[PaperChainBatchItem],
    config: PositionManagementConfig = PositionManagementConfig(),
    retry_failed: bool = False,
) -> PaperBatchRunReport:
    """
    Value and manage multiple bound paper positions from one chain observation.

    Reusing an identical run_id is safe. Each underlying chain valuation is also
    idempotent, so recovery cannot double-count fees/rewards or repeat an exit.
    """
    if not run_id.strip():
        raise ValueError("run_id is required")
    if not observed_at.strip():
        raise ValueError("observed_at is required")
    if not items:
        raise ValueError("at least one chain paper item is required")

    ordered = tuple(sorted(items, key=lambda item: item.position_id))
    if len({item.position_id for item in ordered}) != len(ordered):
        raise ValueError("chain paper batch contains duplicate position_id values")

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
                        f"chain-run:{run_id}:{item.position_id}",
                        _payload(item, config),
                    ),
                )
        else:
            reused = True
            if str(run[0]) != observed_at:
                raise ValueError("existing chain paper run observed_at does not match")
            if int(run[2]) != len(ordered):
                raise ValueError("existing chain paper run item count does not match")
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
                raise ValueError("existing chain paper run inputs do not match")
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
            result = apply_chain_paper_observation(
                storage,
                position_id=position_id,
                observed_at=observed_at,
                token_y_quote_per_atomic=item.token_y_quote_per_atomic,
                pool_safe=item.pool_safe,
                quote_max_age_seconds=item.quote_max_age_seconds,
                estimated_exit_cost_quote=item.estimated_exit_cost_quote,
                rebalance_cost_quote=item.rebalance_cost_quote,
                emergency_exit=item.emergency_exit,
                config=config,
            )
            stored_result = {
                "executed_action": result.executed_action,
                "recovered": bool(
                    result.detail.get("recovered", False)
                    if isinstance(result.detail, dict)
                    else False
                ),
                "detail": result.to_record(),
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
