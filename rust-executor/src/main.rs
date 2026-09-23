mod events;
mod models;
mod risk;
mod state_reader;
mod transaction_events;

use anyhow::{Context, Result};

fn usage() {
    eprintln!(
        "Usage:
  meteora-executor inspect-pool <RPC_URL> <POOL_ADDRESS> [ARRAY_RADIUS]
  meteora-executor inspect-position <RPC_URL> <POSITION_ADDRESS>
  meteora-executor inspect-transaction-events <RPC_URL> <SIGNATURE>"
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
        _ => {
            usage();
            anyhow::bail!("unknown command: {command}");
        }
    }

    Ok(())
}
