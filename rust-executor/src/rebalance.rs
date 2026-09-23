use anchor_lang::{InstructionData, ToAccountMetas};
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use commons::dlmm::accounts::{BinArray, LbPair, PositionV2};
use commons::dlmm::types::{
    AddLiquidityParams, RebalanceLiquidityParams, RemainingAccountsInfo,
    RemoveLiquidityParams,
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

const SPL_TOKEN_PROGRAM: &str =
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const MEMO_PROGRAM: &str =
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";
const MIN_DEFAULT_BITMAP_INDEX: i32 = -512;
const MAX_DEFAULT_BITMAP_INDEX: i32 = 511;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RebalanceRemovePlan {
    pub min_bin_id: Option<i32>,
    pub max_bin_id: Option<i32>,
    pub bps: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RebalanceAddPlan {
    pub min_delta_id: i32,
    pub max_delta_id: i32,
    pub x0: u64,
    pub y0: u64,
    pub delta_x: u64,
    pub delta_y: u64,
    pub bit_flag: u8,
    pub favor_x_in_active_id: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StandardSplRebalanceRequest {
    pub position: String,
    pub lb_pair: String,
    pub sender: String,
    pub user_token_x: String,
    pub user_token_y: String,
    pub reserve_x: String,
    pub reserve_y: String,
    pub token_x_mint: String,
    pub token_y_mint: String,
    pub active_id: i32,
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
    pub initialize_bin_array_indexes: Vec<i32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedStandardSplRebalanceRequest {
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
pub struct StandardSplRebalanceChainValidation {
    pub position: String,
    pub lb_pair: String,
    pub owner_matches_sender: bool,
    pub token_programs_standard_spl: bool,
    pub user_token_x_valid: bool,
    pub user_token_y_valid: bool,
    pub active_id: i32,
    pub bin_array_indexes: Vec<i32>,
    pub missing_bin_array_indexes: Vec<i32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedStandardSplRebalanceReport {
    pub validation: StandardSplRebalanceChainValidation,
    pub request: StandardSplRebalanceRequest,
    pub build: StandardSplRebalanceBuildReport,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StandardSplRebalanceBuildReport {
    pub transaction_base64: String,
    pub program_id: String,
    pub position: String,
    pub lb_pair: String,
    pub sender: String,
    pub bin_array_indexes: Vec<i32>,
    pub initialized_bin_arrays: Vec<String>,
    pub instruction_count: usize,
    pub claims_fee: bool,
    pub claims_reward: bool,
}

fn parse_pubkey(name: &str, value: &str) -> Result<Pubkey> {
    Pubkey::from_str(value.trim())
        .with_context(|| format!("{name} is not a valid Solana pubkey"))
}

fn require_account<'a>(
    name: &str,
    account: &'a Option<solana_sdk::account::Account>,
) -> Result<&'a solana_sdk::account::Account> {
    account.as_ref().with_context(|| format!("{name} account is missing"))
}

fn standard_token_balance(
    name: &str,
    account: &solana_sdk::account::Account,
    expected_mint: &Pubkey,
    expected_owner: &Pubkey,
    token_program: &Pubkey,
) -> Result<u64> {
    if account.owner != *token_program {
        anyhow::bail!("{name} is not owned by the standard SPL Token program");
    }
    if account.data.len() < 64 {
        anyhow::bail!("{name} token account data is too short");
    }
    let mint = Pubkey::new_from_array(
        account.data[0..32]
            .try_into()
            .context("invalid token-account mint bytes")?,
    );
    let owner = Pubkey::new_from_array(
        account.data[32..64]
            .try_into()
            .context("invalid token-account owner bytes")?,
    );
    if mint != *expected_mint {
        anyhow::bail!("{name} mint does not match the DLMM pool mint");
    }
    if owner != *expected_owner {
        anyhow::bail!("{name} token-account owner does not match sender");
    }
    if account.data.len() < 72 {
        anyhow::bail!("{name} token account data is too short for amount");
    }
    Ok(u64::from_le_bytes(
        account.data[64..72]
            .try_into()
            .context("invalid token-account amount bytes")?,
    ))
}

fn validate_narrow_rebalance_contract(
    active_id: i32,
    should_claim_fee: bool,
    should_claim_reward: bool,
    shrink_mode: u8,
    removes: &[RebalanceRemovePlan],
    adds: &[RebalanceAddPlan],
) -> Result<(i32, i32)> {
    if should_claim_fee || should_claim_reward {
        anyhow::bail!(
            "live rebalance does not support fee or reward harvesting yet"
        );
    }
    if shrink_mode != 0 {
        anyhow::bail!(
            "live rebalance currently requires ShrinkBoth mode"
        );
    }
    if removes.len() != 1 {
        anyhow::bail!(
            "live rebalance requires exactly one full old-range removal"
        );
    }
    let remove = &removes[0];
    if remove.bps != 10_000
        || remove.min_bin_id.is_none()
        || remove.max_bin_id.is_none()
    {
        anyhow::bail!(
            "live rebalance requires one explicit 10000-bps old-range removal"
        );
    }
    if adds.len() != 1 {
        anyhow::bail!(
            "live rebalance requires exactly one new-range deposit"
        );
    }
    let add = &adds[0];
    if add.bit_flag > 0b1111 {
        anyhow::bail!("rebalance add bit_flag uses unsupported bits");
    }
    let new_min = active_id
        .checked_add(add.min_delta_id)
        .context("rebalance new minimum bin overflow")?;
    let new_max = active_id
        .checked_add(add.max_delta_id)
        .context("rebalance new maximum bin overflow")?;
    if new_min > new_max {
        anyhow::bail!("rebalance new minimum bin exceeds maximum bin");
    }
    let width = new_max
        .checked_sub(new_min)
        .and_then(|value| value.checked_add(1))
        .context("rebalance new range width overflow")?;
    if width <= 0 || width > 70 {
        anyhow::bail!(
            "live rebalance new range width {width} exceeds supported range 1..=70"
        );
    }
    Ok((new_min, new_max))
}

fn touched_bin_array_indexes(
    active_id: i32,
    removes: &[RebalanceRemovePlan],
    adds: &[RebalanceAddPlan],
) -> Result<Vec<i32>> {
    let mut indexes = BTreeSet::new();

    for remove in removes {
        if remove.bps == 0 || remove.bps > 10_000 {
            anyhow::bail!("rebalance remove bps must be in 1..=10000");
        }
        let min = remove.min_bin_id.unwrap_or(active_id);
        let max = remove.max_bin_id.unwrap_or(active_id);
        if min > max {
            anyhow::bail!("rebalance remove min_bin_id cannot exceed max_bin_id");
        }
        indexes.extend(BinArray::get_bin_array_indexes_coverage(min, max)?);
    }

    for add in adds {
        if add.min_delta_id > add.max_delta_id {
            anyhow::bail!(
                "rebalance add min_delta_id cannot exceed max_delta_id"
            );
        }
        let min = active_id
            .checked_add(add.min_delta_id)
            .context("rebalance add minimum bin overflow")?;
        let max = active_id
            .checked_add(add.max_delta_id)
            .context("rebalance add maximum bin overflow")?;
        indexes.extend(BinArray::get_bin_array_indexes_coverage(min, max)?);
    }

    if indexes.is_empty() {
        anyhow::bail!("rebalance requires at least one remove or add range");
    }
    Ok(indexes.into_iter().collect())
}

pub fn build_standard_spl_rebalance_from_chain(
    rpc_url: &str,
    request: &ChainResolvedStandardSplRebalanceRequest,
) -> Result<ChainResolvedStandardSplRebalanceReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let position_key = parse_pubkey("position", &request.position)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let user_token_x = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y = parse_pubkey("user_token_y", &request.user_token_y)?;
    let token_program = Pubkey::from_str(SPL_TOKEN_PROGRAM)
        .context("hard-coded SPL token program id is invalid")?;
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
        anyhow::bail!("executor sender does not own the rebalance position");
    }

    let lb_pair_key = position.lb_pair;
    let lb_pair_account = client
        .get_account(&lb_pair_key)
        .context("failed to fetch rebalance DLMM pool")?;
    if lb_pair_account.owner != dlmm::ID {
        anyhow::bail!("rebalance pool is not owned by Meteora DLMM");
    }
    let lb_pair: LbPair =
        pod_read_unaligned_skip_disc(&lb_pair_account.data)
            .context("failed to decode Meteora LbPair")?;
    let [token_x_program, token_y_program] = lb_pair.get_token_programs()?;
    if token_x_program != token_program || token_y_program != token_program {
        anyhow::bail!(
            "chain-resolved live rebalance currently supports standard SPL pools only"
        );
    }

    let (_new_min, _new_max) = validate_narrow_rebalance_contract(
        lb_pair.active_id,
        request.should_claim_fee,
        request.should_claim_reward,
        request.shrink_mode,
        &request.removes,
        &request.adds,
    )?;
    let remove = &request.removes[0];
    if remove.min_bin_id != Some(position.lower_bin_id)
        || remove.max_bin_id != Some(position.upper_bin_id)
    {
        anyhow::bail!(
            "rebalance removal range must exactly match the chain position range"
        );
    }

    let bin_array_indexes = touched_bin_array_indexes(
        lb_pair.active_id,
        &request.removes,
        &request.adds,
    )?;
    if bin_array_indexes.iter().any(|index| {
        *index < MIN_DEFAULT_BITMAP_INDEX || *index > MAX_DEFAULT_BITMAP_INDEX
    }) {
        anyhow::bail!(
            "chain-resolved live rebalance currently blocks bitmap-extension ranges"
        );
    }
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
        .context("failed to fetch rebalance dependent accounts")?;
    if accounts.len() != keys.len() {
        anyhow::bail!("unexpected rebalance account fetch result");
    }

    let balance_x = standard_token_balance(
        "user_token_x",
        require_account("user_token_x", &accounts[0])?,
        &lb_pair.token_x_mint,
        &sender,
        &token_program,
    )?;
    let balance_y = standard_token_balance(
        "user_token_y",
        require_account("user_token_y", &accounts[1])?,
        &lb_pair.token_y_mint,
        &sender,
        &token_program,
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
    if require_account("reserve_x", &accounts[2])?.owner != token_program
        || require_account("reserve_y", &accounts[3])?.owner != token_program
    {
        anyhow::bail!("DLMM reserve accounts are not standard SPL Token accounts");
    }
    if require_account("token_x_mint", &accounts[4])?.owner != token_program
        || require_account("token_y_mint", &accounts[5])?.owner != token_program
    {
        anyhow::bail!("DLMM mint accounts are not standard SPL Token mints");
    }

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

    let resolved = StandardSplRebalanceRequest {
        position: position_key.to_string(),
        lb_pair: lb_pair_key.to_string(),
        sender: sender.to_string(),
        user_token_x: user_token_x.to_string(),
        user_token_y: user_token_y.to_string(),
        reserve_x: lb_pair.reserve_x.to_string(),
        reserve_y: lb_pair.reserve_y.to_string(),
        token_x_mint: lb_pair.token_x_mint.to_string(),
        token_y_mint: lb_pair.token_y_mint.to_string(),
        active_id: lb_pair.active_id,
        max_active_bin_slippage: request.max_active_bin_slippage,
        should_claim_fee: request.should_claim_fee,
        should_claim_reward: request.should_claim_reward,
        min_withdraw_x_amount: request.min_withdraw_x_amount,
        max_deposit_x_amount: request.max_deposit_x_amount,
        min_withdraw_y_amount: request.min_withdraw_y_amount,
        max_deposit_y_amount: request.max_deposit_y_amount,
        shrink_mode: request.shrink_mode,
        removes: request.removes.clone(),
        adds: request.adds.clone(),
        initialize_bin_array_indexes: missing_bin_array_indexes.clone(),
    };
    let build = build_standard_spl_rebalance(&resolved)?;

    Ok(ChainResolvedStandardSplRebalanceReport {
        validation: StandardSplRebalanceChainValidation {
            position: position_key.to_string(),
            lb_pair: lb_pair_key.to_string(),
            owner_matches_sender: true,
            token_programs_standard_spl: true,
            user_token_x_valid: true,
            user_token_y_valid: true,
            active_id: lb_pair.active_id,
            bin_array_indexes,
            missing_bin_array_indexes,
        },
        request: resolved,
        build,
    })
}

pub fn build_standard_spl_rebalance(
    request: &StandardSplRebalanceRequest,
) -> Result<StandardSplRebalanceBuildReport> {
    let position = parse_pubkey("position", &request.position)?;
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
    let memo_program = Pubkey::from_str(MEMO_PROGRAM)
        .context("hard-coded memo program id is invalid")?;

    let (_new_min, _new_max) = validate_narrow_rebalance_contract(
        request.active_id,
        request.should_claim_fee,
        request.should_claim_reward,
        request.shrink_mode,
        &request.removes,
        &request.adds,
    )?;
    let bin_array_indexes = touched_bin_array_indexes(
        request.active_id,
        &request.removes,
        &request.adds,
    )?;
    if bin_array_indexes
        .iter()
        .any(|index| *index < MIN_DEFAULT_BITMAP_INDEX
            || *index > MAX_DEFAULT_BITMAP_INDEX)
    {
        anyhow::bail!(
            "standard live rebalance currently blocks bitmap-extension ranges"
        );
    }

    for index in &request.initialize_bin_array_indexes {
        if !bin_array_indexes.contains(index) {
            anyhow::bail!(
                "requested rebalance bin-array initialization index {index} is outside touched ranges"
            );
        }
    }

    let mut instructions = vec![];
    let mut initialized_bin_arrays = vec![];
    let mut requested_indexes = request.initialize_bin_array_indexes.clone();
    requested_indexes.sort_unstable();
    requested_indexes.dedup();

    for index in requested_indexes {
        let bin_array = derive_bin_array_pda(lb_pair, i64::from(index)).0;
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
    let mut accounts = dlmm::client::accounts::RebalanceLiquidity {
        position,
        lb_pair,
        bin_array_bitmap_extension: Some(dlmm::ID),
        user_token_x,
        user_token_y,
        reserve_x,
        reserve_y,
        token_x_mint,
        token_y_mint,
        owner: sender,
        rent_payer: sender,
        token_x_program: token_program,
        token_y_program: token_program,
        memo_program,
        system_program: solana_sdk::system_program::ID,
        event_authority,
        program: dlmm::ID,
    }
    .to_account_metas(None);

    accounts.extend(bin_array_indexes.iter().map(|index| AccountMeta {
        pubkey: derive_bin_array_pda(lb_pair, i64::from(*index)).0,
        is_signer: false,
        is_writable: true,
    }));

    let params = RebalanceLiquidityParams {
        active_id: request.active_id,
        max_active_bin_slippage: request.max_active_bin_slippage,
        should_claim_fee: request.should_claim_fee,
        should_claim_reward: request.should_claim_reward,
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
        accounts,
        data: dlmm::client::args::RebalanceLiquidity {
            params,
            remaining_accounts_info: RemainingAccountsInfo {
                slices: vec![],
            },
        }
        .data(),
    });

    let message = Message::new(&instructions, Some(&sender));
    let required_signatures =
        usize::from(message.header.num_required_signatures);
    if required_signatures != 1 {
        anyhow::bail!(
            "rebalance transaction unexpectedly requires {required_signatures} signatures"
        );
    }
    let transaction = VersionedTransaction {
        signatures: vec![Signature::default(); required_signatures],
        message: VersionedMessage::Legacy(message),
    };
    let transaction_base64 = general_purpose::STANDARD.encode(
        bincode::serialize(&transaction)
            .context("failed to serialize rebalance transaction")?,
    );

    Ok(StandardSplRebalanceBuildReport {
        transaction_base64,
        program_id: dlmm::ID.to_string(),
        position: position.to_string(),
        lb_pair: lb_pair.to_string(),
        sender: sender.to_string(),
        bin_array_indexes,
        initialized_bin_arrays,
        instruction_count: instructions.len(),
        claims_fee: request.should_claim_fee,
        claims_reward: request.should_claim_reward,
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

    fn request() -> StandardSplRebalanceRequest {
        StandardSplRebalanceRequest {
            position: Pubkey::new_unique().to_string(),
            lb_pair: Pubkey::new_unique().to_string(),
            sender: Pubkey::new_unique().to_string(),
            user_token_x: Pubkey::new_unique().to_string(),
            user_token_y: Pubkey::new_unique().to_string(),
            reserve_x: Pubkey::new_unique().to_string(),
            reserve_y: Pubkey::new_unique().to_string(),
            token_x_mint: Pubkey::new_unique().to_string(),
            token_y_mint: Pubkey::new_unique().to_string(),
            active_id: 0,
            max_active_bin_slippage: 3,
            should_claim_fee: false,
            should_claim_reward: false,
            min_withdraw_x_amount: 0,
            max_deposit_x_amount: u64::MAX,
            min_withdraw_y_amount: 0,
            max_deposit_y_amount: u64::MAX,
            shrink_mode: 0,
            removes: vec![RebalanceRemovePlan {
                min_bin_id: Some(-5),
                max_bin_id: Some(5),
                bps: 10_000,
            }],
            adds: vec![RebalanceAddPlan {
                min_delta_id: -4,
                max_delta_id: 4,
                x0: 100,
                y0: 100,
                delta_x: 0,
                delta_y: 0,
                bit_flag: 0,
                favor_x_in_active_id: false,
            }],
            initialize_bin_array_indexes: vec![],
        }
    }

    #[test]
    fn rebalance_builds_unsigned_action_bound_transaction() {
        let request = request();
        let report = build_standard_spl_rebalance(&request).unwrap();

        assert_eq!(report.program_id, dlmm::ID.to_string());
        assert_eq!(report.instruction_count, 1);

        let proposal = TradeProposal {
            decision_id: Uuid::new_v4(),
            mode: Mode::Live,
            action: Action::Rebalance,
            pool_address: request.lb_pair.clone(),
            capital_quote: 10.0,
            account_equity_quote: 1_000.0,
            portfolio_deployed_quote: 100.0,
            daily_drawdown_pct: 0.0,
            min_bin_id: -4,
            max_bin_id: 4,
            strategy: "SPOT".into(),
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
                max_instructions: 4,
                max_static_accounts: 40,
                allow_address_lookup_tables: false,
                require_unsigned: true,
                require_proposal_pool_account: true,
                required_account_pubkeys: vec![
                    request.position.clone(),
                    request.user_token_x.clone(),
                    request.user_token_y.clone(),
                ],
                require_instruction_policy: true,
                instruction_policies: vec![ProgramInstructionPolicy {
                    program_id: dlmm::ID.to_string(),
                    allowed_actions: vec![Action::Rebalance],
                    allowed_data_prefixes_hex: vec![
                        "5c04b0c177b95309".into(),
                        "235613b94ed44bd3".into(),
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
    fn invalid_remove_bps_fails_closed() {
        let mut request = request();
        request.removes[0].bps = 0;
        assert!(build_standard_spl_rebalance(&request).is_err());
    }

    #[test]
    fn overflow_bitmap_range_fails_closed() {
        let mut request = request();
        request.active_id = 36_000;
        request.removes.clear();
        request.adds = vec![RebalanceAddPlan {
            min_delta_id: 0,
            max_delta_id: 0,
            x0: 1,
            y0: 0,
            delta_x: 0,
            delta_y: 0,
            bit_flag: 0,
            favor_x_in_active_id: true,
        }];
        assert!(build_standard_spl_rebalance(&request).is_err());
    }
    #[test]
    fn claims_are_blocked_in_live_rebalance() {
        let mut request = request();
        request.should_claim_fee = true;
        assert!(build_standard_spl_rebalance(&request).is_err());

        let mut request = request();
        request.should_claim_reward = true;
        assert!(build_standard_spl_rebalance(&request).is_err());
    }

    #[test]
    fn partial_or_ambiguous_remove_plan_is_blocked() {
        let mut request = request();
        request.removes[0].bps = 9_999;
        assert!(build_standard_spl_rebalance(&request).is_err());

        let mut request = request();
        request.removes[0].min_bin_id = None;
        assert!(build_standard_spl_rebalance(&request).is_err());
    }

    #[test]
    fn multiple_add_ranges_are_blocked() {
        let mut request = request();
        request.adds.push(request.adds[0].clone());
        assert!(build_standard_spl_rebalance(&request).is_err());
    }

    #[test]
    fn overly_wide_new_range_is_blocked() {
        let mut request = request();
        request.adds[0].min_delta_id = -35;
        request.adds[0].max_delta_id = 35;
        assert!(build_standard_spl_rebalance(&request).is_err());
    }

    #[test]
    fn unsupported_bit_flag_is_blocked() {
        let mut request = request();
        request.adds[0].bit_flag = 0b1_0000;
        assert!(build_standard_spl_rebalance(&request).is_err());
    }

}
