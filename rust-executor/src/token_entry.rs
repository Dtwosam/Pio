use anchor_lang::{InstructionData, ToAccountMetas};
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use commons::dlmm::accounts::{BinArray, LbPair};
use commons::dlmm::types::{
    LiquidityParameterByStrategy, StrategyParameters, StrategyType,
};
use commons::{
    derive_bin_array_bitmap_extension, derive_bin_array_pda,
    derive_event_authority_pda, derive_position_pda, dlmm,
    pod_read_unaligned_skip_disc, BinArrayExtension, LbPairExtension,
};
use serde::{Deserialize, Serialize};
use solana_client::rpc_client::RpcClient;
use solana_sdk::instruction::Instruction;
use solana_sdk::message::{Message, VersionedMessage};
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use solana_sdk::transaction::VersionedTransaction;
use std::str::FromStr;

use crate::token_extensions::{
    parse_supported_token_program, resolve_liquidity_remaining_accounts,
    validate_mint_owner, validate_token_account_base,
    validate_token_vault_base, TOKEN_2022_PROGRAM,
};

const MAX_POSITION_WIDTH: i32 = 70;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedTokenEntryRequest {
    pub lb_pair: String,
    pub sender: String,
    pub user_token_x: String,
    pub user_token_y: String,
    pub amount_x: u64,
    pub amount_y: u64,
    pub min_bin_id: i32,
    pub max_bin_id: i32,
    pub max_active_bin_slippage: i32,
    pub strategy: String,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenEntryValidation {
    pub lb_pair: String,
    pub position: String,
    pub sender: String,
    pub token_x_program: String,
    pub token_y_program: String,
    pub token_x_is_2022: bool,
    pub token_y_is_2022: bool,
    pub user_token_x_balance: u64,
    pub user_token_y_balance: u64,
    pub bin_array_indexes: Vec<i32>,
    pub missing_bin_array_indexes: Vec<i32>,
    pub bitmap_extension_exists: bool,
    pub transfer_hook_account_count: usize,
    pub position_does_not_exist: bool,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenEntryBuildReport {
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
    pub token_x_program: String,
    pub token_y_program: String,
    pub transfer_hook_account_count: usize,
    pub initialized_bin_arrays: Vec<String>,
    pub initializes_bitmap_extension: bool,
    pub instruction_count: usize,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedTokenEntryReport {
    pub validation: TokenEntryValidation,
    pub build: TokenEntryBuildReport,
}


fn parse_pubkey(name: &str, value: &str) -> Result<Pubkey> {
    Pubkey::from_str(value.trim())
        .with_context(|| format!("{name} is not a valid Solana pubkey"))
}


fn require_account<'a>(
    name: &str,
    account: &'a Option<solana_sdk::account::Account>,
) -> Result<&'a solana_sdk::account::Account> {
    account
        .as_ref()
        .with_context(|| format!("{name} account is missing"))
}


fn strategy_type(value: &str) -> Result<StrategyType> {
    Ok(match value {
        "SPOT" => StrategyType::SpotImBalanced,
        "CURVE" => StrategyType::CurveImBalanced,
        "BID_ASK" => StrategyType::BidAskImBalanced,
        other => anyhow::bail!("unsupported entry strategy: {other}"),
    })
}


fn strategy_parameters(
    request: &ChainResolvedTokenEntryRequest,
) -> Result<StrategyParameters> {
    Ok(StrategyParameters {
        min_bin_id: request.min_bin_id,
        max_bin_id: request.max_bin_id,
        strategy_type: strategy_type(&request.strategy)?,
        parameteres: [0_u8; 64],
    })
}


pub async fn build_token_entry_from_chain(
    rpc_url: &str,
    request: &ChainResolvedTokenEntryRequest,
) -> Result<ChainResolvedTokenEntryReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
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
            "token-aware live entry width {width} exceeds supported range 1..={MAX_POSITION_WIDTH}"
        );
    }

    let lb_pair_key = parse_pubkey("lb_pair", &request.lb_pair)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let user_token_x = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y = parse_pubkey("user_token_y", &request.user_token_y)?;
    let client = RpcClient::new(rpc_url.to_string());

    let lb_pair_account = client
        .get_account(&lb_pair_key)
        .context("failed to fetch entry DLMM pool")?;
    if lb_pair_account.owner != dlmm::ID {
        anyhow::bail!("entry pool is not owned by the Meteora DLMM program");
    }
    let lb_pair: LbPair =
        pod_read_unaligned_skip_disc(&lb_pair_account.data)
            .context("failed to decode Meteora LbPair")?;
    let [token_x_program, token_y_program] = lb_pair.get_token_programs()?;
    parse_supported_token_program(&token_x_program)?;
    parse_supported_token_program(&token_y_program)?;

    let (position, _) = derive_position_pda(
        lb_pair_key,
        sender,
        request.min_bin_id,
        width,
    );
    let bin_array_indexes = BinArray::get_bin_array_indexes_coverage(
        request.min_bin_id,
        request.max_bin_id,
    )?;
    if bin_array_indexes.is_empty() || bin_array_indexes.len() > 2 {
        anyhow::bail!(
            "token-aware live entry currently supports at most two bin arrays"
        );
    }
    let bin_array_keys = bin_array_indexes
        .iter()
        .map(|index| derive_bin_array_pda(lb_pair_key, i64::from(*index)).0)
        .collect::<Vec<_>>();
    let bin_array_metas =
        BinArray::get_bin_array_account_metas_coverage(
            request.min_bin_id,
            request.max_bin_id,
            lb_pair_key,
        )?;
    let (bitmap_extension, _) =
        derive_bin_array_bitmap_extension(lb_pair_key);

    let mut keys = vec![
        user_token_x,
        user_token_y,
        lb_pair.reserve_x,
        lb_pair.reserve_y,
        lb_pair.token_x_mint,
        lb_pair.token_y_mint,
        position,
        bitmap_extension,
    ];
    keys.extend(bin_array_keys.iter().copied());
    let accounts = client
        .get_multiple_accounts(&keys)
        .context("failed to fetch token-aware entry dependent accounts")?;
    if accounts.len() != keys.len() {
        anyhow::bail!("unexpected token-aware entry account fetch result");
    }

    let balance_x = validate_token_account_base(
        "user_token_x",
        require_account("user_token_x", &accounts[0])?,
        &lb_pair.token_x_mint,
        &sender,
        &token_x_program,
    )?;
    let balance_y = validate_token_account_base(
        "user_token_y",
        require_account("user_token_y", &accounts[1])?,
        &lb_pair.token_y_mint,
        &sender,
        &token_y_program,
    )?;
    if balance_x < request.amount_x {
        anyhow::bail!(
            "user_token_x balance {balance_x} is below requested amount {}",
            request.amount_x
        );
    }
    if balance_y < request.amount_y {
        anyhow::bail!(
            "user_token_y balance {balance_y} is below requested amount {}",
            request.amount_y
        );
    }

    validate_token_vault_base(
        "reserve_x",
        require_account("reserve_x", &accounts[2])?,
        &lb_pair.token_x_mint,
        &token_x_program,
    )?;
    validate_token_vault_base(
        "reserve_y",
        require_account("reserve_y", &accounts[3])?,
        &lb_pair.token_y_mint,
        &token_y_program,
    )?;
    validate_mint_owner(
        "token_x_mint",
        require_account("token_x_mint", &accounts[4])?,
        &token_x_program,
    )?;
    validate_mint_owner(
        "token_y_mint",
        require_account("token_y_mint", &accounts[5])?,
        &token_y_program,
    )?;

    if accounts[6].is_some() {
        anyhow::bail!(
            "derived entry position already exists; concurrent duplicate range is blocked"
        );
    }

    let bitmap_extension_exists = match &accounts[7] {
        Some(account) => {
            if account.owner != dlmm::ID {
                anyhow::bail!(
                    "bin-array bitmap extension is not owned by Meteora DLMM"
                );
            }
            true
        }
        None => false,
    };

    let mut missing_bin_array_indexes = vec![];
    for (offset, index) in bin_array_indexes.iter().enumerate() {
        match &accounts[8 + offset] {
            Some(account) => {
                if account.owner != dlmm::ID {
                    anyhow::bail!(
                        "entry bin-array account is not owned by Meteora DLMM"
                    );
                }
            }
            None => missing_bin_array_indexes.push(*index),
        }
    }

    let remaining =
        resolve_liquidity_remaining_accounts(rpc_url, &lb_pair).await?;
    let hook_count = remaining.accounts.len();
    let mut instructions = vec![];

    if !bitmap_extension_exists {
        let accounts =
            dlmm::client::accounts::InitializeBinArrayBitmapExtension {
                lb_pair: lb_pair_key,
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
    for index in &missing_bin_array_indexes {
        let bin_array =
            derive_bin_array_pda(lb_pair_key, i64::from(*index)).0;
        let accounts = dlmm::client::accounts::InitializeBinArray {
            lb_pair: lb_pair_key,
            bin_array,
            funder: sender,
            system_program: solana_sdk::system_program::ID,
        }
        .to_account_metas(None);
        instructions.push(Instruction {
            program_id: dlmm::ID,
            accounts,
            data: dlmm::client::args::InitializeBinArray {
                index: i64::from(*index),
            }
            .data(),
        });
        initialized_bin_arrays.push(bin_array.to_string());
    }

    let (event_authority, _) = derive_event_authority_pda();
    let init_accounts = dlmm::client::accounts::InitializePositionPda {
        payer: sender,
        base: sender,
        position,
        lb_pair: lb_pair_key,
        owner: sender,
        system_program: solana_sdk::system_program::ID,
        rent: solana_sdk::sysvar::rent::ID,
        event_authority,
        program: dlmm::ID,
    }
    .to_account_metas(None);
    instructions.push(Instruction {
        program_id: dlmm::ID,
        accounts: init_accounts,
        data: dlmm::client::args::InitializePositionPda {
            lower_bin_id: request.min_bin_id,
            width,
        }
        .data(),
    });

    let mut add_accounts =
        dlmm::client::accounts::AddLiquidityByStrategy2 {
            position,
            lb_pair: lb_pair_key,
            bin_array_bitmap_extension: Some(bitmap_extension),
            user_token_x,
            user_token_y,
            reserve_x: lb_pair.reserve_x,
            reserve_y: lb_pair.reserve_y,
            token_x_mint: lb_pair.token_x_mint,
            token_y_mint: lb_pair.token_y_mint,
            sender,
            token_x_program,
            token_y_program,
            event_authority,
            program: dlmm::ID,
        }
        .to_account_metas(None);
    add_accounts.extend(remaining.accounts);
    add_accounts.extend(bin_array_metas);

    instructions.push(Instruction {
        program_id: dlmm::ID,
        accounts: add_accounts,
        data: dlmm::client::args::AddLiquidityByStrategy2 {
            liquidity_parameter: LiquidityParameterByStrategy {
                amount_x: request.amount_x,
                amount_y: request.amount_y,
                active_id: lb_pair.active_id,
                max_active_bin_slippage: request.max_active_bin_slippage,
                strategy_parameters: strategy_parameters(request)?,
            },
            remaining_accounts_info: remaining.info,
        }
        .data(),
    });

    let message = Message::new(&instructions, Some(&sender));
    let required_signatures =
        usize::from(message.header.num_required_signatures);
    if required_signatures != 1 {
        anyhow::bail!(
            "token-aware entry unexpectedly requires {required_signatures} signatures"
        );
    }
    let transaction = VersionedTransaction {
        signatures: vec![Signature::default(); required_signatures],
        message: VersionedMessage::Legacy(message),
    };
    let transaction_base64 = general_purpose::STANDARD.encode(
        bincode::serialize(&transaction)
            .context("failed to serialize token-aware entry transaction")?,
    );

    let token_2022 = Pubkey::from_str(TOKEN_2022_PROGRAM)
        .context("hard-coded Token-2022 program id is invalid")?;
    let build = TokenEntryBuildReport {
        transaction_base64,
        program_id: dlmm::ID.to_string(),
        position: position.to_string(),
        lb_pair: lb_pair_key.to_string(),
        sender: sender.to_string(),
        min_bin_id: request.min_bin_id,
        max_bin_id: request.max_bin_id,
        width,
        strategy: request.strategy.clone(),
        amount_x: request.amount_x,
        amount_y: request.amount_y,
        token_x_program: token_x_program.to_string(),
        token_y_program: token_y_program.to_string(),
        transfer_hook_account_count: hook_count,
        initialized_bin_arrays,
        initializes_bitmap_extension: !bitmap_extension_exists,
        instruction_count: instructions.len(),
    };

    Ok(ChainResolvedTokenEntryReport {
        validation: TokenEntryValidation {
            lb_pair: lb_pair_key.to_string(),
            position: position.to_string(),
            sender: sender.to_string(),
            token_x_program: token_x_program.to_string(),
            token_y_program: token_y_program.to_string(),
            token_x_is_2022: token_x_program == token_2022,
            token_y_is_2022: token_y_program == token_2022,
            user_token_x_balance: balance_x,
            user_token_y_balance: balance_y,
            bin_array_indexes,
            missing_bin_array_indexes,
            bitmap_extension_exists,
            transfer_hook_account_count: hook_count,
            position_does_not_exist: true,
        },
        build,
    })
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_strategy_fails_before_rpc() {
        let request = ChainResolvedTokenEntryRequest {
            lb_pair: Pubkey::new_unique().to_string(),
            sender: Pubkey::new_unique().to_string(),
            user_token_x: Pubkey::new_unique().to_string(),
            user_token_y: Pubkey::new_unique().to_string(),
            amount_x: 1,
            amount_y: 1,
            min_bin_id: -1,
            max_bin_id: 1,
            max_active_bin_slippage: 1,
            strategy: "INVALID".into(),
        };
        assert!(strategy_parameters(&request).is_err());
    }

    #[test]
    fn strategy_parameters_match_requested_range() {
        let request = ChainResolvedTokenEntryRequest {
            lb_pair: Pubkey::new_unique().to_string(),
            sender: Pubkey::new_unique().to_string(),
            user_token_x: Pubkey::new_unique().to_string(),
            user_token_y: Pubkey::new_unique().to_string(),
            amount_x: 1,
            amount_y: 1,
            min_bin_id: -7,
            max_bin_id: 9,
            max_active_bin_slippage: 1,
            strategy: "CURVE".into(),
        };
        let params = strategy_parameters(&request).unwrap();
        assert_eq!(params.min_bin_id, -7);
        assert_eq!(params.max_bin_id, 9);
        assert!(matches!(
            params.strategy_type,
            StrategyType::CurveImBalanced
        ));
    }
}
