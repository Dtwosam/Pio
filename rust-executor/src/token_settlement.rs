use anchor_lang::{InstructionData, ToAccountMetas};
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use commons::dlmm::accounts::{LbPair, PositionV2};
use commons::{
    derive_event_authority_pda, derive_reward_vault_pda, dlmm,
    pod_read_unaligned_skip_disc, LbPairExtension, PositionExtension,
};
use serde::{Deserialize, Serialize};
use solana_client::rpc_client::RpcClient;
use solana_sdk::instruction::Instruction;
use solana_sdk::message::{Message, VersionedMessage};
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use solana_sdk::transaction::VersionedTransaction;
use std::collections::BTreeMap;
use std::str::FromStr;

use crate::settlement::RewardTokenDestination;
use crate::token_extensions::{
    parse_supported_token_program, resolve_liquidity_remaining_accounts,
    resolve_reward_remaining_accounts, validate_mint_owner,
    validate_token_account_base, validate_token_vault_base,
    TOKEN_2022_PROGRAM,
};

const MEMO_PROGRAM: &str =
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";
const MAX_SETTLEMENT_WIDTH: i32 = 70;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedTokenSettlementRequest {
    pub position: String,
    pub sender: String,
    pub user_token_x: String,
    pub user_token_y: String,
    #[serde(default)]
    pub reward_token_destinations: Vec<RewardTokenDestination>,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenRewardSettlementValidation {
    pub reward_index: u64,
    pub mint: String,
    pub token_program: String,
    pub is_token_2022: bool,
    pub transfer_hook_account_count: usize,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenSettlementValidation {
    pub position: String,
    pub lb_pair: String,
    pub owner_matches_sender: bool,
    pub position_has_zero_liquidity: bool,
    pub token_x_program: String,
    pub token_y_program: String,
    pub token_x_is_2022: bool,
    pub token_y_is_2022: bool,
    pub position_width: i32,
    pub fee_transfer_hook_account_count: usize,
    pub rewards: Vec<TokenRewardSettlementValidation>,
    pub bin_array_accounts: Vec<String>,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenSettlementBuildReport {
    pub transaction_base64: String,
    pub program_id: String,
    pub position: String,
    pub lb_pair: String,
    pub sender: String,
    pub claims_fees: bool,
    pub reward_indices_claimed: Vec<u64>,
    pub closes_position_account: bool,
    pub fee_transfer_hook_account_count: usize,
    pub reward_transfer_hook_account_count: usize,
    pub instruction_count: usize,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedTokenSettlementReport {
    pub validation: TokenSettlementValidation,
    pub build: TokenSettlementBuildReport,
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


pub async fn build_token_settlement_from_chain(
    rpc_url: &str,
    request: &ChainResolvedTokenSettlementRequest,
) -> Result<ChainResolvedTokenSettlementReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let position_key = parse_pubkey("position", &request.position)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let user_token_x = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y = parse_pubkey("user_token_y", &request.user_token_y)?;
    let memo_program = Pubkey::from_str(MEMO_PROGRAM)
        .context("hard-coded memo program id is invalid")?;
    let token_2022 = Pubkey::from_str(TOKEN_2022_PROGRAM)
        .context("hard-coded Token-2022 program id is invalid")?;
    let client = RpcClient::new(rpc_url.to_string());

    let position_account = client
        .get_account(&position_key)
        .context("failed to fetch settlement position")?;
    if position_account.owner != dlmm::ID {
        anyhow::bail!("settlement position is not owned by Meteora DLMM");
    }
    let position: PositionV2 =
        pod_read_unaligned_skip_disc(&position_account.data)
            .context("failed to decode Meteora PositionV2")?;
    if position.owner != sender {
        anyhow::bail!("executor sender does not own settlement position");
    }
    if position.fee_owner != Pubkey::default()
        && position.fee_owner != sender
    {
        anyhow::bail!(
            "settlement does not support fee_owner different from sender"
        );
    }
    if position.liquidity_shares.iter().any(|share| *share != 0) {
        anyhow::bail!(
            "settlement requires position liquidity fully removed first"
        );
    }

    let width = position
        .upper_bin_id
        .checked_sub(position.lower_bin_id)
        .and_then(|value| value.checked_add(1))
        .context("settlement position width overflow")?;
    if width <= 0 || width > MAX_SETTLEMENT_WIDTH {
        anyhow::bail!(
            "token-aware settlement supports width 1..={MAX_SETTLEMENT_WIDTH}"
        );
    }

    let lb_pair_key = position.lb_pair;
    let lb_pair_account = client
        .get_account(&lb_pair_key)
        .context("failed to fetch settlement pool")?;
    if lb_pair_account.owner != dlmm::ID {
        anyhow::bail!("settlement pool is not owned by Meteora DLMM");
    }
    let lb_pair: LbPair =
        pod_read_unaligned_skip_disc(&lb_pair_account.data)
            .context("failed to decode Meteora LbPair")?;
    let [token_x_program, token_y_program] = lb_pair.get_token_programs()?;
    parse_supported_token_program(&token_x_program)?;
    parse_supported_token_program(&token_y_program)?;

    let bin_array_metas =
        position.get_bin_array_accounts_meta_coverage()?;
    let bin_array_keys = bin_array_metas
        .iter()
        .map(|meta| meta.pubkey)
        .collect::<Vec<_>>();

    let mut destinations = BTreeMap::new();
    for destination in &request.reward_token_destinations {
        if destination.reward_index > 1 {
            anyhow::bail!("reward_index must be 0 or 1");
        }
        if destinations
            .insert(
                destination.reward_index,
                destination.user_token_account.clone(),
            )
            .is_some()
        {
            anyhow::bail!("duplicate reward destination index");
        }
    }

    let mut reward_entries = vec![];
    for reward_index in 0_u64..2 {
        let info = lb_pair.reward_infos[reward_index as usize];
        if info.mint == Pubkey::default() {
            if destinations.contains_key(&reward_index) {
                anyhow::bail!(
                    "reward destination supplied for inactive reward index {reward_index}"
                );
            }
            continue;
        }
        let destination = destinations
            .get(&reward_index)
            .with_context(|| {
                format!(
                    "active reward index {reward_index} requires destination"
                )
            })?;
        let user_reward = parse_pubkey(
            &format!("reward_{reward_index}_user_token_account"),
            destination,
        )?;
        let (derived_vault, _) =
            derive_reward_vault_pda(lb_pair_key, reward_index);
        if info.vault != derived_vault {
            anyhow::bail!(
                "reward index {reward_index} vault does not match Meteora PDA"
            );
        }
        reward_entries.push((
            reward_index,
            info.mint,
            derived_vault,
            user_reward,
        ));
    }

    let mut keys = vec![
        user_token_x,
        user_token_y,
        lb_pair.reserve_x,
        lb_pair.reserve_y,
        lb_pair.token_x_mint,
        lb_pair.token_y_mint,
    ];
    keys.extend(bin_array_keys.iter().copied());
    for (_, mint, vault, user_reward) in &reward_entries {
        keys.push(*mint);
        keys.push(*vault);
        keys.push(*user_reward);
    }
    let accounts = client
        .get_multiple_accounts(&keys)
        .context("failed to fetch token-aware settlement accounts")?;
    if accounts.len() != keys.len() {
        anyhow::bail!(
            "unexpected token-aware settlement account fetch result"
        );
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

    let mut cursor = 6;
    for meta in &bin_array_metas {
        let account =
            require_account("position bin array", &accounts[cursor])?;
        if account.owner != dlmm::ID {
            anyhow::bail!(
                "position bin array {} is not owned by Meteora DLMM",
                meta.pubkey
            );
        }
        cursor += 1;
    }

    let mut validated_rewards = vec![];
    let mut reward_programs = vec![];
    for (reward_index, mint, _, _) in &reward_entries {
        let mint_account =
            require_account("reward mint", &accounts[cursor])?;
        let reward_program = mint_account.owner;
        parse_supported_token_program(&reward_program)?;
        validate_mint_owner(
            &format!("reward_{reward_index}_mint"),
            mint_account,
            &reward_program,
        )?;
        cursor += 1;
        validate_token_vault_base(
            &format!("reward_{reward_index}_vault"),
            require_account("reward vault", &accounts[cursor])?,
            mint,
            &reward_program,
        )?;
        cursor += 1;
        validate_token_account_base(
            &format!("reward_{reward_index}_user_token"),
            require_account("reward user token", &accounts[cursor])?,
            mint,
            &sender,
            &reward_program,
        )?;
        cursor += 1;
        reward_programs.push(reward_program);
        validated_rewards.push((*reward_index, *mint));
    }

    let fee_remaining =
        resolve_liquidity_remaining_accounts(rpc_url, &lb_pair).await?;
    let fee_hook_count = fee_remaining.accounts.len();
    let (event_authority, _) = derive_event_authority_pda();
    let mut instructions = vec![];

    let mut fee_accounts = dlmm::client::accounts::ClaimFee2 {
        lb_pair: lb_pair_key,
        position: position_key,
        sender,
        reserve_x: lb_pair.reserve_x,
        reserve_y: lb_pair.reserve_y,
        user_token_x,
        user_token_y,
        token_x_mint: lb_pair.token_x_mint,
        token_y_mint: lb_pair.token_y_mint,
        token_program_x: token_x_program,
        token_program_y: token_y_program,
        memo_program,
        event_authority,
        program: dlmm::ID,
    }
    .to_account_metas(None);
    fee_accounts.extend(fee_remaining.accounts);
    fee_accounts.extend(bin_array_metas.clone());
    instructions.push(Instruction {
        program_id: dlmm::ID,
        accounts: fee_accounts,
        data: dlmm::client::args::ClaimFee2 {
            min_bin_id: position.lower_bin_id,
            max_bin_id: position.upper_bin_id,
            remaining_accounts_info: fee_remaining.info,
        }
        .data(),
    });

    let mut reward_indices_claimed = vec![];
    let mut reward_hook_total = 0_usize;
    let mut reward_validations = vec![];
    for (entry_index, (reward_index, reward_mint, reward_vault, user_reward))
        in reward_entries.into_iter().enumerate()
    {
        let reward_program = reward_programs[entry_index];
        let reward_remaining =
            resolve_reward_remaining_accounts(
                rpc_url,
                &lb_pair,
                reward_index as usize,
            )
            .await?;
        reward_hook_total += reward_remaining.accounts.len();

        let mut reward_accounts =
            dlmm::client::accounts::ClaimReward2 {
                lb_pair: lb_pair_key,
                position: position_key,
                sender,
                reward_vault,
                reward_mint,
                user_token_account: user_reward,
                token_program: reward_program,
                memo_program,
                event_authority,
                program: dlmm::ID,
            }
            .to_account_metas(None);
        reward_accounts.extend(reward_remaining.accounts);
        reward_accounts.extend(bin_array_metas.clone());
        instructions.push(Instruction {
            program_id: dlmm::ID,
            accounts: reward_accounts,
            data: dlmm::client::args::ClaimReward2 {
                reward_index,
                min_bin_id: position.lower_bin_id,
                max_bin_id: position.upper_bin_id,
                remaining_accounts_info: reward_remaining.info,
            }
            .data(),
        });
        reward_indices_claimed.push(reward_index);
        reward_validations.push(TokenRewardSettlementValidation {
            reward_index,
            mint: reward_mint.to_string(),
            token_program: reward_program.to_string(),
            is_token_2022: reward_program == token_2022,
            transfer_hook_account_count: reward_hook_total
                - reward_validations
                    .iter()
                    .map(|item| item.transfer_hook_account_count)
                    .sum::<usize>(),
        });
    }

    let mut close_accounts = dlmm::client::accounts::ClosePosition2 {
        position: position_key,
        sender,
        rent_receiver: sender,
        event_authority,
        program: dlmm::ID,
    }
    .to_account_metas(None);
    close_accounts.extend(bin_array_metas.clone());
    instructions.push(Instruction {
        program_id: dlmm::ID,
        accounts: close_accounts,
        data: dlmm::client::args::ClosePosition2 {}.data(),
    });

    let message = Message::new(&instructions, Some(&sender));
    let required_signatures =
        usize::from(message.header.num_required_signatures);
    if required_signatures != 1 {
        anyhow::bail!(
            "token-aware settlement unexpectedly requires {required_signatures} signatures"
        );
    }
    let transaction = VersionedTransaction {
        signatures: vec![Signature::default(); required_signatures],
        message: VersionedMessage::Legacy(message),
    };
    let transaction_base64 = general_purpose::STANDARD.encode(
        bincode::serialize(&transaction)
            .context("failed to serialize token-aware settlement")?,
    );

    Ok(ChainResolvedTokenSettlementReport {
        validation: TokenSettlementValidation {
            position: position_key.to_string(),
            lb_pair: lb_pair_key.to_string(),
            owner_matches_sender: true,
            position_has_zero_liquidity: true,
            token_x_program: token_x_program.to_string(),
            token_y_program: token_y_program.to_string(),
            token_x_is_2022: token_x_program == token_2022,
            token_y_is_2022: token_y_program == token_2022,
            position_width: width,
            fee_transfer_hook_account_count: fee_hook_count,
            rewards: reward_validations,
            bin_array_accounts: bin_array_keys
                .iter()
                .map(ToString::to_string)
                .collect(),
        },
        build: TokenSettlementBuildReport {
            transaction_base64,
            program_id: dlmm::ID.to_string(),
            position: position_key.to_string(),
            lb_pair: lb_pair_key.to_string(),
            sender: sender.to_string(),
            claims_fees: true,
            reward_indices_claimed,
            closes_position_account: true,
            fee_transfer_hook_account_count: fee_hook_count,
            reward_transfer_hook_account_count: reward_hook_total,
            instruction_count: instructions.len(),
        },
    })
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicate_reward_destination_is_rejected_by_map_contract() {
        let request = ChainResolvedTokenSettlementRequest {
            position: Pubkey::new_unique().to_string(),
            sender: Pubkey::new_unique().to_string(),
            user_token_x: Pubkey::new_unique().to_string(),
            user_token_y: Pubkey::new_unique().to_string(),
            reward_token_destinations: vec![
                RewardTokenDestination {
                    reward_index: 0,
                    user_token_account: Pubkey::new_unique().to_string(),
                },
                RewardTokenDestination {
                    reward_index: 0,
                    user_token_account: Pubkey::new_unique().to_string(),
                },
            ],
        };
        let mut destinations = BTreeMap::new();
        let mut duplicate = false;
        for item in &request.reward_token_destinations {
            if destinations
                .insert(item.reward_index, item.user_token_account.clone())
                .is_some()
            {
                duplicate = true;
            }
        }
        assert!(duplicate);
    }
}
