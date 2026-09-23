mod blockhash;
mod confirmation;
mod dry_run;
mod entry;
mod emergency_exit;
mod events;
mod execution_guard;
mod execution_receipt;
mod execution_store;
mod journaled_dry_run;
mod models;
mod preflight;
mod prestate_verifier;
mod presign;
mod risk;
mod simulation;
mod signer;
mod submission;
mod state_reader;
mod transaction_events;
mod transaction_guard;
mod wallet;
mod wallet_guard;

use anyhow::{Context, Result};
use std::io::Read;

fn usage() {
    eprintln!(
        "Usage:
  meteora-executor inspect-pool <RPC_URL> <POOL_ADDRESS> [ARRAY_RADIUS]
  meteora-executor inspect-pool-env <POOL_ADDRESS> [ARRAY_RADIUS]
  meteora-executor inspect-position <RPC_URL> <POSITION_ADDRESS>
  meteora-executor risk-check <PROPOSAL_JSON_OR_-> <RISK_CONFIG_JSON>
  meteora-executor dry-run-execution <REQUEST_JSON_OR_-> <RISK_CONFIG_JSON> <TRANSACTION_GUARD_CONFIG_JSON> <EXECUTION_DB>
  meteora-executor preflight-execution <REQUEST_JSON_OR_-> <RISK_CONFIG_JSON> <TRANSACTION_GUARD_CONFIG_JSON>
  meteora-executor presign-preflight <REQUEST_JSON_OR_-> <RISK_CONFIG_JSON> <TRANSACTION_GUARD_CONFIG_JSON>
  meteora-executor execution-intent-status <EXECUTION_DB> <DECISION_ID>
  meteora-executor execution-confirmation <EXECUTION_DB> <DECISION_ID>
  meteora-executor execution-receipt <EXECUTION_DB> <DECISION_ID>
  meteora-executor execution-wallet-authorize <EXECUTION_DB> <DECISION_ID>
  meteora-executor execution-presign-prepare <REQUEST_JSON_OR_-> <RISK_CONFIG_JSON> <TRANSACTION_GUARD_CONFIG_JSON> <EXECUTION_DB>
  meteora-executor build-emergency-exit <REQUEST_JSON_OR_->
  meteora-executor wallet-status
  meteora-executor build-standard-spl-entry <ENTRY_REQUEST_JSON_OR_->
  meteora-executor build-standard-spl-entry-from-chain <ENTRY_REQUEST_JSON_OR_->
  meteora-executor wallet-authorize-transaction <PROPOSAL_JSON_OR_-> <TRANSACTION_BASE64_FILE_OR_-> <TRANSACTION_GUARD_CONFIG_JSON>
  meteora-executor simulate-transaction <TRANSACTION_BASE64_FILE_OR_->
  meteora-executor guard-transaction <PROPOSAL_JSON_OR_-> <TRANSACTION_BASE64_FILE_OR_-> <TRANSACTION_GUARD_CONFIG_JSON>
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
        "dry-run-execution" => {
            let request_source = args
                .next()
                .context("REQUEST_JSON_OR_- is required")?;
            let config_path = args
                .next()
                .context("RISK_CONFIG_JSON is required")?;
            let transaction_config_path = args
                .next()
                .context("TRANSACTION_GUARD_CONFIG_JSON is required")?;
            let execution_db = args
                .next()
                .context("EXECUTION_DB is required")?;
            if args.next().is_some() {
                anyhow::bail!("dry-run-execution accepts exactly four arguments");
            }

            let request_json = if request_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read dry-run request JSON from stdin")?;
                input
            } else {
                std::fs::read_to_string(&request_source)
                    .with_context(|| {
                        format!("failed to read dry-run request JSON: {request_source}")
                    })?
            };
            let config_json = std::fs::read_to_string(&config_path)
                .with_context(|| format!("failed to read risk config JSON: {config_path}"))?;
            let transaction_config_json =
                std::fs::read_to_string(&transaction_config_path)
                    .with_context(|| {
                        format!(
                            "failed to read transaction guard config JSON: {transaction_config_path}"
                        )
                    })?;
            let request: dry_run::DryRunExecutionRequest =
                serde_json::from_str(&request_json)
                    .context("invalid dry-run execution request JSON")?;
            let config: risk::RiskConfig = serde_json::from_str(&config_json)
                .context("invalid risk config JSON")?;
            let transaction_config:
                transaction_guard::TransactionGuardConfig =
                serde_json::from_str(&transaction_config_json)
                    .context("invalid transaction guard config JSON")?;
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; RPC_URL is accepted as a compatibility fallback",
                )?;
            let store = execution_store::ExecutionIntentStore::open(
                &execution_db,
            )?;

            let report = journaled_dry_run::run_journaled_dry_run_with(
                &store,
                &request,
                &config,
                &transaction_config,
                |encoded| simulation::simulate_base64_transaction(&rpc_url, encoded),
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
            if !report.report.accepted {
                std::process::exit(2);
            }
        }
        "preflight-execution" => {
            let request_source = args
                .next()
                .context("REQUEST_JSON_OR_- is required")?;
            let risk_config_path = args
                .next()
                .context("RISK_CONFIG_JSON is required")?;
            let transaction_config_path = args
                .next()
                .context("TRANSACTION_GUARD_CONFIG_JSON is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "preflight-execution accepts exactly three arguments"
                );
            }

            let request_json = if request_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read execution request JSON from stdin")?;
                input
            } else {
                std::fs::read_to_string(&request_source)
                    .with_context(|| {
                        format!(
                            "failed to read execution request JSON: {request_source}"
                        )
                    })?
            };
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
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; RPC_URL is accepted as a compatibility fallback",
                )?;

            let report = preflight::evaluate_preflight_with(
                &request,
                &risk_config,
                &transaction_config,
                |encoded| {
                    simulation::simulate_base64_transaction(
                        &rpc_url,
                        encoded,
                    )
                },
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
            if !report.accepted {
                std::process::exit(2);
            }
        }
        "presign-preflight" => {
            let request_source = args
                .next()
                .context("REQUEST_JSON_OR_- is required")?;
            let risk_config_path = args
                .next()
                .context("RISK_CONFIG_JSON is required")?;
            let transaction_config_path = args
                .next()
                .context("TRANSACTION_GUARD_CONFIG_JSON is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "presign-preflight accepts exactly three arguments"
                );
            }

            let request_json = if request_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read execution request JSON from stdin")?;
                input
            } else {
                std::fs::read_to_string(&request_source)
                    .with_context(|| {
                        format!(
                            "failed to read execution request JSON: {request_source}"
                        )
                    })?
            };
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
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; RPC_URL is accepted as a compatibility fallback",
                )?;
            let wallet_status = wallet::inspect_executor_wallet_from_env()?;
            let wallet_pubkey: solana_sdk::pubkey::Pubkey =
                wallet_status.pubkey.parse()
                    .context("executor wallet pubkey is invalid")?;

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
        }
        "execution-intent-status" => {
            let execution_db = args
                .next()
                .context("EXECUTION_DB is required")?;
            let decision_id = args
                .next()
                .context("DECISION_ID is required")?;
            if args.next().is_some() {
                anyhow::bail!("execution-intent-status accepts exactly two arguments");
            }
            let store = execution_store::ExecutionIntentStore::open(
                &execution_db,
            )?;
            let record = store.load(&decision_id)?;
            println!("{}", serde_json::to_string_pretty(&record)?);
        }
        "execution-wallet-authorize" => {
            let execution_db = args
                .next()
                .context("EXECUTION_DB is required")?;
            let decision_id = args
                .next()
                .context("DECISION_ID is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "execution-wallet-authorize accepts exactly two arguments"
                );
            }
            let store = execution_store::ExecutionIntentStore::open(
                &execution_db,
            )?;
            let intent = store.load(&decision_id)?;
            let transaction = intent
                .transaction_guard
                .as_ref()
                .context(
                    "execution intent has no persisted transaction guard"
                )?;
            let wallet_status = wallet::inspect_executor_wallet_from_env()?;
            let wallet_pubkey: solana_sdk::pubkey::Pubkey =
                wallet_status.pubkey.parse()
                    .context("executor wallet pubkey is invalid")?;
            let authorization = wallet_guard::authorize_wallet(
                &wallet_pubkey,
                transaction,
            )?;
            let persisted = store.record_wallet_authorization(
                &decision_id,
                &authorization,
            )?;
            let output = serde_json::json!({
                "wallet": wallet_status,
                "authorization": authorization,
                "intent": persisted,
            });
            println!("{}", serde_json::to_string_pretty(&output)?);
        }
        "execution-presign-prepare" => {
            let request_source = args
                .next()
                .context("REQUEST_JSON_OR_- is required")?;
            let risk_config_path = args
                .next()
                .context("RISK_CONFIG_JSON is required")?;
            let transaction_config_path = args
                .next()
                .context("TRANSACTION_GUARD_CONFIG_JSON is required")?;
            let execution_db = args
                .next()
                .context("EXECUTION_DB is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "execution-presign-prepare accepts exactly four arguments"
                );
            }

            let request_json = if request_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read execution request JSON from stdin")?;
                input
            } else {
                std::fs::read_to_string(&request_source)
                    .with_context(|| {
                        format!(
                            "failed to read execution request JSON: {request_source}"
                        )
                    })?
            };
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
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; RPC_URL is accepted as a compatibility fallback",
                )?;
            let wallet_status = wallet::inspect_executor_wallet_from_env()?;
            let wallet_pubkey: solana_sdk::pubkey::Pubkey =
                wallet_status.pubkey.parse()
                    .context("executor wallet pubkey is invalid")?;
            let store = execution_store::ExecutionIntentStore::open(
                &execution_db,
            )?;
            let registered = store.register_with_transaction_policy(
                &request,
                &risk_config,
                &transaction_config,
            )?;
            if registered.record.status
                != execution_store::ExecutionIntentStatus::SimulationPassed
            {
                anyhow::bail!(
                    "execution-presign-prepare requires SIMULATION_PASSED intent; current status is {:?}",
                    registered.record.status
                );
            }
            if registered.record.wallet_authorization.is_none() {
                anyhow::bail!(
                    "execution-presign-prepare requires persisted wallet authorization"
                );
            }

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
            if !report.accepted {
                println!("{}", serde_json::to_string_pretty(&report)?);
                std::process::exit(2);
            }
            let prepared = report
                .prepared
                .as_ref()
                .context("accepted presign report is missing prepared transaction")?;
            let transaction = report
                .transaction
                .as_ref()
                .context("accepted presign report is missing transaction guard")?;
            let wallet = report
                .wallet
                .as_ref()
                .context("accepted presign report is missing wallet authorization")?;
            let simulation = report
                .simulation
                .as_ref()
                .context("accepted presign report is missing simulation")?;
            let persisted = store.record_final_presign(
                &request.proposal.decision_id.to_string(),
                prepared,
                transaction,
                wallet,
                simulation,
            )?;
            let output = serde_json::json!({
                "report": report,
                "intent": persisted,
            });
            println!("{}", serde_json::to_string_pretty(&output)?);
        }
        "execution-receipt" => {
            let execution_db = args
                .next()
                .context("EXECUTION_DB is required")?;
            let decision_id = args
                .next()
                .context("DECISION_ID is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "execution-receipt accepts exactly two arguments"
                );
            }
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; RPC_URL is accepted as a compatibility fallback",
                )?;
            let store = execution_store::ExecutionIntentStore::open(
                &execution_db,
            )?;
            let intent = store.load(&decision_id)?;
            let signature = intent
                .signature
                .as_deref()
                .context("execution intent has no signature")?;
            let snapshot =
                transaction_events::inspect_transaction_events(
                    &rpc_url,
                    signature,
                )
                .await?;
            let receipt = execution_receipt::build_execution_receipt(
                &intent,
                &snapshot,
            )?;
            println!("{}", serde_json::to_string_pretty(&receipt)?);
        }
        "execution-confirmation" => {
            let execution_db = args
                .next()
                .context("EXECUTION_DB is required")?;
            let decision_id = args
                .next()
                .context("DECISION_ID is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "execution-confirmation accepts exactly two arguments"
                );
            }
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; RPC_URL is accepted as a compatibility fallback",
                )?;
            let store = execution_store::ExecutionIntentStore::open(
                &execution_db,
            )?;
            let report = confirmation::reconcile_confirmation_with(
                &store,
                &decision_id,
                |signature| {
                    confirmation::observe_confirmation_rpc(
                        &rpc_url,
                        signature,
                    )
                },
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
            if matches!(
                report.intent_status,
                execution_store::ExecutionIntentStatus::Failed
            ) {
                std::process::exit(2);
            }
        }
        "build-emergency-exit" => {
            let request_source = args
                .next()
                .context("REQUEST_JSON_OR_- is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "build-emergency-exit accepts exactly one argument"
                );
            }
            let request_json = if request_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read emergency exit request from stdin")?;
                input
            } else {
                std::fs::read_to_string(&request_source)
                    .with_context(|| {
                        format!(
                            "failed to read emergency exit request: {request_source}"
                        )
                    })?
            };
            let request: emergency_exit::ChainResolvedEmergencyExitRequest =
                serde_json::from_str(&request_json)
                    .context("invalid emergency exit request JSON")?;
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; RPC_URL is accepted as a compatibility fallback",
                )?;
            let report =
                emergency_exit::build_standard_spl_emergency_exit_from_chain(
                    &rpc_url,
                    &request,
                )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        "build-standard-spl-entry" => {
            let request_source = args
                .next()
                .context("ENTRY_REQUEST_JSON_OR_- is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "build-standard-spl-entry accepts exactly one argument"
                );
            }
            let request_json = if request_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read entry request JSON from stdin")?;
                input
            } else {
                std::fs::read_to_string(&request_source)
                    .with_context(|| {
                        format!(
                            "failed to read entry request JSON: {request_source}"
                        )
                    })?
            };
            let request: entry::StandardSplEntryRequest =
                serde_json::from_str(&request_json)
                    .context("invalid standard-SPL entry request JSON")?;
            let report = entry::build_standard_spl_entry(&request)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        "build-standard-spl-entry-from-chain" => {
            let request_source = args
                .next()
                .context("ENTRY_REQUEST_JSON_OR_- is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "build-standard-spl-entry-from-chain accepts exactly one argument"
                );
            }
            let request_json = if request_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read entry request JSON from stdin")?;
                input
            } else {
                std::fs::read_to_string(&request_source)
                    .with_context(|| {
                        format!(
                            "failed to read entry request JSON: {request_source}"
                        )
                    })?
            };
            let request: entry::ChainResolvedStandardSplEntryRequest =
                serde_json::from_str(&request_json)
                    .context("invalid chain-resolved entry request JSON")?;
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; RPC_URL is accepted as a compatibility fallback",
                )?;
            let report =
                entry::build_standard_spl_entry_from_chain(&rpc_url, &request)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        "wallet-status" => {
            if args.next().is_some() {
                anyhow::bail!("wallet-status accepts no arguments");
            }
            let status = wallet::inspect_executor_wallet_from_env()?;
            println!("{}", serde_json::to_string_pretty(&status)?);
        }
        "wallet-authorize-transaction" => {
            let proposal_source = args
                .next()
                .context("PROPOSAL_JSON_OR_- is required")?;
            let transaction_source = args
                .next()
                .context("TRANSACTION_BASE64_FILE_OR_- is required")?;
            let config_path = args
                .next()
                .context("TRANSACTION_GUARD_CONFIG_JSON is required")?;
            if args.next().is_some() {
                anyhow::bail!(
                    "wallet-authorize-transaction accepts exactly three arguments"
                );
            }
            if proposal_source == "-" && transaction_source == "-" {
                anyhow::bail!(
                    "proposal and transaction cannot both be read from stdin"
                );
            }

            let proposal_json = if proposal_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read proposal JSON from stdin")?;
                input
            } else {
                std::fs::read_to_string(&proposal_source)
                    .with_context(|| {
                        format!("failed to read proposal JSON: {proposal_source}")
                    })?
            };
            let transaction_base64 = if transaction_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read transaction base64 from stdin")?;
                input
            } else {
                std::fs::read_to_string(&transaction_source)
                    .with_context(|| {
                        format!(
                            "failed to read transaction base64 file: {transaction_source}"
                        )
                    })?
            };
            let config_json = std::fs::read_to_string(&config_path)
                .with_context(|| {
                    format!(
                        "failed to read transaction guard config JSON: {config_path}"
                    )
                })?;

            let proposal: models::TradeProposal =
                serde_json::from_str(&proposal_json)
                    .context("invalid trade proposal JSON")?;
            let config: transaction_guard::TransactionGuardConfig =
                serde_json::from_str(&config_json)
                    .context("invalid transaction guard config JSON")?;
            let transaction = transaction_guard::check_transaction(
                &proposal,
                &transaction_base64,
                &config,
            )?;
            let wallet_status = wallet::inspect_executor_wallet_from_env()?;
            let wallet_pubkey: solana_sdk::pubkey::Pubkey =
                wallet_status.pubkey.parse()
                    .context("executor wallet pubkey is invalid")?;
            let authorization = wallet_guard::authorize_wallet(
                &wallet_pubkey,
                &transaction,
            )?;

            let output = serde_json::json!({
                "wallet": wallet_status,
                "transaction": transaction,
                "authorization": authorization,
            });
            println!("{}", serde_json::to_string_pretty(&output)?);
            if !authorization.accepted {
                std::process::exit(2);
            }
        }
        "simulate-transaction" => {
            let transaction_source = args
                .next()
                .context("TRANSACTION_BASE64_FILE_OR_- is required")?;
            if args.next().is_some() {
                anyhow::bail!("simulate-transaction accepts exactly one argument");
            }
            let encoded = if transaction_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read transaction base64 from stdin")?;
                input
            } else {
                std::fs::read_to_string(&transaction_source)
                    .with_context(|| {
                        format!(
                            "failed to read transaction base64 file: {transaction_source}"
                        )
                    })?
            };
            let rpc_url = std::env::var("SOLANA_RPC_URL")
                .or_else(|_| std::env::var("RPC_URL"))
                .context(
                    "SOLANA_RPC_URL environment variable is required; \
RPC_URL is accepted as a compatibility fallback",
                )?;
            let report = simulation::simulate_base64_transaction(
                &rpc_url,
                &encoded,
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
            if !report.succeeded {
                std::process::exit(2);
            }
        }
        "guard-transaction" => {
            let proposal_source = args
                .next()
                .context("PROPOSAL_JSON_OR_- is required")?;
            let transaction_source = args
                .next()
                .context("TRANSACTION_BASE64_FILE_OR_- is required")?;
            let config_path = args
                .next()
                .context("TRANSACTION_GUARD_CONFIG_JSON is required")?;
            if args.next().is_some() {
                anyhow::bail!("guard-transaction accepts exactly three arguments");
            }

            let proposal_json = if proposal_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read proposal JSON from stdin")?;
                input
            } else {
                std::fs::read_to_string(&proposal_source)
                    .with_context(|| {
                        format!("failed to read proposal JSON: {proposal_source}")
                    })?
            };
            if transaction_source == "-" && proposal_source == "-" {
                anyhow::bail!(
                    "proposal and transaction cannot both be read from stdin"
                );
            }
            let transaction_base64 = if transaction_source == "-" {
                let mut input = String::new();
                std::io::stdin()
                    .read_to_string(&mut input)
                    .context("failed to read transaction base64 from stdin")?;
                input
            } else {
                std::fs::read_to_string(&transaction_source)
                    .with_context(|| {
                        format!(
                            "failed to read transaction base64 file: {transaction_source}"
                        )
                    })?
            };
            let config_json = std::fs::read_to_string(&config_path)
                .with_context(|| {
                    format!(
                        "failed to read transaction guard config JSON: {config_path}"
                    )
                })?;
            let proposal: models::TradeProposal =
                serde_json::from_str(&proposal_json)
                    .context("invalid trade proposal JSON")?;
            let config: transaction_guard::TransactionGuardConfig =
                serde_json::from_str(&config_json)
                    .context("invalid transaction guard config JSON")?;
            let report = transaction_guard::check_transaction(
                &proposal,
                &transaction_base64,
                &config,
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
            if !report.accepted {
                std::process::exit(2);
            }
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
