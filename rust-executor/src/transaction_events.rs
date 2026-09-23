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
    pub explicit_distribution: Vec<ExplicitBinDistribution>,
    pub weighted_distribution: Vec<WeightedBinDistribution>,
}

#[derive(Debug, Serialize)]
pub struct TransactionEventSnapshot {
    pub signature: String,
    pub slot: u64,
    pub block_time: Option<i64>,
    pub network_fee_lamports: Option<u64>,
    pub compute_units_consumed: Option<u64>,
    pub succeeded: Option<bool>,
    pub add_requests: Vec<LiquidityAddRequest>,
    pub events: Vec<TransactionEventRecord>,
}

const ADD_LIQUIDITY_IX: [u8; 8] = [181, 157, 89, 67, 143, 182, 52, 72];
const ADD_LIQUIDITY2_IX: [u8; 8] = [228, 162, 78, 28, 70, 219, 116, 115];
const ADD_BY_STRATEGY_IX: [u8; 8] = [7, 3, 150, 127, 148, 40, 61, 200];
const ADD_BY_STRATEGY2_IX: [u8; 8] = [3, 221, 149, 218, 111, 141, 118, 213];
const ADD_BY_WEIGHT_IX: [u8; 8] = [28, 140, 238, 99, 231, 162, 21, 149];
const ADD_BY_WEIGHT2_IX: [u8; 8] = [209, 59, 63, 91, 111, 200, 153, 228];

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
        explicit_distribution,
        weighted_distribution,
    })
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
    let (network_fee_lamports, compute_units_consumed, succeeded) =
        extract_transaction_costs(&value);

    Ok(TransactionEventSnapshot {
        signature: signature.to_string(),
        slot: confirmed.slot,
        block_time: confirmed.block_time,
        network_fee_lamports,
        compute_units_consumed,
        succeeded,
        add_requests,
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
