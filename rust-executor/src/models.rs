use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Mode {
    Backtest,
    Paper,
    Live,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Action {
    Skip,
    Enter,
    Rebalance,
    Exit,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TradeProposal {
    pub decision_id: Uuid,
    pub mode: Mode,
    pub action: Action,
    pub pool_address: String,

    // Portfolio snapshot in quote-currency units.
    pub capital_quote: f64,
    pub account_equity_quote: f64,
    pub portfolio_deployed_quote: f64,
    pub daily_drawdown_pct: f64,

    pub min_bin_id: i32,
    pub max_bin_id: i32,
    pub strategy: String,

    // Percent units: 0.25 means 0.25%, not 25%.
    pub expected_net_return_pct: f64,
    pub expected_downside_pct: f64,

    pub model_version: String,
    pub data_age_seconds: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionResult {
    pub decision_id: Uuid,
    pub accepted: bool,
    pub mode: Mode,
    pub reason: String,
    pub signature: Option<String>,
}


#[cfg(test)]
mod contract_tests {
    use super::*;
    use crate::risk::RiskConfig;

    #[test]
    fn shared_trade_proposal_fixture_deserializes() {
        let raw = include_str!(
            "../../contracts/examples/trade_proposal.live.example.json"
        );
        let proposal: TradeProposal = serde_json::from_str(raw).unwrap();
        assert_eq!(proposal.mode, Mode::Live);
        assert_eq!(proposal.action, Action::Enter);
        assert_eq!(proposal.strategy, "SPOT");
        assert_eq!(proposal.data_age_seconds, 5);
    }

    #[test]
    fn shared_risk_config_fixture_deserializes() {
        let raw = include_str!(
            "../../contracts/examples/risk_config.example.json"
        );
        let config: RiskConfig = serde_json::from_str(raw).unwrap();
        assert_eq!(config.max_capital_per_position_pct, 2.0);
        assert_eq!(config.max_data_age_seconds, 30);
    }
}
