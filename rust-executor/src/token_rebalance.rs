use anchor_lang::{InstructionData, ToAccountMetas};
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use commons::dlmm::accounts::{BinArray, LbPair, PositionV2};
use commons::dlmm::types::{
    AddLiquidityParams, RebalanceLiquidityParams, RemoveLiquidityParams,
};
use commons::{
    derive_bin_array_pda, derive_event_authority_pda, dlmm,
    pod_read_unaligned_skip_disc, BinArrayExtension, LbPairExtension,
};
use serde::{Deserialize, Serialize};
use solana_client::rpc_client::RpcClient;
use solana_sdk::instruction::{AccountMeta, Instruction};
use solana_sdk::message::{Message, VersionedMessage};
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use solana_sdk::transaction::VersionedTransaction;
use std::collections::BTreeSet;
use std::str::FromStr;

use crate::rebalance::{RebalanceAddPlan, RebalanceRemovePlan};
use crate::token_extensions::{
    parse_supported_token_program, resolve_liquidity_remaining_accounts,
    validate_mint_owner, validate_token_account_base,
    validate_token_vault_base, TOKEN_2022_PROGRAM,
};

const MEMO_PROGRAM: &str =
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";
const MIN_DEFAULT_BITMAP_INDEX: i32 = -512;
const MAX_DEFAULT_BITMAP_INDEX: i32 = 511;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedTokenRebalanceRequest {
    pub position: String,
    pub sender: String,
    pub user_token_x: String,
    pub user_token_y: String,
    pub max_active_bin_slippage: u16,
    pub should_claim_fee: bool,
    pub should_claim_reward: bool,
    pub min_withdraw_x_amount: u64,
    pub max_deposit_x_amount: u64,
    pub min_withdraw_y_amount: u64,
    pub max_deposit_y_amount: u64,
    pub shrink_mode: u8,
    pub removes: Vec<RebalanceRemovePlan>,
    pub adds: Vec<RebalanceAddPlan>,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenRebalanceValidation {
    pub position: String,
    pub lb_pair: String,
    pub owner_matches_sender: bool,
    pub token_x_program: String,
    pub token_y_program: String,
    pub token_x_is_2022: bool,
    pub token_y_is_2022: bool,
    pub active_id: i32,
    pub bin_array_indexes: Vec<i32>,
    pub missing_bin_array_indexes: Vec<i32>,
    pub transfer_hook_account_count: usize,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenRebalanceBuildReport {
    pub transaction_base64: String,
    pub program_id: String,
    pub position: String,
    pub lb_pair: String,
    pub sender: String,
    pub token_x_program: String,
    pub token_y_program: String,
    pub bin_array_indexes: Vec<i32>,
    pub initialized_bin_arrays: Vec<String>,
    pub transfer_hook_account_count: usize,
    pub instruction_count: usize,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedTokenRebalanceReport {
    pub validation: TokenRebalanceValidation,
    pub build: TokenRebalanceBuildReport,
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


fn validate_contract(
    active_id: i32,
    request: &ChainResolvedTokenRebalanceRequest,
) -> Result<()> {
    if request.should_claim_fee || request.should_claim_reward {
        anyhow::bail!(
            "token-aware live rebalance does not harvest fees/rewards"
        );
    }
    if request.shrink_mode != 0 {
        anyhow::bail!(
            "token-aware live rebalance requires ShrinkBoth mode"
        );
    }
    if request.removes.len() != 1 {
        anyhow::bail!(
            "token-aware live rebalance requires one removal range"
        );
    }
    let remove = &request.removes[0];
    if remove.bps != 10_000
        || remove.min_bin_id.is_none()
        || remove.max_bin_id.is_none()
    {
        anyhow::bail!(
            "token-aware live rebalance requires explicit 10000-bps removal"
        );
    }
    if request.adds.len() != 1 {
        anyhow::bail!(
            "token-aware live rebalance requires one add range"
        );
    }
    let add = &request.adds[0];
    if add.bit_flag > 0b1111 {
        anyhow::bail!("rebalance add bit_flag uses unsupported bits");
    }
    if add.min_delta_id > add.max_delta_id {
        anyhow::bail!("rebalance add min_delta_id exceeds max_delta_id");
    }
    let new_min = active_id
        .checked_add(add.min_delta_id)
        .context("rebalance new minimum bin overflow")?;
    let new_max = active_id
        .checked_add(add.max_delta_id)
        .context("rebalance new maximum bin overflow")?;
    let width = new_max
        .checked_sub(new_min)
        .and_then(|value| value.checked_add(1))
        .context("rebalance new range width overflow")?;
    if width <= 0 || width > 70 {
        anyhow::bail!(
            "token-aware live rebalance range width {width} exceeds 1..=70"
        );
    }
    Ok(())
}


fn touched_indexes(
    active_id: i32,
    removes: &[RebalanceRemovePlan],
    adds: &[RebalanceAddPlan],
) -> Result<Vec<i32>> {
    let mut indexes = BTreeSet::new();
    for remove in removes {
        let min = remove.min_bin_id.context(
            "token-aware rebalance removal requires min_bin_id",
        )?;
        let max = remove.max_bin_id.context(
            "token-aware rebalance removal requires max_bin_id",
        )?;
        if min > max {
            anyhow::bail!("rebalance removal min exceeds max");
        }
        indexes.extend(BinArray::get_bin_array_indexes_coverage(min, max)?);
    }
    for add in adds {
        let min = active_id
            .checked_add(add.min_delta_id)
            .context("rebalance add min overflow")?;
        let max = active_id
            .checked_add(add.max_delta_id)
            .context("rebalance add max overflow")?;
        indexes.extend(BinArray::get_bin_array_indexes_coverage(min, max)?);
    }
    if indexes.is_empty() {
        anyhow::bail!("rebalance touches no bin arrays");
    }
    let indexes = indexes.into_iter().collect::<Vec<_>>();
    if indexes.iter().any(|index| {
        *index < MIN_DEFAULT_BITMAP_INDEX || *index > MAX_DEFAULT_BITMAP_INDEX
    }) {
        anyhow::bail!(
            "token-aware rebalance currently blocks bitmap-extension ranges"
        );
    }
    Ok(indexes)
}


pub async fn build_token_rebalance_from_chain(
    rpc_url: &str,
    request: &ChainResolvedTokenRebalanceRequest,
) -> Result<ChainResolvedTokenRebalanceReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let position_key = parse_pubkey("position", &request.position)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let user_token_x = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y = parse_pubkey("user_token_y", &request.user_token_y)?;
    let memo_program = Pubkey::from_str(MEMO_PROGRAM)
        .context("hard-coded memo program id is invalid")?;
    let client = RpcClient::new(rpc_url.to_string());

    let position_account = client
        .get_account(&position_key)
        .context("failed to fetch rebalance position")?;
    if position_account.owner != dlmm::ID {
        anyhow::bail!("rebalance position is not owned by Meteora DLMM");
    }
    let position: PositionV2 =
        pod_read_unaligned_skip_disc(&position_account.data)
            .context("failed to decode Meteora PositionV2")?;
    if position.owner != sender {
        anyhow::bail!("executor sender does not own rebalance position");
    }

    let lb_pair_key = position.lb_pair;
    let lb_pair_account = client
        .get_account(&lb_pair_key)
        .context("failed to fetch rebalance pool")?;
    if lb_pair_account.owner != dlmm::ID {
        anyhow::bail!("rebalance pool is not owned by Meteora DLMM");
    }
    let lb_pair: LbPair =
        pod_read_unaligned_skip_disc(&lb_pair_account.data)
            .context("failed to decode Meteora LbPair")?;
    let [token_x_program, token_y_program] = lb_pair.get_token_programs()?;
    parse_supported_token_program(&token_x_program)?;
    parse_supported_token_program(&token_y_program)?;

    validate_contract(lb_pair.active_id, request)?;
    let remove = &request.removes[0];
    if remove.min_bin_id != Some(position.lower_bin_id)
        || remove.max_bin_id != Some(position.upper_bin_id)
    {
        anyhow::bail!(
            "rebalance removal range must exactly match chain position range"
        );
    }

    let bin_array_indexes = touched_indexes(
        lb_pair.active_id,
        &request.removes,
        &request.adds,
    )?;
    let bin_array_keys = bin_array_indexes
        .iter()
        .map(|index| derive_bin_array_pda(lb_pair_key, i64::from(*index)).0)
        .collect::<Vec<_>>();

    let mut keys = vec![
        user_token_x,
        user_token_y,
        lb_pair.reserve_x,
        lb_pair.reserve_y,
        lb_pair.token_x_mint,
        lb_pair.token_y_mint,
    ];
    keys.extend(bin_array_keys.iter().copied());
    let accounts = client
        .get_multiple_accounts(&keys)
        .context("failed to fetch token-aware rebalance accounts")?;
    if accounts.len() != keys.len() {
        anyhow::bail!("unexpected token-aware rebalance account fetch result");
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
    if balance_x < request.max_deposit_x_amount {
        anyhow::bail!(
            "user_token_x balance {balance_x} is below max deposit {}",
            request.max_deposit_x_amount
        );
    }
    if balance_y < request.max_deposit_y_amount {
        anyhow::bail!(
            "user_token_y balance {balance_y} is below max deposit {}",
            request.max_deposit_y_amount
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

    let mut missing_bin_array_indexes = vec![];
    for (offset, index) in bin_array_indexes.iter().enumerate() {
        match &accounts[6 + offset] {
            Some(account) => {
                if account.owner != dlmm::ID {
                    anyhow::bail!(
                        "rebalance bin-array account is not owned by Meteora DLMM"
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

    let removes = request
        .removes
        .iter()
        .map(|item| RemoveLiquidityParams {
            min_bin_id: item.min_bin_id,
            max_bin_id: item.max_bin_id,
            bps: item.bps,
            padding: [0_u8; 16],
        })
        .collect::<Vec<_>>();
    let adds = request
        .adds
        .iter()
        .map(|item| AddLiquidityParams {
            min_delta_id: item.min_delta_id,
            max_delta_id: item.max_delta_id,
            x0: item.x0,
            y0: item.y0,
            delta_x: item.delta_x,
            delta_y: item.delta_y,
            bit_flag: item.bit_flag,
            favor_x_in_active_id: item.favor_x_in_active_id,
            padding: [0_u8; 16],
        })
        .collect::<Vec<_>>();

    let (event_authority, _) = derive_event_authority_pda();
    let mut rebalance_accounts =
        dlmm::client::accounts::RebalanceLiquidity {
            position: position_key,
            lb_pair: lb_pair_key,
            bin_array_bitmap_extension: Some(dlmm::ID),
            user_token_x,
            user_token_y,
            reserve_x: lb_pair.reserve_x,
            reserve_y: lb_pair.reserve_y,
            token_x_mint: lb_pair.token_x_mint,
            token_y_mint: lb_pair.token_y_mint,
            owner: sender,
            rent_payer: sender,
            token_x_program,
            token_y_program,
            memo_program,
            system_program: solana_sdk::system_program::ID,
            event_authority,
            program: dlmm::ID,
        }
        .to_account_metas(None);
    rebalance_accounts.extend(remaining.accounts);
    rebalance_accounts.extend(bin_array_indexes.iter().map(|index| {
        AccountMeta {
            pubkey: derive_bin_array_pda(
                lb_pair_key,
                i64::from(*index),
            )
            .0,
            is_signer: false,
            is_writable: true,
        }
    }));

    let params = RebalanceLiquidityParams {
        active_id: lb_pair.active_id,
        max_active_bin_slippage: request.max_active_bin_slippage,
        should_claim_fee: false,
        should_claim_reward: false,
        min_withdraw_x_amount: request.min_withdraw_x_amount,
        max_deposit_x_amount: request.max_deposit_x_amount,
        min_withdraw_y_amount: request.min_withdraw_y_amount,
        max_deposit_y_amount: request.max_deposit_y_amount,
        shrink_mode: request.shrink_mode,
        padding: [0_u8; 31],
        removes,
        adds,
    };
    instructions.push(Instruction {
        program_id: dlmm::ID,
        accounts: rebalance_accounts,
        data: dlmm::client::args::RebalanceLiquidity {
            params,
            remaining_accounts_info: remaining.info,
        }
        .data(),
    });

    let message = Message::new(&instructions, Some(&sender));
    let required_signatures =
        usize::from(message.header.num_required_signatures);
    if required_signatures != 1 {
        anyhow::bail!(
            "token-aware rebalance unexpectedly requires {required_signatures} signatures"
        );
    }
    let transaction = VersionedTransaction {
        signatures: vec![Signature::default(); required_signatures],
        message: VersionedMessage::Legacy(message),
    };
    let transaction_base64 = general_purpose::STANDARD.encode(
        bincode::serialize(&transaction)
            .context("failed to serialize token-aware rebalance")?,
    );

    let token_2022 = Pubkey::from_str(TOKEN_2022_PROGRAM)
        .context("hard-coded Token-2022 program id is invalid")?;
    Ok(ChainResolvedTokenRebalanceReport {
        validation: TokenRebalanceValidation {
            position: position_key.to_string(),
            lb_pair: lb_pair_key.to_string(),
            owner_matches_sender: true,
            token_x_program: token_x_program.to_string(),
            token_y_program: token_y_program.to_string(),
            token_x_is_2022: token_x_program == token_2022,
            token_y_is_2022: token_y_program == token_2022,
            active_id: lb_pair.active_id,
            bin_array_indexes: bin_array_indexes.clone(),
            missing_bin_array_indexes,
            transfer_hook_account_count: hook_count,
        },
        build: TokenRebalanceBuildReport {
            transaction_base64,
            program_id: dlmm::ID.to_string(),
            position: position_key.to_string(),
            lb_pair: lb_pair_key.to_string(),
            sender: sender.to_string(),
            token_x_program: token_x_program.to_string(),
            token_y_program: token_y_program.to_string(),
            bin_array_indexes,
            initialized_bin_arrays,
            transfer_hook_account_count: hook_count,
            instruction_count: instructions.len(),
        },
    })
}


#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> ChainResolvedTokenRebalanceRequest {
        ChainResolvedTokenRebalanceRequest {
            position: Pubkey::new_unique().to_string(),
            sender: Pubkey::new_unique().to_string(),
            user_token_x: Pubkey::new_unique().to_string(),
            user_token_y: Pubkey::new_unique().to_string(),
            max_active_bin_slippage: 3,
            should_claim_fee: false,
            should_claim_reward: false,
            min_withdraw_x_amount: 0,
            max_deposit_x_amount: 100,
            min_withdraw_y_amount: 0,
            max_deposit_y_amount: 100,
            shrink_mode: 0,
            removes: vec![RebalanceRemovePlan {
                min_bin_id: Some(-5),
                max_bin_id: Some(5),
                bps: 10_000,
            }],
            adds: vec![RebalanceAddPlan {
                min_delta_id: -4,
                max_delta_id: 4,
                x0: 1,
                y0: 1,
                delta_x: 0,
                delta_y: 0,
                bit_flag: 0,
                favor_x_in_active_id: false,
            }],
        }
    }

    #[test]
    fn token_rebalance_keeps_narrow_contract() {
        let request = request();
        validate_contract(0, &request).unwrap();

        let mut partial = request.clone();
        partial.removes[0].bps = 9_999;
        assert!(validate_contract(0, &partial).is_err());

        let mut claims = request.clone();
        claims.should_claim_fee = true;
        assert!(validate_contract(0, &claims).is_err());
    }

    #[test]
    fn touched_ranges_stay_in_default_bitmap() {
        let request = request();
        let indexes =
            touched_indexes(0, &request.removes, &request.adds).unwrap();
        assert!(!indexes.is_empty());

        let mut overflow = request.clone();
        overflow.adds[0].min_delta_id = 36_000;
        overflow.adds[0].max_delta_id = 36_000;
        assert!(
            touched_indexes(0, &overflow.removes, &overflow.adds).is_err()
        );
    }
}
