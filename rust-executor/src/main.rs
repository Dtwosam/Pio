mod models;
mod risk;

use anyhow::Result;

#[tokio::main]
async fn main() -> Result<()> {
    println!("meteora-executor v0.1");
    println!("default safety state: PAPER / signing disabled");
    Ok(())
}
