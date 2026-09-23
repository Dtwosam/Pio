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

#[derive(Debug, Serialize)]
pub struct TransactionEventSnapshot {
    pub signature: String,
    pub slot: u64,
    pub block_time: Option<i64>,
    pub events: Vec<TransactionEventRecord>,
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

    Ok(TransactionEventSnapshot {
        signature: signature.to_string(),
        slot: confirmed.slot,
        block_time: confirmed.block_time,
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
    fn missing_inner_instructions_is_empty_not_error() {
        let value = serde_json::json!({"transaction": {"meta": {}}});
        assert!(decode_inner_events(&value).unwrap().is_empty());
    }
}
