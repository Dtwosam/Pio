use anyhow::{Context, Result};
use commons::dlmm::accounts::LbPair;
use commons::dlmm::types::RemainingAccountsInfo;
use commons::{
    get_potential_token_2022_related_ix_data_and_accounts, ActionType,
};
use solana_sdk::account::Account;
use solana_sdk::instruction::AccountMeta;
use solana_sdk::pubkey::Pubkey;
use std::str::FromStr;


pub const SPL_TOKEN_PROGRAM: &str =
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
pub const TOKEN_2022_PROGRAM: &str =
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";


pub struct RemainingAccountsResolution {
    pub info: RemainingAccountsInfo,
    pub accounts: Vec<AccountMeta>,
}


pub fn parse_supported_token_program(value: &Pubkey) -> Result<Pubkey> {
    let spl = Pubkey::from_str(SPL_TOKEN_PROGRAM)
        .context("hard-coded SPL token program id is invalid")?;
    let token_2022 = Pubkey::from_str(TOKEN_2022_PROGRAM)
        .context("hard-coded Token-2022 program id is invalid")?;
    if *value != spl && *value != token_2022 {
        anyhow::bail!("unsupported token program: {value}");
    }
    Ok(*value)
}


pub fn validate_token_account_base(
    name: &str,
    account: &Account,
    expected_mint: &Pubkey,
    expected_owner: &Pubkey,
    token_program: &Pubkey,
) -> Result<u64> {
    parse_supported_token_program(token_program)?;
    if account.owner != *token_program {
        anyhow::bail!("{name} is not owned by its expected token program");
    }
    if account.data.len() < 72 {
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
        anyhow::bail!("{name} token-account owner does not match expected owner");
    }
    Ok(u64::from_le_bytes(
        account.data[64..72]
            .try_into()
            .context("invalid token-account amount bytes")?,
    ))
}


pub fn validate_token_vault_base(
    name: &str,
    account: &Account,
    expected_mint: &Pubkey,
    token_program: &Pubkey,
) -> Result<()> {
    parse_supported_token_program(token_program)?;
    if account.owner != *token_program {
        anyhow::bail!("{name} is not owned by its expected token program");
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


pub fn validate_mint_owner(
    name: &str,
    account: &Account,
    token_program: &Pubkey,
) -> Result<()> {
    parse_supported_token_program(token_program)?;
    if account.owner != *token_program {
        anyhow::bail!("{name} is not owned by its expected token program");
    }
    Ok(())
}


async fn resolve(
    rpc_url: &str,
    lb_pair: &LbPair,
    action: ActionType,
) -> Result<RemainingAccountsResolution> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let rpc =
        anchor_client::solana_client::nonblocking::rpc_client::RpcClient::new(
            rpc_url.to_string(),
        );
    let resolved =
        get_potential_token_2022_related_ix_data_and_accounts(
            lb_pair,
            rpc,
            action,
        )
        .await?;

    Ok(match resolved {
        Some((slices, accounts)) => RemainingAccountsResolution {
            info: RemainingAccountsInfo { slices },
            accounts,
        },
        None => RemainingAccountsResolution {
            info: RemainingAccountsInfo { slices: vec![] },
            accounts: vec![],
        },
    })
}


pub async fn resolve_liquidity_remaining_accounts(
    rpc_url: &str,
    lb_pair: &LbPair,
) -> Result<RemainingAccountsResolution> {
    resolve(rpc_url, lb_pair, ActionType::Liquidity).await
}


pub async fn resolve_reward_remaining_accounts(
    rpc_url: &str,
    lb_pair: &LbPair,
    reward_index: usize,
) -> Result<RemainingAccountsResolution> {
    if reward_index > 1 {
        anyhow::bail!("reward_index must be 0 or 1");
    }
    resolve(rpc_url, lb_pair, ActionType::Reward(reward_index)).await
}


#[cfg(test)]
mod tests {
    use super::*;

    fn token_account(
        program: Pubkey,
        mint: Pubkey,
        owner: Pubkey,
        amount: u64,
    ) -> Account {
        let mut data = vec![0_u8; 165];
        data[0..32].copy_from_slice(mint.as_ref());
        data[32..64].copy_from_slice(owner.as_ref());
        data[64..72].copy_from_slice(&amount.to_le_bytes());
        Account {
            lamports: 1,
            data,
            owner: program,
            executable: false,
            rent_epoch: 0,
        }
    }

    #[test]
    fn accepts_standard_and_token_2022_programs() {
        let spl = Pubkey::from_str(SPL_TOKEN_PROGRAM).unwrap();
        let token_2022 = Pubkey::from_str(TOKEN_2022_PROGRAM).unwrap();

        assert_eq!(parse_supported_token_program(&spl).unwrap(), spl);
        assert_eq!(
            parse_supported_token_program(&token_2022).unwrap(),
            token_2022
        );
        assert!(
            parse_supported_token_program(&Pubkey::new_unique()).is_err()
        );
    }

    #[test]
    fn validates_token_2022_base_account_layout() {
        let program = Pubkey::from_str(TOKEN_2022_PROGRAM).unwrap();
        let mint = Pubkey::new_unique();
        let owner = Pubkey::new_unique();
        let account = token_account(program, mint, owner, 123);

        assert_eq!(
            validate_token_account_base(
                "account",
                &account,
                &mint,
                &owner,
                &program,
            )
            .unwrap(),
            123
        );
    }

    #[test]
    fn token_account_wrong_program_fails_closed() {
        let spl = Pubkey::from_str(SPL_TOKEN_PROGRAM).unwrap();
        let token_2022 = Pubkey::from_str(TOKEN_2022_PROGRAM).unwrap();
        let mint = Pubkey::new_unique();
        let owner = Pubkey::new_unique();
        let account = token_account(token_2022, mint, owner, 1);

        assert!(
            validate_token_account_base(
                "account",
                &account,
                &mint,
                &owner,
                &spl,
            )
            .is_err()
        );
    }
}
