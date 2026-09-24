import json
import sys
from types import SimpleNamespace

from meteora_learner import cli
from datetime import datetime, timedelta, timezone
import json

import pytest

from meteora_learner.continuous_promotion import (
    CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
)
from meteora_learner.phase8_historical_promotion import (
    audit_persisted_phase8_promotion_at,
    evaluate_phase8_historical_promotion,
)
from meteora_learner.phase8_transition_history import (
    audit_phase8_transition_history,
)
from meteora_learner.phase8_validation import Phase8PromotionCriteria
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
)
from meteora_learner.storage import Storage


def text(value):
    return value.astimezone(timezone.utc).isoformat()


def journal_base(storage):
    audit = audit_phase8_transition_history(storage)
    assert audit.started_at is not None
    started = datetime.fromisoformat(
        audit.started_at.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    return started + timedelta(minutes=1)


def seed_phase7(storage, when):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence_history(
                phase_name,
                promoted_at,
                evidence_type,
                qualified,
                evidence_json
            ) VALUES (?, ?, ?, 1, '{}')
            """,
            (PHASE7, text(when), PHASE7_EVIDENCE_TYPE),
        )


def seed_champion_and_completed_cycle(storage, base):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id,
                created_at,
                updated_at,
                model_family,
                feature_version,
                dataset_version,
                status,
                metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, 'CHAMPION', '{}')
            """,
            (
                "champion-a",
                text(base),
                text(base),
                "ML_V1_HIST_GRADIENT_BOOSTING",
                "ML_ACTION_FEATURES_V1",
                "dataset-v2",
            ),
        )
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles(
                cycle_id,
                created_at,
                updated_at,
                status,
                active_key,
                champion_model_id,
                champion_dataset_version,
                champion_evidence_watermark,
                plan_evidence_id,
                plan_as_of,
                target_dataset_version,
                challenger_model_id,
                plan_json,
                notes
            ) VALUES (?, ?, ?, 'COMPLETED', NULL, ?, ?, ?, ?, ?, ?, ?, '{}', NULL)
            """,
            (
                "cycle-a",
                text(base),
                text(base + timedelta(minutes=1)),
                "incumbent-a",
                "dataset-v1",
                text(base - timedelta(days=1)),
                1,
                text(base),
                "dataset-v2",
                "champion-a",
            ),
        )


def seed_continuous_promotion(storage, when, *, qualified=True):
    payload = {
        "cycle_id": "cycle-a",
        "predecessor_model_id": "incumbent-a",
        "validation": {"qualified": qualified},
    }
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_live_evidence(
                model_id,
                created_at,
                evidence_type,
                status,
                evidence_json
            ) VALUES (?, ?, ?, 'CHAMPION', ?)
            """,
            (
                "champion-a",
                text(when),
                CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
                json.dumps(payload, separators=(",", ":")),
            ),
        )


def seed_label(
    storage,
    *,
    position,
    pool,
    when,
    realized_return_bps,
    prediction_error_bps=0,
):
    positive = int(realized_return_bps > 0)
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_learning_labels(
                position_address,
                decision_id,
                pool_address,
                model_version,
                strategy,
                min_bin_id,
                max_bin_id,
                range_width_bins,
                proposed_capital_quote,
                expected_net_return_pct,
                expected_downside_pct,
                realized_pnl_quote,
                realized_return_bps,
                prediction_error_bps,
                target_positive_return,
                quote_unit,
                opened_signature,
                closed_decision_id,
                created_at,
                raw_json
            ) VALUES (?, ?, ?, 'champion-a', 'CURVE', -2, 2, 5,
                      '100', '1', '1', ?, ?, ?, ?, 'ACCOUNT_QUOTE',
                      ?, ?, ?, '{}')
            """,
            (
                position,
                f"decision-{position}",
                pool,
                str(realized_return_bps),
                realized_return_bps,
                prediction_error_bps,
                positive,
                f"sig-{position}",
                f"close-{position}",
                text(when),
            ),
        )


def criteria():
    return Phase8PromotionCriteria(
        min_completed_cycles=1,
        min_live_labels=2,
        min_live_pools=2,
        max_realized_drawdown_bps=2000,
        max_single_loss_bps=1500,
        min_win_rate=0.50,
        min_mean_return_bps=0.0,
        max_mean_abs_prediction_error_bps=100.0,
    )


def seed_phase8_promotion_history(
    storage,
    *,
    promoted_at,
    report,
):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence_history(
                phase_name,
                promoted_at,
                evidence_type,
                qualified,
                evidence_json
            ) VALUES ('PHASE8', ?, 'PHASE8_PROMOTION_V1', 1, ?)
            """,
            (
                text(promoted_at),
                json.dumps(
                    report.to_record(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )


def seed_ready_history(storage, base):
    seed_phase7(storage, base)
    seed_champion_and_completed_cycle(storage, base)
    seed_continuous_promotion(storage, base + timedelta(minutes=2))
    seed_label(
        storage,
        position="p1",
        pool="pool-a",
        when=base + timedelta(minutes=3),
        realized_return_bps=200,
    )
    seed_label(
        storage,
        position="p2",
        pool="pool-b",
        when=base + timedelta(minutes=4),
        realized_return_bps=100,
    )


def test_historical_phase8_promotion_ready_from_cutoff_bound_sources(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    base = journal_base(storage)
    seed_ready_history(storage, base)

    report = evaluate_phase8_historical_promotion(
        storage,
        as_of=text(base + timedelta(minutes=10)),
        criteria=criteria(),
    )

    assert report.promotion_ready is True
    assert report.state_consistent is True
    assert report.phase7_promoted is True
    assert report.champion_model_id == "champion-a"
    assert report.completed_cycles == 1
    assert report.champion_cycle_id == "cycle-a"
    assert report.continuous_promotion_evidence_id is not None
    assert report.live_champion is not None
    assert report.live_champion.status == "HEALTHY"
    assert report.live_champion.label_count == 2
    assert report.live_champion.distinct_pools == 2
    assert report.reasons == ()


def test_future_promotion_evidence_cannot_repair_earlier_cutoff(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    base = journal_base(storage)
    seed_phase7(storage, base)
    seed_champion_and_completed_cycle(storage, base)
    seed_label(
        storage,
        position="p1",
        pool="pool-a",
        when=base + timedelta(minutes=2),
        realized_return_bps=200,
    )
    seed_label(
        storage,
        position="p2",
        pool="pool-b",
        when=base + timedelta(minutes=3),
        realized_return_bps=100,
    )
    seed_continuous_promotion(
        storage,
        base + timedelta(minutes=20),
    )

    earlier = evaluate_phase8_historical_promotion(
        storage,
        as_of=text(base + timedelta(minutes=10)),
        criteria=criteria(),
    )
    later = evaluate_phase8_historical_promotion(
        storage,
        as_of=text(base + timedelta(minutes=30)),
        criteria=criteria(),
    )

    assert earlier.promotion_ready is False
    assert earlier.continuous_promotion_evidence_id is None
    assert any(
        "continuous-promotion evidence" in reason
        for reason in earlier.reasons
    )
    assert later.promotion_ready is True
    assert later.continuous_promotion_evidence_id is not None


def test_future_bad_label_cannot_retroactively_spoil_earlier_cutoff(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    base = journal_base(storage)
    seed_ready_history(storage, base)
    seed_label(
        storage,
        position="p3",
        pool="pool-c",
        when=base + timedelta(minutes=20),
        realized_return_bps=-5000,
    )

    earlier = evaluate_phase8_historical_promotion(
        storage,
        as_of=text(base + timedelta(minutes=10)),
        criteria=criteria(),
    )
    later = evaluate_phase8_historical_promotion(
        storage,
        as_of=text(base + timedelta(minutes=30)),
        criteria=criteria(),
    )

    assert earlier.promotion_ready is True
    assert earlier.live_champion is not None
    assert earlier.live_champion.label_count == 2
    assert earlier.live_champion.status == "HEALTHY"

    assert later.promotion_ready is False
    assert later.live_champion is not None
    assert later.live_champion.label_count == 3
    assert later.live_champion.status == "BREACH"
    assert later.live_champion.rollback_recommended is True


def test_historical_promotion_rejects_prejournal_cutoff(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    audit = audit_phase8_transition_history(storage)
    assert audit.started_at is not None
    started = datetime.fromisoformat(
        audit.started_at.replace("Z", "+00:00")
    ).astimezone(timezone.utc)

    with pytest.raises(ValueError, match="predates transition journal"):
        evaluate_phase8_historical_promotion(
            storage,
            as_of=text(started - timedelta(seconds=1)),
            criteria=criteria(),
        )


def test_historical_phase8_promotion_cli_require_ready(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    report = SimpleNamespace(
        promotion_ready=False,
        to_record=lambda: {
            "as_of": "2026-09-24T12:00:00+00:00",
            "promotion_ready": False,
            "reasons": ["insufficient historical evidence"],
        },
    )
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        cli,
        "evaluate_phase8_historical_promotion",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pio",
            "phase8-historical-promotion",
            "--as-of",
            "2026-09-24T12:00:00+00:00",
            "--require-ready",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["promotion_ready"] is False
    assert payload["as_of"] == "2026-09-24T12:00:00+00:00"


def test_historical_persisted_phase8_promotion_becomes_valid_at_promotion_time(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    base = journal_base(storage)
    seed_ready_history(storage, base)
    report = evaluate_phase8_historical_promotion(
        storage,
        as_of=text(base + timedelta(minutes=10)),
        criteria=criteria(),
    )
    assert report.promotion_ready is True
    seed_phase8_promotion_history(
        storage,
        promoted_at=base + timedelta(minutes=12),
        report=report,
    )

    before = audit_persisted_phase8_promotion_at(
        storage,
        as_of=text(base + timedelta(minutes=11)),
    )
    after = audit_persisted_phase8_promotion_at(
        storage,
        as_of=text(base + timedelta(minutes=13)),
    )

    assert before.exists is False
    assert before.valid_at_cutoff is False
    assert after.exists is True
    assert after.qualified is True
    assert after.evidence_type_valid is True
    assert after.criteria_valid is True
    assert after.persisted_report_ready is True
    assert after.historical_promotion_ready is True
    assert after.champion_lineage_matches is True
    assert after.valid_at_cutoff is True
    assert after.reasons == ()


def test_historical_persisted_phase8_promotion_rejects_invalid_criteria(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    base = journal_base(storage)
    seed_ready_history(storage, base)
    report = evaluate_phase8_historical_promotion(
        storage,
        as_of=text(base + timedelta(minutes=10)),
        criteria=criteria(),
    )
    payload = report.to_record()
    payload["criteria"]["min_completed_cycles"] = 0

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence_history(
                phase_name,
                promoted_at,
                evidence_type,
                qualified,
                evidence_json
            ) VALUES ('PHASE8', ?, 'PHASE8_PROMOTION_V1', 1, ?)
            """,
            (
                text(base + timedelta(minutes=11)),
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )

    audit = audit_persisted_phase8_promotion_at(
        storage,
        as_of=text(base + timedelta(minutes=12)),
    )

    assert audit.exists is True
    assert audit.criteria_valid is False
    assert audit.historical_promotion_ready is False
    assert audit.valid_at_cutoff is False
    assert any("criteria are invalid" in reason for reason in audit.reasons)


def test_historical_phase8_promotion_audit_cli_require_valid(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    report = SimpleNamespace(
        valid_at_cutoff=False,
        to_record=lambda: {
            "as_of": "2026-09-24T12:00:00+00:00",
            "exists": False,
            "valid_at_cutoff": False,
            "reasons": [
                "persisted Phase 8 promotion history is missing at cutoff"
            ],
        },
    )
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        cli,
        "audit_persisted_phase8_promotion_at",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pio",
            "phase8-historical-promotion-audit",
            "--as-of",
            "2026-09-24T12:00:00+00:00",
            "--require-valid",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid_at_cutoff"] is False
    assert payload["exists"] is False
