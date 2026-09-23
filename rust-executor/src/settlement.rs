use anchor_lang::{InstructionData, ToAccountMetas};
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use commons::dlmm::accounts::{LbPair, PositionV2};
use commons::dlmm::types::RemainingAccountsInfo;
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

const SPL_TOKEN_PROGRAM: &str =
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const MEMO_PROGRAM: &str =
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";
const MAX_SETTLEMENT_WIDTH: i32 = 70;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RewardTokenDestination {
    pub reward_index: u64,
    pub user_token_account: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedSettlementRequest {
    pub position: String,
    pub sender: String,
    pub user_token_x: String,
    pub user_token_y: String,
    #[serde(default)]
    pub reward_token_destinations: Vec<RewardTokenDestination>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SettlementValidation {
    pub position: String,
    pub lb_pair: String,
    pub owner_matches_sender: bool,
    pub position_has_zero_liquidity: bool,
    pub token_programs_standard_spl: bool,
    pub position_width: i32,
    pub reward_indices_claimed: Vec<u64>,
    pub bin_array_accounts: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SettlementBuildReport {
    pub transaction_base64: String,
    pub program_id: String,
    pub position: String,
    pub lb_pair: String,
    pub sender: String,
    pub claims_fees: bool,
    pub reward_indices_claimed: Vec<u64>,
    pub closes_position_account: bool,
    pub instruction_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainResolvedSettlementReport {
    pub validation: SettlementValidation,
    pub build: SettlementBuildReport,
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

fn validate_standard_token_account(
    name: &str,
    account: &solana_sdk::account::Account,
    expected_mint: &Pubkey,
    expected_owner: &Pubkey,
    token_program: &Pubkey,
) -> Result<()> {
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
        anyhow::bail!("{name} mint does not match expected mint");
    }
    if owner != *expected_owner {
        anyhow::bail!("{name} token-account owner does not match sender");
    }
    Ok(())
}

fn validate_standard_vault(
    name: &str,
    account: &solana_sdk::account::Account,
    expected_mint: &Pubkey,
    token_program: &Pubkey,
) -> Result<()> {
    if account.owner != *token_program {
        anyhow::bail!("{name} is not owned by the standard SPL Token program");
    }
    if account.data.len() < 32 {
        anyhow::bail!("{name} token account data is too short");
    }
    let mint = Pubkey::new_from_array(
        account.data[0..32]
            .try_into()
            .context("invalid token-account mint bytes")?,
    );
    if mint != *expected_mint {
        anyhow::bail!("{name} mint does not match expected mint");
    }
    Ok(())
}

pub fn build_standard_spl_settlement_from_chain(
    rpc_url: &str,
    request: &ChainResolvedSettlementRequest,
) -> Result<ChainResolvedSettlementReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let position_key = parse_pubkey("position", &request.position)?;
    let sender = parse_pubkey("sender", &request.sender)?;
    let user_token_x = parse_pubkey("user_token_x", &request.user_token_x)?;
    let user_token_y = parse_pubkey("user_token_y", &request.user_token_y)?;
    let token_program = Pubkey::from_str(SPL_TOKEN_PROGRAM)
        .context("hard-coded SPL token program id is invalid")?;
    let memo_program = Pubkey::from_str(MEMO_PROGRAM)
        .context("hard-coded memo program id is invalid")?;
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
            "settlement does not support a fee_owner different from executor sender"
        );
    }
    if position.liquidity_shares.iter().any(|share| *share != 0) {
        anyhow::bail!(
            "settlement requires position liquidity to be fully removed first"
        );
    }

    let width = position
        .upper_bin_id
        .checked_sub(position.lower_bin_id)
        .and_then(|value| value.checked_add(1))
        .context("settlement position width overflow")?;
    if width <= 0 || width > MAX_SETTLEMENT_WIDTH {
        anyhow::bail!(
            "standard settlement currently supports position width 1..={MAX_SETTLEMENT_WIDTH}"
        );
    }

    let lb_pair_key = position.lb_pair;
    let lb_pair_account = client
        .get_account(&lb_pair_key)
        .context("failed to fetch settlement DLMM pool")?;
    if lb_pair_account.owner != dlmm::ID {
        anyhow::bail!("settlement pool is not owned by Meteora DLMM");
    }
    let lb_pair: LbPair =
        pod_read_unaligned_skip_disc(&lb_pair_account.data)
            .context("failed to decode Meteora LbPair")?;
    let [token_x_program, token_y_program] = lb_pair.get_token_programs()?;
    if token_x_program != token_program || token_y_program != token_program {
        anyhow::bail!(
            "standard settlement currently supports standard SPL pool tokens only"
        );
    }

    let bin_array_metas =
        position.get_bin_array_accounts_meta_coverage()?;
    let bin_array_keys = bin_array_metas
        .iter()
        .map(|meta| meta.pubkey)
        .collect::<Vec<_>>();

    let mut reward_destinations = BTreeMap::new();
    for destination in &request.reward_token_destinations {
        if destination.reward_index > 1 {
            anyhow::bail!("reward_index must be 0 or 1");
        }
        if reward_destinations
            .insert(destination.reward_index, destination.user_token_account.clone())
            .is_some()
        {
            anyhow::bail!("duplicate reward destination index");
        }
    }

    let mut reward_entries = vec![];
    for reward_index in 0_u64..2 {
        let info = lb_pair.reward_infos[reward_index as usize];
        if info.mint == Pubkey::default() {
            if reward_destinations.contains_key(&reward_index) {
                anyhow::bail!(
                    "reward destination supplied for inactive reward index {reward_index}"
                );
            }
            continue;
        }
        let destination = reward_destinations
            .get(&reward_index)
            .with_context(|| {
                format!(
                    "active reward index {reward_index} requires a user token destination"
                )
            })?;
        let user_reward = parse_pubkey(
            &format!("reward_{reward_index}_user_token_account"),
            destination,
        )?;
        let (vault, _) = derive_reward_vault_pda(lb_pair_key, reward_index);
        reward_entries.push((reward_index, info.mint, vault, user_reward));
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
        .context("failed to fetch settlement dependent accounts")?;
    if accounts.len() != keys.len() {
        anyhow::bail!("unexpected settlement account fetch result");
    }

    validate_standard_token_account(
        "user_token_x",
        require_account("user_token_x", &accounts[0])?,
        &lb_pair.token_x_mint,
        &sender,
        &token_program,
    )?;
    validate_standard_token_account(
        "user_token_y",
        require_account("user_token_y", &accounts[1])?,
        &lb_pair.token_y_mint,
        &sender,
        &token_program,
    )?;
    if require_account("reserve_x", &accounts[2])?.owner != token_program
        || require_account("reserve_y", &accounts[3])?.owner != token_program
    {
        anyhow::bail!("settlement reserves are not standard SPL Token accounts");
    }
    if require_account("token_x_mint", &accounts[4])?.owner != token_program
        || require_account("token_y_mint", &accounts[5])?.owner != token_program
    {
        anyhow::bail!("settlement pool mints are not standard SPL Token mints");
    }

    let mut cursor = 6;
    for meta in &bin_array_metas {
        let account = require_account("position bin array", &accounts[cursor])?;
        if account.owner != dlmm::ID {
            anyhow::bail!(
                "position bin-array account {} is not owned by Meteora DLMM",
                meta.pubkey
            );
        }
        cursor += 1;
    }
    for (reward_index, mint, _, user_reward) in &reward_entries {
        let mint_account =
            require_account("reward mint", &accounts[cursor])?;
        if mint_account.owner != token_program {
            anyhow::bail!(
                "reward index {reward_index} mint is not standard SPL"
            );
        }
        cursor += 1;
        validate_standard_vault(
            &format!("reward_{reward_index}_vault"),
            require_account("reward vault", &accounts[cursor])?,
            mint,
            &token_program,
        )?;
        cursor += 1;
        validate_standard_token_account(
            &format!("reward_{reward_index}_user_token"),
            require_account("reward user token", &accounts[cursor])?,
            mint,
            &sender,
            &token_program,
        )?;
        if keys[cursor] != *user_reward {
            anyhow::bail!("reward destination account ordering mismatch");
        }
        cursor += 1;
    }

    let (event_authority, _) = derive_event_authority_pda();
    let remaining_accounts_info = RemainingAccountsInfo { slices: vec![] };
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
        token_program_x: token_program,
        token_program_y: token_program,
        memo_program,
        event_authority,
        program: dlmm::ID,
    }
    .to_account_metas(None);
    fee_accounts.extend(bin_array_metas.clone());
    instructions.push(Instruction {
        program_id: dlmm::ID,
        accounts: fee_accounts,
        data: dlmm::client::args::ClaimFee2 {
            min_bin_id: position.lower_bin_id,
            max_bin_id: position.upper_bin_id,
            remaining_accounts_info: remaining_accounts_info.clone(),
        }
        .data(),
    });

    let mut reward_indices_claimed = vec![];
    for (reward_index, reward_mint, reward_vault, user_reward) in reward_entries {
        let mut reward_accounts = dlmm::client::accounts::ClaimReward2 {
            lb_pair: lb_pair_key,
            position: position_key,
            sender,
            reward_vault,
            reward_mint,
            user_token_account: user_reward,
            token_program,
            memo_program,
            event_authority,
            program: dlmm::ID,
        }
        .to_account_metas(None);
        reward_accounts.extend(bin_array_metas.clone());
        instructions.push(Instruction {
            program_id: dlmm::ID,
            accounts: reward_accounts,
            data: dlmm::client::args::ClaimReward2 {
                reward_index,
                min_bin_id: position.lower_bin_id,
                max_bin_id: position.upper_bin_id,
                remaining_accounts_info: remaining_accounts_info.clone(),
            }
            .data(),
        });
        reward_indices_claimed.push(reward_index);
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
            "settlement transaction unexpectedly requires {required_signatures} signatures"
        );
    }
    let transaction = VersionedTransaction {
        signatures: vec![Signature::default(); required_signatures],
        message: VersionedMessage::Legacy(message),
    };
    let transaction_base64 = general_purpose::STANDARD.encode(
        bincode::serialize(&transaction)
            .context("failed to serialize settlement transaction")?,
    );

    let build = SettlementBuildReport {
        transaction_base64,
        program_id: dlmm::ID.to_string(),
        position: position_key.to_string(),
        lb_pair: lb_pair_key.to_string(),
        sender: sender.to_string(),
        claims_fees: true,
        reward_indices_claimed: reward_indices_claimed.clone(),
        closes_position_account: true,
        instruction_count: instructions.len(),
    };

    Ok(ChainResolvedSettlementReport {
        validation: SettlementValidation {
            position: position_key.to_string(),
            lb_pair: lb_pair_key.to_string(),
            owner_matches_sender: true,
            position_has_zero_liquidity: true,
            token_programs_standard_spl: true,
            position_width: width,
            reward_indices_claimed,
            bin_array_accounts: bin_array_keys
                .iter()
                .map(ToString::to_string)
                .collect(),
        },
        build,
    })
}
