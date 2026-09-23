use anchor_lang::{InstructionData, ToAccountMetas};
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use commons::{derive_event_authority_pda, dlmm};
use serde::{Deserialize, Serialize};
use solana_sdk::instruction::Instruction;
use solana_sdk::message::{Message, VersionedMessage};
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use solana_sdk::transaction::VersionedTransaction;
use std::str::FromStr;


const SPL_TOKEN_PROGRAM: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmergencyExitRequest {
    pub position: String,
    pub lb_pair: String,
    pub bin_array_bitmap_extension: Option<String>,
    pub user_token_x: String,
    pub user_token_y: String,
    pub reserve_x: String,
    pub reserve_y: String,
    pub token_x_mint: String,
    pub token_y_mint: String,
    pub bin_array_lower: String,
    pub bin_array_upper: String,
    pub sender: String,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmergencyExitBuildReport {
    pub transaction_base64: String,
    pub program_id: String,
    pub position: String,
    pub lb_pair: String,
    pub sender: String,
    pub removes_all_liquidity: bool,
    pub claims_fees_or_rewards: bool,
    pub closes_position_account: bool,
}


fn parse_pubkey(name: &str, value: &str) -> Result<Pubkey> {
    Pubkey::from_str(value.trim())
        .with_context(|| format!("{name} is not a valid Solana pubkey"))
}


pub fn build_standard_spl_emergency_exit(
    request: &EmergencyExitRequest,
) -> Result<EmergencyExitBuildReport> {
    let position = parse_pubkey("position", &request.position)?;
    let lb_pair = parse_pubkey("lb_pair", &request.lb_pair)?;
    let user_token_x = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y = parse_pubkey("user_token_y", &request.user_token_y)?;
    let reserve_x = parse_pubkey("reserve_x", &request.reserve_x)?;
    let reserve_y = parse_pubkey("reserve_y", &request.reserve_y)?;
    let token_x_mint = parse_pubkey("token_x_mint", &request.token_x_mint)?;
    let token_y_mint = parse_pubkey("token_y_mint", &request.token_y_mint)?;
    let bin_array_lower =
        parse_pubkey("bin_array_lower", &request.bin_array_lower)?;
    let bin_array_upper =
        parse_pubkey("bin_array_upper", &request.bin_array_upper)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let token_program = Pubkey::from_str(SPL_TOKEN_PROGRAM)
        .context("hard-coded SPL token program id is invalid")?;
    let bitmap_extension = match request.bin_array_bitmap_extension.as_deref() {
        Some(value) => parse_pubkey("bin_array_bitmap_extension", value)?,
        None => dlmm::ID,
    };
    let (event_authority, _) = derive_event_authority_pda();

    let accounts = dlmm::client::accounts::RemoveAllLiquidity {
        position,
        lb_pair,
        bin_array_bitmap_extension: bitmap_extension,
        user_token_x,
        user_token_y,
        reserve_x,
        reserve_y,
        token_x_mint,
        token_y_mint,
        bin_array_lower,
        bin_array_upper,
        sender,
        token_x_program: token_program,
        token_y_program: token_program,
        event_authority,
        program: dlmm::ID,
    }
    .to_account_metas(None);

    let data = dlmm::client::args::RemoveAllLiquidity {}.data();
    let instruction = Instruction {
        program_id: dlmm::ID,
        accounts,
        data,
    };
    let message = Message::new(&[instruction], Some(&sender));
    let required_signatures =
        usize::from(message.header.num_required_signatures);
    if required_signatures != 1 {
        anyhow::bail!(
            "emergency exit unexpectedly requires {required_signatures} signatures"
        );
    }
    let transaction = VersionedTransaction {
        signatures: vec![Signature::default(); required_signatures],
        message: VersionedMessage::Legacy(message),
    };
    let transaction_base64 = general_purpose::STANDARD.encode(
        bincode::serialize(&transaction)
            .context("failed to serialize emergency exit transaction")?,
    );

    Ok(EmergencyExitBuildReport {
        transaction_base64,
        program_id: dlmm::ID.to_string(),
        position: position.to_string(),
        lb_pair: lb_pair.to_string(),
        sender: sender.to_string(),
        removes_all_liquidity: true,
        claims_fees_or_rewards: false,
        closes_position_account: false,
    })
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode, TradeProposal};
    use crate::transaction_guard::{
        check_transaction, ProgramInstructionPolicy, TransactionGuardConfig,
    };
    use uuid::Uuid;

    fn request() -> EmergencyExitRequest {
        EmergencyExitRequest {
            position: Pubkey::new_unique().to_string(),
            lb_pair: Pubkey::new_unique().to_string(),
            bin_array_bitmap_extension: None,
            user_token_x: Pubkey::new_unique().to_string(),
            user_token_y: Pubkey::new_unique().to_string(),
            reserve_x: Pubkey::new_unique().to_string(),
            reserve_y: Pubkey::new_unique().to_string(),
            token_x_mint: Pubkey::new_unique().to_string(),
            token_y_mint: Pubkey::new_unique().to_string(),
            bin_array_lower: Pubkey::new_unique().to_string(),
            bin_array_upper: Pubkey::new_unique().to_string(),
            sender: Pubkey::new_unique().to_string(),
        }
    }

    #[test]
    fn emergency_exit_builds_unsigned_dlmm_transaction() {
        let request = request();
        let report = build_standard_spl_emergency_exit(&request).unwrap();

        assert!(report.removes_all_liquidity);
        assert!(!report.claims_fees_or_rewards);
        assert!(!report.closes_position_account);
        assert_eq!(report.program_id, dlmm::ID.to_string());

        let proposal = TradeProposal {
            decision_id: Uuid::new_v4(),
            mode: Mode::Live,
            action: Action::Exit,
            pool_address: request.lb_pair.clone(),
            capital_quote: 0.0,
            account_equity_quote: 0.0,
            portfolio_deployed_quote: 0.0,
            daily_drawdown_pct: 99.0,
            min_bin_id: 0,
            max_bin_id: 0,
            strategy: "SPOT".into(),
            expected_net_return_pct: 0.0,
            expected_downside_pct: 0.0,
            model_version: "emergency".into(),
            data_age_seconds: u64::MAX,
        };
        let guard = check_transaction(
            &proposal,
            &report.transaction_base64,
            &TransactionGuardConfig {
                expected_fee_payer: request.sender.clone(),
                allowed_program_ids: vec![dlmm::ID.to_string()],
                max_instructions: 2,
                max_static_accounts: 32,
                allow_address_lookup_tables: false,
                require_unsigned: true,
                require_instruction_policy: true,
                instruction_policies: vec![ProgramInstructionPolicy {
                    program_id: dlmm::ID.to_string(),
                    allowed_data_prefixes_hex: vec![
                        "0a333d2370691855".into(),
                    ],
                }],
            },
        )
        .unwrap();

        assert!(guard.accepted);
        assert!(guard.pool_account_present);
        assert!(guard.signatures_all_default);
    }

    #[test]
    fn invalid_account_key_fails_closed() {
        let mut request = request();
        request.position = "not-a-pubkey".into();
        assert!(build_standard_spl_emergency_exit(&request).is_err());
    }
}
