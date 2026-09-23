use crate::dry_run::{DryRunExecutionReport, DryRunExecutionRequest};
use crate::execution_guard::{check_proposal, RiskCheckReport};
use crate::execution_store::{
    ExecutionIntentStatus, ExecutionIntentStore,
};
use crate::risk::RiskConfig;
use crate::simulation::SimulationReport;
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
    simulation: Option<SimulationReport>,
) -> Result<DryRunExecutionReport> {
    match status {
        ExecutionIntentStatus::Rejected => Ok(DryRunExecutionReport {
            accepted: false,
            stage: "RISK".into(),
            reason: risk.reason.clone(),
            risk,
            simulation: None,
        }),
        ExecutionIntentStatus::SimulationPassed => Ok(DryRunExecutionReport {
            accepted: true,
            stage: "SIMULATED".into(),
            reason: "risk_and_simulation_passed".into(),
            risk,
            simulation,
        }),
        ExecutionIntentStatus::SimulationFailed => Ok(DryRunExecutionReport {
            accepted: false,
            stage: "SIMULATION".into(),
            reason: "transaction_simulation_failed".into(),
            risk,
            simulation,
        }),
        other => anyhow::bail!(
            "execution intent status {:?} is not a completed dry-run state",
            other
        ),
    }
}

pub fn run_journaled_dry_run_with<F>(
    store: &ExecutionIntentStore,
    request: &DryRunExecutionRequest,
    config: &RiskConfig,
    simulate: F,
) -> Result<JournaledDryRunReport>
where
    F: FnOnce(&str) -> Result<SimulationReport>,
{
    let registered = store.register(request, config)?;
    let decision_id = request.proposal.decision_id.to_string();
    let current = registered.record;

    match current.status {
        ExecutionIntentStatus::Rejected
        | ExecutionIntentStatus::SimulationPassed
        | ExecutionIntentStatus::SimulationFailed => {
            let risk = current
                .risk
                .context("completed dry-run intent is missing persisted risk result")?;
            let report = report_from_persisted(
                &current.status,
                risk,
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

    let risk = if current.status == ExecutionIntentStatus::RiskApproved {
        current
            .risk
            .context("RISK_APPROVED intent is missing persisted risk result")?
    } else {
        let result = check_proposal(&request.proposal, config);
        store.record_risk(&decision_id, &result)?;
        if !result.accepted {
            return Ok(JournaledDryRunReport {
                reused_existing_intent: registered.reused_existing,
                report: DryRunExecutionReport {
                    accepted: false,
                    stage: "RISK".into(),
                    reason: result.reason.clone(),
                    risk: result,
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
            reason: "risk_and_simulation_passed".into(),
            risk,
            simulation: Some(simulation),
        }
    } else {
        DryRunExecutionReport {
            accepted: false,
            stage: "SIMULATION".into(),
            reason: "transaction_simulation_failed".into(),
            risk,
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
    use serde_json::json;
    use std::cell::Cell;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn db_path() -> PathBuf {
        std::env::temp_dir().join(format!("pio-journaled-dry-run-{}.db", Uuid::new_v4()))
    }

    fn request(mode: Mode) -> DryRunExecutionRequest {
        DryRunExecutionRequest {
            proposal: TradeProposal {
                decision_id: Uuid::new_v4(),
                mode,
                action: Action::Enter,
                pool_address: "pool".into(),
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
            transaction_base64: "tx".into(),
        }
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
        let request = request(Mode::Live);
        let calls = Cell::new(0);

        let first = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
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
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn risk_rejection_is_reused_without_simulation() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request(Mode::Paper);
        let calls = Cell::new(0);

        let first = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
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
            |_| {
                calls.set(calls.get() + 1);
                Ok(simulation(true))
            },
        )
        .unwrap();

        assert!(!first.report.accepted);
        assert_eq!(first.report.stage, "RISK");
        assert!(second.reused_existing_intent);
        assert_eq!(calls.get(), 0);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn rpc_error_resumes_from_risk_approved_state() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request(Mode::Live);

        let first = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            |_| anyhow::bail!("rpc unavailable"),
        );
        assert!(first.is_err());
        assert_eq!(
            store
                .load(&request.proposal.decision_id.to_string())
                .unwrap()
                .status,
            ExecutionIntentStatus::RiskApproved
        );

        let second = run_journaled_dry_run_with(
            &store,
            &request,
            &config(),
            |_| Ok(simulation(true)),
        )
        .unwrap();

        assert!(second.report.accepted);
        assert!(second.reused_existing_intent);
        let _ = std::fs::remove_file(path);
    }
}
