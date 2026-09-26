use std::collections::{BTreeMap, BTreeSet};
use std::str::FromStr;

use anchor_client::solana_client::nonblocking::rpc_client::RpcClient;
use anchor_client::solana_client::rpc_config::RpcTransactionConfig;
use solana_transaction_status_client_types::UiTransactionEncoding;
use anchor_client::solana_sdk::commitment_config::CommitmentConfig;
use anchor_client::solana_sdk::signature::Signature;
use anyhow::{Context, Result};
use serde::Serialize;
use serde_json::Value;

use crate::events::{decode_event_cpi_data, DecodedDlmmEvent};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TransactionEventRecord {
    pub event_index: usize,
    pub parent_ix_index: u64,
    pub event: DecodedDlmmEvent,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ExplicitBinDistribution {
    pub bin_id: i32,
    pub distribution_x: u16,
    pub distribution_y: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WeightedBinDistribution {
    pub bin_id: i32,
    pub weight: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LiquidityAddRequest {
    pub instruction_index: usize,
    pub instruction_type: String,
    pub requested_amount_x: String,
    pub requested_amount_y: String,
    pub observed_active_id: Option<i32>,
    pub max_active_bin_slippage: Option<i32>,
    pub min_bin_id: Option<i32>,
    pub max_bin_id: Option<i32>,
    pub strategy_variant: Option<u8>,
    pub strategy_favor_x: Option<bool>,
    pub explicit_distribution: Vec<ExplicitBinDistribution>,
    pub weighted_distribution: Vec<WeightedBinDistribution>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RebalanceRequest {
    pub instruction_index: usize,
    pub observed_active_id: i32,
    pub max_active_bin_slippage: u16,
    pub should_claim_fee: bool,
    pub should_claim_reward: bool,
    pub min_withdraw_x_amount: String,
    pub max_deposit_x_amount: String,
    pub min_withdraw_y_amount: String,
    pub max_deposit_y_amount: String,
    pub shrink_mode: u8,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TokenBalanceDelta {
    pub account_index: u64,
    pub account_address: Option<String>,
    pub mint: String,
    pub pre_owner: Option<String>,
    pub post_owner: Option<String>,
    pub pre_amount: String,
    pub post_amount: String,
    pub delta_amount: String,
    pub decimals: Option<u8>,
}

#[derive(Debug, Serialize)]
pub struct TransactionEventSnapshot {
    pub signature: String,
    pub slot: u64,
    pub block_time: Option<i64>,
    pub network_fee_lamports: Option<u64>,
    pub compute_units_consumed: Option<u64>,
    pub succeeded: Option<bool>,
    pub token_balance_deltas: Vec<TokenBalanceDelta>,
    pub add_requests: Vec<LiquidityAddRequest>,
    pub rebalance_requests: Vec<RebalanceRequest>,
    pub events: Vec<TransactionEventRecord>,
}

const ADD_LIQUIDITY_IX: [u8; 8] = [181, 157, 89, 67, 143, 182, 52, 72];
const ADD_LIQUIDITY2_IX: [u8; 8] = [228, 162, 78, 28, 70, 219, 116, 115];
const ADD_BY_STRATEGY_IX: [u8; 8] = [7, 3, 150, 127, 148, 40, 61, 200];
const ADD_BY_STRATEGY2_IX: [u8; 8] = [3, 221, 149, 218, 111, 141, 118, 213];
const ADD_BY_WEIGHT_IX: [u8; 8] = [28, 140, 238, 99, 231, 162, 21, 149];
const ADD_BY_WEIGHT2_IX: [u8; 8] = [209, 59, 63, 91, 111, 200, 153, 228];
const REBALANCE_LIQUIDITY_IX: [u8; 8] = [92, 4, 176, 193, 119, 185, 83, 9];

fn read_i32_at(data: &[u8], offset: usize) -> Option<i32> {
    Some(i32::from_le_bytes(data.get(offset..offset + 4)?.try_into().ok()?))
}

fn read_u16_at(data: &[u8], offset: usize) -> Option<u16> {
    Some(u16::from_le_bytes(data.get(offset..offset + 2)?.try_into().ok()?))
}

fn read_u32_at(data: &[u8], offset: usize) -> Option<u32> {
    Some(u32::from_le_bytes(data.get(offset..offset + 4)?.try_into().ok()?))
}

fn decode_add_request(data: &[u8], instruction_index: usize) -> Option<LiquidityAddRequest> {
    if data.len() < 24 {
        return None;
    }
    let discriminator: [u8; 8] = data[..8].try_into().ok()?;
    let amount_x = u64::from_le_bytes(data[8..16].try_into().ok()?);
    let amount_y = u64::from_le_bytes(data[16..24].try_into().ok()?);

    let mut observed_active_id = None;
    let mut max_active_bin_slippage = None;
    let mut min_bin_id = None;
    let mut max_bin_id = None;
    let mut strategy_variant = None;
    let mut strategy_favor_x = None;
    let mut explicit_distribution = Vec::new();
    let mut weighted_distribution = Vec::new();

    let instruction_type = match discriminator {
        ADD_LIQUIDITY_IX | ADD_LIQUIDITY2_IX => {
            let count = read_u32_at(data, 24)? as usize;
            let mut offset = 28usize;
            for _ in 0..count {
                let bin_id = read_i32_at(data, offset)?;
                let distribution_x = read_u16_at(data, offset + 4)?;
                let distribution_y = read_u16_at(data, offset + 6)?;
                explicit_distribution.push(ExplicitBinDistribution {
                    bin_id,
                    distribution_x,
                    distribution_y,
                });
                offset += 8;
            }
            if discriminator == ADD_LIQUIDITY_IX {
                "add_liquidity"
            } else {
                "add_liquidity2"
            }
        }
        ADD_BY_STRATEGY_IX | ADD_BY_STRATEGY2_IX => {
            if data.len() < 105 {
                return None;
            }
            observed_active_id = Some(read_i32_at(data, 24)?);
            max_active_bin_slippage = Some(read_i32_at(data, 28)?);
            min_bin_id = Some(read_i32_at(data, 32)?);
            max_bin_id = Some(read_i32_at(data, 36)?);
            strategy_variant = data.get(40).copied();
            strategy_favor_x = data.get(41).map(|value| *value == 1);
            if discriminator == ADD_BY_STRATEGY_IX {
                "add_liquidity_by_strategy"
            } else {
                "add_liquidity_by_strategy2"
            }
        }
        ADD_BY_WEIGHT_IX | ADD_BY_WEIGHT2_IX => {
            observed_active_id = Some(read_i32_at(data, 24)?);
            max_active_bin_slippage = Some(read_i32_at(data, 28)?);
            let count = read_u32_at(data, 32)? as usize;
            let mut offset = 36usize;
            for _ in 0..count {
                let bin_id = read_i32_at(data, offset)?;
                let weight = read_u16_at(data, offset + 4)?;
                weighted_distribution.push(WeightedBinDistribution { bin_id, weight });
                offset += 6;
            }
            if discriminator == ADD_BY_WEIGHT_IX {
                "add_liquidity_by_weight"
            } else {
                "add_liquidity_by_weight2"
            }
        }
        _ => return None,
    };

    Some(LiquidityAddRequest {
        instruction_index,
        instruction_type: instruction_type.to_string(),
        requested_amount_x: amount_x.to_string(),
        requested_amount_y: amount_y.to_string(),
        observed_active_id,
        max_active_bin_slippage,
        min_bin_id,
        max_bin_id,
        strategy_variant,
        strategy_favor_x,
        explicit_distribution,
        weighted_distribution,
    })
}
fn decode_rebalance_request(
    data: &[u8],
    instruction_index: usize,
) -> Option<RebalanceRequest> {
    if data.len() < 80 || data[..8] != REBALANCE_LIQUIDITY_IX {
        return None;
    }

    Some(RebalanceRequest {
        instruction_index,
        observed_active_id: read_i32_at(data, 8)?,
        max_active_bin_slippage: read_u16_at(data, 12)?,
        should_claim_fee: *data.get(14)? != 0,
        should_claim_reward: *data.get(15)? != 0,
        min_withdraw_x_amount: u64::from_le_bytes(
            data.get(16..24)?.try_into().ok()?,
        )
        .to_string(),
        max_deposit_x_amount: u64::from_le_bytes(
            data.get(24..32)?.try_into().ok()?,
        )
        .to_string(),
        min_withdraw_y_amount: u64::from_le_bytes(
            data.get(32..40)?.try_into().ok()?,
        )
        .to_string(),
        max_deposit_y_amount: u64::from_le_bytes(
            data.get(40..48)?.try_into().ok()?,
        )
        .to_string(),
        shrink_mode: *data.get(48)?,
    })
}

fn decode_rebalance_requests(value: &Value) -> Vec<RebalanceRequest> {
    let Some(instructions) = value
        .pointer("/transaction/transaction/message/instructions")
        .and_then(Value::as_array)
    else {
        return Vec::new();
    };
    let meteora_program = commons::dlmm::ID.to_string();

    instructions
        .iter()
        .enumerate()
        .filter_map(|(index, instruction)| {
            if instruction.get("programId").and_then(Value::as_str)
                != Some(meteora_program.as_str())
            {
                return None;
            }
            let encoded = instruction.get("data").and_then(Value::as_str)?;
            let data = bs58::decode(encoded).into_vec().ok()?;
            decode_rebalance_request(&data, index)
        })
        .collect()
}

fn decode_add_requests(value: &Value) -> Vec<LiquidityAddRequest> {
    let Some(instructions) = value
        .pointer("/transaction/transaction/message/instructions")
        .and_then(Value::as_array)
    else {
        return Vec::new();
    };
    let meteora_program = commons::dlmm::ID.to_string();

    instructions
        .iter()
        .enumerate()
        .filter_map(|(index, instruction)| {
            if instruction.get("programId").and_then(Value::as_str)
                != Some(meteora_program.as_str())
            {
                return None;
            }
            let encoded = instruction.get("data").and_then(Value::as_str)?;
            let data = bs58::decode(encoded).into_vec().ok()?;
            decode_add_request(&data, index)
        })
        .collect()
}

#[derive(Debug, Clone)]
struct TokenBalancePoint {
    amount: u64,
    owner: Option<String>,
    decimals: Option<u8>,
}

fn token_balance_array<'a>(
    meta: &'a Value,
    camel_key: &str,
    snake_key: &str,
) -> Option<&'a Vec<Value>> {
    meta.get(camel_key)
        .or_else(|| meta.get(snake_key))
        .and_then(Value::as_array)
}

fn token_balance_map(
    meta: &Value,
    camel_key: &str,
    snake_key: &str,
) -> Result<BTreeMap<(u64, String), TokenBalancePoint>> {
    let Some(entries) = token_balance_array(meta, camel_key, snake_key) else {
        return Ok(BTreeMap::new());
    };

    let mut out = BTreeMap::new();
    for entry in entries {
        let account_index = entry
            .get("accountIndex")
            .or_else(|| entry.get("account_index"))
            .and_then(Value::as_u64)
            .context("token balance is missing account index")?;
        let mint = entry
            .get("mint")
            .and_then(Value::as_str)
            .context("token balance is missing mint")?
            .to_string();
        let ui_amount = entry
            .get("uiTokenAmount")
            .or_else(|| entry.get("ui_token_amount"))
            .context("token balance is missing ui token amount")?;
        let amount = ui_amount
            .get("amount")
            .and_then(Value::as_str)
            .context("token balance is missing atomic amount")?
            .parse::<u64>()
            .context("token balance atomic amount is not u64")?;
        let decimals = ui_amount
            .get("decimals")
            .and_then(Value::as_u64)
            .map(u8::try_from)
            .transpose()
            .context("token balance decimals exceed u8")?;
        let owner = entry
            .get("owner")
            .and_then(Value::as_str)
            .map(str::to_string);
        out.insert(
            (account_index, mint),
            TokenBalancePoint {
                amount,
                owner,
                decimals,
            },
        );
    }
    Ok(out)
}

fn account_address_at(value: &Value, account_index: u64) -> Option<String> {
    let index = usize::try_from(account_index).ok()?;
    let account = value
        .pointer("/transaction/transaction/message/accountKeys")
        .and_then(Value::as_array)?
        .get(index)?;
    account
        .as_str()
        .map(str::to_string)
        .or_else(|| {
            account
                .get("pubkey")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
}

fn extract_token_balance_deltas(
    value: &Value,
) -> Result<Vec<TokenBalanceDelta>> {
    let Some(meta) = value.pointer("/transaction/meta") else {
        return Ok(Vec::new());
    };
    let pre = token_balance_map(
        meta,
        "preTokenBalances",
        "pre_token_balances",
    )?;
    let post = token_balance_map(
        meta,
        "postTokenBalances",
        "post_token_balances",
    )?;

    let keys: BTreeSet<(u64, String)> = pre
        .keys()
        .chain(post.keys())
        .cloned()
        .collect();
    let mut out = Vec::new();
    for (account_index, mint) in keys {
        let before = pre.get(&(account_index, mint.clone()));
        let after = post.get(&(account_index, mint.clone()));
        let pre_amount = before.map(|item| item.amount).unwrap_or(0);
        let post_amount = after.map(|item| item.amount).unwrap_or(0);
        if pre_amount == post_amount {
            continue;
        }

        let pre_decimals = before.and_then(|item| item.decimals);
        let post_decimals = after.and_then(|item| item.decimals);
        if pre_decimals.is_some()
            && post_decimals.is_some()
            && pre_decimals != post_decimals
        {
            anyhow::bail!(
                "token balance decimals changed for account {account_index} mint {mint}"
            );
        }

        out.push(TokenBalanceDelta {
            account_index,
            account_address: account_address_at(value, account_index),
            mint,
            pre_owner: before.and_then(|item| item.owner.clone()),
            post_owner: after.and_then(|item| item.owner.clone()),
            pre_amount: pre_amount.to_string(),
            post_amount: post_amount.to_string(),
            delta_amount: (
                i128::from(post_amount) - i128::from(pre_amount)
            )
            .to_string(),
            decimals: post_decimals.or(pre_decimals),
        });
    }
    Ok(out)
}

fn extract_transaction_costs(value: &Value) -> (Option<u64>, Option<u64>, Option<bool>) {
    let meta = value.pointer("/transaction/meta");
    let fee = meta
        .and_then(|value| value.get("fee"))
        .and_then(Value::as_u64);
    let compute_units = meta
        .and_then(|value| {
            value
                .get("computeUnitsConsumed")
                .or_else(|| value.get("compute_units_consumed"))
        })
        .and_then(Value::as_u64);
    let succeeded = meta
        .and_then(|value| value.get("err"))
        .map(Value::is_null);
    (fee, compute_units, succeeded)
}

fn decode_inner_events(value: &Value) -> Result<Vec<TransactionEventRecord>> {
    let mut out = Vec::new();
    let Some(groups) = value
        .pointer("/transaction/meta/innerInstructions")
        .and_then(Value::as_array)
    else {
        return Ok(out);
    };

    let meteora_program = commons::dlmm::ID.to_string();

    for group in groups {
        let parent_ix_index = group
            .get("index")
            .and_then(Value::as_u64)
            .context("inner instruction group is missing index")?;
        let Some(instructions) = group.get("instructions").and_then(Value::as_array) else {
            continue;
        };

        for instruction in instructions {
            if instruction
                .get("programId")
                .and_then(Value::as_str)
                != Some(meteora_program.as_str())
            {
                continue;
            }
            let Some(encoded) = instruction.get("data").and_then(Value::as_str) else {
                continue;
            };
            let Ok(data) = bs58::decode(encoded).into_vec() else {
                continue;
            };
            if let Some(event) = decode_event_cpi_data(&data)? {
                out.push(TransactionEventRecord {
                    event_index: out.len(),
                    parent_ix_index,
                    event,
                });
            }
        }
    }

    Ok(out)
}

pub async fn inspect_transaction_events(
    rpc_url: &str,
    signature: &str,
) -> Result<TransactionEventSnapshot> {
    let signature = Signature::from_str(signature).context("invalid transaction signature")?;
    let rpc = RpcClient::new(rpc_url.to_string());
    let confirmed = rpc
        .get_transaction_with_config(
            &signature,
            RpcTransactionConfig {
                encoding: Some(UiTransactionEncoding::JsonParsed),
                commitment: Some(CommitmentConfig::confirmed()),
                max_supported_transaction_version: Some(0),
            },
        )
        .await
        .context("failed to fetch transaction")?;

    let value = serde_json::to_value(&confirmed).context("failed to serialize transaction")?;
    let events = decode_inner_events(&value)?;
    let add_requests = decode_add_requests(&value);
    let rebalance_requests = decode_rebalance_requests(&value);
    let (network_fee_lamports, compute_units_consumed, succeeded) =
        extract_transaction_costs(&value);
    let token_balance_deltas = extract_token_balance_deltas(&value)?;

    Ok(TransactionEventSnapshot {
        signature: signature.to_string(),
        slot: confirmed.slot,
        block_time: confirmed.block_time,
        network_fee_lamports,
        compute_units_consumed,
        succeeded,
        token_balance_deltas,
        add_requests,
        rebalance_requests,
        events,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{AddLiquidityEvent, DecodedDlmmEvent};

    #[test]
    fn extracts_only_meteora_event_cpi_from_json_metadata() {
        let lb_pair = anchor_client::solana_sdk::pubkey::Pubkey::new_unique();
        let from = anchor_client::solana_sdk::pubkey::Pubkey::new_unique();
        let position = anchor_client::solana_sdk::pubkey::Pubkey::new_unique();

        let mut bytes = Vec::new();
        bytes.extend_from_slice(&0x1d9acb512ea545e4u64.to_le_bytes());
        bytes.extend_from_slice(&[31, 94, 125, 90, 227, 52, 61, 186]);
        bytes.extend_from_slice(lb_pair.as_ref());
        bytes.extend_from_slice(from.as_ref());
        bytes.extend_from_slice(position.as_ref());
        bytes.extend_from_slice(&10u64.to_le_bytes());
        bytes.extend_from_slice(&20u64.to_le_bytes());
        bytes.extend_from_slice(&5i32.to_le_bytes());

        let value = serde_json::json!({
            "transaction": {
                "meta": {
                    "innerInstructions": [
                        {
                            "index": 2,
                            "instructions": [
                                {
                                    "programId": commons::dlmm::ID.to_string(),
                                    "data": bs58::encode(bytes).into_string()
                                },
                                {
                                    "programId": anchor_client::solana_sdk::system_program::ID.to_string(),
                                    "data": "111"
                                }
                            ]
                        }
                    ]
                }
            }
        });

        let events = decode_inner_events(&value).unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].event_index, 0);
        assert_eq!(events[0].parent_ix_index, 2);
        assert_eq!(
            events[0].event,
            DecodedDlmmEvent::AddLiquidity(AddLiquidityEvent {
                lb_pair: lb_pair.to_string(),
                from: from.to_string(),
                position: position.to_string(),
                amount_x: "10".to_string(),
                amount_y: "20".to_string(),
                active_bin_id: 5,
            })
        );
    }

    #[test]
    fn decodes_strategy_add_request_amounts_and_active_guard() {
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&ADD_BY_STRATEGY_IX);
        bytes.extend_from_slice(&100u64.to_le_bytes());
        bytes.extend_from_slice(&200u64.to_le_bytes());
        bytes.extend_from_slice(&12i32.to_le_bytes());
        bytes.extend_from_slice(&3i32.to_le_bytes());
        bytes.extend_from_slice(&0i32.to_le_bytes());
        bytes.extend_from_slice(&0i32.to_le_bytes());
        bytes.push(0u8);
        bytes.extend_from_slice(&[0u8; 64]);

        let request = decode_add_request(&bytes, 7).expect("request");
        assert_eq!(request.instruction_index, 7);
        assert_eq!(request.instruction_type, "add_liquidity_by_strategy");
        assert_eq!(request.requested_amount_x, "100");
        assert_eq!(request.requested_amount_y, "200");
        assert_eq!(request.observed_active_id, Some(12));
        assert_eq!(request.max_active_bin_slippage, Some(3));
        assert_eq!(request.min_bin_id, Some(0));
        assert_eq!(request.max_bin_id, Some(0));
        assert_eq!(request.strategy_variant, Some(0));
        assert_eq!(request.strategy_favor_x, Some(false));
    }

    #[test]
    fn extracts_add_requests_from_outer_instructions() {
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&ADD_LIQUIDITY_IX);
        bytes.extend_from_slice(&10u64.to_le_bytes());
        bytes.extend_from_slice(&20u64.to_le_bytes());
        bytes.extend_from_slice(&0u32.to_le_bytes());

        let value = serde_json::json!({
            "transaction": {
                "transaction": {
                    "message": {
                        "instructions": [
                            {
                                "programId": commons::dlmm::ID.to_string(),
                                "data": bs58::encode(bytes).into_string()
                            }
                        ]
                    }
                }
            }
        });
        let requests = decode_add_requests(&value);
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].requested_amount_x, "10");
        assert_eq!(requests[0].requested_amount_y, "20");
        assert_eq!(requests[0].observed_active_id, None);
        assert!(requests[0].explicit_distribution.is_empty());
    }

    #[test]
    fn decodes_rebalance_execution_bounds() {
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&REBALANCE_LIQUIDITY_IX);
        bytes.extend_from_slice(&12i32.to_le_bytes());
        bytes.extend_from_slice(&3u16.to_le_bytes());
        bytes.push(1);
        bytes.push(0);
        bytes.extend_from_slice(&100u64.to_le_bytes());
        bytes.extend_from_slice(&90u64.to_le_bytes());
        bytes.extend_from_slice(&200u64.to_le_bytes());
        bytes.extend_from_slice(&180u64.to_le_bytes());
        bytes.push(2);
        bytes.extend_from_slice(&[0u8; 31]);

        let request = decode_rebalance_request(&bytes, 4).expect("request");
        assert_eq!(request.instruction_index, 4);
        assert_eq!(request.observed_active_id, 12);
        assert_eq!(request.max_active_bin_slippage, 3);
        assert!(request.should_claim_fee);
        assert!(!request.should_claim_reward);
        assert_eq!(request.min_withdraw_x_amount, "100");
        assert_eq!(request.max_deposit_x_amount, "90");
        assert_eq!(request.min_withdraw_y_amount, "200");
        assert_eq!(request.max_deposit_y_amount, "180");
        assert_eq!(request.shrink_mode, 2);
    }

    #[test]
    fn extracts_changed_token_balances_in_atomic_units() {
        let owner = anchor_client::solana_sdk::pubkey::Pubkey::new_unique();
        let mint = anchor_client::solana_sdk::pubkey::Pubkey::new_unique();
        let token_account =
            anchor_client::solana_sdk::pubkey::Pubkey::new_unique();
        let value = serde_json::json!({
            "transaction": {
                "transaction": {
                    "message": {
                        "accountKeys": [
                            {"pubkey": "payer"},
                            {"pubkey": token_account.to_string()}
                        ]
                    }
                },
                "meta": {
                    "preTokenBalances": [
                        {
                            "accountIndex": 1,
                            "mint": mint.to_string(),
                            "owner": owner.to_string(),
                            "uiTokenAmount": {
                                "amount": "100",
                                "decimals": 6
                            }
                        }
                    ],
                    "postTokenBalances": [
                        {
                            "accountIndex": 1,
                            "mint": mint.to_string(),
                            "owner": owner.to_string(),
                            "uiTokenAmount": {
                                "amount": "135",
                                "decimals": 6
                            }
                        }
                    ]
                }
            }
        });

        let deltas = extract_token_balance_deltas(&value).unwrap();
        assert_eq!(deltas.len(), 1);
        assert_eq!(deltas[0].account_index, 1);
        let token_account_string = token_account.to_string();
        let owner_string = owner.to_string();
        assert_eq!(
            deltas[0].account_address.as_deref(),
            Some(token_account_string.as_str())
        );
        assert_eq!(deltas[0].mint, mint.to_string());
        assert_eq!(
            deltas[0].pre_owner.as_deref(),
            Some(owner_string.as_str())
        );
        assert_eq!(
            deltas[0].post_owner.as_deref(),
            Some(owner_string.as_str())
        );
        assert_eq!(deltas[0].pre_amount, "100");
        assert_eq!(deltas[0].post_amount, "135");
        assert_eq!(deltas[0].delta_amount, "35");
        assert_eq!(deltas[0].decimals, Some(6));
    }

    #[test]
    fn token_balance_delta_handles_created_and_closed_accounts() {
        let mint = anchor_client::solana_sdk::pubkey::Pubkey::new_unique();
        let owner = anchor_client::solana_sdk::pubkey::Pubkey::new_unique();
        let value = serde_json::json!({
            "transaction": {
                "meta": {
                    "preTokenBalances": [
                        {
                            "accountIndex": 2,
                            "mint": mint.to_string(),
                            "owner": owner.to_string(),
                            "uiTokenAmount": {
                                "amount": "9",
                                "decimals": 0
                            }
                        }
                    ],
                    "postTokenBalances": [
                        {
                            "accountIndex": 3,
                            "mint": mint.to_string(),
                            "owner": owner.to_string(),
                            "uiTokenAmount": {
                                "amount": "4",
                                "decimals": 0
                            }
                        }
                    ]
                }
            }
        });

        let deltas = extract_token_balance_deltas(&value).unwrap();
        assert_eq!(deltas.len(), 2);
        assert_eq!(deltas[0].account_index, 2);
        assert_eq!(deltas[0].delta_amount, "-9");
        assert_eq!(deltas[1].account_index, 3);
        assert_eq!(deltas[1].delta_amount, "4");
    }

    #[test]
    fn token_balance_delta_ignores_unchanged_balances() {
        let value = serde_json::json!({
            "transaction": {
                "meta": {
                    "pre_token_balances": [
                        {
                            "account_index": 1,
                            "mint": "mint",
                            "ui_token_amount": {
                                "amount": "5",
                                "decimals": 0
                            }
                        }
                    ],
                    "post_token_balances": [
                        {
                            "account_index": 1,
                            "mint": "mint",
                            "ui_token_amount": {
                                "amount": "5",
                                "decimals": 0
                            }
                        }
                    ]
                }
            }
        });

        assert!(extract_token_balance_deltas(&value).unwrap().is_empty());
    }

    #[test]
    fn extracts_transaction_fee_and_compute_units() {
        let value = serde_json::json!({
            "transaction": {
                "meta": {
                    "fee": 12345,
                    "computeUnitsConsumed": 67890,
                    "err": null
                }
            }
        });
        assert_eq!(
            extract_transaction_costs(&value),
            (Some(12345), Some(67890), Some(true))
        );
    }

    #[test]
    fn missing_inner_instructions_is_empty_not_error() {
        let value = serde_json::json!({"transaction": {"meta": {}}});
        assert!(decode_inner_events(&value).unwrap().is_empty());
    }
}
