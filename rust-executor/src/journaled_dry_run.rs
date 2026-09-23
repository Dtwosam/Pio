use crate::dry_run::{DryRunExecutionReport, DryRunExecutionRequest};
use crate::execution_guard::{check_proposal, RiskCheckReport};
use crate::execution_store::{
    ExecutionIntentStatus, ExecutionIntentStore,
};
use crate::risk::RiskConfig;
use crate::simulation::SimulationReport;
use crate::transaction_guard::{
    check_transaction, TransactionGuardConfig, TransactionGuardReport,
};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JournaledDryRunReport {
    pub reused_existing_intent: bool,
    pub report: DryRunExecutionReport,
}

fn report_from_persisted(
    status: &ExecutionIntentStatus,
    risk: RiskCheckReport,
    transaction: Option<TransactionGuardReport>,
    simulation: Option<SimulationReport>,
) -> Result<DryRunExecutionReport> {
    match status {
        ExecutionIntentStatus::Rejected => {
            if let Some(transaction) = transaction {
                if !transaction.accepted {
                    return Ok(DryRunExecutionReport {
                        accepted: false,
                        stage: "TRANSACTION".into(),
                        reason: transaction.reason.clone(),
                        risk,
                        transaction: Some(transaction),
                        simulation: None,
                    });
                }
            }
            Ok(DryRunExecutionReport {
                accepted: false,
                stage: "RISK".into(),
                reason: risk.reason.clone(),
                risk,
                transaction: None,
                simulation: None,
            })
        }
        ExecutionIntentStatus::SimulationPassed => {
            let transaction = transaction.context(
                "completed dry-run intent is missing persisted transaction guard",
            )?;
            if !transaction.accepted {
                anyhow::bail!(
                    "SIMULATION_PASSED intent has a rejected transaction guard"
                );
            }
            Ok(DryRunExecutionReport {
                accepted: true,
                stage: "SIMULATED".into(),
                reason: "risk_transaction_and_simulation_passed".into(),
                risk,
                transaction: Some(transaction),
                simulation,
            })
        }
        ExecutionIntentStatus::SimulationFailed => {
            let transaction = transaction.context(
                "failed simulation intent is missing persisted transaction guard",
            )?;
            Ok(DryRunExecutionReport {
                accepted: false,
                stage: "SIMULATION".into(),
                reason: "transaction_simulation_failed".into(),
                risk,
                transaction: Some(transaction),
                simulation,
            })
        }
        other => anyhow::bail!(
            "execution intent status {:?} is not a completed dry-run state",
            other
        ),
    }
}

pub fn run_journaled_dry_run_with<F>(
    store: &ExecutionIntentStore,
    request: &DryRunExecutionRequest,
    risk_config: &RiskConfig,
    transaction_config: &TransactionGuardConfig,
    simulate: F,
) -> Result<JournaledDryRunReport>
where
    F: FnOnce(&str) -> Result<SimulationReport>,
{
    let registered = store.register_with_transaction_policy(
        request,
        risk_config,
        transaction_config,
    )?;
    let decision_id = request.proposal.decision_id.to_string();
    let current = registered.record;
    let current_status = current.status.clone();

    match current_status {
        ExecutionIntentStatus::Rejected
        | ExecutionIntentStatus::SimulationPassed
        | ExecutionIntentStatus::SimulationFailed => {
            let risk = current
                .risk
                .context("completed dry-run intent is missing persisted risk result")?;
            let report = report_from_persisted(
                &current_status,
                risk,
                current.transaction_guard,
                current.simulation,
            )?;
            return Ok(JournaledDryRunReport {
                reused_existing_intent: true,
                report,
            });
        }
        ExecutionIntentStatus::Signing
        | ExecutionIntentStatus::Sent
        | ExecutionIntentStatus::Confirmed
        | ExecutionIntentStatus::Failed => {
            anyhow::bail!(
                "execution intent has progressed beyond resumable dry-run state: {:?}",
                current.status
            );
        }
        ExecutionIntentStatus::Received | ExecutionIntentStatus::RiskApproved => {}
    }

    let risk = if current_status == ExecutionIntentStatus::RiskApproved {
        current
            .risk
            .clone()
            .context("RISK_APPROVED intent is missing persisted risk result")?
    } else {
        let result = check_proposal(&request.proposal, risk_config);
        store.record_risk(&decision_id, &result)?;
        if !result.accepted {
            return Ok(JournaledDryRunReport {
                reused_existing_intent: registered.reused_existing,
                report: DryRunExecutionReport {
                    accepted: false,
                    stage: "RISK".into(),
                    reason: result.reason.clone(),
                    risk: result,
                    transaction: None,
                    simulation: None,
                },
            });
        }
        result
    };

    let transaction = if let Some(existing) = current.transaction_guard.clone() {
        if !existing.accepted {
            anyhow::bail!(
                "RISK_APPROVED intent unexpectedly contains a rejected transaction guard"
            );
        }
        existing
    } else {
        let result = check_transaction(
            &request.proposal,
            &request.transaction_base64,
            transaction_config,
        )?;
        store.record_transaction_guard(&decision_id, &result)?;
        if !result.accepted {
            return Ok(JournaledDryRunReport {
                reused_existing_intent: registered.reused_existing,
                report: DryRunExecutionReport {
                    accepted: false,
                    stage: "TRANSACTION".into(),
                    reason: result.reason.clone(),
                    risk,
                    transaction: Some(result),
                    simulation: None,
                },
            });
        }
        result
    };

    let simulation = simulate(&request.transaction_base64)?;
    store.record_simulation(&decision_id, &simulation)?;
    let report = if simulation.succeeded {
        DryRunExecutionReport {
            accepted: true,
            stage: "SIMULATED".into(),
            reason: "risk_transaction_and_simulation_passed".into(),
            risk,
            transaction: Some(transaction),
            simulation: Some(simulation),
        }
    } else {
        DryRunExecutionReport {
            accepted: false,
            stage: "SIMULATION".into(),
            reason: "transaction_simulation_failed".into(),
            risk,
            transaction: Some(transaction),
            simulation: Some(simulation),
        }
    };

    Ok(JournaledDryRunReport {
        reused_existing_intent: registered.reused_existing,
        report,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode, TradeProposal};
    use base64::{engine::general_purpose, Engine as _};
    use serde_json::json;
    use solana_sdk::instruction::{AccountMeta, Instruction};
    use solana_sdk::message::{Message, VersionedMessage};
    use solana_sdk::pubkey::Pubkey;
    use solana_sdk::signature::Signature;
    use solana_sdk::transaction::VersionedTransaction;
    use std::cell::Cell;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn db_path() -> PathBuf {
        std::env::temp_dir().join(format!("pio-journaled-dry-run-{}.db", Uuid::new_v4()))
    }

    fn fixture(mode: Mode) -> (DryRunExecutionRequest, TransactionGuardConfig) {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let instruction = Instruction {
            program_id: program,
            accounts: vec![AccountMeta::new_readonly(pool, false)],
            data: vec![1],
        };
        let message = Message::new(&[instruction], Some(&payer));
        let transaction = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::Legacy(message),
        };
        let transaction_base64 =
            general_purpose::STANDARD.encode(bincode::serialize(&transaction).unwrap());

        (
            DryRunExecutionRequest {
                proposal: TradeProposal {
                    decision_id: Uuid::new_v4(),
                    mode,
                    action: Action::Enter,
                    pool_address: pool.to_string(),
                    capital_quote: 10.0,
                    account_equity_quote: 1_000.0,
                    portfolio_deployed_quote: 100.0,
                    daily_drawdown_pct: 0.5,
                    min_bin_id: -10,
                    max_bin_id: 10,
                    strategy: "SPOT".into(),
                    expected_net_return_pct: 1.0,
                    expected_downside_pct: 0.5,
                    model_version: "baseline".into(),
                    data_age_seconds: 1,
                },
                transaction_base64,
            },
            TransactionGuardConfig {
                expected_fee_payer: payer.to_string(),
                allowed_program_ids: vec![program.to_string()],
                max_instructions: 4,
                max_static_accounts: 16,
                allow_address_lookup_tables: false,
                require_unsigned: true,
                require_instruction_policy: false,
                instruction_policies: vec![],
            },
        )
    }

    fn config() -> RiskConfig {
        RiskConfig {
            max_capital_per_position_pct: 2.0,
            max_total_deployed_pct: 20.0,
            max_daily_drawdown_pct: 3.0,
            min_expected_edge_pct: 0.25,
            max_expected_downside_pct: 2.0,
            max_data_age_seconds: 30,
        }
    }

    fn simulation(succeeded: bool) -> SimulationReport {
        SimulationReport {
            succeeded,
            rpc_context_slot: 1,
            result: json!({
                "err": if succeeded {
                    serde_json::Value::Null
                } else {
                    json!("boom")
                }
            }),
        }
    }

    #[test]
    fn completed_dry_run_is_reused_without_resimulation() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let (request, tx_config) = fixture(Mode::Live);
        let calls = Cell::new(0);

        let first = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            &tx_config,
            |_| {
                calls.set(calls.get() + 1);
                Ok(simulation(true))
            },
        )
        .unwrap();
        let second = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            &tx_config,
            |_| {
                calls.set(calls.get() + 1);
                Ok(simulation(true))
            },
        )
        .unwrap();

        assert!(first.report.accepted);
        assert!(second.report.accepted);
        assert!(second.reused_existing_intent);
        assert_eq!(calls.get(), 1);
        let persisted = store
            .load(&request.proposal.decision_id.to_string())
            .unwrap();
        assert!(persisted.transaction_guard.unwrap().accepted);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn risk_rejection_is_reused_without_transaction_or_simulation() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let (request, tx_config) = fixture(Mode::Paper);
        let calls = Cell::new(0);

        let first = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            &tx_config,
            |_| {
                calls.set(calls.get() + 1);
                Ok(simulation(true))
            },
        )
        .unwrap();
        let second = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            &tx_config,
            |_| {
                calls.set(calls.get() + 1);
                Ok(simulation(true))
            },
        )
        .unwrap();

        assert!(!first.report.accepted);
        assert_eq!(first.report.stage, "RISK");
        assert!(first.report.transaction.is_none());
        assert!(second.reused_existing_intent);
        assert_eq!(calls.get(), 0);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn transaction_rejection_is_reused_without_simulation() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let (request, mut tx_config) = fixture(Mode::Live);
        tx_config.allowed_program_ids = vec![Pubkey::new_unique().to_string()];
        let calls = Cell::new(0);

        let first = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            &tx_config,
            |_| {
                calls.set(calls.get() + 1);
                Ok(simulation(true))
            },
        )
        .unwrap();
        let second = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            &tx_config,
            |_| {
                calls.set(calls.get() + 1);
                Ok(simulation(true))
            },
        )
        .unwrap();

        assert!(!first.report.accepted);
        assert_eq!(first.report.stage, "TRANSACTION");
        assert!(second.reused_existing_intent);
        assert_eq!(calls.get(), 0);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn rpc_error_resumes_after_persisted_transaction_guard() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let (request, tx_config) = fixture(Mode::Live);

        let first = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            &tx_config,
            |_| anyhow::bail!("rpc unavailable"),
        );
        assert!(first.is_err());
        let persisted = store
            .load(&request.proposal.decision_id.to_string())
            .unwrap();
        assert_eq!(persisted.status, ExecutionIntentStatus::RiskApproved);
        assert!(persisted.transaction_guard.unwrap().accepted);

        let second = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            &tx_config,
            |_| Ok(simulation(true)),
        )
        .unwrap();

        assert!(second.report.accepted);
        assert!(second.reused_existing_intent);
        let _ = std::fs::remove_file(path);
    }
}
