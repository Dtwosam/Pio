use std::io::{self, Write};
use std::str::FromStr;

use anchor_client::solana_sdk::commitment_config::CommitmentConfig;
use anchor_client::solana_sdk::pubkey::Pubkey;
use anyhow::{Context, Result};
use serde::Serialize;
use solana_account_decoder_client_types::UiAccountEncoding;
use solana_client::pubsub_client::PubsubClient;
use solana_client::rpc_config::RpcAccountInfoConfig;


#[derive(Debug, Serialize)]
pub struct AccountChangeNotification {
    pub account_address: String,
    pub slot: u64,
}


pub fn derive_ws_url(rpc_url: &str) -> Result<String> {
    let value = rpc_url.trim();
    if value.is_empty() {
        anyhow::bail!("RPC URL is empty");
    }
    if let Some(rest) = value.strip_prefix("https://") {
        return Ok(format!("wss://{rest}"));
    }
    if let Some(rest) = value.strip_prefix("http://") {
        return Ok(format!("ws://{rest}"));
    }
    if value.starts_with("wss://") || value.starts_with("ws://") {
        return Ok(value.to_string());
    }
    anyhow::bail!("RPC URL must use http(s) or ws(s)");
}


pub fn watch_account_changes(
    ws_url: &str,
    account_address: &str,
    max_notifications: usize,
) -> Result<()> {
    let account = Pubkey::from_str(account_address)
        .context("invalid account address")?;
    let config = RpcAccountInfoConfig {
        encoding: Some(UiAccountEncoding::Base64),
        commitment: Some(CommitmentConfig::confirmed()),
        ..RpcAccountInfoConfig::default()
    };
    let (_subscription, receiver) = PubsubClient::account_subscribe(
        ws_url,
        &account,
        Some(config),
    )
    .context("failed to subscribe to account changes")?;

    let mut emitted = 0usize;
    loop {
        let response = receiver
            .recv()
            .context("account change subscription ended")?;
        let notification = AccountChangeNotification {
            account_address: account.to_string(),
            slot: response.context.slot,
        };
        println!("{}", serde_json::to_string(&notification)?);
        io::stdout().flush().context("failed to flush notification")?;

        emitted += 1;
        if max_notifications > 0 && emitted >= max_notifications {
            break;
        }
    }

    Ok(())
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derives_secure_websocket_url_and_preserves_query() {
        assert_eq!(
            derive_ws_url(
                "https://mainnet.helius-rpc.com/?api-key=secret"
            )
            .expect("ws url"),
            "wss://mainnet.helius-rpc.com/?api-key=secret"
        );
    }

    #[test]
    fn derives_plain_websocket_url_from_http() {
        assert_eq!(
            derive_ws_url("http://127.0.0.1:8899").expect("ws url"),
            "ws://127.0.0.1:8899"
        );
    }

    #[test]
    fn accepts_explicit_websocket_url() {
        assert_eq!(
            derive_ws_url("wss://example.invalid/rpc").expect("ws url"),
            "wss://example.invalid/rpc"
        );
    }

    #[test]
    fn rejects_unsupported_or_empty_urls() {
        assert!(derive_ws_url("").is_err());
        assert!(derive_ws_url("ftp://example.invalid").is_err());
    }
}
