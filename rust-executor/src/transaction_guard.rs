use crate::models::{Action, TradeProposal};
use crate::simulation::decode_transaction_base64;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use solana_sdk::message::VersionedMessage;
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use std::collections::{BTreeMap, BTreeSet};
use std::str::FromStr;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProgramInstructionPolicy {
    pub program_id: String,
    pub allowed_actions: Vec<Action>,
    pub allowed_data_prefixes_hex: Vec<String>,
}


fn default_require_proposal_pool_account() -> bool {
    true
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransactionGuardConfig {
    pub expected_fee_payer: String,
    pub allowed_program_ids: Vec<String>,
    pub max_instructions: usize,
    pub max_static_accounts: usize,
    pub allow_address_lookup_tables: bool,
    pub require_unsigned: bool,
    #[serde(default = "default_require_proposal_pool_account")]
    pub require_proposal_pool_account: bool,
    #[serde(default)]
    pub required_account_pubkeys: Vec<String>,
    #[serde(default)]
    pub require_instruction_policy: bool,
    #[serde(default)]
    pub instruction_policies: Vec<ProgramInstructionPolicy>,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InstructionFingerprint {
    pub program_id: String,
    pub data_prefix_hex: String,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransactionGuardReport {
    pub accepted: bool,
    pub reason: String,
    pub fee_payer: String,
    pub pool_account_present: bool,
    pub required_accounts_present: bool,
    pub instruction_count: usize,
    pub static_account_count: usize,
    pub required_signatures: usize,
    pub signatures_all_default: bool,
    pub address_lookup_table_count: usize,
    pub program_ids: Vec<String>,
    pub instruction_fingerprints: Vec<InstructionFingerprint>,
}

struct InspectedInstruction {
    program_id: Pubkey,
    data: Vec<u8>,
}

struct InspectedTransaction {
    fee_payer: Pubkey,
    account_keys: Vec<Pubkey>,
    instructions: Vec<InspectedInstruction>,
    required_signatures: usize,
    signatures_all_default: bool,
    address_lookup_table_count: usize,
}

fn decode_hex(value: &str) -> Result<Vec<u8>> {
    let value = value.trim();
    if value.is_empty() || value.len() % 2 != 0 {
        anyhow::bail!("instruction data prefix must be non-empty even-length hex");
    }
    (0..value.len())
        .step_by(2)
        .map(|index| {
            u8::from_str_radix(&value[index..index + 2], 16)
                .with_context(|| format!("invalid hex instruction prefix: {value}"))
        })
        .collect()
}

fn encode_prefix(data: &[u8]) -> String {
    data.iter()
        .take(8)
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>()
}

fn validate_config(
    config: &TransactionGuardConfig,
) -> Result<(
    Pubkey,
    BTreeSet<Pubkey>,
    BTreeMap<Pubkey, Vec<(Vec<Action>, Vec<u8>)>>,
    BTreeSet<Pubkey>,
)> {
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

    let mut required_accounts = BTreeSet::new();
    for raw in &config.required_account_pubkeys {
        required_accounts.insert(
            Pubkey::from_str(raw.trim())
                .with_context(|| format!("invalid required account pubkey: {raw}"))?,
        );
    }
    if !config.require_proposal_pool_account && required_accounts.is_empty() {
        anyhow::bail!(
            "disabling proposal-pool binding requires at least one explicit required account"
        );
    }

    let mut policies: BTreeMap<
        Pubkey,
        Vec<(Vec<Action>, Vec<u8>)>,
    > = BTreeMap::new();
    for policy in &config.instruction_policies {
        let program = Pubkey::from_str(policy.program_id.trim())
            .with_context(|| {
                format!(
                    "invalid instruction policy program id: {}",
                    policy.program_id
                )
            })?;
        if !programs.contains(&program) {
            anyhow::bail!(
                "instruction policy program {program} is not in allowed_program_ids"
            );
        }
        if policy.allowed_actions.is_empty() {
            anyhow::bail!(
                "instruction policy for {program} has no allowed actions"
            );
        }
        if policy.allowed_data_prefixes_hex.is_empty() {
            anyhow::bail!(
                "instruction policy for {program} has no allowed data prefixes"
            );
        }
        let entry = policies.entry(program).or_default();
        for prefix in &policy.allowed_data_prefixes_hex {
            entry.push((
                policy.allowed_actions.clone(),
                decode_hex(prefix)?,
            ));
        }
    }

    Ok((payer, programs, policies, required_accounts))
}

fn inspect_transaction(encoded: &str) -> Result<InspectedTransaction> {
    let transaction = decode_transaction_base64(encoded)?;
    let signatures_all_default = transaction
        .signatures
        .iter()
        .all(|signature| *signature == Signature::default());

    let (
        fee_payer,
        account_keys,
        compiled_instructions,
        required_signatures,
        address_lookup_table_count,
    ) = match &transaction.message {
        VersionedMessage::Legacy(message) => (
            *message
                .account_keys
                .first()
                .context("transaction has no static account keys")?,
            message.account_keys.clone(),
            message.instructions.clone(),
            usize::from(message.header.num_required_signatures),
            0,
        ),
        VersionedMessage::V0(message) => (
            *message
                .account_keys
                .first()
                .context("transaction has no static account keys")?,
            message.account_keys.clone(),
            message.instructions.clone(),
            usize::from(message.header.num_required_signatures),
            message.address_table_lookups.len(),
        ),
    };

    if transaction.signatures.len() != required_signatures {
        anyhow::bail!(
            "signature vector length {} does not match required signer count {}",
            transaction.signatures.len(),
            required_signatures
        );
    }

    let mut instructions = Vec::with_capacity(compiled_instructions.len());
    for instruction in compiled_instructions {
        let index = usize::from(instruction.program_id_index);
        let program = account_keys.get(index).with_context(|| {
            format!(
                "instruction program index {index} is not a static account; transaction guard does not resolve lookup-table program ids"
            )
        })?;
        instructions.push(InspectedInstruction {
            program_id: *program,
            data: instruction.data,
        });
    }

    Ok(InspectedTransaction {
        fee_payer,
        account_keys,
        instructions,
        required_signatures,
        signatures_all_default,
        address_lookup_table_count,
    })
}

pub fn check_transaction(
    proposal: &TradeProposal,
    encoded: &str,
    config: &TransactionGuardConfig,
) -> Result<TransactionGuardReport> {
    let (
        expected_payer,
        allowed_programs,
        policies,
        required_accounts,
    ) = validate_config(config)?;
    let pool = Pubkey::from_str(proposal.pool_address.trim())
        .context("proposal pool_address is not a valid Solana pubkey")?;
    let inspected = inspect_transaction(encoded)?;

    let pool_account_present = inspected.account_keys.contains(&pool);
    let required_accounts_present = required_accounts
        .iter()
        .all(|account| inspected.account_keys.contains(account));
    let unique_program_ids: BTreeSet<Pubkey> = inspected
        .instructions
        .iter()
        .map(|instruction| instruction.program_id)
        .collect();
    let program_ids = unique_program_ids
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    let instruction_fingerprints = inspected
        .instructions
        .iter()
        .map(|instruction| InstructionFingerprint {
            program_id: instruction.program_id.to_string(),
            data_prefix_hex: encode_prefix(&instruction.data),
        })
        .collect::<Vec<_>>();

    let unapproved_program = unique_program_ids
        .iter()
        .any(|program| !allowed_programs.contains(program));

    let instruction_policy_failure = inspected.instructions.iter().any(
        |instruction| {
            match policies.get(&instruction.program_id) {
                Some(rules) => !rules.iter().any(|(actions, prefix)| {
                    actions.contains(&proposal.action)
                        && instruction.data.starts_with(prefix)
                }),
                None => config.require_instruction_policy,
            }
        },
    );

    let rejection = if inspected.fee_payer != expected_payer {
        Some("unexpected_fee_payer")
    } else if inspected.required_signatures == 0 {
        Some("transaction_has_no_required_signer")
    } else if inspected.instructions.is_empty() {
        Some("transaction_has_no_instructions")
    } else if inspected.instructions.len() > config.max_instructions {
        Some("instruction_count_limit")
    } else if inspected.account_keys.len() > config.max_static_accounts {
        Some("static_account_count_limit")
    } else if inspected.address_lookup_table_count > 0
        && !config.allow_address_lookup_tables
    {
        Some("address_lookup_tables_not_allowed")
    } else if config.require_proposal_pool_account && !pool_account_present {
        Some("proposal_pool_not_in_transaction")
    } else if !required_accounts_present {
        Some("required_account_not_in_transaction")
    } else if unapproved_program {
        Some("transaction_uses_unapproved_program")
    } else if instruction_policy_failure {
        Some("transaction_instruction_not_allowed")
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
        required_accounts_present,
        instruction_count: inspected.instructions.len(),
        static_account_count: inspected.account_keys.len(),
        required_signatures: inspected.required_signatures,
        signatures_all_default: inspected.signatures_all_default,
        address_lookup_table_count: inspected.address_lookup_table_count,
        program_ids,
        instruction_fingerprints,
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
        data: Vec<u8>,
    ) -> String {
        let instruction = Instruction {
            program_id: program,
            accounts: vec![
                solana_sdk::instruction::AccountMeta::new_readonly(pool, false),
            ],
            data,
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
            require_proposal_pool_account: true,
            required_account_pubkeys: vec![],
            require_instruction_policy: true,
            instruction_policies: vec![ProgramInstructionPolicy {
                program_id: program.to_string(),
                allowed_actions: vec![Action::Enter],
                allowed_data_prefixes_hex: vec!["0102".into()],
            }],
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
            vec![1, 2, 3],
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
        assert_eq!(
            report.instruction_fingerprints[0].data_prefix_hex,
            "010203"
        );
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
            vec![1, 2, 3],
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
            vec![1, 2, 3],
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
    fn unapproved_instruction_data_is_rejected() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let encoded = encoded_transaction(
            payer,
            pool,
            program,
            Signature::default(),
            vec![9, 9, 9],
        );

        let report = check_transaction(
            &proposal(pool),
            &encoded,
            &config(payer, program),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "transaction_instruction_not_allowed"
        );
    }

    #[test]
    fn wrong_action_for_instruction_policy_is_rejected() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let encoded = encoded_transaction(
            payer,
            pool,
            program,
            Signature::default(),
            vec![1, 2, 3],
        );
        let mut p = proposal(pool);
        p.action = Action::Exit;

        let report = check_transaction(
            &p,
            &encoded,
            &config(payer, program),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "transaction_instruction_not_allowed"
        );
    }

    #[test]
    fn explicit_required_account_can_replace_pool_binding() {
        let payer = Pubkey::new_unique();
        let proposal_pool = Pubkey::new_unique();
        let target = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let encoded = encoded_transaction(
            payer,
            target,
            program,
            Signature::default(),
            vec![1, 2, 3],
        );
        let mut cfg = config(payer, program);
        cfg.require_proposal_pool_account = false;
        cfg.required_account_pubkeys = vec![target.to_string()];

        let report = check_transaction(
            &proposal(proposal_pool),
            &encoded,
            &cfg,
        )
        .unwrap();

        assert!(report.accepted);
        assert!(!report.pool_account_present);
        assert!(report.required_accounts_present);
    }

    #[test]
    fn disabling_pool_binding_without_required_account_fails_config() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let encoded = encoded_transaction(
            payer,
            pool,
            program,
            Signature::default(),
            vec![1, 2, 3],
        );
        let mut cfg = config(payer, program);
        cfg.require_proposal_pool_account = false;
        cfg.required_account_pubkeys.clear();

        assert!(
            check_transaction(&proposal(pool), &encoded, &cfg).is_err()
        );
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
            vec![1, 2, 3],
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
            vec![1, 2, 3],
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
    #[test]
    fn checked_in_phase6_policy_is_action_strict() {
        let raw = include_str!(
            "../../contracts/examples/transaction_guard.phase6.example.json"
        );
        let cfg: TransactionGuardConfig = serde_json::from_str(raw).unwrap();
        assert!(cfg.require_instruction_policy);
        assert!(cfg.require_proposal_pool_account);
        assert!(cfg.require_unsigned);
        assert!(!cfg.allow_address_lookup_tables);

        let prefixes_for = |action: Action| {
            let mut prefixes = cfg
                .instruction_policies
                .iter()
                .filter(|policy| policy.allowed_actions.contains(&action))
                .flat_map(|policy| {
                    policy.allowed_data_prefixes_hex.iter().cloned()
                })
                .collect::<Vec<_>>();
            prefixes.sort();
            prefixes
        };

        let mut enter = vec![
            "2f9de2b40cf02147".to_string(),
            "235613b94ed44bd3".to_string(),
            "2e527d92558de499".to_string(),
            "0703967f94283dc8".to_string(),
            "03dd95da6f8d76d5".to_string(),
        ];
        enter.sort();
        assert_eq!(prefixes_for(Action::Enter), enter);

        let mut rebalance = vec![
            "235613b94ed44bd3".to_string(),
            "5c04b0c177b95309".to_string(),
        ];
        rebalance.sort();
        assert_eq!(prefixes_for(Action::Rebalance), rebalance);

        let mut exit = vec![
            "0a333d2370691855".to_string(),
            "e6d7527ff165e392".to_string(),
            "70bf65ab1c907fbb".to_string(),
            "be037f77b2579db7".to_string(),
            "ae5a2373ba2893e2".to_string(),
        ];
        exit.sort();
        assert_eq!(prefixes_for(Action::Exit), exit);
    }

}
