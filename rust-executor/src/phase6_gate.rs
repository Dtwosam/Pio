use anyhow::{Context, Result};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;

const PHASE_NAME: &str = "PHASE6";
const EVIDENCE_TYPE: &str = "PHASE6_PROMOTION_V1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Phase6PromotionGateReport {
    pub accepted: bool,
    pub reason: String,
    pub phase_name: String,
    pub evidence_type: Option<String>,
    pub promoted_at: Option<String>,
    pub qualified: bool,
    pub evidence_promotion_ready: bool,
    pub evidence_phase5_promoted: bool,
    pub passed_enter_intents: u64,
    pub distinct_pools: u64,
    pub blocked_intents: u64,
    pub postsimulation_intents: u64,
    pub invalid_passed_intents: u64,
    pub authorized_wallets: Vec<String>,
}

fn bool_field(value: &Value, key: &str) -> bool {
    value.get(key).and_then(Value::as_bool).unwrap_or(false)
}

fn u64_field(value: &Value, key: &str) -> u64 {
    value.get(key).and_then(Value::as_u64).unwrap_or(u64::MAX)
}

fn criteria_u64(value: &Value, key: &str) -> Option<u64> {
    value
        .get("criteria")
        .and_then(|item| item.get(key))
        .and_then(Value::as_u64)
}

pub fn verify_phase6_promotion_database(
    database_path: &Path,
) -> Result<Phase6PromotionGateReport> {
    if !database_path.is_absolute() {
        anyhow::bail!("Phase 6 promotion database path must be absolute");
    }
    let conn = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| {
        format!(
            "failed to open Phase 6 promotion database read-only: {}",
            database_path.display()
        )
    })?;

    let row = conn.query_row(
        r#"
        SELECT promoted_at, evidence_type, qualified, evidence_json
        FROM phase_promotion_evidence
        WHERE phase_name = ?1
        LIMIT 1
        "#,
        [PHASE_NAME],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, String>(3)?,
            ))
        },
    );

    let (promoted_at, evidence_type, qualified_raw, evidence_json) =
        match row {
            Ok(value) => value,
            Err(rusqlite::Error::QueryReturnedNoRows) => {
                return Ok(Phase6PromotionGateReport {
                    accepted: false,
                    reason: "phase6_promotion_evidence_missing".into(),
                    phase_name: PHASE_NAME.into(),
                    evidence_type: None,
                    promoted_at: None,
                    qualified: false,
                    evidence_promotion_ready: false,
                    evidence_phase5_promoted: false,
                    passed_enter_intents: 0,
                    distinct_pools: 0,
                    blocked_intents: 0,
                    postsimulation_intents: 0,
                    invalid_passed_intents: 0,
                    authorized_wallets: vec![],
                });
            }
            Err(error) => {
                return Err(error)
                    .context("phase_promotion_evidence table is unavailable");
            }
        };

    let evidence: Value = serde_json::from_str(&evidence_json)
        .context("Phase 6 evidence_json is invalid JSON")?;
    let qualified = qualified_raw == 1;
    let promotion_ready = bool_field(&evidence, "promotion_ready");
    let phase5_promoted = bool_field(&evidence, "phase5_promoted");
    let passed_enter_intents = u64_field(&evidence, "passed_enter_intents");
    let distinct_pools = u64_field(&evidence, "distinct_pools");
    let blocked_intents = u64_field(&evidence, "blocked_intents");
    let postsimulation_intents =
        u64_field(&evidence, "postsimulation_intents");
    let invalid_passed_intents =
        u64_field(&evidence, "invalid_passed_intents");
    let authorized_wallets = evidence
        .get("distinct_authorized_wallets")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let min_passed = criteria_u64(
        &evidence,
        "min_passed_enter_intents",
    );
    let min_pools = criteria_u64(&evidence, "min_distinct_pools");
    let min_blocked = criteria_u64(&evidence, "min_blocked_intents");
    let max_postsimulation = criteria_u64(
        &evidence,
        "max_postsimulation_intents",
    );

    let reason = if evidence_type != EVIDENCE_TYPE {
        "phase6_evidence_type_mismatch"
    } else if !qualified {
        "phase6_evidence_not_qualified"
    } else if !promotion_ready {
        "phase6_evidence_not_promotion_ready"
    } else if !phase5_promoted {
        "phase6_evidence_missing_phase5_promotion"
    } else if invalid_passed_intents != 0 {
        "phase6_evidence_contains_invalid_passed_intents"
    } else if authorized_wallets.len() != 1 {
        "phase6_evidence_wallet_count_invalid"
    } else if min_passed.is_none()
        || passed_enter_intents < min_passed.unwrap()
    {
        "phase6_passed_enter_intents_below_criteria"
    } else if min_pools.is_none()
        || distinct_pools < min_pools.unwrap()
    {
        "phase6_distinct_pools_below_criteria"
    } else if min_blocked.is_none()
        || blocked_intents < min_blocked.unwrap()
    {
        "phase6_blocked_intents_below_criteria"
    } else if max_postsimulation.is_none()
        || postsimulation_intents > max_postsimulation.unwrap()
    {
        "phase6_postsimulation_intents_exceed_criteria"
    } else {
        "approved"
    };

    Ok(Phase6PromotionGateReport {
        accepted: reason == "approved",
        reason: reason.into(),
        phase_name: PHASE_NAME.into(),
        evidence_type: Some(evidence_type),
        promoted_at: Some(promoted_at),
        qualified,
        evidence_promotion_ready: promotion_ready,
        evidence_phase5_promoted: phase5_promoted,
        passed_enter_intents,
        distinct_pools,
        blocked_intents,
        postsimulation_intents,
        invalid_passed_intents,
        authorized_wallets,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;
    use serde_json::json;
    use std::path::PathBuf;
    use uuid::Uuid;

    fn db_path() -> PathBuf {
        std::env::temp_dir().join(format!(
            "pio-phase6-gate-{}.db",
            Uuid::new_v4()
        ))
    }

    fn ready_evidence() -> Value {
        json!({
            "promotion_ready": true,
            "phase5_promoted": true,
            "passed_enter_intents": 10,
            "distinct_pools": 2,
            "blocked_intents": 2,
            "postsimulation_intents": 0,
            "invalid_passed_intents": 0,
            "distinct_authorized_wallets": ["wallet"],
            "criteria": {
                "min_passed_enter_intents": 10,
                "min_distinct_pools": 2,
                "min_blocked_intents": 2,
                "max_postsimulation_intents": 0
            }
        })
    }

    fn seed(path: &Path, evidence_type: &str, qualified: i64, evidence: Value) {
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
                "2026-09-23T14:00:00+00:00",
                evidence_type,
                qualified,
                serde_json::to_string(&evidence).unwrap(),
            ],
        )
        .unwrap();
    }

    #[test]
    fn qualified_phase6_evidence_passes() {
        let path = db_path();
        seed(&path, EVIDENCE_TYPE, 1, ready_evidence());

        let report = verify_phase6_promotion_database(&path).unwrap();

        assert!(report.accepted);
        assert_eq!(report.reason, "approved");
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn missing_phase6_evidence_is_rejected() {
        let path = db_path();
        let conn = Connection::open(&path).unwrap();
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
        drop(conn);

        let report = verify_phase6_promotion_database(&path).unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "phase6_promotion_evidence_missing");
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn evidence_cannot_hide_postsimulation_execution() {
        let path = db_path();
        let mut evidence = ready_evidence();
        evidence["postsimulation_intents"] = json!(1);
        seed(&path, EVIDENCE_TYPE, 1, evidence);

        let report = verify_phase6_promotion_database(&path).unwrap();

        assert!(!report.accepted);
        assert_eq!(
            report.reason,
            "phase6_postsimulation_intents_exceed_criteria"
        );
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn evidence_requires_one_authorized_wallet() {
        let path = db_path();
        let mut evidence = ready_evidence();
        evidence["distinct_authorized_wallets"] =
            json!(["wallet-a", "wallet-b"]);
        seed(&path, EVIDENCE_TYPE, 1, evidence);

        let report = verify_phase6_promotion_database(&path).unwrap();

        assert!(!report.accepted);
        assert_eq!(report.reason, "phase6_evidence_wallet_count_invalid");
        let _ = std::fs::remove_file(path);
    }
}
