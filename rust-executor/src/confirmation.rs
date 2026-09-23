use crate::execution_store::{
    ExecutionIntentStatus, ExecutionIntentStore,
};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use solana_client::rpc_client::RpcClient;
use solana_sdk::commitment_config::CommitmentConfig;
use solana_sdk::signature::Signature;
use std::str::FromStr;


#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "status", content = "error", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ObservedConfirmation {
    Pending,
    Confirmed,
    Failed(String),
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfirmationReconcileReport {
    pub decision_id: String,
    pub signature: String,
    pub observation: ObservedConfirmation,
    pub intent_status: ExecutionIntentStatus,
    pub changed: bool,
}


pub fn observe_confirmation_rpc(
    rpc_url: &str,
    signature: &str,
) -> Result<ObservedConfirmation> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let signature = Signature::from_str(signature.trim())
        .context("execution signature is not valid base58")?;
    let client = RpcClient::new(rpc_url.to_string());
    let status = client
        .get_signature_status_with_commitment_and_history(
            &signature,
            CommitmentConfig::confirmed(),
            true,
        )
        .context("Solana signature status RPC failed")?;

    Ok(match status {
        None => ObservedConfirmation::Pending,
        Some(Ok(())) => ObservedConfirmation::Confirmed,
        Some(Err(error)) => ObservedConfirmation::Failed(error.to_string()),
    })
}


pub fn reconcile_confirmation_with<F>(
    store: &ExecutionIntentStore,
    decision_id: &str,
    observe: F,
) -> Result<ConfirmationReconcileReport>
where
    F: FnOnce(&str) -> Result<ObservedConfirmation>,
{
    let current = store.load(decision_id)?;

    if current.status == ExecutionIntentStatus::Confirmed {
        let signature = current
            .signature
            .context("CONFIRMED execution intent is missing signature")?;
        return Ok(ConfirmationReconcileReport {
            decision_id: decision_id.to_string(),
            signature,
            observation: ObservedConfirmation::Confirmed,
            intent_status: ExecutionIntentStatus::Confirmed,
            changed: false,
        });
    }

    if current.status == ExecutionIntentStatus::Failed {
        let signature = current
            .signature
            .unwrap_or_default();
        return Ok(ConfirmationReconcileReport {
            decision_id: decision_id.to_string(),
            signature,
            observation: ObservedConfirmation::Failed(
                current.error.unwrap_or_else(|| "execution_failed".into()),
            ),
            intent_status: ExecutionIntentStatus::Failed,
            changed: false,
        });
    }

    if current.status != ExecutionIntentStatus::Sent {
        anyhow::bail!(
            "confirmation reconciliation requires SENT status; current status is {:?}",
            current.status
        );
    }

    let signature = current
        .signature
        .context("SENT execution intent is missing signature")?;
    let observation = observe(&signature)?;

    let (intent_status, changed) = match &observation {
        ObservedConfirmation::Pending => (
            ExecutionIntentStatus::Sent,
            false,
        ),
        ObservedConfirmation::Confirmed => (
            store
                .record_confirmed(decision_id, &signature)?
                .status,
            true,
        ),
        ObservedConfirmation::Failed(error) => {
            let reason = error.trim();
            if reason.is_empty() {
                anyhow::bail!("failed confirmation requires an error reason");
            }
            (
                store.record_failure(decision_id, reason)?.status,
                true,
            )
        }
    };

    Ok(ConfirmationReconcileReport {
        decision_id: decision_id.to_string(),
        signature,
        observation,
        intent_status,
        changed,
    })
}


#[cfg(test)]
mod tests {
    use super::*;
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

    fn db_path() -> PathBuf {
        std::env::temp_dir().join(format!(
            "pio-confirmation-{}.db",
            Uuid::new_v4()
        ))
    }

    fn config() -> RiskConfig {
        RiskConfig {
            max_capital_per_position_pct: 2.0,
            max_total_deployed_pct: 20.0,
            max_daily_drawdown_pct: 3.0,
            min_expected_edge_pct: 0.25,
            max_expected_downside_pct: 2.0,
            max_data_age_seconds: 30,
        }
    }

    fn request() -> DryRunExecutionRequest {
        DryRunExecutionRequest {
            proposal: TradeProposal {
                decision_id: Uuid::new_v4(),
                mode: Mode::Live,
                action: Action::Enter,
                pool_address: "pool".into(),
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
            transaction_base64: "tx".into(),
        }
    }

    fn risk(id: Uuid) -> RiskCheckReport {
        RiskCheckReport {
            decision_id: id,
            mode: Mode::Live,
            action: Action::Enter,
            accepted: true,
            reason: "approved".into(),
        }
    }

    fn guard() -> TransactionGuardReport {
        TransactionGuardReport {
            accepted: true,
            reason: "approved".into(),
            fee_payer: "payer".into(),
            pool_account_present: true,
            instruction_count: 1,
            static_account_count: 3,
            required_signatures: 1,
            signatures_all_default: true,
            address_lookup_table_count: 0,
            program_ids: vec!["program".into()],
            instruction_fingerprints: vec![],
        }
    }

    fn simulation() -> SimulationReport {
        SimulationReport {
            succeeded: true,
            rpc_context_slot: 1,
            result: json!({"err": null}),
        }
    }

    fn sent_store() -> (
        ExecutionIntentStore,
        PathBuf,
        String,
    ) {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let id = request.proposal.decision_id.to_string();
        store.register(&request, &config()).unwrap();
        store
            .record_risk(
                &id,
                &risk(request.proposal.decision_id),
            )
            .unwrap();
        store.record_transaction_guard(&id, &guard()).unwrap();
        store.record_simulation(&id, &simulation()).unwrap();
        store
            .record_wallet_authorization(
                &id,
                &WalletAuthorizationReport {
                    accepted: true,
                    reason: "approved".into(),
                    wallet_pubkey: "payer".into(),
                    transaction_fee_payer: "payer".into(),
                },
            )
            .unwrap();
        store.begin_signing(&id).unwrap();
        store.record_sent(&id, "signature-1").unwrap();
        (store, path, id)
    }

    #[test]
    fn pending_confirmation_leaves_sent_state_unchanged() {
        let (store, path, id) = sent_store();

        let report = reconcile_confirmation_with(
            &store,
            &id,
            |_| Ok(ObservedConfirmation::Pending),
        )
        .unwrap();

        assert!(!report.changed);
        assert_eq!(report.intent_status, ExecutionIntentStatus::Sent);
        assert_eq!(store.load(&id).unwrap().status, ExecutionIntentStatus::Sent);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn confirmed_signature_is_persisted_idempotently() {
        let (store, path, id) = sent_store();
        let calls = Cell::new(0);

        let first = reconcile_confirmation_with(
            &store,
            &id,
            |_| {
                calls.set(calls.get() + 1);
                Ok(ObservedConfirmation::Confirmed)
            },
        )
        .unwrap();
        let second = reconcile_confirmation_with(
            &store,
            &id,
            |_| {
                calls.set(calls.get() + 1);
                Ok(ObservedConfirmation::Pending)
            },
        )
        .unwrap();

        assert!(first.changed);
        assert_eq!(first.intent_status, ExecutionIntentStatus::Confirmed);
        assert!(!second.changed);
        assert_eq!(second.intent_status, ExecutionIntentStatus::Confirmed);
        assert_eq!(calls.get(), 1);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn failed_signature_becomes_terminal_failure() {
        let (store, path, id) = sent_store();

        let report = reconcile_confirmation_with(
            &store,
            &id,
            |_| Ok(ObservedConfirmation::Failed("chain rejected".into())),
        )
        .unwrap();

        assert!(report.changed);
        assert_eq!(report.intent_status, ExecutionIntentStatus::Failed);
        assert_eq!(
            store.load(&id).unwrap().error.as_deref(),
            Some("chain rejected")
        );
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn confirmation_cannot_run_before_sent_state() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let id = request.proposal.decision_id.to_string();
        store.register(&request, &config()).unwrap();

        assert!(
            reconcile_confirmation_with(
                &store,
                &id,
                |_| Ok(ObservedConfirmation::Confirmed),
            )
            .is_err()
        );
        let _ = std::fs::remove_file(path);
    }
}
