from meteora_learner.continuous_promotion import CONTINUOUS_PROMOTION_EVIDENCE_TYPE
from meteora_learner.phase8_validation import (
    Phase8PromotionCriteria,
    evaluate_phase8_promotion,
)
from meteora_learner.mint_risk import (
    MintRiskCriteria,
    persist_pool_mint_risk,
    research_pool_mint_risk,
)
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    persist_phase8_promotion,
)
from meteora_learner.storage import Storage


SPL = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def seed_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status, metrics_json
            ) VALUES (
                'champion', '2026-09-20T00:00:00+00:00',
                '2026-09-22T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles(
                cycle_id, created_at, updated_at, status,
                active_key, champion_model_id,
                champion_dataset_version,
                champion_evidence_watermark,
                plan_evidence_id, plan_as_of,
                target_dataset_version,
                challenger_model_id, plan_json
            ) VALUES (
                'phase8-cycle',
                '2026-09-21T00:00:00+00:00',
                '2026-09-22T00:00:00+00:00',
                'COMPLETED', NULL,
                'old-champion', 'dataset-v0',
                '2026-09-20T00:00:00+00:00',
                1, '2026-09-21T00:00:00+00:00',
                'dataset-v1', 'champion', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_learning_labels(
                position_address, decision_id, pool_address,
                model_version, strategy,
                min_bin_id, max_bin_id, range_width_bins,
                proposed_capital_quote,
                expected_net_return_pct, expected_downside_pct,
                realized_pnl_quote, realized_return_bps,
                prediction_error_bps, target_positive_return,
                quote_unit, opened_signature, closed_decision_id,
                created_at, raw_json
            ) VALUES (
                'phase8-position', 'phase8-decision',
                'phase8-pool', 'champion', 'SPOT',
                -1, 1, 3, '10', '1', '1',
                '1', 100, 0, 1, 'USD',
                'phase8-signature', 'phase8-close',
                '2026-09-23T00:00:00+00:00', '{}'
            )
            """
        )
    storage.save_model_live_evidence(
        model_id="champion",
        evidence_type=CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
        status="CHAMPION",
        evidence={
            "cycle_id": "phase8-cycle",
            "predecessor_model_id": "old-champion",
            "validation": {"qualified": True},
        },
    )
    report = evaluate_phase8_promotion(
        storage,
        criteria=Phase8PromotionCriteria(
            min_completed_cycles=1,
            min_live_labels=1,
            min_live_pools=1,
            max_realized_drawdown_bps=10_000,
            max_single_loss_bps=10_000,
            min_win_rate=0.0,
            min_mean_return_bps=-10_000,
            max_mean_abs_prediction_error_bps=10_000,
        ),
    )
    assert report.promotion_ready is True
    persist_phase8_promotion(storage, report=report)
def seed_pool(storage, *, x="x", y="y", x_program=SPL, y_program=SPL):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint,
                token_x_program, token_y_program, raw_json
            ) VALUES (
                '2026-09-23T10:00:00+00:00', 'pool', 0, 25,
                ?, ?, ?, ?, '{}'
            )
            """,
            (x, y, x_program, y_program),
        )


def save_mint(
    storage,
    *,
    mint,
    program=SPL,
    observed_at="2026-09-23T10:00:00+00:00",
    mint_authority=None,
    freeze_authority=None,
    extension_len=0,
):
    storage.save_token_mint_snapshot(
        {
            "mint_address": mint,
            "token_program": program,
            "capture_slot_start": 10,
            "capture_slot_end": 11,
            "supply": "1000000",
            "decimals": 6,
            "is_initialized": True,
            "mint_authority": mint_authority,
            "freeze_authority": freeze_authority,
            "data_len": 83 + extension_len if program == TOKEN2022 else 82,
            "token_2022_extension_data_len": extension_len,
            "has_token_2022_extension_data": extension_len > 0,
        },
        observed_at=observed_at,
    )


def test_revoked_standard_spl_mints_qualify(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase8(storage)
    seed_pool(storage)
    save_mint(storage, mint="x")
    save_mint(storage, mint="y")

    report = research_pool_mint_risk(
        storage,
        pool_address="pool",
        as_of="2026-09-23T10:10:00+00:00",
    )

    assert report.research_qualified is True
    assert report.policy_actionable is False
    assert report.mints_required == 2
    assert report.mints_accepted == 2
    assert report.pool_snapshot_id > 0
    assert all(
        item.mint_snapshot_id is not None
        for item in report.assessments
    )

    evidence_id = persist_pool_mint_risk(storage, report=report)
    assert evidence_id > 0


def test_active_authority_and_token2022_extensions_fail_closed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase8(storage)
    seed_pool(
        storage,
        x_program=TOKEN2022,
        y_program=SPL,
    )
    save_mint(
        storage,
        mint="x",
        program=TOKEN2022,
        mint_authority="authority",
        extension_len=24,
    )
    save_mint(storage, mint="y")

    report = research_pool_mint_risk(
        storage,
        pool_address="pool",
        as_of="2026-09-23T10:10:00+00:00",
    )

    assert report.research_qualified is False
    x = next(item for item in report.assessments if item.mint_address == "x")
    assert "mint authority is still active" in x.reasons
    assert (
        "Token-2022 extension data requires explicit allowance"
        in x.reasons
    )


def test_stale_snapshot_fails(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase8(storage)
    seed_pool(storage)
    save_mint(
        storage,
        mint="x",
        observed_at="2026-09-23T08:00:00+00:00",
    )
    save_mint(storage, mint="y")

    report = research_pool_mint_risk(
        storage,
        pool_address="pool",
        criteria=MintRiskCriteria(max_snapshot_age_seconds=300),
        as_of="2026-09-23T10:10:00+00:00",
    )

    assert report.research_qualified is False
    x = next(item for item in report.assessments if item.mint_address == "x")
    assert any("snapshot age" in reason for reason in x.reasons)


def test_no_lookahead_uses_snapshot_at_or_before_cutoff(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase8(storage)
    seed_pool(storage)
    save_mint(
        storage,
        mint="x",
        observed_at="2026-09-23T10:00:00+00:00",
    )
    save_mint(
        storage,
        mint="x",
        observed_at="2026-09-23T10:20:00+00:00",
        mint_authority="future-authority",
    )
    save_mint(
        storage,
        mint="y",
        observed_at="2026-09-23T10:00:00+00:00",
    )

    report = research_pool_mint_risk(
        storage,
        pool_address="pool",
        as_of="2026-09-23T10:10:00+00:00",
    )

    x = next(item for item in report.assessments if item.mint_address == "x")
    assert x.accepted is True
    assert x.observed_at == "2026-09-23T10:00:00+00:00"
    with storage.connect() as conn:
        expected_id = conn.execute(
            """
            SELECT id
            FROM token_mint_snapshots
            WHERE mint_address = 'x'
              AND observed_at = '2026-09-23T10:00:00+00:00'
            """
        ).fetchone()[0]
    assert x.mint_snapshot_id == expected_id


def test_phase8_is_required_for_qualification(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_pool(storage)
    save_mint(storage, mint="x")
    save_mint(storage, mint="y")

    report = research_pool_mint_risk(
        storage,
        pool_address="pool",
        as_of="2026-09-23T10:10:00+00:00",
    )

    assert report.research_qualified is False
    assert report.status == "RESEARCH_ONLY_PHASE8_BLOCKED"


def test_persisted_mint_risk_keeps_source_snapshot_ids(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase8(storage)
    seed_pool(storage)
    save_mint(storage, mint="x")
    save_mint(storage, mint="y")

    report = research_pool_mint_risk(
        storage,
        pool_address="pool",
        as_of="2026-09-23T10:10:00+00:00",
    )
    persist_pool_mint_risk(storage, report=report)
    latest = storage.latest_advanced_edge_evidence(
        edge_type="PHASE9_MINT_RISK_V1",
        pool_address="pool",
    )

    assert latest is not None
    assert latest["evidence"]["pool_snapshot_id"] == (
        report.pool_snapshot_id
    )
    observed_ids = {
        item["mint_snapshot_id"]
        for item in latest["evidence"]["assessments"]
    }
    assert observed_ids == {
        item.mint_snapshot_id for item in report.assessments
    }
