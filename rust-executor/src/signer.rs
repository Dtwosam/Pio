use crate::blockhash::PreparedUnsignedTransaction;
use crate::execution_store::{ExecutionIntentStatus, ExecutionIntentStore};
use crate::phase5_gate::Phase5PromotionGateReport;
use crate::simulation::decode_transaction_base64;
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use serde::{Deserialize, Serialize};
use solana_sdk::message::VersionedMessage;
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::{Keypair, Signature, Signer};


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignedExecutionTransaction {
    pub decision_id: String,
    pub signature: String,
    pub transaction_base64: String,
    pub recent_blockhash: String,
    pub last_valid_block_height: u64,
}


fn message_metadata(
    message: &VersionedMessage,
) -> Result<(Pubkey, usize, String)> {
    match message {
        VersionedMessage::Legacy(message) => Ok((
            *message
                .account_keys
                .first()
                .context("prepared transaction has no fee payer")?,
            usize::from(message.header.num_required_signatures),
            message.recent_blockhash.to_string(),
        )),
        VersionedMessage::V0(message) => Ok((
            *message
                .account_keys
                .first()
                .context("prepared transaction has no fee payer")?,
            usize::from(message.header.num_required_signatures),
            message.recent_blockhash.to_string(),
        )),
    }
}


pub fn sign_prepared_transaction(
    decision_id: &str,
    prepared: &PreparedUnsignedTransaction,
    keypair: &Keypair,
) -> Result<SignedExecutionTransaction> {
    if decision_id.trim().is_empty() {
        anyhow::bail!("decision_id is required");
    }
    if !prepared.signatures_all_default {
        anyhow::bail!("prepared transaction is not marked unsigned");
    }

    let mut transaction =
        decode_transaction_base64(&prepared.transaction_base64)?;
    if !transaction
        .signatures
        .iter()
        .all(|signature| *signature == Signature::default())
    {
        anyhow::bail!("prepared transaction already contains a signature");
    }

    let (fee_payer, required_signatures, blockhash) =
        message_metadata(&transaction.message)?;
    if required_signatures != 1 {
        anyhow::bail!(
            "executor signer requires exactly one transaction signer; found {required_signatures}"
        );
    }
    if transaction.signatures.len() != required_signatures {
        anyhow::bail!(
            "prepared signature vector length does not match required signer count"
        );
    }
    if fee_payer != keypair.pubkey() {
        anyhow::bail!(
            "isolated executor wallet is not the prepared transaction fee payer"
        );
    }
    if blockhash != prepared.recent_blockhash {
        anyhow::bail!(
            "prepared transaction blockhash differs from persisted presign evidence"
        );
    }

    let message_bytes = transaction.message.serialize();
    let signature = keypair.sign_message(&message_bytes);
    transaction.signatures[0] = signature;

    let bytes = bincode::serialize(&transaction)
        .context("failed to serialize signed VersionedTransaction")?;
    Ok(SignedExecutionTransaction {
        decision_id: decision_id.to_string(),
        signature: signature.to_string(),
        transaction_base64: general_purpose::STANDARD.encode(bytes),
        recent_blockhash: prepared.recent_blockhash.clone(),
        last_valid_block_height: prepared.last_valid_block_height,
    })
}


pub fn sign_execution_intent(
    store: &ExecutionIntentStore,
    decision_id: &str,
    keypair: &Keypair,
    phase5_gate: &Phase5PromotionGateReport,
) -> Result<SignedExecutionTransaction> {
    if !phase5_gate.accepted {
        anyhow::bail!(
            "Phase 5 promotion gate rejected execution signing: {}",
            phase5_gate.reason
        );
    }
    let current = store.load(decision_id)?;
    let signing = match current.status {
        ExecutionIntentStatus::SimulationPassed => {
            store.begin_signing(decision_id)?
        }
        ExecutionIntentStatus::Signing => current,
        other => {
            anyhow::bail!(
                "execution signing requires SIMULATION_PASSED or SIGNING status; current status is {:?}",
                other
            )
        }
    };

    let authorization = signing
        .wallet_authorization
        .as_ref()
        .context("signing intent is missing wallet authorization")?;
    if !authorization.accepted {
        anyhow::bail!("signing intent wallet authorization is not accepted");
    }
    if authorization.wallet_pubkey != keypair.pubkey().to_string() {
        anyhow::bail!(
            "loaded executor keypair differs from persisted wallet authorization"
        );
    }

    let prepared = signing
        .prepared_transaction
        .as_ref()
        .context("signing intent is missing prepared transaction")?;
    sign_prepared_transaction(decision_id, prepared, keypair)
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::blockhash::PreparedUnsignedTransaction;
    use solana_sdk::hash::Hash;
    use solana_sdk::message::{Message, VersionedMessage};
    use solana_sdk::transaction::VersionedTransaction;

    fn prepared_for(keypair: &Keypair) -> PreparedUnsignedTransaction {
        let mut message = Message::new(&[], Some(&keypair.pubkey()));
        let blockhash = Hash::new_unique();
        message.recent_blockhash = blockhash;
        let transaction = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::Legacy(message),
        };
        PreparedUnsignedTransaction {
            transaction_base64: general_purpose::STANDARD.encode(
                bincode::serialize(&transaction).unwrap(),
            ),
            recent_blockhash: blockhash.to_string(),
            last_valid_block_height: 123,
            rpc_context_slot: 100,
            signatures_all_default: true,
        }
    }

    #[test]
    fn exact_prepared_message_is_signed_deterministically() {
        let keypair = Keypair::new();
        let prepared = prepared_for(&keypair);

        let first =
            sign_prepared_transaction("decision", &prepared, &keypair)
                .unwrap();
        let second =
            sign_prepared_transaction("decision", &prepared, &keypair)
                .unwrap();

        assert_eq!(first.signature, second.signature);
        assert_eq!(
            first.transaction_base64,
            second.transaction_base64
        );
        assert_ne!(first.signature, Signature::default().to_string());

        let signed =
            decode_transaction_base64(&first.transaction_base64).unwrap();
        assert_eq!(signed.signatures[0].to_string(), first.signature);
    }

    #[test]
    fn different_wallet_cannot_sign_prepared_message() {
        let expected = Keypair::new();
        let other = Keypair::new();
        let prepared = prepared_for(&expected);

        assert!(
            sign_prepared_transaction("decision", &prepared, &other)
                .is_err()
        );
    }

    #[test]
    fn changed_persisted_blockhash_fails_closed() {
        let keypair = Keypair::new();
        let mut prepared = prepared_for(&keypair);
        prepared.recent_blockhash = Hash::new_unique().to_string();

        assert!(
            sign_prepared_transaction("decision", &prepared, &keypair)
                .is_err()
        );
    }

    #[test]
    fn signed_input_cannot_be_signed_again() {
        let keypair = Keypair::new();
        let prepared = prepared_for(&keypair);
        let signed =
            sign_prepared_transaction("decision", &prepared, &keypair)
                .unwrap();
        let mut changed = prepared.clone();
        changed.transaction_base64 = signed.transaction_base64;
        changed.signatures_all_default = false;

        assert!(
            sign_prepared_transaction("decision", &changed, &keypair)
                .is_err()
        );
    }

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

    fn ready_store(
        keypair: &Keypair,
    ) -> (ExecutionIntentStore, std::path::PathBuf, String) {
        use crate::dry_run::DryRunExecutionRequest;
        use crate::execution_guard::RiskCheckReport;
        use crate::models::{Action, Mode, TradeProposal};
        use crate::risk::RiskConfig;
        use crate::simulation::SimulationReport;
        use crate::transaction_guard::TransactionGuardReport;
        use crate::wallet_guard::WalletAuthorizationReport;
        use serde_json::json;
        use uuid::Uuid;

        let path = std::env::temp_dir().join(format!(
            "pio-signer-{}.db",
            Uuid::new_v4()
        ));
        let store = ExecutionIntentStore::open(&path).unwrap();
        let prepared = prepared_for(keypair);
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
            transaction_base64: prepared.transaction_base64.clone(),
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
        let initial_simulation = SimulationReport {
            succeeded: true,
            rpc_context_slot: 90,
            result: json!({"err": null}),
        };
        let final_simulation = SimulationReport {
            succeeded: true,
            rpc_context_slot: 101,
            result: json!({"err": null}),
        };
        let wallet = WalletAuthorizationReport {
            accepted: true,
            reason: "approved".into(),
            wallet_pubkey: keypair.pubkey().to_string(),
            transaction_fee_payer: keypair.pubkey().to_string(),
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
    fn execution_intent_signer_enters_signing_and_is_restart_deterministic() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);

        let first =
            sign_execution_intent(
                &store,
                &id,
                &keypair,
                &accepted_phase5_gate(),
            )
            .unwrap();
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::Signing
        );

        let second =
            sign_execution_intent(
                &store,
                &id,
                &keypair,
                &accepted_phase5_gate(),
            )
            .unwrap();
        assert_eq!(first.signature, second.signature);
        assert_eq!(
            first.transaction_base64,
            second.transaction_base64
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn execution_intent_signer_rejects_different_loaded_wallet() {
        let expected = Keypair::new();
        let other = Keypair::new();
        let (store, path, id) = ready_store(&expected);

        assert!(sign_execution_intent(
                &store,
                &id,
                &other,
                &accepted_phase5_gate(),
            )
            .is_err());
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::Signing
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn phase5_rejection_blocks_before_signing_state() {
        let keypair = Keypair::new();
        let (store, path, id) = ready_store(&keypair);
        let mut gate = accepted_phase5_gate();
        gate.accepted = false;
        gate.reason = "phase5_promotion_evidence_missing".into();

        assert!(
            sign_execution_intent(
                &store,
                &id,
                &keypair,
                &gate,
            )
            .is_err()
        );
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::SimulationPassed
        );

        let _ = std::fs::remove_file(path);
    }

}
