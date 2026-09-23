use std::str::FromStr;

use anchor_client::solana_client::nonblocking::rpc_client::RpcClient;
use anchor_client::solana_client::rpc_config::{RpcTransactionConfig, UiTransactionEncoding};
use anchor_client::solana_sdk::commitment_config::CommitmentConfig;
use anchor_client::solana_sdk::pubkey::Pubkey;
use anchor_client::solana_sdk::signature::Signature;
use anyhow::{Context, Result};
use serde::Serialize;
use serde_json::Value;

const ANCHOR_EVENT_TAG: [u8; 8] = [0xe4, 0x45, 0xa5, 0x2e, 0x51, 0xcb, 0x9a, 0x1d];
const ADD_LIQUIDITY_DISCRIMINATOR: [u8; 8] = [31, 94, 125, 90, 227, 52, 61, 186];
const COMPOSITION_FEE_DISCRIMINATOR: [u8; 8] = [128, 151, 123, 106, 17, 102, 113, 142];

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "event_type", rename_all = "snake_case")]
pub enum MeteoraTransactionEvent {
    AddLiquidity {
        parent_ix_index: u64,
        lb_pair: String,
        from_address: String,
        position: String,
        amount_x: String,
        amount_y: String,
        active_bin_id: i32,
    },
    CompositionFee {
        parent_ix_index: u64,
        from_address: String,
        bin_id: i16,
        token_x_fee_amount: String,
        token_y_fee_amount: String,
        protocol_token_x_fee_amount: String,
        protocol_token_y_fee_amount: String,
    },
}

#[derive(Debug, Serialize)]
pub struct TransactionEventSnapshot {
    pub signature: String,
    pub slot: u64,
    pub block_time: Option<i64>,
    pub events: Vec<MeteoraTransactionEvent>,
}

fn take<const N: usize>(data: &[u8], offset: &mut usize) -> Option<[u8; N]> {
    let end = offset.checked_add(N)?;
    let bytes: [u8; N] = data.get(*offset..end)?.try_into().ok()?;
    *offset = end;
    Some(bytes)
}

fn read_pubkey(data: &[u8], offset: &mut usize) -> Option<Pubkey> {
    Some(Pubkey::new_from_array(take::<32>(data, offset)?))
}

fn read_u64(data: &[u8], offset: &mut usize) -> Option<u64> {
    Some(u64::from_le_bytes(take::<8>(data, offset)?))
}

fn read_i32(data: &[u8], offset: &mut usize) -> Option<i32> {
    Some(i32::from_le_bytes(take::<4>(data, offset)?))
}

fn read_i16(data: &[u8], offset: &mut usize) -> Option<i16> {
    Some(i16::from_le_bytes(take::<2>(data, offset)?))
}

pub fn decode_event_instruction_data(
    data: &[u8],
    parent_ix_index: u64,
) -> Option<MeteoraTransactionEvent> {
    if data.len() < 16 || data[..8] != ANCHOR_EVENT_TAG {
        return None;
    }
    let discriminator: [u8; 8] = data[8..16].try_into().ok()?;
    let body = &data[16..];
    let mut offset = 0usize;

    if discriminator == ADD_LIQUIDITY_DISCRIMINATOR {
        let lb_pair = read_pubkey(body, &mut offset)?;
        let from_address = read_pubkey(body, &mut offset)?;
        let position = read_pubkey(body, &mut offset)?;
        let amount_x = read_u64(body, &mut offset)?;
        let amount_y = read_u64(body, &mut offset)?;
        let active_bin_id = read_i32(body, &mut offset)?;
        return Some(MeteoraTransactionEvent::AddLiquidity {
            parent_ix_index,
            lb_pair: lb_pair.to_string(),
            from_address: from_address.to_string(),
            position: position.to_string(),
            amount_x: amount_x.to_string(),
            amount_y: amount_y.to_string(),
            active_bin_id,
        });
    }

    if discriminator == COMPOSITION_FEE_DISCRIMINATOR {
        let from_address = read_pubkey(body, &mut offset)?;
        let bin_id = read_i16(body, &mut offset)?;
        let token_x_fee_amount = read_u64(body, &mut offset)?;
        let token_y_fee_amount = read_u64(body, &mut offset)?;
        let protocol_token_x_fee_amount = read_u64(body, &mut offset)?;
        let protocol_token_y_fee_amount = read_u64(body, &mut offset)?;
        return Some(MeteoraTransactionEvent::CompositionFee {
            parent_ix_index,
            from_address: from_address.to_string(),
            bin_id,
            token_x_fee_amount: token_x_fee_amount.to_string(),
            token_y_fee_amount: token_y_fee_amount.to_string(),
            protocol_token_x_fee_amount: protocol_token_x_fee_amount.to_string(),
            protocol_token_y_fee_amount: protocol_token_y_fee_amount.to_string(),
        });
    }

    None
}

fn decode_inner_events(value: &Value) -> Result<Vec<MeteoraTransactionEvent>> {
    let mut out = Vec::new();
    let groups = value
        .pointer("/transaction/meta/innerInstructions")
        .and_then(Value::as_array)
        .context("transaction metadata has no innerInstructions array")?;

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
            if let Some(event) = decode_event_instruction_data(&data, parent_ix_index) {
                out.push(event);
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

    fn event_bytes(discriminator: [u8; 8], body: Vec<u8>) -> Vec<u8> {
        let mut out = Vec::new();
        out.extend_from_slice(&ANCHOR_EVENT_TAG);
        out.extend_from_slice(&discriminator);
        out.extend_from_slice(&body);
        out
    }

    #[test]
    fn decodes_add_liquidity_event() {
        let lb_pair = Pubkey::new_unique();
        let from_address = Pubkey::new_unique();
        let position = Pubkey::new_unique();

        let mut body = Vec::new();
        body.extend_from_slice(lb_pair.as_ref());
        body.extend_from_slice(from_address.as_ref());
        body.extend_from_slice(position.as_ref());
        body.extend_from_slice(&10u64.to_le_bytes());
        body.extend_from_slice(&20u64.to_le_bytes());
        body.extend_from_slice(&(-7i32).to_le_bytes());

        let event = decode_event_instruction_data(
            &event_bytes(ADD_LIQUIDITY_DISCRIMINATOR, body),
            3,
        )
        .expect("event");

        assert_eq!(
            event,
            MeteoraTransactionEvent::AddLiquidity {
                parent_ix_index: 3,
                lb_pair: lb_pair.to_string(),
                from_address: from_address.to_string(),
                position: position.to_string(),
                amount_x: "10".to_string(),
                amount_y: "20".to_string(),
                active_bin_id: -7,
            }
        );
    }

    #[test]
    fn decodes_composition_fee_event() {
        let from_address = Pubkey::new_unique();

        let mut body = Vec::new();
        body.extend_from_slice(from_address.as_ref());
        body.extend_from_slice(&12i16.to_le_bytes());
        body.extend_from_slice(&100u64.to_le_bytes());
        body.extend_from_slice(&200u64.to_le_bytes());
        body.extend_from_slice(&10u64.to_le_bytes());
        body.extend_from_slice(&20u64.to_le_bytes());

        let event = decode_event_instruction_data(
            &event_bytes(COMPOSITION_FEE_DISCRIMINATOR, body),
            5,
        )
        .expect("event");

        assert_eq!(
            event,
            MeteoraTransactionEvent::CompositionFee {
                parent_ix_index: 5,
                from_address: from_address.to_string(),
                bin_id: 12,
                token_x_fee_amount: "100".to_string(),
                token_y_fee_amount: "200".to_string(),
                protocol_token_x_fee_amount: "10".to_string(),
                protocol_token_y_fee_amount: "20".to_string(),
            }
        );
    }

    #[test]
    fn rejects_non_event_cpi_data() {
        let mut data = Vec::new();
        data.extend_from_slice(&[0u8; 8]);
        data.extend_from_slice(&ADD_LIQUIDITY_DISCRIMINATOR);
        data.extend_from_slice(&[0u8; 116]);
        assert!(decode_event_instruction_data(&data, 0).is_none());
    }

    #[test]
    fn rejects_truncated_event() {
        let data = event_bytes(COMPOSITION_FEE_DISCRIMINATOR, vec![0u8; 10]);
        assert!(decode_event_instruction_data(&data, 0).is_none());
    }
}
