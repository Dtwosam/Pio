use std::str::FromStr;

use anchor_client::solana_client::nonblocking::rpc_client::RpcClient;
use anchor_client::solana_client::rpc_client::GetConfirmedSignaturesForAddress2Config;
use anchor_client::solana_client::rpc_config::RpcTransactionConfig;
use anchor_client::solana_sdk::commitment_config::CommitmentConfig;
use anchor_client::solana_sdk::pubkey::Pubkey;
use anchor_client::solana_sdk::signature::Signature;
use anyhow::{Context, Result};
use serde::Serialize;
use solana_transaction_status_client_types::UiTransactionEncoding;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SignatureSlot {
    pub signature: String,
    pub slot: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AccountGapCheck {
    pub address: String,
    pub target_signature_seen: bool,
    pub conflicting_transactions: Vec<SignatureSlot>,
    pub eligible: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PrestateVerification {
    pub signature: String,
    pub transaction_slot: u64,
    pub capture_slot_start: u64,
    pub capture_slot_end: u64,
    pub account_checks: Vec<AccountGapCheck>,
    pub eligible: bool,
    pub reasons: Vec<String>,
}

fn assess_account_history(
    address: &str,
    target_signature: &str,
    transaction_slot: u64,
    capture_slot_start: u64,
    history: &[SignatureSlot],
) -> AccountGapCheck {
    let target_signature_seen = history
        .iter()
        .any(|item| item.signature == target_signature);

    let conflicting_transactions = history
        .iter()
        .filter(|item| {
            item.signature != target_signature
                && item.slot >= capture_slot_start
                && item.slot <= transaction_slot
        })
        .cloned()
        .collect::<Vec<_>>();

    AccountGapCheck {
        address: address.to_string(),
        target_signature_seen,
        eligible: target_signature_seen && conflicting_transactions.is_empty(),
        conflicting_transactions,
    }
}

async fn account_history_to_target(
    rpc: &RpcClient,
    address: &Pubkey,
    target_signature: &Signature,
    capture_slot_start: u64,
) -> Result<Vec<SignatureSlot>> {
    let mut before: Option<Signature> = None;
    let mut history = Vec::new();

    for _ in 0..50 {
        let page = rpc
            .get_signatures_for_address_with_config(
                address,
                GetConfirmedSignaturesForAddress2Config {
                    before,
                    until: None,
                    limit: Some(1000),
                    commitment: Some(CommitmentConfig::confirmed()),
                },
            )
            .await
            .with_context(|| format!("failed to fetch signature history for {address}"))?;

        if page.is_empty() {
            break;
        }

        let mut reached_target = false;
        let mut reached_before_capture = false;
        for item in &page {
            history.push(SignatureSlot {
                signature: item.signature.clone(),
                slot: item.slot,
            });
            if item.signature == target_signature.to_string() {
                reached_target = true;
            }
            if item.slot < capture_slot_start {
                reached_before_capture = true;
            }
        }

        if reached_target || reached_before_capture {
            break;
        }

        let last_signature = page
            .last()
            .context("signature history page unexpectedly empty")?
            .signature
            .parse::<Signature>()
            .context("RPC returned invalid signature")?;
        before = Some(last_signature);
    }

    Ok(history)
}

pub async fn verify_prestate_gap(
    rpc_url: &str,
    signature: &str,
    capture_slot_start: u64,
    capture_slot_end: u64,
    addresses: &[String],
) -> Result<PrestateVerification> {
    if capture_slot_start > capture_slot_end {
        anyhow::bail!("capture_slot_start cannot exceed capture_slot_end");
    }
    if addresses.is_empty() {
        anyhow::bail!("at least one account address is required");
    }

    let target_signature =
        Signature::from_str(signature).context("invalid transaction signature")?;
    let rpc = RpcClient::new(rpc_url.to_string());
    let transaction = rpc
        .get_transaction_with_config(
            &target_signature,
            RpcTransactionConfig {
                encoding: Some(UiTransactionEncoding::Json),
                commitment: Some(CommitmentConfig::confirmed()),
                max_supported_transaction_version: Some(0),
            },
        )
        .await
        .context("failed to fetch target transaction")?;
    let transaction_slot = transaction.slot;

    let mut reasons = Vec::new();
    if transaction_slot <= capture_slot_end {
        reasons.push(format!(
            "target slot {transaction_slot} is not after capture end slot {capture_slot_end}"
        ));
    }

    let mut account_checks = Vec::new();
    for raw_address in addresses {
        let address = Pubkey::from_str(raw_address)
            .with_context(|| format!("invalid account address {raw_address}"))?;
        let history = account_history_to_target(
            &rpc,
            &address,
            &target_signature,
            capture_slot_start,
        )
        .await?;
        let check = assess_account_history(
            raw_address,
            signature,
            transaction_slot,
            capture_slot_start,
            &history,
        );

        if !check.target_signature_seen {
            reasons.push(format!(
                "target transaction was not found in account history for {raw_address}"
            ));
        }
        if !check.conflicting_transactions.is_empty() {
            reasons.push(format!(
                "{} conflicting transaction(s) touched {raw_address} from capture start through target slot",
                check.conflicting_transactions.len()
            ));
        }
        account_checks.push(check);
    }

    let eligible = reasons.is_empty() && account_checks.iter().all(|item| item.eligible);

    Ok(PrestateVerification {
        signature: signature.to_string(),
        transaction_slot,
        capture_slot_start,
        capture_slot_end,
        account_checks,
        eligible,
        reasons,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_gap_accepts_only_target_after_snapshot() {
        let history = vec![
            SignatureSlot {
                signature: "target".to_string(),
                slot: 120,
            },
            SignatureSlot {
                signature: "old".to_string(),
                slot: 99,
            },
        ];
        let check = assess_account_history(
            "account",
            "target",
            120,
            100,
            &history,
        );
        assert!(check.eligible);
        assert!(check.conflicting_transactions.is_empty());
    }

    #[test]
    fn same_slot_other_transaction_is_ambiguous_and_rejected() {
        let history = vec![
            SignatureSlot {
                signature: "target".to_string(),
                slot: 120,
            },
            SignatureSlot {
                signature: "other".to_string(),
                slot: 120,
            },
        ];
        let check = assess_account_history(
            "account",
            "target",
            120,
            100,
            &history,
        );
        assert!(!check.eligible);
        assert_eq!(check.conflicting_transactions.len(), 1);
    }

    #[test]
    fn intervening_transaction_is_rejected() {
        let history = vec![
            SignatureSlot {
                signature: "target".to_string(),
                slot: 120,
            },
            SignatureSlot {
                signature: "middle".to_string(),
                slot: 110,
            },
        ];
        let check = assess_account_history(
            "account",
            "target",
            120,
            100,
            &history,
        );
        assert!(!check.eligible);
        assert_eq!(check.conflicting_transactions[0].slot, 110);
    }

    #[test]
    fn missing_target_reference_is_rejected() {
        let history = vec![SignatureSlot {
            signature: "old".to_string(),
            slot: 90,
        }];
        let check = assess_account_history(
            "account",
            "target",
            120,
            100,
            &history,
        );
        assert!(!check.eligible);
        assert!(!check.target_signature_seen);
    }
}
