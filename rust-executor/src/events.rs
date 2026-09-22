use anyhow::{bail, Context, Result};
use anchor_client::solana_sdk::pubkey::Pubkey;
use serde::Serialize;

const EVENT_IX_TAG_LE: [u8; 8] = 0x1d9acb512ea545e4u64.to_le_bytes();
const ADD_LIQUIDITY_DISCRIMINATOR: [u8; 8] = [31, 94, 125, 90, 227, 52, 61, 186];
const COMPOSITION_FEE_DISCRIMINATOR: [u8; 8] = [128, 151, 123, 106, 17, 102, 113, 142];

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AddLiquidityEvent {
    pub lb_pair: String,
    pub from: String,
    pub position: String,
    pub amount_x: String,
    pub amount_y: String,
    pub active_bin_id: i32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CompositionFeeEvent {
    pub from: String,
    pub bin_id: i16,
    pub token_x_fee_amount: String,
    pub token_y_fee_amount: String,
    pub protocol_token_x_fee_amount: String,
    pub protocol_token_y_fee_amount: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "event_type", content = "event")]
pub enum DecodedDlmmEvent {
    AddLiquidity(AddLiquidityEvent),
    CompositionFee(CompositionFeeEvent),
}

fn take<const N: usize>(data: &[u8], offset: &mut usize) -> Result<[u8; N]> {
    let end = offset
        .checked_add(N)
        .context("event byte offset overflow")?;
    let slice = data
        .get(*offset..end)
        .context("event payload is truncated")?;
    let bytes: [u8; N] = slice
        .try_into()
        .map_err(|_| anyhow::anyhow!("invalid fixed-size event field"))?;
    *offset = end;
    Ok(bytes)
}

fn read_pubkey(data: &[u8], offset: &mut usize) -> Result<Pubkey> {
    Ok(Pubkey::new_from_array(take::<32>(data, offset)?))
}

fn read_u64(data: &[u8], offset: &mut usize) -> Result<u64> {
    Ok(u64::from_le_bytes(take::<8>(data, offset)?))
}

fn read_i32(data: &[u8], offset: &mut usize) -> Result<i32> {
    Ok(i32::from_le_bytes(take::<4>(data, offset)?))
}

fn read_i16(data: &[u8], offset: &mut usize) -> Result<i16> {
    Ok(i16::from_le_bytes(take::<2>(data, offset)?))
}

fn decode_add_liquidity(payload: &[u8]) -> Result<DecodedDlmmEvent> {
    let mut offset = 0;
    let event = AddLiquidityEvent {
        lb_pair: read_pubkey(payload, &mut offset)?.to_string(),
        from: read_pubkey(payload, &mut offset)?.to_string(),
        position: read_pubkey(payload, &mut offset)?.to_string(),
        amount_x: read_u64(payload, &mut offset)?.to_string(),
        amount_y: read_u64(payload, &mut offset)?.to_string(),
        active_bin_id: read_i32(payload, &mut offset)?,
    };
    if offset != payload.len() {
        bail!("unexpected trailing bytes in AddLiquidity event");
    }
    Ok(DecodedDlmmEvent::AddLiquidity(event))
}

fn decode_composition_fee(payload: &[u8]) -> Result<DecodedDlmmEvent> {
    let mut offset = 0;
    let event = CompositionFeeEvent {
        from: read_pubkey(payload, &mut offset)?.to_string(),
        bin_id: read_i16(payload, &mut offset)?,
        token_x_fee_amount: read_u64(payload, &mut offset)?.to_string(),
        token_y_fee_amount: read_u64(payload, &mut offset)?.to_string(),
        protocol_token_x_fee_amount: read_u64(payload, &mut offset)?.to_string(),
        protocol_token_y_fee_amount: read_u64(payload, &mut offset)?.to_string(),
    };
    if offset != payload.len() {
        bail!("unexpected trailing bytes in CompositionFee event");
    }
    Ok(DecodedDlmmEvent::CompositionFee(event))
}

pub fn decode_event_cpi_data(data: &[u8]) -> Result<Option<DecodedDlmmEvent>> {
    if data.len() < 16 {
        return Ok(None);
    }
    if data[..8] != EVENT_IX_TAG_LE {
        return Ok(None);
    }

    let discriminator: [u8; 8] = data[8..16]
        .try_into()
        .map_err(|_| anyhow::anyhow!("event discriminator is truncated"))?;
    let payload = &data[16..];

    if discriminator == ADD_LIQUIDITY_DISCRIMINATOR {
        return decode_add_liquidity(payload).map(Some);
    }
    if discriminator == COMPOSITION_FEE_DISCRIMINATOR {
        return decode_composition_fee(payload).map(Some);
    }

    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pk(byte: u8) -> Pubkey {
        Pubkey::new_from_array([byte; 32])
    }

    fn envelope(discriminator: [u8; 8], payload: Vec<u8>) -> Vec<u8> {
        let mut data = Vec::new();
        data.extend_from_slice(&EVENT_IX_TAG_LE);
        data.extend_from_slice(&discriminator);
        data.extend_from_slice(&payload);
        data
    }

    #[test]
    fn decodes_add_liquidity_event_cpi_bytes() {
        let mut payload = Vec::new();
        payload.extend_from_slice(pk(1).as_ref());
        payload.extend_from_slice(pk(2).as_ref());
        payload.extend_from_slice(pk(3).as_ref());
        payload.extend_from_slice(&10u64.to_le_bytes());
        payload.extend_from_slice(&20u64.to_le_bytes());
        payload.extend_from_slice(&(-7i32).to_le_bytes());

        let decoded = decode_event_cpi_data(&envelope(ADD_LIQUIDITY_DISCRIMINATOR, payload))
            .unwrap()
            .unwrap();

        assert_eq!(
            decoded,
            DecodedDlmmEvent::AddLiquidity(AddLiquidityEvent {
                lb_pair: pk(1).to_string(),
                from: pk(2).to_string(),
                position: pk(3).to_string(),
                amount_x: "10".to_string(),
                amount_y: "20".to_string(),
                active_bin_id: -7,
            })
        );
    }

    #[test]
    fn decodes_composition_fee_event_cpi_bytes() {
        let mut payload = Vec::new();
        payload.extend_from_slice(pk(4).as_ref());
        payload.extend_from_slice(&12i16.to_le_bytes());
        payload.extend_from_slice(&100u64.to_le_bytes());
        payload.extend_from_slice(&200u64.to_le_bytes());
        payload.extend_from_slice(&10u64.to_le_bytes());
        payload.extend_from_slice(&20u64.to_le_bytes());

        let decoded = decode_event_cpi_data(&envelope(COMPOSITION_FEE_DISCRIMINATOR, payload))
            .unwrap()
            .unwrap();

        assert_eq!(
            decoded,
            DecodedDlmmEvent::CompositionFee(CompositionFeeEvent {
                from: pk(4).to_string(),
                bin_id: 12,
                token_x_fee_amount: "100".to_string(),
                token_y_fee_amount: "200".to_string(),
                protocol_token_x_fee_amount: "10".to_string(),
                protocol_token_y_fee_amount: "20".to_string(),
            })
        );
    }

    #[test]
    fn ignores_non_event_and_unknown_event_bytes() {
        assert!(decode_event_cpi_data(&[1, 2, 3]).unwrap().is_none());

        let data = envelope([9; 8], Vec::new());
        assert!(decode_event_cpi_data(&data).unwrap().is_none());
    }

    #[test]
    fn rejects_truncated_known_event() {
        let data = envelope(ADD_LIQUIDITY_DISCRIMINATOR, vec![0; 10]);
        assert!(decode_event_cpi_data(&data).is_err());
    }
}
