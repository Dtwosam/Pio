use crate::models::TradeProposal;
use crate::simulation::decode_transaction_base64;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use solana_sdk::message::VersionedMessage;
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use std::collections::BTreeSet;
use std::str::FromStr;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransactionGuardConfig {
    pub expected_fee_payer: String,
    pub allowed_program_ids: Vec<String>,
    pub max_instructions: usize,
    pub max_static_accounts: usize,
    pub allow_address_lookup_tables: bool,
    pub require_unsigned: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransactionGuardReport {
    pub accepted: bool,
    pub reason: String,
    pub fee_payer: String,
    pub pool_account_present: bool,
    pub instruction_count: usize,
    pub static_account_count: usize,
    pub required_signatures: usize,
    pub signatures_all_default: bool,
    pub address_lookup_table_count: usize,
    pub program_ids: Vec<String>,
}

struct InspectedTransaction {
    fee_payer: Pubkey,
    account_keys: Vec<Pubkey>,
    program_ids: Vec<Pubkey>,
    instruction_count: usize,
    required_signatures: usize,
    signatures_all_default: bool,
    address_lookup_table_count: usize,
}

fn validate_config(config: &TransactionGuardConfig) -> Result<(Pubkey, BTreeSet<Pubkey>)> {
    if config.max_instructions == 0 {
        anyhow::bail!("max_instructions must be positive");
    }
    if config.max_static_accounts == 0 {
        anyhow::bail!("max_static_accounts must be positive");
    }
    let payer = Pubkey::from_str(config.expected_fee_payer.trim())
        .context("expected_fee_payer is not a valid Solana pubkey")?;
    if config.allowed_program_ids.is_empty() {
        anyhow::bail!("allowed_program_ids cannot be empty");
    }
    let mut programs = BTreeSet::new();
    for raw in &config.allowed_program_ids {
        programs.insert(
            Pubkey::from_str(raw.trim())
                .with_context(|| format!("invalid allowed program id: {raw}"))?,
        );
    }
    Ok((payer, programs))
}

fn inspect_transaction(encoded: &str) -> Result<InspectedTransaction> {
    let transaction = decode_transaction_base64(encoded)?;
    let signatures_all_default = transaction
        .signatures
        .iter()
        .all(|signature| *signature == Signature::default());

    match &transaction.message {
        VersionedMessage::Legacy(message) => {
            let fee_payer = *message
                .account_keys
                .first()
                .context("transaction has no static account keys")?;
            let required_signatures =
                usize::from(message.header.num_required_signatures);
            if transaction.signatures.len() != required_signatures {
                anyhow::bail!(
                    "signature vector length {} does not match required signer count {}",
                    transaction.signatures.len(),
                    required_signatures
                );
            }

            let mut program_ids = Vec::with_capacity(message.instructions.len());
            for instruction in &message.instructions {
                let index = usize::from(instruction.program_id_index);
                let program = message.account_keys.get(index).with_context(|| {
                    format!(
                        "instruction program index {index} is outside static account keys"
                    )
                })?;
                program_ids.push(*program);
            }

            Ok(InspectedTransaction {
                fee_payer,
                account_keys: message.account_keys.clone(),
                program_ids,
                instruction_count: message.instructions.len(),
                required_signatures,
                signatures_all_default,
                address_lookup_table_count: 0,
            })
        }
        VersionedMessage::V0(message) => {
            let fee_payer = *message
                .account_keys
                .first()
                .context("transaction has no static account keys")?;
            let required_signatures =
                usize::from(message.header.num_required_signatures);
            if transaction.signatures.len() != required_signatures {
                anyhow::bail!(
                    "signature vector length {} does not match required signer count {}",
                    transaction.signatures.len(),
                    required_signatures
                );
            }

            let mut program_ids = Vec::with_capacity(message.instructions.len());
            for instruction in &message.instructions {
                let index = usize::from(instruction.program_id_index);
                let program = message.account_keys.get(index).with_context(|| {
                    format!(
                        "instruction program index {index} is not a static account; transaction guard does not resolve lookup-table program ids"
                    )
                })?;
                program_ids.push(*program);
            }

            Ok(InspectedTransaction {
                fee_payer,
                account_keys: message.account_keys.clone(),
                program_ids,
                instruction_count: message.instructions.len(),
                required_signatures,
                signatures_all_default,
                address_lookup_table_count: message.address_table_lookups.len(),
            })
        }
    }
}

pub fn check_transaction(
    proposal: &TradeProposal,
    encoded: &str,
    config: &TransactionGuardConfig,
) -> Result<TransactionGuardReport> {
    let (expected_payer, allowed_programs) = validate_config(config)?;
    let pool = Pubkey::from_str(proposal.pool_address.trim())
        .context("proposal pool_address is not a valid Solana pubkey")?;
    let inspected = inspect_transaction(encoded)?;

    let pool_account_present = inspected.account_keys.contains(&pool);
    let unique_program_ids: BTreeSet<Pubkey> =
        inspected.program_ids.iter().copied().collect();
    let program_ids = unique_program_ids
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>();

    let rejection = if inspected.fee_payer != expected_payer {
        Some("unexpected_fee_payer")
    } else if inspected.required_signatures == 0 {
        Some("transaction_has_no_required_signer")
    } else if inspected.instruction_count == 0 {
        Some("transaction_has_no_instructions")
    } else if inspected.instruction_count > config.max_instructions {
        Some("instruction_count_limit")
    } else if inspected.account_keys.len() > config.max_static_accounts {
        Some("static_account_count_limit")
    } else if inspected.address_lookup_table_count > 0
        && !config.allow_address_lookup_tables
    {
        Some("address_lookup_tables_not_allowed")
    } else if !pool_account_present {
        Some("proposal_pool_not_in_transaction")
    } else if unique_program_ids
        .iter()
        .any(|program| !allowed_programs.contains(program))
    {
        Some("transaction_uses_unapproved_program")
    } else if config.require_unsigned && !inspected.signatures_all_default {
        Some("transaction_already_signed")
    } else {
        None
    };

    Ok(TransactionGuardReport {
        accepted: rejection.is_none(),
        reason: rejection.unwrap_or("approved").to_string(),
        fee_payer: inspected.fee_payer.to_string(),
        pool_account_present,
        instruction_count: inspected.instruction_count,
        static_account_count: inspected.account_keys.len(),
        required_signatures: inspected.required_signatures,
        signatures_all_default: inspected.signatures_all_default,
        address_lookup_table_count: inspected.address_lookup_table_count,
        program_ids,
    })
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode};
    use base64::{engine::general_purpose, Engine as _};
    use solana_sdk::instruction::Instruction;
    use solana_sdk::message::{Message, VersionedMessage};
    use solana_sdk::signature::Signature;
    use solana_sdk::transaction::VersionedTransaction;
    use uuid::Uuid;

    fn proposal(pool: Pubkey) -> TradeProposal {
        TradeProposal {
            decision_id: Uuid::new_v4(),
            mode: Mode::Live,
            action: Action::Enter,
            pool_address: pool.to_string(),
            capital_quote: 10.0,
            account_equity_quote: 1_000.0,
            portfolio_deployed_quote: 100.0,
            daily_drawdown_pct: 0.5,
            min_bin_id: -10,
            max_bin_id: 10,
            strategy: "SPOT".into(),
            expected_net_return_pct: 1.0,
            expected_downside_pct: 0.5,
            model_version: "baseline".into(),
            data_age_seconds: 1,
        }
    }

    fn encoded_transaction(
        payer: Pubkey,
        pool: Pubkey,
        program: Pubkey,
        signature: Signature,
    ) -> String {
        let instruction = Instruction {
            program_id: program,
            accounts: vec![
                solana_sdk::instruction::AccountMeta::new_readonly(pool, false),
            ],
            data: vec![1, 2, 3],
        };
        let message = Message::new(&[instruction], Some(&payer));
        let transaction = VersionedTransaction {
            signatures: vec![signature],
            message: VersionedMessage::Legacy(message),
        };
        general_purpose::STANDARD.encode(bincode::serialize(&transaction).unwrap())
    }

    fn config(payer: Pubkey, program: Pubkey) -> TransactionGuardConfig {
        TransactionGuardConfig {
            expected_fee_payer: payer.to_string(),
            allowed_program_ids: vec![program.to_string()],
            max_instructions: 4,
            max_static_accounts: 16,
            allow_address_lookup_tables: false,
            require_unsigned: true,
        }
    }

    #[test]
    fn matching_unsigned_transaction_is_approved() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let encoded = encoded_transaction(
            payer,
            pool,
            program,
            Signature::default(),
        );

        let report = check_transaction(
            &proposal(pool),
            &encoded,
            &config(payer, program),
        )
        .unwrap();

        assert!(report.accepted);
        assert_eq!(report.reason, "approved");
        assert!(report.pool_account_present);
        assert_eq!(report.required_signatures, 1);
        assert!(report.signatures_all_default);
    }

    #[test]
    fn wrong_pool_is_rejected() {
        let payer = Pubkey::new_unique();
        let proposal_pool = Pubkey::new_unique();
        let transaction_pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let encoded = encoded_transaction(
            payer,
            transaction_pool,
            program,
            Signature::default(),
        );

        let report = check_transaction(
            &proposal(proposal_pool),
            &encoded,
            &config(payer, program),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "proposal_pool_not_in_transaction");
    }

    #[test]
    fn unapproved_program_is_rejected() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let allowed = Pubkey::new_unique();
        let actual = Pubkey::new_unique();
        let encoded = encoded_transaction(
            payer,
            pool,
            actual,
            Signature::default(),
        );

        let report = check_transaction(
            &proposal(pool),
            &encoded,
            &config(payer, allowed),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "transaction_uses_unapproved_program");
    }

    #[test]
    fn wrong_fee_payer_is_rejected() {
        let expected = Pubkey::new_unique();
        let actual = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let encoded = encoded_transaction(
            actual,
            pool,
            program,
            Signature::default(),
        );

        let report = check_transaction(
            &proposal(pool),
            &encoded,
            &config(expected, program),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "unexpected_fee_payer");
    }

    #[test]
    fn pre_signed_transaction_is_rejected_when_unsigned_is_required() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let encoded = encoded_transaction(
            payer,
            pool,
            program,
            Signature::new_unique(),
        );

        let report = check_transaction(
            &proposal(pool),
            &encoded,
            &config(payer, program),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "transaction_already_signed");
    }
}
