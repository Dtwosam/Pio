use anyhow::{Context, Result};
use solana_sdk::pubkey::Pubkey;
use std::io::Read;
use std::str::FromStr;

#[path = "../models.rs"]
mod models;
#[path = "../risk.rs"]
mod risk;
#[path = "../execution_guard.rs"]
mod execution_guard;
#[path = "../simulation.rs"]
mod simulation;
#[path = "../transaction_guard.rs"]
mod transaction_guard;
#[path = "../wallet_guard.rs"]
mod wallet_guard;
#[path = "../blockhash.rs"]
mod blockhash;
#[path = "../dry_run.rs"]
mod dry_run;
#[path = "../presign.rs"]
mod presign;


fn read_request(source: &str) -> Result<String> {
    if source == "-" {
        let mut input = String::new();
        std::io::stdin()
            .read_to_string(&mut input)
            .context("failed to read execution request JSON from stdin")?;
        Ok(input)
    } else {
        std::fs::read_to_string(source)
            .with_context(|| format!("failed to read execution request JSON: {source}"))
    }
}


fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let request_source = args
        .next()
        .context("REQUEST_JSON_OR_- is required")?;
    let risk_config_path = args
        .next()
        .context("RISK_CONFIG_JSON is required")?;
    let transaction_config_path = args
        .next()
        .context("TRANSACTION_GUARD_CONFIG_JSON is required")?;
    let expected_wallet = args
        .next()
        .context("EXPECTED_WALLET_PUBKEY is required")?;
    if args.next().is_some() {
        anyhow::bail!(
            "phase7-exit-finalizer accepts exactly four arguments"
        );
    }

    let request_json = read_request(&request_source)?;
    let risk_config_json = std::fs::read_to_string(&risk_config_path)
        .with_context(|| {
            format!(
                "failed to read risk config JSON: {risk_config_path}"
            )
        })?;
    let transaction_config_json =
        std::fs::read_to_string(&transaction_config_path)
            .with_context(|| {
                format!(
                    "failed to read transaction guard config JSON: {transaction_config_path}"
                )
            })?;

    let request: dry_run::DryRunExecutionRequest =
        serde_json::from_str(&request_json)
            .context("invalid execution request JSON")?;
    let risk_config: risk::RiskConfig =
        serde_json::from_str(&risk_config_json)
            .context("invalid risk config JSON")?;
    let transaction_config:
        transaction_guard::TransactionGuardConfig =
        serde_json::from_str(&transaction_config_json)
            .context("invalid transaction guard config JSON")?;
    let wallet_pubkey = Pubkey::from_str(expected_wallet.trim())
        .context("expected wallet pubkey is invalid")?;

    if transaction_config.expected_fee_payer.trim() != wallet_pubkey.to_string() {
        anyhow::bail!(
            "transaction guard expected_fee_payer differs from expected wallet"
        );
    }

    let rpc_url = std::env::var("SOLANA_RPC_URL")
        .or_else(|_| std::env::var("RPC_URL"))
        .context(
            "SOLANA_RPC_URL environment variable is required; \
RPC_URL is accepted as a compatibility fallback",
        )?;

    let report = presign::evaluate_final_presign_with(
        &request,
        &risk_config,
        &transaction_config,
        &wallet_pubkey,
        |encoded| {
            blockhash::prepare_unsigned_transaction_with_latest_blockhash(
                &rpc_url,
                encoded,
            )
        },
        |encoded| {
            simulation::simulate_exact_base64_transaction(
                &rpc_url,
                encoded,
            )
        },
    )?;

    println!("{}", serde_json::to_string_pretty(&report)?);
    if !report.accepted {
        std::process::exit(2);
    }
    Ok(())
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expected_wallet_must_be_valid_pubkey() {
        assert!(Pubkey::from_str("not-a-pubkey").is_err());
    }
}
