from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .ml_registry import CHAMPION, PAPER_CHALLENGER, ROLLED_BACK, _to_record
from .paper_performance import PaperPerformanceReport, build_paper_performance
from .phase_promotion import PHASE7, PHASE7_EVIDENCE_TYPE
from .retraining_cycle import retraining_cycle
from .retraining_workflow import WALK_FORWARD_EVIDENCE_TYPE
from .storage import Storage, utc_now_iso


CONTINUOUS_PAPER_EVIDENCE_TYPE = "CONTINUOUS_PAPER_VALIDATION_V1"
CONTINUOUS_PROMOTION_EVIDENCE_TYPE = "CONTINUOUS_CHAMPION_PROMOTION_V1"
CONTINUOUS_SUPERSEDED_EVIDENCE_TYPE = "CONTINUOUS_CHAMPION_SUPERSEDED_V1"


@dataclass(frozen=True)
class ContinuousChampionCriteria:
    min_challenger_closed_trades: int = 20
    min_incumbent_closed_trades: int = 20
    min_challenger_return_bps: int = 0
    min_challenger_win_rate: float = 0.50
    max_challenger_drawdown_bps: int = 1_500
    max_single_trade_loss_bps: int = 1_000
    min_return_uplift_vs_incumbent_bps: int = 0

    def __post_init__(self) -> None:
        if self.min_challenger_closed_trades <= 0:
            raise ValueError("min_challenger_closed_trades must be positive")
        if self.min_incumbent_closed_trades <= 0:
            raise ValueError("min_incumbent_closed_trades must be positive")
        if not 0.0 <= self.min_challenger_win_rate <= 1.0:
            raise ValueError(
                "min_challenger_win_rate must be between 0 and 1"
            )
        if self.max_challenger_drawdown_bps < 0:
            raise ValueError(
                "max_challenger_drawdown_bps cannot be negative"
            )
        if self.max_single_trade_loss_bps < 0:
            raise ValueError(
                "max_single_trade_loss_bps cannot be negative"
            )


@dataclass(frozen=True)
class ContinuousChampionValidation:
    cycle_id: str
    account_id: str
    phase7_promoted: bool
    incumbent_model_id: str
    incumbent_status: str
    challenger_model_id: str
    challenger_status: str
    dataset_version_matches_cycle: bool
    walk_forward_qualified: bool
    walk_forward_evidence_id: int | None
    incumbent: PaperPerformanceReport
    challenger: PaperPerformanceReport
    return_uplift_vs_incumbent_bps: int | None
    criteria: ContinuousChampionCriteria
    qualified: bool
    policy_actionable: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_continuous_champion(
    storage: Storage,
    *,
    cycle_id: str,
    account_id: str,
    criteria: ContinuousChampionCriteria = ContinuousChampionCriteria(),
) -> ContinuousChampionValidation:
    cycle = retraining_cycle(storage, cycle_id=cycle_id)
    if cycle.challenger_model_id is None:
        raise ValueError("retraining cycle has no attached challenger")

    incumbent_raw = storage.model_registry_entry(
        cycle.champion_model_id
    )
    challenger_raw = storage.model_registry_entry(
        cycle.challenger_model_id
    )
    if incumbent_raw is None:
        raise ValueError("cycle incumbent model no longer exists")
    if challenger_raw is None:
        raise ValueError("cycle challenger model no longer exists")

    incumbent_status = str(incumbent_raw["status"])
    challenger_status = str(challenger_raw["status"])
    dataset_matches = (
        str(challenger_raw["dataset_version"])
        == cycle.target_dataset_version
    )
    phase7_promoted = storage.phase_is_promoted(
        PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
    )
    walk_forward = storage.latest_model_live_evidence(
        cycle.challenger_model_id,
        evidence_type=WALK_FORWARD_EVIDENCE_TYPE,
    )
    walk_forward_qualified = False
    walk_forward_evidence_id = None
    if walk_forward is not None:
        walk_forward_evidence_id = int(walk_forward["id"])
        evidence = walk_forward["evidence"]
        report = evidence.get("report", {})
        walk_forward_qualified = (
            walk_forward.get("status") == "QUALIFIED"
            and evidence.get("cycle_id") == cycle_id
            and evidence.get("model_id")
                == cycle.challenger_model_id
            and evidence.get("dataset_version")
                == cycle.target_dataset_version
            and report.get("walk_forward_qualified") is True
        )

    incumbent = build_paper_performance(
        storage,
        account_id=account_id,
        policy_source="ML_CHAMPION",
        model_id=cycle.champion_model_id,
    )
    challenger = build_paper_performance(
        storage,
        account_id=account_id,
        policy_source="ML_CHALLENGER",
        model_id=cycle.challenger_model_id,
    )

    uplift = None
    if (
        challenger.realized_return_bps is not None
        and incumbent.realized_return_bps is not None
    ):
        uplift = (
            challenger.realized_return_bps
            - incumbent.realized_return_bps
        )

    reasons: list[str] = []
    checks = (
        (
            phase7_promoted,
            "Phase 7 is not persistently promoted",
        ),
        (
            cycle.status in {
                "CHALLENGER_REGISTERED",
                "OFFLINE_QUALIFIED",
                "PAPER_CHALLENGER",
            },
            f"retraining cycle status {cycle.status} is not promotable",
        ),
        (
            incumbent_status == CHAMPION,
            f"incumbent status {incumbent_status} is not CHAMPION",
        ),
        (
            challenger_status == PAPER_CHALLENGER,
            f"challenger status {challenger_status} is not PAPER_CHALLENGER",
        ),
        (
            dataset_matches,
            "challenger dataset_version does not match retraining cycle",
        ),
        (
            walk_forward_qualified,
            "qualified cycle-bound walk-forward evidence is missing",
        ),
        (
            challenger.closed_trades
            >= criteria.min_challenger_closed_trades,
            f"challenger closed_trades {challenger.closed_trades} < required "
            f"{criteria.min_challenger_closed_trades}",
        ),
        (
            incumbent.closed_trades
            >= criteria.min_incumbent_closed_trades,
            f"incumbent closed_trades {incumbent.closed_trades} < required "
            f"{criteria.min_incumbent_closed_trades}",
        ),
        (
            challenger.realized_return_bps is not None
            and challenger.realized_return_bps
            >= criteria.min_challenger_return_bps,
            "challenger realized return is below required minimum",
        ),
        (
            challenger.win_rate is not None
            and challenger.win_rate
            >= criteria.min_challenger_win_rate,
            "challenger win rate is below required minimum",
        ),
        (
            challenger.realized_max_drawdown_bps is not None
            and challenger.realized_max_drawdown_bps
            <= criteria.max_challenger_drawdown_bps,
            "challenger realized drawdown exceeds allowed maximum",
        ),
        (
            challenger.worst_trade_return_bps is not None
            and challenger.worst_trade_return_bps
            >= -criteria.max_single_trade_loss_bps,
            "challenger worst trade exceeds allowed loss",
        ),
        (
            uplift is not None
            and uplift
            >= criteria.min_return_uplift_vs_incumbent_bps,
            "challenger return uplift versus incumbent is below required minimum",
        ),
    )
    reasons.extend(message for passed, message in checks if not passed)

    return ContinuousChampionValidation(
        cycle_id=cycle_id,
        account_id=account_id,
        phase7_promoted=phase7_promoted,
        incumbent_model_id=cycle.champion_model_id,
        incumbent_status=incumbent_status,
        challenger_model_id=cycle.challenger_model_id,
        challenger_status=challenger_status,
        dataset_version_matches_cycle=dataset_matches,
        walk_forward_qualified=walk_forward_qualified,
        walk_forward_evidence_id=walk_forward_evidence_id,
        incumbent=incumbent,
        challenger=challenger,
        return_uplift_vs_incumbent_bps=uplift,
        criteria=criteria,
        qualified=not reasons,
        policy_actionable=False,
        reasons=tuple(reasons),
    )


def promote_continuous_challenger(
    storage: Storage,
    *,
    validation: ContinuousChampionValidation,
    notes: str | None = None,
) -> dict[str, Any]:
    if not validation.qualified:
        raise ValueError(
            "continuous challenger has not passed promotion validation"
        )
    now = utc_now_iso()
    evidence_json = json.dumps(
        validation.to_record(),
        separators=(",", ":"),
    )

    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")

        cycle = conn.execute(
            """
            SELECT status, active_key, champion_model_id,
                   challenger_model_id, target_dataset_version
            FROM continuous_learning_cycles
            WHERE cycle_id = ?
            """,
            (validation.cycle_id,),
        ).fetchone()
        if cycle is None:
            raise ValueError("retraining cycle disappeared")
        if cycle[1] != "ACTIVE":
            raise ValueError("retraining cycle is no longer active")
        if str(cycle[2]) != validation.incumbent_model_id:
            raise ValueError(
                "cycle incumbent does not match validation"
            )
        if str(cycle[3]) != validation.challenger_model_id:
            raise ValueError(
                "cycle challenger does not match validation"
            )

        incumbent = conn.execute(
            """
            SELECT status
            FROM model_registry
            WHERE model_id = ?
            """,
            (validation.incumbent_model_id,),
        ).fetchone()
        challenger = conn.execute(
            """
            SELECT status, dataset_version
            FROM model_registry
            WHERE model_id = ?
            """,
            (validation.challenger_model_id,),
        ).fetchone()
        if incumbent is None or str(incumbent[0]) != CHAMPION:
            raise ValueError(
                "incumbent is no longer the current champion"
            )
        if challenger is None or str(challenger[0]) != PAPER_CHALLENGER:
            raise ValueError(
                "challenger is no longer PAPER_CHALLENGER"
            )
        if str(challenger[1]) != str(cycle[4]):
            raise ValueError(
                "challenger dataset changed from retraining cycle"
            )

        latest_walk_forward = conn.execute(
            """
            SELECT id, status, evidence_json
            FROM model_live_evidence
            WHERE model_id = ?
              AND evidence_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                validation.challenger_model_id,
                WALK_FORWARD_EVIDENCE_TYPE,
            ),
        ).fetchone()
        if latest_walk_forward is None:
            raise ValueError(
                "walk-forward evidence disappeared before champion rotation"
            )
        latest_walk_forward_json = json.loads(
            str(latest_walk_forward[2])
        )
        if (
            int(latest_walk_forward[0])
                != validation.walk_forward_evidence_id
            or str(latest_walk_forward[1]) != "QUALIFIED"
            or latest_walk_forward_json.get("cycle_id")
                != validation.cycle_id
            or latest_walk_forward_json.get("model_id")
                != validation.challenger_model_id
            or latest_walk_forward_json.get("dataset_version")
                != str(cycle[4])
            or latest_walk_forward_json
                .get("report", {})
                .get("walk_forward_qualified") is not True
        ):
            raise ValueError(
                "walk-forward evidence changed before champion rotation"
            )

        phase7 = conn.execute(
            """
            SELECT evidence_type, qualified
            FROM phase_promotion_evidence
            WHERE phase_name = 'PHASE7'
            """
        ).fetchone()
        if (
            phase7 is None
            or str(phase7[0]) != PHASE7_EVIDENCE_TYPE
            or not bool(phase7[1])
        ):
            raise ValueError(
                "Phase 7 promotion changed before champion rotation"
            )

        conn.execute(
            """
            INSERT INTO model_promotion_evidence(
                model_id, created_at, evidence_type,
                qualified, evidence_json
            ) VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(model_id) DO UPDATE SET
                created_at=excluded.created_at,
                evidence_type=excluded.evidence_type,
                qualified=excluded.qualified,
                evidence_json=excluded.evidence_json
            """,
            (
                validation.challenger_model_id,
                now,
                CONTINUOUS_PAPER_EVIDENCE_TYPE,
                evidence_json,
            ),
        )

        retired = conn.execute(
            """
            UPDATE model_registry
            SET status = ?, updated_at = ?,
                notes = COALESCE(?, notes)
            WHERE model_id = ? AND status = ?
            """,
            (
                ROLLED_BACK,
                now,
                (
                    notes
                    or f"superseded by {validation.challenger_model_id}"
                ),
                validation.incumbent_model_id,
                CHAMPION,
            ),
        )
        if retired.rowcount != 1:
            raise ValueError(
                "incumbent changed during champion rotation"
            )

        promoted = conn.execute(
            """
            UPDATE model_registry
            SET status = ?, updated_at = ?,
                notes = COALESCE(?, notes)
            WHERE model_id = ? AND status = ?
            """,
            (
                CHAMPION,
                now,
                notes,
                validation.challenger_model_id,
                PAPER_CHALLENGER,
            ),
        )
        if promoted.rowcount != 1:
            raise ValueError(
                "challenger changed during champion rotation"
            )

        completed = conn.execute(
            """
            UPDATE continuous_learning_cycles
            SET status = 'COMPLETED',
                active_key = NULL,
                updated_at = ?
            WHERE cycle_id = ?
              AND active_key = 'ACTIVE'
            """,
            (now, validation.cycle_id),
        )
        if completed.rowcount != 1:
            raise ValueError(
                "retraining cycle changed during champion rotation"
            )

        conn.execute(
            """
            INSERT INTO model_live_evidence(
                model_id, created_at, evidence_type,
                status, evidence_json
            ) VALUES (?, ?, ?, 'SUPERSEDED', ?)
            """,
            (
                validation.incumbent_model_id,
                now,
                CONTINUOUS_SUPERSEDED_EVIDENCE_TYPE,
                json.dumps(
                    {
                        "cycle_id": validation.cycle_id,
                        "successor_model_id": (
                            validation.challenger_model_id
                        ),
                        "validation": validation.to_record(),
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO model_live_evidence(
                model_id, created_at, evidence_type,
                status, evidence_json
            ) VALUES (?, ?, ?, 'CHAMPION', ?)
            """,
            (
                validation.challenger_model_id,
                now,
                CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
                json.dumps(
                    {
                        "cycle_id": validation.cycle_id,
                        "predecessor_model_id": (
                            validation.incumbent_model_id
                        ),
                        "validation": validation.to_record(),
                    },
                    separators=(",", ":"),
                ),
            ),
        )

    current = storage.current_model_champion()
    if current is None:
        raise RuntimeError("champion rotation left no champion")
    if str(current["model_id"]) != validation.challenger_model_id:
        raise RuntimeError(
            "champion rotation selected an unexpected model"
        )
    return {
        "cycle_id": validation.cycle_id,
        "incumbent_model_id": validation.incumbent_model_id,
        "champion": _to_record(current).__dict__,
    }
