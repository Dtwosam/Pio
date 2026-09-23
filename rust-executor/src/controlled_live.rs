use crate::execution_store::ExecutionIntentStore;
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
    pub max_rebalances_per_position: usize,
    pub max_capital_quote_per_entry: f64,
    pub max_daily_entry_capital_quote: f64,
    pub max_daily_realized_loss_quote: f64,
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
    pub unresolved_entry_intents: usize,
    pub effective_open_positions: usize,
    pub matching_pool_positions: usize,
    pub matching_pool_rebalances: usize,
    pub max_open_positions: usize,
    pub max_rebalances_per_position: usize,
    pub pool_allowed: bool,
    pub capital_quote: f64,
    pub max_capital_quote_per_entry: f64,
    pub daily_submitted_entry_capital_quote: f64,
    pub max_daily_entry_capital_quote: f64,
    pub daily_realized_loss_quote: f64,
    pub max_daily_realized_loss_quote: f64,
    pub unvalued_closed_positions_today: usize,
    pub daily_drawdown_pct: f64,
    pub max_daily_drawdown_pct: f64,
}

fn validate_config(config: &ControlledLiveConfig) -> Result<BTreeSet<Pubkey>> {
    if config.max_open_positions == 0 {
        anyhow::bail!("max_open_positions must be positive");
    }
    if config.max_rebalances_per_position == 0 {
        anyhow::bail!("max_rebalances_per_position must be positive");
    }
    if !config.max_capital_quote_per_entry.is_finite()
        || config.max_capital_quote_per_entry <= 0.0
    {
        anyhow::bail!(
            "max_capital_quote_per_entry must be finite and positive"
        );
    }
    if !config.max_daily_entry_capital_quote.is_finite()
        || config.max_daily_entry_capital_quote <= 0.0
    {
        anyhow::bail!(
            "max_daily_entry_capital_quote must be finite and positive"
        );
    }
    if config.max_daily_entry_capital_quote
        < config.max_capital_quote_per_entry
    {
        anyhow::bail!(
            "max_daily_entry_capital_quote cannot be below the per-entry cap"
        );
    }
    if !config.max_daily_realized_loss_quote.is_finite()
        || config.max_daily_realized_loss_quote <= 0.0
    {
        anyhow::bail!(
            "max_daily_realized_loss_quote must be finite and positive"
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

fn daily_realized_loss_state(
    database_path: &Path,
    as_of: Option<&str>,
) -> Result<(f64, usize)> {
    let conn = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| {
        format!(
            "failed to open controlled-live loss database read-only: {}",
            database_path.display()
        )
    })?;

    let mut statement = conn
        .prepare(
            r#"
            SELECT o.position_address, v.realized_pnl_quote
            FROM live_position_outcomes o
            LEFT JOIN live_position_valuations v
              ON v.position_address = o.position_address
            WHERE date(o.created_at) = date(COALESCE(?1, 'now'))
            ORDER BY o.position_address ASC
            "#,
        )
        .context(
            "live outcome/valuation tables are unavailable for daily loss gate",
        )?;
    let rows = statement.query_map([as_of], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?,
        ))
    })?;

    let mut realized_loss_quote = 0.0_f64;
    let mut unvalued = 0_usize;
    for row in rows {
        let (position_address, realized_pnl_quote) = row?;
        let Some(raw_pnl) = realized_pnl_quote else {
            unvalued = unvalued
                .checked_add(1)
                .context("unvalued closed-position count overflow")?;
            continue;
        };
        let pnl: f64 = raw_pnl.parse().with_context(|| {
            format!(
                "invalid realized_pnl_quote for live position {position_address}"
            )
        })?;
        if !pnl.is_finite() {
            anyhow::bail!(
                "non-finite realized_pnl_quote for live position {position_address}"
            );
        }
        if pnl < 0.0 {
            realized_loss_quote += -pnl;
            if !realized_loss_quote.is_finite() {
                anyhow::bail!("daily realized live loss overflow");
            }
        }
    }
    Ok((realized_loss_quote, unvalued))
}

fn live_position_counts(
    database_path: &Path,
    pool_address: &str,
) -> Result<(usize, usize, usize)> {
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

    let total: i64 = conn
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
    let (matching, matching_rebalances): (i64, i64) = conn
        .query_row(
            r#"
            SELECT COUNT(*), COALESCE(MAX(rebalances), 0)
            FROM live_positions
            WHERE pool_address = ?1
              AND status IN ('OPEN', 'LIQUIDITY_REMOVED')
            "#,
            [pool_address],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .context("failed to inspect live positions for proposal pool")?;

    Ok((
        usize::try_from(total).context("invalid live position count")?,
        usize::try_from(matching)
            .context("invalid matching live position count")?,
        usize::try_from(matching_rebalances)
            .context("invalid matching live position rebalance count")?,
    ))
}

pub fn evaluate_controlled_live(
    database_path: &Path,
    proposal: &TradeProposal,
    config: &ControlledLiveConfig,
) -> Result<ControlledLiveReport> {
    evaluate_controlled_live_at(
        database_path,
        proposal,
        config,
        None,
    )
}

fn evaluate_controlled_live_at(
    database_path: &Path,
    proposal: &TradeProposal,
    config: &ControlledLiveConfig,
    as_of: Option<&str>,
) -> Result<ControlledLiveReport> {
    if !database_path.is_absolute() {
        anyhow::bail!("controlled-live database path must be absolute");
    }
    let allowed_pools = validate_config(config)?;
    let phase5 = verify_phase5_promotion_database(database_path)?;
    let proposal_pool = Pubkey::from_str(proposal.pool_address.trim())
        .context("proposal pool_address is not a valid Solana pubkey")?;
    let pool_allowed = allowed_pools.contains(&proposal_pool);
    let (
        open_positions,
        matching_pool_positions,
        matching_pool_rebalances,
    ) = live_position_counts(database_path, &proposal.pool_address)?;
    let (daily_realized_loss_quote, unvalued_closed_positions_today) =
        if matches!(proposal.action, Action::Enter | Action::Rebalance) {
            daily_realized_loss_state(database_path, as_of)?
        } else {
            (0.0, 0)
        };

    let reason = if !phase5.accepted {
        "phase5_promotion_gate_rejected"
    } else if proposal.mode != crate::models::Mode::Live {
        "controlled_live_requires_live_mode"
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
                } else if unvalued_closed_positions_today > 0 {
                    "daily_realized_loss_evidence_incomplete"
                } else if daily_realized_loss_quote
                    >= config.max_daily_realized_loss_quote
                {
                    "daily_realized_loss_budget_reached"
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
                if matching_pool_positions == 0 {
                    "no_tracked_live_position_for_rebalance"
                } else if matching_pool_positions > 1 {
                    "ambiguous_tracked_live_positions_for_rebalance"
                } else if matching_pool_rebalances
                    >= config.max_rebalances_per_position
                {
                    "max_rebalances_per_position_reached"
                } else if !config.enabled {
                    "controlled_live_disabled"
                } else if !config.allow_rebalance {
                    "controlled_live_rebalance_disabled"
                } else if !pool_allowed {
                    "pool_not_allowed_for_rebalance"
                } else if unvalued_closed_positions_today > 0 {
                    "daily_realized_loss_evidence_incomplete"
                } else if daily_realized_loss_quote
                    >= config.max_daily_realized_loss_quote
                {
                    "daily_realized_loss_budget_reached"
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
                if matching_pool_positions == 0 {
                    "no_tracked_live_position_for_exit"
                } else if matching_pool_positions > 1 {
                    "ambiguous_tracked_live_positions_for_exit"
                } else if !config.allow_exit {
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
        unresolved_entry_intents: 0,
        effective_open_positions: open_positions,
        matching_pool_positions,
        matching_pool_rebalances,
        max_open_positions: config.max_open_positions,
        max_rebalances_per_position:
            config.max_rebalances_per_position,
        pool_allowed,
        capital_quote: proposal.capital_quote,
        max_capital_quote_per_entry:
            config.max_capital_quote_per_entry,
        daily_submitted_entry_capital_quote: 0.0,
        max_daily_entry_capital_quote:
            config.max_daily_entry_capital_quote,
        daily_realized_loss_quote,
        max_daily_realized_loss_quote:
            config.max_daily_realized_loss_quote,
        unvalued_closed_positions_today,
        daily_drawdown_pct: proposal.daily_drawdown_pct,
        max_daily_drawdown_pct: config.max_daily_drawdown_pct,
    })
}

fn reconciled_open_decision_ids(
    database_path: &Path,
) -> Result<BTreeSet<String>> {
    let conn = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| {
        format!(
            "failed to open live position reconciliation database read-only: {}",
            database_path.display()
        )
    })?;
    let mut statement = conn.prepare(
        r#"
        SELECT opened_decision_id
        FROM live_positions
        WHERE status IN ('OPEN', 'LIQUIDITY_REMOVED', 'CLOSED')
        "#,
    )?;
    let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
    let mut result = BTreeSet::new();
    for row in rows {
        result.insert(row?);
    }
    Ok(result)
}

pub fn evaluate_controlled_live_intent(
    database_path: &Path,
    execution_store: &ExecutionIntentStore,
    decision_id: &str,
    config: &ControlledLiveConfig,
) -> Result<ControlledLiveReport> {
    let request = execution_store.load_request(decision_id)?;
    if request.proposal.decision_id.to_string() != decision_id {
        anyhow::bail!(
            "persisted execution request decision_id does not match lookup key"
        );
    }
    let mut report = evaluate_controlled_live(
        database_path,
        &request.proposal,
        config,
    )?;

    let unresolved = execution_store
        .unresolved_live_entry_decision_ids(Some(decision_id))?;
    let daily_submitted_entry_capital_quote = execution_store
        .submitted_live_entry_capital_today(Some(decision_id))?;
    let reconciled = reconciled_open_decision_ids(database_path)?;
    let unresolved_entry_intents = unresolved
        .iter()
        .filter(|item| !reconciled.contains(*item))
        .count();
    let effective_open_positions = report
        .open_positions
        .checked_add(unresolved_entry_intents)
        .context("effective live position count overflow")?;

    report.unresolved_entry_intents = unresolved_entry_intents;
    report.effective_open_positions = effective_open_positions;
    report.daily_submitted_entry_capital_quote =
        daily_submitted_entry_capital_quote;

    if report.accepted
        && request.proposal.action == Action::Enter
        && daily_submitted_entry_capital_quote
            + request.proposal.capital_quote
            > config.max_daily_entry_capital_quote
    {
        report.accepted = false;
        report.reason = "daily_entry_capital_budget_exceeded".into();
    } else if report.accepted
        && request.proposal.action == Action::Enter
        && effective_open_positions >= config.max_open_positions
    {
        report.accepted = false;
        report.reason = "max_effective_open_positions_reached".into();
    }

    Ok(report)
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
                pool_address TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_decision_id TEXT,
                rebalances INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE live_position_outcomes (
                position_address TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE live_position_valuations (
                position_address TEXT PRIMARY KEY,
                realized_pnl_quote TEXT NOT NULL
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
                "INSERT INTO live_positions(position_address, pool_address, status) VALUES (?1, ?2, 'OPEN')",
                [
                    format!("position-{index}"),
                    Pubkey::default().to_string(),
                ],
            )
            .unwrap();
        }
    }

    fn add_live_position(path: &Path, pool: Pubkey) {
        add_live_position_with_rebalances(path, pool, 0);
    }

    fn add_live_position_with_rebalances(
        path: &Path,
        pool: Pubkey,
        rebalances: usize,
    ) {
        let conn = Connection::open(path).unwrap();
        conn.execute(
            "INSERT INTO live_positions(position_address, pool_address, status, rebalances) VALUES (?1, ?2, 'OPEN', ?3)",
            params![
                format!("tracked-{}", Uuid::new_v4()),
                pool.to_string(),
                i64::try_from(rebalances).unwrap(),
            ],
        )
        .unwrap();
    }

    fn add_daily_closed_outcome(
        path: &Path,
        position_address: &str,
        created_at: &str,
        realized_pnl_quote: Option<&str>,
    ) {
        let conn = Connection::open(path).unwrap();
        conn.execute(
            "INSERT INTO live_position_outcomes(position_address, created_at) VALUES (?1, ?2)",
            [position_address, created_at],
        )
        .unwrap();
        if let Some(pnl) = realized_pnl_quote {
            conn.execute(
                "INSERT INTO live_position_valuations(position_address, realized_pnl_quote) VALUES (?1, ?2)",
                [position_address, pnl],
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
            max_rebalances_per_position: 3,
            max_capital_quote_per_entry: 50.0,
            max_daily_entry_capital_quote: 100.0,
            max_daily_realized_loss_quote: 20.0,
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
        seed(&path, 0);
        let pool = Pubkey::new_unique();
        add_live_position(&path, pool);
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
            &proposal(pool, Action::Exit),
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
        seed(&path, 0);
        let pool = Pubkey::new_unique();
        add_live_position(&path, pool);
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

    #[test]
    fn non_live_proposal_is_rejected() {
        let path = db_path();
        seed(&path, 0);
        let pool = Pubkey::new_unique();
        let mut item = proposal(pool, Action::Enter);
        item.mode = Mode::Paper;

        let report = evaluate_controlled_live(
            &path,
            &item,
            &config(pool),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "controlled_live_requires_live_mode"
        );
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn untracked_exit_is_rejected() {
        let path = db_path();
        seed(&path, 0);
        let pool = Pubkey::new_unique();

        let report = evaluate_controlled_live(
            &path,
            &proposal(pool, Action::Exit),
            &config(pool),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "no_tracked_live_position_for_exit"
        );
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn intent_evaluation_uses_persisted_proposal() {
        use crate::dry_run::DryRunExecutionRequest;
        use crate::risk::RiskConfig;

        let pio_path = db_path();
        seed(&pio_path, 0);
        let execution_path = std::env::temp_dir().join(format!(
            "pio-controlled-live-exec-{}.db",
            Uuid::new_v4()
        ));
        let store = ExecutionIntentStore::open(&execution_path).unwrap();
        let pool = Pubkey::new_unique();
        let mut item = proposal(pool, Action::Enter);
        item.capital_quote = 75.0;
        let decision_id = item.decision_id.to_string();
        let request = DryRunExecutionRequest {
            proposal: item,
            transaction_base64: "tx".into(),
        };
        let risk = RiskConfig {
            max_capital_per_position_pct: 10.0,
            max_total_deployed_pct: 50.0,
            max_daily_drawdown_pct: 5.0,
            min_expected_edge_pct: 0.0,
            max_expected_downside_pct: 10.0,
            max_data_age_seconds: 60,
        };
        store.register(&request, &risk).unwrap();

        let report = evaluate_controlled_live_intent(
            &pio_path,
            &store,
            &decision_id,
            &config(pool),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "entry_capital_cap_exceeded");

        let _ = std::fs::remove_file(pio_path);
        let _ = std::fs::remove_file(execution_path);
    }

    #[test]
    fn unresolved_entry_consumes_position_slot_until_reconciled() {
        use crate::blockhash::PreparedUnsignedTransaction;
        use crate::dry_run::DryRunExecutionRequest;
        use crate::execution_guard::RiskCheckReport;
        use crate::risk::RiskConfig;
        use crate::simulation::SimulationReport;
        use crate::transaction_guard::TransactionGuardReport;
        use crate::wallet_guard::WalletAuthorizationReport;
        use serde_json::json;

        let pio_path = db_path();
        seed(&pio_path, 0);
        let execution_path = std::env::temp_dir().join(format!(
            "pio-controlled-live-pending-{}.db",
            Uuid::new_v4()
        ));
        let store = ExecutionIntentStore::open(&execution_path).unwrap();
        let pool = Pubkey::new_unique();

        let first = proposal(pool, Action::Enter);
        let first_id = first.decision_id.to_string();
        let first_request = DryRunExecutionRequest {
            proposal: first.clone(),
            transaction_base64: "tx-1".into(),
        };
        let second = proposal(pool, Action::Enter);
        let second_id = second.decision_id.to_string();
        let second_request = DryRunExecutionRequest {
            proposal: second,
            transaction_base64: "tx-2".into(),
        };
        let risk_config = RiskConfig {
            max_capital_per_position_pct: 10.0,
            max_total_deployed_pct: 50.0,
            max_daily_drawdown_pct: 5.0,
            min_expected_edge_pct: 0.0,
            max_expected_downside_pct: 10.0,
            max_data_age_seconds: 60,
        };
        store.register(&first_request, &risk_config).unwrap();
        store.register(&second_request, &risk_config).unwrap();

        let risk = RiskCheckReport {
            decision_id: first.decision_id,
            mode: Mode::Live,
            action: Action::Enter,
            accepted: true,
            reason: "approved".into(),
        };
        let guard = TransactionGuardReport {
            accepted: true,
            reason: "approved".into(),
            fee_payer: "payer".into(),
            pool_account_present: true,
            required_accounts_present: true,
            instruction_count: 1,
            static_account_count: 2,
            required_signatures: 1,
            signatures_all_default: true,
            address_lookup_table_count: 0,
            program_ids: vec!["program".into()],
            instruction_fingerprints: vec![],
        };
        let simulation = SimulationReport {
            succeeded: true,
            rpc_context_slot: 1,
            result: json!({"err": null}),
        };
        let wallet = WalletAuthorizationReport {
            accepted: true,
            reason: "approved".into(),
            wallet_pubkey: "payer".into(),
            transaction_fee_payer: "payer".into(),
        };
        store.record_risk(&first_id, &risk).unwrap();
        store.record_transaction_guard(&first_id, &guard).unwrap();
        store.record_simulation(&first_id, &simulation).unwrap();
        store
            .record_wallet_authorization(&first_id, &wallet)
            .unwrap();
        store
            .record_final_presign(
                &first_id,
                &PreparedUnsignedTransaction {
                    transaction_base64: "prepared".into(),
                    recent_blockhash: "blockhash".into(),
                    last_valid_block_height: 100,
                    rpc_context_slot: 1,
                    signatures_all_default: true,
                },
                &guard,
                &wallet,
                &simulation,
            )
            .unwrap();
        store.begin_signing(&first_id).unwrap();

        let report = evaluate_controlled_live_intent(
            &pio_path,
            &store,
            &second_id,
            &config(pool),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "max_effective_open_positions_reached"
        );
        assert_eq!(report.unresolved_entry_intents, 1);
        assert_eq!(report.effective_open_positions, 1);

        let conn = Connection::open(&pio_path).unwrap();
        conn.execute(
            "INSERT INTO live_positions(position_address, pool_address, status, opened_decision_id) VALUES (?1, ?2, 'OPEN', ?3)",
            [
                "tracked-first".to_string(),
                pool.to_string(),
                first_id.clone(),
            ],
        )
        .unwrap();

        let reconciled_report = evaluate_controlled_live_intent(
            &pio_path,
            &store,
            &second_id,
            &ControlledLiveConfig {
                max_open_positions: 2,
                ..config(pool)
            },
        )
        .unwrap();
        assert_eq!(reconciled_report.unresolved_entry_intents, 0);
        assert_eq!(reconciled_report.effective_open_positions, 1);

        let _ = std::fs::remove_file(pio_path);
        let _ = std::fs::remove_file(execution_path);
    }

    #[test]
    fn daily_submitted_capital_blocks_additional_entry() {
        use crate::blockhash::PreparedUnsignedTransaction;
        use crate::dry_run::DryRunExecutionRequest;
        use crate::execution_guard::RiskCheckReport;
        use crate::risk::RiskConfig;
        use crate::simulation::SimulationReport;
        use crate::transaction_guard::TransactionGuardReport;
        use crate::wallet_guard::WalletAuthorizationReport;
        use serde_json::json;

        let pio_path = db_path();
        seed(&pio_path, 0);
        let execution_path = std::env::temp_dir().join(format!(
            "pio-controlled-live-budget-{}.db",
            Uuid::new_v4()
        ));
        let store = ExecutionIntentStore::open(&execution_path).unwrap();
        let pool = Pubkey::new_unique();
        let risk_config = RiskConfig {
            max_capital_per_position_pct: 10.0,
            max_total_deployed_pct: 50.0,
            max_daily_drawdown_pct: 5.0,
            min_expected_edge_pct: 0.0,
            max_expected_downside_pct: 10.0,
            max_data_age_seconds: 60,
        };

        let mut first = proposal(pool, Action::Enter);
        first.capital_quote = 80.0;
        let first_id = first.decision_id.to_string();
        let first_request = DryRunExecutionRequest {
            proposal: first.clone(),
            transaction_base64: "tx-1".into(),
        };
        store.register(&first_request, &risk_config).unwrap();

        let risk = RiskCheckReport {
            decision_id: first.decision_id,
            mode: Mode::Live,
            action: Action::Enter,
            accepted: true,
            reason: "approved".into(),
        };
        let guard = TransactionGuardReport {
            accepted: true,
            reason: "approved".into(),
            fee_payer: "payer".into(),
            pool_account_present: true,
            required_accounts_present: true,
            instruction_count: 1,
            static_account_count: 2,
            required_signatures: 1,
            signatures_all_default: true,
            address_lookup_table_count: 0,
            program_ids: vec!["program".into()],
            instruction_fingerprints: vec![],
        };
        let simulation = SimulationReport {
            succeeded: true,
            rpc_context_slot: 1,
            result: json!({"err": null}),
        };
        let wallet = WalletAuthorizationReport {
            accepted: true,
            reason: "approved".into(),
            wallet_pubkey: "payer".into(),
            transaction_fee_payer: "payer".into(),
        };
        store.record_risk(&first_id, &risk).unwrap();
        store.record_transaction_guard(&first_id, &guard).unwrap();
        store.record_simulation(&first_id, &simulation).unwrap();
        store
            .record_wallet_authorization(&first_id, &wallet)
            .unwrap();
        store
            .record_final_presign(
                &first_id,
                &PreparedUnsignedTransaction {
                    transaction_base64: "prepared".into(),
                    recent_blockhash: "blockhash".into(),
                    last_valid_block_height: 100,
                    rpc_context_slot: 1,
                    signatures_all_default: true,
                },
                &guard,
                &wallet,
                &simulation,
            )
            .unwrap();
        store.begin_signing(&first_id).unwrap();

        let mut second = proposal(pool, Action::Enter);
        second.capital_quote = 25.0;
        let second_id = second.decision_id.to_string();
        store
            .register(
                &DryRunExecutionRequest {
                    proposal: second,
                    transaction_base64: "tx-2".into(),
                },
                &risk_config,
            )
            .unwrap();

        let mut cfg = config(pool);
        cfg.max_open_positions = 3;
        cfg.max_capital_quote_per_entry = 100.0;
        cfg.max_daily_entry_capital_quote = 100.0;

        let report = evaluate_controlled_live_intent(
            &pio_path,
            &store,
            &second_id,
            &cfg,
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "daily_entry_capital_budget_exceeded"
        );
        assert_eq!(
            report.daily_submitted_entry_capital_quote,
            80.0
        );

        let _ = std::fs::remove_file(pio_path);
        let _ = std::fs::remove_file(execution_path);
    }

    #[test]
    fn realized_daily_loss_blocks_new_risk_but_not_exit() {
        let path = db_path();
        seed(&path, 0);
        let pool = Pubkey::new_unique();
        add_live_position(&path, pool);
        add_daily_closed_outcome(
            &path,
            "loss-position",
            "2026-09-23T10:00:00+00:00",
            Some("-25"),
        );
        let mut cfg = config(pool);
        cfg.max_open_positions = 2;

        let entry = evaluate_controlled_live_at(
            &path,
            &proposal(pool, Action::Enter),
            &cfg,
            Some("2026-09-23T15:00:00+00:00"),
        )
        .unwrap();
        let rebalance = evaluate_controlled_live_at(
            &path,
            &proposal(pool, Action::Rebalance),
            &cfg,
            Some("2026-09-23T15:00:00+00:00"),
        )
        .unwrap();
        let exit = evaluate_controlled_live_at(
            &path,
            &proposal(pool, Action::Exit),
            &cfg,
            Some("2026-09-23T15:00:00+00:00"),
        )
        .unwrap();

        assert!(!entry.accepted);
        assert_eq!(
            entry.reason,
            "daily_realized_loss_budget_reached"
        );
        assert_eq!(entry.daily_realized_loss_quote, 25.0);
        assert!(!rebalance.accepted);
        assert_eq!(
            rebalance.reason,
            "daily_realized_loss_budget_reached"
        );
        assert!(exit.accepted);

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn unvalued_daily_close_blocks_new_risk() {
        let path = db_path();
        seed(&path, 0);
        let pool = Pubkey::new_unique();
        add_daily_closed_outcome(
            &path,
            "unvalued-position",
            "2026-09-23T10:00:00+00:00",
            None,
        );

        let report = evaluate_controlled_live_at(
            &path,
            &proposal(pool, Action::Enter),
            &config(pool),
            Some("2026-09-23T15:00:00+00:00"),
        )
        .unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "daily_realized_loss_evidence_incomplete"
        );
        assert_eq!(report.unvalued_closed_positions_today, 1);

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn rebalance_requires_unambiguous_position_and_respects_cap() {
        let path = db_path();
        seed(&path, 0);
        let pool = Pubkey::new_unique();
        add_live_position_with_rebalances(&path, pool, 3);

        let capped = evaluate_controlled_live(
            &path,
            &proposal(pool, Action::Rebalance),
            &config(pool),
        )
        .unwrap();

        assert!(!capped.accepted);
        assert_eq!(
            capped.reason,
            "max_rebalances_per_position_reached"
        );
        assert_eq!(capped.matching_pool_rebalances, 3);

        add_live_position(&path, pool);
        let ambiguous = evaluate_controlled_live(
            &path,
            &proposal(pool, Action::Rebalance),
            &config(pool),
        )
        .unwrap();

        assert!(!ambiguous.accepted);
        assert_eq!(
            ambiguous.reason,
            "ambiguous_tracked_live_positions_for_rebalance"
        );

        let exit = evaluate_controlled_live(
            &path,
            &proposal(pool, Action::Exit),
            &config(pool),
        )
        .unwrap();
        assert!(!exit.accepted);
        assert_eq!(
            exit.reason,
            "ambiguous_tracked_live_positions_for_exit"
        );

        let _ = std::fs::remove_file(path);
    }

}
