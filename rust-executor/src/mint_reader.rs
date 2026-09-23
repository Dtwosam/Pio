use anyhow::{Context, Result};
use serde::Serialize;
use solana_client::nonblocking::rpc_client::RpcClient;
use solana_sdk::account::Account;
use solana_sdk::pubkey::Pubkey;
use std::str::FromStr;

use crate::token_extensions::{
    parse_supported_token_program, SPL_TOKEN_PROGRAM, TOKEN_2022_PROGRAM,
};

const MINT_BASE_LEN: usize = 82;
const TOKEN_2022_ACCOUNT_TYPE_LEN: usize = 1;

#[derive(Debug, Clone, Serialize)]
pub struct MintChainSnapshot {
    pub mint_address: String,
    pub token_program: String,
    pub capture_slot_start: u64,
    pub capture_slot_end: u64,
    pub supply: String,
    pub decimals: u8,
    pub is_initialized: bool,
    pub mint_authority: Option<String>,
    pub freeze_authority: Option<String>,
    pub data_len: usize,
    pub token_2022_extension_data_len: usize,
    pub has_token_2022_extension_data: bool,
}

fn parse_coption_pubkey(
    data: &[u8],
    tag_offset: usize,
    key_offset: usize,
    field: &str,
) -> Result<Option<Pubkey>> {
    let tag = u32::from_le_bytes(
        data.get(tag_offset..tag_offset + 4)
            .with_context(|| format!("{field} option tag is truncated"))?
            .try_into()
            .context("invalid option tag bytes")?,
    );
    match tag {
        0 => Ok(None),
        1 => {
            let bytes: [u8; 32] = data
                .get(key_offset..key_offset + 32)
                .with_context(|| format!("{field} pubkey is truncated"))?
                .try_into()
                .context("invalid pubkey bytes")?;
            Ok(Some(Pubkey::new_from_array(bytes)))
        }
        other => anyhow::bail!(
            "{field} option tag must be 0 or 1, got {other}"
        ),
    }
}

pub fn decode_mint_account(
    mint: &Pubkey,
    account: &Account,
) -> Result<MintChainSnapshot> {
    parse_supported_token_program(&account.owner)?;
    if account.data.len() < MINT_BASE_LEN {
        anyhow::bail!(
            "mint account data is too short: {} < {MINT_BASE_LEN}",
            account.data.len()
        );
    }

    let mint_authority = parse_coption_pubkey(
        &account.data,
        0,
        4,
        "mint_authority",
    )?;
    let supply = u64::from_le_bytes(
        account.data[36..44]
            .try_into()
            .context("invalid mint supply bytes")?,
    );
    let decimals = account.data[44];
    let is_initialized = match account.data[45] {
        0 => false,
        1 => true,
        other => anyhow::bail!(
            "mint initialized flag must be 0 or 1, got {other}"
        ),
    };
    let freeze_authority = parse_coption_pubkey(
        &account.data,
        46,
        50,
        "freeze_authority",
    )?;

    let token_2022 = account.owner
        == Pubkey::from_str(TOKEN_2022_PROGRAM)
            .context("hard-coded Token-2022 program id is invalid")?;
    let extension_data_len = if token_2022 {
        account
            .data
            .len()
            .saturating_sub(MINT_BASE_LEN + TOKEN_2022_ACCOUNT_TYPE_LEN)
    } else {
        0
    };

    Ok(MintChainSnapshot {
        mint_address: mint.to_string(),
        token_program: account.owner.to_string(),
        capture_slot_start: 0,
        capture_slot_end: 0,
        supply: supply.to_string(),
        decimals,
        is_initialized,
        mint_authority: mint_authority.map(|value| value.to_string()),
        freeze_authority: freeze_authority.map(|value| value.to_string()),
        data_len: account.data.len(),
        token_2022_extension_data_len: extension_data_len,
        has_token_2022_extension_data: extension_data_len > 0,
    })
}

pub async fn inspect_mint(
    rpc_url: &str,
    mint_address: &str,
) -> Result<MintChainSnapshot> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let mint = Pubkey::from_str(mint_address.trim())
        .context("invalid mint address")?;
    let rpc = RpcClient::new(rpc_url.to_string());
    let capture_slot_start = rpc
        .get_slot()
        .await
        .context("failed to get mint snapshot start slot")?;
    let account = rpc
        .get_account(&mint)
        .await
        .with_context(|| format!("failed to fetch mint account {mint}"))?;
    let capture_slot_end = rpc
        .get_slot()
        .await
        .context("failed to get mint snapshot end slot")?;

    let mut snapshot = decode_mint_account(&mint, &account)?;
    snapshot.capture_slot_start = capture_slot_start;
    snapshot.capture_slot_end = capture_slot_end;
    Ok(snapshot)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mint_account(
        program: Pubkey,
        mint_authority: Option<Pubkey>,
        freeze_authority: Option<Pubkey>,
        supply: u64,
        decimals: u8,
        extension_bytes: usize,
    ) -> Account {
        let is_token_2022 =
            program == Pubkey::from_str(TOKEN_2022_PROGRAM).unwrap();
        let mut data = vec![
            0_u8;
            MINT_BASE_LEN
                + if is_token_2022 {
                    TOKEN_2022_ACCOUNT_TYPE_LEN + extension_bytes
                } else {
                    0
                }
        ];
        if let Some(authority) = mint_authority {
            data[0..4].copy_from_slice(&1_u32.to_le_bytes());
            data[4..36].copy_from_slice(authority.as_ref());
        }
        data[36..44].copy_from_slice(&supply.to_le_bytes());
        data[44] = decimals;
        data[45] = 1;
        if let Some(authority) = freeze_authority {
            data[46..50].copy_from_slice(&1_u32.to_le_bytes());
            data[50..82].copy_from_slice(authority.as_ref());
        }
        Account {
            lamports: 1,
            data,
            owner: program,
            executable: false,
            rent_epoch: 0,
        }
    }

    #[test]
    fn standard_spl_mint_decodes_authorities() {
        let program = Pubkey::from_str(SPL_TOKEN_PROGRAM).unwrap();
        let mint = Pubkey::new_unique();
        let mint_authority = Pubkey::new_unique();
        let account = mint_account(
            program,
            Some(mint_authority),
            None,
            1_000_000,
            6,
            0,
        );

        let snapshot = decode_mint_account(&mint, &account).unwrap();

        assert_eq!(snapshot.supply, "1000000");
        assert_eq!(snapshot.decimals, 6);
        assert!(snapshot.is_initialized);
        assert_eq!(
            snapshot.mint_authority.as_deref(),
            Some(mint_authority.to_string().as_str())
        );
        assert!(snapshot.freeze_authority.is_none());
        assert_eq!(snapshot.token_2022_extension_data_len, 0);
    }

    #[test]
    fn token_2022_extension_region_is_reported() {
        let program = Pubkey::from_str(TOKEN_2022_PROGRAM).unwrap();
        let mint = Pubkey::new_unique();
        let account = mint_account(
            program,
            None,
            None,
            42,
            9,
            24,
        );

        let snapshot = decode_mint_account(&mint, &account).unwrap();

        assert_eq!(snapshot.token_program, TOKEN_2022_PROGRAM);
        assert_eq!(snapshot.token_2022_extension_data_len, 24);
        assert!(snapshot.has_token_2022_extension_data);
        assert!(snapshot.mint_authority.is_none());
        assert!(snapshot.freeze_authority.is_none());
    }

    #[test]
    fn unsupported_owner_fails_closed() {
        let mint = Pubkey::new_unique();
        let account = mint_account(
            Pubkey::new_unique(),
            None,
            None,
            1,
            6,
            0,
        );

        assert!(decode_mint_account(&mint, &account).is_err());
    }

    #[test]
    fn invalid_coption_tag_fails_closed() {
        let program = Pubkey::from_str(SPL_TOKEN_PROGRAM).unwrap();
        let mint = Pubkey::new_unique();
        let mut account = mint_account(
            program,
            None,
            None,
            1,
            6,
            0,
        );
        account.data[0..4].copy_from_slice(&2_u32.to_le_bytes());

        assert!(decode_mint_account(&mint, &account).is_err());
    }
}
