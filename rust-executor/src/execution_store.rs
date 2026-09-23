use crate::blockhash::PreparedUnsignedTransaction;
use crate::dry_run::DryRunExecutionRequest;
use crate::execution_guard::RiskCheckReport;
use crate::simulation::SimulationReport;
use crate::transaction_guard::TransactionGuardReport;
use crate::wallet_guard::WalletAuthorizationReport;
use crate::risk::RiskConfig;
use anyhow::{Context, Result};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ExecutionIntentStatus {
    Received,
    RiskApproved,
    Rejected,
    SimulationPassed,
    SimulationFailed,
    Signing,
    Sent,
    Confirmed,
    Failed,
}

impl ExecutionIntentStatus {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Received => "RECEIVED",
            Self::RiskApproved => "RISK_APPROVED",
            Self::Rejected => "REJECTED",
            Self::SimulationPassed => "SIMULATION_PASSED",
            Self::SimulationFailed => "SIMULATION_FAILED",
            Self::Signing => "SIGNING",
            Self::Sent => "SENT",
            Self::Confirmed => "CONFIRMED",
            Self::Failed => "FAILED",
        }
    }

    fn parse(value: &str) -> Result<Self> {
        Ok(match value {
            "RECEIVED" => Self::Received,
            "RISK_APPROVED" => Self::RiskApproved,
            "REJECTED" => Self::Rejected,
            "SIMULATION_PASSED" => Self::SimulationPassed,
            "SIMULATION_FAILED" => Self::SimulationFailed,
            "SIGNING" => Self::Signing,
            "SENT" => Self::Sent,
            "CONFIRMED" => Self::Confirmed,
            "FAILED" => Self::Failed,
            other => anyhow::bail!("unknown execution intent status: {other}"),
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionIntentRecord {
    pub decision_id: String,
    pub mode: String,
    pub action: String,
    pub pool_address: String,
    pub status: ExecutionIntentStatus,
    pub created_at_unix: u64,
    pub updated_at_unix: u64,
    pub risk: Option<RiskCheckReport>,
    pub simulation: Option<SimulationReport>,
    pub transaction_guard: Option<TransactionGuardReport>,
    pub wallet_authorization: Option<WalletAuthorizationReport>,
    pub prepared_transaction: Option<PreparedUnsignedTransaction>,
    pub final_simulation: Option<SimulationReport>,
    pub signature: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegisteredExecutionIntent {
    pub reused_existing: bool,
    pub record: ExecutionIntentRecord,
}

#[derive(Debug, Clone)]
pub struct ExecutionIntentStore {
    path: PathBuf,
}

fn now_unix() -> Result<i64> {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .context("system clock is before Unix epoch")?
        .as_secs();
    i64::try_from(seconds).context("Unix timestamp exceeds SQLite integer range")
}

fn enum_text<T: Serialize>(value: &T) -> Result<String> {
    let raw = serde_json::to_value(value)?;
    raw.as_str()
        .map(str::to_owned)
        .context("serialized enum is not a string")
}

impl ExecutionIntentStore {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        let store = Self { path };
        store.initialize()?;
        Ok(store)
    }

    fn connection(&self) -> Result<Connection> {
        Connection::open(&self.path)
            .with_context(|| format!("failed to open execution DB: {}", self.path.display()))
    }

    fn initialize(&self) -> Result<()> {
        let conn = self.connection()?;
        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS execution_intents (
                decision_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                action TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(
                    status IN (
                        'RECEIVED',
                        'RISK_APPROVED',
                        'REJECTED',
                        'SIMULATION_PASSED',
                        'SIMULATION_FAILED',
                        'SIGNING',
                        'SENT',
                        'CONFIRMED',
                        'FAILED'
                    )
                ),
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL,
                risk_json TEXT,
                simulation_json TEXT,
                transaction_guard_json TEXT,
                wallet_authorization_json TEXT,
                prepared_transaction_json TEXT,
                final_simulation_json TEXT,
                signature TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_execution_intents_status
            ON execution_intents(status, updated_at_unix);
            "#,
        )?;

        let mut columns = conn.prepare(
            "PRAGMA table_info(execution_intents)"
        )?;
        let names = columns
            .query_map([], |row| row.get::<_, String>(1))?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        if !names.iter().any(|name| name == "transaction_guard_json") {
            conn.execute(
                "ALTER TABLE execution_intents ADD COLUMN transaction_guard_json TEXT",
                [],
            )?;
        }
        if !names.iter().any(|name| name == "wallet_authorization_json") {
            conn.execute(
                "ALTER TABLE execution_intents ADD COLUMN wallet_authorization_json TEXT",
                [],
            )?;
        }
        if !names.iter().any(|name| name == "prepared_transaction_json") {
            conn.execute(
                "ALTER TABLE execution_intents ADD COLUMN prepared_transaction_json TEXT",
                [],
            )?;
        }
        if !names.iter().any(|name| name == "final_simulation_json") {
            conn.execute(
                "ALTER TABLE execution_intents ADD COLUMN final_simulation_json TEXT",
                [],
            )?;
        }
        Ok(())
    }

    pub fn register(
        &self,
        request: &DryRunExecutionRequest,
        config: &RiskConfig,
    ) -> Result<RegisteredExecutionIntent> {
        let canonical = serde_json::to_string(&serde_json::json!({
            "request": request,
            "risk_config": config,
        }))?;
        let decision_id = request.proposal.decision_id.to_string();
        let mode = enum_text(&request.proposal.mode)?;
        let action = enum_text(&request.proposal.action)?;
        let now = now_unix()?;

        let mut conn = self.connection()?;
        let tx = conn.transaction()?;
        let inserted = tx.execute(
            r#"
            INSERT OR IGNORE INTO execution_intents(
                decision_id, mode, action, pool_address,
                request_json, status, created_at_unix, updated_at_unix
            ) VALUES (?, ?, ?, ?, ?, 'RECEIVED', ?, ?)
            "#,
            params![
                decision_id.as_str(),
                mode.as_str(),
                action.as_str(),
                request.proposal.pool_address.as_str(),
                canonical.as_str(),
                now,
                now,
            ],
        )?;

        let existing_request: String = tx.query_row(
            "SELECT request_json FROM execution_intents WHERE decision_id = ?",
            params![request.proposal.decision_id.to_string()],
            |row| row.get(0),
        )?;
        if existing_request != canonical {
            anyhow::bail!(
                "decision_id already exists with a different execution request"
            );
        }
        tx.commit()?;

        Ok(RegisteredExecutionIntent {
            reused_existing: inserted == 0,
            record: self.load(&request.proposal.decision_id.to_string())?,
        })
    }

    pub fn register_with_transaction_policy(
        &self,
        request: &DryRunExecutionRequest,
        config: &RiskConfig,
        transaction_config: &crate::transaction_guard::TransactionGuardConfig,
    ) -> Result<RegisteredExecutionIntent> {
        let canonical = serde_json::to_string(&serde_json::json!({
            "request": request,
            "risk_config": config,
            "transaction_guard_config": transaction_config,
        }))?;
        let decision_id = request.proposal.decision_id.to_string();
        let mode = enum_text(&request.proposal.mode)?;
        let action = enum_text(&request.proposal.action)?;
        let now = now_unix()?;

        let mut conn = self.connection()?;
        let tx = conn.transaction()?;
        let inserted = tx.execute(
            r#"
            INSERT OR IGNORE INTO execution_intents(
                decision_id, mode, action, pool_address,
                request_json, status, created_at_unix, updated_at_unix
            ) VALUES (?, ?, ?, ?, ?, 'RECEIVED', ?, ?)
            "#,
            params![
                decision_id.as_str(),
                mode.as_str(),
                action.as_str(),
                request.proposal.pool_address.as_str(),
                canonical.as_str(),
                now,
                now,
            ],
        )?;

        let existing_request: String = tx.query_row(
            "SELECT request_json FROM execution_intents WHERE decision_id = ?",
            params![request.proposal.decision_id.to_string()],
            |row| row.get(0),
        )?;
        if existing_request != canonical {
            anyhow::bail!(
                "decision_id already exists with different execution or safety inputs"
            );
        }
        tx.commit()?;

        Ok(RegisteredExecutionIntent {
            reused_existing: inserted == 0,
            record: self.load(&request.proposal.decision_id.to_string())?,
        })
    }

    pub fn record_risk(
        &self,
        decision_id: &str,
        risk: &RiskCheckReport,
    ) -> Result<ExecutionIntentRecord> {
        let current = self.load(decision_id)?;
        if let Some(existing) = &current.risk {
            let existing_json = serde_json::to_string(existing)?;
            let incoming_json = serde_json::to_string(risk)?;
            if existing_json == incoming_json {
                return Ok(current);
            }
            anyhow::bail!(
                "execution intent {decision_id} already has a different risk result"
            );
        }
        if current.status != ExecutionIntentStatus::Received {
            anyhow::bail!(
                "risk evaluation requires RECEIVED status; current status is {:?}",
                current.status
            );
        }

        let status = if risk.accepted {
            ExecutionIntentStatus::RiskApproved
        } else {
            ExecutionIntentStatus::Rejected
        };
        let payload = serde_json::to_string(risk)?;
        let now = now_unix()?;
        let conn = self.connection()?;
        let changed = conn.execute(
            r#"
            UPDATE execution_intents
            SET status = ?, risk_json = ?, updated_at_unix = ?, error = ?
            WHERE decision_id = ? AND status = 'RECEIVED'
              AND risk_json IS NULL
            "#,
            params![
                status.as_str(),
                payload,
                now,
                if risk.accepted {
                    None::<String>
                } else {
                    Some(risk.reason.clone())
                },
                decision_id,
            ],
        )?;
        if changed != 1 {
            anyhow::bail!(
                "execution intent {decision_id} changed concurrently while recording risk"
            );
        }
        self.load(decision_id)
    }

    pub fn record_transaction_guard(
        &self,
        decision_id: &str,
        guard: &TransactionGuardReport,
    ) -> Result<ExecutionIntentRecord> {
        let current = self.load(decision_id)?;
        if let Some(existing) = &current.transaction_guard {
            let existing_json = serde_json::to_string(existing)?;
            let incoming_json = serde_json::to_string(guard)?;
            if existing_json == incoming_json {
                return Ok(current);
            }
            anyhow::bail!(
                "execution intent {decision_id} already has a different transaction guard result"
            );
        }
        if current.status != ExecutionIntentStatus::RiskApproved {
            anyhow::bail!(
                "transaction guard requires RISK_APPROVED status; current status is {:?}",
                current.status
            );
        }
        let payload = serde_json::to_string(guard)?;
        let next_status = if guard.accepted {
            ExecutionIntentStatus::RiskApproved
        } else {
            ExecutionIntentStatus::Rejected
        };
        let now = now_unix()?;
        let conn = self.connection()?;
        let changed = conn.execute(
            r#"
            UPDATE execution_intents
            SET status = ?, transaction_guard_json = ?,
                updated_at_unix = ?, error = ?
            WHERE decision_id = ? AND status = 'RISK_APPROVED'
              AND transaction_guard_json IS NULL
            "#,
            params![
                next_status.as_str(),
                payload,
                now,
                if guard.accepted {
                    None::<String>
                } else {
                    Some(guard.reason.clone())
                },
                decision_id,
            ],
        )?;
        if changed != 1 {
            anyhow::bail!(
                "execution intent {decision_id} changed concurrently while recording transaction guard"
            );
        }
        self.load(decision_id)
    }

    pub fn record_simulation(
        &self,
        decision_id: &str,
        simulation: &SimulationReport,
    ) -> Result<ExecutionIntentRecord> {
        let current = self.load(decision_id)?;
        let guard = current
            .transaction_guard
            .as_ref()
            .context("simulation requires an accepted transaction guard")?;
        if current.status != ExecutionIntentStatus::RiskApproved || !guard.accepted {
            anyhow::bail!(
                "simulation requires RISK_APPROVED status and an accepted transaction guard"
            );
        }
        let status = if simulation.succeeded {
            ExecutionIntentStatus::SimulationPassed
        } else {
            ExecutionIntentStatus::SimulationFailed
        };
        let payload = serde_json::to_string(simulation)?;
        let now = now_unix()?;
        let conn = self.connection()?;
        let changed = conn.execute(
            r#"
            UPDATE execution_intents
            SET status = ?, simulation_json = ?, updated_at_unix = ?, error = ?
            WHERE decision_id = ? AND status = 'RISK_APPROVED'
            "#,
            params![
                status.as_str(),
                payload,
                now,
                if simulation.succeeded {
                    None::<String>
                } else {
                    Some("transaction_simulation_failed".to_string())
                },
                decision_id,
            ],
        )?;
        if changed != 1 {
            anyhow::bail!("unknown execution decision_id: {decision_id}");
        }
        self.load(decision_id)
    }

    pub fn record_wallet_authorization(
        &self,
        decision_id: &str,
        authorization: &WalletAuthorizationReport,
    ) -> Result<ExecutionIntentRecord> {
        let current = self.load(decision_id)?;
        if let Some(existing) = &current.wallet_authorization {
            let existing_json = serde_json::to_string(existing)?;
            let incoming_json = serde_json::to_string(authorization)?;
            if existing_json == incoming_json {
                return Ok(current);
            }
            anyhow::bail!(
                "execution intent {decision_id} already has a different wallet authorization"
            );
        }
        if current.status != ExecutionIntentStatus::SimulationPassed {
            anyhow::bail!(
                "wallet authorization requires SIMULATION_PASSED status; current status is {:?}",
                current.status
            );
        }
        if !authorization.accepted {
            anyhow::bail!(
                "rejected wallet authorization cannot be persisted as execution-ready"
            );
        }
        let payload = serde_json::to_string(authorization)?;
        let now = now_unix()?;
        let conn = self.connection()?;
        let changed = conn.execute(
            r#"
            UPDATE execution_intents
            SET wallet_authorization_json = ?, updated_at_unix = ?
            WHERE decision_id = ? AND status = 'SIMULATION_PASSED'
              AND wallet_authorization_json IS NULL
            "#,
            params![payload, now, decision_id],
        )?;
        if changed != 1 {
            anyhow::bail!(
                "execution intent {decision_id} changed concurrently while authorizing wallet"
            );
        }
        self.load(decision_id)
    }

    pub fn record_final_presign(
        &self,
        decision_id: &str,
        prepared: &PreparedUnsignedTransaction,
        transaction: &TransactionGuardReport,
        wallet: &WalletAuthorizationReport,
        simulation: &SimulationReport,
    ) -> Result<ExecutionIntentRecord> {
        let current = self.load(decision_id)?;
        if current.status != ExecutionIntentStatus::SimulationPassed {
            anyhow::bail!(
                "final presign requires SIMULATION_PASSED status; current status is {:?}",
                current.status
            );
        }
        if !prepared.signatures_all_default {
            anyhow::bail!("final presign transaction must remain unsigned");
        }
        if !transaction.accepted {
            anyhow::bail!("final presign requires accepted transaction guard");
        }
        if !wallet.accepted {
            anyhow::bail!("final presign requires accepted wallet authorization");
        }
        if !simulation.succeeded {
            anyhow::bail!("final presign requires successful exact simulation");
        }

        let persisted_guard = current
            .transaction_guard
            .as_ref()
            .context("final presign requires persisted transaction guard")?;
        if serde_json::to_string(persisted_guard)?
            != serde_json::to_string(transaction)?
        {
            anyhow::bail!(
                "final presign transaction guard differs from persisted guard"
            );
        }
        let persisted_wallet = current
            .wallet_authorization
            .as_ref()
            .context("final presign requires persisted wallet authorization")?;
        if serde_json::to_string(persisted_wallet)?
            != serde_json::to_string(wallet)?
        {
            anyhow::bail!(
                "final presign wallet authorization differs from persisted authorization"
            );
        }

        if let (Some(existing_prepared), Some(existing_simulation)) = (
            &current.prepared_transaction,
            &current.final_simulation,
        ) {
            if serde_json::to_string(existing_prepared)?
                == serde_json::to_string(prepared)?
                && serde_json::to_string(existing_simulation)?
                    == serde_json::to_string(simulation)?
            {
                return Ok(current);
            }
            anyhow::bail!(
                "execution intent {decision_id} already has different final presign evidence"
            );
        }
        if current.prepared_transaction.is_some()
            || current.final_simulation.is_some()
        {
            anyhow::bail!(
                "execution intent {decision_id} has incomplete final presign evidence"
            );
        }

        let prepared_json = serde_json::to_string(prepared)?;
        let simulation_json = serde_json::to_string(simulation)?;
        let now = now_unix()?;
        let conn = self.connection()?;
        let changed = conn.execute(
            r#"
            UPDATE execution_intents
            SET prepared_transaction_json = ?,
                final_simulation_json = ?,
                updated_at_unix = ?
            WHERE decision_id = ?
              AND status = 'SIMULATION_PASSED'
              AND prepared_transaction_json IS NULL
              AND final_simulation_json IS NULL
            "#,
            params![
                prepared_json,
                simulation_json,
                now,
                decision_id,
            ],
        )?;
        if changed != 1 {
            anyhow::bail!(
                "execution intent {decision_id} changed concurrently while recording final presign"
            );
        }
        self.load(decision_id)
    }

    fn transition(
        &self,
        decision_id: &str,
        allowed_from: &[ExecutionIntentStatus],
        next: ExecutionIntentStatus,
        signature: Option<&str>,
        error: Option<&str>,
    ) -> Result<ExecutionIntentRecord> {
        let current = self.load(decision_id)?;

        if current.status == next {
            if let Some(expected_signature) = signature {
                if current.signature.as_deref() != Some(expected_signature) {
                    anyhow::bail!(
                        "execution intent {decision_id} already has status {:?} with a different signature",
                        next
                    );
                }
            }
            if let Some(expected_error) = error {
                if current.error.as_deref() != Some(expected_error) {
                    anyhow::bail!(
                        "execution intent {decision_id} already has status {:?} with a different error",
                        next
                    );
                }
            }
            return Ok(current);
        }

        if !allowed_from.contains(&current.status) {
            anyhow::bail!(
                "invalid execution intent transition for {decision_id}: {:?} -> {:?}",
                current.status,
                next
            );
        }

        let now = now_unix()?;
        let conn = self.connection()?;
        let changed = conn.execute(
            r#"
            UPDATE execution_intents
            SET status = ?,
                signature = COALESCE(?, signature),
                error = ?,
                updated_at_unix = ?
            WHERE decision_id = ? AND status = ?
            "#,
            params![
                next.as_str(),
                signature,
                error,
                now,
                decision_id,
                current.status.as_str(),
            ],
        )?;
        if changed != 1 {
            anyhow::bail!(
                "execution intent {decision_id} changed concurrently while transitioning"
            );
        }
        self.load(decision_id)
    }

    pub fn begin_signing(&self, decision_id: &str) -> Result<ExecutionIntentRecord> {
        let current = self.load(decision_id)?;
        let authorization = current
            .wallet_authorization
            .as_ref()
            .context("signing requires persisted wallet authorization")?;
        if !authorization.accepted {
            anyhow::bail!("signing requires accepted wallet authorization");
        }
        let prepared = current
            .prepared_transaction
            .as_ref()
            .context("signing requires persisted prepared transaction")?;
        if !prepared.signatures_all_default {
            anyhow::bail!("signing requires an unsigned prepared transaction");
        }
        let final_simulation = current
            .final_simulation
            .as_ref()
            .context("signing requires persisted exact final simulation")?;
        if !final_simulation.succeeded {
            anyhow::bail!("signing requires successful exact final simulation");
        }
        self.transition(
            decision_id,
            &[ExecutionIntentStatus::SimulationPassed],
            ExecutionIntentStatus::Signing,
            None,
            None,
        )
    }

    pub fn record_sent(
        &self,
        decision_id: &str,
        signature: &str,
    ) -> Result<ExecutionIntentRecord> {
        let signature = signature.trim();
        if signature.is_empty() {
            anyhow::bail!("transaction signature is required");
        }
        self.transition(
            decision_id,
            &[ExecutionIntentStatus::Signing],
            ExecutionIntentStatus::Sent,
            Some(signature),
            None,
        )
    }

    pub fn record_confirmed(
        &self,
        decision_id: &str,
        signature: &str,
    ) -> Result<ExecutionIntentRecord> {
        let signature = signature.trim();
        if signature.is_empty() {
            anyhow::bail!("transaction signature is required");
        }
        self.transition(
            decision_id,
            &[ExecutionIntentStatus::Sent],
            ExecutionIntentStatus::Confirmed,
            Some(signature),
            None,
        )
    }

    pub fn record_failure(
        &self,
        decision_id: &str,
        error: &str,
    ) -> Result<ExecutionIntentRecord> {
        let error = error.trim();
        if error.is_empty() {
            anyhow::bail!("execution failure reason is required");
        }
        self.transition(
            decision_id,
            &[
                ExecutionIntentStatus::SimulationPassed,
                ExecutionIntentStatus::Signing,
                ExecutionIntentStatus::Sent,
            ],
            ExecutionIntentStatus::Failed,
            None,
            Some(error),
        )
    }

    pub fn load(&self, decision_id: &str) -> Result<ExecutionIntentRecord> {
        let conn = self.connection()?;
        let mut stmt = conn.prepare(
            r#"
            SELECT decision_id, mode, action, pool_address, status,
                   created_at_unix, updated_at_unix,
                   risk_json, simulation_json, transaction_guard_json,
                   wallet_authorization_json, prepared_transaction_json,
                   final_simulation_json, signature, error
            FROM execution_intents
            WHERE decision_id = ?
            "#,
        )?;
        let raw = stmt
            .query_row(params![decision_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, Option<String>>(7)?,
                    row.get::<_, Option<String>>(8)?,
                    row.get::<_, Option<String>>(9)?,
                    row.get::<_, Option<String>>(10)?,
                    row.get::<_, Option<String>>(11)?,
                    row.get::<_, Option<String>>(12)?,
                    row.get::<_, Option<String>>(13)?,
                    row.get::<_, Option<String>>(14)?,
                ))
            })
            .with_context(|| format!("unknown execution decision_id: {decision_id}"))?;

        Ok(ExecutionIntentRecord {
            decision_id: raw.0,
            mode: raw.1,
            action: raw.2,
            pool_address: raw.3,
            status: ExecutionIntentStatus::parse(&raw.4)?,
            created_at_unix: u64::try_from(raw.5).context("invalid created_at_unix")?,
            updated_at_unix: u64::try_from(raw.6).context("invalid updated_at_unix")?,
            risk: raw
                .7
                .map(|value| serde_json::from_str(&value))
                .transpose()?,
            simulation: raw
                .8
                .map(|value| serde_json::from_str(&value))
                .transpose()?,
            transaction_guard: raw
                .9
                .map(|value| serde_json::from_str(&value))
                .transpose()?,
            wallet_authorization: raw
                .10
                .map(|value| serde_json::from_str(&value))
                .transpose()?,
            prepared_transaction: raw
                .11
                .map(|value| serde_json::from_str(&value))
                .transpose()?,
            final_simulation: raw
                .12
                .map(|value| serde_json::from_str(&value))
                .transpose()?,
            signature: raw.13,
            error: raw.14,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Action, Mode, TradeProposal};
    use serde_json::json;
    use uuid::Uuid;

    fn db_path() -> PathBuf {
        std::env::temp_dir().join(format!("pio-execution-{}.db", Uuid::new_v4()))
    }

    fn request() -> DryRunExecutionRequest {
        DryRunExecutionRequest {
            proposal: TradeProposal {
                decision_id: Uuid::new_v4(),
                mode: Mode::Live,
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
            transaction_base64: "same".into(),
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

    fn risk(accepted: bool, id: Uuid) -> RiskCheckReport {
        RiskCheckReport {
            decision_id: id,
            mode: Mode::Live,
            action: Action::Enter,
            accepted,
            reason: if accepted { "approved" } else { "blocked" }.into(),
        }
    }

    fn guard(accepted: bool) -> TransactionGuardReport {
        TransactionGuardReport {
            accepted,
            reason: if accepted {
                "approved".into()
            } else {
                "transaction_rejected".into()
            },
            fee_payer: "payer".into(),
            pool_account_present: true,
            instruction_count: 1,
            static_account_count: 3,
            required_signatures: 1,
            signatures_all_default: true,
            address_lookup_table_count: 0,
            program_ids: vec!["program".into()],
            instruction_fingerprints: vec![],
        }
    }

    fn wallet_authorization() -> WalletAuthorizationReport {
        WalletAuthorizationReport {
            accepted: true,
            reason: "approved".into(),
            wallet_pubkey: "payer".into(),
            transaction_fee_payer: "payer".into(),
        }
    }

    fn simulation(succeeded: bool) -> SimulationReport {
        SimulationReport {
            succeeded,
            rpc_context_slot: 1,
            result: json!({"err": if succeeded { serde_json::Value::Null } else { json!("boom") }}),
        }
    }

    #[test]
    fn identical_decision_request_is_idempotent() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();

        let cfg = config();
        let first = store.register(&request, &cfg).unwrap();
        let second = store.register(&request, &cfg).unwrap();

        assert!(!first.reused_existing);
        assert!(second.reused_existing);
        assert_eq!(second.record.status, ExecutionIntentStatus::Received);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn decision_id_cannot_be_reused_for_different_request() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let cfg = config();
        store.register(&request, &cfg).unwrap();

        let mut changed = request.clone();
        changed.transaction_base64 = "different".into();
        assert!(store.register(&changed, &cfg).is_err());
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn decision_id_cannot_reuse_different_risk_config() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let cfg = config();
        store.register(&request, &cfg).unwrap();

        let mut changed = cfg.clone();
        changed.max_total_deployed_pct = 25.0;
        assert!(store.register(&request, &changed).is_err());
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn risk_and_simulation_states_are_persisted() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let id = request.proposal.decision_id;
        let cfg = config();
        store.register(&request, &cfg).unwrap();

        let after_risk = store.record_risk(&id.to_string(), &risk(true, id)).unwrap();
        assert_eq!(after_risk.status, ExecutionIntentStatus::RiskApproved);

        store
            .record_transaction_guard(&id.to_string(), &guard(true))
            .unwrap();
        let after_simulation = store
            .record_simulation(&id.to_string(), &simulation(true))
            .unwrap();
        assert_eq!(
            after_simulation.status,
            ExecutionIntentStatus::SimulationPassed
        );
        assert!(after_simulation.simulation.unwrap().succeeded);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn signing_send_and_confirmation_transitions_are_guarded() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let id = request.proposal.decision_id.to_string();
        let cfg = config();

        store.register(&request, &cfg).unwrap();
        assert!(store.begin_signing(&id).is_err());

        store
            .record_risk(&id, &risk(true, request.proposal.decision_id))
            .unwrap();
        store.record_transaction_guard(&id, &guard(true)).unwrap();
        store.record_simulation(&id, &simulation(true)).unwrap();
        assert!(store.begin_signing(&id).is_err());
        store
            .record_wallet_authorization(&id, &wallet_authorization())
            .unwrap();

        assert_eq!(
            store.begin_signing(&id).unwrap().status,
            ExecutionIntentStatus::Signing
        );
        assert_eq!(
            store.begin_signing(&id).unwrap().status,
            ExecutionIntentStatus::Signing
        );

        let sent = store.record_sent(&id, "signature-1").unwrap();
        assert_eq!(sent.status, ExecutionIntentStatus::Sent);
        assert_eq!(sent.signature.as_deref(), Some("signature-1"));

        assert!(store.record_sent(&id, "signature-2").is_err());

        let confirmed = store.record_confirmed(&id, "signature-1").unwrap();
        assert_eq!(confirmed.status, ExecutionIntentStatus::Confirmed);
        assert_eq!(confirmed.signature.as_deref(), Some("signature-1"));

        let repeated = store.record_confirmed(&id, "signature-1").unwrap();
        assert_eq!(repeated.status, ExecutionIntentStatus::Confirmed);
        assert!(store.record_failure(&id, "too late").is_err());

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn execution_failure_is_terminal_and_idempotent() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let id = request.proposal.decision_id.to_string();
        let cfg = config();

        store.register(&request, &cfg).unwrap();
        store
            .record_risk(&id, &risk(true, request.proposal.decision_id))
            .unwrap();
        store.record_transaction_guard(&id, &guard(true)).unwrap();
        store.record_simulation(&id, &simulation(true)).unwrap();
        store
            .record_wallet_authorization(&id, &wallet_authorization())
            .unwrap();
        store.begin_signing(&id).unwrap();

        let failed = store.record_failure(&id, "signer unavailable").unwrap();
        assert_eq!(failed.status, ExecutionIntentStatus::Failed);
        assert_eq!(failed.error.as_deref(), Some("signer unavailable"));

        let repeated = store.record_failure(&id, "signer unavailable").unwrap();
        assert_eq!(repeated.status, ExecutionIntentStatus::Failed);
        assert!(store.record_failure(&id, "different failure").is_err());

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn transaction_guard_is_required_before_simulation() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let id = request.proposal.decision_id.to_string();
        let cfg = config();

        store.register(&request, &cfg).unwrap();
        store
            .record_risk(&id, &risk(true, request.proposal.decision_id))
            .unwrap();

        assert!(store.record_simulation(&id, &simulation(true)).is_err());
        store.record_transaction_guard(&id, &guard(true)).unwrap();
        assert_eq!(
            store
                .record_simulation(&id, &simulation(true))
                .unwrap()
                .status,
            ExecutionIntentStatus::SimulationPassed
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn rejected_transaction_guard_is_terminal() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let id = request.proposal.decision_id.to_string();
        let cfg = config();

        store.register(&request, &cfg).unwrap();
        store
            .record_risk(&id, &risk(true, request.proposal.decision_id))
            .unwrap();
        let rejected = store
            .record_transaction_guard(&id, &guard(false))
            .unwrap();

        assert_eq!(rejected.status, ExecutionIntentStatus::Rejected);
        assert!(store.record_simulation(&id, &simulation(true)).is_err());

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn risk_replay_cannot_regress_progressed_intent() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let id = request.proposal.decision_id.to_string();
        let accepted = risk(true, request.proposal.decision_id);

        store.register(&request, &config()).unwrap();
        store.record_risk(&id, &accepted).unwrap();
        store.record_transaction_guard(&id, &guard(true)).unwrap();
        store.record_simulation(&id, &simulation(true)).unwrap();

        let replayed = store.record_risk(&id, &accepted).unwrap();
        assert_eq!(
            replayed.status,
            ExecutionIntentStatus::SimulationPassed
        );

        let different = risk(false, request.proposal.decision_id);
        assert!(store.record_risk(&id, &different).is_err());
        assert_eq!(
            store.load(&id).unwrap().status,
            ExecutionIntentStatus::SimulationPassed
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn decision_id_cannot_reuse_different_transaction_policy() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let cfg = config();
        let tx_cfg = crate::transaction_guard::TransactionGuardConfig {
            expected_fee_payer: "11111111111111111111111111111111".into(),
            allowed_program_ids: vec![
                "11111111111111111111111111111111".into(),
            ],
            max_instructions: 4,
            max_static_accounts: 16,
            allow_address_lookup_tables: false,
            require_unsigned: true,
            require_instruction_policy: false,
            instruction_policies: vec![],
        };
        store
            .register_with_transaction_policy(&request, &cfg, &tx_cfg)
            .unwrap();

        let mut changed = tx_cfg.clone();
        changed.max_instructions = 5;
        assert!(
            store
                .register_with_transaction_policy(&request, &cfg, &changed)
                .is_err()
        );

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn wallet_authorization_cannot_precede_simulation() {
        let path = db_path();
        let store = ExecutionIntentStore::open(&path).unwrap();
        let request = request();
        let id = request.proposal.decision_id.to_string();

        store.register(&request, &config()).unwrap();
        store
            .record_risk(&id, &risk(true, request.proposal.decision_id))
            .unwrap();
        store.record_transaction_guard(&id, &guard(true)).unwrap();

        assert!(
            store
                .record_wallet_authorization(&id, &wallet_authorization())
                .is_err()
        );
        store.record_simulation(&id, &simulation(true)).unwrap();
        assert!(
            store
                .record_wallet_authorization(&id, &wallet_authorization())
                .is_ok()
        );

        let _ = std::fs::remove_file(path);
    }


}
