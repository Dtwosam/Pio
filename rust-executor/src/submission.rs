use crate::controlled_live::ControlledLiveReport;
use crate::execution_store::{ExecutionIntentStatus, ExecutionIntentStore};
use crate::phase5_gate::Phase5PromotionGateReport;
use crate::phase6_readiness::Phase6ReadinessReport;
use crate::signer::{
    sign_execution_intent, sign_prepared_transaction, SignedExecutionTransaction,
};
#[cfg(feature = "live-submit")]
use crate::simulation::decode_transaction_base64;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
#[cfg(feature = "live-submit")]
use solana_client::rpc_client::RpcClient;
use solana_sdk::signature::Keypair;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubmissionReport {
    pub decision_id: String,
    pub signature: String,
    pub reused_persisted_signature: bool,
    pub rpc_accepted: bool,
    pub rpc_error: Option<String>,
    pub intent_status: ExecutionIntentStatus,
}


fn ensure_submission_blockhash_is_live(
    store: &ExecutionIntentStore,
    decision_id: &str,
    current_block_height: u64,
) -> Result<()> {
    let current = store.load(decision_id)?;
    let prepared = current
        .prepared_transaction
        .as_ref()
        .context("execution intent is missing prepared transaction")?;
    if current_block_height > prepared.last_valid_block_height {
        if current.status == ExecutionIntentStatus::Sent {
            anyhow::bail!(
                "persisted SENT transaction blockhash has expired; confirmation recovery is required before any further action"
            );
        }
        if matches!(
            current.status,
            ExecutionIntentStatus::SimulationPassed
                | ExecutionIntentStatus::Signing
        ) {
            let reason = "prepared_blockhash_expired_before_submission";
            store.record_failure(decision_id, reason)?;
            anyhow::bail!("{reason}");
        }
        anyhow::bail!(
            "execution transaction blockhash expired in unexpected state {:?}",
            current.status
        );
    }
    Ok(())
}

fn signed_for_current(
    store: &ExecutionIntentStore,
    decision_id: &str,
    keypair: &Keypair,
    phase5_gate: &Phase5PromotionGateReport,
) -> Result<(SignedExecutionTransaction, bool)> {
    let current = store.load(decision_id)?;
    match current.status {
        ExecutionIntentStatus::SimulationPassed
        | ExecutionIntentStatus::Signing => Ok((
            sign_execution_intent(
                store,
                decision_id,
                keypair,
                phase5_gate,
            )?,
            false,
        )),
        ExecutionIntentStatus::Sent => {
            let prepared = current
                .prepared_transaction
                .as_ref()
                .context("SENT intent is missing prepared transaction")?;
            let signed =
                sign_prepared_transaction(decision_id, prepared, keypair)?;
            let persisted = current
                .signature
                .as_deref()
                .context("SENT intent is missing persisted signature")?;
            if signed.signature != persisted {
                anyhow::bail!(
                    "regenerated signed transaction does not match persisted SENT signature"
                );
            }
            Ok((signed, true))
        }
        other => anyhow::bail!(
            "submission requires SIMULATION_PASSED, SIGNING or SENT status; current status is {:?}",
            other
        ),
    }
}


pub fn submit_execution_intent_with<F>(
    store: &ExecutionIntentStore,
    decision_id: &str,
    keypair: &Keypair,
    phase5_gate: &Phase5PromotionGateReport,
    phase6_readiness: &Phase6ReadinessReport,
    controlled_live: &ControlledLiveReport,
    current_block_height: u64,
    send: F,
) -> Result<SubmissionReport>
where
    F: FnOnce(&SignedExecutionTransaction) -> Result<String>,
{
    if !phase5_gate.accepted {
        anyhow::bail!(
            "Phase 5 promotion gate rejected live submission: {}",
            phase5_gate.reason
        );
    }
    if !phase6_readiness.accepted {
        anyhow::bail!(
            "Phase 6 readiness rejected live submission: {}",
            phase6_readiness.reason
        );
    }
    if phase6_readiness.phase5.promoted_at != phase5_gate.promoted_at
        || phase6_readiness.phase5.evidence_type
            != phase5_gate.evidence_type
    {
        anyhow::bail!(
            "Phase 6 readiness Phase 5 evidence does not match submission gate"
        );
    }
    if !controlled_live.accepted {
        anyhow::bail!(
            "controlled-live authorization rejected submission: {}",
            controlled_live.reason
        );
    }
    if controlled_live.decision_id != decision_id {
        anyhow::bail!(
            "controlled-live authorization decision_id does not match submission"
        );
    }
    if controlled_live.phase5.promoted_at != phase5_gate.promoted_at
        || controlled_live.phase5.evidence_type != phase5_gate.evidence_type
    {
        anyhow::bail!(
            "controlled-live authorization Phase 5 evidence does not match submission gate"
        );
    }
    let current_for_authorization = store.load(decision_id)?;
    let persisted_wallet = current_for_authorization
        .wallet_authorization
        .as_ref()
        .context(
            "execution intent is missing persisted wallet authorization",
        )?;
    if persisted_wallet.wallet_pubkey != phase6_readiness.wallet_pubkey {
        anyhow::bail!(
            "Phase 6 readiness wallet does not match persisted execution wallet authorization"
        );
    }
    if controlled_live.pool_address != current_for_authorization.pool_address {
        anyhow::bail!(
            "controlled-live authorization pool does not match execution intent"
        );
    }
    let expected_action = match controlled_live.action {
        crate::models::Action::Enter => "ENTER",
        crate::models::Action::Rebalance => "REBALANCE",
        crate::models::Action::Exit => "EXIT",
        crate::models::Action::Skip => "SKIP",
    };
    if current_for_authorization.action != expected_action {
        anyhow::bail!(
            "controlled-live authorization action does not match execution intent"
        );
    }
    ensure_submission_blockhash_is_live(
        store,
        decision_id,
        current_block_height,
    )?;
    let (signed, reused_persisted_signature) =
        signed_for_current(
            store,
            decision_id,
            keypair,
            phase5_gate,
        )?;

    let current = store.load(decision_id)?;
    if current.status == ExecutionIntentStatus::Signing {
        store.record_sent(decision_id, &signed.signature)?;
    } else if current.status != ExecutionIntentStatus::Sent {
        anyhow::bail!(
            "signer did not leave execution intent in SIGNING or SENT state"
        );
    }

    match send(&signed) {
        Ok(observed_signature) => {
            let observed_signature = observed_signature.trim();
            if observed_signature != signed.signature {
                anyhow::bail!(
                    "RPC returned a transaction signature different from the persisted signed transaction"
                );
            }
            Ok(SubmissionReport {
                decision_id: decision_id.to_string(),
                signature: signed.signature,
                reused_persisted_signature,
                rpc_accepted: true,
                rpc_error: None,
                intent_status: store.load(decision_id)?.status,
            })
        }
        Err(error) => Ok(SubmissionReport {
            decision_id: decision_id.to_string(),
            signature: signed.signature,
            reused_persisted_signature,
            rpc_accepted: false,
            rpc_error: Some(error.to_string()),
            intent_status: store.load(decision_id)?.status,
        }),
    }
}


#[cfg(feature = "live-submit")]
pub fn submit_execution_intent_rpc(
    rpc_url: &str,
    store: &ExecutionIntentStore,
    decision_id: &str,
    keypair: &Keypair,
    phase5_gate: &Phase5PromotionGateReport,
    phase6_readiness: &Phase6ReadinessReport,
    controlled_live: &ControlledLiveReport,
) -> Result<SubmissionReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let client = RpcClient::new(rpc_url.to_string());
    let current_block_height = client
        .get_block_height()
        .context("failed to fetch current Solana block height")?;

    submit_execution_intent_with(
        store,
        decision_id,
        keypair,
        phase5_gate,
        phase6_readiness,
        controlled_live,
        current_block_height,
        |signed| {
            let transaction =
                decode_transaction_base64(&signed.transaction_base64)?;
            let signature = client
                .send_transaction(&transaction)
                .context("Solana send_transaction RPC failed")?;
            Ok(signature.to_string())
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::blockhash::PreparedUnsignedTransaction;
    use crate::dry_run::DryRunExecutionRequest;
    use crate::execution_guard::RiskCheckReport;
    use crate::models::{Action, Mode, TradeProposal};
    use crate::risk::RiskConfig;
    use crate::simulation::SimulationReport;
    use crate::transaction_guard::TransactionGuardReport;
    use crate::wallet_guard::WalletAuthorizationReport;
    use base64::{engine::general_purpose, Engine as _};
    use serde_json::json;
    use solana_sdk::hash::Hash;
    use solana_sdk::message::{Message, VersionedMessage};
    use solana_sdk::pubkey::Pubkey;
    use solana_sdk::signature::{Signature, Signer};
    use solana_sdk::transaction::VersionedTransaction;
    use std::cell::Cell;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn accepted_phase5_gate() -> Phase5PromotionGateReport {
        Phase5PromotionGateReport {
            accepted: true,
            reason: "approved".into(),
            phase_name: "PHASE5".into(),
            evidence_type: Some("PHASE5_PROMOTION_V1".into()),
            promoted_at: Some("2026-09-23T12:00:00+00:00".into()),
            qualified: true,
            evidence_promotion_ready: true,
            evidence_endurance_passing: true,
            evidence_ledger_audit_passing: true,
            evidence_phase3_promoted: true,
        }
    }

    fn accepted_phase6_readiness(
        wallet_pubkey: &str,
    ) -> Phase6ReadinessReport {
        Phase6ReadinessReport {
            accepted: true,
            reason: "approved".into(),
            phase5: accepted_phase5_gate(),
            wallet_pubkey: wallet_pubkey.into(),
            policy_fee_payer_matches_wallet: true,
            meteora_program_allowed: true,
            requires_unsigned: true,
            requires_pool_binding: true,
            requires_instruction_policy: true,
            address_lookup_tables_disabled: true,
            enter_policy_present: true,
            rebalance_policy_present: true,
            exit_policy_present: true,
        }
    }

    fn accepted_controlled_live(
        decision_id: &str,
        pool_address: &str,
        action: crate::models::Action,
    ) -> ControlledLiveReport {
        ControlledLiveReport {
            decision_id: decision_id.into(),
            accepted: true,
            reason: "approved".into(),
            phase5: accepted_phase5_gate(),
            action,
            pool_address: pool_address.into(),
            live_enabled: true,
            open_positions: 0,
            unresolved_entry_intents: 0,
            effective_open_positions: 0,
            matching_pool_positions: 1,
            matching_pool_rebalances: 0,
            max_open_positions: 1,
            max_rebalances_per_position: 3,
            pool_allowed: true,
            capital_quote: 10.0,
            max_capital_quote_per_entry: 50.0,
            daily_submitted_entry_capital_quote: 0.0,
            max_daily_entry_capital_quote: 100.0,
            daily_realized_loss_quote: 0.0,
            max_daily_realized_loss_quote: 20.0,
            unvalued_closed_positions_today: 0,
            daily_drawdown_pct: 0.5,
            max_daily_drawdown_pct: 2.0,
        }
    }

    fn ready_store(
        keypair: &Keypair,
    ) -> (ExecutionIntentStore, PathBuf, String) {
        let path = std::env::temp_dir().join(format!(
            "pio-submission-{}.db",
            Uuid::new_v4()
        ));
        let store = ExecutionIntentStore::open(&path).unwrap();

        let mut message = Message::new(&[], Some(&keypair.pubkey()));
        let blockhash = Hash::new_unique();
        message.recent_blockhash = blockhash;
        let transaction = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::Legacy(message),
        };
        let encoded = general_purpose::STANDARD.encode(
            bincode::serialize(&transaction).unwrap(),
        );
        let prepared = PreparedUnsignedTransaction {
            transaction_base64: encoded.clone(),
            recent_blockhash: blockhash.to_string(),
            last_valid_block_height: 1000,
            rpc_context_slot: 900,
            signatures_all_default: true,
        };

        let decision_id = Uuid::new_v4();
        let request = DryRunExecutionRequest {
            proposal: TradeProposal {
                decision_id,
                mode: Mode::Live,
                action: Action::Enter,
                pool_address: Pubkey::new_unique().to_string(),
                capital_quote: 10.0,
                account_equity_quote: 1_000.0,
                portfolio_deployed_quote: 100.0,
                daily_drawdown_pct: 0.5,
                min_bin_id: -1,
                max_bin_id: 1,
                strategy: "SPOT".into(),
                expected_net_return_pct: 1.0,
                expected_downside_pct: 0.5,
                model_version: "baseline".into(),
                data_age_seconds: 1,
            },
            transaction_base64: encoded,
        };
        let config = RiskConfig {
            max_capital_per_position_pct: 2.0,
            max_total_deployed_pct: 20.0,
            max_daily_drawdown_pct: 3.0,
            min_expected_edge_pct: 0.25,
            max_expected_downside_pct: 2.0,
            max_data_age_seconds: 30,
        };
        let risk = RiskCheckReport {
            decision_id,
            mode: Mode::Live,
            action: Action::Enter,
            accepted: true,
            reason: "approved".into(),
        };
        let guard = TransactionGuardReport {
            accepted: true,
            reason: "approved".into(),
            fee_payer: keypair.pubkey().to_string(),
            pool_account_present: true,
            required_accounts_present: true,
            instruction_count: 1,
            static_account_count: 2,
            required_signatures: 1,
            signatures_all_default: true,
            address_lookup_table_count: 0,
            program_ids: vec![Pubkey::new_unique().to_string()],
            instruction_fingerprints: vec![],
        };
        let wallet = WalletAuthorizationReport {
            accepted: true,
            reason: "approved".into(),
            wallet_pubkey: keypair.pubkey().to_string(),
            transaction_fee_payer: keypair.pubkey().to_string(),
        };
        let initial_simulation = SimulationReport {
            succeeded: true,
            rpc_context_slot: 901,
            result: json!({"err": null}),
        };
        let final_simulation = SimulationReport {
            succeeded: true,
            rpc_context_slot: 902,
            result: json!({"err": null}),
        };

        let id = decision_id.to_string();
        store.register(&request, &config).unwrap();
        store.record_risk(&id, &risk).unwrap();
        store.record_transaction_guard(&id, &guard).unwrap();
        store.record_simulation(&id, &initial_simulation).unwrap();
        store.record_wallet_authorization(&id, &wallet).unwrap();
        store
            .record_final_presign(
                &id,
                &prepared,
                &guard,
                &wallet,
                &final_simulation,
            )
            .unwrap();

        (store, path, id)
    }

    #[test]
    fn signature_is_persisted_before_rpc_send() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);
        let saw_sent = Cell::new(false);

        let report = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &accepted_controlled_live(
                &id,
                &store.load(&id).unwrap().pool_address,
                crate::models::Action::Enter,
            ),
            950,
            |signed| {
                let current = store.load(&id).unwrap();
                saw_sent.set(
                    current.status == ExecutionIntentStatus::Sent
                        && current.signature.as_deref()
                            == Some(signed.signature.as_str()),
                );
                Ok(signed.signature.clone())
            },
        )
        .unwrap();

        assert!(saw_sent.get());
        assert!(report.rpc_accepted);
        assert_eq!(report.intent_status, ExecutionIntentStatus::Sent);

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn ambiguous_rpc_error_stays_sent() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);

        let report = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &accepted_controlled_live(
                &id,
                &store.load(&id).unwrap().pool_address,
                crate::models::Action::Enter,
            ),
            950,
            |_| anyhow::bail!("timeout after submit"),
        )
        .unwrap();

        assert!(!report.rpc_accepted);
        assert!(report.rpc_error.unwrap().contains("timeout"));
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::Sent
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn retry_resubmits_same_signature_only() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);

        let first = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &accepted_controlled_live(
                &id,
                &store.load(&id).unwrap().pool_address,
                crate::models::Action::Enter,
            ),
            950,
            |_| anyhow::bail!("ambiguous"),
        )
        .unwrap();
        let second = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &accepted_controlled_live(
                &id,
                &store.load(&id).unwrap().pool_address,
                crate::models::Action::Enter,
            ),
            950,
            |signed| Ok(signed.signature.clone()),
        )
        .unwrap();

        assert_eq!(first.signature, second.signature);
        assert!(second.reused_persisted_signature);
        assert!(second.rpc_accepted);

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn rpc_signature_mismatch_fails_closed_and_stays_sent() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);

        let result = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &accepted_controlled_live(
                &id,
                &store.load(&id).unwrap().pool_address,
                crate::models::Action::Enter,
            ),
            950,
            |_| Ok(Signature::new_unique().to_string()),
        );

        assert!(result.is_err());
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::Sent
        );

        let _ = std::fs::remove_file(path);
    }
    #[test]
    fn expired_pre_submission_intent_fails_before_send() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);
        let called = Cell::new(false);

        let result = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &accepted_controlled_live(
                &id,
                &store.load(&id).unwrap().pool_address,
                crate::models::Action::Enter,
            ),
            1_001,
            |_| {
                called.set(true);
                anyhow::bail!("must not send expired transaction")
            },
        );

        assert!(result.is_err());
        assert!(!called.get());
        let current = store.load(&id).unwrap();
        assert_eq!(current.status, ExecutionIntentStatus::Failed);
        assert_eq!(
            current.error.as_deref(),
            Some("prepared_blockhash_expired_before_submission")
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn expired_sent_intent_cannot_be_resubmitted_blindly() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);

        let first = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &accepted_controlled_live(
                &id,
                &store.load(&id).unwrap().pool_address,
                crate::models::Action::Enter,
            ),
            950,
            |_| anyhow::bail!("ambiguous"),
        )
        .unwrap();
        assert_eq!(first.intent_status, ExecutionIntentStatus::Sent);

        let called = Cell::new(false);
        let retry = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &accepted_controlled_live(
                &id,
                &store.load(&id).unwrap().pool_address,
                crate::models::Action::Enter,
            ),
            1_001,
            |_| {
                called.set(true);
                Ok("must-not-send".into())
            },
        );

        assert!(retry.is_err());
        assert!(!called.get());
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::Sent
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn phase5_rejection_blocks_before_signing_or_send() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);
        let called = Cell::new(false);
        let mut gate = accepted_phase5_gate();
        gate.accepted = false;
        gate.reason = "phase5_promotion_evidence_missing".into();

        let result = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &gate,
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &accepted_controlled_live(
                &id,
                &store.load(&id).unwrap().pool_address,
                crate::models::Action::Enter,
            ),
            950,
            |_| {
                called.set(true);
                Ok("must-not-send".into())
            },
        );

        assert!(result.is_err());
        assert!(!called.get());
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::SimulationPassed
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn controlled_live_rejection_blocks_submission() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);
        let pool = store.load(&id).unwrap().pool_address;
        let mut live = accepted_controlled_live(
            &id,
            &pool,
            crate::models::Action::Enter,
        );
        live.accepted = false;
        live.reason = "entry_capital_cap_exceeded".into();
        let called = Cell::new(false);

        let result = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &accepted_phase6_readiness(
                &keypair.pubkey().to_string(),
            ),
            &live,
            950,
            |_| {
                called.set(true);
                Ok("must-not-send".into())
            },
        );

        assert!(result.is_err());
        assert!(!called.get());
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::SimulationPassed
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn controlled_live_authorization_cannot_be_replayed_to_another_decision() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);
        let pool = store.load(&id).unwrap().pool_address;
        let live = accepted_controlled_live(
            "different-decision",
            &pool,
            crate::models::Action::Enter,
        );

        assert!(
            submit_execution_intent_with(
                &store,
                &id,
                &keypair,
                &accepted_phase5_gate(),
                &accepted_phase6_readiness(
                    &keypair.pubkey().to_string(),
                ),
                &live,
                950,
                |_| Ok("must-not-send".into()),
            )
            .is_err()
        );
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::SimulationPassed
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn phase6_readiness_rejection_blocks_submission() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);
        let pool = store.load(&id).unwrap().pool_address;
        let live = accepted_controlled_live(
            &id,
            &pool,
            crate::models::Action::Enter,
        );
        let mut readiness = accepted_phase6_readiness(
            &keypair.pubkey().to_string(),
        );
        readiness.accepted = false;
        readiness.reason = "transaction_policy_fee_payer_does_not_match_executor_wallet".into();
        let called = Cell::new(false);

        let result = submit_execution_intent_with(
            &store,
            &id,
            &keypair,
            &accepted_phase5_gate(),
            &readiness,
            &live,
            950,
            |_| {
                called.set(true);
                Ok("must-not-send".into())
            },
        );

        assert!(result.is_err());
        assert!(!called.get());
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::SimulationPassed
        );

        let _ = std::fs::remove_file(path);
    }

}
