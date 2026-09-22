use std::str::FromStr;

use anchor_client::solana_client::nonblocking::rpc_client::RpcClient;
use anchor_client::solana_sdk::pubkey::Pubkey;
use anyhow::{Context, Result};
use commons::dlmm::accounts::{BinArray, LbPair};
use commons::{derive_bin_array_pda, pod_read_unaligned_skip_disc, BinArrayExtension};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct BinSnapshot {
    pub bin_id: i32,
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
