use anchor_lang::{InstructionData, ToAccountMetas};
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use commons::dlmm::accounts::BinArray;
use commons::dlmm::types::{
    LiquidityParameterByStrategy, StrategyParameters, StrategyType,
};
use commons::{
    derive_bin_array_bitmap_extension, derive_bin_array_pda,
    derive_event_authority_pda, derive_position_pda, dlmm,
    BinArrayExtension,
};
use serde::{Deserialize, Serialize};
use solana_sdk::instruction::Instruction;
use solana_sdk::message::{Message, VersionedMessage};
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use solana_sdk::transaction::VersionedTransaction;
use std::str::FromStr;

const SPL_TOKEN_PROGRAM: &str =
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const MAX_POSITION_WIDTH: i32 = 70;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StandardSplEntryRequest {
    pub lb_pair: String,
    pub sender: String,
    pub user_token_x: String,
    pub user_token_y: String,
    pub reserve_x: String,
    pub reserve_y: String,
    pub token_x_mint: String,
    pub token_y_mint: String,
    pub amount_x: u64,
    pub amount_y: u64,
    pub active_id: i32,
    pub min_bin_id: i32,
    pub max_bin_id: i32,
    pub max_active_bin_slippage: i32,
    pub strategy: String,
    pub initialize_bin_array_indexes: Vec<i32>,
    pub initialize_bitmap_extension: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StandardSplEntryBuildReport {
    pub transaction_base64: String,
    pub program_id: String,
    pub position: String,
    pub lb_pair: String,
    pub sender: String,
    pub min_bin_id: i32,
    pub max_bin_id: i32,
    pub width: i32,
    pub strategy: String,
    pub amount_x: u64,
    pub amount_y: u64,
    pub bin_array_lower: String,
    pub bin_array_upper: String,
    pub initialized_bin_arrays: Vec<String>,
    pub initializes_bitmap_extension: bool,
    pub instruction_count: usize,
}

fn parse_pubkey(name: &str, value: &str) -> Result<Pubkey> {
    Pubkey::from_str(value.trim())
        .with_context(|| format!("{name} is not a valid Solana pubkey"))
}

fn strategy_type(value: &str) -> Result<StrategyType> {
    Ok(match value {
        "SPOT" => StrategyType::SpotImBalanced,
        "CURVE" => StrategyType::CurveImBalanced,
        "BID_ASK" => StrategyType::BidAskImBalanced,
        other => anyhow::bail!("unsupported entry strategy: {other}"),
    })
}

fn build_strategy_parameters(
    request: &StandardSplEntryRequest,
) -> Result<StrategyParameters> {
    let mut parameteres = [0_u8; 64];
    // Matches the pinned TS SDK toStrategyParameters() convention.
    // 1 favors X in the active bin for single-sided X. Standard live entry
    // currently requires both sides or leaves this default at 0.
    parameteres[0] = 0;

    Ok(StrategyParameters {
        min_bin_id: request.min_bin_id,
        max_bin_id: request.max_bin_id,
        strategy_type: strategy_type(&request.strategy)?,
        parameteres,
    })
}

pub fn build_standard_spl_entry(
    request: &StandardSplEntryRequest,
) -> Result<StandardSplEntryBuildReport> {
    if request.amount_x == 0 && request.amount_y == 0 {
        anyhow::bail!("entry requires a non-zero token amount");
    }
    if request.min_bin_id > request.max_bin_id {
        anyhow::bail!("entry min_bin_id cannot exceed max_bin_id");
    }
    if request.max_active_bin_slippage < 0 {
        anyhow::bail!("max_active_bin_slippage cannot be negative");
    }

    let width = request
        .max_bin_id
        .checked_sub(request.min_bin_id)
        .and_then(|value| value.checked_add(1))
        .context("entry position width overflow")?;
    if width <= 0 || width > MAX_POSITION_WIDTH {
        anyhow::bail!(
            "standard live entry width {width} exceeds supported range 1..={MAX_POSITION_WIDTH}"
        );
    }

    let lb_pair = parse_pubkey("lb_pair", &request.lb_pair)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let user_token_x = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y = parse_pubkey("user_token_y", &request.user_token_y)?;
    let reserve_x = parse_pubkey("reserve_x", &request.reserve_x)?;
    let reserve_y = parse_pubkey("reserve_y", &request.reserve_y)?;
    let token_x_mint = parse_pubkey("token_x_mint", &request.token_x_mint)?;
    let token_y_mint = parse_pubkey("token_y_mint", &request.token_y_mint)?;
    let token_program = Pubkey::from_str(SPL_TOKEN_PROGRAM)
        .context("hard-coded SPL token program id is invalid")?;

    let (position, _) = derive_position_pda(
        lb_pair,
        sender,
        request.min_bin_id,
        width,
    );
    let bin_indexes = BinArray::get_bin_array_indexes_coverage(
        request.min_bin_id,
        request.max_bin_id,
    )?;
    if bin_indexes.is_empty() || bin_indexes.len() > 2 {
        anyhow::bail!(
            "standard live entry currently supports at most two bin arrays"
        );
    }
    let bin_array_lower = derive_bin_array_pda(
        lb_pair,
        i64::from(bin_indexes[0]),
    )
    .0;
    let bin_array_upper = derive_bin_array_pda(
        lb_pair,
        i64::from(*bin_indexes.last().unwrap()),
    )
    .0;

    for index in &request.initialize_bin_array_indexes {
        if !bin_indexes.contains(index) {
            anyhow::bail!(
                "requested bin-array initialization index {index} is outside the entry range"
            );
        }
    }

    let (bitmap_extension, _) =
        derive_bin_array_bitmap_extension(lb_pair);
    let (event_authority, _) = derive_event_authority_pda();

    let mut instructions: Vec<Instruction> = vec![];

    if request.initialize_bitmap_extension {
        let accounts =
            dlmm::client::accounts::InitializeBinArrayBitmapExtension {
                lb_pair,
                bin_array_bitmap_extension: bitmap_extension,
                funder: sender,
                system_program: solana_sdk::system_program::ID,
                rent: solana_sdk::sysvar::rent::ID,
            }
            .to_account_metas(None);
        instructions.push(Instruction {
            program_id: dlmm::ID,
            accounts,
            data: dlmm::client::args::InitializeBinArrayBitmapExtension {}
                .data(),
        });
    }

    let mut initialized_bin_arrays = vec![];
    let mut requested_indexes =
        request.initialize_bin_array_indexes.clone();
    requested_indexes.sort_unstable();
    requested_indexes.dedup();
    for index in requested_indexes {
        let bin_array =
            derive_bin_array_pda(lb_pair, i64::from(index)).0;
        let accounts = dlmm::client::accounts::InitializeBinArray {
            lb_pair,
            bin_array,
            funder: sender,
            system_program: solana_sdk::system_program::ID,
        }
        .to_account_metas(None);
        instructions.push(Instruction {
            program_id: dlmm::ID,
            accounts,
            data: dlmm::client::args::InitializeBinArray {
                index: i64::from(index),
            }
            .data(),
        });
        initialized_bin_arrays.push(bin_array.to_string());
    }

    let init_position_accounts =
        dlmm::client::accounts::InitializePositionPda {
            payer: sender,
            base: sender,
            position,
            lb_pair,
            owner: sender,
            system_program: solana_sdk::system_program::ID,
            rent: solana_sdk::sysvar::rent::ID,
            event_authority,
            program: dlmm::ID,
        }
        .to_account_metas(None);
    instructions.push(Instruction {
        program_id: dlmm::ID,
        accounts: init_position_accounts,
        data: dlmm::client::args::InitializePositionPda {
            lower_bin_id: request.min_bin_id,
            width,
        }
        .data(),
    });

    let add_accounts = dlmm::client::accounts::AddLiquidityByStrategy {
        position,
        lb_pair,
        bin_array_bitmap_extension: Some(bitmap_extension),
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

    let liquidity_parameter = LiquidityParameterByStrategy {
        amount_x: request.amount_x,
        amount_y: request.amount_y,
        active_id: request.active_id,
        max_active_bin_slippage: request.max_active_bin_slippage,
        strategy_parameters: build_strategy_parameters(request)?,
    };
    instructions.push(Instruction {
        program_id: dlmm::ID,
        accounts: add_accounts,
        data: dlmm::client::args::AddLiquidityByStrategy {
            liquidity_parameter,
        }
        .data(),
    });

    let message = Message::new(&instructions, Some(&sender));
    let required_signatures =
        usize::from(message.header.num_required_signatures);
    if required_signatures != 1 {
        anyhow::bail!(
            "entry transaction unexpectedly requires {required_signatures} signatures"
        );
    }
    let transaction = VersionedTransaction {
        signatures: vec![Signature::default(); required_signatures],
        message: VersionedMessage::Legacy(message),
    };
    let transaction_base64 = general_purpose::STANDARD.encode(
        bincode::serialize(&transaction)
            .context("failed to serialize entry transaction")?,
    );

    Ok(StandardSplEntryBuildReport {
        transaction_base64,
        program_id: dlmm::ID.to_string(),
        position: position.to_string(),
        lb_pair: lb_pair.to_string(),
        sender: sender.to_string(),
        min_bin_id: request.min_bin_id,
        max_bin_id: request.max_bin_id,
        width,
        strategy: request.strategy.clone(),
        amount_x: request.amount_x,
        amount_y: request.amount_y,
        bin_array_lower: bin_array_lower.to_string(),
        bin_array_upper: bin_array_upper.to_string(),
        initialized_bin_arrays,
        initializes_bitmap_extension: request.initialize_bitmap_extension,
        instruction_count: instructions.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode, TradeProposal};
    use crate::transaction_guard::{
        check_transaction, ProgramInstructionPolicy,
        TransactionGuardConfig,
    };
    use uuid::Uuid;

    fn request() -> StandardSplEntryRequest {
        StandardSplEntryRequest {
            lb_pair: Pubkey::new_unique().to_string(),
            sender: Pubkey::new_unique().to_string(),
            user_token_x: Pubkey::new_unique().to_string(),
            user_token_y: Pubkey::new_unique().to_string(),
            reserve_x: Pubkey::new_unique().to_string(),
            reserve_y: Pubkey::new_unique().to_string(),
            token_x_mint: Pubkey::new_unique().to_string(),
            token_y_mint: Pubkey::new_unique().to_string(),
            amount_x: 10_000,
            amount_y: 20_000,
            active_id: 0,
            min_bin_id: -5,
            max_bin_id: 5,
            max_active_bin_slippage: 3,
            strategy: "SPOT".into(),
            initialize_bin_array_indexes: BinArray::get_bin_array_indexes_coverage(
                -5, 5,
            )
            .unwrap(),
            initialize_bitmap_extension: true,
        }
    }

    #[test]
    fn entry_builds_single_wallet_unsigned_transaction() {
        let request = request();
        let report = build_standard_spl_entry(&request).unwrap();

        assert_eq!(report.width, 11);
        assert_eq!(report.strategy, "SPOT");
        assert_eq!(report.program_id, dlmm::ID.to_string());
        assert!(report.instruction_count >= 4);

        let proposal = TradeProposal {
            decision_id: Uuid::new_v4(),
            mode: Mode::Live,
            action: Action::Enter,
            pool_address: request.lb_pair.clone(),
            capital_quote: 10.0,
            account_equity_quote: 1_000.0,
            portfolio_deployed_quote: 100.0,
            daily_drawdown_pct: 0.0,
            min_bin_id: request.min_bin_id,
            max_bin_id: request.max_bin_id,
            strategy: request.strategy.clone(),
            expected_net_return_pct: 1.0,
            expected_downside_pct: 0.5,
            model_version: "test".into(),
            data_age_seconds: 1,
        };

        let guard = check_transaction(
            &proposal,
            &report.transaction_base64,
            &TransactionGuardConfig {
                expected_fee_payer: request.sender.clone(),
                allowed_program_ids: vec![dlmm::ID.to_string()],
                max_instructions: 8,
                max_static_accounts: 40,
                allow_address_lookup_tables: false,
                require_unsigned: true,
                require_proposal_pool_account: true,
                required_account_pubkeys: vec![
                    report.position.clone(),
                    request.user_token_x.clone(),
                    request.user_token_y.clone(),
                ],
                require_instruction_policy: true,
                instruction_policies: vec![ProgramInstructionPolicy {
                    program_id: dlmm::ID.to_string(),
                    allowed_actions: vec![Action::Enter],
                    allowed_data_prefixes_hex: vec![
                        "2f9de2b40cf02147".into(),
                        "235613b94ed44bd3".into(),
                        "2e527d92558de499".into(),
                        "0703967f94283dc8".into(),
                    ],
                }],
            },
        )
        .unwrap();

        assert!(guard.accepted, "{}", guard.reason);
        assert!(guard.signatures_all_default);
        assert_eq!(guard.required_signatures, 1);
    }

    #[test]
    fn unsupported_strategy_fails_closed() {
        let mut request = request();
        request.strategy = "MAGIC".into();
        assert!(build_standard_spl_entry(&request).is_err());
    }

    #[test]
    fn overly_wide_position_fails_closed() {
        let mut request = request();
        request.min_bin_id = 0;
        request.max_bin_id = 70;
        assert!(build_standard_spl_entry(&request).is_err());
    }

    #[test]
    fn bin_array_init_outside_range_fails_closed() {
        let mut request = request();
        request.initialize_bin_array_indexes = vec![999];
        assert!(build_standard_spl_entry(&request).is_err());
    }
}
