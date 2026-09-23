use anchor_lang::{InstructionData, ToAccountMetas};
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use commons::dlmm::accounts::{LbPair, PositionV2};
use commons::dlmm::types::BinLiquidityReduction;
use commons::{
    derive_bin_array_bitmap_extension, derive_event_authority_pda, dlmm,
    pod_read_unaligned_skip_disc, LbPairExtension, PositionExtension,
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

const MEMO_PROGRAM: &str =
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedTokenExitRequest {
    pub position: String,
    pub user_token_x: String,
    pub user_token_y: String,
    pub sender: String,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenExitValidation {
    pub position: String,
    pub lb_pair: String,
    pub owner_matches_sender: bool,
    pub token_x_program: String,
    pub token_y_program: String,
    pub token_x_is_2022: bool,
    pub token_y_is_2022: bool,
    pub user_token_x_valid: bool,
    pub user_token_y_valid: bool,
    pub removal_bin_count: usize,
    pub transfer_hook_account_count: usize,
    pub bin_array_accounts: Vec<String>,
    pub bitmap_extension: Option<String>,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenExitBuildReport {
    pub transaction_base64: String,
    pub program_id: String,
    pub position: String,
    pub lb_pair: String,
    pub sender: String,
    pub removes_all_position_liquidity: bool,
    pub removal_bin_count: usize,
    pub transfer_hook_account_count: usize,
    pub claims_fees_or_rewards: bool,
    pub closes_position_account: bool,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedTokenExitReport {
    pub validation: TokenExitValidation,
    pub build: TokenExitBuildReport,
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


fn full_removal(position: &PositionV2) -> Result<Vec<BinLiquidityReduction>> {
    let mut removals = vec![];
    for (index, share) in position.liquidity_shares.iter().enumerate() {
        if *share == 0 {
            continue;
        }
        let offset = i32::try_from(index)
            .context("position liquidity index exceeds i32")?;
        let bin_id = position
            .lower_bin_id
            .checked_add(offset)
            .context("position bin id overflow")?;
        if bin_id > position.upper_bin_id {
            anyhow::bail!(
                "non-zero position liquidity exists outside declared range"
            );
        }
        removals.push(BinLiquidityReduction {
            bin_id,
            bps_to_remove: 10_000,
        });
    }
    if removals.is_empty() {
        anyhow::bail!(
            "position has no liquidity to remove; use settlement/close path"
        );
    }
    Ok(removals)
}


pub async fn build_token_exit_from_chain(
    rpc_url: &str,
    request: &ChainResolvedTokenExitRequest,
) -> Result<ChainResolvedTokenExitReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let position_key = parse_pubkey("position", &request.position)?;
    let user_token_x = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y = parse_pubkey("user_token_y", &request.user_token_y)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let memo_program = Pubkey::from_str(MEMO_PROGRAM)
        .context("hard-coded memo program id is invalid")?;
    let client = RpcClient::new(rpc_url.to_string());

    let position_account = client
        .get_account(&position_key)
        .context("failed to fetch exit position")?;
    if position_account.owner != dlmm::ID {
        anyhow::bail!("exit position is not owned by Meteora DLMM");
    }
    let position: PositionV2 =
        pod_read_unaligned_skip_disc(&position_account.data)
            .context("failed to decode Meteora PositionV2")?;
    if position.owner != sender {
        anyhow::bail!("executor sender does not own exit position");
    }
    let removals = full_removal(&position)?;
    let min_bin_id = removals
        .first()
        .context("removal list unexpectedly empty")?
        .bin_id;
    let max_bin_id = removals
        .last()
        .context("removal list unexpectedly empty")?
        .bin_id;

    let lb_pair_key = position.lb_pair;
    let lb_pair_account = client
        .get_account(&lb_pair_key)
        .context("failed to fetch exit pool")?;
    if lb_pair_account.owner != dlmm::ID {
        anyhow::bail!("exit pool is not owned by Meteora DLMM");
    }
    let lb_pair: LbPair =
        pod_read_unaligned_skip_disc(&lb_pair_account.data)
            .context("failed to decode Meteora LbPair")?;
    let [token_x_program, token_y_program] = lb_pair.get_token_programs()?;
    parse_supported_token_program(&token_x_program)?;
    parse_supported_token_program(&token_y_program)?;

    let bin_array_metas =
        position.get_bin_array_accounts_meta_coverage_by_chunk(
            min_bin_id,
            max_bin_id,
        )?;
    let bin_array_keys = bin_array_metas
        .iter()
        .map(|meta| meta.pubkey)
        .collect::<Vec<_>>();
    let (bitmap_key, _) =
        derive_bin_array_bitmap_extension(lb_pair_key);

    let mut keys = vec![
        user_token_x,
        user_token_y,
        lb_pair.reserve_x,
        lb_pair.reserve_y,
        lb_pair.token_x_mint,
        lb_pair.token_y_mint,
        bitmap_key,
    ];
    keys.extend(bin_array_keys.iter().copied());
    let accounts = client
        .get_multiple_accounts(&keys)
        .context("failed to fetch token-aware exit dependent accounts")?;
    if accounts.len() != keys.len() {
        anyhow::bail!("unexpected token-aware exit account fetch result");
    }

    validate_token_account_base(
        "user_token_x",
        require_account("user_token_x", &accounts[0])?,
        &lb_pair.token_x_mint,
        &sender,
        &token_x_program,
    )?;
    validate_token_account_base(
        "user_token_y",
        require_account("user_token_y", &accounts[1])?,
        &lb_pair.token_y_mint,
        &sender,
        &token_y_program,
    )?;
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

    let bitmap_extension = match &accounts[6] {
        Some(account) => {
            if account.owner != dlmm::ID {
                anyhow::bail!(
                    "exit bitmap extension is not owned by Meteora DLMM"
                );
            }
            Some(bitmap_key)
        }
        None => None,
    };
    for (offset, meta) in bin_array_metas.iter().enumerate() {
        let account = require_account(
            "exit bin array",
            &accounts[7 + offset],
        )?;
        if account.owner != dlmm::ID {
            anyhow::bail!(
                "exit bin array {} is not owned by Meteora DLMM",
                meta.pubkey
            );
        }
    }

    let remaining =
        resolve_liquidity_remaining_accounts(rpc_url, &lb_pair).await?;
    let hook_count = remaining.accounts.len();
    let (event_authority, _) = derive_event_authority_pda();

    let mut ix_accounts = dlmm::client::accounts::RemoveLiquidity2 {
        position: position_key,
        lb_pair: lb_pair_key,
        bin_array_bitmap_extension: bitmap_extension.or(Some(dlmm::ID)),
        user_token_x,
        user_token_y,
        reserve_x: lb_pair.reserve_x,
        reserve_y: lb_pair.reserve_y,
        token_x_mint: lb_pair.token_x_mint,
        token_y_mint: lb_pair.token_y_mint,
        sender,
        token_x_program,
        token_y_program,
        memo_program,
        event_authority,
        program: dlmm::ID,
    }
    .to_account_metas(None);
    ix_accounts.extend(remaining.accounts);
    ix_accounts.extend(bin_array_metas.clone());

    let instruction = Instruction {
        program_id: dlmm::ID,
        accounts: ix_accounts,
        data: dlmm::client::args::RemoveLiquidity2 {
            bin_liquidity_removal: removals.clone(),
            remaining_accounts_info: remaining.info,
        }
        .data(),
    };
    let message = Message::new(&[instruction], Some(&sender));
    let required_signatures =
        usize::from(message.header.num_required_signatures);
    if required_signatures != 1 {
        anyhow::bail!(
            "token-aware exit unexpectedly requires {required_signatures} signatures"
        );
    }
    let transaction = VersionedTransaction {
        signatures: vec![Signature::default(); required_signatures],
        message: VersionedMessage::Legacy(message),
    };
    let transaction_base64 = general_purpose::STANDARD.encode(
        bincode::serialize(&transaction)
            .context("failed to serialize token-aware exit")?,
    );

    let token_2022 = Pubkey::from_str(TOKEN_2022_PROGRAM)
        .context("hard-coded Token-2022 program id is invalid")?;
    Ok(ChainResolvedTokenExitReport {
        validation: TokenExitValidation {
            position: position_key.to_string(),
            lb_pair: lb_pair_key.to_string(),
            owner_matches_sender: true,
            token_x_program: token_x_program.to_string(),
            token_y_program: token_y_program.to_string(),
            token_x_is_2022: token_x_program == token_2022,
            token_y_is_2022: token_y_program == token_2022,
            user_token_x_valid: true,
            user_token_y_valid: true,
            removal_bin_count: removals.len(),
            transfer_hook_account_count: hook_count,
            bin_array_accounts: bin_array_keys
                .iter()
                .map(ToString::to_string)
                .collect(),
            bitmap_extension: bitmap_extension.map(|key| key.to_string()),
        },
        build: TokenExitBuildReport {
            transaction_base64,
            program_id: dlmm::ID.to_string(),
            position: position_key.to_string(),
            lb_pair: lb_pair_key.to_string(),
            sender: sender.to_string(),
            removes_all_position_liquidity: true,
            removal_bin_count: removals.len(),
            transfer_hook_account_count: hook_count,
            claims_fees_or_rewards: false,
            closes_position_account: false,
        },
    })
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_removal_only_includes_nonzero_liquidity_bins() {
        let mut position: PositionV2 = unsafe { std::mem::zeroed() };
        position.lower_bin_id = -2;
        position.upper_bin_id = 2;
        position.liquidity_shares[0] = 10;
        position.liquidity_shares[2] = 20;
        position.liquidity_shares[4] = 30;

        let removals = full_removal(&position).unwrap();

        assert_eq!(removals.len(), 3);
        assert_eq!(removals[0].bin_id, -2);
        assert_eq!(removals[1].bin_id, 0);
        assert_eq!(removals[2].bin_id, 2);
        assert!(removals.iter().all(|item| item.bps_to_remove == 10_000));
    }

    #[test]
    fn zero_liquidity_position_routes_to_settlement() {
        let mut position: PositionV2 = unsafe { std::mem::zeroed() };
        position.lower_bin_id = 0;
        position.upper_bin_id = 1;

        let error = full_removal(&position).unwrap_err().to_string();
        assert!(error.contains("settlement"));
    }
}
