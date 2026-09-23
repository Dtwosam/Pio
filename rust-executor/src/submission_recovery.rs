use crate::confirmation::{
    observe_confirmation_rpc, ObservedConfirmation,
};
use crate::execution_store::{
    ExecutionIntentStatus, ExecutionIntentStore,
};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use solana_client::rpc_client::RpcClient;
use solana_sdk::commitment_config::CommitmentConfig;

pub const DEFAULT_EXPIRY_GRACE_BLOCKS: u64 = 32;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubmissionRecoveryReport {
    pub decision_id: String,
    pub signature: String,
    pub observation: ObservedConfirmation,
    pub intent_status: ExecutionIntentStatus,
    pub current_block_height: Option<u64>,
    pub last_valid_block_height: Option<u64>,
    pub expiry_grace_blocks: u64,
    pub historical_checks: u8,
    pub changed: bool,
    pub expired_without_observation: bool,
}

fn apply_observation(
    store: &ExecutionIntentStore,
    decision_id: &str,
    signature: &str,
    observation: &ObservedConfirmation,
) -> Result<(ExecutionIntentStatus, bool)> {
    match observation {
        ObservedConfirmation::Pending => {
            Ok((ExecutionIntentStatus::Sent, false))
        }
        ObservedConfirmation::Confirmed => Ok((
            store
                .record_confirmed(decision_id, signature)?
                .status,
            true,
        )),
        ObservedConfirmation::Failed(error) => {
            let reason = error.trim();
            if reason.is_empty() {
                anyhow::bail!(
                    "failed confirmation requires an error reason"
                );
            }
            Ok((
                store.record_failure(decision_id, reason)?.status,
                true,
            ))
        }
    }
}

pub fn reconcile_sent_recovery_with<FO, FH>(
    store: &ExecutionIntentStore,
    decision_id: &str,
    expiry_grace_blocks: u64,
    mut observe: FO,
    current_block_height: FH,
) -> Result<SubmissionRecoveryReport>
where
    FO: FnMut(&str) -> Result<ObservedConfirmation>,
    FH: FnOnce() -> Result<u64>,
{
    let current = store.load(decision_id)?;

    if current.status == ExecutionIntentStatus::Confirmed {
        let signature = current
            .signature
            .context("CONFIRMED execution intent is missing signature")?;
        return Ok(SubmissionRecoveryReport {
            decision_id: decision_id.to_string(),
            signature,
            observation: ObservedConfirmation::Confirmed,
            intent_status: ExecutionIntentStatus::Confirmed,
            current_block_height: None,
            last_valid_block_height: current
                .prepared_transaction
                .map(|item| item.last_valid_block_height),
            expiry_grace_blocks,
            historical_checks: 0,
            changed: false,
            expired_without_observation: false,
        });
    }

    if current.status == ExecutionIntentStatus::Failed {
        let signature = current.signature.unwrap_or_default();
        return Ok(SubmissionRecoveryReport {
            decision_id: decision_id.to_string(),
            signature,
            observation: ObservedConfirmation::Failed(
                current
                    .error
                    .unwrap_or_else(|| "execution_failed".into()),
            ),
            intent_status: ExecutionIntentStatus::Failed,
            current_block_height: None,
            last_valid_block_height: current
                .prepared_transaction
                .map(|item| item.last_valid_block_height),
            expiry_grace_blocks,
            historical_checks: 0,
            changed: false,
            expired_without_observation: false,
        });
    }

    if current.status != ExecutionIntentStatus::Sent {
        anyhow::bail!(
            "submission recovery requires SENT status; current status is {:?}",
            current.status
        );
    }

    let signature = current
        .signature
        .as_deref()
        .context("SENT execution intent is missing signature")?
        .to_string();
    let prepared = current
        .prepared_transaction
        .as_ref()
        .context("SENT execution intent is missing prepared transaction")?;
    let last_valid_block_height = prepared.last_valid_block_height;

    let first = observe(&signature)?;
    if first != ObservedConfirmation::Pending {
        let (status, changed) = apply_observation(
            store,
            decision_id,
            &signature,
            &first,
        )?;
        return Ok(SubmissionRecoveryReport {
            decision_id: decision_id.to_string(),
            signature,
            observation: first,
            intent_status: status,
            current_block_height: None,
            last_valid_block_height: Some(last_valid_block_height),
            expiry_grace_blocks,
            historical_checks: 1,
            changed,
            expired_without_observation: false,
        });
    }

    let height = current_block_height()?;
    let terminal_height = last_valid_block_height
        .saturating_add(expiry_grace_blocks);
    if height <= terminal_height {
        return Ok(SubmissionRecoveryReport {
            decision_id: decision_id.to_string(),
            signature,
            observation: ObservedConfirmation::Pending,
            intent_status: ExecutionIntentStatus::Sent,
            current_block_height: Some(height),
            last_valid_block_height: Some(last_valid_block_height),
            expiry_grace_blocks,
            historical_checks: 1,
            changed: false,
            expired_without_observation: false,
        });
    }

    // Re-check history after proving the blockhash is beyond its validity
    // window. A late observation always wins over local expiry inference.
    let second = observe(&signature)?;
    if second != ObservedConfirmation::Pending {
        let (status, changed) = apply_observation(
            store,
            decision_id,
            &signature,
            &second,
        )?;
        return Ok(SubmissionRecoveryReport {
            decision_id: decision_id.to_string(),
            signature,
            observation: second,
            intent_status: status,
            current_block_height: Some(height),
            last_valid_block_height: Some(last_valid_block_height),
            expiry_grace_blocks,
            historical_checks: 2,
            changed,
            expired_without_observation: false,
        });
    }

    let reason = "blockhash_expired_without_observed_transaction";
    let failed = store.record_failure(decision_id, reason)?;
    Ok(SubmissionRecoveryReport {
        decision_id: decision_id.to_string(),
        signature,
        observation: ObservedConfirmation::Pending,
        intent_status: failed.status,
        current_block_height: Some(height),
        last_valid_block_height: Some(last_valid_block_height),
        expiry_grace_blocks,
        historical_checks: 2,
        changed: true,
        expired_without_observation: true,
    })
}

pub fn reconcile_sent_recovery_rpc(
    rpc_url: &str,
    store: &ExecutionIntentStore,
    decision_id: &str,
    expiry_grace_blocks: u64,
) -> Result<SubmissionRecoveryReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let client = RpcClient::new(rpc_url.to_string());
    reconcile_sent_recovery_with(
        store,
        decision_id,
        expiry_grace_blocks,
        |signature| observe_confirmation_rpc(rpc_url, signature),
        || {
            client
                .get_block_height_with_commitment(
                    CommitmentConfig::confirmed(),
                )
                .context("failed to fetch confirmed Solana block height")
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
    use serde_json::json;
    use std::cell::Cell;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn sent_store() -> (
        ExecutionIntentStore,
        PathBuf,
        String,
    ) {
        let path = std::env::temp_dir().join(format!(
            "pio-submission-recovery-{}.db",
            Uuid::new_v4()
        ));
        let store = ExecutionIntentStore::open(&path).unwrap();
        let decision_id = Uuid::new_v4();
        let id = decision_id.to_string();
        let request = DryRunExecutionRequest {
            proposal: TradeProposal {
                decision_id,
                mode: Mode::Live,
                action: Action::Enter,
                pool_address: "pool".into(),
                capital_quote: 10.0,
                account_equity_quote: 1_000.0,
                portfolio_deployed_quote: 100.0,
                daily_drawdown_pct: 0.0,
                min_bin_id: -1,
                max_bin_id: 1,
                strategy: "SPOT".into(),
                expected_net_return_pct: 1.0,
                expected_downside_pct: 0.5,
                model_version: "test".into(),
                data_age_seconds: 1,
            },
            transaction_base64: "tx".into(),
        };
        let risk_config = RiskConfig {
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
            fee_payer: "payer".into(),
            pool_account_present: true,
            required_accounts_present: true,
            instruction_count: 1,
            static_account_count: 3,
            required_signatures: 1,
            signatures_all_default: true,
            address_lookup_table_count: 0,
            program_ids: vec!["program".into()],
            instruction_fingerprints: vec![],
        };
        let wallet = WalletAuthorizationReport {
            accepted: true,
            reason: "approved".into(),
            wallet_pubkey: "payer".into(),
            transaction_fee_payer: "payer".into(),
        };
        let simulation = SimulationReport {
            succeeded: true,
            rpc_context_slot: 10,
            result: json!({"err": null}),
        };
        store.register(&request, &risk_config).unwrap();
        store.record_risk(&id, &risk).unwrap();
        store.record_transaction_guard(&id, &guard).unwrap();
        store.record_simulation(&id, &simulation).unwrap();
        store.record_wallet_authorization(&id, &wallet).unwrap();
        store
            .record_final_presign(
                &id,
                &PreparedUnsignedTransaction {
                    transaction_base64: "prepared".into(),
                    recent_blockhash: "hash".into(),
                    last_valid_block_height: 100,
                    rpc_context_slot: 9,
                    signatures_all_default: true,
                },
                &guard,
                &wallet,
                &simulation,
            )
            .unwrap();
        store.begin_signing(&id).unwrap();
        store.record_sent(&id, "signature").unwrap();
        (store, path, id)
    }

    #[test]
    fn pending_before_expiry_stays_sent() {
        let (store, path, id) = sent_store();
        let checks = Cell::new(0);

        let report = reconcile_sent_recovery_with(
            &store,
            &id,
            10,
            |_| {
                checks.set(checks.get() + 1);
                Ok(ObservedConfirmation::Pending)
            },
            || Ok(105),
        )
        .unwrap();

        assert_eq!(report.intent_status, ExecutionIntentStatus::Sent);
        assert!(!report.changed);
        assert!(!report.expired_without_observation);
        assert_eq!(report.historical_checks, 1);
        assert_eq!(checks.get(), 1);

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn confirmed_observation_wins_before_expiry_logic() {
        let (store, path, id) = sent_store();

        let report = reconcile_sent_recovery_with(
            &store,
            &id,
            10,
            |_| Ok(ObservedConfirmation::Confirmed),
            || anyhow::bail!("height should not be requested"),
        )
        .unwrap();

        assert_eq!(
            report.intent_status,
            ExecutionIntentStatus::Confirmed
        );
        assert!(report.changed);

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn late_confirmation_wins_after_expiry_height() {
        let (store, path, id) = sent_store();
        let checks = Cell::new(0);

        let report = reconcile_sent_recovery_with(
            &store,
            &id,
            10,
            |_| {
                let next = checks.get() + 1;
                checks.set(next);
                if next == 1 {
                    Ok(ObservedConfirmation::Pending)
                } else {
                    Ok(ObservedConfirmation::Confirmed)
                }
            },
            || Ok(111),
        )
        .unwrap();

        assert_eq!(
            report.intent_status,
            ExecutionIntentStatus::Confirmed
        );
        assert_eq!(report.historical_checks, 2);
        assert!(!report.expired_without_observation);

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn expired_and_twice_unseen_signature_becomes_failed() {
        let (store, path, id) = sent_store();

        let report = reconcile_sent_recovery_with(
            &store,
            &id,
            10,
            |_| Ok(ObservedConfirmation::Pending),
            || Ok(111),
        )
        .unwrap();

        assert_eq!(report.intent_status, ExecutionIntentStatus::Failed);
        assert!(report.changed);
        assert!(report.expired_without_observation);
        assert_eq!(
            store.load(&id).unwrap().error.as_deref(),
            Some("blockhash_expired_without_observed_transaction")
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn exact_expiry_grace_boundary_remains_sent() {
        let (store, path, id) = sent_store();

        let report = reconcile_sent_recovery_with(
            &store,
            &id,
            10,
            |_| Ok(ObservedConfirmation::Pending),
            || Ok(110),
        )
        .unwrap();

        assert_eq!(report.intent_status, ExecutionIntentStatus::Sent);
        assert!(!report.changed);

        let _ = std::fs::remove_file(path);
    }
}
