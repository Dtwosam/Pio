use crate::transaction_guard::TransactionGuardReport;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use solana_sdk::pubkey::Pubkey;
use std::str::FromStr;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WalletAuthorizationReport {
    pub accepted: bool,
    pub reason: String,
    pub wallet_pubkey: String,
    pub transaction_fee_payer: String,
}


pub fn authorize_wallet(
    wallet_pubkey: &Pubkey,
    transaction: &TransactionGuardReport,
) -> Result<WalletAuthorizationReport> {
    let transaction_payer = Pubkey::from_str(transaction.fee_payer.trim())
        .context("transaction guard fee payer is not a valid Solana pubkey")?;

    let rejection = if !transaction.accepted {
        Some("transaction_guard_not_accepted")
    } else if transaction.required_signatures == 0 {
        Some("transaction_has_no_required_signer")
    } else if transaction.required_signatures != 1 {
        Some("transaction_requires_additional_signers")
    } else if !transaction.signatures_all_default {
        Some("transaction_not_unsigned")
    } else if transaction_payer != *wallet_pubkey {
        Some("executor_wallet_is_not_transaction_fee_payer")
    } else {
        None
    };

    Ok(WalletAuthorizationReport {
        accepted: rejection.is_none(),
        reason: rejection.unwrap_or("approved").to_string(),
        wallet_pubkey: wallet_pubkey.to_string(),
        transaction_fee_payer: transaction_payer.to_string(),
    })
}


#[cfg(test)]
mod tests {
    use super::*;

    fn transaction(payer: Pubkey, accepted: bool) -> TransactionGuardReport {
        TransactionGuardReport {
            accepted,
            reason: if accepted {
                "approved".into()
            } else {
                "blocked".into()
            },
            fee_payer: payer.to_string(),
            pool_account_present: true,
            instruction_count: 1,
            static_account_count: 3,
            required_signatures: 1,
            signatures_all_default: true,
            address_lookup_table_count: 0,
            program_ids: vec![Pubkey::new_unique().to_string()],
            instruction_fingerprints: vec![],
        }
    }

    #[test]
    fn matching_executor_wallet_is_authorized() {
        let wallet = Pubkey::new_unique();
        let report = authorize_wallet(
            &wallet,
            &transaction(wallet, true),
        )
        .unwrap();

        assert!(report.accepted);
        assert_eq!(report.reason, "approved");
    }

    #[test]
    fn different_fee_payer_is_rejected() {
        let wallet = Pubkey::new_unique();
        let payer = Pubkey::new_unique();
        let report = authorize_wallet(
            &wallet,
            &transaction(payer, true),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "executor_wallet_is_not_transaction_fee_payer"
        );
    }

    #[test]
    fn additional_required_signer_is_rejected() {
        let wallet = Pubkey::new_unique();
        let mut tx = transaction(wallet, true);
        tx.required_signatures = 2;

        let report = authorize_wallet(&wallet, &tx).unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "transaction_requires_additional_signers"
        );
    }

    #[test]
    fn rejected_transaction_cannot_authorize_wallet() {
        let wallet = Pubkey::new_unique();
        let report = authorize_wallet(
            &wallet,
            &transaction(wallet, false),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "transaction_guard_not_accepted");
    }
}
