mod events;
mod execution_guard;
mod models;
mod prestate_verifier;
mod risk;
mod state_reader;
mod transaction_events;

use anyhow::{Context, Result};
use std::io::Read;

fn usage() {
    eprintln!(
        "Usage:
  meteora-executor inspect-pool <RPC_URL> <POOL_ADDRESS> [ARRAY_RADIUS]
  meteora-executor inspect-pool-env <POOL_ADDRESS> [ARRAY_RADIUS]
  meteora-executor inspect-position <RPC_URL> <POSITION_ADDRESS>
  meteora-executor risk-check <PROPOSAL_JSON_OR_-> <RISK_CONFIG_JSON>
  meteora-executor inspect-transaction-events <RPC_URL> <SIGNATURE>
  meteora-executor verify-prestate <RPC_URL> <SIGNATURE> <CAPTURE_START_SLOT> <CAPTURE_END_SLOT> <ACCOUNT> [ACCOUNT ...]"
    );
}

#[tokio::main]
async fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let Some(command) = args.next() else {
        println!("meteora-executor v0.3");
        println!("default safety state: PAPER / signing disabled");
        usage();
        return Ok(());
    };

    match command.as_str() {
        "inspect-pool" => {
            let rpc_url = args.next().context("RPC_URL is required")?;
            let pool_address = args.next().context("POOL_ADDRESS is required")?;
            let array_radius: i32 = args
                .next()
                .as_deref()
                .unwrap_or("1")
                .parse()
                .context("ARRAY_RADIUS must be an integer")?;

            let snapshot =
                state_reader::inspect_pool(&rpc_url, &pool_address, array_radius).await?;
            println!("{}", serde_json::to_string_pretty(&snapshot)?);
        }
        "inspect-pool-env" => {
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; \
RPC_URL is accepted as a compatibility fallback",
                )?;
            let pool_address = args.next().context("POOL_ADDRESS is required")?;
            let array_radius: i32 = args
                .next()
                .as_deref()
                .unwrap_or("1")
                .parse()
                .context("ARRAY_RADIUS must be an integer")?;

            let snapshot =
                state_reader::inspect_pool(&rpc_url, &pool_address, array_radius).await?;
            println!("{}", serde_json::to_string_pretty(&snapshot)?);
        }
        "risk-check" => {
            let proposal_source = args
                .next()
                .context("PROPOSAL_JSON_OR_- is required")?;
            let config_path = args
                .next()
                .context("RISK_CONFIG_JSON is required")?;
            if args.next().is_some() {
                anyhow::bail!("risk-check accepts exactly two arguments");
            }

            let proposal_json = if proposal_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read proposal JSON from stdin")?;
                input
            } else {
                std::fs::read_to_string(&proposal_source)
                    .with_context(|| format!("failed to read proposal JSON: {proposal_source}"))?
            };
            let config_json = std::fs::read_to_string(&config_path)
                .with_context(|| format!("failed to read risk config JSON: {config_path}"))?;

            let proposal: models::TradeProposal = serde_json::from_str(&proposal_json)
                .context("invalid trade proposal JSON")?;
            let config: risk::RiskConfig = serde_json::from_str(&config_json)
                .context("invalid risk config JSON")?;
            let report = execution_guard::check_proposal(&proposal, &config);
            println!("{}", serde_json::to_string_pretty(&report)?);
            if !report.accepted {
                std::process::exit(2);
            }
        }
        "inspect-position" => {
            let rpc_url = args.next().context("RPC_URL is required")?;
            let position_address = args.next().context("POSITION_ADDRESS is required")?;
            let snapshot =
                state_reader::inspect_position(&rpc_url, &position_address).await?;
            println!("{}", serde_json::to_string_pretty(&snapshot)?);
        }
        "inspect-transaction-events" => {
            let rpc_url = args.next().context("RPC_URL is required")?;
            let signature = args.next().context("SIGNATURE is required")?;
            let snapshot =
                transaction_events::inspect_transaction_events(&rpc_url, &signature).await?;
            println!("{}", serde_json::to_string_pretty(&snapshot)?);
        }
        "verify-prestate" => {
            let rpc_url = args.next().context("RPC_URL is required")?;
            let signature = args.next().context("SIGNATURE is required")?;
            let capture_slot_start: u64 = args
                .next()
                .context("CAPTURE_START_SLOT is required")?
                .parse()
                .context("CAPTURE_START_SLOT must be an integer")?;
            let capture_slot_end: u64 = args
                .next()
                .context("CAPTURE_END_SLOT is required")?
                .parse()
                .context("CAPTURE_END_SLOT must be an integer")?;
            let addresses: Vec<String> = args.collect();
            let result = prestate_verifier::verify_prestate_gap(
                &rpc_url,
                &signature,
                capture_slot_start,
                capture_slot_end,
                &addresses,
            )
            .await?;
            println!("{}", serde_json::to_string_pretty(&result)?);
        }
        _ => {
            usage();
            anyhow::bail!("unknown command: {command}");
        }
    }

    Ok(())
}
