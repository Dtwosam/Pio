use crate::execution_store::{ExecutionIntentRecord, ExecutionIntentStatus};
use crate::transaction_events::TransactionEventSnapshot;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionReceipt {
    pub decision_id: String,
    pub signature: String,
    pub mode: String,
    pub action: String,
    pub pool_address: String,
    pub intent_status: ExecutionIntentStatus,
    pub slot: u64,
    pub block_time: Option<i64>,
    pub network_fee_lamports: Option<u64>,
    pub compute_units_consumed: Option<u64>,
    pub succeeded: bool,
    pub event_count: usize,
    pub add_request_count: usize,
    pub rebalance_request_count: usize,
}


pub fn build_execution_receipt(
    intent: &ExecutionIntentRecord,
    snapshot: &TransactionEventSnapshot,
) -> Result<ExecutionReceipt> {
    if intent.mode != "LIVE" {
        anyhow::bail!("execution receipts require LIVE intents");
    }
    if !matches!(
        intent.status,
        ExecutionIntentStatus::Confirmed | ExecutionIntentStatus::Failed
    ) {
        anyhow::bail!(
            "execution receipt requires terminal CONFIRMED or FAILED intent status"
        );
    }

    let signature = intent
        .signature
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .context("terminal execution intent is missing signature")?;
    if signature != snapshot.signature {
        anyhow::bail!(
            "execution receipt signature does not match transaction snapshot"
        );
    }

    let succeeded = snapshot
        .succeeded
        .context("transaction snapshot is missing success outcome")?;
    match intent.status {
        ExecutionIntentStatus::Confirmed if !succeeded => {
            anyhow::bail!(
                "CONFIRMED intent cannot be reconciled to a failed transaction snapshot"
            );
        }
        ExecutionIntentStatus::Failed if succeeded => {
            anyhow::bail!(
                "FAILED intent cannot be reconciled to a successful transaction snapshot"
            );
        }
        _ => {}
    }

    Ok(ExecutionReceipt {
        decision_id: intent.decision_id.clone(),
        signature: signature.to_string(),
        mode: intent.mode.clone(),
        action: intent.action.clone(),
        pool_address: intent.pool_address.clone(),
        intent_status: intent.status.clone(),
        slot: snapshot.slot,
        block_time: snapshot.block_time,
        network_fee_lamports: snapshot.network_fee_lamports,
        compute_units_consumed: snapshot.compute_units_consumed,
        succeeded,
        event_count: snapshot.events.len(),
        add_request_count: snapshot.add_requests.len(),
        rebalance_request_count: snapshot.rebalance_requests.len(),
    })
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::execution_store::ExecutionIntentStatus;
    use crate::transaction_events::TransactionEventSnapshot;

    fn intent(status: ExecutionIntentStatus, signature: &str) -> ExecutionIntentRecord {
        ExecutionIntentRecord {
            decision_id: "decision".into(),
            mode: "LIVE".into(),
            action: "ENTER".into(),
            pool_address: "pool".into(),
            status,
            created_at_unix: 1,
            updated_at_unix: 2,
            risk: None,
            simulation: None,
            transaction_guard: None,
            wallet_authorization: None,
            prepared_transaction: None,
            final_simulation: None,
            signature: Some(signature.into()),
            error: None,
        }
    }

    fn snapshot(signature: &str, succeeded: bool) -> TransactionEventSnapshot {
        TransactionEventSnapshot {
            signature: signature.into(),
            slot: 123,
            block_time: Some(456),
            network_fee_lamports: Some(5000),
            compute_units_consumed: Some(100_000),
            succeeded: Some(succeeded),
            add_requests: vec![],
            rebalance_requests: vec![],
            events: vec![],
        }
    }

    #[test]
    fn confirmed_intent_builds_receipt() {
        let receipt = build_execution_receipt(
            &intent(ExecutionIntentStatus::Confirmed, "sig"),
            &snapshot("sig", true),
        )
        .unwrap();

        assert_eq!(receipt.decision_id, "decision");
        assert_eq!(receipt.signature, "sig");
        assert!(receipt.succeeded);
        assert_eq!(receipt.network_fee_lamports, Some(5000));
    }

    #[test]
    fn signature_mismatch_fails_closed() {
        assert!(
            build_execution_receipt(
                &intent(ExecutionIntentStatus::Confirmed, "sig-a"),
                &snapshot("sig-b", true),
            )
            .is_err()
        );
    }

    #[test]
    fn confirmed_intent_cannot_hide_failed_chain_outcome() {
        assert!(
            build_execution_receipt(
                &intent(ExecutionIntentStatus::Confirmed, "sig"),
                &snapshot("sig", false),
            )
            .is_err()
        );
    }

    #[test]
    fn failed_intent_accepts_failed_chain_outcome() {
        let receipt = build_execution_receipt(
            &intent(ExecutionIntentStatus::Failed, "sig"),
            &snapshot("sig", false),
        )
        .unwrap();

        assert!(!receipt.succeeded);
        assert_eq!(receipt.intent_status, ExecutionIntentStatus::Failed);
    }

    #[test]
    fn non_live_intent_is_rejected() {
        let mut item = intent(ExecutionIntentStatus::Confirmed, "sig");
        item.mode = "PAPER".into();

        assert!(build_execution_receipt(&item, &snapshot("sig", true)).is_err());
    }
}
