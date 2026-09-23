from meteora_learner.phase_promotion import (
    PHASE8,
    PHASE8_EVIDENCE_TYPE,
)
from meteora_learner.storage import Storage
from meteora_learner.wallet_flow import (
    WALLET_FLOW_EVIDENCE_TYPE,
    WalletFlowCriteria,
    persist_wallet_flow_research,
    research_wallet_flow,
    wallet_flow_source_sha256,
)


def add_event(
    storage,
    *,
    index,
    user,
    event_type,
    total_usd,
    pool="pool",
    created_at=None,
):
    when = created_at or f"2026-09-23T{index:02d}:00:00+00:00"
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO position_event_history(
                observed_at, position_address, signature, ix_index,
                event_type, block_time, slot, pool_address,
                user_address, token_x, token_y,
                amount_x, amount_y, amount_x_usd, amount_y_usd,
                total_usd, created_at, raw_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'x', 'y', '1', '1', '1', '1', ?, ?, '{}'
            )
            """,
            (
                when,
                f"position-{index}",
                f"signature-{index}",
                index,
                event_type,
                1_700_000_000 + index,
                1000 + index,
                pool,
                user,
                str(total_usd),
                when,
            ),
        )


def promote_phase8(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def criteria(**overrides):
    values = {
        "lookback_events": 100,
        "min_events": 10,
        "min_unique_users": 5,
        "max_top_user_share_bps": 3_000,
    }
    values.update(overrides)
    return WalletFlowCriteria(**values)


def seed_broad_flow(storage):
    event_types = [
        "ADD_LIQUIDITY",
        "REMOVE_LIQUIDITY",
        "DEPOSIT",
        "WITHDRAW",
        "CLAIM_FEE",
        "ADD_LIQUIDITY",
        "REMOVE_LIQUIDITY",
        "DEPOSIT",
        "WITHDRAW",
        "CLAIM_REWARD",
    ]
    for index, event_type in enumerate(event_types):
        add_event(
            storage,
            index=index,
            user=f"user-{index % 5}",
            event_type=event_type,
            total_usd=10,
        )


def test_broad_wallet_flow_is_research_ready(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_broad_flow(storage)
    promote_phase8(storage)

    report = research_wallet_flow(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T09:00:00+00:00",
    )

    assert report.status == "RESEARCH_READY"
    assert report.research_qualified is True
    assert report.events_used == 10
    assert report.unique_users == 5
    assert report.repeat_users == 5
    assert report.top_user_share_bps == 2_000
    assert report.classified_add_events == 4
    assert report.classified_remove_events == 4
    assert report.neutral_events == 2
    assert report.unclassified_events == 0
    assert report.direction_fidelity == "CLASSIFIED"
    assert report.policy_actionable is False
    assert len(report.source_event_ids) == 10
    assert len(report.source_event_sha256) == 64


def test_wallet_flow_cutoff_excludes_future_whale(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_broad_flow(storage)
    promote_phase8(storage)

    before = research_wallet_flow(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T09:00:00+00:00",
    )
    add_event(
        storage,
        index=20,
        user="future-whale",
        event_type="ADD_LIQUIDITY",
        total_usd=10_000,
        created_at="2026-09-24T00:00:00+00:00",
    )
    after = research_wallet_flow(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T09:00:00+00:00",
    )

    assert after.total_activity_usd == before.total_activity_usd
    assert after.top_user_share_bps == before.top_user_share_bps
    assert after.top_wallets == before.top_wallets


def test_concentrated_wallet_flow_is_flagged(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_broad_flow(storage)
    add_event(
        storage,
        index=10,
        user="whale",
        event_type="ADD_LIQUIDITY",
        total_usd=900,
        created_at="2026-09-23T10:00:00+00:00",
    )
    promote_phase8(storage)

    report = research_wallet_flow(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T10:00:00+00:00",
    )

    assert report.status == "CONCENTRATED_FLOW"
    assert report.research_qualified is False
    assert report.top_user_share_bps > 3_000
    assert any(
        "top-user activity concentration" in reason
        for reason in report.reasons
    )


def test_unknown_event_types_are_not_guessed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    for index in range(10):
        add_event(
            storage,
            index=index,
            user=f"user-{index % 5}",
            event_type="MYSTERY_EVENT",
            total_usd=10,
        )
    promote_phase8(storage)

    report = research_wallet_flow(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T09:00:00+00:00",
    )

    assert report.unclassified_events == 10
    assert report.classified_add_events == 0
    assert report.classified_remove_events == 0
    assert report.direction_fidelity == "UNCLASSIFIED"
    assert report.classified_net_add_usd == 0.0


def test_wallet_flow_evidence_round_trip(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_broad_flow(storage)
    promote_phase8(storage)
    report = research_wallet_flow(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T09:00:00+00:00",
    )

    evidence_id = persist_wallet_flow_research(
        storage,
        report=report,
    )

    assert evidence_id > 0
    latest = storage.latest_advanced_edge_evidence(
        edge_type=WALLET_FLOW_EVIDENCE_TYPE,
        pool_address="pool",
    )
    assert latest is not None
    assert latest["qualified"] is True
    assert latest["status"] == "RESEARCH_READY"
    assert latest["evidence"]["research_only"] is True
    assert latest["evidence"]["policy_actionable"] is False


def test_wallet_flow_source_hash_matches_persisted_events(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_broad_flow(storage)
    promote_phase8(storage)

    report = research_wallet_flow(
        storage,
        pool_address="pool",
        criteria=criteria(),
        as_of="2026-09-23T09:00:00+00:00",
    )

    records = []
    with storage.connect() as conn:
        for event_id in report.source_event_ids:
            row = conn.execute(
                """
                SELECT id, created_at, user_address, event_type,
                       total_usd, signature, ix_index,
                       position_address
                FROM position_event_history
                WHERE id = ?
                """,
                (event_id,),
            ).fetchone()
            assert row is not None
            records.append(
                {
                    "id": int(row[0]),
                    "created_at": str(row[1]),
                    "user_address": str(row[2]),
                    "event_type": str(row[3]),
                    "total_usd": str(row[4]),
                    "signature": str(row[5]),
                    "ix_index": int(row[6]),
                    "position_address": str(row[7]),
                }
            )

    assert (
        wallet_flow_source_sha256(records)
        == report.source_event_sha256
    )
