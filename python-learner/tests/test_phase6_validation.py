import json
import sqlite3

from meteora_learner.phase6_validation import (
    Phase6PromotionCriteria,
    evaluate_phase6_promotion,
)
from meteora_learner.phase_promotion import (
    PHASE5,
    PHASE5_EVIDENCE_TYPE,
    PHASE6,
    PHASE6_EVIDENCE_TYPE,
    persist_phase6_promotion,
    phase_promotion_state,
)
from meteora_learner.storage import Storage


def seed_phase5(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE5,
        evidence_type=PHASE5_EVIDENCE_TYPE,
        qualified=True,
        evidence={
            "promotion_ready": True,
            "phase3_promoted": True,
            "endurance": {"passing": True},
            "ledger_audit": {"passing": True},
        },
    )


def create_execution_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE execution_intents (
            decision_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            action TEXT NOT NULL,
            pool_address TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at_unix INTEGER NOT NULL,
            risk_json TEXT,
            transaction_guard_json TEXT,
            wallet_authorization_json TEXT,
            prepared_transaction_json TEXT,
            final_simulation_json TEXT
        )
        """
    )
    conn.close()


def insert_intent(
    path,
    *,
    decision_id,
    pool,
    status,
    action="ENTER",
    wallet="wallet",
    valid=True,
):
    risk = {"accepted": valid}
    guard = {
        "accepted": valid,
        "fee_payer": wallet if valid else "other",
    }
    authorization = {
        "accepted": valid,
        "wallet_pubkey": wallet,
        "transaction_fee_payer": wallet,
    }
    prepared = {"signatures_all_default": valid}
    simulation = {"succeeded": valid}
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO execution_intents(
            decision_id, mode, action, pool_address, status,
            created_at_unix, risk_json, transaction_guard_json,
            wallet_authorization_json, prepared_transaction_json,
            final_simulation_json
        ) VALUES (?, 'LIVE', ?, ?, ?, 1, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            action,
            pool,
            status,
            json.dumps(risk),
            json.dumps(guard),
            json.dumps(authorization),
            json.dumps(prepared),
            json.dumps(simulation),
        ),
    )
    conn.commit()
    conn.close()


def criteria():
    return Phase6PromotionCriteria(
        min_passed_enter_intents=2,
        min_distinct_pools=2,
        min_blocked_intents=1,
        max_postsimulation_intents=0,
    )


def test_phase6_ready_corpus_can_be_persisted(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase5(storage)
    execution_db = tmp_path / "execution.db"
    create_execution_db(execution_db)
    insert_intent(
        execution_db,
        decision_id="pass-1",
        pool="pool-a",
        status="SIMULATION_PASSED",
    )
    insert_intent(
        execution_db,
        decision_id="pass-2",
        pool="pool-b",
        status="SIMULATION_PASSED",
    )
    insert_intent(
        execution_db,
        decision_id="blocked",
        pool="pool-a",
        status="REJECTED",
        valid=False,
    )

    report = evaluate_phase6_promotion(
        storage,
        execution_db=execution_db.resolve(),
        criteria=criteria(),
    )

    assert report.promotion_ready is True
    assert report.passed_enter_intents == 2
    assert report.distinct_pools == 2
    assert report.blocked_intents == 1
    assert report.distinct_authorized_wallets == ("wallet",)

    state = persist_phase6_promotion(storage, report=report)
    assert state.phase_name == PHASE6
    assert state.promoted is True
    assert state.evidence_type == PHASE6_EVIDENCE_TYPE
    assert phase_promotion_state(
        storage,
        phase_name=PHASE6,
    ).promoted is True


def test_phase6_requires_phase5_and_complete_presign_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    execution_db = tmp_path / "execution.db"
    create_execution_db(execution_db)
    insert_intent(
        execution_db,
        decision_id="invalid-pass",
        pool="pool-a",
        status="SIMULATION_PASSED",
        valid=False,
    )

    report = evaluate_phase6_promotion(
        storage,
        execution_db=execution_db.resolve(),
        criteria=Phase6PromotionCriteria(
            min_passed_enter_intents=1,
            min_distinct_pools=1,
            min_blocked_intents=0,
            max_postsimulation_intents=0,
        ),
    )

    assert report.promotion_ready is False
    assert report.invalid_passed_intents == 1
    assert any("Phase 5" in reason for reason in report.reasons)
    assert any(
        "lack complete accepted presign evidence" in reason
        for reason in report.reasons
    )


def test_phase6_rejects_any_pre_promotion_signing_or_send(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase5(storage)
    execution_db = tmp_path / "execution.db"
    create_execution_db(execution_db)
    insert_intent(
        execution_db,
        decision_id="pass",
        pool="pool-a",
        status="SIMULATION_PASSED",
    )
    insert_intent(
        execution_db,
        decision_id="sent-too-early",
        pool="pool-a",
        status="SENT",
    )

    report = evaluate_phase6_promotion(
        storage,
        execution_db=execution_db.resolve(),
        criteria=Phase6PromotionCriteria(
            min_passed_enter_intents=1,
            min_distinct_pools=1,
            min_blocked_intents=0,
            max_postsimulation_intents=0,
        ),
    )

    assert report.promotion_ready is False
    assert report.postsimulation_intents == 1
    assert any(
        "post-simulation execution intents" in reason
        for reason in report.reasons
    )


def test_phase6_rejects_multiple_executor_wallets(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase5(storage)
    execution_db = tmp_path / "execution.db"
    create_execution_db(execution_db)
    insert_intent(
        execution_db,
        decision_id="pass-a",
        pool="pool-a",
        status="SIMULATION_PASSED",
        wallet="wallet-a",
    )
    insert_intent(
        execution_db,
        decision_id="pass-b",
        pool="pool-b",
        status="SIMULATION_PASSED",
        wallet="wallet-b",
    )

    report = evaluate_phase6_promotion(
        storage,
        execution_db=execution_db.resolve(),
        criteria=Phase6PromotionCriteria(
            min_passed_enter_intents=2,
            min_distinct_pools=2,
            min_blocked_intents=0,
            max_postsimulation_intents=0,
        ),
    )

    assert report.promotion_ready is False
    assert report.distinct_authorized_wallets == (
        "wallet-a",
        "wallet-b",
    )
    assert any(
        "exactly one executor wallet" in reason
        for reason in report.reasons
    )
