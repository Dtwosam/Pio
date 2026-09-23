use std::collections::HashMap;
use std::str::FromStr;

use anchor_client::solana_client::nonblocking::rpc_client::RpcClient;
use anchor_client::solana_sdk::pubkey::Pubkey;
use anchor_client::solana_sdk::sysvar::clock::{Clock, ID as CLOCK_ID};
use anyhow::{Context, Result};
use commons::dlmm::accounts::{BinArray, LbPair, PositionV2};
use commons::{
    derive_bin_array_pda, get_price_from_id, pod_read_unaligned_skip_disc, BinArrayExtension,
    DynamicPosition, LbPairExtension, MAX_REWARD_BIN_SPLIT, NUM_REWARDS, SCALE_OFFSET,
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
    pub reward_per_token_stored: [String; 2],
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
    pub token_x_program: String,
    pub token_y_program: String,
    pub base_fee_rate: String,
    pub variable_fee_rate: String,
    pub total_fee_rate: String,
    pub deposit_total_fee_rate: String,
    pub protocol_share_bps: u16,
    pub collect_fee_mode: u8,
    pub supports_limit_order: bool,
    pub reward_mints: [String; 2],
    pub reward_rates: [String; 2],
    pub reward_duration_ends: [u64; 2],
    pub reward_last_update_times: [u64; 2],
    pub bin_arrays: Vec<BinArraySnapshot>,
}

#[derive(Debug, Serialize)]
pub struct PositionBinSnapshot {
    pub bin_id: i32,
    pub price: String,
    pub bin_x_amount: String,
    pub bin_y_amount: String,
    pub bin_liquidity: String,
    pub bin_fee_x_per_token_stored: String,
    pub bin_fee_y_per_token_stored: String,
    pub bin_reward_per_token_stored: [String; 2],
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
    pub supports_limit_order: bool,
    pub reward_mints: [String; 2],
    pub bins: Vec<PositionBinSnapshot>,
}

fn decode_lb_pair(data: &[u8]) -> Result<LbPair> {
    pod_read_unaligned_skip_disc::<LbPair>(data)
}

fn decode_bin_array(data: &[u8]) -> Result<BinArray> {
    pod_read_unaligned_skip_disc::<BinArray>(data)
}

fn effective_reward_per_token_stored(
    lb_pair: &LbPair,
    bin_id: i32,
    liquidity_supply: u128,
    fulfilled_order_amount_x: u64,
    fulfilled_order_amount_y: u64,
    limit_order_fee_ask_side: u64,
    limit_order_fee_bid_side: u64,
    current_timestamp: i64,
) -> [u128; NUM_REWARDS] {
    if lb_pair.is_support_limit_order() {
        return [0u128; NUM_REWARDS];
    }

    let mut reward_0_bytes = [0u8; 16];
    reward_0_bytes[0..8].copy_from_slice(&fulfilled_order_amount_x.to_le_bytes());
    reward_0_bytes[8..16].copy_from_slice(&fulfilled_order_amount_y.to_le_bytes());

    let mut reward_1_bytes = [0u8; 16];
    reward_1_bytes[0..8].copy_from_slice(&limit_order_fee_ask_side.to_le_bytes());
    reward_1_bytes[8..16].copy_from_slice(&limit_order_fee_bid_side.to_le_bytes());

    let mut rewards = [
        u128::from_le_bytes(reward_0_bytes),
        u128::from_le_bytes(reward_1_bytes),
    ];

    if bin_id != lb_pair.active_id || liquidity_supply == 0 {
        return rewards;
    }

    let now = u64::try_from(current_timestamp).unwrap_or(0);
    let liquidity_supply_scaled = liquidity_supply >> SCALE_OFFSET as u32;
    if liquidity_supply_scaled == 0 {
        return rewards;
    }

    for (index, reward) in rewards.iter_mut().enumerate() {
        let reward_info = &lb_pair.reward_infos[index];
        if reward_info.mint == Pubkey::default() {
            continue;
        }

        let current_time = std::cmp::min(now, reward_info.reward_duration_end);
        let delta = current_time.saturating_sub(reward_info.last_update_time);
        if delta == 0 {
            continue;
        }

        let reward_delta = reward_info
            .reward_rate
            .checked_mul(delta.into())
            .and_then(|value| value.checked_div(MAX_REWARD_BIN_SPLIT as u128))
            .and_then(|value| value.checked_div(liquidity_supply_scaled))
            .unwrap_or(0);
        *reward = reward.saturating_add(reward_delta);
    }

    rewards
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
    let mut lb_pair = decode_lb_pair(&account.data).context("failed to decode LbPair")?;
    let token_programs = lb_pair
        .get_token_programs()
        .context("failed to resolve token programs")?;
    let base_fee_rate = lb_pair.get_base_fee().context("failed to compute base fee")?;
    let variable_fee_rate = lb_pair
        .get_variable_fee()
        .context("failed to compute variable fee")?;
    let total_fee_rate = lb_pair
        .get_total_fee()
        .context("failed to compute total fee")?;

    let clock_account = rpc
        .get_account(&CLOCK_ID)
        .await
        .context("failed to fetch Solana clock")?;
    let clock: Clock =
        bincode::deserialize(&clock_account.data).context("failed to decode Solana clock")?;
    lb_pair
        .update_references(clock.unix_timestamp)
        .context("failed to update fee references")?;
    lb_pair
        .update_volatility_accumulator()
        .context("failed to update volatility accumulator")?;
    let deposit_total_fee_rate = lb_pair
        .get_total_fee()
        .context("failed to compute deposit-time total fee")?;

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
                let bin_id = lower_bin_id + offset as i32;
                let price = if bin.price == 0 {
                    get_price_from_id(bin_id, lb_pair.bin_step).ok()?
                } else {
                    bin.price
                };
                let rewards = effective_reward_per_token_stored(
                    &lb_pair,
                    bin_id,
                    bin.liquidity_supply,
                    bin.fulfilled_order_amount_x,
                    bin.fulfilled_order_amount_y,
                    bin.limit_order_fee_ask_side,
                    bin.limit_order_fee_bid_side,
                    clock.unix_timestamp,
                );
                Some(BinSnapshot {
                    bin_id,
                    price: price.to_string(),
                    amount_x: bin.amount_x.to_string(),
                    amount_y: bin.amount_y.to_string(),
                    liquidity_supply: bin.liquidity_supply.to_string(),
                    fee_amount_x_per_token_stored: bin
                        .fee_amount_x_per_token_stored
                        .to_string(),
                    fee_amount_y_per_token_stored: bin
                        .fee_amount_y_per_token_stored
                        .to_string(),
                    reward_per_token_stored: [
                        rewards[0].to_string(),
                        rewards[1].to_string(),
                    ],
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
        token_x_program: token_programs[0].to_string(),
        token_y_program: token_programs[1].to_string(),
        base_fee_rate: base_fee_rate.to_string(),
        variable_fee_rate: variable_fee_rate.to_string(),
        total_fee_rate: total_fee_rate.to_string(),
        deposit_total_fee_rate: deposit_total_fee_rate.to_string(),
        protocol_share_bps: lb_pair.parameters.protocol_share,
        collect_fee_mode: lb_pair.parameters.collect_fee_mode,
        supports_limit_order: lb_pair.is_support_limit_order(),
        reward_mints: [
            lb_pair.reward_infos[0].mint.to_string(),
            lb_pair.reward_infos[1].mint.to_string(),
        ],
        reward_rates: [
            lb_pair.reward_infos[0].reward_rate.to_string(),
            lb_pair.reward_infos[1].reward_rate.to_string(),
        ],
        reward_duration_ends: [
            lb_pair.reward_infos[0].reward_duration_end,
            lb_pair.reward_infos[1].reward_duration_end,
        ],
        reward_last_update_times: [
            lb_pair.reward_infos[0].last_update_time,
            lb_pair.reward_infos[1].last_update_time,
        ],
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
        .map(|bin| {
            let (
                bin_fee_x_per_token_stored,
                bin_fee_y_per_token_stored,
                bin_reward_per_token_stored,
            ) = BinArray::bin_id_to_bin_array_index(bin.bin_id)
                .ok()
                .and_then(|index| bin_array_map.get(&index))
                .and_then(|array| array.get_bin(bin.bin_id).ok())
                .map(|raw_bin| {
                    (
                        raw_bin.fee_amount_x_per_token_stored,
                        raw_bin.fee_amount_y_per_token_stored,
                        effective_reward_per_token_stored(
                            &lb_pair_state,
                            bin.bin_id,
                            raw_bin.liquidity_supply,
                            raw_bin.fulfilled_order_amount_x,
                            raw_bin.fulfilled_order_amount_y,
                            raw_bin.limit_order_fee_ask_side,
                            raw_bin.limit_order_fee_bid_side,
                            clock.unix_timestamp,
                        ),
                    )
                })
                .unwrap_or((0, 0, [0u128; NUM_REWARDS]));

            PositionBinSnapshot {
            bin_id: bin.bin_id,
            price: bin.price.to_string(),
            bin_x_amount: bin.bin_x_amount.to_string(),
            bin_y_amount: bin.bin_y_amount.to_string(),
            bin_liquidity: bin.bin_liquidity.to_string(),
            bin_fee_x_per_token_stored: bin_fee_x_per_token_stored.to_string(),
            bin_fee_y_per_token_stored: bin_fee_y_per_token_stored.to_string(),
            bin_reward_per_token_stored: [
                bin_reward_per_token_stored[0].to_string(),
                bin_reward_per_token_stored[1].to_string(),
            ],
            position_liquidity: bin.position_liquidity.to_string(),
            position_x_amount: bin.position_x_amount.to_string(),
            position_y_amount: bin.position_y_amount.to_string(),
            position_fee_x_amount: bin.position_fee_x_amount.to_string(),
            position_fee_y_amount: bin.position_fee_y_amount.to_string(),
            position_reward_amounts: [
                bin.position_reward_amounts[0].to_string(),
                bin.position_reward_amounts[1].to_string(),
            ],
        }
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
        supports_limit_order: lb_pair_state.is_support_limit_order(),
        reward_mints: [
            lb_pair_state.reward_infos[0].mint.to_string(),
            lb_pair_state.reward_infos[1].mint.to_string(),
        ],
        bins,
    })
}
