mod models;
mod risk;
mod state_reader;

use anyhow::{Context, Result};

fn usage() {
    eprintln!(
        "Usage:\n  meteora-executor inspect-pool <RPC_URL> <POOL_ADDRESS> [ARRAY_RADIUS]"
    );
}

#[tokio::main]
async fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let Some(command) = args.next() else {
        println!("meteora-executor v0.2");
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
        _ => {
            usage();
            anyhow::bail!("unknown command: {command}");
        }
    }

    Ok(())
}
