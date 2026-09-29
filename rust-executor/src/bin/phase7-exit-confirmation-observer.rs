use anyhow::{Context, Result};
use serde::Serialize;
use solana_client::rpc_client::RpcClient;
use solana_sdk::commitment_config::CommitmentConfig;
use solana_sdk::signature::Signature;
use solana_sdk::transaction::TransactionError;
use std::str::FromStr;


#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum ExitConfirmationStatus {
    Pending,
    Confirmed,
    Failed,
}


#[derive(Debug, Clone, Serialize)]
struct ExitConfirmationObservation {
    signature: String,
    status: ExitConfirmationStatus,
    error: Option<String>,
    commitment: String,
    search_transaction_history: bool,
    observation_only: bool,
    automatic_retry_performed: bool,
    transaction_submission_attempted: bool,
}


fn classify_status(
    status: Option<Result<(), TransactionError>>,
) -> (ExitConfirmationStatus, Option<String>) {
    match status {
        None => (ExitConfirmationStatus::Pending, None),
        Some(Ok(())) => (ExitConfirmationStatus::Confirmed, None),
        Some(Err(error)) => (
            ExitConfirmationStatus::Failed,
            Some(error.to_string()),
        ),
    }
}


fn observe_confirmation(
    rpc_url: &str,
    signature: &str,
) -> Result<ExitConfirmationObservation> {
    if rpc_url.trim().is_empty() {
        anyhow::bail!("SOLANA_RPC_URL is required");
    }
    let parsed = Signature::from_str(signature.trim())
        .context("Phase 7 EXIT signature is not valid base58")?;
    let client = RpcClient::new(rpc_url.to_string());
    let observed = client
        .get_signature_status_with_commitment_and_history(
            &parsed,
            CommitmentConfig::confirmed(),
            true,
        )
        .context("Phase 7 EXIT signature status RPC failed")?;
    let (status, error) = classify_status(observed);

    Ok(ExitConfirmationObservation {
        signature: parsed.to_string(),
        status,
        error,
        commitment: "CONFIRMED".into(),
        search_transaction_history: true,
        observation_only: true,
        automatic_retry_performed: false,
        transaction_submission_attempted: false,
    })
}


fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let signature = args
        .next()
        .context("EXIT_TRANSACTION_SIGNATURE is required")?;
    if args.next().is_some() {
        anyhow::bail!(
            "phase7-exit-confirmation-observer accepts exactly one argument"
        );
    }

    let rpc_url = std::env::var("SOLANA_RPC_URL")
        .or_else(|_| std::env::var("RPC_URL"))
        .context(
            "SOLANA_RPC_URL environment variable is required; \
RPC_URL is accepted as a compatibility fallback",
        )?;
    let report = observe_confirmation(&rpc_url, &signature)?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pending_status_is_observation_only() {
        let (status, error) = classify_status(None);
        assert_eq!(status, ExitConfirmationStatus::Pending);
        assert_eq!(error, None);
    }

    #[test]
    fn confirmed_status_is_terminal_success() {
        let (status, error) = classify_status(Some(Ok(())));
        assert_eq!(status, ExitConfirmationStatus::Confirmed);
        assert_eq!(error, None);
    }

    #[test]
    fn failed_status_preserves_chain_error() {
        let error = TransactionError::AccountNotFound;
        let (status, observed_error) =
            classify_status(Some(Err(error.clone())));
        assert_eq!(status, ExitConfirmationStatus::Failed);
        assert_eq!(observed_error, Some(error.to_string()));
    }
}
