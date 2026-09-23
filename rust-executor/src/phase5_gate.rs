use anyhow::{Context, Result};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;

const PHASE_NAME: &str = "PHASE5";
const EVIDENCE_TYPE: &str = "PHASE5_PROMOTION_V1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Phase5PromotionGateReport {
    pub accepted: bool,
    pub reason: String,
    pub phase_name: String,
    pub evidence_type: Option<String>,
    pub promoted_at: Option<String>,
    pub qualified: bool,
    pub evidence_promotion_ready: bool,
    pub evidence_endurance_passing: bool,
    pub evidence_ledger_audit_passing: bool,
    pub evidence_phase3_promoted: bool,
}

fn nested_bool(value: &Value, path: &[&str]) -> bool {
    let mut current = value;
    for part in path {
        let Some(next) = current.get(*part) else {
            return false;
        };
        current = next;
    }
    current.as_bool().unwrap_or(false)
}

pub fn verify_phase5_promotion_database(
    database_path: &Path,
) -> Result<Phase5PromotionGateReport> {
    if !database_path.is_absolute() {
        anyhow::bail!("Phase 5 promotion database path must be absolute");
    }
    let conn = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| {
        format!(
            "failed to open Phase 5 promotion database read-only: {}",
            database_path.display()
        )
    })?;

    let mut statement = conn
        .prepare(
            r#"
            SELECT promoted_at, evidence_type, qualified, evidence_json
            FROM phase_promotion_evidence
            WHERE phase_name = ?1
            LIMIT 1
            "#,
        )
        .context("phase_promotion_evidence table is unavailable")?;

    let row = statement
        .query_row([PHASE_NAME], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .optional()?;

    let Some((promoted_at, evidence_type, qualified_raw, evidence_json)) = row
    else {
        return Ok(Phase5PromotionGateReport {
            accepted: false,
            reason: "phase5_promotion_evidence_missing".into(),
            phase_name: PHASE_NAME.into(),
            evidence_type: None,
            promoted_at: None,
            qualified: false,
            evidence_promotion_ready: false,
            evidence_endurance_passing: false,
            evidence_ledger_audit_passing: false,
            evidence_phase3_promoted: false,
        });
    };

    let qualified = qualified_raw == 1;
    let evidence: Value = serde_json::from_str(&evidence_json)
        .context("Phase 5 evidence_json is invalid JSON")?;
    let promotion_ready = nested_bool(&evidence, &["promotion_ready"]);
    let endurance_passing =
        nested_bool(&evidence, &["endurance", "passing"]);
    let ledger_audit_passing =
        nested_bool(&evidence, &["ledger_audit", "passing"]);
    let phase3_promoted =
        nested_bool(&evidence, &["phase3_promoted"]);

    let reason = if evidence_type != EVIDENCE_TYPE {
        "phase5_evidence_type_mismatch"
    } else if !qualified {
        "phase5_evidence_not_qualified"
    } else if !promotion_ready {
        "phase5_evidence_not_promotion_ready"
    } else if !endurance_passing {
        "phase5_endurance_evidence_not_passing"
    } else if !ledger_audit_passing {
        "phase5_ledger_audit_evidence_not_passing"
    } else if !phase3_promoted {
        "phase5_evidence_missing_phase3_promotion"
    } else {
        "approved"
    };

    Ok(Phase5PromotionGateReport {
        accepted: reason == "approved",
        reason: reason.into(),
        phase_name: PHASE_NAME.into(),
        evidence_type: Some(evidence_type),
        promoted_at: Some(promoted_at),
        qualified,
        evidence_promotion_ready: promotion_ready,
        evidence_endurance_passing: endurance_passing,
        evidence_ledger_audit_passing: ledger_audit_passing,
        evidence_phase3_promoted: phase3_promoted,
    })
}

trait OptionalRow<T> {
    fn optional(self) -> rusqlite::Result<Option<T>>;
}

impl<T> OptionalRow<T> for rusqlite::Result<T> {
    fn optional(self) -> rusqlite::Result<Option<T>> {
        match self {
            Ok(value) => Ok(Some(value)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(error) => Err(error),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn db_path() -> PathBuf {
        std::env::temp_dir().join(format!(
            "pio-phase5-gate-{}.db",
            Uuid::new_v4()
        ))
    }

    fn seed(
        path: &Path,
        *,
        evidence_type: &str,
        qualified: i64,
        evidence: Value,
    ) {
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
            "#,
        )
        .unwrap();
        conn.execute(
            r#"
            INSERT INTO phase_promotion_evidence(
                phase_name, promoted_at, evidence_type,
                qualified, evidence_json
            ) VALUES (?1, ?2, ?3, ?4, ?5)
            "#,
            params![
                PHASE_NAME,
                "2026-09-23T12:00:00+00:00",
                evidence_type,
                qualified,
                serde_json::to_string(&evidence).unwrap(),
            ],
        )
        .unwrap();
    }

    fn ready_evidence() -> Value {
        serde_json::json!({
            "promotion_ready": true,
            "phase3_promoted": true,
            "endurance": {"passing": true},
            "ledger_audit": {"passing": true}
        })
    }

    #[test]
    fn qualified_phase5_evidence_passes() {
        let path = db_path();
        seed(
            &path,
            evidence_type: EVIDENCE_TYPE,
            qualified: 1,
            evidence: ready_evidence(),
        );

        let report = verify_phase5_promotion_database(&path).unwrap();

        assert!(report.accepted);
        assert_eq!(report.reason, "approved");
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn wrong_evidence_type_fails_closed() {
        let path = db_path();
        seed(
            &path,
            evidence_type: "PHASE5_PROMOTION_OLD",
            qualified: 1,
            evidence: ready_evidence(),
        );

        let report = verify_phase5_promotion_database(&path).unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "phase5_evidence_type_mismatch");
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn evidence_json_must_confirm_all_promotion_gates() {
        let path = db_path();
        let mut evidence = ready_evidence();
        evidence["ledger_audit"]["passing"] = Value::Bool(false);
        seed(
            &path,
            evidence_type: EVIDENCE_TYPE,
            qualified: 1,
            evidence,
        );

        let report = verify_phase5_promotion_database(&path).unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "phase5_ledger_audit_evidence_not_passing"
        );
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn relative_database_path_is_rejected() {
        assert!(
            verify_phase5_promotion_database(
                Path::new("relative/pio.db")
            )
            .is_err()
        );
    }
}
