use crate::execution_guard::{check_proposal, RiskCheckReport};
use crate::models::TradeProposal;
use crate::risk::RiskConfig;
use crate::simulation::SimulationReport;
use anyhow::Result;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DryRunExecutionRequest {
    pub proposal: TradeProposal,
    pub transaction_base64: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DryRunExecutionReport {
    pub accepted: bool,
    pub stage: String,
    pub reason: String,
    pub risk: RiskCheckReport,
    pub simulation: Option<SimulationReport>,
}

pub fn evaluate_dry_run_with<F>(
    request: &DryRunExecutionRequest,
    config: &RiskConfig,
    simulate: F,
) -> Result<DryRunExecutionReport>
where
    F: FnOnce(&str) -> Result<SimulationReport>,
{
    let risk = check_proposal(&request.proposal, config);
    if !risk.accepted {
        return Ok(DryRunExecutionReport {
            accepted: false,
            stage: "RISK".into(),
            reason: risk.reason.clone(),
            risk,
            simulation: None,
        });
    }

    let simulation = simulate(&request.transaction_base64)?;
    if !simulation.succeeded {
        return Ok(DryRunExecutionReport {
            accepted: false,
            stage: "SIMULATION".into(),
            reason: "transaction_simulation_failed".into(),
            risk,
            simulation: Some(simulation),
        });
    }

    Ok(DryRunExecutionReport {
        accepted: true,
        stage: "SIMULATED".into(),
        reason: "risk_and_simulation_passed".into(),
        risk,
        simulation: Some(simulation),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode};
    use serde_json::json;
    use std::cell::Cell;
    use uuid::Uuid;

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
            transaction_base64: "unused".into(),
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
            rpc_context_slot: 123,
            result: json!({"err": if succeeded { serde_json::Value::Null } else { json!("boom") }}),
        }
    }

    #[test]
    fn rejected_risk_never_calls_simulation() {
        let called = Cell::new(false);
        let report = evaluate_dry_run_with(
            &request(Mode::Paper),
            &config(),
            |_| {
                called.set(true);
                Ok(simulation(true))
            },
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.stage, "RISK");
        assert!(!called.get());
    }

    #[test]
    fn failed_simulation_blocks_dry_run() {
        let report = evaluate_dry_run_with(
            &request(Mode::Live),
            &config(),
            |_| Ok(simulation(false)),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.stage, "SIMULATION");
    }

    #[test]
    fn risk_and_simulation_must_both_pass() {
        let report = evaluate_dry_run_with(
            &request(Mode::Live),
            &config(),
            |_| Ok(simulation(true)),
        )
        .unwrap();

        assert!(report.accepted);
        assert_eq!(report.stage, "SIMULATED");
    }
}
