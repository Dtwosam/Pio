use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use solana_client::rpc_client::RpcClient;
use solana_client::rpc_config::RpcSimulateTransactionConfig;
use solana_sdk::commitment_config::CommitmentConfig;
use solana_sdk::transaction::VersionedTransaction;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SimulationReport {
    pub succeeded: bool,
    pub rpc_context_slot: u64,
    pub result: Value,
}

pub fn decode_transaction_base64(encoded: &str) -> Result<VersionedTransaction> {
    if encoded.trim().is_empty() {
        anyhow::bail!("serialized transaction is required");
    }
    let bytes = general_purpose::STANDARD
        .decode(encoded.trim())
        .context("transaction is not valid base64")?;
    let transaction: VersionedTransaction =
        bincode::deserialize(&bytes).context("invalid serialized VersionedTransaction")?;
    Ok(transaction)
}

pub fn simulate_base64_transaction(
    rpc_url: &str,
    encoded: &str,
) -> Result<SimulationReport> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let transaction = decode_transaction_base64(encoded)?;
    let client = RpcClient::new(rpc_url.to_string());
    let response = client
        .simulate_transaction_with_config(
            &transaction,
            RpcSimulateTransactionConfig {
                sig_verify: false,
                replace_recent_blockhash: true,
                commitment: Some(CommitmentConfig::processed()),
                ..RpcSimulateTransactionConfig::default()
            },
        )
        .context("Solana transaction simulation RPC failed")?;

    let result = serde_json::to_value(&response.value)
        .context("failed to serialize simulation result")?;
    let succeeded = result
        .get("err")
        .map(Value::is_null)
        .unwrap_or(false);

    Ok(SimulationReport {
        succeeded,
        rpc_context_slot: response.context.slot,
        result,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::message::{Message, VersionedMessage};

    #[test]
    fn empty_transaction_is_rejected() {
        assert!(decode_transaction_base64("").is_err());
    }

    #[test]
    fn invalid_base64_is_rejected() {
        assert!(decode_transaction_base64("not-base64***").is_err());
    }

    #[test]
    fn serialized_versioned_transaction_round_trips() {
        let transaction = VersionedTransaction {
            signatures: vec![],
            message: VersionedMessage::Legacy(Message::new(&[], None)),
        };
        let bytes = bincode::serialize(&transaction).unwrap();
        let encoded = general_purpose::STANDARD.encode(bytes);

        let decoded = decode_transaction_base64(&encoded).unwrap();

        assert_eq!(decoded.signatures.len(), 0);
    }
}
