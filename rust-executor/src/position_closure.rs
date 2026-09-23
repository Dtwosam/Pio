use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use solana_client::rpc_client::RpcClient;
use solana_sdk::commitment_config::CommitmentConfig;
use solana_sdk::pubkey::Pubkey;
use std::str::FromStr;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PositionClosureProof {
    pub position: String,
    pub closed: bool,
    pub rpc_context_slot: u64,
}

pub fn verify_position_closed(
    rpc_url: &str,
    position_address: &str,
) -> Result<PositionClosureProof> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let position = Pubkey::from_str(position_address.trim())
        .context("POSITION_ADDRESS is not a valid Solana pubkey")?;
    let client = RpcClient::new_with_commitment(
        rpc_url.to_string(),
        CommitmentConfig::confirmed(),
    );
    let response = client
        .get_account_with_commitment(
            &position,
            CommitmentConfig::confirmed(),
        )
        .context("failed to fetch position closure state")?;

    Ok(PositionClosureProof {
        position: position.to_string(),
        closed: response.value.is_none(),
        rpc_context_slot: response.context.slot,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_position_fails_before_rpc() {
        assert!(verify_position_closed(
            "http://127.0.0.1:8899",
            "not-a-pubkey",
        )
        .is_err());
    }

    #[test]
    fn empty_rpc_url_fails_closed() {
        assert!(verify_position_closed(
            "",
            &Pubkey::new_unique().to_string(),
        )
        .is_err());
    }
}
