use std::collections::HashMap;
use std::str::FromStr;

use anchor_client::solana_client::nonblocking::rpc_client::RpcClient;
use anchor_client::solana_sdk::pubkey::Pubkey;
use anchor_client::solana_sdk::sysvar::clock::{Clock, ID as CLOCK_ID};
use anyhow::{Context, Result};
use commons::dlmm::accounts::{BinArray, LbPair, PositionV2};
use commons::{
    derive_bin_array_pda, pod_read_unaligned_skip_disc, BinArrayExtension, DynamicPosition,
};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct BinSnapshot {
    pub bin_id: i32,
    pub price: String,
    pub amount_x: String,
    pub amount_y: String,
    pub liquidity_supply: String,
    pub fee_amount_x_per_token_stored: String,
    pub fee_amount_y_per_token_stored: String,
}

#[derive(Debug, Serialize)]
pub struct BinArraySnapshot {
    pub address: String,
    pub index: i64,
    pub lower_bin_id: i32,
    pub upper_bin_id: i32,
    pub bins: Vec<BinSnapshot>,
}

#[derive(Debug, Serialize)]
pub struct PoolChainSnapshot {
    pub pool_address: String,
    pub active_bin_id: i32,
    pub bin_step: u16,
    pub token_x_mint: String,
    pub token_y_mint: String,
    pub bin_arrays: Vec<BinArraySnapshot>,
}

#[derive(Debug, Serialize)]
pub struct PositionBinSnapshot {
    pub bin_id: i32,
    pub price: String,
    pub bin_x_amount: String,
    pub bin_y_amount: String,
    pub bin_liquidity: String,
    pub position_liquidity: String,
    pub position_x_amount: String,
    pub position_y_amount: String,
    pub position_fee_x_amount: String,
    pub position_fee_y_amount: String,
    pub position_reward_amounts: [String; 2],
}

#[derive(Debug, Serialize)]
pub struct PositionChainSnapshot {
    pub position_address: String,
    pub pool_address: String,
    pub owner: String,
    pub fee_owner: String,
    pub lower_bin_id: i32,
    pub upper_bin_id: i32,
    pub total_x_amount: String,
    pub total_y_amount: String,
    pub fee_x: String,
    pub fee_y: String,
    pub reward_one: String,
    pub reward_two: String,
    pub last_updated_at: i64,
    pub total_claimed_fee_x_amount: String,
    pub total_claimed_fee_y_amount: String,
    pub bins: Vec<PositionBinSnapshot>,
}

fn decode_lb_pair(data: &[u8]) -> Result<LbPair> {
    pod_read_unaligned_skip_disc::<LbPair>(data)
}

fn decode_bin_array(data: &[u8]) -> Result<BinArray> {
    pod_read_unaligned_skip_disc::<BinArray>(data)
}

pub async fn inspect_pool(
    rpc_url: &str,
    pool_address: &str,
    array_radius: i32,
) -> Result<PoolChainSnapshot> {
    if array_radius < 0 || array_radius > 8 {
        anyhow::bail!("array_radius must be between 0 and 8");
    }

    let pool = Pubkey::from_str(pool_address).context("invalid pool address")?;
    let rpc = RpcClient::new(rpc_url.to_string());
    let account = rpc
        .get_account(&pool)
        .await
        .with_context(|| format!("failed to fetch pool account {pool}"))?;
    if account.owner != commons::dlmm::ID {
        anyhow::bail!("pool account is not owned by Meteora DLMM");
    }
    let lb_pair = decode_lb_pair(&account.data).context("failed to decode LbPair")?;

    let active_array_index =
        BinArray::bin_id_to_bin_array_index(lb_pair.active_id).context("active bin index")?;

    let indexes: Vec<i32> = ((active_array_index - array_radius)
        ..=(active_array_index + array_radius))
        .collect();
    let pubkeys: Vec<Pubkey> = indexes
        .iter()
        .map(|index| derive_bin_array_pda(pool, i64::from(*index)).0)
        .collect();

    let accounts = rpc
        .get_multiple_accounts(&pubkeys)
        .await
        .context("failed to fetch bin array accounts")?;

    let mut bin_arrays = Vec::new();
    for ((index, pubkey), account) in indexes.into_iter().zip(pubkeys).zip(accounts) {
        let Some(account) = account else {
            continue;
        };
        let bin_array = decode_bin_array(&account.data)
            .with_context(|| format!("failed to decode bin array {pubkey}"))?;
        let (lower_bin_id, upper_bin_id) =
            BinArray::get_bin_array_lower_upper_bin_id(index).context("bin array coverage")?;

        let bins = bin_array
            .bins
            .iter()
            .enumerate()
            .filter_map(|(offset, bin)| {
                if bin.amount_x == 0 && bin.amount_y == 0 && bin.liquidity_supply == 0 {
                    return None;
                }
                Some(BinSnapshot {
                    bin_id: lower_bin_id + offset as i32,
                    price: bin.price.to_string(),
                    amount_x: bin.amount_x.to_string(),
                    amount_y: bin.amount_y.to_string(),
                    liquidity_supply: bin.liquidity_supply.to_string(),
                    fee_amount_x_per_token_stored: bin
                        .fee_amount_x_per_token_stored
                        .to_string(),
                    fee_amount_y_per_token_stored: bin
                        .fee_amount_y_per_token_stored
                        .to_string(),
                })
            })
            .collect();

        bin_arrays.push(BinArraySnapshot {
            address: pubkey.to_string(),
            index: i64::from(index),
            lower_bin_id,
            upper_bin_id,
            bins,
        });
    }

    Ok(PoolChainSnapshot {
        pool_address: pool.to_string(),
        active_bin_id: lb_pair.active_id,
        bin_step: lb_pair.bin_step,
        token_x_mint: lb_pair.token_x_mint.to_string(),
        token_y_mint: lb_pair.token_y_mint.to_string(),
        bin_arrays,
    })
}

pub async fn inspect_position(
    rpc_url: &str,
    position_address: &str,
) -> Result<PositionChainSnapshot> {
    let position_key = Pubkey::from_str(position_address).context("invalid position address")?;
    let rpc = RpcClient::new(rpc_url.to_string());

    let position_account = rpc
        .get_account(&position_key)
        .await
        .with_context(|| format!("failed to fetch position account {position_key}"))?;
    if position_account.owner != commons::dlmm::ID {
        anyhow::bail!("position account is not owned by Meteora DLMM");
    }
    let position_state: PositionV2 = pod_read_unaligned_skip_disc(&position_account.data)
        .context("failed to decode PositionV2")?;

    let lower_index =
        BinArray::bin_id_to_bin_array_index(position_state.lower_bin_id).context("lower bin")?;
    let upper_index =
        BinArray::bin_id_to_bin_array_index(position_state.upper_bin_id).context("upper bin")?;

    let bin_array_pubkeys: Vec<Pubkey> = (lower_index..=upper_index)
        .map(|index| derive_bin_array_pda(position_state.lb_pair, i64::from(index)).0)
        .collect();

    let accounts_to_fetch: Vec<Pubkey> = [
        vec![position_state.lb_pair, CLOCK_ID],
        bin_array_pubkeys.clone(),
    ]
    .concat();
    let fetched = rpc
        .get_multiple_accounts(&accounts_to_fetch)
        .await
        .context("failed to fetch position dependencies")?;

    let lb_pair_account = fetched
        .first()
        .and_then(|account| account.as_ref())
        .context("pool account missing")?;
    let lb_pair_state = decode_lb_pair(&lb_pair_account.data).context("failed to decode LbPair")?;

    let clock_account = fetched
        .get(1)
        .and_then(|account| account.as_ref())
        .context("clock account missing")?;
    let clock: Clock =
        bincode::deserialize(&clock_account.data).context("failed to decode Solana clock")?;

    let mut bin_array_map: HashMap<i32, BinArray> = HashMap::new();
    for (offset, index) in (lower_index..=upper_index).enumerate() {
        if let Some(account) = fetched.get(2 + offset).and_then(|account| account.as_ref()) {
            let bin_array = decode_bin_array(&account.data)
                .with_context(|| format!("failed to decode bin array index {index}"))?;
            bin_array_map.insert(index, bin_array);
        }
    }

    let parsed = DynamicPosition::parse(
        &position_state,
        &position_account.data,
        &lb_pair_state,
        &bin_array_map,
        clock.unix_timestamp,
    )
    .context("failed to compute dynamic position state")?;

    let bins = parsed
        .bins
        .into_iter()
        .map(|bin| PositionBinSnapshot {
            bin_id: bin.bin_id,
            price: bin.price.to_string(),
            bin_x_amount: bin.bin_x_amount.to_string(),
            bin_y_amount: bin.bin_y_amount.to_string(),
            bin_liquidity: bin.bin_liquidity.to_string(),
            position_liquidity: bin.position_liquidity.to_string(),
            position_x_amount: bin.position_x_amount.to_string(),
            position_y_amount: bin.position_y_amount.to_string(),
            position_fee_x_amount: bin.position_fee_x_amount.to_string(),
            position_fee_y_amount: bin.position_fee_y_amount.to_string(),
            position_reward_amounts: [
                bin.position_reward_amounts[0].to_string(),
                bin.position_reward_amounts[1].to_string(),
            ],
        })
        .collect();

    Ok(PositionChainSnapshot {
        position_address: position_key.to_string(),
        pool_address: parsed.lb_pair.to_string(),
        owner: parsed.owner.to_string(),
        fee_owner: parsed.fee_owner.to_string(),
        lower_bin_id: parsed.lower_bin_id,
        upper_bin_id: parsed.upper_bin_id,
        total_x_amount: parsed.total_x_amount.to_string(),
        total_y_amount: parsed.total_y_amount.to_string(),
        fee_x: parsed.fee_x.to_string(),
        fee_y: parsed.fee_y.to_string(),
        reward_one: parsed.reward_one.to_string(),
        reward_two: parsed.reward_two.to_string(),
        last_updated_at: parsed.last_updated_at,
        total_claimed_fee_x_amount: parsed.total_claimed_fee_x_amount.to_string(),
        total_claimed_fee_y_amount: parsed.total_claimed_fee_y_amount.to_string(),
        bins,
    })
}
