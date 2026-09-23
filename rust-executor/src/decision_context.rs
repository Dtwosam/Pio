use crate::execution_store::{ExecutionIntentStatus, ExecutionIntentStore};
use anyhow::Result;
use serde::{Deserialize, Serialize};


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionDecisionContext {
    pub decision_id: String,
    pub mode: String,
    pub action: String,
    pub pool_address: String,
    pub status: ExecutionIntentStatus,
    pub created_at_unix: u64,
    pub updated_at_unix: u64,
    pub capital_quote: f64,
    pub account_equity_quote: f64,
    pub portfolio_deployed_quote: f64,
    pub daily_drawdown_pct: f64,
    pub min_bin_id: i32,
    pub max_bin_id: i32,
    pub strategy: String,
    pub expected_net_return_pct: f64,
    pub expected_downside_pct: f64,
    pub model_version: String,
    pub data_age_seconds: u64,
    pub signature: Option<String>,
}


pub fn export_execution_decision_context(
    store: &ExecutionIntentStore,
    decision_id: &str,
) -> Result<ExecutionDecisionContext> {
    let record = store.load(decision_id)?;
    let request = store.load_request(decision_id)?;
    let proposal = request.proposal;

    Ok(ExecutionDecisionContext {
        decision_id: record.decision_id,
        mode: record.mode,
        action: record.action,
        pool_address: record.pool_address,
        status: record.status,
        created_at_unix: record.created_at_unix,
        updated_at_unix: record.updated_at_unix,
        capital_quote: proposal.capital_quote,
        account_equity_quote: proposal.account_equity_quote,
        portfolio_deployed_quote: proposal.portfolio_deployed_quote,
        daily_drawdown_pct: proposal.daily_drawdown_pct,
        min_bin_id: proposal.min_bin_id,
        max_bin_id: proposal.max_bin_id,
        strategy: proposal.strategy,
        expected_net_return_pct: proposal.expected_net_return_pct,
        expected_downside_pct: proposal.expected_downside_pct,
        model_version: proposal.model_version,
        data_age_seconds: proposal.data_age_seconds,
        signature: record.signature,
    })
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::dry_run::DryRunExecutionRequest;
    use crate::models::{Action, Mode, TradeProposal};
    use crate::risk::RiskConfig;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn db_path() -> PathBuf {
        std::env::temp_dir().join(format!(
            "pio-decision-context-{}.db",
            Uuid::new_v4()
        ))
    }

    #[test]
    fn exports_original_proposal_context_from_journal() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let decision_id = Uuid::new_v4();
        let request = DryRunExecutionRequest {
            proposal: TradeProposal {
                decision_id,
                mode: Mode::Live,
                action: Action::Enter,
                pool_address: "pool".into(),
                capital_quote: 25.0,
                account_equity_quote: 1000.0,
                portfolio_deployed_quote: 100.0,
                daily_drawdown_pct: 0.5,
                min_bin_id: -5,
                max_bin_id: 5,
                strategy: "CURVE".into(),
                expected_net_return_pct: 2.5,
                expected_downside_pct: 1.0,
                model_version: "model-v1".into(),
                data_age_seconds: 7,
            },
            transaction_base64: "tx".into(),
        };
        let config = RiskConfig {
            max_capital_per_position_pct: 5.0,
            max_total_deployed_pct: 20.0,
            max_daily_drawdown_pct: 3.0,
            min_expected_edge_pct: 0.25,
            max_expected_downside_pct: 2.0,
            max_data_age_seconds: 30,
        };
        store.register(&request, &config).unwrap();

        let context = export_execution_decision_context(
            &store,
            &decision_id.to_string(),
        )
        .unwrap();

        assert_eq!(context.decision_id, decision_id.to_string());
        assert_eq!(context.mode, "LIVE");
        assert_eq!(context.action, "ENTER");
        assert_eq!(context.strategy, "CURVE");
        assert_eq!(context.model_version, "model-v1");
        assert_eq!(context.capital_quote, 25.0);
        assert_eq!(context.min_bin_id, -5);
        assert_eq!(context.max_bin_id, 5);

        let _ = std::fs::remove_file(path);
    }
}
