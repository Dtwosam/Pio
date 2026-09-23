use crate::dry_run::DryRunExecutionRequest;
use crate::execution_guard::{check_proposal, RiskCheckReport};
use crate::risk::RiskConfig;
use crate::simulation::SimulationReport;
use crate::transaction_guard::{
    check_transaction, TransactionGuardConfig, TransactionGuardReport,
};
use anyhow::Result;
use serde::{Deserialize, Serialize};


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionPreflightReport {
    pub accepted: bool,
    pub stage: String,
    pub reason: String,
    pub risk: RiskCheckReport,
    pub transaction: Option<TransactionGuardReport>,
    pub simulation: Option<SimulationReport>,
}


pub fn evaluate_preflight_with<F>(
    request: &DryRunExecutionRequest,
    risk_config: &RiskConfig,
    transaction_config: &TransactionGuardConfig,
    simulate: F,
) -> Result<ExecutionPreflightReport>
where
    F: FnOnce(&str) -> Result<SimulationReport>,
{
    let risk = check_proposal(&request.proposal, risk_config);
    if !risk.accepted {
        return Ok(ExecutionPreflightReport {
            accepted: false,
            stage: "RISK".into(),
            reason: risk.reason.clone(),
            risk,
            transaction: None,
            simulation: None,
        });
    }

    let transaction = check_transaction(
        &request.proposal,
        &request.transaction_base64,
        transaction_config,
    )?;
    if !transaction.accepted {
        return Ok(ExecutionPreflightReport {
            accepted: false,
            stage: "TRANSACTION".into(),
            reason: transaction.reason.clone(),
            risk,
            transaction: Some(transaction),
            simulation: None,
        });
    }

    let simulation = simulate(&request.transaction_base64)?;
    if !simulation.succeeded {
        return Ok(ExecutionPreflightReport {
            accepted: false,
            stage: "SIMULATION".into(),
            reason: "transaction_simulation_failed".into(),
            risk,
            transaction: Some(transaction),
            simulation: Some(simulation),
        });
    }

    Ok(ExecutionPreflightReport {
        accepted: true,
        stage: "PREFLIGHT".into(),
        reason: "risk_transaction_and_simulation_passed".into(),
        risk,
        transaction: Some(transaction),
        simulation: Some(simulation),
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
    use uuid::Uuid;

    fn encoded_transaction(
        payer: Pubkey,
        pool: Pubkey,
        program: Pubkey,
    ) -> String {
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
        general_purpose::STANDARD.encode(bincode::serialize(&transaction).unwrap())
    }

    fn request(
        payer: Pubkey,
        pool: Pubkey,
        program: Pubkey,
    ) -> DryRunExecutionRequest {
        DryRunExecutionRequest {
            proposal: TradeProposal {
                decision_id: Uuid::new_v4(),
                mode: Mode::Live,
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
            transaction_base64: encoded_transaction(payer, pool, program),
        }
    }

    fn risk_config() -> RiskConfig {
        RiskConfig {
            max_capital_per_position_pct: 2.0,
            max_total_deployed_pct: 20.0,
            max_daily_drawdown_pct: 3.0,
            min_expected_edge_pct: 0.25,
            max_expected_downside_pct: 2.0,
            max_data_age_seconds: 30,
        }
    }

    fn transaction_config(
        payer: Pubkey,
        program: Pubkey,
    ) -> TransactionGuardConfig {
        TransactionGuardConfig {
            expected_fee_payer: payer.to_string(),
            allowed_program_ids: vec![program.to_string()],
            max_instructions: 4,
            max_static_accounts: 16,
            allow_address_lookup_tables: false,
            require_unsigned: true,
            require_proposal_pool_account: true,
            required_account_pubkeys: vec![],
            require_instruction_policy: false,
            instruction_policies: vec![],
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
                    json!("failed")
                }
            }),
        }
    }

    #[test]
    fn risk_rejection_stops_before_transaction_and_simulation() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let mut request = request(payer, pool, program);
        request.proposal.mode = Mode::Paper;
        let called = Cell::new(false);

        let report = evaluate_preflight_with(
            &request,
            &risk_config(),
            &transaction_config(payer, program),
            |_| {
                called.set(true);
                Ok(simulation(true))
            },
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.stage, "RISK");
        assert!(report.transaction.is_none());
        assert!(!called.get());
    }

    #[test]
    fn transaction_rejection_stops_before_simulation() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let unexpected_program = Pubkey::new_unique();
        let request = request(payer, pool, program);
        let called = Cell::new(false);

        let report = evaluate_preflight_with(
            &request,
            &risk_config(),
            &transaction_config(payer, unexpected_program),
            |_| {
                called.set(true);
                Ok(simulation(true))
            },
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.stage, "TRANSACTION");
        assert_eq!(
            report.transaction.unwrap().reason,
            "transaction_uses_unapproved_program"
        );
        assert!(!called.get());
    }

    #[test]
    fn simulation_failure_blocks_preflight() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let request = request(payer, pool, program);

        let report = evaluate_preflight_with(
            &request,
            &risk_config(),
            &transaction_config(payer, program),
            |_| Ok(simulation(false)),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.stage, "SIMULATION");
    }

    #[test]
    fn all_three_gates_must_pass() {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let request = request(payer, pool, program);

        let report = evaluate_preflight_with(
            &request,
            &risk_config(),
            &transaction_config(payer, program),
            |_| Ok(simulation(true)),
        )
        .unwrap();

        assert!(report.accepted);
        assert_eq!(report.stage, "PREFLIGHT");
        assert!(report.transaction.unwrap().accepted);
    }
}
