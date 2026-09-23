use crate::blockhash::PreparedUnsignedTransaction;
use crate::dry_run::DryRunExecutionRequest;
use crate::execution_guard::{check_proposal, RiskCheckReport};
use crate::risk::RiskConfig;
use crate::simulation::SimulationReport;
use crate::transaction_guard::{
    check_transaction, TransactionGuardConfig, TransactionGuardReport,
};
use crate::wallet_guard::{authorize_wallet, WalletAuthorizationReport};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use solana_sdk::pubkey::Pubkey;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FinalPreSignReport {
    pub accepted: bool,
    pub stage: String,
    pub reason: String,
    pub risk: RiskCheckReport,
    pub prepared: Option<PreparedUnsignedTransaction>,
    pub transaction: Option<TransactionGuardReport>,
    pub wallet: Option<WalletAuthorizationReport>,
    pub simulation: Option<SimulationReport>,
}


pub fn evaluate_final_presign_with<P, S>(
    request: &DryRunExecutionRequest,
    risk_config: &RiskConfig,
    transaction_config: &TransactionGuardConfig,
    wallet_pubkey: &Pubkey,
    prepare: P,
    simulate_exact: S,
) -> Result<FinalPreSignReport>
where
    P: FnOnce(&str) -> Result<PreparedUnsignedTransaction>,
    S: FnOnce(&str) -> Result<SimulationReport>,
{
    let risk = check_proposal(&request.proposal, risk_config);
    if !risk.accepted {
        return Ok(FinalPreSignReport {
            accepted: false,
            stage: "RISK".into(),
            reason: risk.reason.clone(),
            risk,
            prepared: None,
            transaction: None,
            wallet: None,
            simulation: None,
        });
    }

    let prepared = prepare(&request.transaction_base64)?;
    if !prepared.signatures_all_default {
        anyhow::bail!("prepared transaction unexpectedly contains signatures");
    }

    let transaction = check_transaction(
        &request.proposal,
        &prepared.transaction_base64,
        transaction_config,
    )?;
    if !transaction.accepted {
        return Ok(FinalPreSignReport {
            accepted: false,
            stage: "TRANSACTION".into(),
            reason: transaction.reason.clone(),
            risk,
            prepared: Some(prepared),
            transaction: Some(transaction),
            wallet: None,
            simulation: None,
        });
    }

    let wallet = authorize_wallet(wallet_pubkey, &transaction)?;
    if !wallet.accepted {
        return Ok(FinalPreSignReport {
            accepted: false,
            stage: "WALLET".into(),
            reason: wallet.reason.clone(),
            risk,
            prepared: Some(prepared),
            transaction: Some(transaction),
            wallet: Some(wallet),
            simulation: None,
        });
    }

    let simulation = simulate_exact(&prepared.transaction_base64)?;
    if !simulation.succeeded {
        return Ok(FinalPreSignReport {
            accepted: false,
            stage: "SIMULATION".into(),
            reason: "exact_presign_simulation_failed".into(),
            risk,
            prepared: Some(prepared),
            transaction: Some(transaction),
            wallet: Some(wallet),
            simulation: Some(simulation),
        });
    }

    Ok(FinalPreSignReport {
        accepted: true,
        stage: "PRESIGN_READY".into(),
        reason: "risk_transaction_wallet_and_exact_simulation_passed".into(),
        risk,
        prepared: Some(prepared),
        transaction: Some(transaction),
        wallet: Some(wallet),
        simulation: Some(simulation),
    })
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode, TradeProposal};
    use crate::transaction_guard::ProgramInstructionPolicy;
    use base64::{engine::general_purpose, Engine as _};
    use serde_json::json;
    use solana_sdk::hash::Hash;
    use solana_sdk::instruction::{AccountMeta, Instruction};
    use solana_sdk::message::{Message, VersionedMessage};
    use solana_sdk::signature::Signature;
    use solana_sdk::transaction::VersionedTransaction;
    use std::cell::Cell;
    use uuid::Uuid;

    fn fixture() -> (
        DryRunExecutionRequest,
        RiskConfig,
        TransactionGuardConfig,
        Pubkey,
    ) {
        let payer = Pubkey::new_unique();
        let pool = Pubkey::new_unique();
        let program = Pubkey::new_unique();
        let instruction = Instruction {
            program_id: program,
            accounts: vec![AccountMeta::new_readonly(pool, false)],
            data: vec![1, 2, 3],
        };
        let mut message = Message::new(&[instruction], Some(&payer));
        message.recent_blockhash = Hash::default();
        let tx = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::Legacy(message),
        };
        let transaction_base64 =
            general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());
        let request = DryRunExecutionRequest {
            proposal: TradeProposal {
                decision_id: Uuid::new_v4(),
                mode: Mode::Live,
                action: Action::Enter,
                pool_address: pool.to_string(),
                capital_quote: 10.0,
                account_equity_quote: 1_000.0,
                portfolio_deployed_quote: 100.0,
                daily_drawdown_pct: 0.5,
                min_bin_id: -1,
                max_bin_id: 1,
                strategy: "SPOT".into(),
                expected_net_return_pct: 1.0,
                expected_downside_pct: 0.5,
                model_version: "baseline".into(),
                data_age_seconds: 1,
            },
            transaction_base64: transaction_base64.clone(),
        };
        let risk = RiskConfig {
            max_capital_per_position_pct: 2.0,
            max_total_deployed_pct: 20.0,
            max_daily_drawdown_pct: 3.0,
            min_expected_edge_pct: 0.25,
            max_expected_downside_pct: 2.0,
            max_data_age_seconds: 30,
        };
        let guard = TransactionGuardConfig {
            expected_fee_payer: payer.to_string(),
            allowed_program_ids: vec![program.to_string()],
            max_instructions: 2,
            max_static_accounts: 8,
            allow_address_lookup_tables: false,
            require_unsigned: true,
            require_instruction_policy: true,
            instruction_policies: vec![ProgramInstructionPolicy {
                program_id: program.to_string(),
                allowed_actions: vec![Action::Enter],
                allowed_data_prefixes_hex: vec!["0102".into()],
            }],
        };
        (request, risk, guard, payer)
    }

    fn prepared(encoded: &str) -> PreparedUnsignedTransaction {
        PreparedUnsignedTransaction {
            transaction_base64: encoded.to_string(),
            recent_blockhash: Hash::new_unique().to_string(),
            last_valid_block_height: 123,
            rpc_context_slot: 99,
            signatures_all_default: true,
        }
    }

    fn simulation(succeeded: bool) -> SimulationReport {
        SimulationReport {
            succeeded,
            rpc_context_slot: 100,
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
    fn successful_final_presign_requires_all_gates() {
        let (request, risk, guard, payer) = fixture();
        let encoded = request.transaction_base64.clone();

        let report = evaluate_final_presign_with(
            &request,
            &risk,
            &guard,
            &payer,
            |_| Ok(prepared(&encoded)),
            |_| Ok(simulation(true)),
        )
        .unwrap();

        assert!(report.accepted);
        assert_eq!(report.stage, "PRESIGN_READY");
        assert!(report.wallet.unwrap().accepted);
    }

    #[test]
    fn wrong_wallet_stops_before_exact_simulation() {
        let (request, risk, guard, _) = fixture();
        let encoded = request.transaction_base64.clone();
        let called = Cell::new(false);

        let report = evaluate_final_presign_with(
            &request,
            &risk,
            &guard,
            &Pubkey::new_unique(),
            |_| Ok(prepared(&encoded)),
            |_| {
                called.set(true);
                Ok(simulation(true))
            },
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.stage, "WALLET");
        assert!(!called.get());
    }

    #[test]
    fn exact_simulation_failure_blocks_presign_readiness() {
        let (request, risk, guard, payer) = fixture();
        let encoded = request.transaction_base64.clone();

        let report = evaluate_final_presign_with(
            &request,
            &risk,
            &guard,
            &payer,
            |_| Ok(prepared(&encoded)),
            |_| Ok(simulation(false)),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.stage, "SIMULATION");
    }
}
