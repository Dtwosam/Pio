use crate::simulation::decode_transaction_base64;
use anyhow::{Context, Result};
use base64::{engine::general_purpose, Engine as _};
use serde::{Deserialize, Serialize};
use solana_client::rpc_client::RpcClient;
use solana_sdk::commitment_config::CommitmentConfig;
use solana_sdk::message::VersionedMessage;
use solana_sdk::signature::Signature;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreparedUnsignedTransaction {
    pub transaction_base64: String,
    pub recent_blockhash: String,
    pub last_valid_block_height: u64,
    pub rpc_context_slot: u64,
    pub signatures_all_default: bool,
}


pub fn prepare_unsigned_transaction_with_latest_blockhash(
    rpc_url: &str,
    encoded: &str,
) -> Result<PreparedUnsignedTransaction> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("RPC URL is required");
    }
    let mut transaction = decode_transaction_base64(encoded)?;
    let signatures_all_default = transaction
        .signatures
        .iter()
        .all(|signature| *signature == Signature::default());
    if !signatures_all_default {
        anyhow::bail!(
            "cannot refresh blockhash on a transaction that already has signatures"
        );
    }

    let client = RpcClient::new(rpc_url.to_string());
    let commitment = CommitmentConfig::confirmed();
    let rpc_context_slot = client
        .get_slot_with_commitment(commitment)
        .context("failed to fetch confirmed Solana slot")?;
    let (blockhash, last_valid_block_height) = client
        .get_latest_blockhash_with_commitment(commitment)
        .context("failed to fetch latest confirmed Solana blockhash")?;

    match &mut transaction.message {
        VersionedMessage::Legacy(message) => {
            message.recent_blockhash = blockhash;
        }
        VersionedMessage::V0(message) => {
            message.recent_blockhash = blockhash;
        }
    }

    let bytes = bincode::serialize(&transaction)
        .context("failed to serialize blockhash-prepared transaction")?;
    let transaction_base64 = general_purpose::STANDARD.encode(bytes);

    Ok(PreparedUnsignedTransaction {
        transaction_base64,
        recent_blockhash: blockhash.to_string(),
        last_valid_block_height,
        rpc_context_slot,
        signatures_all_default,
    })
}


#[cfg(test)]
mod tests {
    use super::*;
    use base64::engine::general_purpose;
    use solana_sdk::hash::Hash;
    use solana_sdk::message::{Message, VersionedMessage};
    use solana_sdk::pubkey::Pubkey;
    use solana_sdk::signature::Signature;
    use solana_sdk::transaction::VersionedTransaction;

    fn encoded_with_signature(signature: Signature) -> String {
        let payer = Pubkey::new_unique();
        let mut message = Message::new(&[], Some(&payer));
        message.recent_blockhash = Hash::default();
        let transaction = VersionedTransaction {
            signatures: vec![signature],
            message: VersionedMessage::Legacy(message),
        };
        general_purpose::STANDARD.encode(
            bincode::serialize(&transaction).unwrap(),
        )
    }

    #[test]
    fn signed_input_is_rejected_before_rpc() {
        let encoded = encoded_with_signature(Signature::new_unique());
        let result = prepare_unsigned_transaction_with_latest_blockhash(
            "http://127.0.0.1:1",
            &encoded,
        );
        assert!(result.is_err());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("already has signatures")
        );
    }
}
