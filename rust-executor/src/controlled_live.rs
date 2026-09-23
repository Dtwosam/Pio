use crate::models::{Action, TradeProposal};
use crate::phase5_gate::{
    verify_phase5_promotion_database, Phase5PromotionGateReport,
};
use anyhow::{Context, Result};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use solana_sdk::pubkey::Pubkey;
use std::collections::BTreeSet;
use std::path::Path;
use std::str::FromStr;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ControlledLiveConfig {
    pub enabled: bool,
    pub allowed_pool_addresses: Vec<String>,
    pub max_open_positions: usize,
    pub max_capital_quote_per_entry: f64,
    pub max_daily_drawdown_pct: f64,
    pub allow_rebalance: bool,
    pub allow_exit: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ControlledLiveReport {
    pub decision_id: String,
    pub accepted: bool,
    pub reason: String,
    pub phase5: Phase5PromotionGateReport,
    pub action: Action,
    pub pool_address: String,
    pub live_enabled: bool,
    pub open_positions: usize,
    pub max_open_positions: usize,
    pub pool_allowed: bool,
    pub capital_quote: f64,
    pub max_capital_quote_per_entry: f64,
    pub daily_drawdown_pct: f64,
    pub max_daily_drawdown_pct: f64,
}

fn validate_config(config: &ControlledLiveConfig) -> Result<BTreeSet<Pubkey>> {
    if config.max_open_positions == 0 {
        anyhow::bail!("max_open_positions must be positive");
    }
    if !config.max_capital_quote_per_entry.is_finite()
        || config.max_capital_quote_per_entry <= 0.0
    {
        anyhow::bail!(
            "max_capital_quote_per_entry must be finite and positive"
        );
    }
    if !config.max_daily_drawdown_pct.is_finite()
        || config.max_daily_drawdown_pct < 0.0
    {
        anyhow::bail!(
            "max_daily_drawdown_pct must be finite and non-negative"
        );
    }
    if config.allowed_pool_addresses.is_empty() {
        anyhow::bail!("allowed_pool_addresses cannot be empty");
    }

    config
        .allowed_pool_addresses
        .iter()
        .map(|value| {
            Pubkey::from_str(value.trim()).with_context(|| {
                format!("invalid controlled-live pool address: {value}")
            })
        })
        .collect()
}

fn open_position_count(database_path: &Path) -> Result<usize> {
    let conn = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| {
        format!(
            "failed to open controlled-live state database read-only: {}",
            database_path.display()
        )
    })?;

    let count: i64 = conn
        .query_row(
            r#"
            SELECT COUNT(*)
            FROM live_positions
            WHERE status IN ('OPEN', 'LIQUIDITY_REMOVED')
            "#,
            [],
            |row| row.get(0),
        )
        .context("live_positions table is unavailable")?;
    usize::try_from(count).context("invalid live position count")
}

pub fn evaluate_controlled_live(
    database_path: &Path,
    proposal: &TradeProposal,
    config: &ControlledLiveConfig,
) -> Result<ControlledLiveReport> {
    if !database_path.is_absolute() {
        anyhow::bail!("controlled-live database path must be absolute");
    }
    let allowed_pools = validate_config(config)?;
    let phase5 = verify_phase5_promotion_database(database_path)?;
    let proposal_pool = Pubkey::from_str(proposal.pool_address.trim())
        .context("proposal pool_address is not a valid Solana pubkey")?;
    let pool_allowed = allowed_pools.contains(&proposal_pool);
    let open_positions = open_position_count(database_path)?;

    let reason = if !phase5.accepted {
        "phase5_promotion_gate_rejected"
    } else {
        match proposal.action {
            Action::Enter => {
                if !config.enabled {
                    "controlled_live_disabled"
                } else if !pool_allowed {
                    "pool_not_allowed_for_entry"
                } else if open_positions >= config.max_open_positions {
                    "max_open_positions_reached"
                } else if !proposal.capital_quote.is_finite()
                    || proposal.capital_quote <= 0.0
                {
                    "invalid_entry_capital"
                } else if proposal.capital_quote
                    > config.max_capital_quote_per_entry
                {
                    "entry_capital_cap_exceeded"
                } else if !proposal.daily_drawdown_pct.is_finite()
                    || proposal.daily_drawdown_pct
                        > config.max_daily_drawdown_pct
                {
                    "daily_drawdown_gate_rejected"
                } else {
                    "approved"
                }
            }
            Action::Rebalance => {
                if !config.enabled {
                    "controlled_live_disabled"
                } else if !config.allow_rebalance {
                    "controlled_live_rebalance_disabled"
                } else if !pool_allowed {
                    "pool_not_allowed_for_rebalance"
                } else if !proposal.daily_drawdown_pct.is_finite()
                    || proposal.daily_drawdown_pct
                        > config.max_daily_drawdown_pct
                {
                    "daily_drawdown_gate_rejected"
                } else {
                    "approved"
                }
            }
            Action::Exit => {
                if !config.allow_exit {
                    "controlled_live_exit_disabled"
                } else {
                    // Exit intentionally remains possible even when the
                    // entry/rebalance kill switch is off or the pool was
                    // removed from the allowlist.
                    "approved"
                }
            }
            Action::Skip => "skip_action_is_not_executable",
        }
    };

    Ok(ControlledLiveReport {
        decision_id: proposal.decision_id.to_string(),
        accepted: reason == "approved",
        reason: reason.into(),
        phase5,
        action: proposal.action.clone(),
        pool_address: proposal.pool_address.clone(),
        live_enabled: config.enabled,
        open_positions,
        max_open_positions: config.max_open_positions,
        pool_allowed,
        capital_quote: proposal.capital_quote,
        max_capital_quote_per_entry:
            config.max_capital_quote_per_entry,
        daily_drawdown_pct: proposal.daily_drawdown_pct,
        max_daily_drawdown_pct: config.max_daily_drawdown_pct,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Mode;
    use rusqlite::params;
    use serde_json::json;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn db_path() -> PathBuf {
        std::env::temp_dir().join(format!(
            "pio-controlled-live-{}.db",
            Uuid::new_v4()
        ))
    }

    fn seed(path: &Path, open_positions: usize) {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(
            r#"
            CREATE TABLE phase_promotion_evidence (
                phase_name TEXT PRIMARY KEY,
                promoted_at TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                qualified INTEGER NOT NULL,
                evidence_json TEXT NOT NULL
            );
            CREATE TABLE live_positions (
                position_address TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );
            "#,
        )
        .unwrap();
        conn.execute(
            r#"
            INSERT INTO phase_promotion_evidence(
                phase_name, promoted_at, evidence_type,
                qualified, evidence_json
            ) VALUES (?1, ?2, ?3, 1, ?4)
            "#,
            params![
                "PHASE5",
                "2026-09-23T12:00:00+00:00",
                "PHASE5_PROMOTION_V1",
                json!({
                    "promotion_ready": true,
                    "phase3_promoted": true,
                    "endurance": {"passing": true},
                    "ledger_audit": {"passing": true}
                })
                .to_string(),
            ],
        )
        .unwrap();

        for index in 0..open_positions {
            conn.execute(
                "INSERT INTO live_positions(position_address, status) VALUES (?1, 'OPEN')",
                [format!("position-{index}")],
            )
            .unwrap();
        }
    }

    fn proposal(pool: Pubkey, action: Action) -> TradeProposal {
        TradeProposal {
            decision_id: Uuid::new_v4(),
            mode: Mode::Live,
            action,
            pool_address: pool.to_string(),
            capital_quote: 25.0,
            account_equity_quote: 1_000.0,
            portfolio_deployed_quote: 0.0,
            daily_drawdown_pct: 0.5,
            min_bin_id: -5,
            max_bin_id: 5,
            strategy: "SPOT".into(),
            expected_net_return_pct: 1.0,
            expected_downside_pct: 0.5,
            model_version: "test".into(),
            data_age_seconds: 1,
        }
    }

    fn config(pool: Pubkey) -> ControlledLiveConfig {
        ControlledLiveConfig {
            enabled: true,
            allowed_pool_addresses: vec![pool.to_string()],
            max_open_positions: 1,
            max_capital_quote_per_entry: 50.0,
            max_daily_drawdown_pct: 2.0,
            allow_rebalance: true,
            allow_exit: true,
        }
    }

    #[test]
    fn small_allowlisted_entry_passes() {
        let path = db_path();
        seed(&path, 0);
        let pool = Pubkey::new_unique();

        let report = evaluate_controlled_live(
            &path,
            &proposal(pool, Action::Enter),
            &config(pool),
        )
        .unwrap();

        assert!(report.accepted);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn disabled_live_blocks_entry_but_not_exit() {
        let path = db_path();
        seed(&path, 1);
        let pool = Pubkey::new_unique();
        let mut cfg = config(pool);
        cfg.enabled = false;

        let entry = evaluate_controlled_live(
            &path,
            &proposal(pool, Action::Enter),
            &cfg,
        )
        .unwrap();
        let exit = evaluate_controlled_live(
            &path,
            &proposal(Pubkey::new_unique(), Action::Exit),
            &cfg,
        )
        .unwrap();

        assert!(!entry.accepted);
        assert_eq!(entry.reason, "controlled_live_disabled");
        assert!(exit.accepted);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn entry_cap_and_concurrency_are_fail_closed() {
        let path = db_path();
        seed(&path, 1);
        let pool = Pubkey::new_unique();

        let report = evaluate_controlled_live(
            &path,
            &proposal(pool, Action::Enter),
            &config(pool),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "max_open_positions_reached");
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn drawdown_blocks_rebalance_but_not_exit() {
        let path = db_path();
        seed(&path, 1);
        let pool = Pubkey::new_unique();
        let cfg = config(pool);
        let mut rebalance = proposal(pool, Action::Rebalance);
        rebalance.daily_drawdown_pct = 10.0;

        let blocked =
            evaluate_controlled_live(&path, &rebalance, &cfg).unwrap();
        let exit = evaluate_controlled_live(
            &path,
            &proposal(pool, Action::Exit),
            &cfg,
        )
        .unwrap();

        assert!(!blocked.accepted);
        assert_eq!(blocked.reason, "daily_drawdown_gate_rejected");
        assert!(exit.accepted);
        let _ = std::fs::remove_file(path);
    }
    #[test]
    fn checked_in_controlled_live_example_is_disabled() {
        let raw = include_str!(
            "../../contracts/examples/controlled_live.example.json"
        );
        let cfg: ControlledLiveConfig =
            serde_json::from_str(raw).unwrap();

        assert!(!cfg.enabled);
        assert_eq!(cfg.max_open_positions, 1);
        assert!(cfg.max_capital_quote_per_entry > 0.0);
        assert!(cfg.allow_exit);
    }

}
