use crate::dry_run::DryRunExecutionRequest;
use crate::execution_guard::RiskCheckReport;
use crate::simulation::SimulationReport;
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
                signature TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_execution_intents_status
            ON execution_intents(status, updated_at_unix);
            "#,
        )?;
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

    pub fn record_risk(
        &self,
        decision_id: &str,
        risk: &RiskCheckReport,
    ) -> Result<ExecutionIntentRecord> {
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
            WHERE decision_id = ?
            "#,
            params![
                status.as_str(),
                payload,
                now,
                if risk.accepted { None::<String> } else { Some(risk.reason.clone()) },
                decision_id,
            ],
        )?;
        if changed != 1 {
            anyhow::bail!("unknown execution decision_id: {decision_id}");
        }
        self.load(decision_id)
    }

    pub fn record_simulation(
        &self,
        decision_id: &str,
        simulation: &SimulationReport,
    ) -> Result<ExecutionIntentRecord> {
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
            WHERE decision_id = ?
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
                   risk_json, simulation_json, signature, error
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
            signature: raw.9,
            error: raw.10,
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
        store.record_simulation(&id, &simulation(true)).unwrap();

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
        store.record_simulation(&id, &simulation(true)).unwrap();
        store.begin_signing(&id).unwrap();

        let failed = store.record_failure(&id, "signer unavailable").unwrap();
        assert_eq!(failed.status, ExecutionIntentStatus::Failed);
        assert_eq!(failed.error.as_deref(), Some("signer unavailable"));

        let repeated = store.record_failure(&id, "signer unavailable").unwrap();
        assert_eq!(repeated.status, ExecutionIntentStatus::Failed);
        assert!(store.record_failure(&id, "different failure").is_err());

        let _ = std::fs::remove_file(path);
    }

}
